# 基金智能交易 Agent

基于 Claude Code Skill 的自动化基金交易系统，支持 LLM 决策 + 量化信号 + 风控硬约束。

## 架构说明

### 两层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Client 层: fund-trade/client.py (7K)                        │
│  - 轻量级 HTTP 客户端                                         │
│  - 命令行接口封装                                             │
│  - 无业务逻辑，仅转发请求                                      │
└─────────────────────────────────────────────────────────────┘
                            │ HTTP (localhost:8900)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Server 层: ths/api/server.py (43K)                          │
│  - FastAPI 服务器 (8900端口)                                 │
│  - 业务逻辑层（风控/量化/决策复盘）                             │
│  - ths_fund_client.py: 同花顺 API 包装层 (104方法)            │
│  - 使用 auth_cache.json 缓存认证参数，后台自动刷新              │
└─────────────────────────────────────────────────────────────┘
                            │ HTTPS (直接调用)
                            ▼
            同花顺服务器 (fund.10jqka.com.cn / trade.5ifund.com)
```

### 核心理念

- LLM (Claude) 通过轻量级客户端调用 API，读新闻、看数据、做决策
- Server 层处理所有业务逻辑（风控、量化、交易）
- 使用 `auth_cache.json` 缓存的认证参数，后台自动刷新
- 数据库（PostgreSQL）持久化所有数据

---

## API 功能模块

| 模块 | 功能 | client.py 命令 |
|------|------|---------------|
| **风控** | 风控快照 | `python client.py snapshot` |
| | 交易前置检查 | `python client.py preflight` |
| **量化信号** | 计算量化信号 | `python client.py evaluate` |
| **决策复盘** | 创建待复盘记录 | `python client.py create-reviews` |
| | 获取待复盘决策 | `python client.py pending-reviews` |
| | 获取复盘统计 | `python client.py review-stats` |
| | 获取经验知识库 | `python client.py lessons` |
| **交易** | 持仓查询 | `python client.py positions` |
| | 订单查询 | `python client.py orders` |
| | 买入基金 | `python client.py buy 008087 100` |
| | 卖出基金 | `python client.py sell 008087 --all` |
| **决策管理** | 今日决策 | `python client.py today-decisions` |
| | 最近决策 | `python client.py recent-decisions 5` |
| | 连续观望天数 | `python client.py watch-streaks` |
| **数据采集** | 基金扫描（精简） | `python client.py scan-summary` |
| | 持仓同步 | `python client.py sync` |
| **账户信息** | 账户总览 | `python client.py account-overview` |
| | 钱包信息 | `python client.py wallet-info` |
| | 钱包首页 | `python client.py wallet-home` |

---

## 完整命令列表

### 基金查询（需要基金代码）

| 命令 | 说明 | 示例 |
|------|------|------|
| `all` | 获取全部信息（默认） | `python client.py 006888` |
| `detail` | 基金综合详情 | `python client.py 006888 detail` |
| `product` | 产品详情（投资理念、风险特征） | `python client.py 006888 product` |
| `rank` | 阶段涨幅排名 | `python client.py 006888 rank` |
| `year_return` | 年度收益率 | `python client.py 006888 year_return` |
| `drawdown` | 最大回撤 | `python client.py 006888 drawdown` |
| `stability` | 收益稳定度 | `python client.py 006888 stability` |
| `holdings` | 前十大持仓 | `python client.py 006888 holdings` |
| `hold_overview` | 持仓概览 | `python client.py 006888 hold_overview` |
| `valuation` | 持仓股估值 | `python client.py 006888 valuation` |
| `position` | 持仓变动追踪 | `python client.py 006888 position` |
| `profit` | 盈亏贡献 | `python client.py 006888 profit` |
| `style` | 投资风格偏好 | `python client.py 006888 style` |
| `asset` | 资产配置 | `python client.py 006888 asset` |
| `nav` | 历史净值 | `python client.py 006888 nav` |
| `realtime` | 实时估值 | `python client.py 006888 realtime` |
| `manager` | 基金经理档案 | `python client.py 006888 manager` |
| `rsi` | RSI买卖指标 | `python client.py 006888 rsi` |
| `trade_rule` | 交易规则费率 | `python client.py 006888 trade_rule` |
| `scale_change` | 规模变动历史 | `python client.py 006888 scale_change` |
| `holder_ratio` | 机构持仓比例 | `python client.py 006888 holder_ratio` |
| `dividend` | 分红历史 | `python client.py 006888 dividend` |
| `announcements` | 基金公告 | `python client.py 006888 announcements` |
| `news` | 基金相关资讯 | `python client.py 006888 news` |
| `compare` | 同类基金对比 | `python client.py 006888 compare` |
| `pe_percentile` | 持仓股估值百分位 | `python client.py 006888 pe_percentile` |
| `nav_technical` | 净值技术面分析 | `python client.py 006888 nav_technical` |
| `market` | 大盘环境 | `python client.py 006888 market` |
| `fund_flow` | 基金申赎资金流 | `python client.py 006888 fund_flow` |

### 市场数据（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `market_overview` | A股大盘总览 |
| `hot_board` | 热点板块 |
| `sector_rank` | 板块涨跌排行 |
| `stock_rank` | 个股涨跌排行 |
| `capital_flow` | 资金流向 |
| `currency` | 货币风向（汇率/Shibor） |
| `dragon_tiger` | 龙虎榜 |
| `ths_dragon_tiger` | 同花顺龙虎榜 |
| `market_changes` | 大盘异动 |
| `stock_changes` | 个股异动 |
| `yesterday_limit` | 昨日涨停今日表现 |

### 热点资讯（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `hotlist` | 市场热榜 |
| `hotlist_topics` | 热榜话题 |
| `hotlist_posts` | 热门文章 |
| `headlines` | 推荐头条 |
| `news_themes` | 新闻主题分类 |
| `theme_articles` | 主题文章列表 |
| `flash_news` | 快讯分类 |
| `news_feed` | 滚动快讯 |

### 基金排行筛选（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `ranking` | 基金排行（默认近一年涨幅） |
| `ranking --fund-type qdii` | QDII 基金排行（美股/海外） |
| `ranking --fund-type stock` | 股票型基金排行 |
| `ranking --fund-type mixed` | 混合型基金排行 |
| `ranking --fund-type bond` | 债券型基金排行 |
| `ranking --fund-type index` | 指数型基金排行 |
| `ranking --sort-type month` | 按近一月涨幅排行 |
| `screen` | 策略筛选 |
| `search <关键词>` | 基金搜索（如 search 标普500、search 纳斯达克） |
| `companies` | 基金公司列表 |

### 交易账户（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `account` | 账户总览 |
| `positions` | 基金持仓列表 |
| `wallet` | 活期宝/钱包 |
| `autoinvest` | 定投计划 |
| `trade_binding` | 账户绑定信息 |
| `trade_all` | 全部交易数据汇总 |
| `account-overview` | 账户总览（JSON） |
| `wallet-info` | 钱包信息（JSON） |
| `wallet-home` | 钱包首页（JSON） |

### 基金交易

| 命令 | 说明 | 示例 |
|------|------|------|
| `buy` | 买入基金 | `python client.py buy 006888 --amount 100` |
| `sell` | 赎回基金 | `python client.py sell 006888 --all` |
| `order` | 查询订单详情 | `python client.py order <订单号>` |
| `orders` | 交易订单列表 | `python client.py orders` |
| `cancel` | 撤销订单 | `python client.py cancel <订单号>` |
| `set_password` | 设置交易密码 | `python client.py set_password <密码>` |

### 个股查询

| 命令 | 说明 | 示例 |
|------|------|------|
| `stock_quote` | 个股实时行情 | `python client.py stock_quote 600519` |
| `stock_kline` | 个股K线数据 | `python client.py stock_kline 600519` |
| `stock_flow` | 个股资金流向 | `python client.py stock_flow 600519` |
| `stock_valuation` | 个股估值历史 | `python client.py stock_valuation 600519` |
| `stock_financial` | 个股财务数据 | `python client.py stock_financial 600519` |

---

## 快速开始

### 1. 启动服务

```bash
# 启动 FastAPI 服务器（8900端口）
cd /home/yuyang/frida-test/ths/api
python server.py
```

### 2. 初始化

```bash
# 进入 Claude Code
claude

# 执行初始化
/fund-trade init
```

### 3. 手动交易（交互模式）

```bash
claude
/fund-trade run          # 完整决策+执行
/fund-trade run --dry    # 模拟运行，不执行交易
```

---

## 自动化配置

### 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         cron job                                 │
│  50 14 * * 1-5  agent_cron.sh run        # 尾盘决策+执行         │
│  30 15 * * 1-5  agent_cron.sh retrospect # 盘后复盘             │
│  0 18 * * 1-5   agent_cron.sh sync       # 持仓同步             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Claude Code (claude -p)                       │
│   读取 SKILL.md → 执行完整流程 → 自动决策 → 自动执行交易          │
└─────────────────────────────────────────────────────────────────┘
```

### 定时任务

| 时间 | 任务 | 命令 | 说明 |
|------|------|------|------|
| **14:50** | 尾盘决策 | `/fund-trade run --auto` | 基于当天数据做决策并执行 |
| 15:30 | 盘后复盘 | `/fund-trade retrospect` | 分析决策效果，更新知识库 |
| 18:00 | 持仓同步 | `/fund-trade sync` | 净值公布后同步持仓 |

### 配置 cron job

```bash
# 编辑 crontab
crontab -e

# 添加以下内容
50 14 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh run
30 15 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh retrospect
0 18 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh sync
```

---

## 文件结构

### Client 层（fund-trade/）

```
.claude/skills/fund-trade/
├── SKILL.md          # Skill 定义（Claude Code 读取）
├── README.md         # 本文档
├── agent_cron.sh     # cron job 包装脚本
├── config.json       # 客户端配置 (server_url, trade_password)
├── client.py         # 轻量级 HTTP 客户端 (7K)
└── prompts/          # LLM 决策 Prompt 模板
    ├── daily_decision.md
    ├── review_decision.md
    └── ...
```

### Server 层（ths/api/）

```
ths/api/
├── server.py              # FastAPI 服务器 (43K, 8900端口)
├── ths_fund_client.py     # 同花顺 API 包装层 (178K, 104方法)
├── auth_cache.json        # 认证参数缓存（后台自动刷新）
├── fund_db.py             # 数据库操作
├── risk_manager.py        # 风控管理
├── indicators.py          # 量化指标计算
└── review_decision_executor.py  # 决策复盘执行器
```

---

## 常见问题

### Q: 交易失败？

1. 检查服务是否运行：`curl -s http://localhost:8900/health`
2. 查看日志：`tail -50 ~/fund-agent.log`

### Q: 如何检查服务是否正常运行？

```bash
# 检查 FastAPI 服务器（8900端口）
curl -s http://localhost:8900/health

# 检查交易前置条件
python client.py preflight
```

### Q: 如何暂时停止自动化？

```bash
crontab -r  # 删除所有定时任务
```
