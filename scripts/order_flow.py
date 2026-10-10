#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_flow.py — 下单与支付流程编排 / Order & payment flow orchestration.

职责 / Responsibilities:
    1. 依据「取餐方式」推导 beType / orderType / 必传参数 (Derive channel params)
    2. 校验下单参数完整性,拦截必然失败的调用 (Validate before calling MCP)
    3. 组装 calculate-price / create-order 的请求体 (Build MCP request payloads)
    4. 编排**支付引导**流程 (Orchestrate the payment hand-off)

⚠️ 关于支付的重要事实 / Critical fact about payment:
    经穷举 mcd-mcp 全部工具确认, 该 MCP Server **没有任何支付类工具**
    (无 pay / payment / transaction 等), 支付**不由本 Skill 代劳**。

    `create-order` 返回后, 用户需通过返回的**支付链接**自行完成支付
    (扫码或麦当劳 APP), 随后回复「支付完成」, 再由 `query-order` 查状态。

    因此本模块提供的是 **支付引导 (payment hand-off)** 而非代扣款。
    严禁在任何输出中暗示本 Skill 能代用户付款。

命令行 / CLI:
    python order_flow.py params  --channel pickup
    python order_flow.py params  --channel delivery --address-id A123
    python order_flow.py params  --channel drive-through --be-code BC9
    python order_flow.py validate --payload payload.json
    python order_flow.py payment-guide --order-id O123 --amount 2750
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 渠道定义 / Channel definitions
# ---------------------------------------------------------------------------

# channel -> (beType, orderType, 需要 beCode, 需要 addressId)
# 这是 mcd-mcp 中最容易出错的一组参数,集中定义以避免散落出错。
CHANNELS: Dict[str, Dict[str, Any]] = {
    "pickup": {
        "label": "到店自取",
        "beType": 1,
        "orderType": 1,
        "needsBeCode": False,
        "needsAddress": False,
        "needsTakeWay": True,
        "note": "到店自取场景不可传 beCode,否则接口报错",
    },
    "drive-through": {
        "label": "得来速(车道取餐)",
        "beType": 5,
        "orderType": 1,
        "needsBeCode": True,
        "needsAddress": False,
        "needsTakeWay": True,
        "note": "得来速必须传 beCode (来自 query-nearby-stores)",
    },
    "delivery": {
        "label": "麦乐送到家",
        "beType": 2,
        "orderType": 2,
        "needsBeCode": True,
        "needsAddress": True,
        "needsTakeWay": False,
        "note": "外送必须传 beCode + addressId,且不传 takeWayCode",
    },
    "group": {
        "label": "企业团餐",
        "beType": 6,
        "orderType": 2,
        "needsBeCode": True,
        "needsAddress": True,
        "needsTakeWay": False,
        "note": "团餐额外必传 gmServiceCode (来自 query-meal-assistance)",
    },
}

# 预约时间格式 / Reservation datetime format
RESERVATION_FMT = "%Y-%m-%d %H:%M"

# 取消原因码 / Cancellation reason codes
CANCEL_REASONS: Dict[str, str] = {
    "1": "改主意了",
    "2": "重复下单",
    "3": "点错了/点多了/点少了",
    "4": "地址/电话填错了",
    "5": "送达时间选错了",
    "-1": "其它原因",
}


# ---------------------------------------------------------------------------
# 渠道参数推导 / Channel parameter derivation
# ---------------------------------------------------------------------------

def resolve_channel(channel: str) -> Dict[str, Any]:
    """
    取渠道定义 (Resolve a channel definition).

    Raises:
        ValueError: 未知渠道
    """
    if channel not in CHANNELS:
        raise ValueError(
            f"未知取餐方式 {channel!r},可选: {', '.join(CHANNELS)}"
        )
    return dict(CHANNELS[channel])


def build_channel_params(
    channel: str,
    store_code: str,
    be_code: Optional[str] = None,
    address_id: Optional[str] = None,
    gm_service_code: Optional[str] = None,
    take_way_code: Optional[str] = None,
    reservation_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建渠道基础参数 (Build the base channel parameters).

    这里是**防止把 beCode 传到 beType=1 场景**的唯一权威位置。

    Returns:
        形如 {"beType":1,"orderType":1,"storeCode":"...","beCode":None,...}
        不需要的键一律置为 None,由 `prune()` 在发请求前剔除。
    """
    spec = resolve_channel(channel)

    params: Dict[str, Any] = {
        "beType": spec["beType"],
        "orderType": spec["orderType"],
        "storeCode": store_code,
        "beCode": be_code,
        "addressId": address_id,
        "gmServiceCode": gm_service_code,
        "takeWayCode": take_way_code,
        "reservationDate": reservation_date,
    }

    # 关键约束: 到店自取绝不传 beCode
    if not spec["needsBeCode"]:
        params["beCode"] = None
    if not spec["needsAddress"]:
        params["addressId"] = None
    # 仅 orderType=1 才用 takeWayCode
    if not spec["needsTakeWay"]:
        params["takeWayCode"] = None

    return params


def prune(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    剔除值为 None 的键 (Drop keys whose value is None).

    MCP 接口对多余的可选键较敏感(部分接口传错会报错),故必须清理。
    """
    return {k: v for k, v in params.items() if v is not None}


# ---------------------------------------------------------------------------
# 参数校验 / Validation
# ---------------------------------------------------------------------------

def validate_order_params(
    channel: str,
    store_code: str,
    items: List[Dict[str, Any]],
    be_code: Optional[str] = None,
    address_id: Optional[str] = None,
    take_way_code: Optional[str] = None,
    gm_service_code: Optional[str] = None,
    reservation_date: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    下单前置校验 (Pre-flight validation before calling MCP).

    目的: 把「必然失败的调用」提前拦截,并给出可执行的修复提示。

    Returns:
        (是否通过, 问题列表)
    """
    issues: List[str] = []

    try:
        spec = resolve_channel(channel)
    except ValueError as exc:
        return False, [str(exc)]

    # 门店编码
    if not store_code or not str(store_code).strip():
        issues.append("缺少 storeCode(门店编码)")
    elif not str(store_code).isdigit():
        issues.append(f"storeCode 必须为纯数字,当前为 {store_code!r}")

    # 商品列表
    if not items:
        issues.append("商品列表为空,无法下单")
    else:
        for i, it in enumerate(items):
            code = it.get("productCode")
            if not code:
                issues.append(f"第 {i + 1} 个商品缺少 productCode")
            qty = it.get("quantity")
            if not isinstance(qty, int) or qty < 1:
                issues.append(f"第 {i + 1} 个商品 quantity 必须为 ≥1 的整数")

    # 渠道必填项
    if spec["needsBeCode"] and not be_code:
        issues.append(f"{spec['label']}必须传 beCode (来自门店查询接口)")
    if not spec["needsBeCode"] and be_code:
        issues.append(f"{spec['label']}不可传 beCode,否则接口报错")
    if spec["needsAddress"] and not address_id:
        issues.append(f"{spec['label']}必须传 addressId (来自配送地址查询)")
    if channel == "group" and not gm_service_code:
        issues.append("企业团餐必须传 gmServiceCode (来自 query-meal-assistance)")

    # takeWayCode 仅 orderType=1 需要
    if spec["orderType"] == 1 and not take_way_code:
        issues.append("到店/得来速场景必须传 takeWayCode (来自 calculate-price 返回)")
    if spec["orderType"] == 2 and take_way_code:
        issues.append("外送/团餐场景不可传 takeWayCode")

    # 预约时间格式
    if reservation_date:
        problems = validate_reservation_date(reservation_date)
        issues.extend(problems)

    return (not issues), issues


def validate_reservation_date(value: str) -> List[str]:
    """
    校验预约时间格式 (Validate reservation datetime).

    要求严格为 `yyyy-MM-dd HH:mm`,因为接口按此格式解析。
    """
    from datetime import datetime

    problems: List[str] = []
    if not isinstance(value, str):
        return ["reservationDate 必须为字符串"]

    try:
        datetime.strptime(value, RESERVATION_FMT)
    except ValueError:
        problems.append(
            f"reservationDate 格式错误: {value!r},应为 'yyyy-MM-dd HH:mm' "
            f"(如 '2026-10-11 07:30')"
        )
    return problems


# ---------------------------------------------------------------------------
# 请求体组装 / Payload builders
# ---------------------------------------------------------------------------

def build_price_payload(
    channel: str,
    store_code: str,
    items: List[Dict[str, Any]],
    be_code: Optional[str] = None,
    address_id: Optional[str] = None,
    take_way_code: Optional[str] = None,
    gm_service_code: Optional[str] = None,
    reservation_date: Optional[str] = None,
    need_tableware: Optional[bool] = None,
    with_order: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    组装 `calculate-price` 请求体 (Build the calculate-price payload).

    注意: 随单购餐品必须携带 withOrder,否则会以原价计价。
    """
    params = build_channel_params(
        channel, store_code, be_code, address_id, gm_service_code,
        take_way_code, reservation_date,
    )

    payload = prune(params)
    payload["items"] = items

    if need_tableware is not None:
        payload["needTableware"] = need_tableware
    if with_order:
        payload["withOrder"] = with_order
    if take_way_code:
        payload["takeWayCode"] = take_way_code
    if reservation_date:
        payload["reservationDate"] = reservation_date

    return payload


def build_order_payload(
    channel: str,
    store_code: str,
    items: List[Dict[str, Any]],
    be_code: Optional[str] = None,
    address_id: Optional[str] = None,
    take_way_code: Optional[str] = None,
    gm_service_code: Optional[str] = None,
    reservation_date: Optional[str] = None,
    remark: Optional[str] = None,
    need_tableware: Optional[bool] = None,
    with_order: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    组装 `create-order` 请求体 (Build the create-order payload).

    与算价的区别: 必须带 takeWayCode(到店场景)与 remark(外送建议填)。
    """
    payload = build_price_payload(
        channel, store_code, items, be_code, address_id,
        take_way_code, gm_service_code, reservation_date,
        need_tableware, with_order,
    )

    # 外送建议填写备注(如「无接触配送」),最多 50 字
    if remark and resolve_channel(channel)["orderType"] == 2:
        if len(remark) > 50:
            raise ValueError(f"remark 超长({len(remark)}字),最多 50 字")
        payload["remark"] = remark

    return payload


def extract_take_way_codes(price_response: Any) -> List[Dict[str, Any]]:
    """
    从 calculate-price 返回中提取取餐方式列表 (Extract takeWayList).

    到店/得来速下单必须从中选取 takeWayCode,不可自行编造。
    """
    node = _locate(price_response, ("takeWayList",))
    if not isinstance(node, list):
        return []

    out: List[Dict[str, Any]] = []
    for row in node:
        if isinstance(row, dict):
            out.append({
                "code": row.get("code"),
                "name": row.get("name") or row.get("takeWayName") or "",
                "selected": bool(row.get("selected") or row.get("isDefault")),
            })
    return out


def _locate(node: Any, keys: Tuple[str, ...], depth: int = 0) -> Any:
    """在嵌套结构中定位首个命中的键 (Locate the first matching key)."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for k in keys:
            if k in node:
                return node[k]
        for v in node.values():
            found = _locate(v, keys, depth + 1)
            if found is not None:
                return found
    return None


def fen_to_yuan(fen: Any) -> Optional[float]:
    """
    分转元 (Convert fen to yuan).

    接口金额单位为**分**,直接当元展示会放大 100 倍。

    ⚠️ 入参必须是**整数分**。若误传「元」为单位的字符串(如 "27.5"),
    会被静默换算成 0.28 元——这是典型的百倍金额错误,必须拒绝。

    Returns:
        合法输入返回 float; 非法输入返回 None,交由调用方显式处理。
    """
    if fen is None or isinstance(fen, bool):
        return None

    # 必须是数值或纯数字字符串,且不含小数点(小数说明单位传错)
    if isinstance(fen, str):
        text = fen.strip()
        if not text:
            return None
        # 带小数点 => 极可能是「元」而非「分」,拒绝以免百倍误差
        if "." in text:
            return None
        if not text.lstrip("-").isdigit():
            return None
        value = int(text)
    elif isinstance(fen, int):
        value = fen
    elif isinstance(fen, float):
        # float 同样视为可疑(多数来自除法结果),要求为整数分
        if not float(fen).is_integer():
            return None
        value = int(fen)
    else:
        return None

    # 负金额无业务意义,拦截
    if value < 0:
        return None

    return round(value / 100.0, 2)


# 订单号格式约束 (来自 MCP schema 中 query-survey-coupon 的显式说明)
# 仅允许字母、数字、下划线和连字符，最长 64 位。
# Agent 不得自行生成或改写 orderId。
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""Order-ID pattern: letters, digits, underscore, hyphen; max 64 chars."""


def validate_order_id(order_id: Any) -> Optional[str]:
    """
    校验订单号格式 (Validate an order ID).

    目的: 拦截**编造或截断**的订单号。若不校验, AI 生成 "O1" 这类
    假 ID 时系统无从发现, 用户会拿到一个查询不到、也付不了的单。

    Returns:
        None 表示合法; 否则返回错误描述。
    """
    if order_id is None or not isinstance(order_id, str) or not order_id.strip():
        return "orderId 为空,必须来自 create-order 的返回"

    value = order_id.strip()
    if not ORDER_ID_RE.match(value):
        illegal = set(re.findall(r"[^A-Za-z0-9_-]", value))
        return (
            f"orderId 含非法字符 {sorted(illegal)!r};"
            "仅允许字母、数字、下划线与连字符"
        )
    if len(value) > 64:
        return f"orderId 超长({len(value)} 字),最长 64 位"
    return None


# ---------------------------------------------------------------------------
# 支付引导 / Payment hand-off
# ---------------------------------------------------------------------------

PAYMENT_FACTS = {
    "has_payment_tool": False,
    "reason": "mcd-mcp 未提供任何支付类工具",
    "user_action": "通过 create-order 返回的支付链接自行完成支付",
    "verify_tool": "query-order",
}


def build_payment_guide(
    order_id: Optional[str],
    amount_fen: Any = None,
    channel: str = "pickup",
    pay_url: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成支付引导信息 (Build the payment hand-off guide).

    ⚠️ 本函数**不执行任何支付动作**,仅生成给用户的引导文案与后续步骤。
    支付必须由用户自行完成。

    Returns:
        含 orderId / amount / payUrl / steps / notice 的字典
    """
    spec = resolve_channel(channel)
    amount_yuan = fen_to_yuan(amount_fen)

    # 订单号缺失或格式非法时不可假装订单已创建 ——
    # 否则会引导用户去支付一个不存在/查不到的订单。
    id_problem = validate_order_id(order_id)
    if id_problem:
        return {
            "orderId": order_id,
            "amountFen": amount_fen,
            "amountYuan": amount_yuan,
            "payUrl": None,
            "channel": channel,
            "channelLabel": spec["label"],
            "status": status,
            "valid": False,
            "error": (
                f"orderId 校验未通过：{id_problem}。"
                "orderId 必须来自 create-order 的真实返回,绝不可编造或改写。"
            ),
            "steps": [],
            "notice": "本 Skill 不代为付款,也无法代付。",
            "canAutoPay": False,
        }

    steps: List[str] = [
        "1. 订单已创建,尚未支付",
        "2. 请通过下方支付链接扫码,或打开麦当劳 APP 完成支付",
        "3. 支付完成后回复「支付完成」,我会用 query-order 查询最新订单状态",
        "4. 若支付遇到问题,回复「支付失败」,我会协助查看或引导取消订单",
    ]

    if spec["orderType"] == 2:
        steps.insert(2, "· 外送订单请确认地址与联系电话无误")

    return {
        "orderId": order_id,
        "amountFen": amount_fen,
        "amountYuan": amount_yuan,
        "payUrl": pay_url,
        "channel": channel,
        "channelLabel": spec["label"],
        "status": status,
        "valid": True,
        "error": None,
        "steps": steps,
        "notice": (
            "本 Skill 不代为付款,也无法代付。支付需由您本人通过官方渠道完成。"
        ),
        "canAutoPay": False,
    }


def render_payment_guide(guide: Dict[str, Any]) -> str:
    """渲染支付引导文案 (Render the payment hand-off text)."""
    # 订单号缺失: 明确报错, 绝不输出「订单已创建」类误导文案
    if not guide.get("valid"):
        return (
            f"❌ 无法生成支付引导：{guide.get('error', '未知原因')}\n"
            f"ℹ️ {guide['notice']}"
        )

    lines: List[str] = []

    if guide.get("orderId"):
        lines.append(f"🧾 订单已创建：{guide['orderId']}")
    if guide.get("amountYuan") is not None:
        lines.append(f"💰 待付金额：¥{guide['amountYuan']}")
    elif guide.get("amountFen") is not None:
        # 金额存在但换算失败, 说明单位可能传错, 不可展示猜测值
        lines.append("⚠️ 待付金额：接口返回值异常，请以官方支付页面为准")
    if guide.get("channelLabel"):
        lines.append(f"🚶 取餐方式：{guide['channelLabel']}")
    lines.append("")

    if guide.get("payUrl"):
        lines.append("请扫码或点击链接支付：")
        lines.append(guide["payUrl"])
        lines.append("")

    lines.append("支付步骤：")
    lines.extend(f"   {s}" for s in guide["steps"])
    lines.append("")
    lines.append(f"ℹ️ {guide['notice']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="麦当劳下单与支付流程编排 (Order & payment flow orchestration)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_params = sub.add_parser("params", help="推导某取餐方式的必传参数")
    p_params.add_argument("--channel", required=True, choices=list(CHANNELS))
    p_params.add_argument("--store-code", required=True)
    p_params.add_argument("--be-code")
    p_params.add_argument("--address-id")
    p_params.add_argument("--take-way-code")
    p_params.add_argument("--gm-service-code")
    p_params.add_argument("--reservation-date")

    p_validate = sub.add_parser("validate", help="校验下单参数完整性")
    p_validate.add_argument("--channel", required=True, choices=list(CHANNELS))
    p_validate.add_argument("--store-code", required=True)
    p_validate.add_argument("--items", required=True, help="商品列表 JSON")
    p_validate.add_argument("--be-code")
    p_validate.add_argument("--address-id")
    p_validate.add_argument("--take-way-code")
    p_validate.add_argument("--gm-service-code")
    p_validate.add_argument("--reservation-date")

    p_pay = sub.add_parser("payment-guide", help="生成支付引导")
    p_pay.add_argument("--order-id")
    p_pay.add_argument("--amount-fen")
    p_pay.add_argument("--channel", default="pickup", choices=list(CHANNELS))
    p_pay.add_argument("--pay-url")

    args = parser.parse_args(argv)

    if args.command == "params":
        params = build_channel_params(
            args.channel, args.store_code,
            args.be_code, args.address_id, args.gm_service_code,
        )
        spec = resolve_channel(args.channel)
        if args.take_way_code:
            params["takeWayCode"] = args.take_way_code
        if args.reservation_date:
            params["reservationDate"] = args.reservation_date
        print(f"# {spec['label']}")
        print(f"# {spec['note']}")
        print(json.dumps(prune(params), ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate":
        try:
            items = json.loads(args.items)
        except json.JSONDecodeError as exc:
            print(f"❌ --items 不是合法 JSON: {exc}", file=sys.stderr)
            return 2
        ok, issues = validate_order_params(
            args.channel, args.store_code, items,
            args.be_code, args.address_id, args.take_way_code,
            args.gm_service_code, args.reservation_date,
        )
        if ok:
            print("✅ 参数校验通过,可以调用 calculate-price / create-order")
            return 0
        print("❌ 参数校验未通过:", file=sys.stderr)
        for i in issues:
            print(f"   - {i}", file=sys.stderr)
        return 1

    if args.command == "payment-guide":
        guide = build_payment_guide(
            args.order_id, args.amount_fen, args.channel, args.pay_url,
        )
        print(render_payment_guide(guide))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())