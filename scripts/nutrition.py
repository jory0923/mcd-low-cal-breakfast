#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_low_cal_breakfast 营养计算与低卡中式早餐排序引擎
Nutrition engine & low-calorie Chinese-style breakfast ranking engine.

用途 / Purpose:
    1. 解析 `mcd-mcp.list-nutrition-foods` 返回的原始文本, 产出结构化营养档案 (Structured nutrition profile)
    2. 将门店菜单 (`mcd-mcp.query-meals`) 与营养库做 **确定性关联** (deterministic join)
    3. 按「中式 + 低卡 + 高蛋白密度」打分, 输出轻食 / 均衡 / 饱腹三档方案

设计原则 / Design principles:
    - 卡路里必须是 **查表计算**, 不能由模型估算 (Calories MUST be looked up, never estimated by an LLM)
    - 所有匹配都带置信度, 低置信度必须显式标注为「估算」(Every join carries a confidence score)
    - 纯标准库实现, 无第三方依赖 (Pure standard library, zero third-party dependencies)

命令行 / CLI:
    python nutrition.py parse  --nutrition-file raw.txt --out nutrition.json
    python nutrition.py rank  --nutrition-file raw.txt --menu-file menu.json \
                              --budget light --top 8
    python nutrition.py combo --nutrition-file raw.txt --menu-file menu.json \
                              --preset balanced
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. 中式早餐关键词库 / Chinese-style breakfast lexicon
# ---------------------------------------------------------------------------
# 分层设计: 分层越高, 越"中式", 在打分中获得更高权重
# Tiered lexicon: the higher the tier, the more "Chinese-style" the item is,
# and the more weight it receives during scoring.

CHINESE_TIER1_CORE: Tuple[str, ...] = (
    "粥", "油条", "豆浆", "香肠", "玉米杯", "脆薯饼", "咸菜", "腐乳", "豆花",
    "粽子", "汤圆", "烧饼", "油饼", "灌汤包", "肠粉", "河粉", "米粉", "面条",
    "porridge", "youtiao", "soymilk", "sausage",
)
"""Tier-1: 典型中式早餐载体 (canonical Chinese breakfast carriers)."""

CHINESE_TIER2_EXTENDED: Tuple[str, ...] = (
    "营养卷", "炒双蛋", "蛋堡", "三明治", "卷", "汉堡包", "猪柳", "火腿扒",
    "脆薯饼", "燕麦", "牛奶", "纯悦", "苹果", "美式", "红茶",
    "wrap", "sandwich", "oat", "milk",
)
"""Tier-2: 中西融合 / 早餐配套 (Chinese-Western fusion / breakfast accompaniments)."""

CHINESE_TIER3_WEAK: Tuple[str, ...] = (
    "麦满分", "麦咖啡", "咖啡", "派", "蛋挞", "小食", "薯条", "鸡翅",
    "pancake", "coffee", "pie",
)
"""Tier-3: 中式属性较弱, 仅在无中式候选时作为兜底 (weak Chinese character)."""

# 明确排除的高卡品类 / Explicitly excluded high-calorie categories
EXCLUDE_KEYWORDS: Tuple[str, ...] = (
    "新地", "麦旋风", "雪冰", "奶昔", "巧克力", "冰淇淋", "圆筒", "派",
)
"""Desserts & ice cream — never part of a low-cal breakfast plan."""

# 低卡分档 / Calorie tiers (kcal, 单品)
TIER_LITE_MAX = 150       # 轻食档上限
TIER_BALANCED_MAX = 250   # 均衡档上限
TIER_HEAVY_MAX = 400      # 饱腹档上限

# 三档套餐总热量目标 / Total calorie targets per preset (kcal)
PRESET_TARGETS: Dict[str, int] = {
    "light": 320,       # 轻食
    "balanced": 480,     # 均衡
    "satisfied": 680,    # 饱腹
}

# 钠警戒线 (mg) / Sodium advisory threshold per single serving
SODIUM_ADVISORY_MG = 800
"""中国居民膳食指南建议每日钠摄入 < 2000mg, 早餐一份建议 < 800mg."""


# ---------------------------------------------------------------------------
# 2. 文本归一化 / Text normalization
# ---------------------------------------------------------------------------

_TRADEMAR_CHARS = str.maketrans({"™": "", "®": "", "　": ""})
"""商标符号清理 / Trademark cleanup.

⚠️ 必须在 NFKC **之前**执行: NFKC 会把 '™'(U+2122) 展开成 'TM',
   一旦展开就再也删不掉, 导致 '麦咖啡™奶铁' 变成 '麦咖啡tm奶铁',
   与营养库 '奶铁' 的关联被破坏 (真实菜单中确有此类餐品名)。
"""

_TRAILING_TM = re.compile(r"tm$", re.IGNORECASE)
"""NFKC 展开后残留的商标后缀 / Trademark suffix left after NFKC expansion."""


def normalize(text: str) -> str:
    """
    归一化餐品名称, 用于营养库关联 (Normalize product name for nutrition lookup).

    处理顺序: 商标符号 → 全角转半角 → 去括号内容 → 去空白 → 小写
    Order matters: trademark symbols MUST be stripped before NFKC.
    """
    if not text:
        return ""
    out = str(text)
    # 第 1 步: 先剥离商标符号 (必须先于 NFKC) / strip trademarks FIRST
    out = out.translate(_TRADEMAR_CHARS)
    # 第 2 步: 再做 NFKC 归一化 / then NFKC
    out = unicodedata.normalize("NFKC", out)
    # 第 3 步: 兜底清理 NFKC 展开出的 TM 后缀 / clean expanded TM suffix
    out = _TRAILING_TM.sub("", out)
    # 去掉括号内的补充说明: 纯牛奶(盒装) -> 纯牛奶
    out = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", out)
    # 去掉所有空白
    out = re.sub(r"\s+", "", out)
    return out.strip().lower()


# ---------------------------------------------------------------------------
# 3. 营养库解析 / Nutrition profile parsing
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

# 营养字段顺序 (来自接口 schema) / Nutrition field order (per API schema)
_NUTRI_FIELDS: Tuple[str, ...] = (
    "energyKj", "energyKcal", "protein", "fat",
    "carbohydrate", "sodium", "calcium",
)


def _to_float(token: str) -> Optional[float]:
    """安全转浮点 / Safe float cast."""
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def parse_nutrition_text(raw: str) -> List[Dict[str, Any]]:
    """
    解析 `list-nutrition-foods` 的原始返回文本 (Parse the raw nutrition payload).

    真实返回格式 (verified against the live MCP server, 2026-10):
        [160]{productName,nutritionDescription,energyKj,energyKcal,
               protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213

    注意 / NOTE: 部分行字段数不足 (如 `yeyeyeye奶冻款,null,932,223,1,4,43,37,64`
    缺少 calcium), 因此采用 **从右向左** 扫描数值字段的容错策略。
    部分行的第二个字段是营养描述文本而非 null (如 `芝士双层安格斯厚牛堡,
    能量约鲈鱼1条,2914,696,...`), 需一并处理。

    Returns:
        List[Dict]: 每个元素含 name / kcal / protein / fat / carb / sodium / calcium
    """
    if not raw:
        return []

    records: List[Dict[str, Any]] = []
    seen: set = set()

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "{" in line:
            continue

        tokens = [t.strip() for t in line.split(",")]
        if len(tokens) < 3:
            continue

        # 从右向左收集连续数值字段 / Collect trailing numeric fields right-to-left
        nums: List[float] = []
        idx = len(tokens) - 1
        while idx >= 0 and len(nums) < len(_NUTRI_FIELDS):
            val = _to_float(tokens[idx])
            if val is None:
                break
            nums.insert(0, val)
            idx -= 1

        # 有效性判定 **只看字段数**, 不看热量值。
        # 原因: 「纯悦」「无糖可口可乐」等真实存在的 0 kcal 饮品会被误删,
        #       而它们恰恰是低卡早餐的最优解, 绝不能因值为 0 而丢弃。
        # Valid rows need >= 2 numeric fields (energyKj + energyKcal).
        if len(nums) < 2:
            continue

        head_tokens = tokens[: idx + 1]
        if not head_tokens:
            continue
        name = head_tokens[0]
        if not name:
            continue

        key = normalize(name)
        if not key or key in seen:
            continue
        seen.add(key)

        rec: Dict[str, Any] = {
            "name": name,
            "kcal": int(round(nums[1] if len(nums) > 1 else nums[0])),
            "energyKj": int(round(nums[0])),
        }
        for offset, field in enumerate(_NUTRI_FIELDS[2:], start=2):
            rec[field] = int(round(nums[offset])) if len(nums) > offset else None

        # 名称中若内嵌热量描述 (如「能量约清水1杯」), 保留以便人工核对
        if len(head_tokens) > 1:
            rec["nutritionDescription"] = head_tokens[1]

        records.append(rec)

    return records


def load_nutrition(path: str) -> List[Dict[str, Any]]:
    """
    载入营养档案 (Load the nutrition profile).

    支持两种来源 (two accepted sources):
      - `list-nutrition-foods` 的原始文本输出
      - 本脚本 `parse` 子命令生成的 JSON 文件
    """
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    stripped = content.lstrip()
    if stripped.startswith("[") and "{" in content[:200]:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = data.get("nutrition", [])
            return data
        except json.JSONDecodeError:
            pass

    return parse_nutrition_text(content)


# ---------------------------------------------------------------------------
# 4. 菜单解析 / Menu parsing
# ---------------------------------------------------------------------------

def extract_json_blob(text: str) -> Any:
    """
    从可能带有前后缀说明的文本中提取 JSON (Extract JSON from a noisy text blob).

    `query-meals` 的返回通常在最前面附带一段字段说明, 因此不能直接 json.loads。
    """
    if not text:
        return None

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 扫描首个 '{' 与最后一个 '}' 之间的内容
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _locate_meal_map(node: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    """
    在任意嵌套结构中定位餐品字典 (Locate the meal map inside an arbitrarily nested structure).

    真实返回存在三种嵌套深度 (three real-world nesting depths):
        {"data": {"meals": {...}}}          # 标准接口返回
        {"meals": {...}}                     # 已抽出 data
        {"513606": {...}}                    # 已抽出 data.meals

    因此不能写死层数, 必须逐层下探并验证「值是否为含 name 的字典」。
    """
    if depth > 6 or not isinstance(node, dict):
        return None

    # 当前层自身即为餐品字典? / Is this level itself the meal map?
    meal_like = [v for v in node.values() if isinstance(v, dict) and v.get("name")]
    if meal_like:
        return node

    # 优先沿着 "meals" 下探 / descend via "meals" first
    for key in ("meals", "data", "result"):
        if key in node:
            found = _locate_meal_map(node[key], depth + 1)
            if found:
                return found

    # 兜底: 扫描其他子字典 / fallback: scan remaining child dicts
    for value in node.values():
        if isinstance(value, dict):
            found = _locate_meal_map(value, depth + 1)
            if found:
                return found
    return None


def load_menu(path: str) -> List[Dict[str, Any]]:
    """
    载入门店菜单并返回餐品列表 (Load the store menu and return the meal list).

    兼容三种结构: 完整接口返回 / 已抽出 data / 已抽出 data.meals
    同时回填每个餐品所属的菜单分类名 (also back-fills the owning category name),
    供分类级兜底关联使用。
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = extract_json_blob(fh.read())
    if data is None:
        return []

    meal_map = _locate_meal_map(data)
    if not meal_map:
        return []

    # 建立 code -> 分类名 映射 / build the code -> category-name mapping
    category_of: Dict[str, str] = {}
    categories = _locate_categories(data)
    for cat in categories:
        cat_name = cat.get("name", "") if isinstance(cat, dict) else ""
        for meal_ref in cat.get("meals", []) or []:
            if isinstance(meal_ref, dict) and meal_ref.get("code") is not None:
                category_of[str(meal_ref["code"])] = cat_name

    meals: List[Dict[str, Any]] = []
    for code, item in meal_map.items():
        if isinstance(item, dict) and item.get("name"):
            meals.append({
                "code": str(code),
                "name": item.get("name", ""),
                "currentPrice": item.get("currentPrice"),
                "originalPrice": item.get("originalPrice"),
                "discountType": item.get("discountType"),
                "canWithOrder": item.get("canWithOrder", False),
                "withOrder": item.get("withOrder"),
                "image": item.get("image"),
                "category": category_of.get(str(code), ""),
            })
    return meals


def _locate_categories(data: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """定位菜单分类数组 (Locate the categories array)."""
    if depth > 6:
        return []
    if isinstance(data, dict):
        cats = data.get("categories")
        if isinstance(cats, list) and cats:
            return [c for c in cats if isinstance(c, dict)]
        for key in ("data", "result", "meals"):
            if key in data:
                found = _locate_categories(data[key], depth + 1)
                if found:
                    return found
    return []


# ---------------------------------------------------------------------------
# 5. 营养关联 / Nutrition join (the deterministic core)
# ---------------------------------------------------------------------------

def _contains_score(menu_key: str, nutr_key: str) -> float:
    """
    包含关系打分 (Containment scoring).

    菜单 `粥王系列精选` 与营养库 `雪菜脆笋鸡肉粥` 语义相关但字面无包含关系,
    故此函数只处理**字面包含**, 语义相似由 `_token_overlap` 兜底。
    """
    if not menu_key or not nutr_key:
        return 0.0
    if menu_key == nutr_key:
        return 1.0
    if nutr_key in menu_key:
        # 营养库名更长(更具体)时置信度更高 / longer, more specific name wins
        return 0.92 if len(nutr_key) >= 3 else 0.80
    if menu_key in nutr_key:
        return 0.88 if len(menu_key) >= 3 else 0.78
    return 0.0


def _token_overlap(menu_key: str, nutr_key: str, token_len: int = 2) -> float:
    """
    字符 n-gram 重合度打分 (Character n-gram overlap scoring).

    用于捕捉「同品类不同命名」的情况, 例如:
        菜单「粥王系列精选」 vs 营养「雪菜脆笋鸡肉粥」 -> 共享 "鸡肉"
        菜单「优品豆浆」     vs 营养「优品豆浆小杯」-> 已被包含关系覆盖
    """
    if not menu_key or not nutr_key:
        return 0.0
    grams_a = {menu_key[i:i + token_len] for i in range(len(menu_key) - token_len + 1)}
    grams_b = {nutr_key[i:i + token_len] for i in range(len(nutr_key) - token_len + 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


def _volume_penalty(menu_key: str, nutr_key: str) -> float:
    """
    杯型/规格不一致惩罚 (Size-spec mismatch penalty).

    「小杯豆浆」(151 kcal) 绝不能被「大杯豆浆」(202 kcal) 顶替, 否则低卡计算失真。
    """
    small_markers = ("小杯", "迷你", "小份", "single", "small")
    large_markers = ("大杯", "特大", "加大", "双层", "大份", "large", "double")

    menu_small = any(m in menu_key for m in small_markers)
    menu_large = any(m in menu_key for m in large_markers)
    nutr_small = any(m in nutr_key for m in small_markers)
    nutr_large = any(m in nutr_key for m in large_markers)

    if menu_small and nutr_large:
        return -0.30
    if menu_large and nutr_small:
        return -0.30
    return 0.0


def build_index(nutrition: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    """构建归一化索引 (Build the normalized lookup index)."""
    index: List[Tuple[str, Dict[str, Any]]] = []
    for rec in nutrition:
        key = normalize(rec.get("name", ""))
        if key:
            index.append((key, rec))
    return index


def match_nutrition(
    menu_name: str,
    index: List[Tuple[str, Dict[str, Any]]],
    threshold: float = 0.30,
) -> Tuple[Optional[Dict[str, Any]], float, str]:
    """
    将单个菜单餐品关联到营养档案 (Join one menu item to the nutrition profile).

    Returns:
        (nutrition_record | None, confidence 0~1, method)
    """
    menu_key = normalize(menu_name)
    if not menu_key:
        return None, 0.0, "none"

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_method = "none"

    for nutr_key, rec in index:
        score = _contains_score(menu_key, nutr_key)

        if score <= 0.0:
            # 字面不匹配时退回 n-gram 重合度 / fall back to n-gram overlap
            score = _token_overlap(menu_key, nutr_key)
            if score > 0.0:
                score = min(score, 0.55)  # n-gram 匹配置信度上限较低

        score += _volume_penalty(menu_key, nutr_key)

        if score > best_score:
            best_score, best, best_method = score, rec, "exact" if score >= 0.85 else "fuzzy"

    if best is None or best_score < threshold:
        return None, max(best_score, 0.0), "unmatched"

    return best, min(best_score, 1.0), best_method


def match_by_category(
    menu_name: str,
    category: str,
    index: List[Tuple[str, Dict[str, Any]]],
) -> Tuple[Optional[Dict[str, Any]], float, str]:
    """
    分类级兜底关联 (Category-level fallback join).

    真实场景中菜单名常为**品类统称**而营养库只有**具体品项**, 例如:
        菜单「粥王系列精选」 → 营养库「雪菜脆笋鸡肉粥」/「皮蛋鸡肉粥」
    此时字面匹配必然失败, 但二者同属「粥」品类。

    做法: 取「分类名 ∪ 餐品名」作为品类锚点, 找出同品类的所有营养项,
    取 **热量中位数** 作为该品类的代表值 (取中位数而非均值, 避免个别极端值拉偏),
    并返回较低置信度 (0.45) 以提示这是品类级推断而非精确匹配。
    """
    anchors = f"{category}{menu_name}"
    anchor_norm = normalize(anchors)
    if not anchor_norm:
        return None, 0.0, "unmatched"

    # 品类关键词 → 营养库品项必须包含该关键词 / category keyword must appear in nutrition name
    category_tokens = [
        t for t in CHINESE_TIER1_CORE + CHINESE_TIER2_EXTENDED
        if t in anchors or normalize(t) in anchor_norm
    ]
    if not category_tokens:
        return None, 0.0, "unmatched"

    hits: List[int] = []
    for nutr_key, rec in index:
        if any(t in rec.get("name", "") or normalize(t) in nutr_key for t in category_tokens):
            kcal = rec.get("kcal")
            if kcal and kcal > 0:
                hits.append(int(kcal))

    if not hits:
        return None, 0.0, "unmatched"

    hits.sort()
    median = hits[len(hits) // 2]

    # 构造一个代表性记录 / synthesize a representative record
    same = [
        rec for nutr_key, rec in index
        if rec.get("kcal") == median
        and any(t in rec.get("name", "") for t in category_tokens)
    ]
    if not same:
        return None, 0.0, "unmatched"

    rep = dict(same[0])
    rep["name"] = f"{rep['name']}（品类代表值·中位{kcal}kcal）"
    return rep, 0.45, "category"


# ---------------------------------------------------------------------------
# 6. 中式属性判定 / Chinese-style classification
# ---------------------------------------------------------------------------

def classify_chinese(name: str) -> Tuple[int, str]:
    """
    判定餐品的中式早餐属性 (Classify the Chinese-style breakfast character).

    Returns:
        (tier, label) — tier ∈ {0, 1, 2, 3}; label 为可读标签
    """
    key = normalize(name)

    if any(k in key for k in (normalize(t) for t in EXCLUDE_KEYWORDS)):
        return 0, "甜品/冰淇淋"

    if any(k in name for k in CHINESE_TIER1_CORE):
        return 1, "中式主食"
    if any(k in name for k in CHINESE_TIER2_EXTENDED):
        return 2, "中式/融合"
    if any(k in name for k in CHINESE_TIER3_WEAK):
        return 3, "中式属性弱"
    return 0, "非中式"


# ---------------------------------------------------------------------------
# 7. 打分 / Scoring
# ---------------------------------------------------------------------------

def score_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    对单个餐品打分 (Score a single menu item).

    打分维度 / Dimensions:
        - 中式层级 (Chinese tier)
        - 热量惩罚 (calorie penalty, 分档递减)
        - 蛋白质密度奖励 (protein density bonus)
        - 钠警示 (sodium advisory)
        - 营养关联置信度 (nutrition join confidence)
    """
    tier, label = classify_chinese(item["name"])
    kcal = item.get("kcal")
    protein = item.get("protein") or 0
    sodium = item.get("sodium")

    score = 0.0

    # 中式层级加权 / Chinese-tier weighting
    score += {1: 45.0, 2: 28.0, 3: 12.0, 0: -60.0}[tier]

    # 热量惩罚 / Calorie penalty (monotonically decreasing)
    if kcal is not None:
        if kcal <= TIER_LITE_MAX:
            score += 40.0
        elif kcal <= TIER_BALANCED_MAX:
            score += 26.0
        elif kcal <= TIER_HEAVY_MAX:
            score += 10.0
        else:
            score -= 18.0

        # 蛋白质密度奖励 (每 100kcal 的蛋白克数) / protein density bonus
        if kcal > 0:
            density = protein / kcal * 100.0
            score += min(density * 1.6, 22.0)
    else:
        score -= 25.0  # 无热量数据 → 不推荐

    # 关联置信度加权 / Join-confidence weighting
    score *= 0.6 + 0.4 * float(item.get("confidence", 0.0))

    # 钠警示 / Sodium advisory
    if sodium and sodium >= SODIUM_ADVISORY_MG:
        score -= 8.0

    # 价格效率 (低卡早餐不应过贵) / price efficiency
    price = item.get("price")
    if price is not None and price > 25:
        score -= 6.0

    item["chineseTier"] = tier
    item["chineseLabel"] = label
    item["score"] = round(score, 2)
    return item


def build_candidates(
    meals: List[Dict[str, Any]],
    index: List[Tuple[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    关联菜单与营养库并打分 (Join menu with nutrition, then score).

    无营养数据的餐品仍保留但会被大幅降权, 以便人工兜底。
    """
    candidates: List[Dict[str, Any]] = []

    for meal in meals:
        rec, confidence, method = match_nutrition(meal["name"], index)

        # 字面关联失败时, 退回分类级推断 / fall back to category-level inference
        if rec is None and meal.get("category"):
            rec, confidence, method = match_by_category(
                meal["name"], meal["category"], index,
            )

        price = None
        try:
            if meal.get("currentPrice") not in (None, ""):
                price = float(meal["currentPrice"])
        except (TypeError, ValueError):
            price = None

        item: Dict[str, Any] = {
            "code": meal["code"],
            "name": meal["name"],
            "price": price,
            "originalPrice": meal.get("originalPrice"),
            "discountType": meal.get("discountType"),
            "canWithOrder": meal.get("canWithOrder", False),
            "withOrder": meal.get("withOrder"),
            "kcal": rec.get("kcal") if rec else None,
            "protein": rec.get("protein") if rec else None,
            "fat": rec.get("fat") if rec else None,
            "carb": rec.get("carbohydrate") if rec else None,
            "sodium": rec.get("sodium") if rec else None,
            "calcium": rec.get("calcium") if rec else None,
            "confidence": round(confidence, 2),
            "matchMethod": method,
            "category": meal.get("category", ""),
            "nutritionName": rec.get("name") if rec else None,
        }
        candidates.append(score_item(item))

    return candidates


# ---------------------------------------------------------------------------
# 8. 套餐组装 / Combo assembly
# ---------------------------------------------------------------------------

# 套餐角色识别 / Combo role detection
ROLE_MAIN = ("粥", "油条", "卷", "堡", "麦满分", "炒双蛋", "香肠", "玉米杯", "wrap", "burger", "sandwich")
ROLE_DRINK = ("豆浆", "牛奶", "纯悦", "美式", "红茶", "咖啡", "果汁", "燕麦奶", "milk", "coffee", "juice", "tea")
ROLE_SIDE = ("薯饼", "脆薯", "蛋", "小食", "玉米", "饼")


def _detect_role(name: str) -> str:
    """识别餐品在套餐中的角色 (Detect the role of an item within a combo)."""
    key = normalize(name)
    if any(k in key for k in (normalize(r) for r in ROLE_MAIN)):
        return "main"
    if any(k in key for k in (normalize(r) for r in ROLE_DRINK)):
        return "drink"
    if any(k in key for k in (normalize(r) for r in ROLE_SIDE)):
        return "side"
    return "other"


def build_combo(
    candidates: List[Dict[str, Any]],
    preset: str = "balanced",
) -> Optional[Dict[str, Any]]:
    """
    组装一档低卡中式套餐 (Assemble one low-cal Chinese breakfast combo).

    策略: 主食(main) + 蛋白/小食(side) + 饮品(drink), 以总热量贴近 preset 目标为最优。
    Strategy: main + side + drink, optimizing proximity to the preset calorie target.

    仅使用「已成功关联营养数据」的餐品, 保证热量可校验。
    """
    target = PRESET_TARGETS.get(preset, PRESET_TARGETS["balanced"])

    pool = [
        c for c in candidates
        if c["kcal"] is not None
        and c["confidence"] >= 0.30
        and c["chineseTier"] in (1, 2)
        and not c["name"].lower().endswith(("组合", "套餐", "三件套", "二件套"))
    ]
    if not pool:
        pool = [c for c in candidates if c["kcal"] is not None and c["confidence"] >= 0.30]

    mains = [c for c in pool if _detect_role(c["name"]) == "main"]
    sides = [c for c in pool if _detect_role(c["name"]) == "side"]
    drinks = [c for c in pool if _detect_role(c["name"]) == "drink"]

    # 任一角色为空时才整体放宽为 pool。
    # 注意: 不可写成 `mains or pool`——那会让**所有**餐品同时充当
    # 主食/小食/饮品, 组合退化为「随便挑三样」, 失去结构意义。
    # Fallback only when a role is entirely missing (never `mains or pool`).
    if not (mains and sides and drinks):
        mains = mains or pool
        sides = sides or pool
        drinks = drinks or pool

    if not mains:
        return None

    best: Optional[Dict[str, Any]] = None
    best_score = float("-inf")

    for main in mains:
        for side in ([None] + sides):
            for drink in ([None] + drinks):
                picked = [x for x in (main, side, drink) if x is not None]
                # 去重 (同一餐品不重复) / de-duplicate
                unique: List[Dict[str, Any]] = []
                for x in picked:
                    if x["code"] not in {u["code"] for u in unique}:
                        unique.append(x)
                if not unique:
                    continue

                total_kcal = sum(x["kcal"] for x in unique)
                if total_kcal > target * 1.30:
                    continue
                if total_kcal < target * 0.45:
                    continue

                delta = abs(total_kcal - target)

                # ---- 组合综合评分 / composite combo score ----
                # 1) 热量贴合度 (满分 40) / calorie fit
                fit = 40.0 * (1.0 - delta / max(target * 0.55, 1.0))
                # 2) 角色完整度 (满分 24) / role completeness
                roles = {_detect_role(x["name"]) for x in unique}
                completeness = 8.0 * len({r for r in roles if r in ("main", "side", "drink")})
                # 3) 单品平均质量 (满分 20) / average item quality
                quality = sum(x["score"] for x in unique) / len(unique) * 0.2
                # 4) 控钠加分 (满分 16) / sodium control bonus
                total_sodium = sum(x["sodium"] or 0 for x in unique)
                sodium_bonus = 16.0 * (1.0 - min(total_sodium / (SODIUM_ADVISORY_MG * 2.0), 1.0))
                # 5) 单品数惩罚 (避免用堆数量凑热量) / item-count penalty
                count_penalty = 2.5 * max(0, len(unique) - 3)

                combo_score = fit + completeness + quality + sodium_bonus - count_penalty

                if combo_score <= best_score:
                    continue

                total_price = sum(x["price"] or 0.0 for x in unique)
                avg_score = sum(x["score"] for x in unique) / len(unique)

                best_score = combo_score
                best = {
                    "preset": preset,
                    "targetKcal": target,
                    "totalKcal": total_kcal,
                    "totalProtein": sum(x["protein"] or 0 for x in unique),
                    "totalSodium": total_sodium,
                    "totalFat": sum(x["fat"] or 0 for x in unique),
                    "totalCarb": sum(x["carb"] or 0 for x in unique),
                    "totalPrice": round(total_price, 2),
                    "avgScore": round(avg_score, 2),
                    "comboScore": round(combo_score, 2),
                    "breakdown": {
                        "fit": round(fit, 2),
                        "completeness": round(completeness, 2),
                        "quality": round(quality, 2),
                        "sodiumBonus": round(sodium_bonus, 2),
                        "countPenalty": round(-count_penalty, 2),
                    },
                    "items": [
                        {
                            "code": x["code"],
                            "name": x["name"],
                            "kcal": x["kcal"],
                            "protein": x["protein"],
                            "sodium": x["sodium"],
                            "price": x["price"],
                            "confidence": x["confidence"],
                            "discountType": x.get("discountType"),
                            "canWithOrder": x.get("canWithOrder", False),
                            # 随单购参数必须透传, 否则算价/下单时用户会按原价付费
                            # withOrder must be passed through or the user pays full price
                            "withOrder": x.get("withOrder"),
                            "role": _detect_role(x["name"]),
                        }
                        for x in unique
                    ],
                }

    return best


# ---------------------------------------------------------------------------
# 9. 输出渲染 / Rendering
# ---------------------------------------------------------------------------

def render_rank(items: List[Dict[str, Any]], top: int = 10) -> str:
    """渲染低卡排行表 (Render the low-calorie ranking table)."""
    lines = [
        "| # | 餐品 | 热量(kcal) | 蛋白(g) | 钠(mg) | 价格(¥) | 中式属性 | 置信度 |",
        "|---:|---|---:|---:|---:|---:|---|---:|",
    ]
    for i, it in enumerate(items[:top], start=1):
        method = it.get("matchMethod", "exact")
        if method == "category":
            flag = "🟡品类推断"
        elif it["confidence"] < 0.6:
            flag = "⚠️估算"
        else:
            flag = "✅精确"
        if it.get("sodium") and it["sodium"] >= SODIUM_ADVISORY_MG:
            flag += " 🧂高钠"
        lines.append(
            f"| {i} | {it['name']} | {it['kcal'] if it['kcal'] is not None else '—'}"
            f" | {it['protein'] if it['protein'] is not None else '—'}"
            f" | {it['sodium'] if it['sodium'] is not None else '—'}"
            f" | {it['price'] if it['price'] is not None else '—'}"
            f" | {it['chineseLabel']} | {it['confidence']:.2f} {flag} |"
        )
    return "\n".join(lines)


def render_combo(combo: Dict[str, Any]) -> str:
    """渲染套餐摘要 (Render the combo summary)."""
    if not combo:
        return "_未能组装出符合条件的套餐_"
    lines = [
        f"**目标 {combo['targetKcal']} kcal → 实际 {combo['totalKcal']} kcal**",
        "",
        "| 餐品 | 热量 | 蛋白 | 钠 | 价格 |",
        "|---|---:|---:|---:|---:|",
    ]
    for it in combo["items"]:
        lines.append(
            f"| {it['name']} | {it['kcal']} kcal | {it['protein']} g | {it['sodium']} mg | ¥{it['price']} |"
        )
    lines += [
        "",
        f"合计热量 **{combo['totalKcal']} kcal** · 蛋白 **{combo['totalProtein']} g** "
        f"· 钠 **{combo['totalSodium']} mg** · 合计 **¥{combo['totalPrice']}**",
    ]
    # 钠超标提示 / sodium advisory (早餐单份建议 < 800mg)
    if combo["totalSodium"] >= SODIUM_ADVISORY_MG:
        lines.append(
            f"> 🧂 **高钠提醒**：本组合钠含量 {combo['totalSodium']} mg，"
            f"已达早餐建议上限（{SODIUM_ADVISORY_MG} mg）。可去掉钠最高的单品后重新计算。"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 10. CLI / Command line interface
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="麦当劳中式低卡早餐 · 营养计算与排序引擎",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="解析营养库为结构化 JSON")
    p_parse.add_argument("--nutrition-file", required=True, help="list-nutrition-foods 原始输出")
    p_parse.add_argument("--out", required=True, help="输出 JSON 路径")

    p_rank = sub.add_parser("rank", help="对门店菜单做低卡中式排序")
    p_rank.add_argument("--nutrition-file", required=True)
    p_rank.add_argument("--menu-file", required=True)
    p_rank.add_argument("--top", type=int, default=10)
    p_rank.add_argument("--budget", choices=list(PRESET_TARGETS), default="balanced")
    p_rank.add_argument("--out", help="可选: 输出 JSON")

    p_combo = sub.add_parser("combo", help="组装一档低卡中式套餐")
    p_combo.add_argument("--nutrition-file", required=True)
    p_combo.add_argument("--menu-file", required=True)
    p_combo.add_argument("--preset", choices=list(PRESET_TARGETS), default="balanced")
    p_combo.add_argument("--out", help="可选: 输出 JSON")

    args = parser.parse_args(argv)

    nutrition = load_nutrition(args.nutrition_file)
    index = build_index(nutrition)

    if args.command == "parse":
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(nutrition, fh, ensure_ascii=False, indent=2)
        print(f"✅ 已解析 {len(nutrition)} 条营养档案 → {args.out}")
        return 0

    meals = load_menu(args.menu_file)
    if not meals:
        print(
            "❌ 未从 --menu-file 解析出任何餐品。\n"
            "   请确认该文件是 mcd-mcp.query-meals 的完整原始返回（含 data.meals 字段）。\n"
            "   若门店当前无早餐在售，data.meals 可能为空对象——此时应提示用户更换时段或门店。",
            file=sys.stderr,
        )
        return 2

    candidates = build_candidates(meals, index)
    ranked = sorted(candidates, key=lambda x: x["score"], reverse=True)

    if args.command == "rank":
        matched = [c for c in ranked if c["kcal"] is not None]
        print(f"共 {len(meals)} 个餐品, {len(matched)} 个已关联营养数据\n")
        print(render_rank(ranked, args.top))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(ranked, fh, ensure_ascii=False, indent=2)
        return 0

    if args.command == "combo":
        combo = build_combo(candidates, args.preset)
        print(render_combo(combo))
        if args.out and combo:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(combo, fh, ensure_ascii=False, indent=2)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())