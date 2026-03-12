# client.py 使用说明

> 此文件从 client.py 的 docstring 自动提取，供 Claude skill 快速查阅可用命令。
> 不要直接读取 client.py（文件过大会超出 token 限制），读取此文件即可。

同花顺基金 API 客户端 - 调用本地 API 服务获取数据

用法:
  python client.py <基金代码> [命令] [选项]

命令列表:
  all            获取全部信息（默认）
  detail         基金综合详情（净值、涨幅、经理、规模、夏普、年化等）
  product        产品详情（投资理念、业绩基准、风险特征、分红拆分等）
  rank           阶段涨幅排名（近一周/月/季/半年/1-5年，含同类排名）
  year_return    年度收益率及同类排名
  drawdown       最大回撤（近半年/一年/三年/成立以来，含同类排名）
  stability      收益稳定度（日/周/月/季/年，含胜率、均值、同类对比）
  hold_overview  持仓概览（股票仓位、主要行业、持仓集中度）
  holdings       前十大持仓（股票代码、名称、占净值比、期间涨幅）
  profit         盈亏贡献（各持仓股票的占净值比和贡献率）
  style          投资风格偏好（科技/周期/消费/制造/金融/医疗/军工 各季度占比）
  asset          资产配置（股票/债券/存款/其他 各季度占比）
  nav            历史净值走势（支持近一年/一月/今年以来）
  realtime       实时估值分时走势（每分钟更新）
  manager        基金经理档案（简历、诊断评分、历年收益、行业偏好、管理基金列表）
  rsi            RSI 买卖指标（买入区间上下限）
  trade_rule     交易规则与费率（买入/卖出规则、申赎费率、管理费、定投费率）
  scale_change   规模变动历史（季度净资产、申购赎回金额、份额变动）
  holder_ratio   机构持仓比例历史（半年度机构持有占比变化）
  dividend       分红历史（分红明细、拆分记录）
  valuation      持仓股估值（前十大持仓的 PE/PB/市值/ROE）
  position       持仓变动追踪（对比相邻两个季度的前十大持仓变化）
  announcements  基金公告（支持分类筛选和分页，含 PDF 下载链接）
  news           基金相关资讯（标题、来源、链接）
  compare        同类基金横向对比（自动发现同赛道基金或手动指定）
  pe_percentile  持仓股估值百分位（当前PE/PB在近N年的历史分位）
  nav_technical  净值技术面分析（RSI14/均线/偏离度/信号）
  market         大盘与行业环境（沪深300趋势/北向资金活跃度）
  fund_flow      基金申赎资金流趋势（季度净申赎/机构占比变化）

  === 基金排行与筛选（不需要基金代码） ===
  ranking        基金排行（同花顺原生数据，支持多种排序/预设排行榜/自定义筛选）
  screen         策略筛选（年年正收益/三年翻倍/机构偏爱等预设策略）
  companies      基金公司列表

  === 认证管理（不需要基金代码） ===
  refresh-token  刷新认证 token（优先 Zygisk，失败则密码登录）
  auth-status    查看认证状态（过期时间、剩余有效期）

  === 交易账户（不需要基金代码） ===
  account        账户总览（总资产、累计盈亏、当日盈亏、风险等级）
  positions      基金持仓列表（持仓基金明细、市值、收益）
  wallet         活期宝/钱包（货币基金收益、可用余额）
  autoinvest     定投计划（定投汇总、计划列表）
  trade_binding  账户绑定信息（客户ID、姓名、身份证号）
  trade_all      全部交易数据一次性汇总

  === 基金交易（不需要基金代码） ===
  buy            买入基金（buy <基金代码> --amount <金额>）
  sell           赎回基金（sell <基金代码> --all 或 --shares <份额>）
  order          查询交易订单（order <订单号>）
  orders         交易订单列表（近30天所有订单，标注可撤状态）
  cancel         撤销订单（cancel <订单号>）
  set_password   设置交易密码（明文或MD5均可）

  === 热榜命令（不需要基金代码） ===
  hotlist        市场热榜（个股/概念/行业/ETF 热度排名）
  hotlist_topics 热榜话题（同花顺社区热门讨论话题，--topic N 查看详情）
  hotlist_posts  热门文章（热门资讯/分析文章）
  headlines      推荐头条（首页推荐tab置顶的重要新闻/专题，--detail N 查看详情）
  news_themes    新闻主题分类（资讯tab的主题标签列表，如小金属、算力租赁等）
  theme_articles 主题文章列表（指定主题的最新动态，--theme TZ-11385）
  flash_news     快讯分类（A股/重要/公告/期货/异动/港股/美股，--tag 选分类，--detail N 看详情）
  market_overview A股大盘总览（指数行情/涨跌家数/成交额/资金流向/涨跌停/大小盘对比）
  market_changes 大盘异动（板块异动时间线+竞价异动，同花顺数据源）
  stock_changes  个股异动（火箭发射/竞价上涨/大笔买入/封涨停等22种，--change-type 选类型）
  yesterday_limit 昨日涨停今日表现（涨跌幅/振幅/连板数/封板时间/行业）
  stock_rank     个股涨跌幅排行（涨幅/跌幅/成交额/换手率榜，--sort 选排序）
  sector_rank    板块涨跌排行（概念/行业板块涨跌幅榜，--sector-type 选类型）
  hot_board      热点板块（今日涨幅/资金流入/5日涨幅 TOP N，--hot-sort 选排序）
  dragon_tiger   龙虎榜（个股明细/活跃营业部游资/机构买卖，--dt-tab 选维度）
  ths_dragon_tiger 同花顺游资龙虎榜（一线游资/知名游资/机构/跟风高手标签，--ths-dt-tab 选维度）
  capital_flow   资金流向（大盘主力资金净流入/北向资金成交额，--cf-tab 选维度）
  currency       货币风向（美元/离岸人民币汇率走势 / Shibor利率+LPR变化，--currency-tab 选维度）
  news_feed      滚动快讯（财经要闻实时滚动，每页20条，支持翻页）
  news_overview  新闻概览（重要快讯+A股快讯+滚动快讯 聚合展示）

  === 个股查询（stock_xxx <股票代码>） ===
  stock_quote    个股实时行情（支持批量，逗号分隔，腾讯数据源）
  stock_kline    个股K线数据（日K/周K/月K，前复权，--stock-period 选周期）
  stock_flow     个股资金流向（主力/超大单/大单/中单/小单，--stock-days 选天数）
  stock_valuation 个股估值历史（PE_TTM/PB历史数据，--years 选年数）
  stock_financial 个股财务数据（EPS/营收/净利/ROE/毛利率/分红方案）

选项:
  --count N      显示条数，默认 10（适用于 stability/nav/announcements/news）
  --period P     净值走势周期: year/month/nowyear（适用于 nav）
  --time-type T  盈亏贡献周期: threeMonth/halfYear/year（适用于 profit）
  --cat C        公告分类: all/report/dividend/change/operation/other（适用于 announcements）
  --page N       页码，默认 1（适用于 announcements/news_feed）
  --with CODES   对比基金代码，逗号分隔（适用于 compare）
  --years N      估值百分位回溯年数，默认 3（适用于 pe_percentile）
  --market M     热榜市场: a=A股, hk=港股, us=美股（适用于 hotlist）
  --topic N      查看第 N 个话题的详细内容（适用于 hotlist_topics）
  --detail N     查看第 N 条的详细内容（适用于 headlines/theme_articles）
  --theme ID     主题ID，如 TZ-11385（适用于 theme_articles）
  --tag TAG      快讯分类: a股/重要/公告/期货/异动/港股/美股 或数字ID（适用于 flash_news）
  --seq N        快讯翻页游标，传上一页最后一条的seq（适用于 flash_news）
  --change-type  异动类型: all/竞价/拉升/跳水/大单/涨停/跌停/火箭发射...（适用于 stock_changes）
  --sort S       排序方式: rise/fall/volume/turnover/turnover_rate（适用于 stock_rank）
  --sector-type  板块类型: concept=概念/industry=行业（适用于 sector_rank/hot_board）
  --hot-sort S   热点板块排序: rise=今日涨幅/flow=资金流入/5day=5日涨幅（适用于 hot_board）
  --dt-tab T     龙虎榜维度: stock=个股明细/dept=活跃营业部/org=机构买卖（适用于 dragon_tiger）
  --dt-days N    龙虎榜回溯天数，默认 3（适用于 dragon_tiger）
  --ths-dt-tab T 同花顺龙虎榜: youzi=游资/jigou=机构/gfgs=跟风高手/all=全部（适用于 ths_dragon_tiger）
  --cf-tab T     资金流向: market=大盘资金/north=北向资金（适用于 capital_flow）
  --cf-days N    资金流向回溯天数，默认 20（适用于 capital_flow）
  --currency-tab 货币风向: usdcny=美元/离岸人民币/shibor=Shibor利率+LPR（适用于 currency）
  --currency-days 货币风向回溯天数，默认 120（适用于 currency）
  --stock-period P K线周期: day=日K/week=周K/month=月K（适用于 stock_kline）
  --stock-days N 个股资金流回溯天数，默认 20（适用于 stock_flow）
  --sort-type S  排行排序字段: year/hyear/tmonth/month/week/sharpeYear/maxDrawDownYear（适用于 ranking/screen）
  --ranking-sort 排行排序方向: DESC=降序/ASC=升序（适用于 ranking）
  --board NAME   排行榜名称: 涨幅榜/反弹榜/人气榜/加仓榜/超额榜（适用于 ranking）
  --strategy KEY 筛选策略 key: fund0001=年年正收益/fund0002=三年翻倍等（适用于 screen）
  --min-scale N  最小规模（元），如 100000000=10亿（适用于 ranking）
  --offset N     翻页偏移量（适用于 ranking）
  --amount N     买入金额（元）（适用于 buy）
  --no-wallet    不使用活期宝支付（适用于 buy）
  --shares N     赎回份额数量（适用于 sell）
  --all          全部赎回（适用于 sell，默认行为）

示例:
  python client.py 006888                                      # 获取全部信息
  python client.py 006888 holdings                             # 前十大持仓
  python client.py 006888 nav --period month --count 30        # 近一月净值，显示30条
  python client.py 006888 profit --time-type year              # 近一年盈亏贡献
  python client.py 006888 stability --count 20                 # 收益稳定度，每组显示20期
  python client.py 006888 announcements --cat report           # 业绩报告公告
  python client.py 006888 announcements --cat change --page 2  # 变更公告第2页
  python client.py 006888 announcements --count 20             # 全部公告显示20条
  python client.py 006888 news --count 15                      # 相关资讯显示15条
  python client.py 006888 compare                              # 自动发现同赛道基金对比
  python client.py 006888 compare --with 022364,018956         # 手动指定对比基金
  python client.py 006888 pe_percentile                        # 持仓股估值百分位（近3年）
  python client.py 006888 pe_percentile --years 5              # 近5年估值百分位

  # 热榜命令（不需要基金代码）
  python client.py hotlist                                      # A股/概念/行业/ETF 热榜
  python client.py hotlist --market hk                          # 港股热榜
  python client.py hotlist --market us --count 20               # 美股热榜前20名
  python client.py hotlist_topics                               # 同花顺社区热门话题
  python client.py hotlist_topics --topic 1                     # 查看第1个话题详情（投票/讨论）
  python client.py hotlist_topics --topic 2 --count 5           # 查看第2个话题，显示5条讨论
  python client.py hotlist_posts                                # 热门文章
  python client.py hotlist_posts --count 5                      # 热门文章前5条
  python client.py news_themes                                  # 新闻主题分类列表（19个热门板块）
  python client.py theme_articles --theme TZ-11385              # 小金属主题文章列表
  python client.py theme_articles --theme TZ-11385 --detail 2   # 小金属第2篇文章全文
  python client.py theme_articles --theme TZ-11907 --count 20   # 人形机器人主题前20篇
  python client.py theme_articles --theme TZ-669 --page 2       # 有色金属主题第2页
  python client.py flash_news                                   # 查看快讯分类列表
  python client.py flash_news --tag 重要                         # 重要快讯
  python client.py flash_news --tag 期货 --count 20              # 期货快讯前20条
  python client.py flash_news --tag 62857 --detail 3             # 第3条重要快讯详情
  python client.py flash_news --tag a股 --seq 674998305          # A股快讯翻页（加载更早的）
  python client.py market_overview                                # A股大盘总览（指数/涨跌/资金/涨跌停）
  python client.py market_overview --count 5                      # 涨跌停列表只显示前5只
  python client.py stock_rank                                     # A股涨幅榜前20
  python client.py stock_rank --sort fall                         # A股跌幅榜
  python client.py stock_rank --sort turnover --count 30          # 成交额榜前30
  python client.py stock_rank --sort turnover_rate                # 换手率榜
  python client.py sector_rank                                    # 概念板块涨跌排行
  python client.py sector_rank --sector-type industry             # 行业板块涨跌排行
  python client.py hot_board                                      # 概念板块今日涨幅 TOP 10
  python client.py hot_board --hot-sort flow                      # 概念板块资金流入 TOP 10
  python client.py hot_board --hot-sort 5day                      # 概念板块5日涨幅 TOP 10
  python client.py hot_board --sector-type industry               # 行业板块今日涨幅 TOP 10
  python client.py hot_board --sector-type industry --hot-sort flow  # 行业板块资金流入
  python client.py dragon_tiger                                     # 龙虎榜个股明细（近3日）
  python client.py dragon_tiger --dt-tab dept                       # 活跃营业部（游资/敢死队）
  python client.py dragon_tiger --dt-tab org                        # 机构买卖明细
  python client.py dragon_tiger --dt-days 7 --count 50              # 近7日龙虎榜前50
  python client.py ths_dragon_tiger                                  # 同花顺游资龙虎榜（一线+知名游资）
  python client.py ths_dragon_tiger --ths-dt-tab jigou               # 机构专用席位
  python client.py ths_dragon_tiger --ths-dt-tab all                 # 全部（含所有标签）
  python client.py capital_flow                                      # 大盘资金净流入（主力/大单/超大单）
  python client.py capital_flow --cf-tab north                       # 北向资金成交额
  python client.py capital_flow --cf-days 30                         # 近30个交易日
  python client.py currency                                         # 美元/离岸人民币汇率走势
  python client.py currency --currency-tab shibor                   # Shibor利率+LPR变化
  python client.py currency --currency-days 60                      # 近60个交易日
  python client.py market_changes                                # 大盘异动（板块异动+竞价异动）
  python client.py market_changes --count 50                     # 显示更多条
  python client.py stock_changes                                 # 全部个股异动
  python client.py stock_changes --change-type 竞价               # 竞价异动
  python client.py stock_changes --change-type 拉升               # 火箭发射+快速反弹
  python client.py stock_changes --change-type 大单               # 大笔买入/卖出+大买盘/卖盘
  python client.py stock_changes --change-type 涨停               # 封涨停+打开涨停
  python client.py stock_changes --change-type 火箭发射            # 单个具体类型
  python client.py stock_changes --change-type 大笔买入 --count 30 # 大笔买入前30条
  python client.py yesterday_limit                               # 昨日涨停今日表现
  python client.py yesterday_limit --count 30                    # 显示前30只
  python client.py headlines                                    # 推荐头条（置顶重要新闻）
  python client.py headlines --detail 1                         # 查看第1条头条详细内容
  python client.py headlines --detail 2                         # 查看第2条头条详细内容
  python client.py news_feed                                    # 滚动快讯（最新20条）
  python client.py news_feed --page 2                           # 滚动快讯第2页

  # 基金排行与筛选（同花顺原生数据）
  python client.py ranking                                       # 默认：近一年涨幅排行 TOP 30
  python client.py ranking --sort-type month                     # 近一月涨幅排行
  python client.py ranking --sort-type sharpeYear                # 夏普比率排行
  python client.py ranking --sort-type maxDrawDownYear --ranking-sort ASC  # 最小回撤排行
  python client.py ranking --board 涨幅榜                        # 使用预设排行榜
  python client.py ranking --board 反弹榜                        # 反弹榜
  python client.py ranking --count 50                            # 显示50只
  python client.py ranking --min-scale 100000000                 # 规模>10亿
  python client.py ranking --count 30 --offset 30               # 第二页（翻页）
  python client.py screen                                        # 列出所有可用筛选策略
  python client.py screen --strategy fund0001                    # 年年正收益
  python client.py screen --strategy fund0002                    # 三年翻倍
  python client.py screen --strategy fund0011                    # 机构偏爱
  python client.py companies                                     # 基金公司列表

当前已覆盖的决策维度（10 大类 35+ 指标）
  维度: 基本面
  覆盖情况: 基金评分、夏普比率、年化收益、最大回撤、阶段排名、收益稳定度
  数据来源: detail/rank/drawdown/stability
  ────────────────────────────────────────
  维度: 估值
  覆盖情况: 持仓股 PE/PB/ROE + 近N年历史百分位
  数据来源: valuation/pe_percentile
  ────────────────────────────────────────
  维度: 持仓
  覆盖情况: 前十大持仓、行业分布、风格偏好、季度持仓变动、盈亏贡献
  数据来源: holdings/style/position/profit
  ────────────────────────────────────────
  维度: 择时
  覆盖情况: RSI(14)、MA5/20/60、均线偏离度、金叉死叉信号
  数据来源: nav_technical/rsi
  ────────────────────────────────────────
  维度: 宏观环境
  覆盖情况:
  沪深300+创业板指趋势、北向资金活跃度、融资融券余额、国债ETF(利率代理)
  数据来源: market
  ────────────────────────────────────────
  维度: 资金流
  覆盖情况: 季度净申赎、机构占比变化趋势
  数据来源: fund_flow/holder_ratio/scale_change
  ────────────────────────────────────────
  维度: 基金经理
  覆盖情况: 从业年限、历史收益、雷达图诊断、行业偏好、代表基金
  数据来源: manager
  ────────────────────────────────────────
  维度: 交易成本
  覆盖情况: 申赎费率、管理费、托管费、确认时间
  数据来源: trade_rule
  ────────────────────────────────────────
  维度: 同类对比
  覆盖情况: 自动发现同赛道基金、多维横向对比
  数据来源: compare/similar
  ────────────────────────────────────────
  维度: 货币风向
  覆盖情况: USD/CNY中间价走势(CFETS)、离岸人民币OHLC(push2his)、Shibor隔夜/1周/1月/3月、LPR 1年/5年
  数据来源: currency
  ────────────────────────────────────────
  维度: 市场热点
  覆盖情况: 推荐头条、滚动快讯、个股热度排名(A/港/美)、概念/行业热榜、ETF热榜、社区话题、热门文章
  数据来源: headlines/news_feed/hotlist/hotlist_topics/hotlist_posts

  如果以 006888（华安媒体互联网A）为例的决策逻辑

  基于 all 命令输出的完整数据，一个职业投资人可以形成如下决策链：

  1. 值不值得买？ — 看 rank（阶段排名）、year_return（年度收益）、drawdown
  （回撤控制）、stability（胜率）→ 判断基金的alpha能力
  2. 现在贵不贵？ — 看 pe_percentile（持仓股估值百分位）→
  如果多数重仓股PE处于历史80%以上，估值偏贵
  3. 什么时候买？ — 看 nav_technical（RSI/均线信号）→
  RSI<30超卖区间考虑建仓，RSI>70超买区间考虑减仓
  4. 大环境支持吗？ — 看 market（沪深300/创业板趋势、融资融券、国债利率）+
   currency（汇率走势、Shibor利率、LPR变化）→
   大盘多头+融资余额上升+利率下行+人民币升值=有利于成长股基金
  5. 聪明钱怎么做？ — 看 fund_flow（净申赎）+ holder_ratio（机构占比）→
  机构在撤退要警惕
  6. 买谁更好？ — 看 compare（同类对比）→ 同赛道是否有更优选择
  7. 市场在追什么？ — 看 headlines（推荐头条）+ hotlist（热度排名）+ news_feed（快讯）→
  判断基金持仓是否在当前市场热点赛道上，重大事件是否影响持仓板块
