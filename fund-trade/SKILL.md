---
name: fund-trade
description: 基金智能交易 - LLM 决策引擎 + 量化信号 + 风控硬约束
user-invocable: true
---

# 基金智能交易 Skill

## 参数解析

```bash
/fund-trade <command> [options]
```

| 命令 | 说明 |
|------|------|
| `init` | 安装依赖 + 建表 + 生成默认 config.json |
| `run` | **核心命令**：采集数据+新闻 → Claude 分析决策 → 执行交易 |
| `run --dry` | 模拟运行，只看决策不执行交易 |
| `select [偏股\|偏债\|指数]` | AI 从排行榜选基金，推荐加入池 |
| `analyze <基金代码>` | 单基金深度分析 |
| `market` | 当前市场环境分析 |
| `review` | 持仓绩效审视 + 调仓建议 |
| `retrospect` | 决策复盘：回顾历史决策 + 提炼经验知识库 |
| `lessons` | 查看经验知识库 |
| `config` | 查看/修改基金池和风控参数 |
| `alipay [文件路径]` | **支付宝持仓管理**：解析 OCR 文本 → 分析 → 手动操作建议 |

---

## 前置条件

1. 同花顺 API 服务已启动：`cd .claude/skills/fund-trade && python server.py`（监听 8900 端口）
2. PostgreSQL 已运行（127.0.0.1:5432，dbname=jettask）

所有脚本路径基于：`.claude/skills/fund-trade/`

---

## 命令分发逻辑

### `/fund-trade init`

```bash
pip install psycopg2-binary requests
python .claude/skills/fund-trade/fund_db.py init
```
然后：
1. 确认 `.claude/skills/fund-trade/config.json` 存在（基金池为空，需用户添加或通过 select 推荐）
2. 配置交易密码（存 `.env` 文件，不进 git）：
   ```bash
   echo 'THS_TRADE_PASSWORD=你的同花顺交易密码' > .claude/skills/fund-trade/.env
   ```

---

### `/fund-trade run` （核心命令，7 步流程）

**这是交互式命令，Claude 自身作为决策引擎。** 按以下步骤执行：

#### Step 0: 连通性检查 + 持仓同步 + 待确认订单检查

**0a. 交易代理连通性**（非 `--dry` 模式必须检查）：

交易操作需要通过手机上同花顺 App 内的 Hook 代理（端口 18900）。执行前必须确保：

**重要：WSL2 环境可能设置了 http_proxy 代理，所有 curl 命令必须加 `--noproxy '*'` 或 `NO_PROXY='*'` 前缀，否则请求会被代理拦截返回 502。Python 的 server.py 已在启动时自动清理代理变量，不受影响。**

```bash
# 1. 检查 adb forward 是否已设置
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward --list

# 2. 如果没有 18900 映射，设置它
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:18900 tcp:18900

# 3. 同时确保 8900 端口的数据查询服务也映射了（如果 server.py 依赖手机端）
# /mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:8900 tcp:8900

# 4. 验证交易代理可达（⚠️ 必须 --noproxy '*'）
curl -s --noproxy '*' --connect-timeout 5 http://127.0.0.1:18900/

# 5. 验证 server.py 可达（⚠️ 必须 --noproxy '*'）
curl -s --noproxy '*' --connect-timeout 5 http://127.0.0.1:8900/api/trade/positions | head -c 100
```

如果交易代理不可达 → 提示用户检查：手机同花顺 App 是否打开、Hook 模块是否加载、adb 连接是否正常。
如果 curl 返回 502 → 检查是否被 http_proxy 代理拦截，确保加了 `--noproxy '*'`。

**0b. 持仓同步 + 前置检查**：

```bash
python .claude/skills/fund-trade/fund_api.py sync
python .claude/skills/fund-trade/risk_manager.py preflight
```

sync 从同花顺真实账户同步持仓数据到 ft_positions，确保：
- 已确认的持仓：同步市值、份额、成本、收益率
- 待确认的买入：记入成本（份额暂为 0），避免被误判为"空仓"
- 已全部赎回的基金：自动从 ft_positions 移除

检查项：
- 是否交易日（周一至周五）
- 是否触发熔断（组合总亏损超过阈值）

**注意**：即使过了交易截止时间（14:50），仍可执行交易。订单会在次日确认，如果市场风向变化可以撤销。

如果 `can_trade=false` 且原因是熔断 → 告知用户原因，停止执行（`--dry` 模式可跳过连通性检查）。

**0c. 待确认订单检查（重要！）**：

检查是否有昨日/更早下单但尚未确认的订单：

```bash
python .claude/skills/fund-trade/client.py orders
```

如果有"处理中 [可撤]"的订单（`endFlag == "0" and processStatus == "1"`）：

1. 获取该订单对应基金的**昨日决策理由**（从 `ft_pending_decisions` 或 `ft_decisions` 表）
2. 与**今日市场概览**（Step 1）对比，判断市场风向是否变化
3. 决策逻辑：
   - **风向未变**（利好/利空因素仍在）→ 不撤销，让订单自然确认
   - **风向逆转**（昨日利好变利空，或出现重大风险）→ 撤销订单：
     ```bash
     python .claude/skills/fund-trade/client.py cancel <订单号>
     ```
   - 撤销后记录原因到 `ft_pending_decisions`（更新 cancel_reason）

**示例决策**：
- 昨日买入理由："AI 政策利好"，今日 AI 板块大跌 -3%+ 或出现利空政策 → 撤销
- 昨日买入理由："黄金避险"，今日中东局势缓和、金价暴跌 → 撤销
- 昨日买入理由："超跌反弹"，今日继续下跌但无新利空 → 不撤销（坚持左侧试仓逻辑）

#### Step 0.5: 自动复盘昨日决策

```bash
python .claude/skills/fund-trade/fund_db.py create-reviews
python .claude/skills/fund-trade/fund_db.py pending-reviews
```

如果有待复盘的决策：
1. 采集相关基金最新净值（从 fund_api scan 缓存获取）
2. 获取已有经验知识库：`python .claude/skills/fund-trade/fund_db.py lessons`
3. 读取 `.claude/skills/fund-trade/prompts/review_decision.md` 模板，填入 pending_reviews + nav_data + existing_lessons
4. Claude 对比决策日净值 vs T+1/T+2 净值，判定 outcome
5. 将复盘结果写入 ft_reviews（通过 `fund_db.update_review()`）
6. 如果提炼出新经验，写入 ft_lessons（通过 `fund_db.save_lesson()`）
7. 如果验证了已有经验，更新可信度（通过 `fund_db.update_lesson_confidence(lesson_id, success)`）
   - 验证 5+ 次且成功率 < 30% 的经验会被**自动废弃**
8. 如果需要修正已有经验，通过 `fund_db.revise_lesson(old_id, new_text, ...)` 创建新版本
   - 旧经验标记为 `revised`，新经验继承分类和行业信息，confidence 重置为 low
9. 向用户简要汇报复盘结果（正确/错误/平局数量 + 经验更新情况）

如果没有待复盘的决策，跳过此步。

#### Step 1: 概览扫描 (Stage 1)

**重要**：所有脚本输出到临时文件，**不要用 `2>&1`**（会把 stderr 警告混入 JSON 导致解析失败），**不要 pipe 内联解析**（数据量大容易出错）。

```bash
# 可以并行执行这 3 个命令（互不依赖）
python .claude/skills/fund-trade/fund_api.py news-overview > /tmp/ft_overview.json
python .claude/skills/fund-trade/indicators.py evaluate > /tmp/ft_signals.json
python .claude/skills/fund-trade/risk_manager.py snapshot > /tmp/ft_snapshot.json
```

执行完后用 Read 工具读取这 3 个文件。Claude 读取概览后：
1. 从热门文章 + 重要快讯中提取 TOP 5 关键事件
2. 板块涨跌 vs 基金池行业交叉匹配
3. 输出"深入计划"（列出最多 6 个定向查询方向及理由）

#### Step 2: 定向深入 (Stage 2)

根据深入计划，动态调用 `news-drill`（每次 1 个方向）：

```bash
# 示例（Claude 根据 Step 1 动态决定具体调用哪些）
python .claude/skills/fund-trade/fund_api.py news-drill themes
python .claude/skills/fund-trade/fund_api.py news-drill article <seq>
python .claude/skills/fund-trade/fund_api.py news-drill flash 异动
python .claude/skills/fund-trade/fund_api.py news-drill fund-news <code>
python .claude/skills/fund-trade/fund_api.py news-drill headlines
python .claude/skills/fund-trade/fund_api.py news-drill hot-board
```

**可用模式**：
| 模式 | 用法 | 说明 |
|------|------|------|
| `themes` | `news-drill themes` | 新闻主题列表 |
| `theme` | `news-drill theme <id>` | 主题下的文章列表 |
| `article` | `news-drill article <seq>` | 文章全文 |
| `topic` | `news-drill topic <code>` | 话题详情 |
| `headlines` | `news-drill headlines` | 推荐头条 |
| `flash` | `news-drill flash <tag>` | 快讯 (a股/重要/公告/期货/异动/港股/美股) |
| `fund-news` | `news-drill fund-news <code>` | 基金相关新闻 |
| `changes` | `news-drill changes` | 大盘异动 |
| `hot-board` | `news-drill hot-board [sort]` | 热门板块 |
| `dragon-tiger` | `news-drill dragon-tiger` | 龙虎榜 |

**约束**：最多 6 次调用，每次后评估"信息是否足够"。
完成后撰写"结构化摘要"（500 字以内）：
- 今日核心事件及其对基金池各行业的影响方向
- 关键板块的资金流向和异动
- 需要特别关注的风险或机会

#### Step 2.5: 基金级消息深挖（强制！）

**A股是消息市场、政策市场，消息对决策至关重要。** 在做决策前，必须对基金池中的每只基金进行消息分析。

对基金池中的每只基金，执行以下命令（可并行）：

```bash
# 1. 基金相关新闻（必须）
python .claude/skills/fund-trade/client.py <code> news

# 2. 基金公告（检查是否有重大公告：经理变更、分红、限购等）
python .claude/skills/fund-trade/client.py <code> announcements --count 5

# 3. 重仓股信息（用于关联板块/个股消息）
python .claude/skills/fund-trade/client.py <code> holdings
```

**分析要点**：
1. **基金自身消息**：是否有基金经理变更、规模限购、分红公告等重大事件
2. **重仓股关联**：
   - 重仓股所属行业（如 AI、新能源、医药）今日是否有政策/消息面变化
   - 重仓股是否有个股公告（业绩预告、重大合同、股权变动）
   - 参考 Step 2 的板块消息，判断重仓股所在板块的资金流向
3. **消息→决策映射**：每条重要消息必须在后续决策中体现权重。如果某基金有利好消息却结论观望，必须解释为什么

**输出**：为每只基金撰写一段消息分析摘要（3-5 句），附加到 `{drill_findings}` 中：
```
## 个基消息分析
### 006888 华安科技动力
- 近期新闻：xxx
- 重仓股动态：前三大持仓为 xxx（AI板块），今日 AI 板块利好消息 xxx
- 消息面评估：偏多/中性/偏空

### 022365 永赢科技智选
...
```

#### Step 3: 量化信号 + 风控 + scan

读取 Step 1 已生成的 signals/snapshot，执行 scan-summary（精简版）：

```bash
# 使用 scan-summary（推荐，~2KB）而不是 scan（~150KB）
python .claude/skills/fund-trade/fund_api.py scan-summary > /tmp/ft_scan.json
```

scan-summary 输出 `{"funds": [...]}` 格式，每只基金包含：
- 基本信息：`code`, `name`, `type`, `nav`, `rate`（今日涨跌）, `risk`
- 排名：`rank_month`, `rank_year`, `yield_month`, `yield_year`
- 回撤：`drawdown_1y`, `drawdown_rank`
- 购买限制：`min_buy`, `max_buy`, `can_buy`
- 持仓：`stock_pct`, `top3_holdings`

**重要：检查 `buy_limits` 字段**，在决策前必须检查：
- `min_buy`: 最低起购额（如 002207 要求 1000 元起购）
- `max_buy`: 单日限购额（如 012922 QDII 限购 50 元/天）
- `can_buy`: 是否可购买（暂停申购的基金为 false）

决策时，买入金额必须在 `[min_buy, max_buy]` 范围内，否则会失败。如果限购金额过低，需要考虑分多日建仓。

#### Step 3.5: 按需补充（在 Step 4 决策过程中可用）

`drill-deep` 按需调用，最多 3 次：

```bash
python .claude/skills/fund-trade/fund_api.py drill-deep holdings <code>
python .claude/skills/fund-trade/fund_api.py drill-deep pe <code>
python .claude/skills/fund-trade/fund_api.py drill-deep yesterday-limit
python .claude/skills/fund-trade/fund_api.py drill-deep currency <tab>
python .claude/skills/fund-trade/fund_api.py drill-deep capital-flow [tab]
```

#### Step 4: Claude 综合决策（核心！）

读取 `.claude/skills/fund-trade/prompts/daily_decision.md` 模板，将以下数据填入：
- `{overview_data}` ← `/tmp/ft_overview.json`（Stage 1 概览原始数据）
- `{drill_findings}` ← Claude 在 Step 2 后自行撰写的结构化摘要（自然语言分析，不是 JSON dump）
- `{fund_news_analysis}` ← Claude 在 Step 2.5 撰写的个基消息分析（每只基金的消息面评估）
- `{fund_signals}` ← `/tmp/ft_signals.json`
- `{risk_snapshot}` ← `/tmp/ft_snapshot.json`
- `{allocation}` ← config.json 的 allocation 字段（核心/卫星/对冲目标比例）
- `{strategy}` ← config.json 的 strategy 字段（建仓/加仓/止盈策略参数）
- `{user_profile}` ← config.json 的 user_profile 字段（用户偏好）
- `{cash_management}` ← config.json 的 cash_management 字段
- `{today_operations}` ← 执行 `python -c "import fund_db, json; print(json.dumps(fund_db.get_today_decisions(), ensure_ascii=False, default=str))"` 获取今日已执行的操作
- `{recent_decisions}` ← 执行 `python -c "import fund_db, json; print(json.dumps(fund_db.get_recent_decisions(5, exclude_today=True), ensure_ascii=False, default=str))"` 获取（不含今日）
- `{lessons}` ← 执行 `python .claude/skills/fund-trade/fund_db.py lessons` 获取经验知识库
- watch_streaks（填入决策 JSON 的 `watch_streak` 字段）← 执行 `python -c "import fund_db, json; print(json.dumps(fund_db.get_watch_streaks(), ensure_ascii=False))"` 获取各基金连续观望天数

scan-summary 数据可直接读取（~2KB），包含每只基金的关键决策信息（净值、涨跌、排名、回撤、购买限制）。

**Claude 阅读全部信息后，按照 prompt 模板做出决策**，输出 JSON 格式（注意新增字段 `market_phase`, `risk_bias`, `watch_streak`）：
```json
{
  "date": "YYYY-MM-DD",
  "market_phase": "趋势上行|震荡企稳|趋势下行|恐慌出清",
  "risk_bias": "偏进攻(怕踏空)|均衡|偏防守(怕亏损)",
  "decisions": [
    {"fund_code": "006888", "fund_name": "...", "action": "buy|sell|hold|watch", "amount": 500, "sell_pct": null, "phase": "左侧试仓|...", "reason": "...", "confidence": "high|medium|low", "watch_streak": 0, "referenced_lesson_ids": []}
  ],
  "market_view": "...",
  "risk_notes": "..."
}
```

#### Step 5: 风控校验

将 Step 4 的决策 JSON 传给风控：
```bash
python .claude/skills/fund-trade/risk_manager.py check '<决策JSON>'
```

如果有被拦截的决策 → Claude 查看原因，决定是否调整决策。

#### Step 6: 保存决策 + 执行交易

**6a. 保存所有决策到 ft_decisions**（无论是否执行交易，所有决策都必须记录，供复盘系统使用）：

对 Step 4 决策 JSON 中的每一条 decision，执行：
```python
import fund_db

# buy 决策示例（需传 amount）
fund_db.save_decision(
    fund_code="022365",
    fund_name="永赢科技智选混合发起C",
    action="buy",
    reason="左侧试仓：企稳信号+AI双重催化",
    confidence="high",
    market_view="震荡企稳，科技有望领涨",
    amount=625,          # buy 时必填
    risk_notes="地缘风险仍存，保留70%现金",
    referenced_lesson_ids=[3, 7]
)

# hold/watch 决策示例（amount 和 sell_pct 可省略）
fund_db.save_decision(
    fund_code="002207",
    fund_name="大成景阳领先混合C",
    action="hold",
    reason="逻辑仍在，继续持有",
    confidence="medium",
    market_view="震荡企稳"
)
```

**重要**：hold 和 watch 决策也必须保存！复盘系统需要回顾所有决策（包括"不动"的决策）。amount/sell_pct 对于非交易决策可省略。

**6b. 执行交易**：

如果是 `--dry` 模式，跳过交易执行，只展示决策结果。

**交易截止时间处理**：
- **14:50 前**：订单当日确认（T+1 到账），直接执行
- **14:50 后**：订单次日确认（T+2 到账），**需要用户确认后才执行**！
  - 次日 15:00 前可撤销，利用 Step 0c 的待确认订单检查机制
  - 如果市场风向变化，次日运行时会自动撤销

**⚠️ 14:50 后执行交易前必须确认**：

如果当前时间 > 14:50 且有 buy/sell 决策，使用 `AskUserQuestion` 工具询问用户：

```
问题：已过交易截止时间 (14:50)，以下订单将在明日确认，是否继续执行？

选项：
1. 确认执行（订单明日确认，如市场变化可撤销）
2. 暂不执行（仅保存决策，不下单）

待执行交易：
- 015945 易方达军工C：买入 500 元
- ...
```

- 用户选择"确认执行" → 继续执行交易
- 用户选择"暂不执行" → 跳过交易执行，只保存决策到 ft_decisions

**6b-1. 保存买入决策到 ft_pending_decisions**（用于次日判断是否撤销）：

对于 buy/sell 决策，在执行交易前保存决策信息：
```python
import fund_db
fund_db.save_pending_decision(
    fund_code="015945",
    fund_name="易方达军工C",
    action="buy",
    reason="左侧试仓：地缘紧张+行业分散",
    confidence="medium",
    market_view="趋势上行第2日，科技/军工轮动",
    market_phase="趋势上行",
    amount=500,
    risk_notes="中东局势不确定"
)
```

**6b-2. 最低起购额检查**：在执行 buy 前，先通过 proxy 查询基金的 minBuy：
```bash
# 查询基金最低起购额（通过 server.py 的 buy_fund Step 1 init 接口）
curl -s --noproxy '*' -X POST http://127.0.0.1:18900/proxy \
  -H "Content-Type: application/json" \
  -d '{"url":"https://trade.5ifund.com/rz/trade/dubbo/subscribe/init","method":"POST","body":"fundCode=<code>","content_type":"application/x-www-form-urlencoded"}'
# 检查返回的 data.minBuy 字段，如果决策金额 < minBuy，需要调整金额到 minBuy
```
如果决策金额 < minBuy，将金额上调到 minBuy（并在日志中记录调整原因）。

交易执行前需加载密码环境变量（密码存在 `.claude/skills/fund-trade/.env`，不进 git）：
```bash
# 加载环境变量后执行（必须使用绝对路径）
SKILL_DIR="$(cd "$(dirname "$0")/../.claude/skills/fund-trade" 2>/dev/null || echo ".claude/skills/fund-trade")"
export $(cat "$SKILL_DIR/.env" | xargs) 2>/dev/null

# 买入（注意：会自动读取 THS_TRADE_PASSWORD 环境变量或 config.json 中的 trade_password）
python .claude/skills/fund-trade/trader.py buy <code> <amount> --reason "..."

# 卖出
python .claude/skills/fund-trade/trader.py sell <code> <pct> --reason "..."
```

**注意**：密码也可以直接配置在 `config.json` 的 `trade_password` 字段，这样无需每次 export 环境变量。trader.py 会优先读取环境变量，其次读取 config.json。

**6b-3. 交易执行后更新 pending 状态**：

交易成功后（获得订单号），更新 ft_pending_decisions：
```python
# 如果订单已确认（当日 14:50 前下单），标记为 executed
fund_db.execute_pending_decision(pending_id)

# 如果订单待确认（14:50 后下单），保持 pending 状态，等次日 Step 0c 检查
# 不做任何操作
```

#### Step 7: 记录 + 汇总

```bash
echo '{"decisions_count": N, "trades_count": M, "summary": "..."}' | python .claude/skills/fund-trade/fund_db.py log-run
```

**向用户输出今日操作汇总**：市场观点、每只基金的决策和理由、实际执行的交易、风控拦截的交易。

---

### `/fund-trade select [类型]`

**重要：本系统必须选 C 类基金（免申购费），绝不选 A 类。选基前必须分析现有持仓和用户偏好。**

#### Phase 0: 了解用户偏好（首次或 user_profile 为空时）

如果 `config.json` 的 `user_profile.risk_tolerance` 为 null，**必须先跟用户沟通**，了解：
1. **风险承受能力**：高/中/低？能接受多大的浮亏？（如 -20%/-10%/-5%）
2. **偏好行业**：科技/消费/医药/新能源/金融/周期？有没有特别看好或回避的？
3. **投资周期**：短线波段（1-4周）/ 中线趋势（1-3月）/ 混合？
4. **资产配置意向**：
   - 核心仓位比例（稳健底仓：宽基+优质主动）想占多少？默认 70%
   - 卫星仓位比例（进攻型：行业/主题）想占多少？默认 20%
   - 对冲仓位比例（防守型：黄金/固收+/QDII）想占多少？默认 10%
5. **总资金量**：确认 total_capital 是否准确

使用 `AskUserQuestion` 工具询问，然后将结果保存到 `config.json` 的 `user_profile` 和 `allocation`。

#### Phase 1: 持仓同步 + 诊断

```bash
python .claude/skills/fund-trade/fund_api.py sync
python .claude/skills/fund-trade/risk_manager.py snapshot
```

分析当前持仓：
- 各基金的行业分布（从缓存的 holdings 数据获取）
- 核心/卫星/对冲的实际仓位 vs 目标比例
- 行业集中度、风格集中度、基金经理重叠

#### Phase 2: 数据采集

```bash
python .claude/skills/fund-trade/client.py ranking --sort-type riseYear
python .claude/skills/fund-trade/fund_api.py market
```

#### Phase 3: 智能筛选

读取 `.claude/skills/fund-trade/prompts/select_fund.md` 模板，填入：
- `{user_profile}` ← config.json 的 user_profile
- `{allocation}` ← config.json 的 allocation（目标配置比例）
- `{positions}` ← Phase 1 的持仓诊断结果
- `{ranking_data}` ← Phase 2 的排行数据
- `{market_data}` ← Phase 2 的市场数据

Claude 按照**配置缺位优先级**筛选：
- 先诊断组合缺什么（核心不足？卫星不足？对冲为零？）
- 按缺位补齐，**只考虑 C 类基金**
- 确保与现有持仓行业互补
- 如果排行榜中某只基金是 A 类，搜索其 C 类份额

#### Phase 4: 确认加入

向用户展示：
- 持仓诊断结果（当前配置 vs 目标配置）
- 推荐的 3-5 只基金及理由
- 每只基金建议的仓位占比

询问用户确认后，更新 `config.json` 的 `fund_pool`。

---

### `/fund-trade analyze <基金代码>`

1. 采集该基金全维度数据：
   ```bash
   python .claude/skills/fund-trade/fund_api.py scan   # 确保缓存
   python .claude/skills/fund-trade/fund_api.py news
   python .claude/skills/fund-trade/fund_api.py market
   ```
2. 获取量化信号：
   ```bash
   python .claude/skills/fund-trade/indicators.py evaluate
   ```
3. 读取 `.claude/skills/fund-trade/prompts/analyze_fund.md` 模板
4. Claude 输出该基金的全面分析报告

---

### `/fund-trade market`

1. 采集市场概览（一个命令，数据已精简至 ~15KB，直接读取即可）：
   ```bash
   python .claude/skills/fund-trade/fund_api.py news-overview > /tmp/ft_overview.json
   ```
2. 读取 `/tmp/ft_overview.json`，直接输出分析（**不需要额外 drill-down**，概览数据已足够）：
   - 大盘走势判断（强/弱/震荡）
   - 资金流向分析
   - 板块涨跌 TOP 10
   - 重要快讯解读
   - 热门文章关键事件
   - 对基金投资的影响

---

### `/fund-trade review`

1. 获取持仓和信号：
   ```bash
   python .claude/skills/fund-trade/risk_manager.py snapshot
   python .claude/skills/fund-trade/indicators.py evaluate
   python .claude/skills/fund-trade/fund_api.py market
   ```
2. 获取历史交易和决策记录（通过 fund_db）
3. 读取 `.claude/skills/fund-trade/prompts/portfolio_review.md` 模板
4. Claude 输出持仓审视报告 + 调仓建议

---

### `/fund-trade retrospect`

手动触发决策复盘（正常情况下 `run` 命令会自动执行 Step 0.5 复盘）。

1. 创建待复盘记录：
   ```bash
   python .claude/skills/fund-trade/fund_db.py create-reviews
   ```
2. 获取待复盘决策：
   ```bash
   python .claude/skills/fund-trade/fund_db.py pending-reviews
   ```
3. 采集相关基金最新净值数据（确保 scan 缓存可用）：
   ```bash
   python .claude/skills/fund-trade/fund_api.py scan
   ```
4. 读取 `.claude/skills/fund-trade/prompts/review_decision.md` 模板，填入：
   - `{pending_reviews}` ← 步骤 2 的输出
   - `{nav_data}` ← 步骤 3 中各基金的最新净值
   - `{decision_context}` ← 决策日的市场数据（如有缓存）
5. Claude 分析每条决策的 T+1/T+2 净值变化，判定 outcome
6. 将复盘结果通过 Python 代码写入数据库：
   ```python
   import fund_db
   fund_db.update_review(review_id, nav_at_decision=..., nav_t1=..., nav_t2=...,
                         change_t1_pct=..., change_t2_pct=..., outcome="correct",
                         review_notes="...")
   fund_db.save_lesson(category="policy", trigger_pattern="...",
                       expected_outcome="...", actual_outcome="...",
                       lesson_text="...", related_sectors=["科技"],
                       source_review_ids=[review_id])
   fund_db.mark_lesson_extracted(review_id)
   ```
7. 获取复盘统计并向用户汇报：
   ```bash
   python .claude/skills/fund-trade/fund_db.py review-stats
   ```

---

### `/fund-trade lessons`

查看经验知识库。

```bash
python .claude/skills/fund-trade/fund_db.py lessons
```

Claude 阅读后按类别汇总展示：
- 政策类经验（policy）
- 技术指标类经验（technical）
- 市场情绪类经验（sentiment）
- 行业轮动类经验（sector）
- 择时类经验（timing）
- 风控类经验（risk）

每条经验展示：触发模式、教训、可信度（low/medium/high）、验证次数/成功次数。

---

### `/fund-trade config`

读取并展示 `.claude/skills/fund-trade/config.json`：
- 基金池列表
- 风控参数
- 服务器地址
- 总资产设置

用户可要求修改参数，Claude 直接编辑 config.json。

---

### `/fund-trade alipay [文件路径]`

**支付宝基金持仓管理** - 解析 OCR 文本，分析持仓，给出手动操作建议。

这些基金在支付宝中持有，**无法通过程序自动交易**，所有操作需用户手动执行。

默认读取文件：`data/支付宝持仓.txt`

#### Step 1: 读取并解析 OCR 文本

Claude 直接读取 OCR 文本文件（使用 Read 工具），用 LLM 自身能力解析出每只基金的：
- 基金名称
- 当前市值
- 持有收益（金额和百分比）
- 昨日收益
- 分类（进阶类/稳健类）

**重要**：OCR 文本格式不固定（每次截图和识别结果可能不同），不要用正则解析，由 Claude 直接理解文本内容提取数据。

解析完成后，将持仓数据整理为 JSON 格式的列表，每个条目包含：
```json
{
  "fund_name": "天弘标普500(QDII-FOF)C",
  "fund_code": "007722",
  "current_value": 62152.29,
  "total_cost": 55500.0,
  "total_profit": 6652.29,
  "profit_rate": 11.99,
  "daily_pnl": -9.13,
  "category": "进阶类"
}
```

#### Step 2: 解析基金代码

使用映射文件解析基金名称为代码：
```bash
python .claude/skills/fund-trade/fund_db.py alipay-resolve '<["基金名1", "基金名2", ...]>'
```

如果有未解析的基金名称，通过 web 搜索找到基金代码后添加映射：
```bash
python .claude/skills/fund-trade/fund_db.py alipay-map "基金名称" "基金代码"
```

#### Step 3: 保存持仓快照到数据库

将解析后的持仓数据保存到 `ft_alipay_positions` 表，用于长期追踪：

```bash
echo '<JSON数据>' | python .claude/skills/fund-trade/fund_db.py alipay-save
```

JSON 格式（可包含 `holdings` 数组和可选的 `snapshot_date`）：
```json
{
  "holdings": [
    {"fund_name": "...", "fund_code": "007722", "current_value": 62152.29, "total_cost": 55500, "total_profit": 6652.29, "profit_rate": 11.99, "daily_pnl": -9.13, "category": "进阶类"}
  ],
  "snapshot_date": "2026-03-04"
}
```

#### Step 4: 获取历史数据对比（可选）

如果数据库中有之前的快照，获取历史数据用于对比：
```bash
python .claude/skills/fund-trade/fund_db.py alipay-positions           # 最新持仓
python .claude/skills/fund-trade/fund_db.py alipay-positions 2026-03-01 # 指定日期
python .claude/skills/fund-trade/fund_db.py alipay-dates               # 所有快照日期
python .claude/skills/fund-trade/fund_db.py alipay-history "天弘标普500(QDII-FOF)C" 30  # 单基金30天历史
python .claude/skills/fund-trade/fund_db.py alipay-decisions           # 最近的操作建议记录
```

#### Step 5: 获取市场数据（需 server.py 运行）

通过 client.py 获取基金市场数据辅助分析（评分、估值、回撤、RSI 等）：
```bash
python .claude/skills/fund-trade/client.py <基金代码> detail     # 基金详情+评分
python .claude/skills/fund-trade/client.py <基金代码> drawdown   # 回撤数据
python .claude/skills/fund-trade/client.py <基金代码> rsi        # RSI指标
python .claude/skills/fund-trade/client.py <基金代码> valuation  # 指数估值百分位
python .claude/skills/fund-trade/client.py <基金代码> holdings   # 重仓持股
```

获取市场整体数据：
```bash
python .claude/skills/fund-trade/fund_api.py news-overview > /tmp/ft_overview.json  # 市场概览
python .claude/skills/fund-trade/client.py market_overview    # 大盘行情
python .claude/skills/fund-trade/client.py sector_rank        # 板块涨跌
python .claude/skills/fund-trade/client.py hot_board          # 热门板块
python .claude/skills/fund-trade/client.py capital_flow       # 资金流向
```

#### Step 6: 综合分析 + 操作建议

读取 `.claude/skills/fund-trade/prompts/alipay_review.md` 模板，填入：
- `{holdings_data}` ← Step 1 解析的持仓数据
- `{market_data}` ← Step 5 的市场数据（如有）
- `{user_profile}` ← config.json 的 user_profile

Claude 按照模板进行分析，重点关注：

1. **结构性问题**（最重要）：
   - 同一标的（如纳斯达克100）持有多只基金 → 建议合并
   - A类和C类同时持有 → 建议统一
   - 小额碎片持仓 → 建议清理
   - 美股集中度过高 → 建议分散

2. **按标的分组分析**：标普500、纳斯达克100、A股宽基、A股行业、日本、商品、债券

3. **具体操作清单**：按优先级排序的操作建议（赎回/减仓/加仓/调整定投）

#### Step 7: 保存操作建议

将重要的操作建议记录到 `ft_alipay_decisions` 表：
```python
import fund_db
fund_db.save_alipay_decision(
    fund_name="XXX",
    fund_code="007722",
    action="sell",        # buy/sell/hold/clear/adjust
    sell_pct=100,         # 卖出比例
    reason="同标的基金过多，合并为规模最大的一只",
    confidence="high",
    market_view="..."
)
```

---

## 架构说明

```
用户: /fund-trade run
        │
        ▼
┌──────────────────────────────────────────────────┐
│              Claude Code（LLM 决策引擎）            │
│  1. 调脚本采集数据 + 新闻                            │
│  2. 调脚本计算量化信号                               │
│  3. 读取全部信息，综合分析                            │
│  4. 做出买入/卖出/持有决策                            │
│  5. 调脚本执行交易                                   │
└──────┬────────┬────────┬────────┬────────┬────────┘
       │        │        │        │        │
  fund_api  indicators  news   risk_mgr  trader
       │        │        │        │        │
       └────────┴────────┴────────┴────────┘
                         │
                      fund_db (PG 持久化)
```

**核心理念**：规则引擎算数字，LLM 读新闻、看数字、做判断。风控拦截极端情况。

## 工具脚本说明

| 脚本 | 功能 | 主要命令 |
|------|------|---------|
| `fund_db.py` | 数据库层（11张表 CRUD + 支付宝映射） | `init`, `log-run`, `create-reviews`, `pending-reviews`, `review-stats`, `lessons`, `alipay-save`, `alipay-positions`, `alipay-history`, `alipay-dates`, `alipay-decisions`, `alipay-map`, `alipay-list-map`, `alipay-resolve` |
| `fund_api.py` | API 封装 + PG 缓存 | `scan`, `news`, `market`, `news-overview`, `news-drill`, `drill-deep` |
| `indicators.py` | 量化指标计算 | `evaluate` |
| `risk_manager.py` | 风控硬约束 | `snapshot`, `check` |
| `trader.py` | 交易执行 + 记录 | `buy`, `sell` |
| `server.py` | 同花顺 FastAPI 服务 | 直接运行启动 |
| `client.py` | CLI 工具（70+ 命令） | `ranking`, `headlines` 等 |

## 用法示例

```bash
# 初始化
/fund-trade init

# AI 选基入池
/fund-trade select 偏股

# 查看市场
/fund-trade market

# 单基分析
/fund-trade analyze 006888

# 每日交易（模拟）
/fund-trade run --dry

# 每日交易（实际执行）
/fund-trade run

# 持仓审视
/fund-trade review

# 决策复盘
/fund-trade retrospect

# 查看经验知识库
/fund-trade lessons

# 查看/修改配置
/fund-trade config

# 支付宝持仓管理
/fund-trade alipay
/fund-trade alipay /path/to/ocr_text.txt
```
