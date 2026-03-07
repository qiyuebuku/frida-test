"""同花顺基金 API 服务"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ths_fund_client import THSFundClient

client: THSFundClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = THSFundClient()
    yield
    await client.close()


app = FastAPI(
    title="同花顺基金 API",
    description="逆向自同花顺 App v11.47.03 的基金数据接口",
    version="1.0.0",
    lifespan=lifespan,
)


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")


# ==================== 基金公司（必须在 /api/fund/{fund_code} 之前） ====================

@app.get("/api/fund/companies", summary="基金公司列表", tags=["基金排行"])
async def fund_company_list():
    """获取基金公司列表"""
    return await safe_call(client.get_fund_company_list())


# ==================== 基金详情 ====================

@app.get("/api/fund/{fund_code}", summary="基金综合详情", tags=["基金详情"])
async def fund_detail(fund_code: str):
    """获取基金综合详情，包括净值、涨幅、基金经理、交易规则等"""
    return await safe_call(client.get_fund_detail(fund_code))


@app.get("/api/fund/{fund_code}/product", summary="产品详情", tags=["基金详情"])
async def product_detail(fund_code: str):
    """获取产品详情（投资理念、业绩基准、风险特征、分红等）"""
    return await safe_call(client.get_product_detail(fund_code))


@app.get("/api/fund/{fund_code}/base", summary="基金基础信息", tags=["基金详情"])
async def fund_base(fund_code: str):
    """获取基金基础信息：评分、风险等级、风格、基金经理"""
    return await safe_call(client.get_fund_base(fund_code))


@app.get("/api/fund/{fund_code}/info", summary="基金行情信息", tags=["基金详情"])
async def fund_info(fund_code: str):
    """获取基金行情：净值、涨幅、规模、交易状态"""
    return await safe_call(client.get_fund_info(fund_code))


@app.get("/api/fund/{fund_code}/flag", summary="基金标志", tags=["基金详情"])
async def fund_flag(fund_code: str):
    """获取基金标志：是否LOF/退市、二级分类"""
    return await safe_call(client.get_fund_flag(fund_code))


# ==================== 净值走势 ====================

@app.get("/api/fund/{fund_code}/nav", summary="净值走势", tags=["净值走势"])
async def nav_trend(
    fund_code: str,
    period: str = Query("year", description="year=近一年, month=近一月, nowyear=今年以来"),
):
    """获取基金净值走势图数据"""
    if period not in ("year", "month", "nowyear"):
        raise HTTPException(400, "period 必须是 year/month/nowyear")
    return await safe_call(client.get_nav_trend(fund_code, period))


@app.get("/api/fund/{fund_code}/realtime", summary="实时估值走势", tags=["净值走势"])
async def realtime_trend(fund_code: str):
    """获取实时估值分时走势（每分钟更新）"""
    return await safe_call(client.get_realtime_trend(fund_code))


# ==================== 业绩表现 ====================

@app.get("/api/fund/{fund_code}/rank", summary="阶段涨幅排名", tags=["业绩表现"])
async def performance_rank(fund_code: str):
    """获取阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
    return await safe_call(client.get_performance_rank(fund_code))


@app.get("/api/fund/{fund_code}/year_return", summary="年度收益率", tags=["业绩表现"])
async def year_return(fund_code: str):
    """获取年度收益率及同类排名"""
    return await safe_call(client.get_year_return(fund_code))


@app.get("/api/fund/{fund_code}/drawdown", summary="最大回撤", tags=["业绩表现"])
async def max_drawdown(fund_code: str):
    """获取最大回撤（近半年/近一年/近三年/成立以来）"""
    return await safe_call(client.get_max_drawdown(fund_code))


PERIODIC_RATE_TYPES = {"day", "week", "month", "quarter", "year"}


@app.get("/api/fund/{fund_code}/periodic_rate", summary="定期收益率（收益稳定度）", tags=["业绩表现"])
async def periodic_rate(
    fund_code: str,
    group: str = Query("day", description="day/week/month/quarter/year"),
):
    """获取定期收益率（收益稳定度）"""
    if group not in PERIODIC_RATE_TYPES:
        raise HTTPException(400, f"group 必须是 {'/'.join(sorted(PERIODIC_RATE_TYPES))}")
    return await safe_call(client.get_periodic_rate(fund_code, f"{group}PeriodicRate"))


@app.get("/api/fund/{fund_code}/profit", summary="收益贡献", tags=["业绩表现"])
async def profit_contribution(
    fund_code: str,
    time_type: str = Query("threeMonth", description="threeMonth/halfYear/year"),
):
    """获取收益贡献分析"""
    return await safe_call(client.get_profit_contribution(fund_code, time_type))


# ==================== 持仓信息 ====================

@app.get("/api/fund/{fund_code}/holdings", summary="前十大持仓", tags=["持仓信息"])
async def top10_holdings(fund_code: str):
    """获取前十大持仓"""
    return await safe_call(client.get_top10_holdings(fund_code))


@app.get("/api/fund/{fund_code}/holdings/overview", summary="持仓概览", tags=["持仓信息"])
async def holding_overview(fund_code: str):
    """获取持仓概览"""
    return await safe_call(client.get_holding_overview(fund_code))


@app.get("/api/fund/{fund_code}/asset_allocation", summary="资产配置", tags=["持仓信息"])
async def asset_allocation(fund_code: str, manager_id: str = ""):
    """获取资产配置"""
    return await safe_call(client.get_asset_allocation(fund_code, manager_id))


@app.get("/api/fund/{fund_code}/style", summary="投资风格偏好", tags=["持仓信息"])
async def style_preference(fund_code: str):
    """获取投资风格偏好"""
    return await safe_call(client.get_style_preference(fund_code))


@app.get("/api/fund/{fund_code}/position/dates", summary="持仓回顾日期列表", tags=["持仓信息"])
async def position_dates(fund_code: str):
    """获取持仓回顾可用的季度日期列表"""
    return await safe_call(client.get_position_dates(fund_code))


@app.get("/api/fund/{fund_code}/position/detail", summary="季度持仓明细", tags=["持仓信息"])
async def position_detail(
    fund_code: str,
    end_date: str = Query("", description="季度末日期 YYYYMMDD，如 20251231"),
):
    """获取指定季度的前十大持仓明细"""
    return await safe_call(client.get_position_detail(fund_code, end_date))


# ==================== 基金经理 ====================

@app.get("/api/fund/{fund_code}/manager", summary="基金经理信息", tags=["基金经理"])
async def manager_info(fund_code: str, manager_id: str = Query(..., description="基金经理ID，如 T191488300")):
    """获取基金经理详细信息"""
    return await safe_call(client.get_manager_info(fund_code, manager_id))


@app.get("/api/manager/{manager_id}/profile", summary="经理完整档案", tags=["基金经理"])
async def manager_profile(manager_id: str):
    """获取基金经理完整档案（简历、雷达图、管理基金列表）"""
    return await safe_call(client.get_manager_profile(manager_id))


@app.get("/api/manager/{manager_id}/invest_history", summary="经理投资历史", tags=["基金经理"])
async def manager_invest_history(manager_id: str):
    """获取基金经理投资历史（所有基金业绩、重仓股）"""
    return await safe_call(client.get_manager_invest_history(manager_id))


@app.get("/api/manager/{manager_id}/diagnose", summary="经理诊断评分", tags=["基金经理"])
async def manager_diagnose(manager_id: str):
    """获取基金经理诊断评分（历史规模、回撤、年化收益）"""
    return await safe_call(client.get_manager_diagnose(manager_id))


@app.get("/api/manager/{manager_id}/industry_prefer", summary="经理行业偏好", tags=["基金经理"])
async def manager_industry_prefer(manager_id: str):
    """获取基金经理行业偏好"""
    return await safe_call(client.get_manager_industry_prefer(manager_id))


@app.get("/api/manager/{manager_id}/represent_fund", summary="经理代表基金", tags=["基金经理"])
async def manager_represent_fund(manager_id: str):
    """获取基金经理代表基金"""
    return await safe_call(client.get_manager_represent_fund(manager_id))


# ==================== 技术面 & 大盘 & 资金流 ====================

@app.get("/api/fund/{fund_code}/nav_technical", summary="净值技术面分析", tags=["决策辅助"])
async def nav_technical(fund_code: str):
    """基金净值技术面分析（RSI14/MA5/MA20/MA60/偏离度/信号）"""
    return await safe_call(client.get_nav_technical(fund_code))


@app.get("/api/market/overview", summary="A股大盘总览", tags=["决策辅助"])
async def market_overview():
    """A股大盘总览：指数行情 + 涨跌家数 + 成交额 + 资金流向 + 涨跌停 + 大小盘对比"""
    return await safe_call(client.get_market_overview())


@app.get("/api/market/yesterday_limit", summary="昨日涨停今日表现", tags=["决策辅助"])
async def yesterday_limit():
    """昨日涨停股票今日涨跌表现（涨跌幅/振幅/换手率/连板数/封板时间）"""
    return await safe_call(client.get_yesterday_limit_performance())


@app.get("/api/market/stock_changes", summary="盘中异动", tags=["决策辅助"])
async def stock_changes(
    change_type: str = Query("all", description="异动类型: all/竞价/拉升/跳水/大单/涨停/跌停/缺口/新高新低/大幅，或具体类型如'火箭发射'"),
    count: int = Query(50, description="返回条数"),
):
    """盘中异动（火箭发射/竞价上涨/大笔买入/封涨停 等22种类型）"""
    return await safe_call(client.get_stock_changes(change_type, min(count, 200)))


@app.get("/api/market/stock_ranking", summary="个股涨跌幅排行", tags=["决策辅助"])
async def stock_ranking(
    sort: str = Query("rise", description="排序: rise=涨幅榜, fall=跌幅榜, volume=成交量, turnover=成交额, turnover_rate=换手率"),
    count: int = Query(20, description="返回条数"),
):
    """个股涨跌幅排行（涨幅榜/跌幅榜/成交榜/换手率榜）"""
    valid = {"rise", "fall", "volume", "turnover", "amplitude", "turnover_rate"}
    if sort not in valid:
        raise HTTPException(400, f"sort 必须是 {'/'.join(sorted(valid))}")
    return await safe_call(client.get_stock_ranking(sort, min(count, 50)))


@app.get("/api/market/sector_ranking", summary="板块涨跌排行", tags=["决策辅助"])
async def sector_ranking(
    sector_type: str = Query("concept", description="类型: concept=概念板块, industry=行业板块"),
    count: int = Query(20, description="返回条数"),
):
    """板块涨跌排行（概念板块/行业板块 涨跌幅榜）"""
    if sector_type not in {"concept", "industry"}:
        raise HTTPException(400, "sector_type 必须是 concept/industry")
    return await safe_call(client.get_sector_ranking(sector_type, min(count, 50)))


@app.get("/api/market/dragon_tiger", summary="龙虎榜", tags=["决策辅助"])
async def dragon_tiger(
    tab: str = Query("stock", description="维度: stock=个股明细, dept=活跃营业部(游资), org=机构买卖"),
    days: int = Query(3, description="回溯天数"),
    count: int = Query(30, description="返回条数"),
):
    """龙虎榜数据（个股明细/活跃营业部/机构买卖）"""
    if tab not in {"stock", "dept", "org"}:
        raise HTTPException(400, "tab 必须是 stock/dept/org")
    return await safe_call(client.get_dragon_tiger(tab, days, min(count, 50)))


@app.get("/api/market/ths_dragon_tiger", summary="同花顺游资龙虎榜", tags=["决策辅助"])
async def ths_dragon_tiger(
    tab: str = Query("youzi", description="维度: youzi=游资(一线+知名), jigou=机构, gfgs=跟风高手, all=全部"),
    count: int = Query(30, description="返回条数"),
):
    """同花顺龙虎榜 - 带游资/机构分类标签"""
    if tab not in {"youzi", "jigou", "gfgs", "gansidui", "all"}:
        raise HTTPException(400, "tab 必须是 youzi/jigou/gfgs/all")
    return await safe_call(client.get_ths_dragon_tiger(tab, min(count, 50)))


@app.get("/api/market/capital_flow", summary="资金流向", tags=["决策辅助"])
async def capital_flow(
    tab: str = Query("market", description="维度: market=大盘资金, north=北向资金"),
    days: int = Query(20, description="回溯天数"),
):
    """资金流向（大盘主力资金净流入 / 北向资金成交额）"""
    if tab not in {"market", "north"}:
        raise HTTPException(400, "tab 必须是 market/north")
    return await safe_call(client.get_capital_flow(tab, min(days, 60)))


@app.get("/api/market/hot_board", summary="热点板块排行", tags=["决策辅助"])
async def hot_board(
    board_type: str = Query("concept", description="类型: concept=概念板块, industry=行业板块"),
    sort: str = Query("rise", description="排序: rise=今日涨幅, flow=资金流入, 5day=5日涨幅"),
    count: int = Query(10, description="返回条数"),
):
    """热点板块排行（今日涨幅/资金流入/5日涨幅 TOP N）"""
    if board_type not in {"concept", "industry"}:
        raise HTTPException(400, "board_type 必须是 concept/industry")
    if sort not in {"rise", "flow", "5day"}:
        raise HTTPException(400, "sort 必须是 rise/flow/5day")
    return await safe_call(client.get_hot_board(board_type, sort, min(count, 30)))


@app.get("/api/market/currency", summary="货币风向", tags=["决策辅助"])
async def currency_data(
    tab: str = Query("usdcny", description="维度: usdcny=美元/离岸人民币, shibor=Shibor利率+LPR"),
    days: int = Query(120, description="回溯天数"),
):
    """货币风向（美元/离岸人民币汇率走势 / Shibor利率+LPR变化）"""
    if tab not in {"usdcny", "shibor"}:
        raise HTTPException(400, "tab 必须是 usdcny/shibor")
    return await safe_call(client.get_currency_data(tab, min(days, 500)))


@app.get("/api/market/environment", summary="大盘与行业环境", tags=["决策辅助"])
async def market_environment():
    """获取沪深300趋势 + 北向资金活跃度"""
    return await safe_call(client.get_market_environment())


@app.get("/api/fund/{fund_code}/fund_flow", summary="基金申赎资金流", tags=["决策辅助"])
async def fund_flow(fund_code: str):
    """基金申赎资金流趋势（季度净申赎 + 机构占比变化）"""
    return await safe_call(client.get_fund_flow_trend(fund_code))


# ==================== 持仓股估值 ====================

@app.get("/api/fund/{fund_code}/holdings/valuation", summary="持仓股估值", tags=["持仓信息"])
async def holdings_valuation(fund_code: str):
    """获取前十大持仓股的估值数据（PE/PB/市值/ROE）"""
    return await safe_call(client.get_holdings_valuation(fund_code))


@app.get("/api/fund/{fund_code}/holdings/pe_percentile", summary="持仓股估值百分位", tags=["持仓信息"])
async def holdings_pe_percentile(
    fund_code: str,
    years: int = Query(3, description="回溯年数，默认3"),
):
    """获取前十大持仓股的估值百分位（近N年PE/PB历史分位）"""
    if years < 1 or years > 10:
        raise HTTPException(400, "years 必须在 1-10 之间")
    return await safe_call(client.get_holdings_valuation_percentile(fund_code, years))


# ==================== 交易规则与费率 ====================

@app.get("/api/fund/{fund_code}/trade_rule", summary="交易规则与费率", tags=["交易规则"])
async def trade_rule(fund_code: str):
    """获取交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
    return await safe_call(client.get_trade_rule(fund_code))


# ==================== 规模与持有人 ====================

@app.get("/api/fund/{fund_code}/scale_change", summary="规模变动历史", tags=["规模与持有人"])
async def scale_change(fund_code: str):
    """获取规模变动历史（季度净资产、申购赎回金额、份额变动）"""
    return await safe_call(client.get_scale_change(fund_code))


@app.get("/api/fund/{fund_code}/holder_ratio", summary="机构持仓比例", tags=["规模与持有人"])
async def holder_ratio(fund_code: str):
    """获取机构持仓比例历史（半年度机构持有占比变化）"""
    return await safe_call(client.get_holder_ratio(fund_code))


# ==================== 分红历史 ====================

@app.get("/api/fund/{fund_code}/dividend", summary="分红历史", tags=["分红"])
async def dividend_history(fund_code: str):
    """获取分红历史和拆分记录"""
    return await safe_call(client.get_dividend_history(fund_code))


# ==================== 指标与追踪 ====================

@app.get("/api/fund/{fund_code}/rsi", summary="RSI买卖指标", tags=["指标"])
async def rsi_indicator(fund_code: str):
    """获取RSI买卖区间指标"""
    return await safe_call(client.get_rsi_indicator(fund_code))


@app.get("/api/fund/{fund_code}/track", summary="基金追踪", tags=["指标"])
async def fund_track(fund_code: str):
    """获取基金追踪数据"""
    return await safe_call(client.get_fund_track(fund_code))


ANNOUNCEMENT_CATS = {"all", "report", "dividend", "change", "operation", "other"}


@app.get("/api/fund/{fund_code}/announcements", summary="基金公告", tags=["其他"])
async def announcements(
    fund_code: str,
    category: str = Query("all", description="分类: all/report/dividend/change/operation/other"),
    page: int = Query(1, description="页码"),
    page_size: int = Query(15, description="每页条数"),
):
    """获取基金公告（支持分类筛选）"""
    if category not in ANNOUNCEMENT_CATS:
        raise HTTPException(400, f"category 必须是 {'/'.join(sorted(ANNOUNCEMENT_CATS))}")
    return await safe_call(client.get_announcements(fund_code, category, page, page_size))


@app.get("/api/fund/{fund_code}/news", summary="基金资讯", tags=["其他"])
async def fund_news(
    fund_code: str,
    limit: int = Query(10, description="返回条数"),
):
    """获取基金相关资讯"""
    return await safe_call(client.get_news(fund_code, limit))


# ==================== 基金排行与筛选 ====================


class FundRankingRequest(BaseModel):
    sort_type: str = Field("year", description="排序字段: year/hyear/tmonth/month/week/nowyear/tyear/fyear/now/sharpeYear/maxDrawDownYear")
    sort: str = Field("DESC", description="排序方向: DESC/ASC")
    limit: int = Field(30, description="每页数量", ge=1, le=200)
    offset: int = Field(0, description="偏移量", ge=0)
    fund_type: str = Field(None, description="基金类型代码（如 282001001=股票型）")
    fund_company: str = Field(None, description="基金公司 orgid")
    min_scale: float = Field(None, description="最小规模（元），默认1000万")
    board: str = Field(None, description="排行榜名称（涨幅榜/反弹榜/人气榜/加仓榜/超额榜），会自动获取配置")
    strategy: str = Field(None, description="策略筛选key（fund0001=年年正收益/fund0002=三年翻倍/fund0010=能涨抗跌 等）")


@app.post("/api/fund/ranking", summary="基金排行", tags=["基金排行"])
async def fund_ranking(req: FundRankingRequest = Body(...)):
    """同花顺基金排行（支持排序、筛选、预设策略、排行榜）"""
    sort_type = req.sort_type
    extra_filters = None

    # 如果指定了排行榜名称，从配置中获取对应参数
    if req.board:
        try:
            config = await client.get_rank_board_config()
            board_list = config.get("data", {}).get("rankList", [])
            for item in board_list:
                if item.get("name") == req.board or item.get("key") == req.board:
                    sort_type = item.get("sortType", sort_type)
                    # 排行榜的 filterList 格式不同，需要转换为排行 API 格式
                    raw_filters = item.get("filterList", [])
                    extra_filters = []
                    for f in raw_filters:
                        extra_filters.append({
                            "filterField": f.get("filterField"),
                            "filterTypeList": [{
                                "filterValue": f.get("filterValue"),
                                "filterSymbol": f.get("filterSymbol"),
                            }],
                        })
                    break
            else:
                raise HTTPException(400, f"未找到排行榜: {req.board}，可用: {[b.get('name') for b in board_list]}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"获取排行榜配置失败: {e}")

    return await safe_call(client.get_fund_ranking(
        sort_type=sort_type,
        sort=req.sort,
        limit=req.limit,
        offset=req.offset,
        fund_type=req.fund_type,
        fund_company=req.fund_company,
        min_scale=req.min_scale,
        strategy=req.strategy,
        extra_filters=extra_filters,
    ))


@app.get("/api/fund/ranking/boards", summary="排行榜配置", tags=["基金排行"])
async def rank_board_config():
    """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
    return await safe_call(client.get_rank_board_config())


@app.get("/api/fund/ranking/filters", summary="筛选策略配置", tags=["基金排行"])
async def rank_filter_config():
    """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
    return await safe_call(client.get_rank_filter_config())


@app.get("/api/fund/ranking/distribution", summary="收益率分布", tags=["基金排行"])
async def rank_distribution():
    """获取收益率分布统计（各周期的收益率分布）"""
    return await safe_call(client.get_rank_distribution())


# ==================== 同类基金对比 ====================

@app.get("/api/fund/{fund_code}/similar", summary="发现同赛道基金", tags=["基金对比"])
async def find_similar_funds(
    fund_code: str,
    top_n: int = Query(5, description="返回数量，默认5"),
):
    """自动发现同赛道基金（基于行业分布相似度）"""
    return await safe_call(client.find_similar_funds(fund_code, top_n))


@app.get("/api/funds/compare", summary="多基金横向对比", tags=["基金对比"])
async def fund_compare(
    codes: str = Query(..., description="基金代码，逗号分隔，如 006888,022364"),
):
    """多基金横向对比数据"""
    fund_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not fund_codes or len(fund_codes) > 10:
        raise HTTPException(400, "需要 1-10 只基金代码")
    return await safe_call(client.get_fund_compare_data(fund_codes))


HOTLIST_MARKETS = {"a", "hk", "us"}
HOTLIST_PLATE_TYPES = {"concept", "industry"}


# ==================== 热榜数据 ====================

@app.get("/api/hotlist/stocks", summary="个股热榜", tags=["热榜"])
async def hot_stocks(
    market: str = Query("a", description="市场: a=A股, hk=港股, us=美股"),
):
    """获取个股热榜（热度排名前100）"""
    if market not in HOTLIST_MARKETS:
        raise HTTPException(400, f"market 必须是 {'/'.join(sorted(HOTLIST_MARKETS))}")
    return await safe_call(client.get_hot_stocks(market))


@app.get("/api/hotlist/plate", summary="概念/行业热榜", tags=["热榜"])
async def hot_plate(
    plate_type: str = Query("concept", description="类型: concept=概念, industry=行业"),
):
    """获取概念或行业热榜"""
    if plate_type not in HOTLIST_PLATE_TYPES:
        raise HTTPException(400, f"plate_type 必须是 {'/'.join(sorted(HOTLIST_PLATE_TYPES))}")
    return await safe_call(client.get_hot_plate(plate_type))


@app.get("/api/hotlist/etf", summary="ETF 热榜", tags=["热榜"])
async def hot_etf():
    """获取 ETF 热度排行"""
    return await safe_call(client.get_hot_etf())


@app.get("/api/hotlist/futures", summary="期货热榜", tags=["热榜"])
async def hot_futures():
    """获取期货热度排行"""
    return await safe_call(client.get_hot_futures())


@app.get("/api/hotlist/bond", summary="可转债热榜", tags=["热榜"])
async def hot_bond():
    """获取可转债热度排行"""
    return await safe_call(client.get_hot_bond())


@app.get("/api/hotlist/topics", summary="热榜话题", tags=["热榜"])
async def hot_topics():
    """获取同花顺热榜话题（15条）"""
    return await safe_call(client.get_hot_topics())


@app.get("/api/hotlist/posts", summary="热门文章", tags=["热榜"])
async def hot_posts(
    page: int = Query(1, description="页码"),
    page_size: int = Query(10, description="每页条数"),
):
    """获取热门文章"""
    return await safe_call(client.get_hot_posts(page, page_size))


@app.get("/api/hotlist/topic/{code}", summary="话题详情", tags=["热榜"])
async def topic_detail(
    code: str,
    page: int = Query(1, description="帖子页码"),
    page_size: int = Query(10, description="每页帖子数"),
):
    """获取话题详情及推荐帖子"""
    return await safe_call(client.get_topic_detail(code, page, page_size))


@app.get("/api/hotlist/special/{code}", summary="专题详情", tags=["热榜"])
async def special_detail(code: str):
    """获取专题详情（从 HTML 解析）"""
    return await safe_call(client.get_special_detail(code))


@app.get("/api/headlines", summary="推荐头条", tags=["热榜"])
async def headlines():
    """获取推荐头条（首页推荐tab头条模块）"""
    return await safe_call(client.get_headlines())


@app.get("/api/article/{encoded_seq}", summary="新闻文章详情", tags=["热榜"])
async def article_detail(encoded_seq: str):
    """获取新闻文章详情（type=1 类型的新闻）"""
    return await safe_call(client.get_article_detail(encoded_seq))


@app.get("/api/news_themes", summary="新闻主题分类", tags=["热榜"])
async def news_themes():
    """获取新闻主题分类列表（资讯→头条 tab 栏的主题标签，如小金属、有色金属、算力租赁等）"""
    return await safe_call(client.get_news_themes())


@app.get("/api/theme/{theme_id}/articles", summary="主题文章列表", tags=["热榜"])
async def theme_articles(
    theme_id: str,
    page: int = Query(1, description="页码"),
    size: int = Query(15, description="每页条数"),
):
    """获取主题下的文章列表（含标题、来源、阅读量、相关股票）"""
    return await safe_call(client.get_theme_articles(theme_id, page, size))


@app.get("/api/flash_news/tabs", summary="快讯分类标签", tags=["热榜"])
async def flash_news_tabs():
    """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
    return await safe_call(client.get_flash_news_tabs())


@app.get("/api/flash_news/list", summary="分类快讯列表", tags=["热榜"])
async def flash_news_list(
    tag_id: int = Query(21101, description="分类ID: 21101=A股, 62857=重要, 34843=公告, 33775=期货, 21111=异动, 21105=港股, 21107=美股"),
    seq: int = Query(0, description="翻页游标，0=最新，传上一页最后一条的seq加载更早"),
):
    """获取指定分类的快讯列表（每次约20条）"""
    return await safe_call(client.get_flash_news_list(tag_id, seq))


@app.get("/api/news_feed", summary="滚动快讯", tags=["热榜"])
async def news_feed(page: int = Query(1, description="页码")):
    """获取滚动快讯（财经要闻，每页20条）"""
    return await safe_call(client.get_news_feed(page))


# ==================== 交易账户 ====================


class TradeAuthUpdate(BaseModel):
    key1: str = Field(None, description="设备UUID")
    key2: str = Field(None, description="签名hash")
    key3: str = Field(None, description="客户ID (custId)")
    key5: str = Field(None, description="JWT token")
    user_id: str = Field(None, description="用户ID")
    session_id: str = Field(None, description="会话ID")
    cookie: str = Field(None, description="用户cookie")


class BuyFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    amount: float = Field(..., description="买入金额（元）", gt=0)
    use_wallet: bool = Field(True, description="是否使用活期宝支付")
    password: str = Field(None, description="交易密码（明文，自动MD5）或MD5哈希；优先级高于 set_password")


class TradePasswordUpdate(BaseModel):
    password_md5: str = Field(..., description="交易密码的MD5哈希值（32位大写）")


@app.get("/api/trade/overview", summary="账户总览", tags=["交易账户"])
async def trade_overview():
    """账户总览（总资产、累计盈亏、当日盈亏、风险等级等）"""
    return await safe_call(client.get_account_overview())


@app.get("/api/trade/positions", summary="基金持仓", tags=["交易账户"])
async def trade_positions():
    """基金持仓列表（持仓基金明细、市值、收益等）"""
    return await safe_call(client.get_fund_positions())


@app.get("/api/trade/wallet", summary="活期宝", tags=["交易账户"])
async def trade_wallet():
    """活期宝/超级T+0（货币基金收益、可用余额等）"""
    return await safe_call(client.get_wallet_info())


@app.get("/api/trade/wallet/home", summary="钱包首页", tags=["交易账户"])
async def trade_wallet_home():
    """钱包首页（活期宝余额、冻结金额、累计收益等）"""
    return await safe_call(client.get_wallet_home())


@app.get("/api/trade/autoinvest/list", summary="定投计划列表", tags=["交易账户"])
async def trade_autoinvest_list(
    page_size: int = Query(20, description="每页数量"),
    status: str = Query("N", description="状态: N=全部, 1=执行中, 2=暂停"),
):
    """定投计划列表"""
    return await safe_call(client.get_auto_invest_list(page_size, status))


@app.get("/api/trade/autoinvest/summary", summary="定投汇总", tags=["交易账户"])
async def trade_autoinvest_summary():
    """定投汇总（总金额、下次执行日期、正常/暂停计划数等）"""
    return await safe_call(client.get_auto_invest_summary())


@app.get("/api/trade/binding", summary="账户绑定信息", tags=["交易账户"])
async def trade_binding():
    """账户绑定信息（客户ID、姓名、身份证号等）"""
    return await safe_call(client.get_account_binding())


@app.get("/api/trade/all", summary="全部交易数据", tags=["交易账户"])
async def trade_all():
    """一次性获取所有交易账户数据"""
    return await safe_call(client.get_trade_account_all())


@app.post("/api/trade/auth", summary="更新交易认证", tags=["交易账户"])
async def trade_auth_update(req: TradeAuthUpdate = Body(...)):
    """更新交易认证参数（token 过期后需要从 Hook 重新捕获）"""
    client.update_trade_auth(
        key1=req.key1, key2=req.key2, key3=req.key3, key5=req.key5,
        user_id=req.user_id, session_id=req.session_id, cookie=req.cookie,
    )
    return {"status": "ok", "message": "交易认证参数已更新"}


# ==================== 基金交易 ====================


@app.post("/api/trade/password", summary="设置交易密码", tags=["基金交易"])
async def trade_password_update(req: TradePasswordUpdate):
    """设置交易密码（MD5哈希）"""
    client.update_trade_password(req.password_md5)
    return {"status": "ok", "message": "交易密码已设置"}


@app.post("/api/trade/buy", summary="买入基金", tags=["基金交易"])
async def trade_buy(req: BuyFundRequest):
    """买入基金（完整流程：初始化→检查→下单）"""
    import hashlib
    password_md5 = None
    if req.password:
        raw = req.password
        if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
            password_md5 = raw.upper()
        else:
            password_md5 = hashlib.md5(raw.encode()).hexdigest().upper()
    return await safe_call(client.buy_fund(req.fund_code, req.amount, req.use_wallet, password_md5))


@app.get("/api/trade/order/{order_no}", summary="查询订单", tags=["基金交易"])
async def trade_order_detail(order_no: str):
    """查询交易订单详情"""
    return await safe_call(client.get_order_detail(order_no))


@app.get("/api/trade/orders", summary="订单列表", tags=["基金交易"])
async def trade_order_list(
    days: int = Query(30, description="查询天数，默认30天"),
    op_type: str = Query("all", description="操作类型: all=全部"),
    limit: int = Query(20, description="每页条数"),
    offset: int = Query(1, description="页码（从1开始）"),
):
    """查询交易订单列表"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    return await safe_call(client.get_order_list(start_date, end_date, op_type, limit, offset))


class SellFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    share_vol: float = Field(None, description="赎回份额数量")
    sell_all: bool = Field(False, description="是否全部赎回")
    password: str = Field(None, description="交易密码（明文或MD5）")


class CancelOrderRequest(BaseModel):
    order_no: str = Field(..., description="订单号 appSheetSerialNo")
    password: str = Field(None, description="交易密码（明文或MD5）")


@app.post("/api/trade/sell", summary="赎回基金", tags=["基金交易"])
async def trade_sell(req: SellFundRequest):
    """赎回基金（完整流程：获取持仓→初始化→提交赎回）"""
    import hashlib
    password_md5 = None
    if req.password:
        raw = req.password
        if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
            password_md5 = raw.upper()
        else:
            password_md5 = hashlib.md5(raw.encode()).hexdigest().upper()
    share_vol_str = f"{req.share_vol:.2f}" if req.share_vol else None
    return await safe_call(client.sell_fund(req.fund_code, share_vol_str, req.sell_all, password_md5))


@app.post("/api/trade/cancel", summary="撤销订单", tags=["基金交易"])
async def trade_cancel(req: CancelOrderRequest):
    """撤销交易订单"""
    import hashlib
    password_md5 = None
    if req.password:
        raw = req.password
        if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
            password_md5 = raw.upper()
        else:
            password_md5 = hashlib.md5(raw.encode()).hexdigest().upper()
    return await safe_call(client.cancel_order(req.order_no, password_md5))


# ==================== 个股查询 ====================

@app.get("/api/stock/quote", summary="个股实时行情", tags=["个股查询"])
async def stock_quote(
    codes: str = Query(..., description="股票代码，逗号分隔，如 600519,000001"),
):
    """获取个股实时行情（腾讯证券数据源，支持批量最多20只）"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(400, "codes 不能为空")
    return await safe_call(client.get_stock_quote(code_list))


@app.get("/api/stock/{code}/kline", summary="个股K线", tags=["个股查询"])
async def stock_kline(
    code: str,
    period: str = Query("101", description="周期: 101=日K, 102=周K, 103=月K"),
    limit: int = Query(60, description="返回条数"),
):
    """获取个股K线数据（前复权）"""
    if period not in {"101", "102", "103"}:
        raise HTTPException(400, "period 必须是 101/102/103")
    return await safe_call(client.get_stock_kline(code, period, min(limit, 500)))


@app.get("/api/stock/{code}/capital_flow", summary="个股资金流", tags=["个股查询"])
async def stock_capital_flow(
    code: str,
    days: int = Query(20, description="回溯天数"),
):
    """获取个股资金流向（主力/超大单/大单/中单/小单）"""
    return await safe_call(client.get_stock_capital_flow(code, min(days, 120)))


@app.get("/api/stock/{code}/valuation", summary="个股估值历史", tags=["个股查询"])
async def stock_valuation(
    code: str,
    years: int = Query(3, description="回溯年数"),
):
    """获取个股 PE_TTM/PB 历史数据"""
    data = await safe_call(client.get_stock_valuation_history(code, min(years, 10)))
    return {"status_code": 0, "data": {"code": code, "total": len(data), "items": data}}


@app.get("/api/stock/{code}/financial", summary="个股财务数据", tags=["个股查询"])
async def stock_financial(
    code: str,
    limit: int = Query(10, description="返回报告期数"),
):
    """获取个股财务数据（EPS/营收/净利/ROE/毛利率等）"""
    return await safe_call(client.get_stock_financial(code, min(limit, 50)))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8900, reload=True)
