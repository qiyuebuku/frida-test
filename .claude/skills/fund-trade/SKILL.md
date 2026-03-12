---
name: fund-trade
description: 基金智能交易 - LLM 决策引擎 + 量化信号 + 风控硬约束
user-invocable: true
---

# 基金智能交易 Skill

## 命令列表

```bash
/fund-trade <command> [options]
```

| 命令 | 说明 |
|------|------|
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
| `ocr-analyze [action]` | **截屏持仓分析**：从 sa_ocr_records 读取 OCR 数据 → 市场分析 → 持仓建议 |

---

## 重要约束

- **所有与服务端的交互必须通过 `python client.py` 命令**，禁止用 curl 直接请求 API（健康检查除外）
- **不要直接读取 client.py**（文件超过 60K tokens 会超出限制），查看可用命令请读取 `docs/CLIENT_USAGE.md`
- 需要了解 client.py 的命令用法时，执行 `python client.py --help` 或读取 `docs/CLIENT_USAGE.md`

## 前置条件

1. **服务端已部署**：`http://119.23.227.187:8900`（远程公网，通过 frp 映射）
2. **PostgreSQL 已运行**：远程服务器 127.0.0.1:5432，dbname=jettask
3. **认证参数已配置**：`auth_cache.json` 中包含有效的认证 token（通过 `python client.py refresh-token` 刷新）

---

## 命令分发逻辑

### `/fund-trade run` （核心命令，Step 0 到 Step 7）

**这是交互式命令，Claude 自身作为决策引擎。**

#### Step 0: 服务检查 + 前置检查

**0a. 服务连通性检查**：
```bash
curl -s --noproxy '*' --connect-timeout 3 http://119.23.227.187:8900/health
```

如果服务不可用，通过部署脚本重启：
```bash
cd /home/yuyang/frida-test/smart-fund-server && bash deploy.sh --restart
```

**0b. 前置检查**：
```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade
python client.py preflight
```

检查项：是否交易日、是否触发熔断。如果非交易日或已熔断，停止执行。

#### Step 0.5: 自动复盘昨日决策

```bash
python client.py create-reviews
python client.py pending-reviews
```

如果有待复盘的决策：
1. 采集相关基金最新净值
2. 获取已有经验知识库：`python client.py lessons`
3. 读取 `prompts/review_decision.md` 模板
4. Claude 对比决策日净值 vs T+1/T+2 净值，判定 outcome
5. 将复盘结果写入数据库
6. 如果提炼出新经验，写入 ft_lessons
7. 验证 5+ 次且成功率 < 30% 的经验会被**自动废弃**

#### Step 1: 概览扫描

```bash
# 可以并行执行这 3 个命令
python client.py evaluate > /tmp/ft_signals.json
python client.py snapshot > /tmp/ft_snapshot.json
python client.py news_overview > /tmp/ft_news.json
```

执行完后用 Read 工具读取这 3 个文件。Claude 读取概览后：
1. 从热门文章 + 重要快讯中提取 TOP 5 关键事件
2. 板块涨跌 vs 基金池行业交叉匹配
3. 输出"深入计划"（列出最多 6 个定向查询方向及理由）

#### Step 2: 定向深入

根据深入计划，动态调用新闻和市场 API（每次 1 个方向）：

**可用模式**：
| 模式 | 用法 | 说明 |
|------|------|------|
| `themes` | `python client.py news_themes` | 新闻主题列表 |
| `headlines` | `python client.py headlines` | 推荐头条 |
| `flash` | `python client.py flash_news` | 快讯分类 |
| `news_feed` | `python client.py news_feed` | 滚动快讯 |
| `fund-news` | `python client.py <code> news` | 基金相关新闻 |
| `hot-board` | `python client.py hot_board` | 热门板块 |
| `dragon-tiger` | `python client.py dragon_tiger` | 龙虎榜 |
| `stock_changes` | `python client.py stock_changes` | 个股异动 |

**约束**：最多 6 次调用，每次后评估"信息是否足够"。
完成后撰写"结构化摘要"（500 字以内）。

#### Step 2.5: 基金级消息深挖（强制！）

**A股是消息市场、政策市场，消息对决策至关重要。**

对基金池中的每只基金，执行（可并行）：
```bash
python client.py <code> news
python client.py <code> announcements --count 5
python client.py <code> holdings
```

**分析要点**：
1. **基金自身消息**：是否有基金经理变更、规模限购、分红公告
2. **重仓股关联**：重仓股所属行业今日是否有政策/消息面变化
3. **消息→决策映射**：每条重要消息必须在后续决策中体现权重

**输出**：为每只基金撰写一段消息分析摘要（3-5 句）。

#### Step 3: 量化信号 + scan

```bash
python client.py scan-summary > /tmp/ft_scan.json
```

scan-summary 输出每只基金包含：
- 基本信息：`code`, `name`, `type`, `nav`, `rate`（今日涨跌）
- 排名：`rank_month`, `rank_year`, `yield_month`, `yield_year`
- 回撤：`drawdown_1y`, `drawdown_rank`
- 购买限制：`min_buy`, `max_buy`, `can_buy`

**重要：检查购买限制**，买入金额必须在 `[min_buy, max_buy]` 范围内。

**限额自动追踪**：每次买入初始化会自动将限额信息（`min_buy`、`max_buy`）保存到数据库，可通过 `python client.py limits` 查看。对于 QDII 基金，每日限额通常只有 10-500 元，需要分散到多只基金才能完成目标投资额度。

#### Step 3.5: 按需补充

深度数据按需调用，最多 3 次：
```bash
python client.py <code> holdings        # 前十大持仓
python client.py <code> pe_percentile   # PE百分位（估值）
python client.py yesterday_limit        # 昨日涨停今日表现
python client.py currency               # 货币风向
python client.py capital_flow           # 资金流向
```

#### Step 3.6: 待确认订单检查（重要！）

```bash
python client.py orders
```

如果有"处理中 [可撤]"的订单（`endFlag == "0" and processStatus == "1"`）：

1. 获取该订单对应基金的**昨日决策理由**（从今日决策记录或历史决策查询）
2. 结合前面采集的**全部市场信息**，判断市场风向是否变化
3. **决策逻辑**：
   - **风向未变**（利好/利空因素仍在）→ 不撤销，让订单自然确认
   - **风向逆转**（昨日利好变利空，或出现重大风险）→ **必须撤销**：
     ```bash
     python client.py cancel <订单号>
     ```

**撤销示例**：
- 昨日买入理由："AI 政策利好"，今日 AI 板块大跌 -3%+ 或出现利空政策 → **撤销**
- 昨日买入理由："黄金避险"，今日中东局势缓和、金价暴跌 → **撤销**
- 昨日买入理由："港股科技反弹"，今日美股暴跌、港股低开 -2%+ → **撤销**

**不撤销示例**：
- 昨日买入理由："超跌反弹"，今日继续下跌但无新利空 → 不撤销（坚持左侧试仓逻辑）
- 昨日买入理由："QDII DCA 建仓"，今日美股下跌 -1% → 不撤销（DCA 策略不择时）
- 昨日买入理由："地缘冲突避险"，今日地缘局势持续紧张 → 不撤销（逻辑未变）

**⚠️ 撤销后必须记录原因**：在决策汇总中说明为何撤销该订单。

#### Step 4: Claude 综合决策（核心！）

读取 `prompts/daily_decision.md` 模板，将以下数据填入：
- `{overview_data}` ← `/tmp/ft_overview.json`
- `{drill_findings}` ← Step 2 后撰写的结构化摘要
- `{fund_news_analysis}` ← Step 2.5 撰写的个基消息分析
- `{fund_signals}` ← `/tmp/ft_signals.json`
- `{risk_snapshot}` ← `/tmp/ft_snapshot.json`
- `{allocation}` ← config.json 的 allocation 字段
- `{strategy}` ← config.json 的 strategy 字段
- `{today_operations}` ← `python client.py today-decisions`
- `{recent_decisions}` ← `python client.py recent-decisions --count 5`
- `{lessons}` ← `python client.py lessons`
- `{watch_streaks}` ← `python client.py watch-streaks`

**输出决策 JSON**：
```json
{
  "date": "YYYY-MM-DD",
  "market_phase": "趋势上行|震荡企稳|趋势下行|恐慌出清",
  "risk_bias": "偏进攻(怕踏空)|均衡|偏防守(怕亏损)",
  "decisions": [
    {
      "fund_code": "006888",
      "fund_name": "...",
      "action": "buy|sell|hold|watch",
      "amount": 500,
      "sell_pct": null,
      "phase": "左侧试仓|...",
      "reason": "...",
      "confidence": "high|medium|low",
      "watch_streak": 0,
      "referenced_lesson_ids": []
    }
  ],
  "market_view": "...",
  "risk_notes": "..."
}
```

#### Step 5: 风控校验

```bash
python client.py risk-check '<决策JSON>'
```

如果有被拦截的决策 → Claude 查看原因，决定是否调整。

#### Step 6: 保存决策 + 执行交易

**6a. 保存所有决策到 ft_decisions**（无论是否执行交易，所有决策都必须记录）：

对 Step 4 决策 JSON 中的每一条 decision，通过 client.py 保存：
```bash
python client.py save-decision '{"fund_code":"015945","fund_name":"易方达国防军工混合C","action":"buy","amount":500,"reason":"...","confidence":"high","market_view":"...","referenced_lesson_ids":[1,3]}'
```

**6b. 执行交易**（**核心！必须主动执行**）：

**⚠️⚠️⚠️ 重要：对于 buy/sell/cancel 决策，Claude 必须主动调用交易命令！不能只是输出建议！⚠️⚠️⚠️**

如果是 `--dry` 模式，跳过交易执行，只展示决策结果。

**交易截止时间处理**：
- **14:50 前**：订单当日确认（T+1 到账），直接执行
- **14:50 后**：订单次日确认（T+2 到账），**需要用户确认后才执行**！

**⚠️ 14:50 后执行交易前必须确认**：使用 `AskUserQuestion` 工具询问用户是否继续执行。

---

**策略 1: 买入 (buy)**

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade
python client.py buy 015945 --amount 500
```

- 必须带 `--amount` 参数指定金额
- 金额不能低于基金最低起购额（通常 10-1000 元不等）
- QDII 基金通常有每日限额（10-500 元），超额会失败
- 成功后返回订单号 `order_no`

---

**策略 2: 卖出 (sell)**

```bash
# 全部赎回
python client.py sell 015945 --all

# 部分赎回（按百分比）
python client.py sell 015945 --percent 50
```

- `--all` 全部赎回
- `--percent N` 赎回 N% 的份额
- 注意赎回费率：通常持有 < 7 天收取 1.5% 惩罚性赎回费

---

**策略 3: 撤销订单 (cancel)**

```bash
# 查看待确认订单
python client.py orders

# 撤销指定订单
python client.py cancel 00000000002785670428
```

**何时撤销**：
- 昨日下单后市场风向逆转（如昨日买入科技，今日科技暴跌 -3%+）
- 出现重大利空消息（政策变化、黑天鹅事件）
- 发现决策错误（重复下单、金额错误）

**撤销窗口**：
- 14:50 前下单 → 当日 15:00 前可撤
- 14:50 后下单 → 次日 15:00 前可撤

---

**策略 4: 持有/观望 (hold/watch)**

- **hold**：已有持仓，继续持有不动
- **watch**：关注但不操作（可能等待更好的入场点）

这两种决策**不执行交易命令**，但必须保存到 ft_decisions 供复盘系统使用。

---

**执行后检查**：
- 检查命令输出是否包含订单号（`order_no`）
- 如果失败，分析错误原因并决定是否重试
- 交易成功后，订单会自动记录到 ft_decisions 表的 `order_no` 和 `executed_at` 字段

#### Step 7: 记录 + 汇总

向用户输出今日操作汇总：市场观点、每只基金的决策和理由、实际执行的交易、风控拦截的交易。

---

### `/fund-trade select [类型]`

**重要：本系统必须选 C 类基金（免申购费），绝不选 A 类。**

#### Phase 0: 了解用户偏好（首次或 user_profile 为空时）

如果 `config.json` 的 `user_profile.risk_tolerance` 为 null，**必须先跟用户沟通**，了解：
1. **风险承受能力**：高/中/低？能接受多大的浮亏？
2. **偏好行业**：科技/消费/医药/新能源/金融/周期？
3. **投资周期**：短线波段（1-4周）/ 中线趋势（1-3月）/ 混合？
4. **资产配置意向**：核心/卫星/对冲目标比例
5. **总资金量**：确认 total_capital 是否准确

使用 `AskUserQuestion` 工具询问，然后保存到 `config.json`。

#### Phase 1: 持仓同步 + 诊断

```bash
python client.py sync
python client.py snapshot
```

分析当前持仓：行业分布、核心/卫星/对冲的实际仓位 vs 目标比例。

#### Phase 2: 数据采集

```bash
python client.py ranking  # 默认近一年涨幅排行
python client.py market_overview
```

#### Phase 3: 智能筛选

读取 `prompts/select_fund.md` 模板，按**配置缺位优先级**筛选：
- 先诊断组合缺什么（核心不足？卫星不足？对冲为零？）
- 按缺位补齐，**只考虑 C 类基金**
- 确保与现有持仓行业互补

#### Phase 4: 确认加入

向用户展示推荐的 3-5 只基金及理由，确认后更新 `config.json` 的 `fund_pool`。

---

### `/fund-trade analyze <基金代码>`

1. 采集该基金全维度数据：
   ```bash
   python client.py <code> all
   ```
2. 获取量化信号：
   ```bash
   python client.py evaluate
   ```
3. 读取 `prompts/analyze_fund.md` 模板
4. Claude 输出该基金的全面分析报告

---

### `/fund-trade market`

```bash
python client.py market_overview
python client.py sector_rank
python client.py capital_flow
python client.py hot_board
```

分析市场数据，输出：
- 大盘走势判断（强/弱/震荡）
- 资金流向分析
- 板块涨跌 TOP 10
- 重要快讯解读
- 对基金投资的影响

---

### `/fund-trade review`

```bash
python client.py snapshot
python client.py evaluate
python client.py recent-decisions --count 30
```

读取 `prompts/portfolio_review.md` 模板，输出持仓审视报告 + 调仓建议。

---

### `/fund-trade retrospect`

手动触发决策复盘（正常情况下 `run` 命令会自动执行 Step 0.5 复盘）。

```bash
python client.py create-reviews
python client.py pending-reviews
python client.py scan-summary  # 获取最新净值
```

读取 `prompts/review_decision.md` 模板，分析 T+1/T+2 净值变化，判定 outcome，更新经验知识库。

---

### `/fund-trade lessons`

```bash
python client.py lessons
```

按类别汇总展示：
- 政策类经验（policy）
- 技术指标类经验（technical）
- 市场情绪类经验（sentiment）
- 行业轮动类经验（sector）
- 择时类经验（timing）
- 风控类经验（risk）

每条经验展示：触发模式、教训、可信度、验证次数/成功次数。

---

### `/fund-trade config`

读取并展示 `config.json`：
- 基金池列表
- 风控参数
- 服务器地址
- 总资产设置

用户可要求修改参数，Claude 直接编辑 config.json。

---

### `/fund-trade alipay [文件路径]`

**支付宝基金持仓管理** - 解析 OCR 文本，分析持仓，给出手动操作建议。

这些基金在支付宝中持有，**无法通过程序自动交易**。

默认读取文件：`data/支付宝持仓.txt`

#### Step 1: 读取并解析 OCR 文本

Claude 直接读取 OCR 文本文件（使用 Read 工具），用 LLM 自身能力解析出每只基金的：
- 基金名称、当前市值、持有收益、昨日收益、分类

**重要**：OCR 文本格式不固定，由 Claude 直接理解文本内容提取数据。

#### Step 2: 解析基金代码

```bash
python fund_db.py alipay-resolve '<["基金名1", "基金名2", ...]>'
```

如有未解析的基金名称，通过 web 搜索后添加映射：
```bash
python fund_db.py alipay-map "基金名称" "基金代码"
```

#### Step 3: 保存持仓快照

```bash
echo '<JSON数据>' | python fund_db.py alipay-save
```

#### Step 4: 获取历史数据对比（可选）

```bash
python fund_db.py alipay-positions           # 最新持仓
python fund_db.py alipay-positions 2026-03-01 # 指定日期
python fund_db.py alipay-history "天弘标普500(QDII-FOF)C" 30  # 单基金30天历史
```

#### Step 5: 获取市场数据

```bash
python client.py <基金代码> detail
python client.py <基金代码> drawdown
python client.py <基金代码> rsi
python client.py <基金代码> valuation
python client.py market_overview
```

#### Step 6: 综合分析 + 操作建议

读取 `prompts/alipay_review.md` 模板，重点关注：

1. **结构性问题**（最重要）：
   - 同一标的持有多只基金 → 建议合并
   - A类和C类同时持有 → 建议统一
   - 小额碎片持仓 → 建议清理
   - 美股集中度过高 → 建议分散

2. **按标的分组分析**：标普500、纳斯达克100、A股宽基、A股行业、日本、商品、债券

3. **具体操作清单**：按优先级排序的操作建议

#### Step 7: 保存操作建议

将重要的操作建议记录到 `ft_alipay_decisions` 表。

---

### `/fund-trade ocr-analyze [action]`

**截屏持仓分析** - 从 `sa_ocr_records` 表读取 OCR 数据 → 采集市场行情 → 综合分析持仓。只分析不交易。

默认 action 为 `fund_holdings`。执行时读取详细流程文档：`prompts/ocr_analyze_flow.md`

---

## client.py 命令速查

详细命令列表见 README.md。

### 交易
```bash
python client.py buy 006888 --amount 100
python client.py sell 006888 --all
python client.py orders
python client.py cancel <订单号>
```

### 风控/量化
```bash
python client.py preflight
python client.py snapshot
python client.py evaluate
```

### 数据同步
```bash
python client.py sync
python client.py scan-summary
```

### 决策管理
```bash
python client.py today-decisions
python client.py recent-decisions --count 5
python client.py watch-streaks
```

### 复盘
```bash
python client.py create-reviews
python client.py pending-reviews
python client.py lessons
```

### 限额管理
```bash
python client.py limits                    # 查看所有已记录的基金限额
python client.py limits-summary            # 限额统计摘要
python client.py limits-plan --amount 2000 # 智能分配购买计划
```

**注意**：限额信息在每次买入初始化时自动保存，无需手动维护。

### OCR 记录查询
```bash
python client.py ocr-records --count 10            # 查看最近10条OCR记录
python client.py ocr-records fund_holdings          # 按action筛选
python client.py ocr-latest                         # 最新一条OCR记录（全文）
python client.py ocr-latest fund_holdings           # 最新一条持仓截图OCR
python client.py ocr-latest --count 3               # 最新3条
```

### 基金查询
```bash
# 综合信息
python client.py 006888                        # 全部信息（all）
python client.py 006888 detail                 # 综合详情
python client.py 006888 product                # 产品详情（投资理念、风险特征）

# 净值与收益
python client.py 006888 nav                    # 历史净值（默认近一年）
python client.py 006888 nav --period month     # 近一月净值
python client.py 006888 nav --count 30         # 显示30条
python client.py 006888 realtime               # 实时估值走势
python client.py 006888 rank                   # 阶段涨幅排名
python client.py 006888 year_return            # 年度收益率
python client.py 006888 drawdown               # 最大回撤
python client.py 006888 stability              # 收益稳定度

# 持仓信息
python client.py 006888 holdings               # 前十大持仓
python client.py 006888 hold_overview          # 持仓概览
python client.py 006888 position               # 持仓变动追踪
python client.py 006888 profit                 # 盈亏贡献
python client.py 006888 style                  # 投资风格偏好
python client.py 006888 asset                  # 资产配置
python client.py 006888 valuation              # 持仓股估值
python client.py 006888 pe_percentile          # 持仓股估值百分位

# 基金经理与公司
python client.py 006888 manager                # 基金经理档案

# 交易相关
python client.py 006888 trade_rule             # 交易规则费率
python client.py 006888 rsi                    # RSI买卖指标

# 规模与持有人
python client.py 006888 scale_change           # 规模变动历史
python client.py 006888 holder_ratio           # 机构持仓比例
python client.py 006888 dividend               # 分红历史

# 资讯与公告
python client.py 006888 news                   # 基金相关资讯
python client.py 006888 announcements          # 基金公告

# 分析工具
python client.py 006888 nav_technical          # 净值技术面分析
python client.py 006888 market                 # 大盘环境
python client.py 006888 fund_flow              # 基金申赎资金流
python client.py 006888 compare                # 同类基金对比
```

### 市场数据
```bash
python client.py market_overview
python client.py hot_board
python client.py sector_rank
python client.py hotlist
python client.py headlines
python client.py news_feed
```

### 基金排行与筛选
```bash
python client.py ranking                       # 默认近一年涨幅排行
python client.py ranking --fund-type qdii      # QDII 基金排行（美股/海外）
python client.py ranking --fund-type stock     # 股票型基金排行
python client.py ranking --fund-type mixed     # 混合型基金排行
python client.py ranking --fund-type bond      # 债券型基金排行
python client.py ranking --fund-type index     # 指数型基金排行
python client.py ranking --sort-type month     # 按近一月涨幅排行
python client.py ranking --board 涨幅榜        # 预设排行榜
python client.py screen                        # 查看可用筛选策略
python client.py screen --strategy fund0001    # 年年正收益
python client.py search 标普500                # 基金搜索（按名称关键词）
python client.py search 纳斯达克 --count 20    # 基金搜索，返回20条
python client.py search 医疗                   # 搜索医疗相关基金
```

---

## 用法示例

```bash
/fund-trade select 偏股    # AI 选基入池
/fund-trade market         # 查看市场
/fund-trade analyze 006888 # 单基分析
/fund-trade run --dry      # 模拟交易
/fund-trade run            # 实际执行
/fund-trade review         # 持仓审视
/fund-trade retrospect     # 决策复盘
/fund-trade lessons        # 经验知识库
/fund-trade config         # 配置管理
/fund-trade alipay         # 支付宝持仓
/fund-trade ocr-analyze    # 截屏持仓分析（默认 fund_holdings）
/fund-trade ocr-analyze ocr # 分析最近一次文字识别结果
```
