# mcd-mcp 接口踩坑记录 / API Field Notes

> 本文档记录在真实调用 `mcd-mcp` 连接器过程中**实测发现的**字段规则与陷阱。
> 每一条均来自接口 schema 或真实响应，非推测。
> 共记录 **17 类陷阱**。
> 核验日期：2026-10-10

## 一、beType / orderType 组合矩阵（最易出错处）

这是本 MCP Server 最容易踩坑的部分。两个字段**必须成对使用**，传错组合会直接报错。

| 业务场景 | beType | orderType | beCode | 必传字段 |
|---|---:|---:|---|---|
| 到店自取 | 1 | 1 | **不传** | storeCode |
| 得来速（车道取餐） | 5 | 1 | **必传** | storeCode |
| 麦乐送（外送） | 2 | 2 | **必传** | storeCode, addressId |
| 企业团餐 | 6 | 2 | **必传** | storeCode, addressId, gmServiceCode |

**关键规则**：

- `beType=1`（到店自取）时传 `beCode` **会报错**。这是最常见的失败原因。
- `orderType=1`（到店）时 `create-order` 必须传 `takeWayCode`，
  取值来自 `calculate-price` 返回的 `data.takeWayList[].code`。
- `orderType=2`（外送）时不传 `takeWayCode`，但必须传 `addressId`。
- 企业团餐（beType=6）在 `calculate-price` 前**必须**先调
  `query-meal-assistance` 获取 `gmServiceCode`。

## 二、到店门店与外送门店是两套接口

| 场景 | 查询工具 | 关键参数 |
|---|---|---|
| 到店 | `query-nearby-stores` | searchType=2 时必填 city + keyword |
| 外送 | `delivery-query-stores` | 必填 addressId + beType |

**不能用到店接口判断外送能力**。`query-nearby-stores` 返回的门店
不含配送信息。

外送能力探测流程：
```
delivery-query-addresses  →  空列表 = 未配置地址，无法判断外送
                         →  非空 = 取 addressId 传给 delivery-query-stores
```

## 三、券的三种查询，语义完全不同

| 工具 | 数据源 | 是否校验门店可用性 |
|---|---|---|
| `available-coupons` | 麦麦省可领取的券 | ❌ 不校验 |
| `query-my-coupons` | 用户卡包已持有的券 | ❌ **明确不校验** |
| `query-store-coupons` | 指定门店 + 订单类型 | ✅ **唯一校验** |

> `query-my-coupons` 的接口描述明确写道：
> "该接口返回的是用户拥有的券，不进行门店、渠道、配送方式等下单规则校验，
> 因此不承诺可用于当前订单。"
>
> **结论：要判断"这张券能不能在这家店用"，只能用 `query-store-coupons`。**

## 四、领券与算价分离

- 领券用 `auto-bind-coupons`（一次领全部，参数为空对象）
- 用券需在 `calculate-price` 和 `create-order` 的 `items[]` 中
  逐项传 `couponId` + `couponCode`，**两次调用都要传**
- 券的适用范围在 `products[].productCode`，需与 `items[].productCode` 比对

## 五、随单购早餐卡（漏传会导致用户多花钱）

菜单中若餐品 `canWithOrder=true`，且 `discountType` 为
`随单购早餐卡优惠`，则必须搭配随单购卡才能享受折扣价。

**不传 `withOrder` 的后果**：用户按 `originalPrice`（原价）付费。

实测案例（storeCode `1450713`）：

| 餐品 | 原价 | 随单购价 | 折扣 |
|---|---:|---:|---:|
| 脆香油条组合 `9900012729` | ¥11 | ¥6.6 | 6 折 |
| 猪柳蛋麦满分套餐 `9900003458` | ¥26 | ¥15.6 | 6 折 |
| 火腿扒早安营养卷组合 `9900003428` | ¥18.5 | ¥11.1 | 6 折 |
| 厚松饼猪柳蛋堡三件套 `9900016524` | ¥29.9 | ¥17.94 | 6 折 |

`withOrder` 参数需传：
```json
{ "cardId": "CRD9311B796110E942234F726EF4B4FE994", "cardType": 2 }
```

## 六、响应体格式不统一

MCP Server 的返回并非纯 JSON，而是 **Markdown 说明 + JSON 混排**：

- `list-nutrition-foods` 返回的是**扁平文本**，非 JSON：
  ```
  [160]{productName,nutritionDescription,energyKj,energyKcal,...}:
    猪柳麦满分,null,1288,308,16,16,24,781,213
  ```
- `query-meals` / `query-nearby-stores` 等返回 JSON，但**前置字段说明**，
  且末尾追加展示指令。

因此**不可直接 `json.loads`**，需先提取 JSON 片段。
`scripts/nutrition.py` 的 `extract_json_blob()` 与 `parse_nutrition_text()`
已封装该逻辑。

## 七、价格单位为「分」

`calculate-price` 返回的价格字段单位是**分**，展示时必须除以 100。
例如返回 `3210` 应展示为 `¥32.10`。

## 八、营养库解析的两个陷阱

1. **字段数不齐**：部分行缺少 `calcium` 字段，例如
   `yeyeyeye奶冻款,null,932,223,1,4,43,37,64` 只有 6 个数值。
   → 必须从右向左扫描数值字段做容错，不能按固定位置索引。

2. **第二字段不总是 null**：可能是营养描述文本，例如
   `芝士双层安格斯厚牛堡,能量约鲈鱼1条,2914,696,...`
   → 不能假设 `tokens[1]` 一定是字符串 `null`。

## 九、门店距离字段 `distance`

`query-nearby-stores` 返回的 `distance` 单位为**米**（实测值 144 / 235 / 355 …）。
`longitude` / `latitude` 实测均为 `0.0`，**不可用于地理计算**。
按距离排序即可，不要引入地图 SDK。

## 十、0 kcal 是合法值，不可当作「解析失败」（重要陷阱）

营养库中存在**真实的 0 热量餐品**：

| 餐品 | energyKj | energyKcal |
|---|---:|---:|
| 纯悦（电解质水） | 0 | 0 |
| 无糖可口可乐（小/中/大杯） | 0 | 0 |

**错误做法**：用 `if energyKj <= 0: continue` 过滤无效行。
这会把上述餐品**一并删除**——而它们恰恰是**低卡早餐的最优解**，
属于最不该丢失的候选。

**正确做法**：仅以**字段数**判定行是否有效（要求至少 `energyKj` + `energyKcal` 两个数值），
不依据热量数值本身做有效性判断。

> 本项目在实测中已踩过此坑：早期版本使用 `energyKj <= 0` 作为有效性判据，
> 导致「纯悦」被静默丢弃（当时缓存仅解析出 19 条）；改为按字段数判定后，
> 零卡条目正确保留。

## 十一、菜单名 ≠ 实际品名（品类兜底的必要性）

同一餐品在两个接口中名称不同：

| 接口 | `code=513606` 的名称 |
|---|---|
| `query-meals`（菜单列表） | 粥王系列精选 |
| `query-meal-detail`（餐品详情） | 雪菜脆笋鸡肉粥 |

营养库中只有「雪菜脆笋鸡肉粥」「皮蛋鸡肉粥」两条，**无「粥王系列精选」**。
纯字面匹配会导致**最具中式的早餐品因查不到热量而被排除**，
推荐结果彻底偏离「中式早餐」需求。

**应对**：见 `SKILL.md` Step 4 的四层关联策略，
L4 品类兜底取同品类热量**中位数**（取中位而非均值，避免极端值拉偏），
并将置信度降级至 0.45，在 UI 上标注 🟡 品类推断。

## 十二、`™` 商标符号会破坏归一化（易被 NFKC 反噬）

菜单中真实存在含 `™` 的餐品名，如 `麦咖啡™奶铁`、`麦咖啡™美式`。

**陷阱**：`unicodedata.normalize("NFKC", "™")` 返回 **`"TM"`**，
即 NFKC 会把该符号**展开成两个字母**，此后无论怎样删除都去不掉：

```python
>>> unicodedata.normalize("NFKC", "麦咖啡™奶铁")
'麦咖啡TM奶铁'          # ← ™ 已变成 TM
```

若归一化顺序写成「先 NFKC、后删商标」，结果会变成 `麦咖啡tm奶铁`，
与营养库中的 `奶铁` 彻底失配。

**正确顺序**：**先剥离商标符号，再做 NFKC**。本项目已实现，
并额外用正则兜底清理展开后残留的 `tm` 后缀。

## 十三、接口返回的 160 条实为 158 条唯一记录

`list-nutrition-foods` 响应头写的是 `[160]`，但实际存在重复项：

| 重复项 | 出现次数 |
|---|---|
| `纯牛奶（盒装）` | 2 |
| `小杯玉米杯` | 2 |

归一化去重后为 **158 条**。这是**接口侧的数据特征**，
缓存条数少于 160 属正常，不应被误判为解析丢失。
## 十四、mcd-mcp **没有任何支付工具**（穷举验证）

对本 MCP Server 全部 40+ 个 Tool 做了穷举检索（关键词：支付、付款、pay、
payment、transaction），结论：

| 结论 | 说明 |
|---|---|
| **无支付类工具** | 不存在 pay / payment / charge / transaction 等接口 |
| **支付在系统外完成** | `create-order` 返回后，用户通过返回的支付链接自行支付 |
| **状态可查** | `query-order(orderId)` 可查最新订单与支付状态 |

**因此本 Skill 的定位是「支付引导 (payment hand-off)」，而非代扣款。**

⚠️ **红线**：任何输出都**不得暗示本 Skill 能代用户付款**。
正确表述：「支付需由您本人通过官方渠道完成」。

相关工具：`cancel-order(orderId, cancelReasonCode)` 可取消订单，
原因码 1=改主意了 2=重复下单 3=点错了 4=地址/电话填错 5=送达时间选错 -1=其它。

## 十五、`takeWayCode` 必须来自接口返回，不可编造

到店/得来速场景（`orderType=1`）下单时，`takeWayCode` 是**必传**字段，
其取值**只能**来自 `calculate-price` 返回的 `data.takeWayList[].code`。

```
calculate-price 返回 → data.takeWayList[]  → 选取 code → 传给 create-order
```

自行编造 `takeWayCode` 会导致下单失败。
`order_flow.py` 的 `extract_take_way_codes()` 封装了提取逻辑，
并同时提取 `selected`/`isDefault` 标记以默认选中项。

`orderType=2`（外送/团餐）则**不可传** `takeWayCode`。

## 十六、支付状态**不可由本地判定**（重要边界）

`query-order` / `order-list` 返回 `orderStatus` 字段，但：

- 接口 schema **未枚举** `orderStatus` 的取值
- 当前环境 `order-list` 返回 `{"data":{}}`（无历史订单），**无法取得真实样本**

因此**不可在本地建立「状态码 → 已支付」的映射表**——
那等于凭空猜测语义，一旦猜错会导致**误报支付成功**。

**正确做法**：
1. 原样展示接口返回的 `orderStatus` **文本**，不做本地翻译
2. 需要判断时，以用户明确表述为准（回复「支付完成」），
   并**同时**调用 `query-order` 核对
3. 若 `orderStatus` 含义不明，如实告知「接口未说明状态含义，
   请以麦当劳 APP 显示为准」，**不猜测**

> 本 Skill 的 `build_payment_guide(status=...)` 仅做**透传**，
> 不做任何状态语义推断，这是刻意为之。

## 十七、微信支付工具**不可用于麦当劳订单**（易误判干扰项）

工具检索「支付 / 付款 / pay」时，会命中：

```
mcp__weixinpay__weixinpay_retry_pay
```

**它不属于 mcd-mcp**，而是**微信支付官方插件**，用于重新发起
*微信支付侧*订单的支付。它要求的 `paymentCode` 来自微信支付的
`WeixinPay-Required` 响应头，与麦当劳订单**完全无关**。

⚠️ **误用后果**：会对一个**完全不同的支付系统**发起重试操作，
既无法推进麦当劳订单支付，又可能产生非预期的支付行为。

**辨别方法**：看工具的 `mcp__` 前缀——
`mcp__weixinpay__*` 属微信支付插件，`mcp__mcd-mcp__*` 才是本项目可用的接口。
**任何 `mcp__weixinpay__*` 工具都不得用于麦当劳订单场景。**
