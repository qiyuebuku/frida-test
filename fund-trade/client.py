"""同花顺基金 API 客户端 - 调用本地 API 服务获取数据

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

  === 个股查询（股票代码作为 code 参数） ===
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
  --sort-type S  排行排序字段（支持别名）: year(1y/rise1y)/hyear(6m)/tmonth(3m)/month(1m)/week(1w)/sharpeYear(sharpe)/maxDrawDownYear(dd)（适用于 ranking/screen）
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

  # 个股查询（股票代码作为 code 参数）
  python client.py stock_quote 600519                               # 贵州茅台实时行情
  python client.py stock_quote 600519,000001,002594                  # 批量查询行情
  python client.py stock_kline 600519                                # 贵州茅台日K线（近60条）
  python client.py stock_kline 600519 --stock-period week --count 20 # 周K线近20条
  python client.py stock_kline 600519 --stock-period month           # 月K线
  python client.py stock_flow 600519                                 # 贵州茅台资金流向（近20日）
  python client.py stock_flow 600519 --stock-days 60                 # 近60日资金流向
  python client.py stock_valuation 600519                            # 估值历史（近3年PE/PB）
  python client.py stock_valuation 600519 --years 5 --count 50       # 近5年估值，显示50条
  python client.py stock_financial 600519                            # 财务数据（近10期）
  python client.py stock_financial 600519 --count 20                 # 近20期财务数据

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
"""

import argparse
import os
import sys

import httpx

# 清除代理，避免请求本地服务走代理
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

API_BASE = "http://127.0.0.1:8900"


_client = httpx.Client(timeout=60)


def get(path: str) -> dict:
    resp = _client.get(f"{API_BASE}{path}")
    resp.raise_for_status()
    return resp.json()


def post(path: str, json_data: dict) -> dict:
    resp = _client.post(f"{API_BASE}{path}", json=json_data)
    resp.raise_for_status()
    return resp.json()


def fmt_pct(v) -> str:
    if v is None or v == "":
        return "N/A"
    return f"{float(v):+.2f}%"


def cmd_product(args):
    """产品详情"""
    d = get(f"/api/fund/{args.code}/product")
    data = d.get("data", {})
    if not data:
        print("暂无产品详情")
        return
    for key in ["基金名称", "基金类型", "成立时间", "资产规模", "所属公司",
                 "投资理念", "业绩比较基准", "风险收益特征", "分红统计", "拆分详情"]:
        val = data.get(key)
        if val:
            print(f"{key}: {val}")


def cmd_detail(args):
    """基金综合详情"""
    d = get(f"/api/fund/{args.code}")
    info = d.get("data", {})
    print(f"{'基金名称':　>6s}: {info.get('name')}")
    print(f"{'基金代码':　>6s}: {info.get('code')}")
    print(f"{'基金类型':　>6s}: {info.get('secTypeName', info.get('type'))}")
    print(f"{'风险等级':　>6s}: {info.get('levelOfRisk')}")
    print(f"{'最新净值':　>6s}: {info.get('net')}  ({info.get('date')})")
    print(f"{'日涨幅':　>6s}: {fmt_pct(info.get('rate'))}")
    print(f"{'成立以来':　>6s}: {fmt_pct(info.get('now'))}")

    managers = info.get("managerInfo", [])
    if managers:
        m = managers[0]
        print(f"{'基金经理':　>6s}: {m.get('name')}  (从业 {m.get('years')} 年)")

    # 从 /base 端点获取补充指标
    base = get(f"/api/fund/{args.code}/base")
    hc = base.get("data", {}).get("handicap", {})
    if hc:
        scale_raw = hc.get("fundScale")
        if scale_raw:
            scale_yi = float(scale_raw) / 1e8
            print(f"{'基金规模':　>6s}: {scale_yi:.2f} 亿")
        estab = hc.get("establishmentDate")
        if estab:
            print(f"{'成立日期':　>6s}: {estab}")
        diag = hc.get("diagnosticScore")
        if diag:
            print(f"{'诊断评分':　>6s}: {diag} 分")
        sharpe = hc.get("sharpeYear")
        if sharpe:
            print(f"{'夏普比率':　>6s}: {float(sharpe):.4f}")
        maxdd = hc.get("maxDrawDownYear")
        if maxdd:
            print(f"{'最大回撤':　>6s}: -{float(maxdd):.2f}%")
        annual = hc.get("nowAnnual")
        if annual:
            print(f"{'年化收益':　>6s}: {fmt_pct(annual)}")

    # 如果有详细涨幅数据
    for key, label in [("week", "近一周"), ("month", "近一月"), ("tmonth", "近三月"),
                       ("hyear", "近半年"), ("year", "近一年"), ("nowyear", "今年以来")]:
        val = info.get(key)
        if val is not None:
            print(f"{'  ' + label:　>6s}: {fmt_pct(val)}")


def cmd_rank(args):
    """阶段涨幅排名"""
    d = get(f"/api/fund/{args.code}/rank")
    items = d.get("data", [])
    if not items:
        print("暂无排名数据")
        return
    print(f"{'周期':<8s} {'收益率':>10s} {'同类排名':>14s} {'同类均值':>10s}")
    print("-" * 46)
    for item in items:
        y = item.get("yield")
        rank = item.get("rank", "")
        avg = item.get("avgYield")
        period = item.get("time", "")
        avg_str = fmt_pct(avg) if avg is not None else ""
        print(f"{period:<8s} {fmt_pct(y):>10s} {rank:>14s} {avg_str:>10s}")


def cmd_year_return(args):
    """年度收益率"""
    d = get(f"/api/fund/{args.code}/year_return")
    items = d.get("data", [])
    if not items:
        print("暂无年度收益数据")
        return
    print(f"{'年度':<8s} {'收益率':>10s} {'同类排名':>14s} {'同类均值':>10s}")
    print("-" * 46)
    for item in items:
        y = item.get("yield")
        rank = item.get("rank", "")
        avg = item.get("avgYield")
        period = item.get("time", "")
        avg_str = fmt_pct(avg) if avg is not None else ""
        print(f"{period:<8s} {fmt_pct(y):>10s} {rank:>14s} {avg_str:>10s}")


def cmd_drawdown(args):
    """最大回撤"""
    d = get(f"/api/fund/{args.code}/drawdown")
    items = d.get("data", [])
    if not items:
        print("暂无回撤数据")
        return
    print(f"{'周期':<8s} {'最大回撤':>10s} {'同类排名':>14s} {'同类均值':>10s}")
    print("-" * 46)
    for item in items:
        dd = item.get("drawdown")
        rank = item.get("rank", "")
        avg = item.get("avgDrawdown")
        period = item.get("time", "")
        dd_str = f"-{dd:.2f}%" if dd is not None else "N/A"
        avg_str = f"-{avg:.2f}%" if avg is not None else ""
        print(f"{period:<8s} {dd_str:>10s} {rank:>14s} {avg_str:>10s}")


def cmd_stability(args):
    """收益稳定度"""
    groups = [("day", "日收益"), ("week", "周收益"), ("month", "月收益"),
              ("quarter", "季收益"), ("year", "年收益")]
    for group, label in groups:
        d = get(f"/api/fund/{args.code}/periodic_rate?group={group}")
        items = d.get("data", {}).get("periodicRateList", [])
        if not items:
            print(f"{label}: 暂无数据")
            continue
        rates = [float(i.get("rate", 0)) for i in items]
        pos = sum(1 for r in rates if r > 0)
        neg = sum(1 for r in rates if r < 0)
        avg_rate = sum(rates) / len(rates)
        win_rate = pos / len(rates) * 100
        max_rate = max(rates)
        min_rate = min(rates)
        print(f"[{label}]  共{len(items)}期  上涨:{pos}  下跌:{neg}  "
              f"胜率:{win_rate:.1f}%  均值:{avg_rate:+.2f}%  "
              f"最高:{max_rate:+.2f}%  最低:{min_rate:+.2f}%")
        # 显示最近几期明细
        show_n = min(getattr(args, "count", 10) or 10, len(items))
        print(f"  {'日期':<12s} {'本基金':>8s} {'同类均值':>8s}")
        for item in items[:show_n]:
            date = item.get("date", "")
            rate = fmt_pct(item.get("rate"))
            sim = fmt_pct(item.get("similarRate"))
            print(f"  {date:<12s} {rate:>8s} {sim:>8s}")
        if len(items) > show_n:
            print(f"  ... 共 {len(items)} 期（用 --count N 显示更多）")
        print()


def cmd_hold_overview(args):
    """持仓概览"""
    d = get(f"/api/fund/{args.code}/holdings/overview")
    data = d.get("data", {}).get(args.code, {})
    if not data:
        print("暂无持仓概览")
        return
    stock_rate = data.get("fundStockRate")
    main_ind = data.get("fundStockMainIndustry")
    concentration = data.get("fundStockConcentration")
    if stock_rate:
        print(f"股票仓位: {stock_rate}%")
    if main_ind:
        print(f"主要行业: {main_ind}")
    if concentration:
        print(f"持仓集中度: {float(concentration) * 100:.2f}%")


def cmd_profit(args):
    """盈亏贡献"""
    time_type = getattr(args, "time_type", "threeMonth") or "threeMonth"
    label_map = {"threeMonth": "近三月", "halfYear": "近半年", "year": "近一年"}
    d = get(f"/api/fund/{args.code}/profit?time_type={time_type}")
    items = d.get("data", [])
    if not items:
        print("暂无盈亏贡献数据")
        return
    print(f"盈亏贡献（{label_map.get(time_type, time_type)}）\n")
    print(f"{'股票名称':<12s} {'占净值比':>8s} {'贡献率':>8s}")
    print("-" * 32)
    for item in items:
        name = item.get("secName", "")
        nav_rate = item.get("fundNavRate")
        contrib = item.get("contributeRate")
        nav_str = f"{nav_rate:.2f}%" if nav_rate is not None else "N/A"
        contrib_str = fmt_pct(contrib) if contrib is not None else "N/A"
        print(f"{name:<12s} {nav_str:>8s} {contrib_str:>8s}")


def cmd_style(args):
    """投资风格偏好"""
    d = get(f"/api/fund/{args.code}/style")
    items = d.get("data", {}).get("rateList", [])
    if not items:
        print("暂无风格偏好数据")
        return
    labels = [("kjRate", "科技"), ("zqRate", "周期"), ("xfRate", "消费"),
              ("zzRate", "制造"), ("jrRate", "金融"), ("ylRate", "医疗"), ("jjRate", "军工")]
    header_parts = [f"{'季度':<8s}"]
    for _, label in labels:
        header_parts.append(f"{label:>6s}")
    print("".join(header_parts))
    print("-" * (8 + 6 * len(labels)))
    for item in items:
        row = [f"{item.get('time', ''):<8s}"]
        for key, _ in labels:
            v = item.get(key, 0)
            row.append(f"{v * 100:>5.1f}%" if v else f"{'--':>6s}")
        print("".join(row))


def cmd_asset(args):
    """资产配置"""
    d = get(f"/api/fund/{args.code}/asset_allocation")
    items = d.get("data", {}).get("rateList", [])
    if not items:
        print("暂无资产配置数据")
        return
    print(f"{'季度':<8s} {'股票':>8s} {'债券':>8s} {'存款':>8s} {'其他':>8s}")
    print("-" * 40)
    for item in items:
        time = item.get("time", "")
        stock = item.get("stockRatio", 0)
        bond = item.get("bondRatio", 0)
        dpst = item.get("dpstRatio", 0)
        other = item.get("otherRatio", 0)
        print(f"{time:<8s} {stock:>7.2f}% {bond:>7.2f}% {dpst:>7.2f}% {other:>7.2f}%")


def cmd_holdings(args, with_valuation=True):
    """前十大持仓（含估值）"""
    d = get(f"/api/fund/{args.code}/holdings")
    data = d.get("data", {})
    stocks = data.get("stock", [])
    total_rate = data.get("totalStockRate")
    if total_rate:
        print(f"股票仓位合计: {total_rate}%\n")

    if not stocks:
        print("暂无持仓数据")
        return

    # 获取估值数据并建立索引
    val_map = {}
    if with_valuation:
        try:
            vd = get(f"/api/fund/{args.code}/holdings/valuation")
            for item in vd.get("data", []):
                val_map[item.get("secCode", "")] = item
        except Exception:
            pass

    if val_map:
        print(f"{'序号':<4s} {'代码':<8s} {'名称':<10s} {'占比':>6s} {'今日':>7s} {'最新价':>8s} {'PE(动)':>8s} {'PE(TTM)':>8s} {'PB':>6s} {'市值(亿)':>8s}")
        print("-" * 82)
        weighted_sum = 0.0
        covered_rate = 0.0
        for i, s in enumerate(stocks, 1):
            code = s.get("secCode", "")
            name = s.get("secName", "")
            nav_rate = s.get("fundNavRate")
            nav_str = f"{nav_rate:.2f}%" if nav_rate is not None else "N/A"
            v = val_map.get(code, {})
            if v and not v.get("error"):
                price = v.get("price")
                change = v.get("changeRate")
                pe = v.get("pe")
                pe_ttm = v.get("peTTM")
                pb = v.get("pb")
                mc = v.get("marketCap")
                change_s = f"{change:+.2f}%" if change is not None else "-"
                price_s = f"{price:.2f}" if price is not None else "-"
                pe_s = f"{pe:.2f}" if pe is not None else "-"
                pettm_s = f"{pe_ttm:.2f}" if pe_ttm is not None else "-"
                pb_s = f"{pb:.2f}" if pb is not None else "-"
                mc_s = f"{mc:.0f}" if mc is not None else "-"
                if change is not None and nav_rate is not None:
                    weighted_sum += change * nav_rate / 100
                    covered_rate += nav_rate
            else:
                change_s = price_s = pe_s = pettm_s = pb_s = mc_s = "-"
            print(f"{i:<4d} {code:<8s} {name:<10s} {nav_str:>6s} {change_s:>7s} {price_s:>8s} {pe_s:>8s} {pettm_s:>8s} {pb_s:>6s} {mc_s:>8s}")
        print("-" * 82)
        print(f"预估今日涨幅: {weighted_sum:+.4f}%  (基于前十大持仓加权，覆盖仓位 {covered_rate:.2f}%)")
    else:
        print(f"{'序号':<4s} {'股票代码':<10s} {'股票名称':<12s} {'占净值比':>8s} {'期间涨幅':>8s}")
        print("-" * 50)
        for i, s in enumerate(stocks, 1):
            code = s.get("secCode", "")
            name = s.get("secName", "")
            nav_rate = s.get("fundNavRate")
            period_rate = s.get("periodIncrRate")
            nav_str = f"{nav_rate:.2f}%" if nav_rate is not None else "N/A"
            period_str = fmt_pct(period_rate) if period_rate is not None else ""
            print(f"{i:<4d} {code:<10s} {name:<12s} {nav_str:>8s} {period_str:>8s}")


def cmd_nav(args):
    """历史净值"""
    period = getattr(args, "period", "year") or "year"
    count = getattr(args, "count", 10) or 10
    d = get(f"/api/fund/{args.code}/nav?period={period}")
    raw = d.get("data", "")
    if not raw:
        print("暂无净值数据")
        return

    lines = raw.split("|")
    show = min(count, len(lines))
    print(f"历史净值 ({period})  共 {len(lines)} 条，显示最近 {show} 条\n")
    print(f"{'日期':<12s} {'净值':>8s} {'涨跌幅':>8s}")
    print("-" * 32)
    for line in lines[:show]:
        parts = line.split(";")
        if len(parts) >= 4:
            date = parts[0]
            net = parts[2]
            rate = parts[3]
            print(f"{date:<12s} {net:>8s} {fmt_pct(rate):>8s}")
    if len(lines) > show:
        print(f"... 共 {len(lines)} 条（用 --count N 显示更多）")


def cmd_realtime(args):
    """实时估值"""
    d = get(f"/api/fund/{args.code}/realtime")
    data = d.get("data", {})
    status = data.get("dataStatus")
    show_time = data.get("showTime", "")
    trend = data.get("holderTrendList", [])

    if not trend:
        print(f"暂无实时数据 (status={status}, date={show_time})")
        return

    print(f"实时估值 {show_time}  共 {len(trend)} 个数据点\n")
    print(f"{'时间':<8s} {'估值涨幅':>10s}")
    print("-" * 20)
    # 显示最后 10 个点
    for item in trend[-10:]:
        print(f"{item['time']:<8s} {fmt_pct(item['value']):>10s}")


def cmd_manager(args):
    """基金经理"""
    # 先获取基金详情以拿到经理 ID
    d = get(f"/api/fund/{args.code}")
    info = d.get("data", {})
    managers = info.get("managerInfo", [])

    if not managers:
        print("暂无基金经理信息")
        return

    for m in managers:
        mid = m.get("id", "")
        print(f"基金经理: {m.get('name')}")
        print(f"经理 ID : {mid}")
        print(f"从业年限: {m.get('years')} 年")

        if not mid:
            print()
            continue

        # 完整档案
        try:
            profile = get(f"/api/manager/{mid}/profile")
            sd = profile.get("singleData", {})
            p = sd.get("profile", {})
            mi = sd.get("managerInfo", {})
            if p.get("personalResume"):
                resume = p["personalResume"]
                if len(resume) > 200:
                    resume = resume[:200] + "..."
                print(f"个人简历: {resume}")
            if p.get("hdegree"):
                print(f"学    历: {p['hdegree']}")
            if p.get("companyName"):
                print(f"所属公司: {p['companyName']}")
            if mi.get("totalFundScale"):
                scale_yi = mi["totalFundScale"] / 1e8
                print(f"管理规模: {scale_yi:.2f} 亿")
            if mi.get("fundNumber"):
                print(f"管理基金: {mi['fundNumber']} 只")
            if mi.get("annualReturn") is not None:
                print(f"年化收益: {fmt_pct(mi['annualReturn'] * 100)}")
            if mi.get("maxReturn") is not None:
                print(f"最大回撤: {mi['maxReturn'] * 100:+.2f}%")
        except Exception:
            pass

        # 诊断评分
        try:
            diag = get(f"/api/manager/{mid}/diagnose")
            dd = diag.get("data", {})
            interval = dd.get("intervalInfo", {})
            annual = interval.get("annualRate", {})
            drawdown = interval.get("rangeDrawdown", {})
            if annual:
                parts = []
                for k, label in [("YEAR", "近一年"), ("THREE_YEAR", "近三年"), ("START_BUILD", "成立以来")]:
                    v = annual.get(k)
                    if v:
                        parts.append(f"{label}:{fmt_pct(v)}")
                if parts:
                    print(f"年化收益: {', '.join(parts)}")
            if drawdown:
                parts = []
                for k, label in [("YEAR", "近一年"), ("THREE_YEAR", "近三年"), ("START_BUILD", "成立以来")]:
                    v = drawdown.get(k)
                    if v:
                        parts.append(f"{label}:{float(v):+.2f}%")
                if parts:
                    print(f"最大回撤: {', '.join(parts)}")
            # 历史年度收益
            hist_rate = interval.get("historyRate", {})
            if hist_rate:
                yr_strs = [f"{y}:{fmt_pct(r)}" for y, r in sorted(hist_rate.items())]
                print(f"历年收益: {', '.join(yr_strs)}")
        except Exception:
            pass

        # 行业偏好
        try:
            ind = get(f"/api/manager/{mid}/industry_prefer")
            ind_list = ind.get("data", {}).get("list", [])
            if ind_list:
                ind_labels = ["金融", "周期", "消费", "医疗", "科技", "制造", "军工"]
                print(f"\n  行业偏好:")
                header = f"  {'季度':<10s}" + "".join(f"{l:>6s}" for l in ind_labels)
                print(header)
                print(f"  {'-' * (10 + 6 * len(ind_labels))}")
                for item in ind_list:
                    tag = item.get("reportTag", "")
                    pcts = item.get("percent", [])
                    row = f"  {tag:<10s}"
                    for p in pcts:
                        row += f"{p * 100:>5.1f}%" if p else f"{'--':>6s}"
                    print(row)
        except Exception:
            pass

        # 管理的基金
        try:
            hist = get(f"/api/manager/{mid}/invest_history")
            inv = hist.get("singleData", {}).get("investHistory", {})
            if inv:
                print(f"\n  管理基金列表:")
                print(f"  {'代码':<10s} {'名称':<16s} {'任职时间':<24s} {'任职收益':>8s} {'年化收益':>8s}")
                print(f"  {'-' * 70}")
                for code, f in inv.items():
                    name = f.get("name", "")
                    period = f"{f.get('start', '')} ~ {f.get('end', '')}"
                    sy = f.get("syInfo", {})
                    ret = fmt_pct(sy.get("sy")) if sy.get("sy") else "N/A"
                    avg = fmt_pct(sy.get("avgsy")) if sy.get("avgsy") else "N/A"
                    print(f"  {code:<10s} {name:<16s} {period:<24s} {ret:>8s} {avg:>8s}")
        except Exception:
            pass

        print()


def cmd_rsi(args):
    """RSI 指标"""
    d = get(f"/api/fund/{args.code}/rsi")
    data = d.get("data", {}).get(args.code, {})
    up = data.get("rsiBestLimitUp", "N/A")
    down = data.get("rsiBestLimitDown", "N/A")
    print(f"基金 {args.code} RSI 指标:")
    print(f"  买入区间上限: {up}")
    print(f"  买入区间下限: {down}")


ANNOUNCEMENT_CAT_LABELS = {
    "all": "全部", "report": "业绩", "dividend": "分红",
    "change": "变更", "operation": "运作", "other": "其他",
}


def cmd_announcements(args):
    """基金公告"""
    cat = getattr(args, "cat", "all") or "all"
    page = getattr(args, "page", 1) or 1
    count = getattr(args, "count", 15) or 15
    label = ANNOUNCEMENT_CAT_LABELS.get(cat, cat)
    d = get(f"/api/fund/{args.code}/announcements?category={cat}&page={page}&page_size={count}")
    items = d.get("data", {}).get("info", [])
    total = d.get("data", {}).get("total", "?")
    if not items:
        print(f"暂无{label}公告")
        return
    print(f"{label}公告（共 {total} 条，第 {page} 页，每页 {count} 条）\n")
    for item in items:
        title = item.get("title", "")
        pubtime = item.get("pubtime", "")
        type_name = item.get("showTypeName", "")
        pdf_url = item.get("rawURL", "")
        print(f"  [{pubtime}] [{type_name}] {title}")
        if pdf_url:
            print(f"          PDF: {pdf_url}")


def cmd_news(args):
    """基金资讯"""
    count = getattr(args, "count", 10) or 10
    d = get(f"/api/fund/{args.code}/news?limit={count}")
    items = d.get("data", {}).get("contentList", [])
    if not items:
        print("暂无资讯")
        return
    from datetime import datetime
    print(f"相关资讯（{len(items)} 条）\n")
    for item in items:
        title = item.get("title", "")
        ts = item.get("time", "")
        source = item.get("source", "")
        try:
            dt = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
        except Exception:
            dt = ts
        url = item.get("url", "")
        print(f"  [{dt}] {title}")
        print(f"          来源: {source}  {url}")


def cmd_trade_rule(args):
    """交易规则与费率"""
    d = get(f"/api/fund/{args.code}/trade_rule")
    data = d.get("data", {})
    if not data:
        print("暂无交易规则数据")
        return

    rate_info = data.get("rateInfo", {})
    trade_rule = data.get("tradeRule", {})
    fund_info = data.get("fundinfo", {})

    if fund_info:
        print(f"基金类型: {fund_info.get('type', '')}")
        zdsg = fund_info.get("zdsg")
        if zdsg:
            print(f"最低申购: {zdsg}元")

    # ---- 买入规则 ----
    print("\n--- 买入规则 ---")
    buy_rules = trade_rule.get("tradeRuleBuy", [])
    if buy_rules:
        steps = " → ".join(f"{r['title']}({r['showtime']})" for r in buy_rules)
        print(f"买入流程: {steps}")

    sg = rate_info.get("sg", {})
    sg_qd = sg.get("qd", [])
    sg_hd = sg.get("hd", [])
    if sg_qd:
        print("申购费率（前端收费）:")
        for item in sg_qd:
            money = item.get("money", "")
            rate = item.get("rate", "")
            irate = item.get("irate", "")
            label = f"  {money}" if money and money != "--" else "  标准"
            if irate and irate != rate:
                print(f"{label}: {rate} → 折后 {irate}")
            else:
                print(f"{label}: {rate}")
    if sg_hd:
        print("申购费率（后端收费）:")
        for item in sg_hd:
            time = item.get("time", "")
            rate = item.get("rate", "")
            print(f"  {time}: {rate}")

    # 定投费率
    dt = rate_info.get("dt", {})
    dt_qd = dt.get("qd", [])
    if dt_qd:
        dt_discount = rate_info.get("dtDiscount", "")
        discount_str = f"（折扣 {float(dt_discount) * 100:.0f}%）" if dt_discount and dt_discount != "1.0000" else ""
        print(f"定投费率{discount_str}:")
        for item in dt_qd:
            money = item.get("money", "")
            rate = item.get("rate", "")
            irate = item.get("irate", "")
            label = f"  {money}" if money and money != "--" else "  标准"
            if irate and irate != rate:
                print(f"{label}: {rate} → 折后 {irate}")
            else:
                print(f"{label}: {rate}")

    discount = rate_info.get("discount")
    if discount and discount != "1.0":
        print(f"申购折扣: {float(discount) * 100:.0f}%")

    # ---- 卖出规则 ----
    print("\n--- 卖出规则 ---")
    sale_rules = trade_rule.get("tradeRuleSale", [])
    if sale_rules:
        steps = " → ".join(f"{r['title']}({r['showtime']})" for r in sale_rules)
        print(f"卖出流程: {steps}")

    sh = rate_info.get("sh", [])
    if sh:
        print("赎回费率:")
        for item in sh:
            time = item.get("time", "")
            rate = item.get("rate", "")
            print(f"  {time}: {rate}")

    # ---- 运作费用 ----
    print("\n--- 运作费用 ---")
    for key, label in [("glf", "管理费"), ("tgf", "托管费"), ("fwf", "销售服务费")]:
        val = rate_info.get(key)
        if val:
            print(f"{label}: {val}/年")


def cmd_scale_change(args):
    """规模变动"""
    d = get(f"/api/fund/{args.code}/scale_change")
    data = d.get("data", {}).get("gmbd", {})
    if not data:
        print("暂无规模变动数据")
        return

    dates = sorted(data.keys(), reverse=True)
    count = getattr(args, "count", 8) or 8
    show = min(count, len(dates))
    print(f"规模变动（共 {len(dates)} 个季度，显示最近 {show} 个）\n")
    print(f"{'季度':<12s} {'净资产(亿)':>10s} {'净资产变动':>10s} {'期间申购(亿)':>12s} {'期间赎回(亿)':>12s} {'总份额(亿)':>10s} {'份额变动':>10s}")
    print("-" * 82)
    for date in dates[:show]:
        info = data[date]
        jzc = float(info.get("jzc", 0))
        jzcbdl = info.get("jzcbdl", "")
        qjsg = float(info.get("qjsg", 0))
        qjsh = float(info.get("qjsh", 0))
        qmzfe = float(info.get("qmzfe", 0))
        zfebdl = info.get("zfebdl", "")
        jzc_str = f"{jzc:.2f}"
        jzcbdl_str = f"{float(jzcbdl):+.2f}%" if jzcbdl else "N/A"
        zfebdl_str = f"{float(zfebdl):+.2f}%" if zfebdl else "N/A"
        print(f"{date:<12s} {jzc_str:>10s} {jzcbdl_str:>10s} {qjsg:>12.2f} {qjsh:>12.2f} {qmzfe:>10.2f} {zfebdl_str:>10s}")


def cmd_holder_ratio(args):
    """机构持仓比例"""
    d = get(f"/api/fund/{args.code}/holder_ratio")
    items = d.get("data", [])
    if not items:
        print("暂无机构持仓数据")
        return
    print(f"机构持仓比例历史（共 {len(items)} 期）\n")
    print(f"{'日期':<12s} {'机构占比':>10s}")
    print("-" * 24)
    for item in items:
        date = item.get("date", "")
        org_rate = item.get("orgRate", "")
        rate_str = f"{float(org_rate):.2f}%" if org_rate else "N/A"
        print(f"{date:<12s} {rate_str:>10s}")


def cmd_dividend(args):
    """分红历史"""
    d = get(f"/api/fund/{args.code}/dividend")
    data = d.get("data", {})
    summary = data.get("summary", "")
    dividends = data.get("dividends", [])
    splits = data.get("splits", [])

    if summary:
        print(f"分红摘要: {summary}")

    if dividends:
        print(f"\n分红明细（共 {len(dividends)} 次）\n")
        print(f"{'红利发放日':<14s} {'权益登记日':<14s} {'每份分红':>10s}")
        print("-" * 40)
        count = getattr(args, "count", 10) or 10
        for item in dividends[:count]:
            print(f"{item['payDate']:<14s} {item['recordDate']:<14s} {item['perShare']:>10s}")
        if len(dividends) > count:
            print(f"... 共 {len(dividends)} 次（用 --count N 显示更多）")
    elif not summary or summary == "无分红记录":
        print("无分红记录")

    if splits:
        print(f"\n拆分记录（共 {len(splits)} 次）")
        for s in splits:
            print(f"  {s['date']}: {s['detail']}")


def cmd_valuation(args):
    """持仓股估值（等同于 holdings，含估值数据）"""
    cmd_holdings(args, with_valuation=True)


def cmd_position_change(args):
    """持仓变动追踪（对比相邻两个季度的前十大持仓变化）"""
    # 从 position_rank 获取可用季度日期列表
    d = get(f"/api/fund/{args.code}/position/dates")
    past_year = d.get("data", {}).get("pastYearRate", {})
    dates = sorted(past_year.keys(), reverse=True)
    if len(dates) < 2:
        print("持仓回顾数据不足两期，无法对比")
        return

    # 取最近两个季度
    cur_date = dates[0]
    prev_date = dates[1]

    # 从 position_detail 获取详细持仓（含 thanPrevious 和 rate）
    cur = get(f"/api/fund/{args.code}/position/detail?end_date={cur_date}")
    cur_stocks = cur.get("data", {}).get("heavyStock", [])

    if not cur_stocks:
        print("暂无持仓明细数据")
        return

    # 上期持仓从 position_rank 的 pastYearRate 获取（不含 thanPrevious，但有 sec_code）
    prev_stocks = past_year.get(prev_date, {}).get("heavyStock", [])

    fmt_d = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d

    print(f"持仓变动: {fmt_d(prev_date)} → {fmt_d(cur_date)}\n")

    # 建立映射
    prev_map = {s["sec_code"]: s for s in prev_stocks}
    cur_codes = {s.get("sec_code", "") for s in cur_stocks}
    prev_codes = set(prev_map.keys())

    new_codes = cur_codes - prev_codes
    exit_codes = prev_codes - cur_codes

    # 当期持仓表
    print(f"{'序号':<4s} {'代码':<8s} {'名称':<10s} {'行业':<6s} {'占比':>6s} {'变动':>8s} {'期间涨幅':>8s} {'变化'}")
    print("-" * 72)
    for i, s in enumerate(cur_stocks, 1):
        code = s.get("sec_code", "")
        name = s.get("sec_name", "")
        industry = s.get("industry", "")
        ratio = s.get("marketValueRatio", "")
        than_prev = s.get("thanPrevious", "")
        rate = s.get("rate", "")
        ratio_s = f"{ratio}%" if ratio else "-"
        than_s = f"{float(than_prev):+.2f}%" if than_prev else "-"
        rate_s = f"{float(rate):+.2f}%" if rate else "-"

        if code in new_codes:
            change = "★ 新进"
        elif than_prev:
            tp = float(than_prev)
            if tp > 0:
                change = "↑ 加仓"
            elif tp < 0:
                change = "↓ 减仓"
            else:
                change = "— 不变"
        else:
            change = ""
        print(f"{i:<4d} {code:<8s} {name:<10s} {industry:<6s} {ratio_s:>6s} {than_s:>8s} {rate_s:>8s} {change}")

    # 退出的股票
    if exit_codes:
        print(f"\n退出持仓:")
        for code in exit_codes:
            s = prev_map[code]
            name = s.get("sec_name", "")
            ratio = s.get("marketValueRatio", "")
            print(f"  ✗ {code} {name}（上期占比 {ratio}%）")

    # 可用季度列表
    print(f"\n可用季度: {', '.join(fmt_d(d) for d in dates[:8])}" +
          (f" ... 共 {len(dates)} 期" if len(dates) > 8 else ""))


def cmd_pe_percentile(args):
    """持仓股估值百分位"""
    years = getattr(args, "years", 3) or 3
    d = get(f"/api/fund/{args.code}/holdings/pe_percentile?years={years}")
    data = d.get("data", {})
    stocks = data.get("stocks", [])
    summary = data.get("summary", {})

    if not stocks:
        print("暂无持仓数据")
        return

    print(f"持仓股估值百分位（近{years}年）\n")
    print(f"{'序号':<4s} {'代码':<8s} {'名称':<10s} {'占比':>6s} {'PE_TTM':>8s} {f'{years}年PE分位':>10s} {'PB':>8s} {f'{years}年PB分位':>10s} {'评级':<4s}")
    print("-" * 78)
    for i, s in enumerate(stocks, 1):
        code = s.get("secCode", "")
        name = s.get("secName", "")
        nav_rate = s.get("fundNavRate")
        pe_ttm = s.get("peTTM")
        pe_pct = s.get("pePct")
        pb = s.get("pb")
        pb_pct = s.get("pbPct")
        rating = s.get("rating", "N/A")

        nav_str = f"{nav_rate:.2f}%" if nav_rate is not None else "N/A"
        pe_str = f"{pe_ttm:.2f}" if pe_ttm is not None else "-"
        pe_pct_str = f"{pe_pct:.1f}%" if pe_pct is not None else "-"
        pb_str = f"{pb:.2f}" if pb is not None else "-"
        pb_pct_str = f"{pb_pct:.1f}%" if pb_pct is not None else "-"

        print(f"{i:<4d} {code:<8s} {name:<10s} {nav_str:>6s} {pe_str:>8s} {pe_pct_str:>10s} {pb_str:>8s} {pb_pct_str:>10s} {rating:<4s}")

    print("-" * 78)

    w_pe = summary.get("weightedPePct")
    w_pb = summary.get("weightedPbPct")
    w_rating = summary.get("rating", "N/A")
    covered = summary.get("coveredWeight")

    parts = []
    if w_pe is not None:
        parts.append(f"加权PE百分位: {w_pe:.1f}%")
    if w_pb is not None:
        parts.append(f"加权PB百分位: {w_pb:.1f}%")
    parts.append(f"综合评级: {w_rating}")
    if covered is not None:
        parts.append(f"(覆盖仓位 {covered:.2f}%)")
    print("  ".join(parts))


def cmd_nav_technical(args):
    """净值技术面分析"""
    d = get(f"/api/fund/{args.code}/nav_technical")
    data = d.get("data", {})
    if not data:
        print(d.get("msg", "暂无技术面数据"))
        return

    nav = data.get("nav")
    date = data.get("date", "")
    rsi = data.get("rsi14")
    print(f"基金 {args.code} 技术面分析\n")
    print(f"最新净值: {nav}  ({date})")

    # RSI 状态文字
    if rsi is not None:
        if rsi > 70:
            rsi_label = "超买"
        elif rsi < 30:
            rsi_label = "超卖"
        else:
            rsi_label = "适中"
        print(f"RSI(14) : {rsi}  [{rsi_label}]")

    print(f"\n{'':>8s} {'均线值':>10s} {'偏离度':>10s}")
    for name, ma_key, dev_key in [("MA5", "ma5", "devMa5"), ("MA20", "ma20", "devMa20"), ("MA60", "ma60", "devMa60")]:
        ma = data.get(ma_key)
        dev = data.get(dev_key)
        ma_s = f"{ma:.4f}" if ma is not None else "-"
        dev_s = f"{dev:+.2f}%" if dev is not None else "-"
        print(f"{name:<8s} {ma_s:>10s} {dev_s:>10s}")

    signals = data.get("signals", [])
    if signals:
        print(f"\n信号:")
        for s in signals:
            print(f"  ● {s}")


def cmd_market_changes(args):
    """大盘异动 / 竞价异动（同花顺数据）"""
    from datetime import datetime

    count = getattr(args, "count", 30)
    seq = getattr(args, "seq", 0)
    d = get(f"/api/flash_news/list?tag_id=21111&seq={seq}")
    items = d.get("data", {}).get("list", [])

    if not items:
        print("暂无异动数据")
        return

    # 按时间分组：竞价时段(09:15-09:25)和盘中
    auction_items = []
    intraday_items = []
    for item in items[:count]:
        ts = item.get("createTime", 0)
        dt = datetime.fromtimestamp(ts) if ts else None
        if dt:
            time_str = dt.strftime("%H:%M")
            if "09:15" <= time_str <= "09:25":
                auction_items.append((dt, item))
            else:
                intraday_items.append((dt, item))
        else:
            intraday_items.append((None, item))

    def _print_item(dt, item, idx):
        title = item.get("title", "")
        time_s = dt.strftime("%H:%M") if dt else "--:--"

        # 提取关联板块
        fields = item.get("fields", [])
        field_names = [f.get("name", "") for f in fields if f.get("name")]
        field_s = f"  [{', '.join(field_names)}]" if field_names else ""

        # 提取关联个股
        stocks = item.get("stocks", [])
        stock_parts = []
        for s in stocks[:5]:
            stock_parts.append(s.get("name", ""))

        print(f"  {idx:>2}. [{time_s}] {title}{field_s}")
        if stock_parts:
            print(f"      相关: {', '.join(stock_parts)}")

    print(f"大盘异动  (共 {len(items)} 条)\n")

    if auction_items:
        print("【竞价异动】")
        for i, (dt, item) in enumerate(auction_items, 1):
            _print_item(dt, item, i)
        print()

    if intraday_items:
        print("【盘中异动】")
        for i, (dt, item) in enumerate(intraday_items, 1):
            _print_item(dt, item, i)

    # 翻页提示
    if items:
        last_seq = items[-1].get("seq", 0)
        if last_seq:
            print(f"\n  提示: 使用 --seq {last_seq} 查看更早的异动")


def cmd_stock_changes(args):
    """盘中异动"""
    change_type = getattr(args, "change_type", "all")
    count = getattr(args, "count", 50)

    import urllib.parse
    encoded_type = urllib.parse.quote(change_type)
    d = get(f"/api/market/stock_changes?change_type={encoded_type}&count={count}")
    data = d.get("data", {})
    changes = data.get("changes", [])
    total = data.get("total", 0)

    if not changes:
        msg = data.get("msg", "暂无数据")
        print(f"盘中异动: {msg}")
        types = data.get("availableTypes", [])
        if types:
            print(f"\n可用类型: {', '.join(types)}")
        return

    filter_name = data.get("typeFilter", "all")
    filter_label = f"[{filter_name}]" if filter_name != "all" else "[全部]"
    print(f"盘中异动 {filter_label}  (共 {total} 条，显示 {len(changes)} 条)\n")

    print(f"{'时间':<10}  {'类型':<10}  {'名称':<6}  {'代码':<8}  {'现价':>8}  {'涨跌幅':>8}  {'成交额(万)':>10}")
    print("-" * 80)
    for c in changes:
        price_s = f"{c['price']:.2f}" if c.get("price") else "-"
        chg_s = f"{c['changeRate']:+.2f}%" if c.get("changeRate") is not None else "-"
        amt_s = f"{c['amount']:.0f}" if c.get("amount") else "-"
        print(f"{c.get('time',''):<10}  {c['typeName']:<10}  {c['name']:<6}  {c['code']:<8}  {price_s:>8}  {chg_s:>8}  {amt_s:>10}")


def cmd_yesterday_limit(args):
    """昨日涨停今日表现"""
    d = get("/api/market/yesterday_limit")
    data = d.get("data", {})
    stocks = data.get("stocks", [])
    stats = data.get("stats", {})

    if not stocks:
        msg = data.get("msg", "暂无数据")
        print(f"昨日涨停今日表现: {msg}")
        return

    qdate = data.get("date", "")
    date_s = f"{qdate[:4]}-{qdate[4:6]}-{qdate[6:]}" if len(qdate) == 8 else qdate
    total = stats.get("total", 0)
    rise = stats.get("riseCount", 0)
    fall = stats.get("fallCount", 0)
    flat = stats.get("flatCount", 0)
    avg = stats.get("avgChangeRate", 0)
    board_dist = stats.get("boardDistribution", {})

    print(f"昨日涨停今日表现  数据日期: {date_s}  (共 {total} 只)\n")

    # 汇总统计
    print(f"  上涨: {rise} 家  |  下跌: {fall} 家  |  平盘: {flat} 家  |  平均涨跌幅: {avg:+.2f}%")
    if board_dist:
        dist_parts = []
        for bc in sorted(board_dist.keys(), key=lambda x: int(x)):
            label = f"{bc}连板" if int(bc) > 1 else "首板"
            dist_parts.append(f"{label}:{board_dist[bc]}只")
        print(f"  连板分布: {' | '.join(dist_parts)}")

    # 涨跌比例条
    if rise + fall > 0:
        bar_len = 40
        rise_bar = round(bar_len * rise / max(rise + fall, 1))
        fall_bar = bar_len - rise_bar
        print(f"  涨跌比: {'█' * rise_bar}{'░' * fall_bar}  {rise}:{fall}")
    print()

    # 股票列表
    count = getattr(args, "count", 20)
    print(f"{'序号':>4}  {'名称':<6}  {'代码':<8}  {'现价':>8}  {'涨跌幅':>8}  {'振幅':>6}  {'换手率':>6}  {'连板':>4}  {'封板时间':>8}  {'行业':<10}  {'成交额(亿)':>10}")
    print("-" * 110)
    for i, s in enumerate(stocks[:count], 1):
        bc = s.get("yesterdayBoardCount", 0)
        bc_s = f"{bc}板" if bc > 1 else "首板"
        fbt = s.get("yesterdayBoardTime", "")
        print(f"{i:>4}  {s['name']:<6}  {s['code']:<8}  {s['price']:>8.2f}  {s['changeRate']:>+7.2f}%  {s.get('amplitude',0):>5.1f}%  {s.get('turnoverRate',0):>5.1f}%  {bc_s:>4}  {fbt:>8}  {s.get('industry',''):<10}  {s.get('turnover',0):>10.2f}")


def cmd_stock_rank(args):
    """个股涨跌幅排行"""
    sort = getattr(args, "sort", "rise")
    count = getattr(args, "count", 20)
    d = get(f"/api/market/stock_ranking?sort={sort}&count={count}")
    data = d.get("data", {})

    sort_names = {
        "rise": "涨幅榜", "fall": "跌幅榜", "volume": "成交量榜",
        "turnover": "成交额榜", "amplitude": "振幅榜", "turnover_rate": "换手率榜",
    }
    title = sort_names.get(data.get("sort", sort), "涨幅榜")
    stocks = data.get("stocks", [])

    print(f"A 股 {title}  (共 {len(stocks)} 只)\n")
    print(f"{'序号':>4}  {'名称':<6}  {'代码':<8}  {'现价':>8}  {'涨跌幅':>8}  {'涨跌额':>8}  {'成交额(亿)':>10}  {'换手率':>6}  {'PE':>8}  {'PB':>6}")
    print("-" * 95)
    for i, s in enumerate(stocks, 1):
        close = s.get("close", 0)
        chg_r = s.get("changeRate", 0)
        chg_a = s.get("changeAmt", 0)
        turnover = (s.get("turnover") or 0) / 1e8 if s.get("turnover") else 0
        tr = s.get("turnoverRate", 0) or 0
        pe = s.get("pe")
        pb = s.get("pb")
        pe_s = f"{pe:.1f}" if pe else "-"
        pb_s = f"{pb:.2f}" if pb else "-"
        print(f"{i:>4}  {s['name']:<6}  {s['code']:<8}  {close:>8.2f}  {chg_r:>+7.2f}%  {chg_a:>+8.2f}  {turnover:>10.2f}  {tr:>5.1f}%  {pe_s:>8}  {pb_s:>6}")


def cmd_sector_rank(args):
    """板块涨跌排行"""
    sector_type = getattr(args, "sector_type", "concept")
    count = getattr(args, "count", 20)
    d = get(f"/api/market/sector_ranking?sector_type={sector_type}&count={count}")
    data = d.get("data", {})

    type_name = "概念板块" if data.get("type") == "concept" else "行业板块"
    total = data.get("total", 0)
    top_rise = data.get("topRise", [])
    top_fall = data.get("topFall", [])

    print(f"{type_name}涨跌排行  (共 {total} 个板块)\n")

    def _print_sectors(title, sectors):
        print(f"【{title}】")
        print(f"{'序号':>4}  {'板块名称':<12}  {'涨跌幅':>8}  {'成分股':>6}  {'成交额(亿)':>10}  {'领涨股':<8}  {'涨幅':>7}")
        print("-" * 80)
        for i, s in enumerate(sectors, 1):
            chg = s.get("changeRate", 0)
            sc = s.get("stockCount", 0)
            turnover = s.get("turnover", 0) / 1e8 if s.get("turnover") else 0
            lead = s.get("leadStock", {})
            lead_name = lead.get("name", "")
            lead_chg = lead.get("changeRate", 0)
            print(f"{i:>4}  {s['name']:<12}  {chg:>+7.2f}%  {sc:>6}  {turnover:>10.2f}  {lead_name:<8}  {lead_chg:>+6.2f}%")
        print()

    if top_rise:
        _print_sectors(f"涨幅前{len(top_rise)}", top_rise)
    if top_fall:
        _print_sectors(f"跌幅前{len(top_fall)}", top_fall)


def cmd_hot_board(args):
    """热点板块排行"""
    board_type = getattr(args, "sector_type", "concept")
    sort = getattr(args, "hot_sort", "rise")
    count = getattr(args, "count", 10)
    d = get(f"/api/market/hot_board?board_type={board_type}&sort={sort}&count={count}")
    data = d.get("data", {})

    type_name = "概念板块" if data.get("boardType") == "concept" else "行业板块"
    sort_name = data.get("sortName", "今日涨幅最大")
    total = data.get("total", 0)
    boards = data.get("boards", [])

    print(f"热点板块  {type_name} · {sort_name}  (共 {total} 个板块)\n")

    if sort == "flow":
        # 资金流入排序：突出 netFlow
        print(f"{'序号':>4}  {'板块名称':<10}  {'涨跌幅':>8}  {'主力净流入(亿)':>14}  {'↑':>4} {'↓':>4}  {'领涨股':<8}  {'5日涨幅':>8}")
        print("-" * 85)
        for i, b in enumerate(boards, 1):
            chg = b.get("changeRate") or 0
            flow = (b.get("netFlow") or 0) / 1e8
            up = b.get("upCount") or 0
            down = b.get("downCount") or 0
            lead = b.get("leadStock", "")
            chg5 = b.get("change5day") or 0
            print(f"{i:>4}  {b['name']:<10}  {chg:>+7.2f}%  {flow:>+13.2f}  {up:>4} {down:>4}  {lead:<8}  {chg5:>+7.2f}%")
    elif sort == "5day":
        # 5日涨幅排序：突出 change5day
        print(f"{'序号':>4}  {'板块名称':<10}  {'5日涨幅':>8}  {'今日涨幅':>8}  {'↑':>4} {'↓':>4}  {'主力净流入(亿)':>14}  {'领涨股':<8}")
        print("-" * 85)
        for i, b in enumerate(boards, 1):
            chg5 = b.get("change5day") or 0
            chg = b.get("changeRate") or 0
            up = b.get("upCount") or 0
            down = b.get("downCount") or 0
            flow = (b.get("netFlow") or 0) / 1e8
            lead = b.get("leadStock", "")
            print(f"{i:>4}  {b['name']:<10}  {chg5:>+7.2f}%  {chg:>+7.2f}%  {up:>4} {down:>4}  {flow:>+13.2f}  {lead:<8}")
    else:
        # 默认涨幅排序
        print(f"{'序号':>4}  {'板块名称':<10}  {'涨跌幅':>8}  {'↑':>4} {'↓':>4}  {'换手率':>6}  {'成交额(亿)':>10}  {'主力净流入(亿)':>14}  {'领涨股':<8}")
        print("-" * 95)
        for i, b in enumerate(boards, 1):
            chg = b.get("changeRate") or 0
            up = b.get("upCount") or 0
            down = b.get("downCount") or 0
            tr = b.get("turnoverRate") or 0
            amt = (b.get("amount") or 0) / 1e8
            flow = (b.get("netFlow") or 0) / 1e8
            lead = b.get("leadStock", "")
            print(f"{i:>4}  {b['name']:<10}  {chg:>+7.2f}%  {up:>4} {down:>4}  {tr:>5.1f}%  {amt:>10.1f}  {flow:>+13.2f}  {lead:<8}")


def cmd_dragon_tiger(args):
    """龙虎榜"""
    tab = getattr(args, "dt_tab", "stock")
    days = getattr(args, "dt_days", 3)
    count = getattr(args, "count", 30)
    d = get(f"/api/market/dragon_tiger?tab={tab}&days={days}&count={count}")
    data = d.get("data", {})
    items = data.get("items", [])
    total = data.get("total", 0)

    def _amt(v):
        """金额转亿"""
        return (v or 0) / 1e8

    if tab == "stock":
        print(f"龙虎榜个股明细  (近{days}个交易日，共 {total} 条)\n")
        print(f"{'日期':<12}{'名称':<8}{'代码':<8}{'涨跌幅':>8}{'净买额(亿)':>12}{'买入(亿)':>10}{'卖出(亿)':>10}  {'次日':>6} {'5日':>6}  {'上榜原因'}")
        print("-" * 115)
        for s in items:
            chg = s.get("changeRate") or 0
            net = _amt(s.get("netAmt"))
            buy = _amt(s.get("buyAmt"))
            sell = _amt(s.get("sellAmt"))
            d1 = s.get("d1Chg")
            d5 = s.get("d5Chg")
            d1_s = f"{d1:+.1f}%" if d1 is not None else "  -  "
            d5_s = f"{d5:+.1f}%" if d5 is not None else "  -  "
            reason = (s.get("reason") or "")[:25]
            explain = s.get("explain") or ""
            tag = f" [{explain}]" if explain else ""
            print(f"{s['date']:<12}{s['name']:<8}{s['code']:<8}{chg:>+7.2f}%{net:>+11.2f}{buy:>10.2f}{sell:>10.2f}  {d1_s:>6} {d5_s:>6}  {reason}{tag}")

    elif tab == "dept":
        print(f"活跃营业部（游资/敢死队）  (近{days}个交易日，共 {total} 条)\n")
        print(f"{'日期':<12}{'营业部名称':<35}{'净额(亿)':>10}{'买入(亿)':>10}{'卖出(亿)':>10}{'买次':>5}{'卖次':>5}")
        print("-" * 100)
        for s in items:
            net = _amt(s.get("netAmt"))
            buy = _amt(s.get("buyAmt"))
            sell = _amt(s.get("sellAmt"))
            name = (s.get("deptName") or "")[:33]
            stocks = s.get("buyStocks", "")
            print(f"{s['date']:<12}{name:<35}{net:>+9.2f}{buy:>10.2f}{sell:>10.2f}{s.get('buyTimes',0):>5}{s.get('sellTimes',0):>5}")
            if stocks:
                # 股票代码转简洁格式
                codes = [c.split(".")[0] for c in stocks.split() if c.strip()]
                if codes:
                    print(f"{'':>12}  买入: {' '.join(codes[:8])}")

    elif tab == "org":
        print(f"机构买卖明细  (近{days}个交易日，共 {total} 条)\n")
        print(f"{'日期':<12}{'名称':<10}{'代码':<8}{'净买额(亿)':>12}{'买入(亿)':>10}{'卖出(亿)':>10}{'买次':>5}{'卖次':>5}{'买机构':>6}{'卖机构':>6}  {'次日':>6} {'5日':>6}")
        print("-" * 115)
        for s in items:
            net = _amt(s.get("netBuy"))
            buy = _amt(s.get("buyAmt"))
            sell = _amt(s.get("sellAmt"))
            d1 = s.get("d1Chg")
            d5 = s.get("d5Chg")
            d1_s = f"{d1:+.1f}%" if d1 is not None else "  -  "
            d5_s = f"{d5:+.1f}%" if d5 is not None else "  -  "
            print(f"{s['date']:<12}{s['name']:<10}{s['code']:<8}{net:>+11.2f}{buy:>10.2f}{sell:>10.2f}{s.get('buyTimes',0):>5}{s.get('sellTimes',0):>5}{s.get('buyCount',0):>6}{s.get('sellCount',0):>6}  {d1_s:>6} {d5_s:>6}")


def cmd_ths_dragon_tiger(args):
    """同花顺游资龙虎榜"""
    tab = getattr(args, "ths_dt_tab", "youzi")
    count = getattr(args, "count", 30)
    d = get(f"/api/market/ths_dragon_tiger?tab={tab}&count={count}")
    data = d.get("data", {})
    items = data.get("items", [])
    report_date = data.get("date", "")

    tab_names = {
        "youzi": "游资(一线+知名)", "jigou": "机构专用",
        "gfgs": "跟风高手", "gansidui": "敢死队", "all": "全部",
    }
    print(f"同花顺龙虎榜 - {tab_names.get(tab, tab)}  ({report_date}，共 {len(items)} 条)\n")

    if not items:
        print("暂无数据")
        return

    # 营业部名称简化映射
    DEPT_SHORT = {
        "深股通专用": "深股通",
        "沪股通专用": "沪股通",
        "机构专用": "机构",
    }

    def _short_dept(name):
        if name in DEPT_SHORT:
            return DEPT_SHORT[name]
        # 去掉通用后缀
        name = name.replace("证券股份有限公司", "").replace("证券有限公司", "").replace("证券营业部", "")
        name = name.replace("股份有限公司", "").replace("有限公司", "")
        if len(name) > 14:
            name = name[:14] + ".."
        return name

    def _fmt_net(val_str):
        try:
            v = float(val_str.replace(",", ""))
            if abs(v) >= 10000:
                return f"{v / 10000:+.2f}亿"
            return f"{v:+.0f}万"
        except (ValueError, TypeError):
            return val_str

    print(f"{'代码':<8}{'名称':<10}{'现价':>8}{'涨跌幅':>8}{'净买入额':>10}  {'标签':<16}{'关键席位'}")
    print("-" * 110)
    for s in items:
        labels = ", ".join(s.get("labels", []))
        # 去重席位（同一个营业部可能出现在买入和卖出两张表中）
        seats = s.get("seats", [])
        seen_depts = set()
        unique_seats = []
        for seat in seats:
            if seat["dept"] not in seen_depts:
                seen_depts.add(seat["dept"])
                unique_seats.append(seat)

        seat_strs = []
        for seat in unique_seats[:3]:
            dept = _short_dept(seat["dept"])
            net_s = _fmt_net(seat["net"])
            seat_strs.append(f"{dept}({net_s})")
        seats_display = " | ".join(seat_strs)

        print(f"{s['code']:<8}{s['name']:<10}{s['price']:>8}{s['chg']:>8}{s['netAmt']:>10}  {labels:<16}{seats_display}")


def cmd_currency(args):
    """货币风向"""
    tab = getattr(args, "currency_tab", "usdcny")
    days = getattr(args, "currency_days", 120)
    d = get(f"/api/market/currency?tab={tab}&days={days}")
    data = d.get("data", {})

    if tab == "usdcny":
        name = data.get("name", "美元/离岸人民币")
        latest = data.get("latest", {})
        chg = data.get("changeRate", 0)
        ma5 = data.get("ma5")
        ma20 = data.get("ma20")
        ma60 = data.get("ma60")
        chg30d = data.get("chg30d")
        chg60d = data.get("chg60d")
        year_high = data.get("yearHigh")
        year_low = data.get("yearLow")
        signals = data.get("signals", [])
        items = data.get("items", [])

        print(f"货币风向 — {name}\n")

        if latest:
            print(f"最新: {latest.get('date', '')}  汇率: {latest.get('close', 0):.4f}  涨跌: {chg:+.4f}%")
            o, h, l = latest.get("open", 0), latest.get("high", 0), latest.get("low", 0)
            if o != latest.get("close", 0) or h != l:
                print(f"  开盘: {o:.4f}  最高: {h:.4f}  最低: {l:.4f}")

        if ma5 or ma20 or ma60:
            print(f"\n{'':>8}{'均线值':>10}{'偏离度':>10}")
            cur = latest.get("close", 0)
            for label, ma in [("MA5", ma5), ("MA20", ma20), ("MA60", ma60)]:
                if ma:
                    dev = (cur - ma) / ma * 100
                    print(f"  {label:<6}{ma:>10.4f}{dev:>+9.2f}%")

        if chg30d is not None or chg60d is not None:
            print(f"\n区间变化:")
            if chg30d is not None:
                direction = "人民币贬值" if chg30d > 0 else "人民币升值"
                print(f"  近30日: {chg30d:+.2f}%  ({direction})")
            if chg60d is not None:
                direction = "人民币贬值" if chg60d > 0 else "人民币升值"
                print(f"  近60日: {chg60d:+.2f}%  ({direction})")

        if year_high and year_low:
            cur = latest.get("close", 0)
            pos = (cur - year_low) / (year_high - year_low) * 100 if year_high != year_low else 50
            print(f"\n近一年区间: {year_low:.4f} ~ {year_high:.4f}  当前位置: {pos:.0f}%")

        if signals:
            print(f"\n信号:")
            for s in signals:
                print(f"  ● {s}")

        if items:
            count = getattr(args, "count", 10)
            show = items[-count:]
            print(f"\n{'日期':<12}{'开盘':>10}{'收盘':>10}{'最高':>10}{'最低':>10}")
            print("-" * 56)
            for s in show:
                print(f"{s['date']:<12}{s['open']:>10.4f}{s['close']:>10.4f}{s['high']:>10.4f}{s['low']:>10.4f}")

        print(f"\n注: 汇率上涨 = 人民币贬值（1美元兑更多人民币），下跌 = 人民币升值")

    elif tab == "shibor":
        shibor = data.get("shibor", {})
        lpr = data.get("lpr", [])
        signals = data.get("signals", [])

        print("货币风向 — Shibor利率 + LPR\n")

        # Shibor 最新利率
        print("【Shibor 最新利率】")
        for period_name in ["隔夜", "1周", "1月", "3月"]:
            rows = shibor.get(period_name, [])
            if rows:
                r = rows[0]
                chg = r.get("change") or 0
                chg_str = f"{chg:+.1f}bp" if chg else "持平"
                print(f"  {period_name:<6}{r.get('rate', 0):>8.4f}%  {chg_str:<10}({r.get('date', '')})")

        # Shibor 隔夜利率趋势表
        overnight = shibor.get("隔夜", [])
        if overnight:
            count = min(getattr(args, "count", 10), len(overnight))
            show = overnight[:count]
            print(f"\n【Shibor隔夜利率走势】(近{count}个交易日)")
            print(f"{'日期':<12}{'利率(%)':>10}{'变动(bp)':>10}")
            print("-" * 34)
            for r in show:
                chg = r.get("change") or 0
                print(f"{r['date']:<12}{r['rate']:>10.4f}{chg:>+10.1f}")

        # LPR 变化
        if lpr:
            print(f"\n【LPR 贷款市场报价利率】(近12期)")
            print(f"{'日期':<12}{'1年期(%)':>10}{'5年期(%)':>10}")
            print("-" * 34)
            for r in lpr[:12]:
                lpr1 = r.get("lpr1y")
                lpr5 = r.get("lpr5y")
                print(f"{r['date']:<12}{lpr1 or '-':>10}{lpr5 or '-':>10}")

        if signals:
            print(f"\n信号:")
            for s in signals:
                print(f"  ● {s}")

        print(f"\n注: Shibor隔夜利率是银行间资金面松紧的核心指标，利率上升=资金面紧张，下降=资金面宽松")


def cmd_capital_flow(args):
    """资金流向"""
    tab = getattr(args, "cf_tab", "market")
    days = getattr(args, "cf_days", 20)
    d = get(f"/api/market/capital_flow?tab={tab}&days={days}")
    data = d.get("data", {})

    def _yi(v):
        """元转亿"""
        return (v or 0) / 1e8

    def _baiwan_yi(v):
        """百万元转亿"""
        return (v or 0) / 100

    if tab == "market":
        name = data.get("name", "上证指数")
        items = data.get("items", [])
        latest = data.get("latest", {})
        sum5d = data.get("sum5d")
        sum10d = data.get("sum10d")
        sum20d = data.get("sum20d")

        print(f"大盘资金流向 ({name})\n")

        if latest:
            main = _yi(latest.get("mainNet"))
            big = _yi(latest.get("bigNet"))
            sup = _yi(latest.get("superNet"))
            mid = _yi(latest.get("midNet"))
            small = _yi(latest.get("smallNet"))
            print(f"最新交易日: {latest.get('date', '')}  {name}: {latest.get('close', 0):.2f}  涨跌幅: {latest.get('changeRate', 0):+.2f}%\n")
            print(f"  主力净流入: {main:>+10.2f} 亿  ({latest.get('mainPct', 0):+.2f}%)")
            print(f"  超大单净流入: {sup:>+8.2f} 亿  ({latest.get('superPct', 0):+.2f}%)")
            print(f"  大单净流入:   {big:>+8.2f} 亿  ({latest.get('bigPct', 0):+.2f}%)")
            print(f"  中单净流入:   {mid:>+8.2f} 亿  ({latest.get('midPct', 0):+.2f}%)")
            print(f"  小单净流入:   {small:>+8.2f} 亿  ({latest.get('smallPct', 0):+.2f}%)")

        if sum5d is not None or sum10d is not None:
            print(f"\n累计主力净流入:")
            if sum5d is not None:
                print(f"  近 5日: {_yi(sum5d):>+10.2f} 亿")
            if sum10d is not None:
                print(f"  近10日: {_yi(sum10d):>+10.2f} 亿")
            if sum20d is not None:
                print(f"  近20日: {_yi(sum20d):>+10.2f} 亿")

        if items:
            print(f"\n{'日期':<12}{'收盘':>8}{'涨跌幅':>8}{'主力(亿)':>10}{'超大单(亿)':>10}{'大单(亿)':>10}{'中单(亿)':>10}{'小单(亿)':>10}")
            print("-" * 90)
            for s in items[-days:]:
                print(f"{s['date']:<12}{s['close']:>8.2f}{s['changeRate']:>+7.2f}%"
                      f"{_yi(s['mainNet']):>+10.2f}{_yi(s['superNet']):>+10.2f}{_yi(s['bigNet']):>+10.2f}"
                      f"{_yi(s['midNet']):>+10.2f}{_yi(s['smallNet']):>+10.2f}")

    elif tab == "north":
        items = data.get("items", [])
        deals = data.get("deals", [])

        print("北向资金流向\n")

        if deals:
            latest = deals[0]
            print(f"最新交易日: {latest.get('date', '')}")
            total = _baiwan_yi(latest.get("totalDealAmt"))
            sh = _baiwan_yi(latest.get("shDealAmt"))
            sz = _baiwan_yi(latest.get("szDealAmt"))
            print(f"  成交额合计: {total:.2f} 亿  (沪股通: {sh:.2f} 亿  深股通: {sz:.2f} 亿)")
            if latest.get("indexClose"):
                print(f"  上证指数: {latest['indexClose']:.2f}  涨跌幅: {latest.get('indexChg', 0):+.2f}%")

            # 计算近5日/10日/20日均值
            if len(deals) >= 5:
                avg5 = sum(_baiwan_yi(d.get("totalDealAmt")) for d in deals[:5]) / 5
                print(f"\n  近 5日日均成交: {avg5:.2f} 亿")
            if len(deals) >= 10:
                avg10 = sum(_baiwan_yi(d.get("totalDealAmt")) for d in deals[:10]) / 10
                print(f"  近10日日均成交: {avg10:.2f} 亿")
            if len(deals) >= 20:
                avg20 = sum(_baiwan_yi(d.get("totalDealAmt")) for d in deals[:20]) / 20
                print(f"  近20日日均成交: {avg20:.2f} 亿")

        print(f"\n注: 2024年8月起北向资金不再披露实时净买卖数据，仅有成交额。\n")

        if deals:
            print(f"{'日期':<12}{'沪股通(亿)':>12}{'深股通(亿)':>12}{'合计(亿)':>12}{'上证':>8}{'涨跌幅':>8}")
            print("-" * 70)
            for dl in deals[:days]:
                sh = _baiwan_yi(dl.get("shDealAmt"))
                sz = _baiwan_yi(dl.get("szDealAmt"))
                total = _baiwan_yi(dl.get("totalDealAmt"))
                idx = dl.get("indexClose") or 0
                chg = dl.get("indexChg") or 0
                print(f"{dl['date']:<12}{sh:>12.2f}{sz:>12.2f}{total:>12.2f}{idx:>8.2f}{chg:>+7.2f}%")


def cmd_market_overview(args):
    """A 股大盘总览"""
    d = get("/api/market/overview")
    data = d.get("data", {})

    print("A 股大盘总览\n")

    # 三大指数 + 其他指数
    indices = data.get("indices", [])
    main_codes = {"000001", "399001", "399006"}
    extra_codes = {"000300", "000016", "000905", "399852", "399303"}

    LIMIT_TAG_MAP = {
        "FIRST_LIMIT": "首板", "LIMIT_BACK": "回封", "CONTINUE_LIMIT": "连板",
        "LIMIT_OPEN": "炸板", "": "",
    }

    def _fmt_chg(v):
        return f"{v:+.2f}" if v is not None else "-"

    # 主要指数
    print("【三大指数】")
    for idx in indices:
        if idx["code"] in main_codes:
            close = idx.get("close", 0)
            chg = idx.get("changeRate", 0)
            chg_amt = idx.get("changeAmt", 0)
            turnover = idx.get("turnover", 0) / 1e8
            amp = idx.get("amplitude", 0)
            print(f"  {idx['name']:<6}  {close:>10.2f}  {_fmt_chg(chg):>7}%  {_fmt_chg(chg_amt):>8}  成交:{turnover:>8.0f}亿  振幅:{amp:.2f}%")
    print()

    # 其他指数
    print("【其他指数】")
    for idx in indices:
        if idx["code"] in extra_codes:
            close = idx.get("close", 0)
            chg = idx.get("changeRate", 0)
            turnover = idx.get("turnover", 0) / 1e8
            print(f"  {idx['name']:<6}  {close:>10.2f}  {_fmt_chg(chg):>7}%  成交:{turnover:>8.0f}亿")
    print()

    # 全市场涨跌统计
    stats = data.get("market_stats", {})
    if stats:
        rise = stats.get("totalRise", 0)
        fall = stats.get("totalFall", 0)
        flat = stats.get("totalFlat", 0)
        total = stats.get("totalStocks", 0)
        turnover = stats.get("totalTurnover", 0)
        print(f"【全市场统计】")
        print(f"  上涨: {rise} 家  |  下跌: {fall} 家  |  平盘: {flat} 家  |  共 {total} 只")
        print(f"  两市总成交额: {turnover:.0f} 亿元")
        if rise and fall:
            ratio = rise / max(fall, 1)
            bar_len = 40
            rise_bar = round(bar_len * rise / max(rise + fall, 1))
            fall_bar = bar_len - rise_bar
            print(f"  涨跌比: {'█' * rise_bar}{'░' * fall_bar}  {rise}:{fall}")
        print()

    # 资金流向
    flow = data.get("capital_flow", {})
    if flow:
        total_flow = flow.get("totalMainFlow", 0)
        arrow = "↑" if total_flow > 0 else "↓"
        print(f"【大盘资金流向】  主力净流入: {arrow} {total_flow:+.2f} 亿元")
        for detail in flow.get("details", []):
            m = detail["market"]
            mf = detail["mainFlow"]
            sl = detail["superLarge"]
            lg = detail["large"]
            print(f"  {m}: 主力 {mf:+.2f}亿  (超大单:{sl:+.2f}  大单:{lg:+.2f})")
        print()

    # 涨跌停统计
    limit_up = data.get("limit_up", {})
    limit_down = data.get("limit_down", {})
    if limit_up or limit_down:
        up_total = limit_up.get("total", 0)
        down_total = limit_down.get("total", 0)
        print(f"【涨跌停统计】  涨停: {up_total} 家  |  跌停: {down_total} 家")
        count = getattr(args, "count", 10)
        if limit_up.get("stocks"):
            print(f"  涨停股 (前{min(count, len(limit_up['stocks']))}只):")
            for i, s in enumerate(limit_up["stocks"][:count]):
                raw_tag = s.get("tag", "")
                tag = LIMIT_TAG_MAP.get(raw_tag, raw_tag)
                tag_s = f" [{tag}]" if tag else ""
                chg = s.get("changeRate")
                chg_s = f" {float(chg):+.2f}%" if chg else ""
                print(f"    {i+1:>2}. {s['name']}({s['code']}){chg_s}{tag_s}")
        if limit_down.get("stocks"):
            print(f"  跌停股 (前{min(count, len(limit_down['stocks']))}只):")
            for i, s in enumerate(limit_down["stocks"][:count]):
                raw_tag = s.get("tag", "")
                tag = LIMIT_TAG_MAP.get(raw_tag, raw_tag)
                tag_s = f" [{tag}]" if tag else ""
                chg = s.get("changeRate")
                chg_s = f" {float(chg):+.2f}%" if chg else ""
                print(f"    {i+1:>2}. {s['name']}({s['code']}){chg_s}{tag_s}")
        print()

    # 大小盘对比
    cap = data.get("cap_comparison", {})
    if cap:
        lc = cap.get("largeCap", {})
        sc = cap.get("smallCap", {})
        stronger = cap.get("stronger", "")
        print(f"【大小盘对比】  {stronger}")
        print(f"  {lc.get('name','')}: {lc.get('changeRate',0):+.2f}%  |  {sc.get('name','')}: {sc.get('changeRate',0):+.2f}%  |  差值: {cap.get('diff',0):+.2f}%")
        print()

    # 信号
    signals = data.get("signals", [])
    if signals:
        print("信号:")
        for s in signals:
            print(f"  ● {s}")


def cmd_market(args):
    """大盘与行业环境"""
    d = get(f"/api/market/environment")
    data = d.get("data", {})
    print(f"大盘与行业环境\n")

    # 指数列表（沪深300 + 创业板指）
    for idx in data.get("indices", []):
        name = idx.get("name", "")
        close = idx.get("close")
        change = idx.get("changeRate")
        change_s = f"{change:+.2f}%" if change is not None else ""
        print(f"【{name}】  {close}  {change_s}")
        for label, key in [("MA5", "ma5"), ("MA20", "ma20"), ("MA60", "ma60")]:
            v = idx.get(key)
            v_s = f"{v:.2f}" if v is not None else "-"
            print(f"  {label}: {v_s}", end="")
        print()
        trend = idx.get("trend", "")
        if trend:
            print(f"  趋势: {trend}")
        dev = idx.get("devMa20")
        if dev is not None:
            print(f"  偏离MA20: {dev:+.2f}%")
        print()

    # 北向资金
    nb = data.get("northbound", {})
    if nb:
        print(f"【北向资金活跃度】（2024年后不再公开日净买卖，以成交额衡量）")
        latest = nb.get("latest", {})
        if latest:
            print(f"  最近交易日: {latest.get('dealAmt')} 亿元 ({latest.get('date')})")
        avg5 = nb.get("avg5d")
        avg20 = nb.get("avg20d")
        if avg5 is not None:
            print(f"  近5日均值:  {avg5:.2f} 亿元")
        if avg20 is not None:
            print(f"  近20日均值: {avg20:.2f} 亿元")
        nb_trend = nb.get("trend", "")
        if nb_trend and avg5 and avg20 and avg20 > 0:
            pct = (avg5 - avg20) / avg20 * 100
            arrow = "↑" if pct > 0 else "↓"
            print(f"  活跃度: {arrow} {nb_trend}（{'高于' if pct > 0 else '低于'}20日均值 {abs(pct):.1f}%）")
        print()

    # 融资融券
    mg = data.get("margin", {})
    if mg:
        print(f"【融资融券】")
        latest_mg = mg.get("latest", {})
        if latest_mg:
            rzye = latest_mg.get("rzye")
            rzjme = latest_mg.get("rzjme")
            print(f"  最近交易日: 融资余额 {rzye:.0f} 亿元 ({latest_mg.get('date')})")
            if rzjme is not None:
                print(f"  当日融资净买入: {rzjme:+.2f} 亿元")
        avg5_mg = mg.get("avg5d")
        avg20_mg = mg.get("avg20d")
        if avg5_mg is not None:
            print(f"  近5日均值:  {avg5_mg:.0f} 亿元")
        if avg20_mg is not None:
            print(f"  近20日均值: {avg20_mg:.0f} 亿元")
        mg_trend = mg.get("trend", "")
        if mg_trend:
            print(f"  趋势: {mg_trend}")
        print()

    # 国债收益率
    bond = data.get("bond", {})
    if bond:
        print(f"【利率环境】（十年国债ETF价格反映收益率方向）")
        print(f"  {bond.get('etfName')}: {bond.get('close')} ({bond.get('date')})")
        ma20 = bond.get("ma20")
        dev = bond.get("devMa20")
        if ma20 is not None:
            print(f"  MA20: {ma20:.3f}  偏离: {dev:+.2f}%")
        bond_trend = bond.get("trend", "")
        if bond_trend:
            print(f"  趋势: {bond_trend}")
        print()

    signals = data.get("signals", [])
    if signals:
        print(f"信号:")
        for s in signals:
            print(f"  ● {s}")


def cmd_fund_flow(args):
    """基金申赎资金流趋势"""
    d = get(f"/api/fund/{args.code}/fund_flow")
    data = d.get("data", {})
    quarters = data.get("quarters", [])

    if not quarters:
        print("暂无资金流向数据")
        return

    print(f"基金 {args.code} 资金流向趋势\n")
    print(f"{'季度':<12s} {'净资产(亿)':>10s} {'申购(亿)':>10s} {'赎回(亿)':>10s} {'净申赎(亿)':>10s} {'净申赎率':>10s} {'趋势'}")
    print("-" * 78)
    for q in quarters:
        date = q.get("date", "")
        nav = q.get("nav", 0)
        sub = q.get("subscribe", 0)
        red = q.get("redeem", 0)
        nf = q.get("netFlow", 0)
        nfr = q.get("netFlowRate")

        nf_s = f"{nf:+.2f}"
        nfr_s = f"{nfr:+.2f}%" if nfr is not None else "N/A"
        if nf > 0:
            arrow = "↑ 净申购"
        elif nf < -abs(nav) * 0.3 if nav else False:
            arrow = "↓↓ 大额赎回"
        elif nf < 0:
            arrow = "↓ 净赎回"
        else:
            arrow = "— 持平"
        print(f"{date:<12s} {nav:>10.2f} {sub:>10.2f} {red:>10.2f} {nf_s:>10s} {nfr_s:>10s}   {arrow}")
    print("-" * 78)

    org_data = data.get("orgData", [])
    org_trend = data.get("orgRatioTrend", "")
    if org_data:
        print(f"\n机构占比变化:")
        org_parts = [f"  {o['date']}: {o['orgRate']}%" for o in org_data[:4]]
        print("  →  ".join(org_parts))
        if org_trend:
            print(f"  趋势: {org_trend}")

    signals = data.get("signals", [])
    if signals:
        print(f"\n信号:")
        for s in signals:
            print(f"  ● {s}")


def cmd_stock_quote(args):
    """个股实时行情"""
    codes = args.code or ""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        print("错误: 请提供股票代码，如: python client.py stock_quote 600519", file=sys.stderr)
        sys.exit(1)
    d = get(f"/api/stock/quote?codes={','.join(code_list)}")
    stocks = d.get("data", {}).get("stocks", [])
    if not stocks:
        print("无行情数据")
        return

    print(f"个股实时行情  (共 {len(stocks)} 只)\n")
    print(f"{'名称':<8}{'代码':<8}{'现价':>8}{'涨跌幅':>8}{'涨跌额':>8}{'成交额(亿)':>10}{'换手率':>7}{'PE':>8}{'PB':>7}{'总市值(亿)':>10}")
    print("-" * 100)
    for s in stocks:
        price = s.get("price")
        chg_r = s.get("changeRate", 0)
        chg_a = s.get("changeAmt", 0)
        turnover = (s.get("turnover") or 0) / 1e8
        tr = s.get("turnoverRate", 0)
        pe = s.get("pe")
        pb = s.get("pb")
        mcap = (s.get("marketCap") or 0) / 1e8
        pe_s = f"{pe:.1f}" if pe else "-"
        pb_s = f"{pb:.2f}" if pb else "-"
        price_s = f"{price:.2f}" if price else "-"
        print(f"{s.get('name', ''):<8}{s.get('code', ''):<8}{price_s:>8}{chg_r:>+7.2f}%{chg_a:>+8.2f}"
              f"{turnover:>10.2f}{tr:>6.2f}%{pe_s:>8}{pb_s:>7}{mcap:>10.1f}")


def cmd_stock_kline(args):
    """个股K线"""
    code = args.code
    if not code:
        print("错误: 请提供股票代码，如: python client.py stock_kline 600519", file=sys.stderr)
        sys.exit(1)
    period_map = {"day": "101", "week": "102", "month": "103"}
    period = period_map.get(getattr(args, "stock_period", "day"), "101")
    limit = getattr(args, "count", 60)
    d = get(f"/api/stock/{code}/kline?period={period}&limit={limit}")
    data = d.get("data", {})
    if d.get("status_code") != 0:
        print(f"获取失败: {d.get('msg', '未知错误')}")
        return

    name = data.get("name", "")
    klines = data.get("klines", [])
    period_name = data.get("period", "日K")
    print(f"{name}({code}) {period_name}  (共 {len(klines)} 条)\n")
    print(f"{'日期':<12}{'开盘':>8}{'收盘':>8}{'最高':>8}{'最低':>8}{'成交量(手)':>12}{'成交额(亿)':>10}")
    print("-" * 80)
    for k in klines:
        vol = k.get("volume", 0)
        amt = k.get("turnover", 0) / 1e8
        print(f"{k['date']:<12}{k['open']:>8.2f}{k['close']:>8.2f}{k['high']:>8.2f}"
              f"{k['low']:>8.2f}{vol:>12,}{amt:>10.2f}")


def cmd_stock_flow(args):
    """个股资金流向"""
    code = args.code
    if not code:
        print("错误: 请提供股票代码，如: python client.py stock_flow 600519", file=sys.stderr)
        sys.exit(1)
    days = getattr(args, "stock_days", 20)
    d = get(f"/api/stock/{code}/capital_flow?days={days}")
    data = d.get("data", {})
    if d.get("status_code") != 0:
        print(f"获取失败: {d.get('msg', '未知错误')}")
        return

    def _yi(v):
        return (v or 0) / 1e8

    name = data.get("name", "")
    items = data.get("items", [])
    latest = data.get("latest", {})
    sum5d = data.get("sum5d")
    sum10d = data.get("sum10d")

    print(f"{name}({code}) 资金流向\n")

    if latest:
        print(f"最新交易日: {latest.get('date', '')}  收盘: {latest.get('close', 0):.2f}  涨跌幅: {latest.get('changeRate', 0):+.2f}%\n")
        print(f"  主力净流入:   {_yi(latest.get('mainNet')):>+10.2f} 亿  ({latest.get('mainPct', 0):+.2f}%)")
        print(f"  超大单净流入: {_yi(latest.get('superNet')):>+10.2f} 亿  ({latest.get('superPct', 0):+.2f}%)")
        print(f"  大单净流入:   {_yi(latest.get('bigNet')):>+10.2f} 亿  ({latest.get('bigPct', 0):+.2f}%)")
        print(f"  中单净流入:   {_yi(latest.get('midNet')):>+10.2f} 亿  ({latest.get('midPct', 0):+.2f}%)")
        print(f"  小单净流入:   {_yi(latest.get('smallNet')):>+10.2f} 亿  ({latest.get('smallPct', 0):+.2f}%)")

    if sum5d is not None or sum10d is not None:
        print(f"\n累计主力净流入:")
        if sum5d is not None:
            print(f"  近 5日: {_yi(sum5d):>+10.2f} 亿")
        if sum10d is not None:
            print(f"  近10日: {_yi(sum10d):>+10.2f} 亿")

    if items:
        print(f"\n{'日期':<12}{'收盘':>8}{'涨跌幅':>8}{'主力(亿)':>10}{'超大单(亿)':>10}{'大单(亿)':>10}{'中单(亿)':>10}{'小单(亿)':>10}")
        print("-" * 90)
        for s in items:
            print(f"{s['date']:<12}{s['close']:>8.2f}{s['changeRate']:>+7.2f}%"
                  f"{_yi(s['mainNet']):>+10.2f}{_yi(s['superNet']):>+10.2f}{_yi(s['bigNet']):>+10.2f}"
                  f"{_yi(s['midNet']):>+10.2f}{_yi(s['smallNet']):>+10.2f}")


def cmd_stock_valuation(args):
    """个股估值历史"""
    code = args.code
    if not code:
        print("错误: 请提供股票代码，如: python client.py stock_valuation 600519", file=sys.stderr)
        sys.exit(1)
    years = getattr(args, "years", 3)
    count = getattr(args, "count", 30)
    d = get(f"/api/stock/{code}/valuation?years={years}")
    data = d.get("data", {})
    items = data.get("items", [])
    if not items:
        print("无估值数据")
        return

    print(f"个股估值历史 {code}  (近{years}年，共 {len(items)} 条，显示最近{min(count, len(items))}条)\n")

    # 计算百分位
    pe_vals = [i["pe_ttm"] for i in items if i.get("pe_ttm") is not None]
    pb_vals = [i["pb"] for i in items if i.get("pb") is not None]
    if pe_vals:
        current_pe = pe_vals[0]
        pe_pct = sum(1 for v in pe_vals if v <= current_pe) / len(pe_vals) * 100
        print(f"当前 PE_TTM: {current_pe:.2f}  百分位: {pe_pct:.1f}%  (近{years}年)")
    if pb_vals:
        current_pb = pb_vals[0]
        pb_pct = sum(1 for v in pb_vals if v <= current_pb) / len(pb_vals) * 100
        print(f"当前 PB:     {current_pb:.2f}  百分位: {pb_pct:.1f}%  (近{years}年)")
    print()

    print(f"{'日期':<12}{'PE_TTM':>10}{'PB':>10}{'总市值(亿)':>12}")
    print("-" * 50)
    for item in items[:count]:
        pe = item.get("pe_ttm")
        pb = item.get("pb")
        mcap = item.get("market_cap")
        pe_s = f"{pe:.2f}" if pe is not None else "-"
        pb_s = f"{pb:.2f}" if pb is not None else "-"
        mcap_s = f"{mcap / 1e8:.1f}" if mcap is not None else "-"
        print(f"{item['date']:<12}{pe_s:>10}{pb_s:>10}{mcap_s:>12}")


def cmd_stock_financial(args):
    """个股财务数据"""
    code = args.code
    if not code:
        print("错误: 请提供股票代码，如: python client.py stock_financial 600519", file=sys.stderr)
        sys.exit(1)
    count = getattr(args, "count", 10)
    d = get(f"/api/stock/{code}/financial?limit={count}")
    data = d.get("data", {})
    if d.get("status_code") != 0:
        print(f"获取失败: {d.get('msg', '未知错误')}")
        return

    name = data.get("name", "")
    items = data.get("items", [])
    print(f"{name}({code}) 财务数据  (共 {len(items)} 期)\n")

    def _yi(v):
        if v is None:
            return "-"
        return f"{v / 1e8:.2f}"

    def _pct(v):
        if v is None:
            return "-"
        return f"{v:+.2f}%"

    def _val(v, fmt=".2f"):
        if v is None:
            return "-"
        return f"{v:{fmt}}"

    print(f"{'报告期':<12}{'EPS':>7}{'营收(亿)':>10}{'净利(亿)':>10}{'ROE':>8}{'毛利率':>8}{'营收同比':>10}{'净利同比':>10}{'分红方案':<20}")
    print("-" * 105)
    for item in items:
        date = item.get("reportDate", "")[:10]
        eps = _val(item.get("basicEps"))
        rev = _yi(item.get("revenue"))
        profit = _yi(item.get("netProfit"))
        roe = _pct(item.get("roe"))
        gm = _pct(item.get("grossMargin"))
        rev_yoy = _pct(item.get("revenueYoy"))
        profit_yoy = _pct(item.get("profitYoy"))
        dividend = item.get("dividend") or "-"
        print(f"{date:<12}{eps:>7}{rev:>10}{profit:>10}{roe:>8}{gm:>8}{rev_yoy:>10}{profit_yoy:>10}  {dividend}")


def cmd_hotlist(args):
    """热榜数据"""
    market = getattr(args, "market", "a")
    count = getattr(args, "count", 10)

    from datetime import datetime
    print(f"同花顺热榜  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 78)

    # A股/港股/美股个股热榜
    market_name = {"a": "A股", "hk": "港股", "us": "美股"}.get(market, market)
    print(f"\n【{market_name}个股热榜 TOP {count}】")
    try:
        d = get(f"/api/hotlist/stocks?market={market}")
        stocks = d.get("data", {}).get("stock_list", [])
        if stocks:
            print(f"{'排名':>4s}  {'代码':8s} {'名称':10s} {'热度':>12s} {'变动':>4s}  {'标签':10s}  概念")
            print("-" * 78)
            for s in stocks[:count]:
                tags = s.get("tag") or {}
                pop = tags.get("popularity_tag", "")
                concepts = ", ".join((tags.get("concept_tag") or [])[:2])
                chg = s.get("hot_rank_chg", 0)
                chg_s = f"+{chg}" if chg > 0 else str(chg) if chg < 0 else "="
                print(f"{s['order']:>4d}  {s['code']:8s} {s['name']:10s} "
                      f"{int(float(s['rate'])):>12,}  {chg_s:>4s}  {pop:10s}  {concepts}")
    except Exception as e:
        print(f"  获取失败: {e}")

    # 概念热榜
    print(f"\n【概念热榜 TOP {count}】")
    try:
        d = get("/api/hotlist/plate?plate_type=concept")
        plates = d.get("data", {}).get("plate_list", [])
        if plates:
            print(f"{'排名':>4s}  {'代码':8s} {'名称':10s} {'热度':>10s} {'变动':>4s}  {'标签':10s}  {'上榜情况':12s}  关联ETF")
            print("-" * 78)
            for p in plates[:count]:
                chg = p.get("hot_rank_chg", 0)
                chg_s = f"+{chg}" if chg > 0 else str(chg) if chg < 0 else "="
                etf = f"{p.get('etf_name', '')}" if p.get("etf_name") else ""
                print(f"{p['order']:>4d}  {p['code']:8s} {p['name']:10s} "
                      f"{int(float(p['rate'])):>10,}  {chg_s:>4s}  {p.get('tag',''):10s}  "
                      f"{p.get('hot_tag',''):12s}  {etf}")
    except Exception as e:
        print(f"  获取失败: {e}")

    # 行业热榜
    print(f"\n【行业热榜 TOP {count}】")
    try:
        d = get("/api/hotlist/plate?plate_type=industry")
        plates = d.get("data", {}).get("plate_list", [])
        if plates:
            print(f"{'排名':>4s}  {'代码':8s} {'名称':14s} {'热度':>10s} {'变动':>4s}  {'标签':10s}  上榜情况")
            print("-" * 78)
            for p in plates[:count]:
                chg = p.get("hot_rank_chg", 0)
                chg_s = f"+{chg}" if chg > 0 else str(chg) if chg < 0 else "="
                print(f"{p['order']:>4d}  {p['code']:8s} {p['name']:14s} "
                      f"{int(float(p['rate'])):>10,}  {chg_s:>4s}  {p.get('tag',''):10s}  "
                      f"{p.get('hot_tag','')}")
    except Exception as e:
        print(f"  获取失败: {e}")

    # ETF 热榜
    print(f"\n【ETF 热榜 TOP {count}】")
    try:
        d = get("/api/hotlist/etf")
        etf_data = d.get("data", {})
        etfs = etf_data.get("list", []) if isinstance(etf_data, dict) else etf_data
        if etfs:
            print(f"{'排名':>4s}  {'代码':8s} {'名称':16s} {'热度':>12s}")
            print("-" * 50)
            for i, item in enumerate(etfs[:count], 1):
                print(f"{i:>4d}  {item['code']:8s} {item['name']:16s} {int(float(item['rate'])):>12,}")
    except Exception as ex:
        print(f"  获取失败: {ex}")


def cmd_hotlist_topics(args):
    """热榜话题"""
    topic_idx = getattr(args, "topic", None)

    # 先获取话题列表
    d = get("/api/hotlist/topics")
    topics = d.get("data", {}).get("topic_list", [])
    if not topics:
        print("暂无热榜话题")
        return

    # 如果指定了 --topic N，显示第 N 个话题的详情
    if topic_idx is not None:
        if topic_idx < 1 or topic_idx > len(topics):
            print(f"序号超出范围，有效范围: 1-{len(topics)}")
            return
        t = topics[topic_idx - 1]
        _show_topic_detail(t, args)
        return

    # 列表模式
    print(f"同花顺热榜话题  共 {len(topics)} 条\n")
    for i, t in enumerate(topics, 1):
        type_tag = {"special": "专题", "topic": "话题"}.get(t.get("type", ""), t.get("type", ""))
        attach = ""
        ai = t.get("attach_info") or {}
        if t.get("attach_type") == "att_vote_id":
            vote = ai.get("att_vote", {})
            attach = f"  [投票:{vote.get('vote_name','')} ({vote.get('vote_count',0)}票)]"
        elif t.get("attach_type") == "att_sub_title":
            attach = f"  [{ai.get('att_sub_title','')}]"
        print(f"  {i:2d}. [{type_tag}] {t['title']}{attach}")
        desc = t.get("description", "")
        if desc:
            print(f"      {desc[:60]}")
    print(f"\n提示: 使用 --topic N 查看第 N 个话题的详细内容，如: hotlist_topics --topic 1")


def _show_topic_detail(t: dict, args):
    """显示单个话题/专题的详细内容"""
    from datetime import datetime

    topic_type = t.get("type", "")
    code = t.get("code", "")
    count = getattr(args, "count", 10)

    if topic_type == "topic":
        # topic 类型：调用 API 获取详情 + 帖子
        d = get(f"/api/hotlist/topic/{code}?page=1&page_size={count}")
        info = d.get("topic", {})
        feeds = d.get("feeds", {}).get("list_feed", [])

        print(f"【话题】{info.get('title', t.get('title', ''))}")
        lead = info.get("lead", "")
        if lead:
            print(f"  {lead}")
        talk_num = info.get("talk_num", 0)
        print(f"  讨论数: {talk_num}")

        # 投票信息
        vote = info.get("vote_detail", {})
        if vote and vote.get("option_list"):
            print(f"\n  投票: {vote.get('title', '')} (共 {vote.get('total_count', 0)} 票)")
            for opt in vote["option_list"]:
                total = vote.get("total_count", 1) or 1
                pct = opt.get("vote_count", 0) / total * 100
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                print(f"    {opt.get('content', ''):8s} {bar} {pct:5.1f}% ({opt.get('vote_count', 0)}票)")

        # 推荐帖子
        if feeds:
            print(f"\n  热门讨论 (共 {len(feeds)} 条):")
            print(f"  {'─' * 60}")
            for i, f in enumerate(feeds, 1):
                author = f.get("author", {})
                stat = f.get("stat", {})
                abstract = f.get("abstract", {}).get("content", "")
                # 清理同花顺自定义标签（hx_topic/hx_stock 等），再去 HTML 标签
                import re
                abstract = re.sub(r"<hx_\w+>.*?</hx_\w+>", "", abstract)
                abstract = re.sub(r"<[^>]+>", "", abstract)
                abstract = abstract.strip()
                ts = f.get("info", {}).get("time", 0)
                if ts > 1e12:
                    ts = ts / 1000
                dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
                # 关联股票
                tags = f.get("tag", {}).get("tags", [])
                stocks = ""
                if tags:
                    stock_names = [tg["name"] for tg in tags[:3] if tg.get("type") == "stock"]
                    if stock_names:
                        stocks = f"  [{', '.join(stock_names)}]"
                print(f"  {i:2d}. @{author.get('name', '')}  {dt}  👍{stat.get('like_num', 0)}  💬{stat.get('comment_num', 0)}{stocks}")
                if abstract:
                    # 截断长文本
                    lines = abstract[:200].split("\n")
                    for line in lines[:3]:
                        line = line.strip()
                        if line:
                            print(f"      {line}")

    elif topic_type == "special":
        # special 类型：解析专题 HTML
        d = get(f"/api/hotlist/special/{code}")
        if d.get("error"):
            print(f"解析失败: {d['error']}")
            return

        title = d.get("title", t.get("title", ""))
        desc = d.get("desc", "")
        abstract = d.get("abstract", "")
        tabs = d.get("tabs", [])
        sections = d.get("sections", [])

        print(f"【专题】{title}")
        if desc:
            print(f"  {desc}")
        if abstract:
            print(f"  {abstract}")
        if tabs:
            print(f"\n  板块: {' | '.join(tabs)}")
        if sections:
            print(f"\n  内容概要:")
            for s in sections:
                s_type = s.get("type", "")
                s_title = s.get("title", "")
                s_content = s.get("content", "")
                if s_title and s_content:
                    print(f"    [{s_type}] {s_title}: {s_content[:100]}")
                elif s_title:
                    print(f"    [{s_type}] {s_title}")
                elif s_content:
                    print(f"    [{s_type}] {s_content[:150]}")
                else:
                    print(f"    [{s_type}]")
    else:
        print(f"未知话题类型: {topic_type}")
        print(f"标题: {t.get('title', '')}")
        print(f"描述: {t.get('description', '')}")


def cmd_hotlist_posts(args):
    """热门文章"""
    count = getattr(args, "count", 10)
    d = get(f"/api/hotlist/posts?page=1&page_size={count}")
    feeds = d.get("data", {}).get("feed", [])
    total = d.get("data", {}).get("count", 0)
    if not feeds:
        print("暂无热门文章")
        return

    from datetime import datetime
    print(f"热门文章  共 {total} 条，显示 {len(feeds)} 条\n")
    for i, f in enumerate(feeds, 1):
        ts = f.get("ctime", 0)
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        stat = f.get("stat", {})
        user = f.get("user", {})
        stocks = ""
        forum_list = (f.get("ext") or {}).get("att_forum", {}).get("forum_list", [])
        if forum_list:
            stock_names = [s["name"].strip() for s in forum_list[:3]]
            stocks = f"  [{', '.join(stock_names)}]"
        title = f.get("title", "")[:60]
        if title:
            print(f"  {i:2d}. {title}")
            print(f"      {dt}  👍{stat.get('like',0):,}  💬{stat.get('reply',0):,}{stocks}  @{user.get('nickname','')}")
        else:
            # 有些文章没有 title，显示 content 片段
            content = f.get("content", "")[:60]
            print(f"  {i:2d}. {content}")
            print(f"      {dt}  👍{stat.get('like',0):,}  💬{stat.get('reply',0):,}{stocks}  @{user.get('nickname','')}")


def cmd_headlines(args):
    """推荐头条"""
    import re
    from datetime import datetime

    d = get("/api/headlines")
    articles = d.get("data", [])
    if not articles:
        print("暂无推荐头条")
        return

    detail_idx = getattr(args, "detail", None)
    if detail_idx is not None:
        # 查看某条头条的详情
        if detail_idx < 1 or detail_idx > len(articles):
            print(f"序号超出范围，有效范围: 1-{len(articles)}")
            return
        a = articles[detail_idx - 1]
        a_type = a.get("type", 0)
        title = a.get("title", "")
        url = a.get("url", "")

        if a_type == 200:
            # 专题 — 复用 get_special_detail
            match = re.search(r'/html/(\d+)\.html', url)
            if not match:
                print(f"无法解析专题链接: {url}")
                return
            code = match.group(1)
            detail = get(f"/api/hotlist/special/{code}")
            print(f"【专题】{detail.get('title') or title}\n")
            desc = detail.get("desc", "")
            abstract = detail.get("abstract", "")
            if desc:
                print(f"  {desc}\n")
            if abstract:
                clean = re.sub(r"<hx_\w+>.*?</hx_\w+>", "", abstract)
                clean = re.sub(r"<[^>]+>", "", clean).strip()
                if clean:
                    print(f"  {clean}\n")
            for sec in detail.get("sections", []):
                sec_type = sec.get("type", "")
                sec_title = sec.get("title", "")
                sec_content = sec.get("content", "")
                if sec_title:
                    print(f"  [{sec_type}] {sec_title}")
                if sec_content:
                    print(f"    {sec_content[:200]}")
                print()
        else:
            # 新闻 — 通过 article detail API 获取
            match = re.search(r'/encoded/([a-f0-9]+)', url)
            if not match:
                print(f"无法解析文章链接: {url}")
                return
            encoded_seq = match.group(1)
            detail = get(f"/api/article/{encoded_seq}")
            news = detail.get("data", {}).get("newsInfo", {})
            if not news:
                print("无法获取文章内容")
                return
            print(f"【{news.get('source', '新闻')}】{news.get('title', title)}\n")
            ts = news.get("ctime", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
            if dt:
                print(f"  时间: {dt}")
            content = news.get("content", "")
            if content:
                # 去掉 HTML 标签，保留纯文本
                text = re.sub(r"<br\s*/?>", "\n", content)
                text = re.sub(r"<p[^>]*>", "\n", text)
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"&nbsp;", " ", text)
                text = re.sub(r"&[a-z]+;", "", text)
                text = text.strip()
                # 整理连续空行
                text = re.sub(r"\n{3,}", "\n\n", text)
                print(f"\n{text}")
        return

    print(f"推荐头条  共 {len(articles)} 条\n")
    for i, a in enumerate(articles, 1):
        ts = int(a.get("rtime", 0))
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        title = a.get("title", "")
        a_type = a.get("type", 0)
        type_tag = "专题" if a_type == 200 else "新闻"
        # type=1 普通新闻，type=200 专题
        print(f"  {i}. [{type_tag}] {title}")
        print(f"     {dt}")
    print(f"\n  提示: 使用 --detail N 查看第 N 条的详细内容")


def cmd_news_themes(args):
    """新闻主题分类"""
    d = get("/api/news_themes")
    themes = d.get("data", [])
    if not themes:
        print("暂无主题分类")
        return

    print(f"新闻主题分类  共 {len(themes)} 个\n")
    for i, t in enumerate(themes, 1):
        theme_id = t.get("themeId", "")
        name = t.get("themeName", "")
        print(f"  {i:2d}. {name:<12s}  {theme_id}")
    print(f"\n  提示: 使用 theme_articles --theme <ID> 查看主题文章")
    print(f"  示例: python client.py theme_articles --theme {themes[0].get('themeId', 'TZ-11385')}")


def cmd_theme_articles(args):
    """主题文章列表"""
    import re
    from datetime import datetime

    theme_id = getattr(args, "theme", None)
    if not theme_id:
        print("错误: 需要指定 --theme 参数，如 --theme TZ-11385")
        print("  使用 news_themes 命令查看所有可用主题")
        return

    count = getattr(args, "count", 15)
    page = getattr(args, "page", 1)
    d = get(f"/api/theme/{theme_id}/articles?page={page}&size={count}")
    data = d.get("data", {})
    articles = data.get("articles", [])
    title = data.get("title", theme_id)
    desc = data.get("description", "")

    detail_idx = getattr(args, "detail", None)
    if detail_idx is not None:
        if not articles:
            print("暂无文章")
            return
        if detail_idx < 1 or detail_idx > len(articles):
            print(f"序号超出范围，有效范围: 1-{len(articles)}")
            return
        a = articles[detail_idx - 1]
        item_id = a.get("itemId", "")
        a_title = a.get("title", "")

        detail = get(f"/api/article/{item_id}")
        news = detail.get("data", {}).get("newsInfo", {})
        redirect_url = detail.get("data", {}).get("redirectUrl", "")

        if news and news.get("content"):
            print(f"【{news.get('source', '新闻')}】{news.get('title', a_title)}\n")
            ts = news.get("ctime", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
            if dt:
                print(f"  时间: {dt}")
            content = news.get("content", "")
            text = re.sub(r"<br\s*/?>", "\n", content)
            text = re.sub(r"<p[^>]*>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"&[a-z]+;", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            print(f"\n{text}")
        elif redirect_url:
            print(f"【{a.get('source', '新闻')}】{a_title}\n")
            print(f"  该文章为外部链接，无法直接获取内容。")
            print(f"  原文地址: {redirect_url}")
        else:
            print(f"无法获取文章内容 (itemId={item_id})")
        return

    print(f"【{title}】主题文章  {theme_id}\n")
    if desc:
        print(f"  {desc}\n")

    if not articles:
        print("  暂无文章")
        return

    for i, a in enumerate(articles, 1):
        ts = a.get("time", 0)
        if ts > 1e12:
            ts = ts / 1000  # ms → s
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        a_title = a.get("title", "")[:60]
        source = a.get("source", "")
        likes = a.get("likesModel", {})
        clicks = likes.get("clicks", 0)
        stocks = a.get("stocks", [])
        stock_str = ""
        if stocks:
            stock_names = [s.get("stockName", "") for s in stocks[:3]]
            stock_str = f"  [{', '.join(stock_names)}]"

        print(f"  {i:2d}. {a_title}")
        click_str = f"  {clicks:,}阅读" if clicks else ""
        print(f"      {dt}  {source}{click_str}{stock_str}")

    print(f"\n  提示: 使用 --detail N 查看第 N 篇文章详情")
    if len(articles) >= count:
        print(f"  提示: 使用 --page {page + 1} 查看下一页")


def cmd_news_feed(args):
    """滚动快讯"""
    from datetime import datetime

    page = getattr(args, "page", 1)
    count = getattr(args, "count", 20)
    d = get(f"/api/news_feed?page={page}")
    articles = d.get("data", {}).get("list", [])
    if not articles:
        print("暂无滚动快讯")
        return

    print(f"滚动快讯  第 {page} 页，{len(articles)} 条\n")
    for i, a in enumerate(articles[:count], 1):
        ts = int(a.get("ctime", 0))
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        title = a.get("title", "")
        digest = a.get("digest", "")
        tags = [t["name"] for t in a.get("tags", [])]
        stocks = [s.get("name", "") for s in a.get("stock", []) if s.get("name")]
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        stock_str = f"  相关: {', '.join(stocks[:3])}" if stocks else ""

        print(f"  {i:2d}. {title}")
        if digest and digest != title:
            print(f"      {digest[:100]}")
        print(f"      {dt}{tag_str}{stock_str}")


def cmd_flash_news(args):
    """快讯分类（A股/重要/公告/期货/异动/港股/美股）"""
    import re
    from datetime import datetime

    TAG_NAME_TO_ID = {
        "a股": 21101, "重要": 62857, "公告": 34843, "期货": 33775,
        "异动": 21111, "港股": 21105, "美股": 21107,
    }
    TAG_ID_TO_NAME = {v: k for k, v in TAG_NAME_TO_ID.items()}

    tag = getattr(args, "tag", None)
    count = getattr(args, "count", 20)
    seq = getattr(args, "seq", 0) or 0
    detail_idx = getattr(args, "detail", None)

    # 无 --tag 参数时列出可用分类
    if not tag:
        d = get("/api/flash_news/tabs")
        filters = d.get("data", {}).get("filter", [])
        if not filters:
            print("暂无快讯分类")
            return
        print(f"快讯分类  共 {len(filters)} 个\n")
        for f in filters:
            print(f"  {f['name']:<6s}  --tag {f['id']}")
        print(f"\n  提示: 使用 flash_news --tag <ID或名称> 查看分类快讯")
        print(f"  示例: python client.py flash_news --tag 重要")
        print(f"        python client.py flash_news --tag 62857")
        return

    # 支持中文名称或数字 ID
    tag_str = str(tag).strip().lower()
    if tag_str in TAG_NAME_TO_ID:
        tag_id = TAG_NAME_TO_ID[tag_str]
    else:
        try:
            tag_id = int(tag_str)
        except ValueError:
            print(f"错误: 无效的分类标签 '{tag}'")
            print(f"  可用名称: {', '.join(TAG_NAME_TO_ID.keys())}")
            return

    tag_name = TAG_ID_TO_NAME.get(tag_id, str(tag_id))
    d = get(f"/api/flash_news/list?tag_id={tag_id}&seq={seq}")
    items = d.get("data", {}).get("list", [])
    if not items:
        print(f"【{tag_name}】暂无快讯")
        return

    # --detail N 查看详情
    if detail_idx is not None:
        if detail_idx < 1 or detail_idx > len(items):
            print(f"序号超出范围，有效范围: 1-{len(items)}")
            return
        a = items[detail_idx - 1]
        item_seq = a.get("seq", "")
        a_title = a.get("title", "")
        summary = a.get("summary", "")
        a_type = a.get("type", 0)
        ts = a.get("createTime", 0)
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        read_amt = a.get("readAmount", 0)
        share_amt = a.get("shareAmount", 0)

        print(f"【{tag_name}快讯】{a_title}\n")
        if dt:
            print(f"  时间: {dt}")
        if read_amt:
            print(f"  阅读: {read_amt:,}  转发: {share_amt:,}")
        if summary:
            print(f"\n{summary}")

        # type=1 表示有文章详情，尝试获取全文
        if a_type == 1 and item_seq:
            detail = get(f"/api/article/{item_seq}")
            news = detail.get("data", {}).get("newsInfo", {})
            if news and news.get("content"):
                content = news.get("content", "")
                text = re.sub(r"<br\s*/?>", "\n", content)
                text = re.sub(r"<p[^>]*>", "\n", text)
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"&nbsp;", " ", text)
                text = re.sub(r"&[a-z]+;", "", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                print(f"\n{'─' * 60}\n全文:\n{text}")

        # 显示相关股票和概念
        stocks = a.get("stocks", [])
        fields = a.get("fields", [])
        if stocks:
            stock_names = [f"{s.get('name', '')}({s.get('stockCode', '')})" for s in stocks[:5]]
            print(f"\n  相关个股: {', '.join(stock_names)}")
        if fields:
            field_names = [f.get("name", "") for f in fields[:5]]
            print(f"  相关板块: {', '.join(field_names)}")
        return

    # 列表模式
    print(f"【{tag_name}】快讯  {len(items)} 条\n")
    for i, a in enumerate(items[:count], 1):
        ts = a.get("createTime", 0)
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        a_title = a.get("title", "")[:60]
        a_type = a.get("type", 0)
        read_amt = a.get("readAmount", 0)
        tags = a.get("tags", [])
        stocks = a.get("stocks", [])
        fields = a.get("fields", [])
        tag_names = [t.get("name", "") for t in tags]
        tag_str = f"  [{', '.join(tag_names)}]" if tag_names else ""
        stock_str = ""
        if stocks:
            stock_names = [s.get("name", "") for s in stocks[:3]]
            stock_str = f"  相关: {', '.join(stock_names)}"
        elif fields:
            field_names = [f.get("name", "") for f in fields[:3]]
            stock_str = f"  板块: {', '.join(field_names)}"
        type_mark = " [详]" if a_type == 1 else ""
        read_str = f"  {read_amt:,}阅读" if read_amt else ""

        print(f"  {i:2d}. {a_title}{type_mark}")
        print(f"      {dt}{tag_str}{stock_str}{read_str}")

    # 翻页提示
    last_seq = items[-1].get("seq", 0) if items else 0
    print(f"\n  提示: 使用 --detail N 查看第 N 条快讯详情")
    if last_seq:
        print(f"  提示: 使用 --seq {last_seq} 加载更早的快讯")


def cmd_ranking(args):
    """基金排行"""
    # 排序字段别名映射（支持多种常见命名方式）
    sort_type_aliases = {
        "rise1y": "year", "riseYear": "year", "1y": "year",           # 近一年
        "rise6m": "hyear", "riseHyear": "hyear", "6m": "hyear",       # 近半年
        "rise3m": "tmonth", "riseTmonth": "tmonth", "3m": "tmonth",   # 近三月
        "rise1m": "month", "riseMonth": "month", "1m": "month",       # 近一月
        "rise1w": "week", "riseWeek": "week", "1w": "week",           # 近一周
        "ytd": "nowyear", "riseYtd": "nowyear",                        # 今年以来
        "rise3y": "tyear", "riseTyear": "tyear", "3y": "tyear",       # 近三年
        "rise5y": "fyear", "riseFyear": "fyear", "5y": "fyear",       # 近五年
        "sharpe": "sharpeYear", "sharpe1y": "sharpeYear",             # 夏普比率
        "drawdown": "maxDrawDownYear", "dd": "maxDrawDownYear",       # 最大回撤
    }
    raw_sort_type = getattr(args, "sort_type", "year")
    sort_type = sort_type_aliases.get(raw_sort_type, raw_sort_type)

    params = {
        "sort_type": sort_type,
        "sort": getattr(args, "ranking_sort", "DESC"),
        "limit": args.count,
        "offset": getattr(args, "offset", 0),
    }

    board = getattr(args, "board", None)
    if board:
        params["board"] = board

    strategy = getattr(args, "strategy", None)
    if strategy:
        params["strategy"] = strategy

    min_scale = getattr(args, "min_scale", None)
    if min_scale:
        params["min_scale"] = min_scale

    # 如果使用了排行榜，从配置中获取实际 sortType 用于展示
    actual_sort_type = params["sort_type"]
    if board:
        try:
            board_config = get("/api/fund/ranking/boards")
            for b in board_config.get("data", {}).get("rankList", []):
                if b.get("name") == board or b.get("key") == board:
                    actual_sort_type = b.get("sortType", actual_sort_type)
                    break
        except Exception:
            pass

    data = post("/api/fund/ranking", params)

    # 解析返回数据: data[0].list
    data_list = data.get("data", [])
    if not data_list:
        print("  未获取到数据")
        return

    card_data = data_list[0] if data_list else {}
    fund_list = card_data.get("list", [])
    total = card_data.get("total", len(fund_list))

    title = "基金排行"
    if board:
        title = f"基金排行 - {board}"

    sort_type = actual_sort_type
    sort_label_map = {
        "year": "近一年", "hyear": "近半年", "tmonth": "近三月", "month": "近一月",
        "week": "近一周", "nowyear": "今年以来", "tyear": "近三年", "fyear": "近五年",
        "now": "成立以来", "sharpeYear": "夏普比率(年)", "automaticYear": "年化收益",
        "maxDrawDownYear": "最大回撤(年)",
    }
    sort_label = sort_label_map.get(sort_type, sort_type)

    print(f"\n{'=' * 80}")
    print(f"  {title}  (按{sort_label}排序, 共{total}只)")
    print(f"{'=' * 80}")

    # 表头
    header = f"{'序号':>4s}  {'代码':<8s}  {'名称':<20s}  {sort_label:>10s}  {'规模(亿)':>10s}"
    # 额外显示几个常用字段
    extra_cols = []
    if sort_type != "month":
        extra_cols.append(("month", "近一月"))
    if sort_type != "year":
        extra_cols.append(("year", "近一年"))
    if sort_type not in ("sharpeYear",):
        extra_cols.append(("sharpeYear", "夏普(年)"))
    if sort_type not in ("maxDrawDownYear",):
        extra_cols.append(("maxDrawDownYear", "回撤(年)"))

    for field, label in extra_cols:
        header += f"  {label:>10s}"
    print(header)
    print("-" * len(header))

    for idx, fund in enumerate(fund_list, 1):
        code = fund.get("tradeCode", "")
        name = fund.get("simpleName", "")
        # 排序字段的值
        sort_val = fund.get(sort_type)
        sort_str = fmt_pct(sort_val) if sort_type not in ("sharpeYear", "automaticYear") else (f"{float(sort_val):.4f}" if sort_val else "N/A")
        if sort_type == "maxDrawDownYear" and sort_val:
            sort_str = f"{float(sort_val):.2f}%"

        # 规模
        scale_raw = fund.get("fundScale")
        if scale_raw:
            scale = float(scale_raw) / 100000000
            scale_str = f"{scale:.2f}"
        else:
            scale_str = "N/A"

        row = f"{idx:>4d}  {code:<8s}  {name:<20s}  {sort_str:>10s}  {scale_str:>10s}"

        for field, label in extra_cols:
            v = fund.get(field)
            if field in ("sharpeYear", "automaticYear"):
                cell = f"{float(v):.4f}" if v else "N/A"
            elif field == "maxDrawDownYear":
                cell = f"{float(v):.2f}%" if v else "N/A"
            else:
                cell = fmt_pct(v)
            row += f"  {cell:>10s}"

        print(row)

        # 标签（可能是 JSON 字符串或 list）
        tags_raw = fund.get("fundTags", [])
        if isinstance(tags_raw, str):
            try:
                import json as _json
                tags_raw = _json.loads(tags_raw)
            except (ValueError, TypeError):
                tags_raw = []
        if tags_raw:
            tag_names = [t.get("content", "") for t in tags_raw if isinstance(t, dict) and t.get("content")]
            if tag_names:
                print(f"      标签: {', '.join(tag_names)}")

    print(f"\n  共显示 {len(fund_list)}/{total} 只基金")


def cmd_screen(args):
    """策略筛选"""
    # 本地策略定义（与 ths_fund_client.py 的 _STRATEGY_FILTERS 对应）
    local_strategies = {
        "fund0001": {"name": "年年正收益", "desc": "连续5年正收益，成立超5年"},
        "fund0002": {"name": "三年翻倍", "desc": "近3年涨幅超100%，成立超3年"},
        "fund0003": {"name": "十年十倍", "desc": "成立以来涨幅超1000%，成立超10年"},
        "fund0004": {"name": "十年绩优", "desc": "成立超10年，近3年收益排名前1/3"},
        "fund0005": {"name": "低回撤率", "desc": "近3年最大回撤<5%，成立超3年"},
        "fund0007": {"name": "高性价比", "desc": "近3年夏普比率排名前10%，成立超3年"},
        "fund0010": {"name": "能涨抗跌", "desc": "近3年收益前1/3且回撤<5%，成立超3年"},
        "fund0011": {"name": "机构偏爱", "desc": "机构持仓占比超80%"},
        "fund0012": {"name": "小规模大潜力", "desc": "规模2-30亿，近1年夏普排名前10%"},
    }

    strategy = getattr(args, "strategy", None)

    if not strategy:
        # 列出所有可用策略：先从远端获取，补充本地可执行标记
        print(f"\n{'=' * 78}")
        print(f"  可用筛选策略")
        print(f"{'=' * 78}")
        print(f"  {'key':<12s}  {'名称':<12s}  {'可执行':^6s}  {'描述'}")
        print(f"  {'-' * 74}")

        # 远端策略配置
        try:
            data = get("/api/fund/ranking/filters")
            remote_list = data.get("data", {}).get("filterList", [])
        except Exception:
            remote_list = []

        shown_keys = set()
        for item in remote_list:
            key = item.get("strategyKey", item.get("key", ""))
            name = item.get("strategyTitle", "")
            desc = item.get("strategyText", "")
            executable = "Y" if key in local_strategies else "-"
            print(f"  {key:<12s}  {name:<12s}  {executable:^6s}  {desc}")
            shown_keys.add(key)

        # 补充本地独有的策略
        for key, info in local_strategies.items():
            if key not in shown_keys:
                print(f"  {key:<12s}  {info['name']:<12s}  {'Y':^6s}  {info['desc']}")

        print(f"\n  Y = 可直接执行筛选,  - = 仅查看描述")
        print(f"  用法: python client.py screen --strategy <key>")
        print(f"  示例: python client.py screen --strategy fund0001")
        print(f"        python client.py screen --strategy fund0010 --count 50")
        return

    # 执行策略筛选
    count = args.count
    offset = getattr(args, "offset", 0)

    # 判断是否为可执行策略
    if strategy in local_strategies:
        info = local_strategies[strategy]
        print(f"\n  策略: {info['name']} ({strategy})")
        print(f"  条件: {info['desc']}")
        print(f"  正在筛选...")

        params = {
            "strategy": strategy,
            "limit": count,
            "offset": offset,
        }

        min_scale = getattr(args, "min_scale", None)
        if min_scale:
            params["min_scale"] = min_scale

        data = post("/api/fund/ranking", params)

        # 解析返回数据
        data_list = data.get("data", [])
        if not data_list:
            print("  未匹配到基金")
            return

        card_data = data_list[0] if data_list else {}
        fund_list = card_data.get("list", [])
        total = card_data.get("total", len(fund_list))

        # 根据策略选择显示的排序字段
        strategy_sort_map = {
            "fund0001": "year", "fund0002": "tyear", "fund0003": "now",
            "fund0004": "now", "fund0005": "maxDrawDownYear",
            "fund0007": "sharpeYear", "fund0010": "tyear",
            "fund0011": "year", "fund0012": "sharpeYear",
        }
        sort_type = strategy_sort_map.get(strategy, "year")

        sort_label_map = {
            "year": "近一年", "hyear": "近半年", "tmonth": "近三月", "month": "近一月",
            "week": "近一周", "nowyear": "今年以来", "tyear": "近三年", "fyear": "近五年",
            "now": "成立以来", "sharpeYear": "夏普比率(年)", "automaticYear": "年化收益",
            "maxDrawDownYear": "最大回撤(年)",
        }
        sort_label = sort_label_map.get(sort_type, sort_type)

        print(f"\n{'=' * 90}")
        print(f"  {info['name']} 筛选结果  (按{sort_label}排序, 共{total}只)")
        print(f"{'=' * 90}")

        # 表头：序号 + 代码 + 名称 + 主排序字段 + 规模 + 额外字段
        extra_cols = []
        if sort_type != "month":
            extra_cols.append(("month", "近一月"))
        if sort_type != "year":
            extra_cols.append(("year", "近一年"))
        if sort_type != "tyear":
            extra_cols.append(("tyear", "近三年"))
        if sort_type not in ("sharpeYear",):
            extra_cols.append(("sharpeYear", "夏普(年)"))
        if sort_type not in ("maxDrawDownYear",):
            extra_cols.append(("maxDrawDownYear", "回撤(年)"))
        # 最多显示4个额外列
        extra_cols = extra_cols[:4]

        header = f"{'序号':>4s}  {'代码':<8s}  {'名称':<20s}  {sort_label:>12s}  {'规模(亿)':>10s}"
        for _, label in extra_cols:
            header += f"  {label:>10s}"
        print(header)
        print("-" * len(header))

        for idx, fund in enumerate(fund_list, 1):
            code = fund.get("tradeCode", "")
            name = fund.get("simpleName", "")

            sort_val = fund.get(sort_type)
            if sort_type in ("sharpeYear", "automaticYear"):
                sort_str = f"{float(sort_val):.4f}" if sort_val else "N/A"
            elif sort_type == "maxDrawDownYear":
                sort_str = f"{float(sort_val):.2f}%" if sort_val else "N/A"
            else:
                sort_str = fmt_pct(sort_val)

            scale_raw = fund.get("fundScale")
            scale_str = f"{float(scale_raw) / 1e8:.2f}" if scale_raw else "N/A"

            row = f"{idx:>4d}  {code:<8s}  {name:<20s}  {sort_str:>12s}  {scale_str:>10s}"

            for field, _ in extra_cols:
                v = fund.get(field)
                if field in ("sharpeYear", "automaticYear"):
                    cell = f"{float(v):.4f}" if v else "N/A"
                elif field == "maxDrawDownYear":
                    cell = f"{float(v):.2f}%" if v else "N/A"
                else:
                    cell = fmt_pct(v)
                row += f"  {cell:>10s}"

            print(row)

        print(f"\n  共显示 {len(fund_list)}/{total} 只基金")
        if total > len(fund_list):
            print(f"  提示: 使用 --count {min(total, 100)} 查看更多, 或 --offset {len(fund_list)} 翻页")
    else:
        # 非本地策略，显示远端描述
        try:
            data = get("/api/fund/ranking/filters")
            remote_list = data.get("data", {}).get("filterList", [])
        except Exception:
            remote_list = []

        found = None
        for item in remote_list:
            if item.get("strategyKey") == strategy or item.get("key") == strategy:
                found = item
                break

        if found:
            print(f"\n  策略: {found.get('strategyTitle', strategy)}")
            print(f"  描述: {found.get('strategyText', '')}")
            print(f"  条件: {found.get('sentence', '')}")
            print(f"\n  该策略暂不支持直接执行（所需字段不可用）")
        else:
            print(f"\n  未找到策略: {strategy}")
            print(f"  使用 'python client.py screen' 查看可用策略列表")


def cmd_companies(args):
    """基金公司列表"""
    data = get("/api/fund/companies")

    # 数据可能是 list 或 dict（上游限流时返回 dict 错误信息）
    if isinstance(data, dict) and data.get("status_code") and data.get("status_code") != 0:
        print(f"  上游返回错误: {data.get('status_msg', '未知错误')}（稍后重试）")
        return

    company_list = data if isinstance(data, list) else data.get("data", data)
    if isinstance(company_list, dict):
        company_list = company_list.get("list", [])

    if not company_list:
        print("  未获取到基金公司数据")
        return

    print(f"\n{'=' * 60}")
    print(f"  基金公司列表 (共{len(company_list)}家)")
    print(f"{'=' * 60}")

    count = args.count if args.count != 10 else len(company_list)
    for idx, company in enumerate(company_list[:count], 1):
        if isinstance(company, dict):
            name = company.get("shortname", company.get("name", company.get("orgName", "")))
            orgid = company.get("orgid", company.get("id", ""))
            namesign = company.get("namesign", "")
            sign_str = f"  ({namesign})" if namesign else ""
            print(f"  {idx:>3d}. {name:<30s}  orgid: {orgid}{sign_str}")
        else:
            print(f"  {idx:>3d}. {company}")


def cmd_account(args):
    """交易账户总览"""
    data = get("/api/trade/overview")
    sd = data.get("singleData", {})
    code = data.get("code", "")
    if code != "0000":
        print(f"  请求失败: {data.get('message', code)}（token 可能已过期，需重新捕获）")
        return

    print(f"\n{'=' * 60}")
    print(f"  交易账户总览  (custId: {data.get('custId', '')})")
    print(f"{'=' * 60}")
    print(f"  总资产:           {sd.get('ov_total_sumvalue', '0')} 元")
    print(f"  累计盈亏:         {sd.get('ov_sumprofitloss', '0.00')} 元")
    print(f"  当日盈亏:         {sd.get('ov_sumdayprofitloss', '0.00')} 元")
    print(f"  活期宝余额:       {sd.get('ov_walletamount', '0')} 元")
    print(f"  冻结金额:         {sd.get('ov_freezeamount', '0')} 元")
    print(f"  持有基金数:       {sd.get('ov_sumcount', '0')}")
    print(f"  在途买入:         {sd.get('ov_buyamount', '0')} 元")
    print(f"  在途赎回:         {sd.get('redeemingAmount', '0')} 元")
    print(f"  银行卡数:         {sd.get('ov_bankcardnum', '0')}")
    print(f"  风险等级:         {sd.get('clientRiskRateText', '')} (等级{sd.get('clientRiskRate', '')})")
    print(f"  数据日期:         {sd.get('ov_newdate', '')}")


def cmd_positions(args):
    """基金持仓"""
    data = get("/api/trade/positions")
    code = data.get("code", "")
    if code != "0000":
        print(f"  请求失败: {data.get('message', code)}")
        return

    sd = data.get("singleData", {})
    general = sd.get("fundGeneral", {})
    positions = general.get("fundPositonCombinedList", [])

    # 获取账户总览（总资产、活期宝、盈亏）
    overview = get("/api/trade/overview")
    ov = overview.get("singleData", {}) if overview.get("code") == "0000" else {}

    # 获取待确认订单
    pending_amount = 0
    pending_count = 0
    try:
        orders_resp = get("/api/trade/orders?days=30&limit=50")
        orders_data = orders_resp.get("singleData", {}).get("data", [])
        for o in orders_data:
            if o.get("endFlag") == "0" and o.get("confirmFlag") == "0":
                pending_count += 1
                try:
                    pending_amount += float(o.get("totalFee", 0))
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    print(f"\n{'=' * 60}")
    print(f"  基金持仓  (custId: {data.get('custId', general.get('custId', ''))})")
    print(f"{'=' * 60}")

    # 资产概览
    total_asset = ov.get("ov_total_sumvalue", "")
    wallet_amount = ov.get("ov_walletamount", "")
    sum_profit = ov.get("ov_sumprofitloss", "")
    day_profit = ov.get("ov_sumdayprofitloss", "")

    if total_asset:
        print(f"  总资产:      {total_asset} 元")
    if general.get("sumValue") is not None:
        print(f"  持仓市值:    {general['sumValue']} 元")
    if wallet_amount:
        print(f"  活期宝:      {wallet_amount} 元")
    if pending_count > 0:
        print(f"  待确认:      {pending_amount:.2f} 元（{pending_count}笔）")
    if general.get("sumBuyAmount"):
        print(f"  总买入金额:  {general['sumBuyAmount']} 元")
    if sum_profit:
        print(f"  累计盈亏:    {sum_profit} 元")
    if day_profit:
        print(f"  当日盈亏:    {day_profit} 元")
    print(f"  数据日期:    {general.get('sumIncomeDate', '')}")

    if not positions:
        print("\n  暂无持仓基金")
    else:
        print(f"\n  {'序号':>4s}  {'基金代码':<8s}  {'基金名称':<20s}  {'市值':>10s}  {'买入金额':>10s}  {'持仓收益':>10s}")
        print(f"  {'─' * 70}")
        for i, pos in enumerate(positions, 1):
            fund_code = pos.get("fundCode") or ""
            fund_name = pos.get("fundName") or ""
            value = str(pos.get("shareValue") if pos.get("shareValue") else pos.get("value") or "0")
            buy_amount = str(pos.get("buyAmount") or "")
            income = str(pos.get("holdIncome") or "0")
            print(f"  {i:>4d}  {fund_code:<8s}  {fund_name:<20s}  {value:>10s}  {buy_amount:>10s}  {income:>10s}")


def cmd_wallet(args):
    """活期宝/钱包"""
    info = get("/api/trade/wallet")
    home = get("/api/trade/wallet/home")

    print(f"\n{'=' * 60}")
    print(f"  活期宝 & 钱包")
    print(f"{'=' * 60}")

    # 活期宝信息
    if info.get("code") == "0000":
        sd = info.get("singleData", {})
        print(f"\n  [活期宝]")
        print(f"  基金名称:    {sd.get('fundName', '')}")
        print(f"  基金代码:    {sd.get('fundCode', '')}")
        print(f"  可用余额:    {sd.get('avaiableVol', '0')} 元")
        print(f"  累计收益:    {sd.get('profits', '0.00')} 元")
        print(f"  持有收益:    {sd.get('holdProfits', '0.00')} 元")
        print(f"  冻结金额:    {sd.get('freezeMoney', '0.00')} 元")
        print(f"  七日年化:    {sd.get('ratio', '')}%")
        print(f"  昨日收益日:  {sd.get('yesterdayIncome', '')}")

    # 钱包首页
    if home.get("code") == "0000":
        hd = home.get("data", {})
        print(f"\n  [钱包]")
        print(f"  可用余额:    {hd.get('avaiableVol', '0')} 元")
        print(f"  累计收益:    {hd.get('profits', '0.00')} 元")
        print(f"  持有收益:    {hd.get('holdProfits', '0.00')} 元")
        print(f"  冻结金额:    {hd.get('freezeMoney', '0')} 元")


def cmd_autoinvest(args):
    """定投计划"""
    summary = get("/api/trade/autoinvest/summary")
    invest_list = get("/api/trade/autoinvest/list")

    print(f"\n{'=' * 60}")
    print(f"  定投计划")
    print(f"{'=' * 60}")

    if summary.get("code") == "0000":
        sd = summary.get("data", {})
        print(f"  定投总金额:    {sd.get('totalAmount', 0)} 元")
        print(f"  累计定投天数:  {sd.get('totalDays', 0)} 天")
        print(f"  正常计划数:    {sd.get('normalProtocolCount', 0)}")
        print(f"  暂停计划数:    {sd.get('pauseProtocolCount', 0)}")
        next_date = sd.get("nextExecuteDate")
        if next_date:
            print(f"  下次执行日期:  {next_date}")

    if invest_list.get("code") == "0000":
        items = invest_list.get("data", {}).get("dataList", [])
        total = invest_list.get("data", {}).get("totalCount", 0)
        print(f"\n  定投计划列表 (共{total}条):")
        if not items:
            print("  暂无定投计划")
        else:
            for i, item in enumerate(items, 1):
                fund_name = item.get("fundName", "")
                fund_code = item.get("fundCode", "")
                amount = item.get("amount", "")
                status = item.get("protocolStatus", "")
                print(f"  {i}. {fund_name}({fund_code})  金额: {amount}  状态: {status}")


def cmd_trade_binding(args):
    """账户绑定信息"""
    data = get("/api/trade/binding")
    code = data.get("code", "")
    if code != "0000":
        print(f"  请求失败: {data.get('message', code)}")
        return

    bindings = data.get("data", [])
    print(f"\n{'=' * 60}")
    print(f"  账户绑定信息")
    print(f"{'=' * 60}")

    if not bindings:
        print("  无绑定信息")
    else:
        for b in bindings:
            print(f"  客户ID:    {b.get('custId', '')}")
            print(f"  姓名:      {b.get('investorName', '')}")
            print(f"  用户ID:    {b.get('userId', '')}")
            print(f"  证件号:    {b.get('certificateNo', '')}")


def cmd_trade_all(args):
    """全部交易账户数据"""
    data = get("/api/trade/all")

    print(f"\n{'=' * 60}")
    print(f"  交易账户数据汇总")
    print(f"{'=' * 60}")

    # 账户总览
    overview = data.get("account_overview", {})
    if overview.get("code") == "0000":
        sd = overview.get("singleData", {})
        print(f"\n  [账户总览]")
        print(f"  总资产:     {sd.get('ov_total_sumvalue', '0')} 元")
        print(f"  累计盈亏:   {sd.get('ov_sumprofitloss', '0.00')} 元")
        print(f"  当日盈亏:   {sd.get('ov_sumdayprofitloss', '0.00')} 元")
        print(f"  持有基金数: {sd.get('ov_sumcount', '0')}")
        print(f"  风险等级:   {sd.get('clientRiskRateText', '')}")
    elif "error" in overview:
        print(f"\n  [账户总览] 错误: {overview['error']}")

    # 持仓
    positions = data.get("fund_positions", {})
    if positions.get("code") == "0000":
        general = positions.get("singleData", {}).get("fundGeneral", {})
        pos_list = general.get("fundPositonCombinedList", [])
        print(f"\n  [基金持仓] 共{len(pos_list)}只基金")
        if general.get("sumValue"):
            print(f"  持仓总市值: {general['sumValue']} 元")

    # 活期宝
    wallet = data.get("wallet_info", {})
    if wallet.get("code") == "0000":
        sd = wallet.get("singleData", {})
        print(f"\n  [活期宝]")
        print(f"  {sd.get('fundName', '')}({sd.get('fundCode', '')})")
        print(f"  可用余额: {sd.get('avaiableVol', '0')} 元  七日年化: {sd.get('ratio', '')}%")

    # 钱包
    wallet_home = data.get("wallet_home", {})
    if wallet_home.get("code") == "0000":
        hd = wallet_home.get("data", {})
        print(f"\n  [钱包]  可用: {hd.get('avaiableVol', '0')} 元  累计收益: {hd.get('profits', '0.00')} 元")

    # 定投
    auto_summary = data.get("auto_invest_summary", {})
    if auto_summary.get("code") == "0000":
        sd = auto_summary.get("data", {})
        total = sd.get("normalProtocolCount", 0) + sd.get("pauseProtocolCount", 0)
        print(f"\n  [定投] 共{total}个计划  正常: {sd.get('normalProtocolCount', 0)}  暂停: {sd.get('pauseProtocolCount', 0)}")

    # 绑定信息
    binding = data.get("account_binding", {})
    if binding.get("code") == "0000":
        bindings = binding.get("data", [])
        if bindings:
            b = bindings[0]
            print(f"\n  [账户] {b.get('investorName', '')}  custId: {b.get('custId', '')}  userId: {b.get('userId', '')}")


def cmd_set_password(args):
    """设置交易密码"""
    import hashlib
    raw = args.code
    if not raw:
        print("错误: 请提供交易密码，如: python client.py set_password ruan19980418", file=sys.stderr)
        sys.exit(1)
    # 如果已经是 32 位十六进制则直接用，否则当明文算 MD5
    if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
        password_md5 = raw.upper()
    else:
        password_md5 = hashlib.md5(raw.encode()).hexdigest().upper()
    resp = post("/api/trade/password", {"password_md5": password_md5})
    print(f"交易密码已设置 (MD5: {password_md5})")


def cmd_buy_limits(args):
    """查询基金购买限制（最低/最高购买额）"""
    import json as _json
    fund_code = args.code
    resp = get(f"/api/trade/buy_limits/{fund_code}")
    print(_json.dumps(resp, ensure_ascii=False, indent=2))


def cmd_buy(args):
    """买入基金"""
    fund_code = args.code
    amount = args.amount
    if not amount or amount <= 0:
        print("错误: 请通过 --amount 指定买入金额（元），如: python client.py buy 001668 --amount 10", file=sys.stderr)
        sys.exit(1)

    use_wallet = not args.no_wallet

    print(f"正在买入 {fund_code}，金额 {amount:.2f} 元，活期宝支付: {'是' if use_wallet else '否'}")
    print("执行中（初始化→检查→下单）...")

    req_body = {
        "fund_code": fund_code,
        "amount": amount,
        "use_wallet": use_wallet,
    }
    if args.password:
        req_body["password"] = args.password

    try:
        resp = post("/api/trade/buy", req_body)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        print(f"买入失败: {detail or e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"  买入提交成功")
    print(f"{'=' * 50}")
    print(f"  基金代码:   {resp.get('fund_code', '')}")
    print(f"  基金名称:   {resp.get('fund_name', '')}")
    print(f"  买入金额:   {resp.get('amount', '')} 元")
    print(f"  手续费:     {resp.get('charge', '0.00')} 元")
    print(f"  银行卡:     {resp.get('bank_name', '')}")
    print(f"  订单号:     {resp.get('app_sheet_serial_no', '')}")
    print(f"  受理时间:   {resp.get('accept_time', '')}")
    print(f"  确认日期:   {resp.get('confirm_date', '')}")
    order_no = resp.get("app_sheet_serial_no", "")
    if order_no:
        print(f"\n  查询订单: python client.py order {order_no}")


def cmd_sell(args):
    """赎回基金"""
    fund_code = args.code
    if not fund_code:
        print("错误: 请提供基金代码，如: python client.py sell 001668 --all", file=sys.stderr)
        sys.exit(1)

    req_body = {"fund_code": fund_code}
    if hasattr(args, 'shares') and args.shares:
        req_body["share_vol"] = args.shares
    if hasattr(args, 'sell_all') and args.sell_all:
        req_body["sell_all"] = True
    if args.password:
        req_body["password"] = args.password

    # 如果既没指定 --shares 也没指定 --all，默认全部赎回
    if "share_vol" not in req_body and "sell_all" not in req_body:
        req_body["sell_all"] = True

    action_desc = f"全部赎回" if req_body.get("sell_all") else f"赎回 {req_body.get('share_vol', '')} 份"
    print(f"正在赎回基金 {fund_code}（{action_desc}）...")

    try:
        resp = post("/api/trade/sell", req_body)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        print(f"赎回失败: {detail or e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"  赎回信息")
    print(f"{'=' * 50}")
    print(f"  基金代码:   {resp.get('fund_code', '')}")
    print(f"  基金名称:   {resp.get('fund_name', '')}")
    print(f"  赎回份额:   {resp.get('share_vol', '')} 份")
    print(f"  可用份额:   {resp.get('available_vol', '')} 份")

    redeem_result = resp.get("redeem_result", {})
    code = redeem_result.get("code", "")
    if code == "0000":
        print(f"\n  赎回结果:   成功")
        redeem_data = redeem_result.get("data", {})
        if redeem_data.get("expectedArriveDate"):
            print(f"  预计到账:   {redeem_data['expectedArriveDate']}")
        if redeem_data.get("appSheetSerialNo"):
            print(f"  订单号:     {redeem_data['appSheetSerialNo']}")
    else:
        msg = redeem_result.get("message", "")
        print(f"\n  赎回结果:   失败 ({code}: {msg})")


def cmd_order(args):
    """查询交易订单"""
    order_no = args.code  # code 位置传入的是订单号
    resp = get(f"/api/trade/order/{order_no}")
    data = resp.get("data", resp)

    print(f"\n{'=' * 50}")
    print(f"  订单详情")
    print(f"{'=' * 50}")
    print(f"  订单号:     {data.get('appSheetSerialNo', order_no)}")
    print(f"  基金代码:   {data.get('fundCode', '')}")
    print(f"  基金名称:   {data.get('fundName', '')}")
    print(f"  交易类型:   {data.get('businessName', '')}")
    print(f"  金额:       {data.get('applicationAmount', '')} 元")
    print(f"  状态:       {data.get('statusName', '')}")
    print(f"  受理时间:   {data.get('acceptTime', '')}")
    print(f"  确认日期:   {data.get('confirmDate', '')}")
    if data.get("expectedArriveDate"):
        print(f"  预计到账:   {data.get('expectedArriveDate', '')}")
    if data.get("charge"):
        print(f"  手续费:     {data.get('charge', '')} 元")


def cmd_orders(args):
    """查询交易订单列表"""
    days = getattr(args, "count", 10)
    # 用 count 参数控制天数不太直觉，这里固定查30天，count控制显示条数
    params = {"days": 30, "limit": 50}
    resp = get(f"/api/trade/orders?days={params['days']}&limit={params['limit']}")
    single_data = resp.get("singleData", resp)
    orders = single_data.get("data", [])

    if not orders:
        print("近30天无交易订单")
        return

    print(f"\n交易订单列表（近30天）\n")
    print(f"{'序号':<4s}  {'基金代码':<8s}  {'基金名称':<28s}  {'金额':>8s}  {'操作类型':<10s}  {'受理时间':<20s}  {'状态'}")
    print("-" * 110)

    for i, o in enumerate(orders, 1):
        fund_code = o.get("fundCode") or ""
        fund_name = o.get("fundName") or ""
        # 截断过长的基金名称
        if len(fund_name) > 14:
            fund_name = fund_name[:13] + "…"
        total_fee = o.get("totalFee") or "0"
        op_type = o.get("opTypeSecondMsg") or o.get("opTypeOneMsg") or ""
        accept_time = o.get("acceptTime", "")
        end_flag = o.get("endFlag", "")
        process_status = o.get("processStatus", "")
        final_status = o.get("finalStatus", "")
        confirm_flag = o.get("confirmFlag", "")

        # 状态判断
        if final_status == "5":
            status = "已撤单"
        elif end_flag == "1":
            if confirm_flag == "1":
                status = "已确认"
            else:
                status = "已结束"
        elif end_flag == "0" and process_status == "1":
            status = "处理中 [可撤]"
        else:
            status = "处理中"

        order_no = o.get("appSheetSerialNo", "")
        print(f"{i:<4d}  {fund_code:<8s}  {fund_name:<28s}  {total_fee:>7s}元  {op_type:<10s}  {accept_time:<20s}  {status}")

    print("-" * 110)
    print(f"提示: 撤单命令 python client.py cancel <订单号>")
    print(f"      查看订单号请在上方列表中找到对应订单")

    # 列出可撤的订单号
    cancelable = [o for o in orders if o.get("endFlag") == "0" and o.get("processStatus") == "1"]
    if cancelable:
        print(f"\n可撤订单:")
        for o in cancelable:
            print(f"  python client.py cancel {o.get('appSheetSerialNo', '')}")


def cmd_cancel(args):
    """撤销交易订单"""
    order_no = args.code  # code 位置传入的是订单号
    if not order_no:
        print("错误: 请提供订单号，如: python client.py cancel 00000000002768534282", file=sys.stderr)
        sys.exit(1)

    print(f"正在撤销订单 {order_no}...")

    req_body = {"order_no": order_no}
    if args.password:
        req_body["password"] = args.password

    try:
        resp = post("/api/trade/cancel", req_body)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        print(f"撤单失败: {detail or e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"  订单信息")
    print(f"{'=' * 50}")
    print(f"  基金代码:   {resp.get('fund_code', '')}")
    print(f"  基金名称:   {resp.get('fund_name', '')}")
    print(f"  金额:       {resp.get('amount', '')} 元")
    print(f"  订单号:     {resp.get('order_no', order_no)}")
    revoke_result = resp.get("revoke_result", {})
    code = revoke_result.get("code", "")
    if code == "0000":
        print(f"\n  撤单结果:   成功")
    else:
        msg = revoke_result.get("message", "")
        print(f"\n  撤单结果:   失败 ({code}: {msg})")


def cmd_compare(args):
    """同类基金横向对比"""
    with_codes = getattr(args, "with_funds", None)

    if with_codes:
        # 手动指定模式
        compare_codes = [c.strip() for c in with_codes.split(",") if c.strip()]
        all_codes = [args.code] + compare_codes
        print(f"横向对比: {args.code} vs {', '.join(compare_codes)}\n")
    else:
        # 自动发现模式
        print(f"正在发现与 {args.code} 同赛道的基金...\n")
        similar = get(f"/api/fund/{args.code}/similar?top_n=5")
        if not similar:
            print("未找到同赛道基金")
            return
        print(f"找到 {len(similar)} 只同赛道基金:")
        for f in similar:
            sim_pct = f"{f['similarity'] * 100:.1f}%" if f.get("similarity") else "-"
            ret = f"{f['return_1y']:+.2f}%" if f.get("return_1y") is not None else "-"
            print(f"  {f['code']} {f['name']}  相似度:{sim_pct}  近一年:{ret}")
        compare_codes = [f["code"] for f in similar]
        all_codes = [args.code] + compare_codes
        print()

    # 获取所有基金的对比数据
    print(f"正在获取 {len(all_codes)} 只基金的对比数据...\n")
    data = get(f"/api/funds/compare?codes={','.join(all_codes)}")
    if not data:
        print("获取对比数据失败")
        return

    # 建立索引
    fund_map = {f["code"]: f for f in data}
    target = fund_map.get(args.code, {})

    # 计算重仓股重合度
    target_stocks = set(target.get("top10_stocks", []))

    # 输出对比表
    # 为了紧凑，用多行对比格式
    cols = [fund_map.get(c, {"code": c}) for c in all_codes]
    col_width = 16

    def _row(label, key, fmt_func=None):
        parts = [f"{label:<14s}"]
        for c in cols:
            v = c.get(key)
            if fmt_func:
                s = fmt_func(v)
            elif v is None:
                s = "-"
            else:
                s = str(v)
            parts.append(f"{s:>{col_width}s}")
        print("".join(parts))

    # 表头
    header = f"{'指标':<14s}"
    for c in cols:
        name = c.get("name", c.get("code", ""))
        # 截断过长的名称
        if len(name) > col_width - 2:
            name = name[:col_width - 3] + ".."
        code_tag = f"[{c.get('code', '')}]"
        header += f"{code_tag:>{col_width}s}"
    print(header)
    # 名称行
    name_row = f"{'名称':<14s}"
    for c in cols:
        name = c.get("name", "-")
        if len(name) > col_width - 1:
            name = name[:col_width - 2] + ".."
        name_row += f"{name:>{col_width}s}"
    print(name_row)
    print("=" * (14 + col_width * len(cols)))

    # 基本面
    _row("基金经理", "manager")
    _row("规模(亿)", "scale", lambda v: f"{v:.2f}" if v is not None else "-")
    _row("成立日期", "establish_date", lambda v: v if v else "-")

    print("-" * (14 + col_width * len(cols)))

    # 收益
    _row("近一年", "return_1y", lambda v: f"{v:+.2f}%" if v is not None else "-")
    _row("近三年", "return_3y", lambda v: f"{v:+.2f}%" if v is not None else "-")
    _row("成立以来", "return_since", lambda v: f"{v:+.2f}%" if v is not None else "-")
    _row("年化收益", "annual_return", lambda v: f"{v:+.2f}%" if v is not None else "-")

    print("-" * (14 + col_width * len(cols)))

    # 风险
    _row("最大回撤(年)", "max_drawdown_1y", lambda v: f"-{v:.2f}%" if v is not None else "-")
    _row("夏普比率(年)", "sharpe_1y", lambda v: f"{v:.4f}" if v is not None else "-")

    print("-" * (14 + col_width * len(cols)))

    # 持仓
    _row("主要行业", "top_industry")
    _row("持仓集中度", "concentration", lambda v: f"{v:.1f}%" if v is not None else "-")
    _row("机构占比", "org_ratio", lambda v: f"{v:.1f}%" if v is not None else "-")

    # 重仓股重合度
    if target_stocks:
        overlap_row = f"{'重仓股重合':<14s}"
        for c in cols:
            if c.get("code") == args.code:
                overlap_row += f"{'(基准)':>{col_width}s}"
            else:
                other_stocks = set(c.get("top10_stocks", []))
                common = target_stocks & other_stocks
                overlap_row += f"{f'{len(common)}/10':>{col_width}s}"
        print(overlap_row)

    print("=" * (14 + col_width * len(cols)))


def cmd_all(args):
    """获取全部信息"""
    print("=" * 50)
    print(f"  基金 {args.code} 全部信息")
    print("=" * 50)

    print("\n【基本信息】")
    cmd_detail(args)

    print("\n【产品详情】")
    cmd_product(args)

    print("\n【阶段排名】")
    cmd_rank(args)

    print("\n【年度收益】")
    cmd_year_return(args)

    print("\n【最大回撤】")
    cmd_drawdown(args)

    print("\n【收益稳定度】")
    cmd_stability(args)

    print("\n【基金经理】")
    cmd_manager(args)

    print("\n【持仓概览】")
    cmd_hold_overview(args)

    print("\n【前十大持仓与估值】")
    cmd_holdings(args, with_valuation=True)

    print("\n【持仓变动】")
    cmd_position_change(args)

    print("\n【盈亏贡献】")
    cmd_profit(args)

    print("\n【投资风格】")
    cmd_style(args)

    print("\n【资产配置】")
    cmd_asset(args)

    print("\n【RSI 指标】")
    cmd_rsi(args)

    print("\n【交易规则与费率】")
    cmd_trade_rule(args)

    print("\n【规模变动】")
    cmd_scale_change(args)

    print("\n【机构持仓比例】")
    cmd_holder_ratio(args)

    print("\n【分红历史】")
    cmd_dividend(args)

    print("\n【最新公告】")
    cmd_announcements(args)

    print("\n【相关资讯】")
    cmd_news(args)

    print("\n【历史净值】")
    cmd_nav(args)

    print("\n【持仓估值百分位】")
    cmd_pe_percentile(args)

    print("\n【净值技术面】")
    cmd_nav_technical(args)

    print("\n【大盘环境】")
    cmd_market(args)

    print("\n【资金流向】")
    cmd_fund_flow(args)

    print("\n【同类基金对比】")
    cmd_compare(args)


def main():
    parser = argparse.ArgumentParser(
        description="同花顺基金数据查询客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python client.py 006888                                      # 获取全部信息
  python client.py 006888 holdings                             # 前十大持仓
  python client.py 006888 nav --period month --count 30        # 近一月净值
  python client.py 006888 profit --time-type year              # 近一年盈亏贡献
  python client.py 006888 announcements --cat report           # 业绩报告公告
  python client.py 006888 announcements --cat change --page 2  # 变更公告第2页
  python client.py 006888 news --count 15                      # 相关资讯
  python client.py 006888 compare                              # 自动发现同赛道基金对比
  python client.py 006888 compare --with 022364,018956         # 手动指定对比基金
  python client.py 006888 pe_percentile                        # 持仓股估值百分位（近3年）
  python client.py 006888 pe_percentile --years 5              # 近5年估值百分位
  python client.py 006888 nav_technical                        # 净值技术面分析
  python client.py 006888 market                               # 大盘与行业环境
  python client.py 006888 fund_flow                            # 资金流向趋势
  python client.py hotlist                                     # A股个股/概念/行业/ETF热榜
  python client.py hotlist --market hk                         # 港股热榜
  python client.py hotlist --count 20                          # 显示前20名
  python client.py hotlist_topics                              # 热榜话题
  python client.py hotlist_posts                               # 热门文章
  python client.py news_themes                                 # 新闻主题分类列表（19个热门板块）
  python client.py theme_articles --theme TZ-11385             # 小金属主题文章列表
  python client.py theme_articles --theme TZ-11385 --detail 2  # 小金属第2篇文章全文
  python client.py theme_articles --theme TZ-11907 --count 20  # 人形机器人主题前20篇
  python client.py theme_articles --theme TZ-669 --page 2      # 有色金属主题第2页
  python client.py headlines                                   # 推荐头条
  python client.py headlines --detail 1                        # 第1条头条详情
  python client.py flash_news                                  # 查看快讯分类列表
  python client.py flash_news --tag 重要                        # 重要快讯
  python client.py flash_news --tag 期货                        # 期货快讯
  python client.py flash_news --tag 62857 --detail 3            # 第3条重要快讯详情
  python client.py flash_news --tag a股 --seq 674998305         # A股快讯翻页
  python client.py news_feed                                   # 滚动快讯
  python client.py news_feed --page 2                          # 快讯第2页
  python client.py ranking                                     # 基金排行（近一年涨幅TOP30）
  python client.py ranking --board 涨幅榜                       # 预设排行榜
  python client.py ranking --sort-type sharpeYear               # 夏普比率排行
  python client.py screen                                      # 列出筛选策略
  python client.py screen --strategy fund0001                  # 年年正收益策略
  python client.py companies                                   # 基金公司列表

  === 交易账户（不需要基金代码） ===
  python client.py account                                     # 账户总览（总资产/盈亏/风险等级）
  python client.py positions                                   # 基金持仓列表
  python client.py wallet                                      # 活期宝/钱包
  python client.py autoinvest                                  # 定投计划
  python client.py trade_binding                               # 账户绑定信息
  python client.py trade_all                                   # 全部交易数据汇总

  === 基金交易 ===
  python client.py set_password ruan19980418                       # 预设交易密码（明文）
  python client.py buy 001668 --amount 10 --password ruan19980418 # 买入时直接传密码
  python client.py buy 001668 --amount 10                         # 使用预设密码买入
  python client.py buy 001668 --amount 100 --no-wallet            # 不使用活期宝
  python client.py order 00000000002768500381                     # 查询订单详情
  python client.py orders                                         # 查看近30天订单列表
  python client.py sell 001668 --all                              # 全部赎回
  python client.py sell 001668 --shares 50                        # 赎回50份
  python client.py sell 001668 --all --password ruan19980418      # 带密码全部赎回
  python client.py cancel 00000000002768534282                    # 撤销指定订单
  python client.py cancel 00000000002768534282 --password xxx     # 带密码撤单
""",
    )
    # 热榜命令不需要基金代码，用 nargs="?" 让 code 可选
    HOTLIST_CMDS = {"hotlist", "hotlist_topics", "hotlist_posts", "headlines", "news_feed", "news_themes", "theme_articles", "flash_news", "market_overview", "stock_rank", "sector_rank", "hot_board", "dragon_tiger", "ths_dragon_tiger", "capital_flow", "currency", "yesterday_limit", "stock_changes", "market_changes", "ranking", "screen", "companies", "stock_quote", "stock_kline", "stock_flow", "stock_valuation", "stock_financial", "account", "positions", "wallet", "autoinvest", "trade_binding", "trade_all", "buy", "sell", "order", "set_password", "orders", "cancel"}
    ALL_CHOICES = ["all", "detail", "product", "rank", "year_return", "drawdown", "stability",
                   "hold_overview", "holdings", "valuation", "position", "profit", "style", "asset",
                   "nav", "realtime", "manager", "rsi",
                   "trade_rule", "scale_change", "holder_ratio", "dividend",
                   "announcements", "news", "compare", "pe_percentile",
                   "nav_technical", "market", "fund_flow",
                   "hotlist", "hotlist_topics", "hotlist_posts", "headlines", "news_feed",
                   "news_themes", "theme_articles", "flash_news", "market_overview",
                   "stock_rank", "sector_rank", "hot_board", "dragon_tiger", "ths_dragon_tiger",
                   "capital_flow", "currency", "yesterday_limit", "stock_changes", "market_changes",
                   "ranking", "screen", "companies",
                   "stock_quote", "stock_kline", "stock_flow", "stock_valuation", "stock_financial",
                   "account", "positions", "wallet", "autoinvest", "trade_binding", "trade_all",
                   "buy_limits", "buy", "sell", "order", "set_password", "orders", "cancel"]

    parser.add_argument("code", nargs="?", default=None, help="基金代码（热榜命令可省略）")
    parser.add_argument(
        "action",
        nargs="?",
        default="all",
        choices=ALL_CHOICES,
        help="查询类型 (默认 all)",
    )
    # 额外位置参数，用于 set_password/order 等命令传参（如密码、订单号）
    parser.add_argument("extra_arg", nargs="?", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--period", default="year", help="净值走势周期: year/month/nowyear")
    parser.add_argument("--count", type=int, default=10, help="显示条数 (默认 10)")
    parser.add_argument("--time-type", default="threeMonth", help="盈亏贡献周期: threeMonth/halfYear/year")
    parser.add_argument("--cat", default="all", choices=["all", "report", "dividend", "change", "operation", "other"],
                        help="公告分类: all/report/dividend/change/operation/other")
    parser.add_argument("--page", type=int, default=1, help="公告页码 (默认 1)")
    parser.add_argument("--with", dest="with_funds", default=None,
                        help="对比基金代码，逗号分隔（适用于 compare）")
    parser.add_argument("--years", type=int, default=3, help="估值百分位回溯年数 (默认 3)")
    parser.add_argument("--market", default="a", choices=["a", "hk", "us"],
                        help="热榜市场: a=A股, hk=港股, us=美股")
    parser.add_argument("--topic", type=int, default=None,
                        help="查看第 N 个话题的详细内容（适用于 hotlist_topics）")
    parser.add_argument("--detail", type=int, default=None,
                        help="查看第 N 条头条的详细内容（适用于 headlines）")
    parser.add_argument("--theme", default=None,
                        help="主题ID，如 TZ-11385（适用于 theme_articles）")
    parser.add_argument("--tag", default=None,
                        help="快讯分类: a股/重要/公告/期货/异动/港股/美股 或数字ID（适用于 flash_news）")
    parser.add_argument("--seq", type=int, default=0,
                        help="快讯翻页游标，传上一页最后一条的seq（适用于 flash_news）")
    parser.add_argument("--change-type", default="all",
                        help="异动类型: all/竞价/拉升/跳水/大单/涨停/跌停/火箭发射等（适用于 stock_changes）")
    parser.add_argument("--sort", default="rise",
                        choices=["rise", "fall", "volume", "turnover", "amplitude", "turnover_rate"],
                        help="排序方式（适用于 stock_rank）")
    parser.add_argument("--sector-type", default="concept", choices=["concept", "industry"],
                        help="板块类型: concept=概念, industry=行业（适用于 sector_rank/hot_board）")
    parser.add_argument("--hot-sort", default="rise", choices=["rise", "flow", "5day"],
                        help="热点板块排序: rise=今日涨幅, flow=资金流入, 5day=5日涨幅（适用于 hot_board）")
    parser.add_argument("--dt-tab", default="stock", choices=["stock", "dept", "org"],
                        help="龙虎榜维度: stock=个股明细, dept=活跃营业部, org=机构买卖")
    parser.add_argument("--dt-days", type=int, default=3,
                        help="龙虎榜回溯天数 (默认 3)")
    parser.add_argument("--ths-dt-tab", default="youzi",
                        choices=["youzi", "jigou", "gfgs", "all"],
                        help="同花顺龙虎榜维度: youzi=游资, jigou=机构, gfgs=跟风高手, all=全部")
    parser.add_argument("--cf-tab", default="market", choices=["market", "north"],
                        help="资金流向维度: market=大盘资金, north=北向资金")
    parser.add_argument("--cf-days", type=int, default=20,
                        help="资金流向回溯天数 (默认 20)")
    parser.add_argument("--currency-tab", default="usdcny", choices=["usdcny", "shibor"],
                        help="货币风向: usdcny=美元/离岸人民币, shibor=Shibor利率+LPR")
    parser.add_argument("--currency-days", type=int, default=120,
                        help="货币风向回溯天数 (默认 120)")
    parser.add_argument("--stock-period", default="day", choices=["day", "week", "month"],
                        help="K线周期: day=日K, week=周K, month=月K (适用于 stock_kline)")
    parser.add_argument("--stock-days", type=int, default=20,
                        help="个股资金流回溯天数 (默认 20，适用于 stock_flow)")
    parser.add_argument("--sort-type", default="year",
                        help="排行排序字段: year/hyear/tmonth/month/week/nowyear/tyear/fyear/sharpeYear/maxDrawDownYear (适用于 ranking/screen)")
    parser.add_argument("--ranking-sort", default="DESC", choices=["DESC", "ASC"],
                        help="排行排序方向: DESC=降序, ASC=升序 (适用于 ranking)")
    parser.add_argument("--board", default=None,
                        help="排行榜名称: 涨幅榜/反弹榜/人气榜/加仓榜/超额榜 (适用于 ranking)")
    parser.add_argument("--strategy", default=None,
                        help="筛选策略 key: fund0001=年年正收益/fund0002=三年翻倍等 (适用于 screen)")
    parser.add_argument("--min-scale", type=float, default=None,
                        help="最小规模（元），如 100000000=10亿 (适用于 ranking)")
    parser.add_argument("--offset", type=int, default=0,
                        help="翻页偏移量 (适用于 ranking)")
    parser.add_argument("--amount", type=float, default=None,
                        help="买入金额（元）(适用于 buy)")
    parser.add_argument("--no-wallet", action="store_true", default=False,
                        help="不使用活期宝支付 (适用于 buy)")
    parser.add_argument("--password", default=None,
                        help="交易密码（明文），优先级高于 set_password (适用于 buy/sell/cancel)")
    parser.add_argument("--shares", type=float, default=None,
                        help="赎回份额数量 (适用于 sell)")
    parser.add_argument("--all", dest="sell_all", action="store_true", default=False,
                        help="全部赎回 (适用于 sell)")

    # 预处理 argv：对 "buy <code> ..."、"set_password <pwd>"、"order <no>" 这类命令
    # 第二个位置参数不在 ALL_CHOICES 中会导致 argparse 报错，需要提前挪走
    _argv = sys.argv[1:]
    _CMD_WITH_ARG = {"buy", "sell", "order", "set_password", "cancel"}
    _extra_cmd_arg = None
    if (len(_argv) >= 2
            and _argv[0] in _CMD_WITH_ARG
            and not _argv[1].startswith("-")
            and _argv[1] not in ALL_CHOICES):
        _extra_cmd_arg = _argv[1]
        _argv = [_argv[0]] + _argv[2:]

    args = parser.parse_args(_argv)
    if _extra_cmd_arg is not None:
        args.extra_arg = _extra_cmd_arg

    # 处理带额外参数的命令：buy <code> / order <no> / set_password <pwd>
    # argparse 把命令名解析到 code，额外参数解析到 action 或 extra_arg
    # 必须在通用 hotlist 处理之前，因为 _CMD_WITH_ARG 也在 ALL_CHOICES 中
    if args.code in _CMD_WITH_ARG:
        cmd = args.code
        arg_val = args.extra_arg if args.extra_arg else (args.action if args.action != "all" else None)
        args.action = cmd
        args.code = arg_val
    # 处理 "python client.py hotlist" 的情况（code=hotlist, action=all）
    elif args.code in ALL_CHOICES and args.action == "all":
        args.action = args.code
        args.code = None

    # buy 命令需要基金代码
    if args.action == "buy" and not args.code:
        parser.error("buy 命令需要提供基金代码，如: python client.py buy 001668 --amount 10")
    # sell 命令需要基金代码
    if args.action == "sell" and not args.code:
        parser.error("sell 命令需要提供基金代码，如: python client.py sell 001668 --all")
    # order 命令需要订单号
    if args.action == "order" and not args.code:
        parser.error("order 命令需要提供订单号，如: python client.py order 00000000002768500381")
    # cancel 命令需要订单号
    if args.action == "cancel" and not args.code:
        parser.error("cancel 命令需要提供订单号，如: python client.py cancel 00000000002768534282")
    # set_password 命令需要密码
    if args.action == "set_password" and not args.code:
        parser.error("set_password 命令需要提供交易密码，如: python client.py set_password ruan19980418")

    # 非热榜命令必须提供基金代码
    if args.action not in HOTLIST_CMDS and not args.code:
        parser.error(f"命令 '{args.action}' 需要提供基金代码，如: python client.py 006888 {args.action}")

    commands = {
        "all": cmd_all,
        "detail": cmd_detail,
        "product": cmd_product,
        "rank": cmd_rank,
        "year_return": cmd_year_return,
        "drawdown": cmd_drawdown,
        "stability": cmd_stability,
        "hold_overview": cmd_hold_overview,
        "holdings": cmd_holdings,
        "valuation": cmd_valuation,
        "position": cmd_position_change,
        "profit": cmd_profit,
        "style": cmd_style,
        "asset": cmd_asset,
        "nav": cmd_nav,
        "realtime": cmd_realtime,
        "manager": cmd_manager,
        "rsi": cmd_rsi,
        "trade_rule": cmd_trade_rule,
        "scale_change": cmd_scale_change,
        "holder_ratio": cmd_holder_ratio,
        "dividend": cmd_dividend,
        "announcements": cmd_announcements,
        "news": cmd_news,
        "compare": cmd_compare,
        "pe_percentile": cmd_pe_percentile,
        "nav_technical": cmd_nav_technical,
        "market": cmd_market,
        "fund_flow": cmd_fund_flow,
        "hotlist": cmd_hotlist,
        "hotlist_topics": cmd_hotlist_topics,
        "hotlist_posts": cmd_hotlist_posts,
        "headlines": cmd_headlines,
        "news_themes": cmd_news_themes,
        "theme_articles": cmd_theme_articles,
        "news_feed": cmd_news_feed,
        "flash_news": cmd_flash_news,
        "market_overview": cmd_market_overview,
        "stock_rank": cmd_stock_rank,
        "sector_rank": cmd_sector_rank,
        "hot_board": cmd_hot_board,
        "dragon_tiger": cmd_dragon_tiger,
        "ths_dragon_tiger": cmd_ths_dragon_tiger,
        "capital_flow": cmd_capital_flow,
        "currency": cmd_currency,
        "market_changes": cmd_market_changes,
        "stock_changes": cmd_stock_changes,
        "yesterday_limit": cmd_yesterday_limit,
        "ranking": cmd_ranking,
        "screen": cmd_screen,
        "companies": cmd_companies,
        "stock_quote": cmd_stock_quote,
        "stock_kline": cmd_stock_kline,
        "stock_flow": cmd_stock_flow,
        "stock_valuation": cmd_stock_valuation,
        "stock_financial": cmd_stock_financial,
        "account": cmd_account,
        "positions": cmd_positions,
        "wallet": cmd_wallet,
        "autoinvest": cmd_autoinvest,
        "trade_binding": cmd_trade_binding,
        "trade_all": cmd_trade_all,
        "buy_limits": cmd_buy_limits,
        "buy": cmd_buy,
        "sell": cmd_sell,
        "order": cmd_order,
        "orders": cmd_orders,
        "cancel": cmd_cancel,
        "set_password": cmd_set_password,
    }

    try:
        commands[args.action](args)
    except httpx.ConnectError:
        print("错误: 无法连接 API 服务，请先启动: python3 server.py", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        import traceback
        traceback.print_exc()
        print(f"错误: API 返回 {e.response.status_code}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
