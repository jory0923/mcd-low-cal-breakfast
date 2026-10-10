#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_order_flow.py — order_flow.py 回归测试 / Regression tests.

重点覆盖 9.1 中标记为「待补」的四个场景:
外送 / 下单 / 预约 / 得来速, 以及支付引导的**安全边界**。

运行 / Run:
    python scripts/test_order_flow.py
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import order_flow as O  # noqa: E402

_TESTS: List[Tuple[str, Callable[[], None]]] = []
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv


def test(fn: Callable[[], None]) -> Callable[[], None]:
    _TESTS.append((fn.__name__, fn))
    return fn


def assert_eq(actual: Any, expected: Any, msg: str = "") -> None:
    if actual != expected:
        raise AssertionError(f"{msg}\n  期望: {expected!r}\n  实际: {actual!r}")


def assert_true(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "断言失败")


ITEMS = [{"productCode": "513606", "quantity": 1},
         {"productCode": "4825", "quantity": 2}]


# ---------------------------------------------------------------------------
# 1. 渠道定义与参数推导 / channel params
# ---------------------------------------------------------------------------

@test
def test_四渠道映射正确():
    """beType / orderType 组合必须与接口 schema 完全一致"""
    assert_eq(O.resolve_channel("pickup")["beType"], 1)
    assert_eq(O.resolve_channel("pickup")["orderType"], 1)
    assert_eq(O.resolve_channel("drive-through")["beType"], 5)
    assert_eq(O.resolve_channel("drive-through")["orderType"], 1)
    assert_eq(O.resolve_channel("delivery")["beType"], 2)
    assert_eq(O.resolve_channel("delivery")["orderType"], 2)
    assert_eq(O.resolve_channel("group")["beType"], 6)
    assert_eq(O.resolve_channel("group")["orderType"], 2)


@test
def test_未知渠道报错():
    try:
        O.resolve_channel("无人机配送")
        raise AssertionError("未知渠道应抛 ValueError")
    except ValueError as exc:
        assert_true("未知取餐方式" in str(exc), "错误信息应说明原因")


@test
def test_到店自取剔除beCode():
    """回归: beType=1 传 beCode 会导致接口报错, 必须在此层就剔除"""
    p = O.prune(O.build_channel_params("pickup", "1450713", be_code="BC123"))
    assert_true("beCode" not in p, "到店自取不得包含 beCode")


@test
def test_得来速保留beCode():
    """待补功能: 得来速(beType=5) 必须传 beCode"""
    p = O.prune(O.build_channel_params(
        "drive-through", "1450713", be_code="BC123", take_way_code="TW1"))
    assert_eq(p.get("beCode"), "BC123", "得来速应保留 beCode")


@test
def test_外送需地址且无takeWay():
    """待补功能: 外送必须带 addressId, 且不得带 takeWayCode"""
    p = O.prune(O.build_channel_params(
        "delivery", "1450713", "BC123", "ADDR9"))
    assert_eq(p.get("addressId"), "ADDR9", "外送应带 addressId")
    assert_true("takeWayCode" not in p, "外送不得带 takeWayCode")


@test
def test_到店剔除addressId():
    p = O.prune(O.build_channel_params(
        "pickup", "1450713", address_id="ADDR9"))
    assert_true("addressId" not in p, "到店不应包含 addressId")


# ---------------------------------------------------------------------------
# 2. 校验 / validation
# ---------------------------------------------------------------------------

@test
def test_校验_到店误传beCode被拦截():
    ok, issues = O.validate_order_params(
        "pickup", "1450713", ITEMS, be_code="BC123", take_way_code="TW1")
    assert_true(not ok, "应校验不通过")
    assert_true(any("不可传 beCode" in i for i in issues), f"应指出 beCode 问题: {issues}")


@test
def test_校验_外送缺地址被拦截():
    ok, issues = O.validate_order_params("delivery", "1450713", ITEMS, be_code="BC")
    assert_true(not ok, "缺 addressId 应不通过")
    assert_true(any("addressId" in i for i in issues), f"应指出地址问题: {issues}")


@test
def test_校验_团餐缺助餐码被拦截():
    ok, issues = O.validate_order_params(
        "group", "1450713", ITEMS, be_code="BC", address_id="AD")
    assert_true(not ok, "团餐缺 gmServiceCode 应不通过")
    assert_true(any("gmServiceCode" in i for i in issues), f"应指出助餐码问题: {issues}")


@test
def test_校验_到店缺takeWayCode被拦截():
    ok, issues = O.validate_order_params("pickup", "1450713", ITEMS)
    assert_true(not ok, "到店缺 takeWayCode 应不通过")
    assert_true(any("takeWayCode" in i for i in issues), f"应指出取餐方式问题: {issues}")


@test
def test_校验_门店编码非数字被拦截():
    ok, issues = O.validate_order_params(
        "pickup", "abc123", ITEMS, take_way_code="TW1")
    assert_true(not ok, "非数字 storeCode 应不通过")
    assert_true(any("纯数字" in i for i in issues), f"应指出编码格式问题: {issues}")


@test
def test_校验_空商品被拦截():
    ok, issues = O.validate_order_params("pickup", "1450713", [], take_way_code="TW")
    assert_true(not ok, "空商品应不通过")
    assert_true(any("商品列表为空" in i for i in issues), f"应指出商品问题: {issues}")


@test
def test_校验_数量非法被拦截():
    bad = [{"productCode": "513606", "quantity": 0}]
    ok, issues = O.validate_order_params("pickup", "1450713", bad, take_way_code="TW")
    assert_true(not ok, "quantity=0 应不通过")
    assert_true(any("quantity" in i for i in issues), f"应指出数量问题: {issues}")


@test
def test_校验_正常参数通过():
    ok, issues = O.validate_order_params(
        "pickup", "1450713", ITEMS, take_way_code="TW1")
    assert_true(ok, f"合法参数应通过, 但报错: {issues}")


@test
def test_校验_外送完整参数通过():
    ok, issues = O.validate_order_params(
        "delivery", "1450713", ITEMS, be_code="BC", address_id="AD")
    assert_true(ok, f"合法外送参数应通过, 但报错: {issues}")


# ---------------------------------------------------------------------------
# 3. 预约场景 / reservation (9.1 待补项)
# ---------------------------------------------------------------------------

@test
def test_预约时间格式校验():
    assert_eq(O.validate_reservation_date("2026-10-11 07:30"), [], "合法格式应无问题")
    bad = O.validate_reservation_date("2026/10/11 7:30")
    assert_true(len(bad) == 1, f"非法格式应报错: {bad}")
    bad2 = O.validate_reservation_date("2026-10-11")
    assert_true(len(bad2) == 1, "缺时间部分应报错")


@test
def test_预约时间格式校验非字符串():
    bad = O.validate_reservation_date(20261011)
    assert_true(len(bad) == 1, "非字符串应报错")


@test
def test_预约时间进入请求体():
    """待补功能: 预约时间需正确传入算价与下单请求"""
    p = O.build_price_payload(
        "pickup", "1450713", ITEMS, take_way_code="TW1",
        reservation_date="2026-10-11 07:30")
    assert_eq(p.get("reservationDate"), "2026-10-11 07:30", "预约时间应保留")


@test
def test_非法预约时间不进入请求体():
    """非法格式应在校验层被拦截, 不污染请求体"""
    ok, issues = O.validate_order_params(
        "pickup", "1450713", ITEMS, take_way_code="TW1",
        reservation_date="11/10/2026")
    assert_true(not ok, "非法预约格式应不通过")


# ---------------------------------------------------------------------------
# 4. 请求体组装 / payload builders
# ---------------------------------------------------------------------------

@test
def test_算价请求体含随单购():
    """回归: 漏传 withOrder 会导致用户按原价付费"""
    p = O.build_price_payload(
        "pickup", "1450713", ITEMS, take_way_code="TW1",
        with_order={"cardId": "CARD1", "cardType": 2})
    assert_true("withOrder" in p, "必须透传 withOrder")
    assert_eq(p["withOrder"]["cardId"], "CARD1")


@test
def test_下单请求体含备注():
    p = O.build_order_payload(
        "delivery", "1450713", ITEMS, be_code="BC", address_id="AD",
        remark="无接触配送")
    assert_eq(p.get("remark"), "无接触配送", "外送备注应保留")


@test
def test_下单备注超长报错():
    try:
        O.build_order_payload(
            "delivery", "1450713", ITEMS, be_code="BC", address_id="AD",
            remark="长" * 60)
        raise AssertionError("超长备注应抛 ValueError")
    except ValueError as exc:
        assert_true("50" in str(exc), f"错误信息应说明 50 字限制: {exc}")


@test
def test_到店场景不传备注():
    """备注仅外送有意义, 到店不应携带"""
    p = O.build_order_payload(
        "pickup", "1450713", ITEMS, take_way_code="TW1", remark="无接触配送")
    assert_true("remark" not in p, "到店不应包含 remark")


@test
def test_请求体不含None值():
    p = O.build_price_payload("pickup", "1450713", ITEMS, take_way_code="TW1")
    for k, v in p.items():
        assert_true(v is not None, f"请求体不应含 None 值: {k}")


# ---------------------------------------------------------------------------
# 5. 金额换算 / amount conversion
# ---------------------------------------------------------------------------

@test
def test_分转元():
    assert_eq(O.fen_to_yuan(2750), 27.5, "2750 分应为 27.5 元")
    assert_eq(O.fen_to_yuan(100), 1.0, "100 分应为 1 元")
    assert_eq(O.fen_to_yuan(0), 0.0, "0 分应为 0 元")
    assert_eq(O.fen_to_yuan("3210"), 32.1, "整数字符串应可换算")
    assert_eq(O.fen_to_yuan(None), None, "None 应返回 None")
    assert_eq(O.fen_to_yuan("abc"), None, "非法值应返回 None")


@test
def test_分转元_拒绝元为单位的入参():
    """回归: 传入 "27.5"(元) 会被静默换算成 0.28 元, 造成百倍金额错误"""
    assert_eq(O.fen_to_yuan("27.5"), None, "带小数点的字符串应被拒绝")
    assert_eq(O.fen_to_yuan(27.5), None, "非整数分应被拒绝")
    assert_eq(O.fen_to_yuan(True), None, "布尔值应被拒绝")
    assert_eq(O.fen_to_yuan([]), None, "列表应被拒绝")


@test
def test_分转元_拒绝负金额():
    """回归: 负金额无业务意义, 不应展示给用户"""
    assert_eq(O.fen_to_yuan(-100), None, "负金额应被拦截")
    assert_eq(O.fen_to_yuan("-500"), None, "负数字符串应被拦截")


@test
def test_支付引导_缺订单号拒绝生成():
    """回归: 无 orderId 时不得输出「订单已创建」, 否则引导用户支付不存在的订单"""
    g = O.build_payment_guide(None, 2750, channel="pickup")
    assert_eq(g["valid"], False, "缺 orderId 应标记为无效")
    assert_true(g["error"] is not None, "应给出错误原因")
    assert_eq(g["steps"], [], "无效引导不应含支付步骤")
    out = O.render_payment_guide(g)
    assert_true("无法生成支付引导" in out, f"应明确报错: {out}")
    assert_true("订单已创建" not in out, f"绝不可出现「订单已创建」: {out}")


@test
def test_订单号格式校验():
    """回归: 拦截编造/截断的订单号"""
    assert_eq(O.validate_order_id("1030108270000795607354880945"), None, "真实订单号应合法")
    assert_eq(O.validate_order_id("ABC-123_x"), None, "字母数字下划线连字符应合法")
    assert_true(O.validate_order_id("") is not None, "空串应报错")
    assert_true(O.validate_order_id(None) is not None, "None 应报错")
    assert_true(O.validate_order_id("../../etc/passwd") is not None, "路径字符应报错")
    assert_true(O.validate_order_id("a" * 100) is not None, "超长应报错")
    assert_true(O.validate_order_id("order 123") is not None, "空格应报错")


@test
def test_支付引导_非法订单号拒绝生成():
    """回归: 编造的订单号(如 O1)不得生成支付引导"""
    g = O.build_payment_guide("../../etc/passwd", 2750, channel="pickup")
    assert_eq(g["valid"], False, "非法 orderId 应标记为无效")
    out = O.render_payment_guide(g)
    assert_true("订单已创建" not in out, f"绝不可出现「订单已创建」: {out}")

    g2 = O.build_payment_guide("a" * 100, 2750, channel="pickup")
    assert_eq(g2["valid"], False, "超长 orderId 应无效")


@test
def test_支付引导_有效时标记valid():
    g = O.build_payment_guide("ORDER1", 2750, channel="pickup")
    assert_eq(g["valid"], True, "有 orderId 应标记有效")
    assert_true(len(g["steps"]) > 0, "有效引导应含步骤")


@test
def test_支付引导_金额异常不展示猜测值():
    """回归: 金额换算失败时不得展示错误金额"""
    g = O.build_payment_guide("O1", "27.5", channel="pickup")
    assert_eq(g["amountYuan"], None, "异常金额应换算失败")
    out = O.render_payment_guide(g)
    assert_true("0.28" not in out, f"绝不可展示百倍误差金额: {out}")
    assert_true("以官方支付页面为准" in out, f"应提示以官方为准: {out}")


# ---------------------------------------------------------------------------
# 6. 取餐方式提取 / takeWayList
# ---------------------------------------------------------------------------

@test
def test_提取取餐方式():
    """到店下单的 takeWayCode 必须来自 calculate-price 返回, 不可编造"""
    resp = {"data": {"takeWayList": [
        {"code": "TW001", "name": "到店取餐", "selected": True},
        {"code": "TW002", "name": "得来速", "selected": False},
    ]}}
    out = O.extract_take_way_codes(resp)
    assert_eq(len(out), 2, "应提取到 2 个取餐方式")
    assert_eq(out[0]["code"], "TW001")
    assert_true(out[0]["selected"], "应识别选中态")


@test
def test_提取取餐方式_响应缺字段():
    assert_eq(O.extract_take_way_codes({"data": {}}), [], "缺字段应返回空列表")
    assert_eq(O.extract_take_way_codes(None), [], "None 应返回空列表")


# ---------------------------------------------------------------------------
# 7. 支付引导 / payment hand-off (安全边界关键)
# ---------------------------------------------------------------------------

@test
def test_支付引导_明确不可代付():
    """安全边界: 本 Skill 无支付能力, 必须如实告知"""
    g = O.build_payment_guide("ORDER1", 2750)
    assert_true(g["canAutoPay"] is False, "canAutoPay 必须为 False")
    assert_true("不代为付款" in g["notice"], "必须明确声明无法代付")


@test
def test_支付引导_金额换算正确():
    g = O.build_payment_guide("ORDER1", 2750)
    assert_eq(g["amountYuan"], 27.5, "金额应从分转为元")


@test
def test_支付引导_含查单步骤():
    g = O.build_payment_guide("ORDER1", 2750)
    joined = " ".join(g["steps"])
    assert_true("支付完成" in joined, "应引导用户回复支付完成")
    assert_true("query-order" in joined, "应说明将用 query-order 查状态")


@test
def test_支付引导_外送补充地址确认():
    g = O.build_payment_guide("ORDER1", 2750, channel="delivery")
    joined = " ".join(g["steps"])
    assert_true("地址" in joined, "外送应补充确认地址的提示")


@test
def test_支付引导_渲染不崩溃():
    for ch in ("pickup", "drive-through", "delivery", "group"):
        g = O.build_payment_guide("O1", 1000, channel=ch)
        out = O.render_payment_guide(g)
        assert_true(isinstance(out, str) and len(out) > 0, f"{ch} 渲染应返回文案")
        assert_true("不代为付款" in out, "渲染文案必须含不可代付声明")


# ---------------------------------------------------------------------------
# 8. 取消原因 / cancellation
# ---------------------------------------------------------------------------

@test
def test_取消原因码齐全():
    for code in ("1", "2", "3", "4", "5", "-1"):
        assert_true(code in O.CANCEL_REASONS, f"缺少取消原因码 {code}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    passed, failed = 0, []
    for name, fn in _TESTS:
        try:
            fn()
            passed += 1
            if VERBOSE:
                print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            if VERBOSE:
                print(f"  FAIL {name}\n       {exc}")

    print()
    if failed:
        print("=" * 60)
        print(f"失败 {len(failed)} / {len(_TESTS)}")
        for name, exc in failed:
            print(f"\n✗ {name}\n  {exc}")
        return 1

    print("=" * 60)
    print(f"全部通过: {passed} / {len(_TESTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())