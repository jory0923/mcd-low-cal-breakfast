# mcd-low-cal-breakfast · 麦当劳中式低卡早餐推荐 Skill

> 一个基于 **`mcd-mcp` 自定义连接器**的 WorkBuddy Skill：
> 自动定位离你最近的麦当劳门店 → 筛选**中式低卡早餐** → 标注**真实卡路里** →
> 判断**是否支持外送** → **自动领券并用券**。

---

## 📌 项目介绍 / Project Overview

### 一句话

**「早上八点，我想吃中式低卡早餐，还要用券。」** —— 一句话完成门店定位、热量计算、
外卖判断、领券用券全流程。

### 解决什么问题

早晨点麦当劳的三个现实痛点：

| 痛点 | 本 Skill 的解法 |
|---|---|
| 不知道哪家店最近，也不知道能不能送外卖 | 自动定位最近门店，并区分**到店自取 / 麦乐送外送**两条链路 |
| 菜单全是汉堡薯条，**看不到热量**，只能凭感觉点 | 调用营养库做**查表计算**，标注 kcal / 蛋白质 / 钠 |
| 想吃中式（粥、油条、豆浆），但不知道哪家有 | 中式属性分层识别，只推中式早餐 |
| 有券但总是忘记领、忘记用 | 一键领券 + 门店可用券校验 + 自动选最省方案 |

### 核心特性

- 🎯 **热量不估算，只查表**
  卡路里全部来自 `list-nutrition-foods` 真实返回值，经确定性脚本计算。
  **绝不让模型凭印象编造数字。** 无法匹配时明确标注「暂无热量数据」。

- 🧮 **四层关联引擎**
  名称归一化 → 字面匹配 → 包含匹配 → 字符 n-gram 重合 → 品类兜底推断。
  每个结果都带**置信度**并如实展示（✅精确 / 🟡品类推断 / ⚠️估算）。

- 📦 **三档套餐求解**
  轻食 320 kcal / 均衡 480 kcal / 饱腹 680 kcal，自动逼近热量目标。

- 🎟 **领券用券闭环**
  `auto-bind-coupons` 领券 → `query-store-coupons` **校验本门店可用性** →
  `calculate-price` 选最优券 → 算价验证实付。

- 💰 **随单购不漏传**
  识别 `canWithOrder` 标记，自动带上 `withOrder` 参数。
  **漏传会让用户按原价多付钱**（实测 6 折商品差价达 ¥12）。

- 🛒 **四种取餐方式全覆盖**
  到店自取 / 得来速车道取餐 / 麦乐送到家 / 企业团餐，
  参数由脚本统一推导，**从根本上杜绝「到店误传 beCode」这类接口报错**。

- 📅 **支持预约取餐**
  算价与下单均支持 `reservationDate`（`yyyy-MM-dd HH:mm`），格式强校验。

- 🧾 **下单与支付引导**
  算价 → 校验 → 下单 → **支付引导** → 查单 → 可取消，全链路闭环。

### ⚠️ 关于支付的重要说明

**本 Skill 无法代为付款，也不得暗示能代付。**

经穷举 `mcd-mcp` 全部 40+ 个工具确认，该 MCP Server **不提供任何支付类工具**
（无 `pay` / `payment` / `transaction` 等）。支付流程实际是：

```
create-order  → 返回支付链接  →  用户自行扫码/APP支付
                                   ↓
                            用户回复「支付完成」
                                   ↓
                            query-order 查状态
```

本 Skill 负责**把用户引导到支付环节并跟踪后续状态**，实际扣款始终由用户本人
通过麦当劳官方渠道完成。

### 与「直接问模型」的区别

如果直接问模型「麦当劳早餐哪个低卡」，模型大概率会**凭训练语料记忆**给出热量数字。
这些数字可能过时、可能张冠李戴、可能根本不存在。

本 Skill 的做法是**每次都从接口拉真实数据**，热量由脚本从数据表查出来，
匹配不确定时**明确说不知道**。区别不在于回答更快，而在于**数字可核验**。

---

## 🛠️ 安装方法 / Installation

### 前置条件

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| WorkBuddy | 最新版 | 运行环境 |
| `mcd-mcp` 连接器 | — | **必须**，本 Skill 的全部数据来源 |
| Python | 3.8+ | 仅用于跑营养脚本，**纯标准库，无需 pip 安装任何依赖** |

### 安装步骤

**第 1 步：确认 mcd-mcp 连接器可用**

在 WorkBuddy 的连接器管理界面确认 `mcd-mcp` 已连接并授权。
本 Skill 依赖以下 8 个工具，缺一不可：

```
query-nearby-stores · query-meals · list-nutrition-foods
delivery-query-addresses · delivery-query-stores
query-store-coupons · auto-bind-coupons · calculate-price
```

**第 2 步：安装 Skill**

将整个 `mcd-low-cal-breakfast/` 目录放入技能目录：

```bash
# 用户级（所有项目可用，推荐）
cp -r mcd-low-cal-breakfast ~/.workbuddy/skills/

# 或项目级（仅当前项目）
cp -r mcd-low-cal-breakfast <your-project>/.workbuddy/skills/
```

Windows 环境：

```powershell
Copy-Item -Recurse mcd-low-cal-breakfast "$env:USERPROFILE\.workbuddy\skills\"
```

**第 3 步：刷新技能列表**

重启 WorkBuddy，或在对话中直接描述需求触发。

### 目录结构

```
mcd-low-cal-breakfast/
├── SKILL.md                          # 主指令：六步执行流程
├── README.md                         # 本文件
├── CONTEST_DECLARATION.md            # 参赛声明（原创性/合规性/敏感信息）
├── MCP_INTEGRATION.md                # MCP Server、Tool、调用流程、业务价值
├── .gitignore                        # 运行时中间文件与凭据防护
├── scripts/
│   ├── nutrition.py                  # 营养解析 + 关联 + 打分 + 套餐求解
│   ├── order_flow.py                 # 取餐参数推导 + 下单校验 + 支付引导
│   ├── test_nutrition.py             # 回归测试（42 用例）
│   └── test_order_flow.py            # 回归测试（40 用例）
└── references/
    ├── nutrition_cache.json          # 全量营养档案（158 条去重后）
    ├── nutrition_full_raw.txt        # 接口原始返回（保留证据，便于复核）
    ├── verified-recipes.md           # 已核验热量速查表 + 实测组合
    └── api-field-notes.md            # mcd-mcp 接口踩坑记录（17 类）
```

### 验证安装

```bash
cd mcd-low-cal-breakfast
python scripts/nutrition.py --help          # 营养引擎 CLI
python scripts/order_flow.py --help         # 下单流程 CLI
python scripts/test_nutrition.py            # 应输出「全部通过: 42 / 42」
python scripts/test_order_flow.py           # 应输出「全部通过: 40 / 40」
```

---

## 💡 使用示例 / Usage Examples

### 示例 1：最简用法 —— 一句话出方案

**用户输入**：
> 上海人民广场附近，帮我来个低卡早餐

**Skill 执行**：定位最近门店 → 拉菜单 → 查热量 → 算套餐

**输出**：
```
🥣 中式低卡早餐推荐 · 麦当劳上海黄浦华旭国际大厦餐厅（距您 144 米）

📍 到店自取（该门店暂无法确认外送，需先配置配送地址）

方案 A｜均衡约 480 kcal
| 餐品 | 热量 | 蛋白 | 钠 | 价格 |
| 脆香油条 | 202 kcal | 5 g | 230 mg | ¥8.5 |
| 脆薯饼 | 146 kcal | 1 g | 311 mg | ¥8.5 |
| 纯牛奶(盒装) | 129 kcal | 7 g | 73 mg | ¥10.5 |
合计 477 kcal · 蛋白 13 g · 钠 614 mg · ¥27.5

备选轻食档 321 kcal · 钠仅 313 mg · ¥33.0
```

---

### 示例 2：外送场景 —— 判断能否送到

**用户输入**：
> 帮我送到家里，[已配置配送地址]

**输出**：
```
🛵 麦乐送外送 · 配送地址：上海市黄浦区南京东路…
门店：麦当劳上海福州路餐厅（可配送）
到店自取门店（更近）：麦当劳上海黄浦华旭国际大厦餐厅（144 米）

💡 建议：外卖需配送费且起送价较高，您离门店仅 144 米，建议到店自取更划算。
```

> Skill 会**同时列出两条链路**并给出取舍建议，而不是只丢一个答案。

---

### 示例 3：领券用券闭环

**用户输入**：
> 有券吗？帮我领了

**执行**：`auto-bind-coupons` → `query-store-coupons` → `calculate-price`

**输出**：
```
🎟 已领取 7 张券

本门店可用券：
| 券名 | 券ID | 有效期 | 适用商品 |
| 新人专享-中杯美式7折 | 365A62… | 10-10 至 11-08 | 美式中杯 |

💰 算价结果（单位已由分转元）
原价 ¥27.50 → 使用「新人专享-中杯美式7折」→ 实付 ¥24.80
✅ 已选该券（实付最低）
```

---

### 示例 4：严格低卡 + 控钠

**用户输入**：
> 我在健身，200 kcal 以内，钠越低越好

**输出**：
```
🥣 极低卡方案 · 雪菜脆笋鸡肉粥 120 kcal

| 餐品 | 热量 | 蛋白 | 钠 |
| 雪菜脆笋鸡肉粥 🟡品类推断 | 120 | 4 g | 552 mg |
| 苹果片 | 32 | 0 g | 0 mg |
合计 152 kcal · 蛋白 4 g · 钠 552 mg

⚠️ 提示：粥品钠含量偏高（552 mg）。
   该热量为「品类推断值」，粥品实际内容以门店出餐为准。
```

> Skill 会**主动揭示数据的不确定性**，而不是给一个看起来很确定的假数字。

---

### 示例 5：下单与支付引导

**用户输入**：
> 就这个吧，下单

**输出**：
```
🧾 订单已创建：1030108270000795607354880945
💰 待付金额：¥27.50
🚶 取餐方式：到店自取

请扫码或点击链接支付：
<支付链接>

支付步骤：
   1. 订单已创建,尚未支付
   2. 请通过支付链接扫码,或打开麦当劳 APP 完成支付
   3. 支付完成后回复「支付完成」,我会查询最新订单状态
   4. 若支付遇到问题,回复「支付失败」,我会协助查看或引导取消订单

ℹ️ 本 Skill 不代为付款,也无法代付。支付需由您本人通过官方渠道完成。
```

**两道前置校验**（防止用户付错单）：

| 情况 | 处理 |
|---|---|
| `orderId` 缺失或格式非法（含路径字符、超 64 位） | ❌ **拒绝生成引导**，报「订单号校验未通过」 |
| 金额带小数点（如 `"27.5"`，单位误传为元） | ⚠️ 显示「以官方支付页面为准」，**不展示猜测金额** |

> 这两道校验是实测中发现并补上的——原实现会把 `"27.5"` 静默换算成
> **¥0.28**（差 100 倍），且缺少订单号时会输出「订单已创建」误导用户。

---

### 示例 6：预约取餐 + 得来速

**用户输入**：
> 明天早上 7:30 我开车路过，来个得来店套餐

**输出**：
```
🚗 取餐方式：得来速（车道取餐）
📅 预约时间：2026-10-11 07:30（早餐时段 06:14–10:15 ✓）

| 餐品 | 热量 | 蛋白 | 钠 | 价格 |
| 脆香油条 | 202 kcal | 5 g | 230 mg | ¥8.5 |
| 脆薯饼 | 146 kcal | 1 g | 311 mg | ¥8.5 |
| 纯牛奶(盒装) | 129 kcal | 7 g | 73 mg | ¥10.5 |
合计 477 kcal · 蛋白 13 g · 钠 614 mg · ¥27.5
```

> 得来速（`beType=5`）必须传 `beCode`，该参数由脚本从门店查询结果自动带入。

---

### 示例 7：手动运行脚本

```bash
# 解析营养库 → 结构化 JSON
python scripts/nutrition.py parse \
  --nutrition-file raw_nutrition.txt \
  --out nutrition.json

# 低卡中式排行（Top 12）
python scripts/nutrition.py rank \
  --nutrition-file references/nutrition_cache.json \
  --menu-file menu.json \
  --top 12

# 组装指定档位套餐
python scripts/nutrition.py combo \
  --nutrition-file references/nutrition_cache.json \
  --menu-file menu.json \
  --preset balanced      # light | balanced | satisfied

# 查看四种取餐方式的必传参数
python scripts/order_flow.py params --channel pickup
python scripts/order_flow.py params --channel drive-through --store-code 1450713 --be-code <be>

# 下单前校验（拦截必然失败的调用）
python scripts/order_flow.py validate \
  --channel delivery --store-code 1450713 \
  --items '[{"productCode":"513606","quantity":1}]' \
  --be-code <be> --address-id <addr>

# 生成支付引导（金额单位为分）
python scripts/order_flow.py payment-guide \
  --order-id <orderId> --amount-fen 2750 --channel delivery
```

---

## 👥 目标用户 / Target Audience

### 主要用户

**1. 健身与体重管理人群（核心用户）**
- 典型场景：晨练后需补充蛋白与碳水，但要控制总热量
- 痛点：麦当劳菜单无热量标注，「全麦鸡腿堡到底多少卡」只能靠猜
- 本 Skill 提供：可核验热量 + 蛋白质密度排序 + 三档热量套餐

**2. 减脂期 / 低碳水人群**
- 典型场景：医生或营养师建议每日早餐 400 kcal 以内
- 痛点：不知道粥、豆浆、油条这类「看起来健康」的食物实际热量
- 本 Skill 提供：✅ 粥 120 kcal ✅ 油条 202 kcal ⚠️ 营养卷 444 kcal（高卡警示）

**3. 上班族（效率导向）**
- 典型场景：早上 8:30 出门，10:30 前要吃完
- 痛点：不想翻菜单、不想算热量、不想研究券怎么用
- 本 Skill 提供：一句话出方案，自动领券，输出即决策

**4. 需要严格控钠的中老年用户 / 家庭采购者**
- 典型场景：高血压需控钠，为家人代为选购
- 痛点：低卡不等于低钠，粥和卷的钠含量可能超标
- 本 Skill 提供：钠含量逐项展示 + ≥800 mg 自动高钠警示

### 次要用户

| 用户 | 使用价值 |
|---|---|
| **营养师 / 健身教练** | 导出真实热量数据作为配餐依据 |
| **麦当劳会员** | 券自动领取使用，避免漏用过期 |
| **Skill 开发者** | 完整的接口踩坑记录 + 可复用的关联引擎 |

### 不适合的用户

- ❌ 期望覆盖**非早餐时段**（本 Skill 聚焦早餐，午餐/下午茶需调整 preset）
- ❌ 需要**精确到克的营养处方**（接口数据为单份参考值，非实验室检测值）
- ❌ 期望**由 AI 代为付款**（本 Skill 无支付能力，支付须本人完成）
- ❌ 未连接 `mcd-mcp` 的环境（无法工作）

---

## 📄 相关文档

| 文档 | 内容 |
|---|---|
| [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md) | 实际使用的 MCP Server、Tool、完整调用流程、业务价值 |
| [`CONTEST_DECLARATION.md`](./CONTEST_DECLARATION.md) | 参赛声明：原创性、合规性、敏感信息 |
| [`SKILL.md`](./SKILL.md) | 技能主指令 |
| [`references/api-field-notes.md`](./references/api-field-notes.md) | 17 类接口踩坑记录 |

---

## ⚖️ 免责声明

- 营养数据来自 `mcd-mcp` 接口返回，为**单份参考值**，非实验室检测值。
- 菜品实际内容可能因门店、季节、供应而变化，**以门店实际出餐为准**。
- 本项目与麦当劳公司无任何隶属或授权关系。
- 热量计算结果为**参考建议**，不构成医疗或营养处方。特殊饮食需求请咨询专业人士。
- **本 Skill 不会在未经确认的情况下自动下单。**
- **本 Skill 无支付能力，不会也无法代为付款。**
  支付须由用户本人通过麦当劳官方渠道完成。
- `orderId` / `beCode` / `takeWayCode` 等标识均由接口返回，**不会被存储或外传**。

## 📄 License

MIT License — 详见文件末尾声明。