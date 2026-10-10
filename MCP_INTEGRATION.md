# MCP 集成说明 / MCP Integration Guide

> 本文档说明 `mcd-low-cal-breakfast` Skill **实际使用的** MCP Server、Tool、
> 完整调用流程与业务价值。
> 所有 Tool 名称、参数、返回值均基于 **2026-10-10 对 `mcd-mcp` 生产环境的真实调用**，
> 非文档摘抄或推测。

---

## 一、使用的 MCP Server / MCP Server Used

### 1.1 Server 基本信息

| 项目 | 内容 |
|---|---|
| **Server 名称** | `mcd-mcp` |
| **工具前缀** | `mcp__mcd-mcp__` |
| **接入方式** | WorkBuddy 自定义连接器（用户在连接器管理界面自主授权） |
| **认证** | 平台托管凭据，**不落盘、不进仓库** |
| **业务域** | 门店定位 / 菜单 / 营养 / 优惠券 / 订单 / 积分 |

### 1.2 本 Skill 使用的 Tool 清单

本 Skill 实际使用 **14 个 Tool**（11 个核心 + 3 个交易/辅助）：

| # | Tool | 用途 | 必需性 |
|---|---|---|---|
| 1 | `now-time-info` | 获取服务器时间，判断是否早餐时段 | ✅ 核心 |
| 2 | `query-nearby-stores` | 按位置查看到店门店（含距离） | ✅ 核心 |
| 3 | `delivery-query-addresses` | 查询配送地址，探测外送可行性 | ✅ 核心 |
| 4 | `delivery-create-address` | 用户无地址时创建配送地址 | 🔸 外送时必需 |
| 5 | `delivery-query-stores` | 查某地址可配送的门店 | 🔸 外送时必需 |
| 6 | `query-meals` | 拉取门店在售菜单与价格 | ✅ 核心 |
| 7 | `list-nutrition-foods` | 拉取全量营养档案（160 条） | ✅ 核心 |
| 8 | `query-store-coupons` | 查询本门店可用券（**唯一校验门店可用性**） | ✅ 核心 |
| 9 | `auto-bind-coupons` | 一键领取当前可用券 | ✅ 核心 |
| 10 | `calculate-price` | 算价（含优惠），单位为分 | ✅ 核心 |
| 11 | `create-order` | 创建订单 | 🔸 需用户确认 |
| 12 | `query-order` | 查询订单与支付状态 | 🔸 下单后 |
| 13 | `cancel-order` | 取消订单 | 🔸 需用户要求 |
| 14 | `order-list` | 查询历史订单 | 🔸 可选 |

**未使用的 Tool**（本 Skill 场景不涉及）：
`query-meal-detail`（特调场景）、`query-promotions`（仅团餐）、
`query-meal-assistance`（仅团餐）、`mall-*`（积分商城）、`draw-lottery`（抽奖）、
`campaign-calendar`（活动日历）、`query-survey-coupon`（满意度答卷）、
`query-my-account`、`query-my-prizes`、`query-lottery-info`、
`party-order-create`、`mall-*` 系列。

### 1.3 ⚠️ 关于支付的重要事实

经穷举本 MCP Server 全部 40+ 个工具（检索关键词：支付、付款、pay、
payment、transaction），确认：

| 结论 | 说明 |
|---|---|
| **无任何支付类工具** | 不存在 pay / payment / charge / transaction 接口 |
| **支付在系统外完成** | `create-order` 返回支付链接，用户自行扫码或用 APP 支付 |
| **状态可查** | `query-order(orderId)` 可查订单与支付状态 |
| **可取消** | `cancel-order(orderId, cancelReasonCode)` |

因此本 Skill 的定位是 **「支付引导 (payment hand-off)」**，
负责把用户引导到支付环节并跟踪状态，**实际扣款始终由用户本人完成**。

⚠️ **红线**：任何输出都不得暗示本 Skill 能代用户付款。

---

## 二、完整调用流程 / Complete Call Flow

### 2.1 流程总览

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1  定位门店 + 外送探测                    【并行执行】  │
│   ├─ delivery-query-addresses  ──► 地址列表                  │
│   │     └─ (非空) delivery-query-stores(beType=2)            │
│   └─ query-nearby-stores(searchType=2) ──► 门店 + 距离      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2  query-meals ──► 门店真实菜单 + 价格                  │
│ Step 3  list-nutrition-foods ──► 全量营养档案（可缓存）       │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 4  scripts/nutrition.py                                 │
│   normalize → 4 层关联 → 中式分层 → 打分 → 套餐求解           │
│   ★ 热量在此处由数据表查出，非模型估算                        │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 5  领券用券                                              │
│   auto-bind-coupons ──► query-store-coupons ──► 选最优券      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 6  calculate-price ──► 展示方案                         │
│   └─ (需用户明确确认) create-order ──► query-order          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Step 1 — 定位门店与外送探测

#### 调用 1-A：`delivery-query-addresses`（外送能力探测）

```json
{ "params": {} }
```

**真实返回**：
```json
{ "success": true, "code": 200,
  "data": { "addresses": [] } }
```

**决策逻辑**：

| 返回情况 | 含义 | Skill 行为 |
|---|---|---|
| `addresses: []` | 用户未配置配送地址 | **如实告知无法判断外送**，请用户先添加地址 |
| `addresses: [...]` | 已配置地址 | 取 `addressId` 进入下一步 |

> ⚠️ 实测当前环境返回**空列表**。这是必须正面处理的分支，
> 绝不能默认「支持外送」或默默跳过。

**强制展示字段**（接口明确要求，不可省略）：
`addressId` / `contactName` / `fullAddress` / `phone`

#### 调用 1-B：`delivery-query-stores`（外送门店）

```json
{ "beType": 2, "addressId": "<addressId>" }
```

- `beType=2` 麦乐送，`beType=6` 企业团餐
- 返回该地址**可配送**的门店，含 `storeCode` + `beCode`

#### 调用 1-C：`query-nearby-stores`（到店门店）

```json
{ "searchType": 2, "city": "上海", "keyword": "人民广场", "beType": 1 }
```

**真实返回**（截取）：
```json
{ "data": [
  { "storeCode": "1450713",
    "storeName": "麦当劳上海黄浦华旭国际大厦餐厅",
    "address": "南京东路街道西藏中路336号",
    "distance": 144,
    "businessStatus": true,
    "businessStartTime": "00:00", "businessEndTime": "23:59",
    "reservation": true,
    "reservationTimeOptions": [
      { "date": "2026-10-10", "today": true,
        "reservationOptionText": "早餐(05:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)，宵夜(22:14至23:59)" } ] },
  { "storeCode": "1450441", "storeName": "麦当劳上海福州路餐厅",
    "distance": 235, "businessStatus": true, ... },
  { "storeCode": "1450056", "distance": 355, ... },
  { "storeCode": "1450195", "distance": 382, ... },
  { "storeCode": "1450124", "distance": 412, ... }
]}
```

**关键观察（实测得出）**：

1. **`distance` 单位为米**，直接升序排序即得「最近门店」——无需地图 SDK
2. **`longitude` / `latitude` 恒为 `0.0`**，**不可用于地理计算**
3. **`reservationOptionText` 内含早餐时段**，如「早餐(05:14至10:15)」，
   可直接解析用于判断**当前是否处于早餐供应时段**
4. `beType=1` 时返回**无 `beCode`**（与 `beType=5` 相反）

**排序与选择规则**：
过滤 `businessStatus=true` → 按 `distance` 升序 → 取最近门店 → 展示 `storeCode`。

---

### 2.3 Step 2 — 拉取门店菜单

#### 调用：`query-meals`

```json
{ "storeCode": "1450713", "orderType": 1, "beType": 1 }
```

**注意**：`beType=1` 时**不可传 `beCode`**，否则接口报错。

**真实返回**（结构化截取）：
```json
{ "data": {
  "categories": [
    { "name": "粥王系列", "meals": [{ "code": "513606", "tags": ["单品"] }] },
    { "name": "超值早餐", "meals": [ ... ] },
    { "name": "6折起\n早餐卡专区", "meals": [ ... ] },
    { "name": "早安营养卷", "meals": [ ... ] },
    { "name": "麦咖啡™", "meals": [ ... ] }
  ],
  "meals": {
    "513606": { "name": "粥王系列精选", "currentPrice": "7.5",
                "originalPrice": "7.5", "canWithOrder": false },
    "2631":   { "name": "脆香油条", "currentPrice": "8.5", "canWithOrder": false },
    "9900002729": { "name": "脆香油条组合", "currentPrice": "6.6",
                    "originalPrice": "11", "discountType": "随单购早餐卡优惠",
                    "canWithOrder": true,
                    "withOrder": { "cardId": "CRD9311B796110E942234F726EF4B4FE994",
                                   "cardType": 2 } },
    "3755":   { "name": "纯牛奶(盒装)", "currentPrice": "10.5" },
    "9900016524": { "name": "厚松饼猪柳蛋堡三件套", "currentPrice": "17.94",
                    "originalPrice": "29.9", "discountType": "随单购早餐卡优惠",
                    "canWithOrder": true, "withOrder": { ... } }
  }}}
```

**实测发现的三个陷阱**：

| 陷阱 | 现象 | 应对 |
|---|---|---|
| **分类名含换行符** | `"6折起\n早餐卡专区"` | 归一化时去除空白 |
| **菜单名 ≠ 实际品名** | `513606` 菜单名「粥王系列精选」，详情名「雪菜脆笋鸡肉粥」 | 品类级兜底关联 |
| **`discountType` 决定实付价** | 原价 ¥11，随单购价 ¥6.6 | 必须传 `withOrder` |

---

### 2.4 Step 3 — 拉取营养档案

#### 调用：`list-nutrition-foods`

```json
{ "params": {} }
```

**返回格式为扁平文本，非 JSON**：
```
[160]{productName,nutritionDescription,energyKj,energyKcal,
       protein,fat,carbohydrate,sodium,calcium}:
  雪菜脆笋鸡肉粥,null,504,120,4,2,22,552,14
  皮蛋鸡肉粥,null,558,133,6,3,20,611,20
  脆香油条,null,844,202,5,12,19,230,18
  优品豆浆小杯,null,633,151,6,3,25,32,15
```

**实测解析陷阱**：

1. **字段数不齐**：`yeyeyeye奶冻款,null,932,223,1,4,43,37,64` 仅 6 个数值（缺 calcium）
   → 脚本从右向左扫描数值字段容错

2. **第二字段不总是 `null`**：
   `芝士双层安格斯厚牛堡,能量约鲈鱼1条,2914,696,...` 第二字段是描述文本
   → 不能假设固定位置

3. **存在脏数据条目**：如 `yeyeyeye奶冻款`、`"苹板"支撑Pro` 等异常名称
   → 解析时跳过异常但不中断整体流程

**缓存策略**：该数据为静态资产。仓库内已内置**全量缓存**，
源自真实返回（160 条中 `纯牛奶（盒装）`、`小杯玉米杯` 各重复一次，
归一化去重后 **158 条**），存于 `references/nutrition_cache.json`，
原始文本存于 `references/nutrition_full_raw.txt`，**默认直接复用**。

实测效果：使用全量缓存后，真实菜单匹配率由 **13/15 提升至 15/15**。

---

### 2.5 Step 4 — 本地计算（Skill 的核心价值）

调用顺序：`delivery-query-addresses` → `query-nearby-stores` → `query-meals`
→ `list-nutrition-foods` → `scripts/nutrition.py`

**四层关联策略**（全部为本地确定性计算）：

| 层级 | 方法 | 置信度 | 示例 |
|---|---|---|---|
| L1 | 归一化后精确相等 | 1.00 | `脆香油条` → `脆香油条` |
| L2 | 字面包含（长名优先） | 0.88–0.92 | `纯牛奶(盒装)` → `纯牛奶（盒装）` |
| L3 | 字符 n-gram 重合 | ≤ 0.55 | `玉米杯` → `大杯玉米杯` |
| L4 | **品类中位数兜底** | 0.45 | `粥王系列精选` → `皮蛋鸡肉粥` |
| — | 规格互斥惩罚 | −0.30 | 阻止「小杯」被「大杯」顶替 |

**L4 的必要性（真实案例）**：

| 菜单名 | 营养库候选 | 字面匹配 | 结果 |
|---|---|---|---|
| 粥王系列精选 | 雪菜脆笋鸡肉粥 / 皮蛋鸡肉粥 | ❌ 失败 | 取品类中位 → **133 kcal** 🟡 |

若无 L4，**最具中式的早餐品（粥）会因查不到热量而被排除**，
推荐结果将完全偏离「中式」诉求。这是本项目解决的核心问题。

---

### 2.6 Step 5 — 领券与用券

#### 调用 5-A：`auto-bind-coupons`（一键领券）

```json
{ "params": {} }
```
> 一次领取全部当前可用券，无需指定 couponId。

#### 调用 5-B：`query-store-coupons`（门店可用券）

```json
{ "storeCode": "1450713", "orderType": 1, "beType": 1 }
```

**真实返回**（券 ID / 券码已脱敏，其余为接口原样返回）：
```json
{ "data": [
  { "title": "新人专享-中杯美式7折",
    "couponId": "365A62…（已脱敏）",
    "couponCode": "MCD6872…（已脱敏）",
    "tradeDateTime": "2026-10-10 00:00:00-2026-11-08 23:59:59",
    "products": [{ "productCode": "9900000119", "productName": "美式中杯" }],
    "promotionId": "110961005" },
  { "title": "新人专享-中杯奶铁7折",
    "couponId": "54573C…（已脱敏）",
    "products": [{ "productCode": "9900000117", "productName": "奶铁中杯" }] }
]}
```

> 🔒 **脱敏说明**：券 ID 与券码属于**特定用户账户资产**，不属于公开商品数据。
> 本文档仅保留前缀用于说明字段结构，完整值**不写入仓库**，
> 与 `CONTEST_DECLARATION.md` 中「不含用户个人信息」的声明保持一致。
> 实际调用时这些值由接口实时返回，仅在内存中使用。

**为什么必须用这个接口**：

| 接口 | 校验门店可用性 |
|---|---|
| `available-coupons` | ❌ |
| `query-my-coupons` | ❌ 接口明示「不承诺可用于当前订单」 |
| **`query-store-coupons`** | ✅ **唯一校验** |

**选券规则**：`products[].productCode` 命中推荐组合 → 选 `calculate-price`
实付最低者。

**强制展示字段**：券名 / 券ID / 券码 / 有效期 / 适用商品 / 商品编码

---

### 2.7 Step 6 — 算价与下单

#### 调用：`calculate-price`

```json
{ "storeCode": "1450713", "orderType": 1, "beType": 1,
  "items": [{ "productCode": "513606", "quantity": 1 }],
  "withOrder": { "cardId": "CRD9311B796110E942234F726EF4B4FE994", "cardType": 2 } }
```

⚠️ **价格单位为「分」**，展示时除以 100。

**随单购漏传的后果（实测差价）**：

| 餐品 | 不传 withOrder | 传 withOrder | 差额 |
|---|---:|---:|---:|
| 脆香油条组合 `9900012729` | ¥11 | ¥6.6 | **¥4.4** |
| 猪柳蛋麦满分套餐 `9900003458` | ¥26 | ¥15.6 | **¥10.4** |
| 厚松饼猪柳蛋堡三件套 `9900016524` | ¥29.9 | ¥17.94 | **¥11.96** |

#### 调用：`create-order`（**需用户明确确认**）

- 到店（`orderType=1`）必传 `takeWayCode`，取自 `calculate-price` 的
  `data.takeWayList[].code`，**不可自行编造**
- 外送（`orderType=2`）必传 `addressId`，**不传** `takeWayCode`
- 预约场景需传 `reservationDate`（格式严格为 `yyyy-MM-dd HH:mm`）
- 外送建议传 `remark`（如「无接触配送」，≤50 字）

### 2.8 Step 7 — 支付引导与状态跟踪

#### 支付能力的边界

**mcd-mcp 无任何支付工具**（见 1.3 节穷举结论）。支付在系统外完成，
本 Skill 只负责引导与跟踪。

#### 流程编排

```
create-order 返回 orderId + 支付链接
        ↓
order_flow.py payment-guide --order-id <id> --amount-fen <分> --channel <渠道>
        ↓
输出支付引导（含「不可代付」声明）
        ↓
用户自行扫码 / 麦当劳 APP 支付
        ↓
用户回复「支付完成」→ query-order(orderId) 查状态
用户回复「支付失败」→ 协助查看，或 cancel-order 取消
```

#### 取消订单

```
mcp__mcd-mcp__cancel-order(orderId=<orderId>, cancelReasonCode=<1|2|3|4|5|-1>)
```

| 码 | 含义 |
|---:|---|
| 1 | 改主意了 |
| 2 | 重复下单 |
| 3 | 点错了/点多了/点少了 |
| 4 | 地址/电话填错了 |
| 5 | 送达时间选错了 |
| -1 | 其它原因 |

### 2.9 四种取餐方式的参数矩阵

这是最易出错处，已固化为 `order_flow.py` 的 `CHANNELS` 常量：

| 取餐方式 | beType | orderType | beCode | addressId | takeWayCode | 额外必填 |
|---|---:|---:|---|---|---|---|
| 到店自取 | 1 | 1 | ❌ **不可传** | ❌ | ✅ | — |
| 得来速 | 5 | 1 | ✅ | ❌ | ✅ | — |
| 麦乐送到家 | 2 | 2 | ✅ | ✅ | ❌ **不可传** | — |
| 企业团餐 | 6 | 2 | ✅ | ✅ | ❌ | `gmServiceCode` |

推导命令：
```bash
python scripts/order_flow.py params --channel <渠道> --store-code <code> \
  [--be-code <be>] [--address-id <id>] [--take-way-code <tw>]
```

**下单前校验**（拦截必然失败的调用）：
```bash
python scripts/order_flow.py validate --channel <渠道> --store-code <code> \
  --items '[{"productCode":"513606","quantity":1}]' [...]
```

实测拦截示例：
```
$ python scripts/order_flow.py validate --channel pickup --store-code 1450713 \
    --items '[{"productCode":"513606","quantity":1}]' --be-code BC123 --take-way-code TW1
❌ 参数校验未通过:
   - 到店自取不可传 beCode,否则接口报错
```

---

## 三、Tool 调用矩阵 / Call Matrix

| Tool | 触发条件 | 必传参数 | 易错点 |
|---|---|---|---|
| `now-time-info` | 判断早餐时段 | 无 | — |
| `delivery-query-addresses` | 外送探测 | 无 | 返回空列表需正面处理 |
| `delivery-create-address` | 用户无地址时创建 | `city`/`contactName`/`phone`/`address`/`addressDetail` | phone 须 11 位纯数字 |
| `delivery-query-stores` | 已配置地址 | `addressId`, `beType` | 与到店接口不同 |
| `query-nearby-stores` | 到店定位 | `searchType`; searchType=2 需 `city`+`keyword`+`beType` | `beType=1` 返回无 `beCode` |
| `query-meals` | 拉菜单 | `storeCode`, `orderType`, `beType` | **到店不可传 beCode** |
| `list-nutrition-foods` | 拉营养 | 无 | **返回文本非 JSON** |
| `query-store-coupons` | 查可用券 | `storeCode`, `orderType`, `beType` | 不可用 `query-my-coupons` 替代 |
| `auto-bind-coupons` | 领券 | 无 | — |
| `calculate-price` | 算价 | `storeCode`, `orderType`, `beType`, `items` | **单位为分**；漏传 `withOrder` |
| `create-order` | 下单 | `storeCode`, `orderType`, `beType`, `items` | **需用户确认**；到店必传 `takeWayCode` |
| `query-order` | 查状态 | `orderId` | 订单号不可编造 |
| `cancel-order` | 取消订单 | `orderId`, `cancelReasonCode` | 原因码需在 -1~5 |
| `order-list` | 查历史订单 | 无 | 无订单时 `data` 为空对象 |

---

## 四、实测验证记录 / Verified Test Log

| 验证项 | 方法 | 结果 |
|---|---|---|
| 时间获取 | `now-time-info` | ✅ `2026-10-10T09:25:38.975+08:00` |
| 门店定位 | `query-nearby-stores` | ✅ 5 家门店，最近 144 米 |
| 早餐时段解析 | `reservationOptionText` | ✅ 「早餐(05:14至10:15)」 |
| 外送地址探测 | `delivery-query-addresses` | ⚠️ 返回空列表（已按无地址处理） |
| 菜单拉取 | `query-meals` | ✅ 12 分类，含粥王/早安营养卷 |
| 餐品详情校验 | `query-meal-detail` | ✅ `513606` = 雪菜脆笋鸡肉粥 |
| 营养库拉取 | `list-nutrition-foods` | ✅ 160 条 |
| 营养解析 | `nutrition.py parse` | ✅ 容错解析成功 |
| 精确关联 | `nutrition.py rank` | ✅ 脆香油条 202 kcal |
| 品类兜底 | `nutrition.py rank` | ✅ 粥王系列精选 133 kcal 🟡 |
| 三档套餐 | `nutrition.py combo` | ✅ 321 / 477 / 678 kcal |
| 门店券查询 | `query-store-coupons` | ✅ 2 张可用券 |
| 可领券查询 | `available-coupons` | ✅ 7 张可领 |
| 历史订单查询 | `order-list` | ⚠️ `data` 为空（无历史订单） |
| **支付工具穷举** | 检索 pay/payment/transaction | ✅ **确认无支付工具** |
| **四渠道参数推导** | `order_flow.py params` × 4 | ✅ 均正确 |
| **到店 beCode 拦截** | `validate --channel pickup` | ✅ 正确拦截 |
| **预约时间校验** | 传非法格式 | ✅ 正确报错 |
| **订单流程测试** | `test_order_flow.py` | ✅ **40 / 40 通过** |
| 营养流程测试 | `test_nutrition.py` | ✅ **42 / 42 通过** |
| 金额单位换算 | `fen_to_yuan(2750)` | ✅ `27.5` 元 |
| 支付引导边界 | 渲染文案 | ✅ 含「不可代付」声明 |

---

## 五、业务价值 / Business Value

### 5.1 对用户

| 价值点 | 具体体现 |
|---|---|
| **决策效率** | 从「翻菜单 + 猜热量 + 研究券」压缩为**一句话** |
| **信息透明** | 热量、蛋白质、钠**全部来自官方数据**，可核验 |
| **真省钱** | 自动领券 + 选最优券 + 随单购不漏传，实测可省 ¥4–12 |
| **健康知情** | 低卡 ≠ 低钠，粥类 552 mg、卷类 1065 mg 均被标出 |
| **避免误导** | 推断值明确标注 🟡，查不到就说「暂无数据」 |

### 5.2 对 MCP 生态

| 价值点 | 说明 |
|---|---|
| **工具组合范式** | 演示了 14 个 Tool 如何编排成完整业务闭环 |
| **数据价值再发现** | `list-nutrition-foods` 是**已存在但常被忽略**的接口，本项目将其与菜单做确定性关联 |
| **避坑文档沉淀** | `api-field-notes.md` 记录了 17 类接口陷阱，可供其他开发者复用 |
| **AI 可信度示范** | 展示了「让 AI 基于数据计算」而非「让 AI 凭知识回答」的工程路径 |
| **能力边界诚实声明** | 穷举验证「无支付工具」后如实告知用户，不制造虚假能力预期 |

### 5.3 差异化

传统做法是让大模型直接回答「哪个最健康」——**答案不可核验**。
本项目的差异在于：

| 维度 | 直接问模型 | 本 Skill |
|---|---|---|
| 热量来源 | 模型记忆（可能过时） | **接口实时查表** |
| 数据时效 | 训练截止时间 | **每次运行都是最新** |
| 门店距离 | 编造或需另行查询 | **接口返回真实距离** |
| 外送能力 | 猜测 | **接口验证** |
| 券可用性 | 猜测 | **门店维度校验** |
| 不确定性 | 隐藏 | **显式标注置信度** |

> **本项目的核心主张**：
> 在涉及健康、金额、交易的场景，**AI 的价值应体现在「如何组织和计算数据」，
> 而非「代替数据本身」。**

---

## 六、异常处理 / Error Handling

| 异常场景 | 检测方式 | 处理策略 |
|---|---|---|
| 无配送地址 | `addresses: []` | 如实告知无法判断外送，建议到店 |
| 定位失败 | `query-nearby-stores` 抛错 | 请用户更换位置关键词 |
| 门店休息 | `businessStatus: false` | 过滤并选下一家 |
| 非早餐时段 | 解析 `reservationOptionText` | 提示当前非早餐时段 |
| 营养数据缺失 | 关联分数 < 0.30 | 显示「暂无热量数据」，**不编造** |
| 无可用券 | `query-store-coupons` 返回空 | 正常展示价格，不报「优惠」 |
| 券不适用 | `productCode` 未命中 | 跳过该券，继续算下一张 |
| 算价失败 | `calculate-price` 报错 | 回退原价展示并说明原因 |
| **下单参数缺失** | `order_flow.py validate` 拦截 | 下发前给出可执行的修复提示 |
| **预约时间格式错** | 本地格式校验 | 提示正确格式 `yyyy-MM-dd HH:mm` |
| **订单号缺失/编造** | 约束 `orderId` 只能来自接口 | 绝不自行生成，向用户索取真实订单号 |
| **支付未完成** | 用户未回复或 `query-order` 未支付 | 重申「支付需本人完成」，提供查询与取消入口 |

---

## 七、依赖声明 / Dependency Statement

- 本 Skill **仅依赖 `mcd-mcp` 连接器**，不依赖其他 MCP Server
- Python 脚本**仅使用标准库**，无需 `pip install`
- 所有凭据由 WorkBuddy 平台托管，**项目内无任何密钥**
- 与 mcd-mcp 服务方**无隶属或合作关系**，为第三方非官方工具

---

**再次声明**：本项目与麦当劳（中国）无任何隶属、授权或合作关系。
所有商标归其各自所有者所有。本文档所述接口调用均通过官方授权的 MCP 连接器完成。