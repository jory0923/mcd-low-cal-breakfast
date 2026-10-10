---
name: mcd-low-cal-breakfast
description: >-
  使用 mcd-mcp 连接器为用户挑选离最近门店的中式低卡早餐，标注真实卡路里、钠与蛋白质，
  自动判断是否支持外送，并一键领取优惠券后自动应用到最划算的组合。
  This skill should be used when the user asks for a Chinese-style low-calorie
  McDonald's breakfast (低卡早餐 / 中式早餐 / 减脂早餐 / 轻食早餐), asks which
  breakfast item is the lowest in calories, asks whether the nearest McDonald's
  store supports delivery (麦乐送 / 外卖), or asks to automatically claim and
  apply McDonald's coupons (领券 / 用券 / 麦麦省).
---

# 麦当劳中式低卡早餐智能推荐

为用户找到**离他/她最近的麦当劳门店**，在真实菜单与真实营养数据中筛选**中式低卡早餐**，
标注**可核验的卡路里**，判断**是否支持外送**，并**自动领券、用券**。

## 核心原则 / Core principles

1. **热量必须查表，不许估算。**
   卡路里只能来自 `list-nutrition-foods` 的返回值，经 `scripts/nutrition.py` 计算得出。
   若营养库中确实查不到该餐品，必须显式标注「暂无热量数据」，**禁止凭印象编造数字**。

2. **先确认位置，再谈推荐。**
   门店与配送状态是全部推荐的前提。位置不明确时先询问城市/位置关键词，
   不要默认城市、不要用任意门店代替。

3. **下单前必须让用户确认，且无法代为付款。**
   本技能默认执行到「算价」为止。调用 `create-order` 前必须获得用户明确确认。
   **mcd-mcp 不提供任何支付类工具**，支付须由用户本人通过官方支付链接完成，
   绝不可暗示本技能能代付。

4. **如实呈现不确定性。**
   品类推断得到的热量标注 🟡，未匹配到热量标注 ⚠️，不得伪装成精确值。

5. **不编造标识类数据。**
   `orderId`、`beCode`、`takeWayCode`、`productCode` 全部来自接口返回，
   **绝不可自行生成或改写**。

---

## 执行流程 / Workflow

按顺序执行 Step 1 → Step 6。Step 1 与 Step 2 在信息足够时可并行。

### Step 1 — 定位门店（必做）

需要用户给出**城市 + 位置关键词**（如「上海 人民广场」）。若用户只说了「我附近」，
先反问城市与商圈/地标，不要猜测。

**外送能力探测**（并行执行，判断是否支持外卖）：

```
mcp__mcd-mcp__delivery-query-addresses    # {} 参数，取配送地址列表
```

- 返回**空列表** → 用户未配置地址。**如实告知无法判断外送**，
  可引导用户创建地址（`delivery-create-address`，需用户提供
  城市 / 联系人 / 性别 / 手机号 / 地址 / 门牌号），或建议到店自取。
- 返回非空 → 选取最合适的 `addressId`，调用
  `mcp__mcd-mcp__delivery-query-stores(beType=2, addressId=...)` 获取可配送门店。

**到店门店查询**：

```
mcp__mcd-mcp__query-nearby-stores(searchType=2, city=<城市>, keyword=<位置关键词>, beType=1)
```

- `beType=1` 到店自提：返回门店**无** `beCode`，后续调用**不传** `beCode`。
- 结果按 `distance` 升序，**默认选最近且 `businessStatus=true` 的门店**。
- 必须向用户展示：门店名称、地址、距离、`storeCode`、`beCode`（若有）。

若同时支持外送，将外送门店与到店门店一并列出，说明各自距离。

### Step 2 — 确认在售菜单（必做）

```
mcp__mcd-mcp__query-meals(storeCode=<storeCode>, orderType=1, beType=1)
```

- 到店自取：`orderType=1`，**不传** `beCode`。
- 外送：`orderType=2`，**必传** `beCode`（来自 Step 1）。

把**原始返回内容**保存为文件，供脚本计算（脚本可解析带说明文字的响应体）。

### Step 3 — 拉取营养数据（必做，仅需一次）

```
mcp__mcd-mcp__list-nutrition-foods    # {} 参数，返回全量 160 条营养档案
```

**全量营养数据是静态资产**。首次拉取后缓存为 `references/nutrition_cache.json`，
后续会话直接复用，避免重复拉取与重复消耗上下文。

仓库内已内置**全量缓存（158 条去重后）**，源自 `list-nutrition-foods` 真实返回
（接口报 160 条，其中 `纯牛奶（盒装）` 与 `小杯玉米杯` 各重复一次，
归一化去重后为 158 条）。**默认直接使用，无需重新调用。**

若接口返回了更新的数据，可覆盖缓存：

```bash
python scripts/nutrition.py parse \
  --nutrition-file references/nutrition_full_raw.txt \
  --out references/nutrition_cache.json
```

> 缓存已覆盖全部中式早餐候选，无需为常规早餐场景重新拉取。
> 仅当需要**全菜单热量排行**（含汉堡、甜品等非早餐项）时才需全量比对。

### Step 4 — 计算与排序（必做，执行脚本）

```bash
# 排序：低卡 + 中式属性
python scripts/nutrition.py rank \
  --nutrition-file <nutrition.txt|json> --menu-file <menu.json> --top 12

# 组装三档套餐之一（light / balanced / satisfied）
python scripts/nutrition.py combo \
  --nutrition-file <nutrition.txt|json> --menu-file <menu.json> --preset balanced
```

脚本负责：名称归一化 → 营养库关联（字面/包含/字符重合/品类兜底四层）→
中式属性分层 → 热量分档 → 钠警示 → 组合求解。**热量由脚本查表得出，不要自行心算。**

> ⚠️ **注意**：`0 kcal` 是合法值（如「纯悦」「无糖可口可乐」）。
> 它们是低卡早餐的**最优解**，绝不能当作「无数据」而过滤掉。

**组合评分是五维加权**（`build_combo` 内部），而非单纯贴近热量目标：

| 维度 | 权重上限 | 作用 |
|---|---:|---|
| 热量贴合度 `fit` | 40 | 接近档位目标值 |
| 角色完整度 `completeness` | 24 | 主食+小食+饮品齐全优于同角色堆叠 |
| 单品质量 `quality` | 20 | 单项得分的平均值 |
| 控钠加分 `sodiumBonus` | 16 | 组合总钠越低得分越高 |
| 单品数惩罚 | −2.5/项 | 避免用堆数量凑热量 |

因此**高钠的粥会输给低钠的牛奶**——这是评分设计的预期结果，非缺陷。
粥品钠含量 611 mg 偏高，控钠维度正是为拦截这种情况而设。

> ✅ 已确认的设计取舍：本 Skill **不提供**「必须含粥」等持久化偏好开关。
> 若用户在对话中主动要求含粥，按用户意愿执行即可，
> 但不要为此临时改动评分逻辑或跳过控钠评估。

脚本输出的置信度标记：

| 标记 | 含义 | 展示要求 |
|---|---|---|
| ✅精确 | 名称精确或强包含匹配（≥0.85） | 可直接给出热量 |
| 🟡品类推断 | 名称对不上，按品类中位数推断（约0.45） | 必须标注「品类推断值」，建议同时给出范围 |
| ⚠️估算 | 关联分数偏低（0.30~0.60） | 必须标注「估算，供参考」 |
| 无热量 | 完全未匹配 | **明确写「暂无热量数据」，不得编造** |

### Step 5 — 领券 + 用券（必做）

**5.1 一键领券**（用户要求自动领券时执行；此操作会真实领取，无需再次确认）：

```
mcp__mcd-mcp__auto-bind-coupons    # {} 参数，自动领取所有当前可用券
```

**5.2 查询本门店可用券**（比「我的卡包」更准确，`query-my-coupons` 不校验门店可用性）：

```
mcp__mcd-mcp__query-store-coupons(storeCode=<storeCode>, orderType=<1|2>, beType=<1|2>)
```

- 到店：`orderType=1, beType=1`，不传 `beCode`
- 外送：`orderType=2, beType=2`，必传 `beCode`

**5.3 选券原则**：

- 券的 `products[].productCode` 命中推荐组合中的餐品才可用。
- 多张券可用时，选**使 `calculate-price` 实付最低**的那张。
- 展示券名、券 ID、有效期、适用商品——这四项是接口强制字段，不可省略。

**5.4 随单购早餐卡**：
若组合中餐品的 `canWithOrder=true`，说明可搭配随单购早餐卡享折扣。
其价格信息在 `query-meals` 返回的 `discountType`（如 `随单购早餐卡优惠`）与
`withOrder.cardId/cardType` 中，**需在算价时一并传入** `withOrder`，
否则用户会以原价购买。忽略这一点等于让用户多花钱。

### Step 6 — 算价、下单与支付

**6.0 推导取餐方式参数（必做）**

先确认用户的取餐方式，再用脚本推导必传参数，**避免把 `beCode` 传到到店场景导致接口报错**：

```bash
# 查看四种取餐方式的参数要求
python scripts/order_flow.py params --channel pickup
python scripts/order_flow.py params --channel delivery --store-code <code> --be-code <be> --address-id <addr>
```

| 取餐方式 | channel | beType | orderType | beCode | addressId | takeWayCode |
|---|---:|---:|---:|---|---|---|
| 到店自取 | `pickup` | 1 | 1 | ❌ 不可传 | ❌ | ✅ 必传 |
| 得来速 | `drive-through` | 5 | 1 | ✅ 必传 | ❌ | ✅ 必传 |
| 麦乐送到家 | `delivery` | 2 | 2 | ✅ 必传 | ✅ 必传 | ❌ 不可传 |
| 企业团餐 | `group` | 6 | 2 | ✅ 必传 | ✅ 必传 | ❌ 不可传 |

**预约场景**：用户要「明天 7:30 取餐」等指定时间时，
算价与下单**均需**传 `reservationDate`，格式严格为 `yyyy-MM-dd HH:mm`。
可先用 `validate` 校验格式：

```bash
python scripts/order_flow.py validate --channel pickup --store-code <code> \
  --items '[{"productCode":"513606","quantity":1}]' \
  --take-way-code <tw> --reservation-date "2026-10-11 07:30"
```

**6.1 算价**（价格单位为**分**，展示时除以 100）：

```
mcp__mcd-mcp__calculate-price(
  storeCode=..., orderType=..., beType=...,
  beCode=...(仅得来速/外送/团餐),
  addressId=...(外送/团餐),
  takeWayCode=...(到店/得来速),
  reservationDate=...(预约场景),
  items=[{productCode, quantity, couponId, couponCode}],
  withOrder={cardId, cardType}   # 若组合支持随单购
)
```

**6.2 展示方案**：门店、距离、取餐方式、逐项热量与营养、合计热量、券抵扣、实付金额。

**6.3 下单需明确确认**：仅当用户明确说「下单 / 购买 / 就要这个」才调用 `create-order`。
到店场景需先从 `calculate-price` 返回的 `takeWayList[].code` 取 `takeWayCode`
（**不可自行编造**）。外送建议引导填写 `remark`（如「无接触配送」，≤50 字）。

**6.4 支付引导（重要）**

> ⚠️ **mcd-mcp 不提供任何支付类工具**（无 pay / payment / transaction）。
> 本 Skill **无法代为付款**，也**不得暗示**能代付。

`create-order` 返回后，订单处于**待支付**状态。正确做法：

```
1. 从 create-order 返回中取支付链接(payUrl / 支付二维码)
2. 生成支付引导：python scripts/order_flow.py payment-guide \
     --order-id <orderId> --amount-fen <金额> --channel <取餐方式>
3. 引导用户自行扫码或用麦当劳 APP 支付
4. 用户回复「支付完成」→ 调用 query-order(orderId) 查最新状态
5. 用户回复「支付失败」→ 协助查看，或用 cancel-order 取消
```

**必须如实告知用户**：支付需由其本人通过官方渠道完成。
订单号（`orderId`）来自接口返回，**绝不可编造或改写**。

**支付前的两道校验**（脚本已内置，务必遵守）：

| 校验 | 规则 | 未通过时 |
|---|---|---|
| **orderId 格式** | 仅字母/数字/下划线/连字符，≤64 位<br>（源自 MCP schema 的显式约束） | **拒绝生成支付引导**并报错 |
| **金额单位** | 必须是**整数分**；带小数点或负值视为单位传错 | 显示「以官方支付页面为准」，**不展示猜测金额** |

> ⚠️ **不要凭空建立「状态码 → 已支付」的映射**。
> `query-order` 返回的 `orderStatus` 其取值含义接口**未枚举**，
> 猜测语义一旦出错会导致**误报支付成功**。
> 正确做法：原样展示状态文本，并以用户明确表述为准，
> 同时调用 `query-order` 核对。含义不明时如实告知「以 APP 显示为准」。

**6.5 取消订单**（用户要求时）：

```
mcp__mcd-mcp__cancel-order(orderId=<orderId>, cancelReasonCode=<1|2|3|4|5|-1>)
```

原因码：1=改主意了 2=重复下单 3=点错了 4=地址/电话填错 5=送达时间选错 -1=其它

---

## 输出格式 / Output format

```
🥣 中式低卡早餐推荐 · <门店名>（距您 <distance> 米）

📍 到店自取 / 🛵 麦乐送外送（配送地址：<地址>）

方案 A｜轻食约 320 kcal
| 餐品 | 热量 | 蛋白 | 钠 | 价格 |
...
合计 321 kcal · 蛋白 5g · 钠 313mg · ¥33.0
🎟 已用券：<券名>（券ID xxx）抵扣 ¥x
💰 实付 ¥xx.x

其余备选：<列出 2-3 个单品及其热量>
```

必须包含：热量数值、置信度标记、外送支持情况、券使用情况、实付金额。

**下单后的支付引导输出**：

```
🧾 订单已创建：<orderId>
💰 待付金额：¥xx.x
🚶 取餐方式：<到店自取 / 麦乐送到家>

请扫码或点击链接支付：
<支付链接>

支付步骤：
   1. 订单已创建,尚未支付
   2. 请通过支付链接扫码,或打开麦当劳 APP 完成支付
   3. 支付完成后回复「支付完成」,我会查询最新订单状态
   4. 若支付遇到问题,回复「支付失败」,我会协助查看或引导取消订单

ℹ️ 本 Skill 不代为付款,也无法代付。支付需由您本人通过官方渠道完成。
```

最后一行**不可省略**——必须如实告知用户支付需其本人完成。

---

## 参考资料 / References

- `references/nutrition_cache.json` — 全量营养档案（158 条去重后）
- `references/verified-recipes.md` — 已人工核验的餐品热量速查表与常见组合
- `references/api-field-notes.md` — mcd-mcp 接口踩坑记录（14 类）

## 脚本 / Scripts

- `scripts/nutrition.py` — 营养解析、关联匹配、打分排序、套餐求解。纯标准库，无依赖。
  支持 `parse` / `rank` / `combo` 三个子命令。
- `scripts/order_flow.py` — 取餐方式参数推导、下单前校验、请求体组装、支付引导。
  支持 `params` / `validate` / `payment-guide` 三个子命令。
- `scripts/test_nutrition.py` / `scripts/test_order_flow.py` — 回归测试
  （42 + 40 = 82 个用例），改动脚本后务必运行。