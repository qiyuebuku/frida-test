# client.py 使用说明

> 需要了解 client.py 的可用命令和用法时，读取此文件即可。
> **不要直接读取 client.py**（文件超过 60K tokens 会超出限制）。

## 基本用法

```
python client.py <基金代码> [命令] [选项]
python client.py <命令> [选项]              # 不需要基金代码的命令
```

## 命令列表

### 基金查询（需要基金代码）

| 命令 | 说明 |
|------|------|
| `all` | 获取全部信息（默认） |
| `detail` | 基金综合详情（净值、涨幅、经理、规模、夏普、年化等） |
| `product` | 产品详情（投资理念、业绩基准、风险特征） |
| `rank` | 阶段涨幅排名（近一周/月/季/半年/1-5年，含同类排名） |
| `year_return` | 年度收益率及同类排名 |
| `drawdown` | 最大回撤（近半年/一年/三年/成立以来） |
| `stability` | 收益稳定度（日/周/月/季/年，含胜率、均值） |
| `hold_overview` | 持仓概览（股票仓位、主要行业、持仓集中度） |
| `holdings` | 前十大持仓（股票代码、名称、占净值比） |
| `valuation` | 持仓股估值（PE/PB/市值/ROE） |
| `position` | 持仓变动追踪（对比相邻两个季度的前十大持仓变化） |
| `profit` | 盈亏贡献（各持仓股票的占净值比和贡献率） |
| `style` | 投资风格偏好（科技/周期/消费/制造/金融/医疗/军工） |
| `asset` | 资产配置（股票/债券/存款/其他 各季度占比） |
| `nav` | 历史净值走势（支持 --period year/month/nowyear） |
| `realtime` | 实时估值分时走势 |
| `manager` | 基金经理档案（简历、诊断评分、历年收益） |
| `rsi` | RSI 买卖指标 |
| `trade_rule` | 交易规则与费率 |
| `scale_change` | 规模变动历史 |
| `holder_ratio` | 机构持仓比例历史 |
| `dividend` | 分红历史 |
| `announcements` | 基金公告（--cat 分类, --page 翻页） |
| `news` | 基金相关资讯 |
| `compare` | 同类基金横向对比（--with 指定对比基金） |
| `pe_percentile` | 持仓股估值百分位（--years N 回溯年数） |
| `nav_technical` | 净值技术面分析（RSI14/均线/偏离度/信号） |
| `market` | 大盘与行业环境 |
| `fund_flow` | 基金申赎资金流趋势 |

### 配置与风控（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `config` | 查看配置（基金池、风控参数、资金设置） |
| `sync` | 同步持仓数据到本地数据库 |
| `snapshot` | 风控快照（持仓/仓位/可用资金/风控状态） |
| `preflight` | 交易前置检查（是否交易日、是否触发熔断） |
| `evaluate` | 量化信号评估（基金池中所有基金） |
| `scan-summary` | 基金池扫描精简版（净值、涨跌、月收益） |

### 决策管理（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `save-decision` | 保存决策到数据库（接受 JSON 字符串） |
| `risk-check` | 风控校验决策（接受 JSON 字符串） |
| `today-decisions` | 今日决策记录 |
| `recent-decisions` | 最近 N 天决策（--count N） |
| `watch-streaks` | 观望连续天数 |

### 复盘与经验（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `create-reviews` | 创建待复盘记录 |
| `pending-reviews` | 查看待复盘决策 |
| `review-stats` | 复盘统计 |
| `save-review` | 保存复盘结果（--review-id/--fund-code/--outcome/--notes） |
| `lessons` | 经验知识库 |

### 限额管理（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `limits` | 查看所有已记录的基金限额 |
| `limits-summary` | 限额统计摘要 |
| `limits-plan` | 智能分配购买计划（--amount N） |

### 认证管理（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `refresh-token` | 刷新认证 token |
| `auth-status` | 查看认证状态（过期时间、剩余有效期） |

### 交易账户（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `account` | 账户总览（总资产、累计盈亏、当日盈亏） |
| `positions` | 基金持仓列表 |
| `wallet` | 活期宝/钱包 |
| `autoinvest` | 定投计划 |
| `trade_binding` | 账户绑定信息 |
| `trade_all` | 全部交易数据一次性汇总 |
| `account-overview` | 账户总览（新版） |
| `wallet-info` | 钱包余额信息 |
| `wallet-home` | 钱包首页完整信息 |
| `orders` | 交易订单列表（近30天，标注可撤状态） |

### 基金交易

| 命令 | 用法 | 说明 |
|------|------|------|
| `buy` | `buy <代码> --amount <金额>` | 买入基金 |
| `sell` | `sell <代码> --all` 或 `--shares <份额>` | 赎回基金 |
| `order` | `order <订单号>` | 查询订单详情 |
| `cancel` | `cancel <订单号>` | 撤销订单 |
| `set_password` | `set_password <密码>` | 设置交易密码 |

### 基金排行与筛选（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `ranking` | 基金排行（--sort-type/--fund-type/--board/--min-scale） |
| `screen` | 策略筛选（--strategy fund0001 等） |
| `search` | 基金搜索（search <关键词>） |
| `companies` | 基金公司列表 |

### 市场数据（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `market_overview` | A股大盘总览（指数/涨跌/成交/资金/涨跌停） |
| `news_overview` | 新闻概览（重要+A股+滚动快讯聚合） |
| `hotlist` | 市场热榜（--market a/hk/us） |
| `hotlist_topics` | 热榜话题（--topic N 查看详情） |
| `hotlist_posts` | 热门文章 |
| `headlines` | 推荐头条（--detail N 查看详情） |
| `news_themes` | 新闻主题分类 |
| `theme_articles` | 主题文章列表（--theme TZ-xxx） |
| `flash_news` | 快讯分类（--tag 分类, --detail N） |
| `news_feed` | 滚动快讯（--page N） |
| `hot_board` | 热点板块（--hot-sort rise/flow/5day） |
| `sector_rank` | 板块涨跌排行（--sector-type concept/industry） |
| `stock_rank` | 个股涨跌幅排行（--sort rise/fall/volume） |
| `dragon_tiger` | 龙虎榜（--dt-tab stock/dept/org） |
| `ths_dragon_tiger` | 同花顺游资龙虎榜（--ths-dt-tab） |
| `capital_flow` | 资金流向（--cf-tab market/north） |
| `currency` | 货币风向（--currency-tab usdcny/shibor） |
| `market_changes` | 大盘异动 |
| `stock_changes` | 个股异动（--change-type） |
| `yesterday_limit` | 昨日涨停今日表现 |

### 个股查询（stock_xxx <股票代码>）

| 命令 | 说明 |
|------|------|
| `stock_quote` | 个股实时行情（支持批量，逗号分隔） |
| `stock_kline` | K线数据（--stock-period day/week/month） |
| `stock_flow` | 资金流向（--stock-days N） |
| `stock_valuation` | 估值历史（PE_TTM/PB，--years N） |
| `stock_financial` | 财务数据（EPS/营收/净利/ROE） |

### OCR 记录（不需要基金代码）

| 命令 | 说明 |
|------|------|
| `ocr-records` | 查看 OCR 记录（--count N, 可按 action 筛选） |
| `ocr-latest` | 最新 OCR 记录（--count N） |

## 常用选项

| 选项 | 说明 | 适用命令 |
|------|------|----------|
| `--count N` | 显示条数（默认10） | nav/announcements/news/stability 等 |
| `--period P` | 净值周期: year/month/nowyear | nav |
| `--page N` | 页码（默认1） | announcements/news_feed |
| `--amount N` | 买入金额（元） | buy |
| `--shares N` | 赎回份额 | sell |
| `--all` | 全部赎回 | sell |
| `--password P` | 交易密码 | buy/sell/cancel |
| `--no-wallet` | 不使用活期宝支付 | buy |
| `--fund-type T` | 基金类型: stock/mixed/bond/index/qdii | ranking |
| `--sort-type S` | 排序字段: year/month/sharpeYear 等 | ranking/screen |
| `--json` | 输出 JSON 格式 | 部分命令 |

## 示例

```bash
# 基金查询
python client.py 006888                                      # 全部信息
python client.py 006888 holdings                             # 前十大持仓
python client.py 006888 nav --period month --count 30        # 近一月净值
python client.py 006888 compare --with 022364,018956         # 对比基金

# 配置与风控
python client.py config                                      # 查看配置
python client.py snapshot                                    # 风控快照
python client.py evaluate                                    # 量化信号
python client.py scan-summary                                # 基金池扫描

# 决策管理
python client.py today-decisions                             # 今日决策
python client.py recent-decisions --count 5                  # 最近5天决策
python client.py save-decision '{"fund_code":"015945",...}'   # 保存决策

# 交易
python client.py buy 022365 --amount 500                     # 买入500元
python client.py sell 002207 --all                           # 全部赎回
python client.py sell 002207 --shares 180                    # 赎回180份
python client.py orders                                      # 查看订单
python client.py cancel 00000000002785670428                 # 撤销订单

# 市场数据
python client.py market_overview                             # 大盘总览
python client.py news_overview                               # 新闻概览
python client.py hot_board                                   # 热点板块
python client.py headlines --detail 1                        # 头条详情
python client.py flash_news --tag 重要                       # 重要快讯
python client.py ranking --fund-type qdii                    # QDII排行

# 个股
python client.py stock_quote 600519                          # 实时行情
python client.py stock_kline 600519 --stock-period week      # 周K线
python client.py stock_flow 600519 --stock-days 60           # 60日资金流
```
