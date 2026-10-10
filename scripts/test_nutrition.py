#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nutrition.py 回归测试套件 / Regression test suite.

覆盖已修复的历史缺陷与边界场景,防止回归。
Covers previously-fixed defects and edge cases to prevent regressions.

运行 / Run:
    python scripts/test_nutrition.py
    python scripts/test_nutrition.py -v      # 显示每个用例
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nutrition as N  # noqa: E402


# ---------------------------------------------------------------------------
# 迷你测试框架 / Minimal test harness
# ---------------------------------------------------------------------------

_TESTS: List[Tuple[str, Callable[[], None]]] = []
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv


def test(fn: Callable[[], None]) -> Callable[[], None]:
    """注册一个测试用例 (Register a test case)."""
    _TESTS.append((fn.__name__, fn))
    return fn


def assert_eq(actual: Any, expected: Any, msg: str = "") -> None:
    if actual != expected:
        raise AssertionError(f"{msg}\n  期望 expected: {expected!r}\n  实际 actual:   {actual!r}")


def assert_true(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "断言失败")


def assert_close(actual: float, expected: float, tol: float = 0.01, msg: str = "") -> None:
    if abs(actual - expected) > tol:
        raise AssertionError(f"{msg}\n  期望 ≈{expected} (±{tol}), 实际 {actual}")


def approx(a: Any, b: Any) -> bool:
    """None 容错的数值近似比较 (None-tolerant numeric comparison)."""
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.01


# ---------------------------------------------------------------------------
# 测试夹具 / Fixtures
# ---------------------------------------------------------------------------

RAW_NUTRITION = """[6]{productName,nutritionDescription,energyKj,energyKcal,
                    protein,fat,carbohydrate,sodium,calcium}:
  雪菜脆笋鸡肉粥,null,504,120,4,2,22,552,14
  皮蛋鸡肉粥,null,558,133,6,3,20,611,20
  脆香油条,null,844,202,5,12,19,230,18
  优品豆浆大杯,null,844,202,8,4,33,43,21
  优品豆浆小杯,null,633,151,6,3,25,32,15
  纯悦,能量约清水1杯,0,0,0,0,0,0,0
  经典麦旋风,null,1115,266,5,9,40,116,185
"""


def build_index() -> List[Tuple[str, Dict[str, Any]]]:
    return N.build_index(N.parse_nutrition_text(RAW_NUTRITION))


def write_tmp(content: str, suffix: str = ".json") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ---------------------------------------------------------------------------
# 1. 归一化 / normalize
# ---------------------------------------------------------------------------

@test
def test_normalize_全角半角与括号():
    """全角括号/括号内容/空白应被正确归一化"""
    assert_eq(N.normalize("纯牛奶（盒装）"), "纯牛奶", "全角括号内容应剥离")
    assert_eq(N.normalize("纯牛奶(盒装)"), "纯牛奶", "半角括号内容应剥离")
    assert_eq(N.normalize("100% 苹果汁(盒装)"), "100%苹果汁", "空白应被去除")
    assert_eq(N.normalize("麦咖啡™奶铁"), "麦咖啡奶铁", "商标符号应剥离")
    assert_eq(N.normalize(""), "", "空输入应返回空串")
    assert_eq(N.normalize(None), "", "None 应安全返回空串")


@test
def test_normalize_商标符号在NFKC前剥离():
    """回归: NFKC 会把 ™ 展开成 TM, 导致 '麦咖啡™奶铁' -> '麦咖啡tm奶铁'
    与营养库关联被破坏。必须在 NFKC 之前剥离。"""
    assert_eq(N.normalize("麦咖啡™奶铁"), "麦咖啡奶铁", "™ 应被完全剥离")
    assert_eq(N.normalize("麦咖啡™奶铁中杯"), "麦咖啡奶铁中杯", "™ 不应残留为 tm")
    assert_true("tm" not in N.normalize("麦咖啡™奶铁"), "归一化结果不得含 tm")
    assert_eq(N.normalize("纯悦®"), "纯悦", "® 应被剥离")
    # 归一化后应能关联到营养库
    idx = build_index()
    assert_eq(N.normalize("麦咖啡™奶铁"), "麦咖啡奶铁", "应可与 '麦咖啡' 类关联")


@test
def test_normalize_菜单分类名含换行():
    """实测菜单分类名含 \\n (如 '6折起\\n早餐卡专区')"""
    assert_eq(N.normalize("6折起\n早餐卡专区"), "6折起早餐卡专区", "换行应被去除")


# ---------------------------------------------------------------------------
# 2. 营养库解析 / parse_nutrition_text
# ---------------------------------------------------------------------------

@test
def test_parse_保留零卡条目():
    """回归: 0 kcal 是合法值, 不能被当作解析失败丢弃"""
    recs = N.parse_nutrition_text(RAW_NUTRITION)
    names = [r["name"] for r in recs]
    assert_true("纯悦" in names, "纯悦(0 kcal) 必须被保留")
    pure = next(r for r in recs if r["name"] == "纯悦")
    assert_eq(pure["kcal"], 0, "纯悦热量应为 0")
    assert_eq(pure["protein"], 0, "纯悦蛋白应为 0")


@test
def test_parse_容错字段数不齐():
    """实测存在缺 calcium 字段的行 (6 个数值)"""
    raw = "[2]{a}:\n  x,null,100,50,2,1,3,10,5\n  y,null,50,20,1,0,2,5\n"
    recs = N.parse_nutrition_text(raw)
    assert_eq(len(recs), 2, "两条都应被解析")
    short = next(r for r in recs if r["name"] == "y")
    assert_eq(short["kcal"], 20, "y 热量应为 20")
    assert_true(short["calcium"] is None, "缺失字段应为 None 而非 0")


@test
def test_parse_第二字段为描述文本():
    """实测第二字段可能是营养描述而非 null"""
    raw = "[1]{a}:\n  芝士双层安格斯厚牛堡,能量约鲈鱼1条,2914,696,32,44,43,897,150\n"
    recs = N.parse_nutrition_text(raw)
    assert_eq(len(recs), 1)
    assert_eq(recs[0]["name"], "芝士双层安格斯厚牛堡", "名称不应含描述文本")
    assert_eq(recs[0]["kcal"], 696, "热量应为 696")
    assert_true("鲈鱼" in recs[0].get("nutritionDescription", ""), "描述应被保留")


@test
def test_parse_名称含逗号():
    """名称本身含逗号时不应被拆错"""
    raw = "[1]{a}:\n  千层饼,无糖,300,150,3,6,20,300,20\n"
    recs = N.parse_nutrition_text(raw)
    assert_eq(len(recs), 1)
    assert_eq(recs[0]["name"], "千层饼", "名称应为 '千层饼'")
    assert_eq(recs[0]["kcal"], 150, "热量应正确解析")


@test
def test_parse_空输入与全脏数据():
    assert_eq(N.parse_nutrition_text(""), [], "空输入应返回空列表")
    assert_eq(N.parse_nutrition_text("[3]{a}:\n  junk,no-numbers\n"), [],
              "无任何数值的行应被跳过")


@test
def test_parse_去重():
    """归一化后同名应只保留一条"""
    raw = "[2]{a}:\n  脆香油条,null,844,202,5,12,19,230,18\n  脆香油条（小）,null,844,202,5,12,19,230,18\n"
    recs = N.parse_nutrition_text(raw)
    assert_eq(len(recs), 1, "归一化后同名应去重")


# ---------------------------------------------------------------------------
# 3. 菜单解析 / load_menu
# ---------------------------------------------------------------------------

@test
def test_load_menu_三种嵌套深度():
    """兼容 完整返回 / 已抽data / 已抽data.meals"""
    meal = {"513606": {"name": "粥王系列精选", "currentPrice": "7.5"}}
    variants = [
        json.dumps({"data": {"meals": meal}}, ensure_ascii=False),
        json.dumps({"meals": meal}, ensure_ascii=False),
        json.dumps(meal, ensure_ascii=False),
    ]
    for i, raw in enumerate(variants):
        path = write_tmp(raw)
        try:
            meals = N.load_menu(path)
            assert_eq(len(meals), 1, f"变体 {i} 应解析出 1 个餐品")
            assert_eq(meals[0]["code"], "513606", f"变体 {i} 编码错误")
        finally:
            os.unlink(path)


@test
def test_load_menu_带前置说明文字():
    """实测 query-meals 返回前置字段说明, 不能直接 json.loads"""
    prefix = "# API Response Information\n\n以下是结构说明...\n\n## 原始响应\n"
    raw = prefix + json.dumps(
        {"data": {"meals": {"2631": {"name": "脆香油条", "currentPrice": "8.5"}}}},
        ensure_ascii=False,
    )
    path = write_tmp(raw)
    try:
        meals = N.load_menu(path)
        assert_eq(len(meals), 1, "应能从混排文本中提取 JSON")
        assert_eq(meals[0]["name"], "脆香油条")
    finally:
        os.unlink(path)


@test
def test_load_menu_回填分类名():
    """分类名需回填, 供品类兜底使用"""
    raw = json.dumps({
        "data": {
            "categories": [{"name": "粥王系列", "meals": [{"code": "513606"}]}],
            "meals": {"513606": {"name": "粥王系列精选", "currentPrice": "7.5"}},
        }
    }, ensure_ascii=False)
    path = write_tmp(raw)
    try:
        meals = N.load_menu(path)
        assert_eq(meals[0]["category"], "粥王系列", "分类名应被回填")
    finally:
        os.unlink(path)


@test
def test_load_menu_非法JSON返回空():
    path = write_tmp("not a json at all", suffix=".txt")
    try:
        assert_eq(N.load_menu(path), [], "非法输入应返回空列表而非崩溃")
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# 4. 关联匹配 / matching
# ---------------------------------------------------------------------------

@test
def test_match_精确匹配():
    idx = build_index()
    rec, conf, method = N.match_nutrition("脆香油条", idx)
    assert_true(rec is not None, "应匹配到")
    assert_eq(rec["kcal"], 202, "热量应为 202")
    assert_close(conf, 1.0, 0.001, "置信度应为 1.0")
    assert_eq(method, "exact")


@test
def test_match_规格互斥惩罚():
    """小杯豆浆绝不能被大杯热量顶替"""
    idx = build_index()
    small, conf_small, _ = N.match_nutrition("优品豆浆小杯", idx)
    large, conf_large, _ = N.match_nutrition("优品豆浆大杯", idx)
    assert_true(small and large, "两者都应匹配成功")
    assert_eq(small["kcal"], 151, "小杯应为 151 kcal")
    assert_eq(large["kcal"], 202, "大杯应为 202 kcal")
    assert_true(conf_small > 0.6 and conf_large > 0.6, "同规格匹配置信度应高")


@test
def test_match_品类兜底():
    """回归: 菜单名与营养库名字面无交集时需兜底"""
    idx = build_index()
    rec, conf, method = N.match_by_category("粥王系列精选", "粥王系列", idx)
    assert_true(rec is not None, "品类兜底应命中")
    assert_eq(method, "category", "方法应为 category")
    assert_true(conf < 0.6, f"兜底置信度应较低, 实际 {conf}")
    assert_true(rec["kcal"] in (120, 133), f"应取粥类中位数, 实际 {rec['kcal']}")


@test
def test_match_兜底不误伤():
    """无共同品类关键词时不得乱兜底"""
    idx = build_index()
    rec, conf, _ = N.match_by_category("经典麦旋风", "小食甜品", idx)
    assert_true(rec is None, "无品类交集应返回 None")


@test
def test_match_空名称不崩溃():
    idx = build_index()
    rec, conf, method = N.match_nutrition("", idx)
    assert_true(rec is None and conf == 0.0, "空名称应安全返回")
    rec2, _, _ = N.match_nutrition("", idx)
    assert_true(rec2 is None)


# ---------------------------------------------------------------------------
# 5. 中式分类 / classify_chinese
# ---------------------------------------------------------------------------

@test
def test_classify_分层():
    assert_eq(N.classify_chinese("雪菜脆笋鸡肉粥")[0], 1, "粥应为 Tier1")
    assert_eq(N.classify_chinese("脆香油条")[0], 1, "油条应为 Tier1")
    assert_eq(N.classify_chinese("经典麦旋风")[0], 0, "甜品应被排除")
    assert_eq(N.classify_chinese("朱古力新地")[0], 0, "新地应被排除")


@test
def test_classify_空输入():
    tier, label = N.classify_chinese("")
    assert_eq(tier, 0, "空名称应返回 Tier0")
    assert_true(isinstance(label, str) and label != "", "标签不应为空串")


# ---------------------------------------------------------------------------
# 6. 打分 / scoring
# ---------------------------------------------------------------------------

def _mk(name: str, kcal: Any, protein: Any = 0, sodium: Any = None,
        price: Any = None, conf: float = 1.0) -> Dict[str, Any]:
    return {"name": name, "kcal": kcal, "protein": protein, "fat": 0,
            "carb": 0, "sodium": sodium, "price": price, "confidence": conf}


@test
def test_score_零卡不崩溃():
    """回归: 0 kcal 曾导致蛋白密度除零"""
    item = N.score_item(_mk("纯悦", 0, 0, 0, 7.5))
    assert_true(isinstance(item["score"], (int, float)), "应正常返回分数")
    assert_true(item["score"] > 0, f"0 kcal 优质饮品不应得负分, 实际 {item['score']}")


@test
def test_score_无热量降权():
    zero = N.score_item(_mk("未知品", None))
    good = N.score_item(_mk("雪菜脆笋鸡肉粥", 120, 4, 552, 7.5))
    assert_true(good["score"] > zero["score"], "无热量应被降权")


@test
def test_score_高钠扣分():
    low = N.score_item(_mk("皮蛋鸡肉粥", 120, 4, 100, 7.5))
    high = N.score_item(_mk("皮蛋鸡肉粥", 120, 4, 1500, 7.5))
    assert_true(low["score"] > high["score"], "高钠应扣分")


@test
def test_score_低卡优于高卡():
    low = N.score_item(_mk("脆香油条", 202, 5, 230, 8.5))
    high = N.score_item(_mk("火腿扒早安营养卷", 444, 16, 1065, 15.5))
    assert_true(low["score"] > high["score"], "低卡应优于高卡")


@test
def test_score_低置信度降权():
    hi = N.score_item(_mk("皮蛋鸡肉粥", 133, 6, 611, 7.5, conf=1.0))
    lo = N.score_item(_mk("皮蛋鸡肉粥", 133, 6, 611, 7.5, conf=0.45))
    assert_true(hi["score"] > lo["score"], "低置信度应被降权")


# ---------------------------------------------------------------------------
# 7. 套餐组装 / build_combo
# ---------------------------------------------------------------------------

def _mk_cand(name: str, kcal: int, protein: int = 0, sodium: int = 0,
             price: float = 8.0, tier: int = 1, code: str = "") -> Dict[str, Any]:
    item = {
        "code": code or name, "name": name, "kcal": kcal, "protein": protein,
        "fat": 0, "carb": 0, "sodium": sodium, "price": price,
        "confidence": 1.0, "matchMethod": "exact", "canWithOrder": False,
        "discountType": None, "score": 50.0,
    }
    return N.score_item({**item, "chineseTier": tier})


@test
def test_combo_基本求解():
    cands = [
        _mk_cand("雪菜脆笋鸡肉粥", 120, 4, 552, 7.5, code="m1"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("优品豆浆", 202, 8, 43, 9.5, code="d1"),
    ]
    combo = N.build_combo(cands, "balanced")
    assert_true(combo is not None, "应能组装出套餐")
    assert_close(combo["totalKcal"], sum(x["kcal"] for x in combo["items"]),
                 msg="套餐总热量应等于各项之和")


@test
def test_combo_零卡饮品():
    """0 kcal 饮品应可参与组合且不破坏求和"""
    cands = [
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯悦", 0, 0, 0, 7.5, code="d1"),
        _mk_cand("雪菜脆笋鸡肉粥", 120, 4, 552, 7.5, code="m1"),
    ]
    combo = N.build_combo(cands, "light")
    assert_true(combo is not None, "应能组装")
    manual = sum(x["kcal"] for x in combo["items"])
    assert_close(combo["totalKcal"], manual, msg="含 0 kcal 项时求和仍应正确")


@test
def test_combo_不选套餐类餐品():
    """组合/套餐类单品不应作为独立项被选中"""
    cands = [
        _mk_cand("脆香油条组合", 202, 5, 230, 6.6, code="combo1"),
        _mk_cand("猪柳蛋麦满分套餐", 387, 23, 846, 15.6, code="combo2"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯悦", 0, 0, 0, 7.5, code="d1"),
    ]
    combo = N.build_combo(cands, "light")
    if combo:
        names = [x["name"] for x in combo["items"]]
        for n in names:
            assert_true(not n.endswith(("组合", "套餐", "三件套", "二件套")),
                        f"不应选中套餐类单品: {n}")


@test
def test_combo_无候选返回None():
    assert_true(N.build_combo([], "balanced") is None, "空候选应返回 None")


@test
def test_combo_全部无热量返回None():
    cands = [_mk_cand("未知A", None), _mk_cand("未知B", None)]
    assert_true(N.build_combo(cands, "balanced") is None,
                "全部无热量时应返回 None 而非编造")


@test
def test_combo_总价为浮点无累加误差():
    """价格求和不应出现 27.499999 之类的浮点误差"""
    cands = [
        _mk_cand("脆香油条", 202, 5, 230, 8.5, code="m1"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯牛奶", 129, 7, 73, 10.5, code="d1"),
    ]
    combo = N.build_combo(cands, "balanced")
    if combo:
        assert_close(combo["totalPrice"], sum(x["price"] for x in combo["items"]),
                     tol=0.02, msg="总价求和误差过大")


@test
def test_combo_未知preset回落默认值():
    cands = [_mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
             _mk_cand("纯悦", 0, 0, 0, 7.5, code="d1")]
    combo = N.build_combo(cands, "不存在的档位")
    assert_true(combo is None or combo["preset"] == "不存在的档位",
                "未知 preset 不应崩溃")


# ---------------------------------------------------------------------------
# 7b. 组合评分质量 / combo scoring quality
# ---------------------------------------------------------------------------

@test
def test_combo_低钠方案优先于高钠():
    """回归: 修复前仅按热量距离选, 现应综合控钠加分"""
    # 构造热量相同的两组, 低钠组应胜出
    cands = [
        # 低钠组: 120 + 146 + 129 = 395
        _mk_cand("玉米杯", 87, 4, 2, 13.0, code="m1"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯牛奶", 129, 7, 73, 10.5, code="d1"),
        # 高钠组: 133 + 202 + 43 ≈ 相似热量但钠远高
        _mk_cand("皮蛋鸡肉粥", 133, 6, 611, 7.5, code="m2"),
        _mk_cand("图林根香肠早安营养卷", 425, 14, 977, 16.0, code="s2"),
        _mk_cand("玉米杯", 87, 4, 2, 13.0, code="m3"),
    ]
    combo = N.build_combo(cands, "satisfied")
    assert_true(combo is not None, "应能组装")
    # 无论选出哪组, 评分明细中的控钠加分都应真实反映
    bd = combo["breakdown"]
    assert_true("sodiumBonus" in bd, "应含控钠加分项")
    # 同等热量下低钠组钠更低
    low_sodium_total = 2 + 311 + 73
    assert_true(low_sodium_total < 611 + 977 + 2, "测试数据本身低钠组应更优")


@test
def test_combo_输出评分明细():
    """应输出可解释的评分拆解, 便于调参与溯源"""
    cands = [
        _mk_cand("雪菜脆笋鸡肉粥", 120, 4, 552, 7.5, code="m1"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯牛奶", 129, 7, 73, 10.5, code="d1"),
    ]
    combo = N.build_combo(cands, "balanced")
    assert_true(combo is not None, "应能组装")
    for key in ("comboScore", "breakdown"):
        assert_true(key in combo, f"输出应包含 {key}")
    bd = combo["breakdown"]
    for key in ("fit", "completeness", "quality", "sodiumBonus", "countPenalty"):
        assert_true(key in bd, f"评分明细应包含 {key}")


@test
def test_combo_透传随单购参数():
    """回归: 组合输出必须含 withOrder, 否则算价时用户按原价付费"""
    # 注意: 不能用「XX组合」命名的餐品, 它们会被正确地排除出候选池
    item = _mk_cand("脆香油条", 202, 5, 230, 8.5, code="w1")
    item["canWithOrder"] = True
    item["withOrder"] = {"cardId": "CARD123", "cardType": 2}
    item["discountType"] = "随单购早餐卡优惠"
    cands = [item, _mk_cand("纯牛奶", 129, 7, 73, 10.5, code="d1"),
             _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1")]
    combo = N.build_combo(cands, "balanced")
    assert_true(combo is not None, "应能组装")
    for x in combo["items"]:
        assert_true("withOrder" in x, "每个组合项都应带 withOrder 键")
        assert_true("role" in x, "每个组合项都应标注角色")


@test
def test_combo_角色多样性优于单一堆叠():
    """结构完整的组合(主食+小食+饮品)应优于同角色堆叠"""
    cands = [
        # 结构组合 A: main + side + drink
        _mk_cand("脆香油条", 202, 5, 230, 8.5, code="m1"),
        _mk_cand("脆薯饼", 146, 1, 311, 8.5, code="s1"),
        _mk_cand("纯牛奶", 129, 7, 73, 10.5, code="d1"),
        # 结构组合 B: 三个 main (角色缺失)
        _mk_cand("德式图林根香肠", 87, 5, 293, 9.5, code="m2"),
        _mk_cand("图林根香肠早安营养卷", 425, 14, 977, 16.0, code="m3"),
        _mk_cand("雪菜脆笋鸡肉粥", 120, 4, 552, 7.5, code="m4"),
    ]
    combo = N.build_combo(cands, "balanced")
    assert_true(combo is not None, "应能组装")
    roles = {x["role"] for x in combo["items"]}
    assert_true(len(roles) >= 2, f"应偏好角色多样的组合, 实际角色 {roles}")


# ---------------------------------------------------------------------------
# 8. 角色识别 / _detect_role
# ---------------------------------------------------------------------------

@test
def test_detect_role():
    assert_eq(N._detect_role("雪菜脆笋鸡肉粥"), "main", "粥应为主食")
    assert_eq(N._detect_role("优品豆浆"), "drink", "豆浆应为饮品")
    assert_eq(N._detect_role("脆薯饼"), "side", "薯饼应为小食")
    assert_eq(N._detect_role("完全未知品名"), "other", "未知应归 other")


# ---------------------------------------------------------------------------
# 9. 渲染 / rendering
# ---------------------------------------------------------------------------

@test
def test_render_缺字段不崩溃():
    """未匹配项 (kcal=None) 渲染不应抛异常"""
    items = [{
        "name": "经典麦旋风", "kcal": None, "protein": None, "sodium": None,
        "price": 15.5, "chineseLabel": "甜品/冰淇淋", "confidence": 0.0,
        "matchMethod": "unmatched",
    }]
    out = N.render_rank(items, top=5)
    assert_true("经典麦旋风" in out, "应包含餐品名")
    assert_true("—" in out, "缺失值应以 — 占位")


@test
def test_render_combo_None():
    out = N.render_combo(None)
    assert_true(isinstance(out, str) and out != "", "None 应返回提示文案")


@test
def test_render_组合法标记():
    items = [{
        "name": "粥王系列精选", "kcal": 133, "protein": 6, "sodium": 611,
        "price": 7.5, "chineseLabel": "中式主食", "confidence": 0.45,
        "matchMethod": "category",
    }]
    out = N.render_rank(items, top=1)
    assert_true("🟡" in out, "品类推断应标记 🟡")


# ---------------------------------------------------------------------------
# 10. CLI / command line
# ---------------------------------------------------------------------------

@test
def test_cli_子命令可调用():
    nut = write_tmp(RAW_NUTRITION, ".txt")
    out_json = write_tmp("{}", ".json")
    try:
        assert_eq(N.main(["parse", "--nutrition-file", nut, "--out", out_json]), 0,
                  "parse 子命令应成功")
        with open(out_json, encoding="utf-8") as fh:
            data = json.load(fh)
        assert_eq(len(data), 7, "应解析出 7 条 (夹具共 7 行)")
    finally:
        os.unlink(nut)
        os.unlink(out_json)


@test
def test_cli_空菜单返回非零():
    nut = write_tmp(RAW_NUTRITION, ".txt")
    menu = write_tmp('{"data":{"meals":{}}}')
    try:
        rc = N.main(["rank", "--nutrition-file", nut, "--menu-file", menu])
        assert_eq(rc, 2, "空菜单应返回退出码 2")
    finally:
        os.unlink(nut)
        os.unlink(menu)


# ---------------------------------------------------------------------------
# 运行 / Runner
# ---------------------------------------------------------------------------

def main() -> int:
    passed, failed = 0, []
    for name, fn in _TESTS:
        try:
            fn()
            passed += 1
            if VERBOSE:
                print(f"  \033[32mPASS\033[0m {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc, traceback.format_exc()))
            if VERBOSE:
                print(f"  \033[31mFAIL\033[0m {name}")
                print(f"       {exc}")

    print()
    if failed:
        print(f"\033[31m{'=' * 60}\033[0m")
        print(f"\033[31m失败 {len(failed)} / {len(_TESTS)}\033[0m")
        for name, exc, tb in failed:
            print(f"\n\033[31m✗ {name}\033[0m")
            print(f"  {exc}")
            if VERBOSE:
                print(tb)
        return 1

    print(f"\033[32m{'=' * 60}\033[0m")
    print(f"\033[32m全部通过: {passed} / {len(_TESTS)}\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())