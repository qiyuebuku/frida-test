"""基金查询路由：排行/详情/净值/业绩/持仓/经理/对比/规则/分红/指标"""

from fastapi import APIRouter, HTTPException, Query, Body

import routers._utils as _utils
from src.interfaces.api.routes._utils import safe_call
from src.interfaces.api.routes._models import FundRankingRequest, PERIODIC_RATE_TYPES, ANNOUNCEMENT_CATS

router = APIRouter()


# ==================== 基金公司（必须在 /api/fund/{fund_code} 之前） ====================

@router.get("/api/fund/companies", summary="基金公司列表", tags=["基金排行"])
async def fund_company_list():
    """获取基金公司列表"""
    return await safe_call(_utils.ths.get_fund_company_list())


@router.get("/api/fund/search", summary="基金搜索", tags=["基金排行"])
async def fund_search(
    keyword: str = Query(..., description="搜索关键词（如 标普500、纳斯达克）"),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100),
):
    """搜索基金（按名称关键词）"""
    return await safe_call(_utils.eastmoney.search_fund(keyword, limit))


# ==================== 基金详情 ====================

@router.get("/api/fund/{fund_code}", summary="基金综合详情", tags=["基金详情"])
async def fund_detail(fund_code: str):
    """获取基金综合详情，包括净值、涨幅、基金经理、交易规则等"""
    return await safe_call(_utils.ths.get_fund_detail(fund_code))


@router.get("/api/fund/{fund_code}/product", summary="产品详情", tags=["基金详情"])
async def product_detail(fund_code: str):
    """获取产品详情（投资理念、业绩基准、风险特征、分红等）"""
    return await safe_call(_utils.ths.get_product_detail(fund_code))


@router.get("/api/fund/{fund_code}/base", summary="基金基础信息", tags=["基金详情"])
async def fund_base(fund_code: str):
    """获取基金基础信息：评分、风险等级、风格、基金经理"""
    return await safe_call(_utils.ths.get_fund_base(fund_code))


@router.get("/api/fund/{fund_code}/info", summary="基金行情信息", tags=["基金详情"])
async def fund_info(fund_code: str):
    """获取基金行情：净值、涨幅、规模、交易状态"""
    return await safe_call(_utils.ths.get_fund_info(fund_code))


@router.get("/api/fund/{fund_code}/flag", summary="基金标志", tags=["基金详情"])
async def fund_flag(fund_code: str):
    """获取基金标志：是否LOF/退市、二级分类"""
    return await safe_call(_utils.ths.get_fund_flag(fund_code))


# ==================== 净值走势 ====================

@router.get("/api/fund/{fund_code}/nav", summary="净值走势", tags=["净值走势"])
async def nav_trend(
    fund_code: str,
    period: str = Query("year", description="year=近一年, month=近一月, nowyear=今年以来"),
):
    """获取基金净值走势图数据"""
    if period not in ("year", "month", "nowyear"):
        raise HTTPException(400, "period 必须是 year/month/nowyear")
    return await safe_call(_utils.ths.get_nav_trend(fund_code, period))


@router.get("/api/fund/{fund_code}/realtime", summary="实时估值走势", tags=["净值走势"])
async def realtime_trend(fund_code: str):
    """获取实时估值分时走势（每分钟更新）"""
    return await safe_call(_utils.ths.get_realtime_trend(fund_code))


# ==================== 业绩表现 ====================

@router.get("/api/fund/{fund_code}/rank", summary="阶段涨幅排名", tags=["业绩表现"])
async def performance_rank(fund_code: str):
    """获取阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
    return await safe_call(_utils.ths.get_performance_rank(fund_code))


@router.get("/api/fund/{fund_code}/year_return", summary="年度收益率", tags=["业绩表现"])
async def year_return(fund_code: str):
    """获取年度收益率及同类排名"""
    return await safe_call(_utils.ths.get_year_return(fund_code))


@router.get("/api/fund/{fund_code}/drawdown", summary="最大回撤", tags=["业绩表现"])
async def max_drawdown(fund_code: str):
    """获取最大回撤（近半年/近一年/近三年/成立以来）"""
    return await safe_call(_utils.ths.get_max_drawdown(fund_code))


@router.get("/api/fund/{fund_code}/periodic_rate", summary="定期收益率（收益稳定度）", tags=["业绩表现"])
async def periodic_rate(
    fund_code: str,
    group: str = Query("day", description="day/week/month/quarter/year"),
):
    """获取定期收益率（收益稳定度）"""
    if group not in PERIODIC_RATE_TYPES:
        raise HTTPException(400, f"group 必须是 {'/'.join(sorted(PERIODIC_RATE_TYPES))}")
    return await safe_call(_utils.ths.get_periodic_rate(fund_code, f"{group}PeriodicRate"))


@router.get("/api/fund/{fund_code}/profit", summary="收益贡献", tags=["业绩表现"])
async def profit_contribution(
    fund_code: str,
    time_type: str = Query("threeMonth", description="threeMonth/halfYear/year"),
):
    """获取收益贡献分析"""
    return await safe_call(_utils.ths.get_profit_contribution(fund_code, time_type))


# ==================== 持仓信息 ====================

@router.get("/api/fund/{fund_code}/holdings", summary="前十大持仓", tags=["持仓信息"])
async def top10_holdings(fund_code: str):
    """获取前十大持仓"""
    return await safe_call(_utils.ths.get_top10_holdings(fund_code))


@router.get("/api/fund/{fund_code}/holdings/overview", summary="持仓概览", tags=["持仓信息"])
async def holding_overview(fund_code: str):
    """获取持仓概览"""
    return await safe_call(_utils.ths.get_holding_overview(fund_code))


@router.get("/api/fund/{fund_code}/asset_allocation", summary="资产配置", tags=["持仓信息"])
async def asset_allocation(fund_code: str, manager_id: str = ""):
    """获取资产配置"""
    return await safe_call(_utils.ths.get_asset_allocation(fund_code, manager_id))


@router.get("/api/fund/{fund_code}/style", summary="投资风格偏好", tags=["持仓信息"])
async def style_preference(fund_code: str):
    """获取投资风格偏好"""
    return await safe_call(_utils.ths.get_style_preference(fund_code))


@router.get("/api/fund/{fund_code}/position/dates", summary="持仓回顾日期列表", tags=["持仓信息"])
async def position_dates(fund_code: str):
    """获取持仓回顾可用的季度日期列表"""
    return await safe_call(_utils.ths.get_position_dates(fund_code))


@router.get("/api/fund/{fund_code}/position/detail", summary="季度持仓明细", tags=["持仓信息"])
async def position_detail(
    fund_code: str,
    end_date: str = Query("", description="季度末日期 YYYYMMDD，如 20251231"),
):
    """获取指定季度的前十大持仓明细"""
    return await safe_call(_utils.ths.get_position_detail(fund_code, end_date))


@router.get("/api/fund/{fund_code}/holdings/valuation", summary="持仓股估值", tags=["持仓信息"])
async def holdings_valuation(fund_code: str):
    """获取前十大持仓股的估值数据（PE/PB/市值/ROE）"""
    return await safe_call(_utils.aggregator.get_holdings_valuation(fund_code))


@router.get("/api/fund/{fund_code}/holdings/pe_percentile", summary="持仓股估值百分位", tags=["持仓信息"])
async def holdings_pe_percentile(
    fund_code: str,
    years: int = Query(3, description="回溯年数，默认3"),
):
    """获取前十大持仓股的估值百分位（近N年PE/PB历史分位）"""
    if years < 1 or years > 10:
        raise HTTPException(400, "years 必须在 1-10 之间")
    return await safe_call(_utils.aggregator.get_holdings_valuation_percentile(fund_code, years))


# ==================== 基金经理 ====================

@router.get("/api/fund/{fund_code}/manager", summary="基金经理信息", tags=["基金经理"])
async def manager_info(fund_code: str, manager_id: str = Query(..., description="基金经理ID，如 T191488300")):
    """获取基金经理详细信息"""
    return await safe_call(_utils.ths.get_manager_info(fund_code, manager_id))


@router.get("/api/manager/{manager_id}/profile", summary="经理完整档案", tags=["基金经理"])
async def manager_profile(manager_id: str):
    """获取基金经理完整档案（简历、雷达图、管理基金列表）"""
    return await safe_call(_utils.ths.get_manager_profile(manager_id))


@router.get("/api/manager/{manager_id}/invest_history", summary="经理投资历史", tags=["基金经理"])
async def manager_invest_history(manager_id: str):
    """获取基金经理投资历史（所有基金业绩、重仓股）"""
    return await safe_call(_utils.ths.get_manager_invest_history(manager_id))


@router.get("/api/manager/{manager_id}/diagnose", summary="经理诊断评分", tags=["基金经理"])
async def manager_diagnose(manager_id: str):
    """获取基金经理诊断评分（历史规模、回撤、年化收益）"""
    return await safe_call(_utils.ths.get_manager_diagnose(manager_id))


@router.get("/api/manager/{manager_id}/industry_prefer", summary="经理行业偏好", tags=["基金经理"])
async def manager_industry_prefer(manager_id: str):
    """获取基金经理行业偏好"""
    return await safe_call(_utils.ths.get_manager_industry_prefer(manager_id))


@router.get("/api/manager/{manager_id}/represent_fund", summary="经理代表基金", tags=["基金经理"])
async def manager_represent_fund(manager_id: str):
    """获取基金经理代表基金"""
    return await safe_call(_utils.ths.get_manager_represent_fund(manager_id))


# ==================== 同类基金对比 ====================

@router.get("/api/fund/{fund_code}/similar", summary="发现同赛道基金", tags=["基金对比"])
async def find_similar_funds(
    fund_code: str,
    top_n: int = Query(5, description="返回数量，默认5"),
):
    """自动发现同赛道基金（基于行业分布相似度）"""
    return await safe_call(_utils.ths.find_similar_funds(fund_code, top_n))


@router.get("/api/funds/compare", summary="多基金横向对比", tags=["基金对比"])
async def fund_compare(
    codes: str = Query(..., description="基金代码，逗号分隔，如 006888,022364"),
):
    """多基金横向对比数据"""
    fund_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not fund_codes or len(fund_codes) > 10:
        raise HTTPException(400, "需要 1-10 只基金代码")
    return await safe_call(_utils.ths.get_fund_compare_data(fund_codes))


# ==================== 交易规则与费率 ====================

@router.get("/api/fund/{fund_code}/trade_rule", summary="交易规则与费率", tags=["交易规则"])
async def trade_rule(fund_code: str):
    """获取交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
    return await safe_call(_utils.ths.get_trade_rule(fund_code))


# ==================== 规模与持有人 ====================

@router.get("/api/fund/{fund_code}/scale_change", summary="规模变动历史", tags=["规模与持有人"])
async def scale_change(fund_code: str):
    """获取规模变动历史（季度净资产、申购赎回金额、份额变动）"""
    return await safe_call(_utils.ths.get_scale_change(fund_code))


@router.get("/api/fund/{fund_code}/holder_ratio", summary="机构持仓比例", tags=["规模与持有人"])
async def holder_ratio(fund_code: str):
    """获取机构持仓比例历史（半年度机构持有占比变化）"""
    return await safe_call(_utils.ths.get_holder_ratio(fund_code))


# ==================== 分红历史 ====================

@router.get("/api/fund/{fund_code}/dividend", summary="分红历史", tags=["分红"])
async def dividend_history(fund_code: str):
    """获取分红历史和拆分记录"""
    return await safe_call(_utils.ths.get_dividend_history(fund_code))


# ==================== 指标与追踪 ====================

@router.get("/api/fund/{fund_code}/rsi", summary="RSI买卖指标", tags=["指标"])
async def rsi_indicator(fund_code: str):
    """获取RSI买卖区间指标"""
    return await safe_call(_utils.ths.get_rsi_indicator(fund_code))


@router.get("/api/fund/{fund_code}/track", summary="基金追踪", tags=["指标"])
async def fund_track(fund_code: str):
    """获取基金追踪数据"""
    return await safe_call(_utils.ths.get_fund_track(fund_code))


@router.get("/api/fund/{fund_code}/announcements", summary="基金公告", tags=["其他"])
async def announcements(
    fund_code: str,
    category: str = Query("all", description="分类: all/report/dividend/change/operation/other"),
    page: int = Query(1, description="页码"),
    page_size: int = Query(15, description="每页条数"),
):
    """获取基金公告（支持分类筛选）"""
    if category not in ANNOUNCEMENT_CATS:
        raise HTTPException(400, f"category 必须是 {'/'.join(sorted(ANNOUNCEMENT_CATS))}")
    return await safe_call(_utils.ths.get_announcements(fund_code, category, page, page_size))


@router.get("/api/fund/{fund_code}/news", summary="基金资讯", tags=["其他"])
async def fund_news(
    fund_code: str,
    limit: int = Query(10, description="返回条数"),
):
    """获取基金相关资讯"""
    return await safe_call(_utils.ths.get_news(fund_code, limit))


# ==================== 基金排行与筛选 ====================

@router.post("/api/fund/ranking", summary="基金排行", tags=["基金排行"])
async def fund_ranking(req: FundRankingRequest = Body(...)):
    """同花顺基金排行（支持排序、筛选、预设策略、排行榜）"""
    sort_type = req.sort_type
    extra_filters = None

    # 如果指定了排行榜名称，从配置中获取对应参数
    if req.board:
        try:
            config = await _utils.ths.get_rank_board_config()
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

    return await safe_call(_utils.ths.get_fund_ranking(
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


@router.get("/api/fund/ranking/boards", summary="排行榜配置", tags=["基金排行"])
async def rank_board_config():
    """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
    return await safe_call(_utils.ths.get_rank_board_config())


@router.get("/api/fund/ranking/filters", summary="筛选策略配置", tags=["基金排行"])
async def rank_filter_config():
    """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
    return await safe_call(_utils.ths.get_rank_filter_config())


@router.get("/api/fund/ranking/distribution", summary="收益率分布", tags=["基金排行"])
async def rank_distribution():
    """获取收益率分布统计（各周期的收益率分布）"""
    return await safe_call(_utils.ths.get_rank_distribution())
