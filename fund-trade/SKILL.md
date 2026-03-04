---
name: fund-trade
description: 基金智能交易 - LLM 决策引擎 + 量化信号 + 风控硬约束
user_invocable: true
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

#### Step 0: 连通性检查 + 持仓同步 + 前置检查

**0a. 交易代理连通性**（非 `--dry` 模式必须检查）：

交易操作需要通过手机上同花顺 App 内的 Hook 代理（端口 18900）。执行前必须确保：

```bash
# 1. 检查 adb forward 是否已设置
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward --list

# 2. 如果没有 18900 映射，设置它
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:18900 tcp:18900

# 3. 同时确保 8900 端口的数据查询服务也映射了（如果 server.py 依赖手机端）
# /mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:8900 tcp:8900

# 4. 验证交易代理可达
curl -s --noproxy '*' --connect-timeout 5 http://127.0.0.1:18900/
```

如果交易代理不可达 → 提示用户检查：手机同花顺 App 是否打开、Hook 模块是否加载、adb 连接是否正常。

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
- 是否在交易截止时间（14:50）前
- 是否触发熔断（组合总亏损超过阈值）

如果 `can_trade=false` → 告知用户原因，停止执行（`--dry` 模式可跳过时间检查和连通性检查）。

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

#### Step 1-3: 数据采集 + 量化信号 + 风控快照

**重要**：所有脚本输出到临时文件，**不要用 `2>&1`**（会把 stderr 警告混入 JSON 导致解析失败），**不要 pipe 内联解析**（数据量大容易出错）。

```bash
# 可以并行执行这 5 个命令（互不依赖）
python .claude/skills/fund-trade/fund_api.py scan > /tmp/ft_scan.json
python .claude/skills/fund-trade/fund_api.py news > /tmp/ft_news.json
python .claude/skills/fund-trade/fund_api.py market > /tmp/ft_market.json
python .claude/skills/fund-trade/indicators.py evaluate > /tmp/ft_signals.json
python .claude/skills/fund-trade/risk_manager.py snapshot > /tmp/ft_snapshot.json
```

执行完后用 Read 工具读取这 5 个文件获取数据。scan 输出较大（~120KB），可只读取需要的部分。

#### Step 4: Claude 综合决策（核心！）

读取 `.claude/skills/fund-trade/prompts/daily_decision.md` 模板，将以下数据填入：
- `{news_data}` ← `/tmp/ft_news.json`
- `{market_data}` ← `/tmp/ft_market.json`
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

scan 数据不直接填入 prompt（太大），而是 Claude 自行阅读 `/tmp/ft_scan.json` 提取关键信息（净值趋势、持仓变动等）作为分析参考。

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
fund_db.save_decision(
    fund_code="022365",
    fund_name="永赢科技智选混合发起C",
    action="buy",       # buy/sell/hold/watch/clear
    amount=625,          # 买入金额（buy 时填，其他为 None）
    sell_pct=None,       # 卖出比例（sell 时填）
    reason="左侧试仓：企稳信号+AI双重催化",
    confidence="high",
    market_view="震荡企稳，科技有望领涨",
    risk_notes="地缘风险仍存，保留70%现金",
    referenced_lesson_ids=[3, 7]  # 参考了哪些经验
)
```

**重要**：hold 和 watch 决策也必须保存！复盘系统需要回顾所有决策（包括"不动"的决策）。

**6b. 执行交易**：

如果是 `--dry` 模式，跳过交易执行，只展示决策结果。

交易执行前需加载密码环境变量（密码存在 `.claude/skills/fund-trade/.env`，不进 git）：
```bash
# 加载环境变量后执行
export $(cat .claude/skills/fund-trade/.env | xargs) 2>/dev/null

# 买入
python .claude/skills/fund-trade/trader.py buy <code> <amount> --reason "..."

# 卖出
python .claude/skills/fund-trade/trader.py sell <code> <pct> --reason "..."
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

1. 采集市场数据：
   ```bash
   python .claude/skills/fund-trade/fund_api.py market
   python .claude/skills/fund-trade/fund_api.py news
   ```
2. Claude 阅读数据后输出：
   - 大盘走势判断（强/弱/震荡）
   - 资金流向分析
   - 板块热点
   - 重要政策/新闻解读
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
| `fund_db.py` | 数据库层（9张表 CRUD） | `init`, `log-run`, `create-reviews`, `pending-reviews`, `review-stats`, `lessons` |
| `fund_api.py` | API 封装 + PG 缓存 | `scan`, `news`, `market` |
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
```
