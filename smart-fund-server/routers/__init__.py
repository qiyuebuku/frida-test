"""All API routes converted from THS server.py + screenshot-assistant"""

import asyncio
import base64
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Body, Request
from pydantic import BaseModel, Field

from services.ths_fund_client import THSFundClient
from services import fund_db

router = APIRouter()

client: THSFundClient = None


def set_client(c: THSFundClient):
    global client
    client = c


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")


# ==================== Pydantic Models ====================


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


class BuyFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    amount: float = Field(..., description="买入金额（元）", gt=0)
    use_wallet: bool = Field(True, description="是否使用活期宝支付")
    password: str = Field(None, description="交易密码（明文，自动MD5）或MD5哈希；优先级高于 set_password")
    reason: str = Field(None, description="买入理由")


class SellFundRequest(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    share_vol: float = Field(None, description="赎回份额数量")
    sell_all: bool = Field(False, description="是否全部赎回")
    password: str = Field(None, description="交易密码（明文或MD5）")


class CancelOrderRequest(BaseModel):
    order_no: str = Field(..., description="订单号 appSheetSerialNo")
    password: str = Field(None, description="交易密码（明文或MD5）")


class TradeAuthUpdate(BaseModel):
    key1: str = Field(None, description="设备UUID")
    key2: str = Field(None, description="签名hash")
    key3: str = Field(None, description="客户ID (custId)")
    key5: str = Field(None, description="JWT token")
    user_id: str = Field(None, description="用户ID")
    session_id: str = Field(None, description="会话ID")
    cookie: str = Field(None, description="用户cookie")


class TradePasswordUpdate(BaseModel):
    password: str = Field(..., description="交易密码（明文）")


# ==================== Constants ====================

PERIODIC_RATE_TYPES = {"day", "week", "month", "quarter", "year"}
ANNOUNCEMENT_CATS = {"all", "report", "dividend", "change", "operation", "other"}
HOTLIST_MARKETS = {"a", "hk", "us"}
HOTLIST_PLATE_TYPES = {"concept", "industry"}


# ==================== 基金公司（必须在 /api/fund/{fund_code} 之前） ====================

@router.get("/api/fund/companies", summary="基金公司列表", tags=["基金排行"])
async def fund_company_list():
    """获取基金公司列表"""
    return await safe_call(client.get_fund_company_list())


@router.get("/api/fund/search", summary="基金搜索", tags=["基金排行"])
async def fund_search(
    keyword: str = Query(..., description="搜索关键词（如 标普500、纳斯达克）"),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100),
):
    """搜索基金（按名称关键词）"""
    return await safe_call(client.search_fund(keyword, limit))


# ==================== 基金详情 ====================

@router.get("/api/fund/{fund_code}", summary="基金综合详情", tags=["基金详情"])
async def fund_detail(fund_code: str):
    """获取基金综合详情，包括净值、涨幅、基金经理、交易规则等"""
    return await safe_call(client.get_fund_detail(fund_code))


@router.get("/api/fund/{fund_code}/product", summary="产品详情", tags=["基金详情"])
async def product_detail(fund_code: str):
    """获取产品详情（投资理念、业绩基准、风险特征、分红等）"""
    return await safe_call(client.get_product_detail(fund_code))


@router.get("/api/fund/{fund_code}/base", summary="基金基础信息", tags=["基金详情"])
async def fund_base(fund_code: str):
    """获取基金基础信息：评分、风险等级、风格、基金经理"""
    return await safe_call(client.get_fund_base(fund_code))


@router.get("/api/fund/{fund_code}/info", summary="基金行情信息", tags=["基金详情"])
async def fund_info(fund_code: str):
    """获取基金行情：净值、涨幅、规模、交易状态"""
    return await safe_call(client.get_fund_info(fund_code))


@router.get("/api/fund/{fund_code}/flag", summary="基金标志", tags=["基金详情"])
async def fund_flag(fund_code: str):
    """获取基金标志：是否LOF/退市、二级分类"""
    return await safe_call(client.get_fund_flag(fund_code))


# ==================== 净值走势 ====================

@router.get("/api/fund/{fund_code}/nav", summary="净值走势", tags=["净值走势"])
async def nav_trend(
    fund_code: str,
    period: str = Query("year", description="year=近一年, month=近一月, nowyear=今年以来"),
):
    """获取基金净值走势图数据"""
    if period not in ("year", "month", "nowyear"):
        raise HTTPException(400, "period 必须是 year/month/nowyear")
    return await safe_call(client.get_nav_trend(fund_code, period))


@router.get("/api/fund/{fund_code}/realtime", summary="实时估值走势", tags=["净值走势"])
async def realtime_trend(fund_code: str):
    """获取实时估值分时走势（每分钟更新）"""
    return await safe_call(client.get_realtime_trend(fund_code))


# ==================== 业绩表现 ====================

@router.get("/api/fund/{fund_code}/rank", summary="阶段涨幅排名", tags=["业绩表现"])
async def performance_rank(fund_code: str):
    """获取阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
    return await safe_call(client.get_performance_rank(fund_code))


@router.get("/api/fund/{fund_code}/year_return", summary="年度收益率", tags=["业绩表现"])
async def year_return(fund_code: str):
    """获取年度收益率及同类排名"""
    return await safe_call(client.get_year_return(fund_code))


@router.get("/api/fund/{fund_code}/drawdown", summary="最大回撤", tags=["业绩表现"])
async def max_drawdown(fund_code: str):
    """获取最大回撤（近半年/近一年/近三年/成立以来）"""
    return await safe_call(client.get_max_drawdown(fund_code))


@router.get("/api/fund/{fund_code}/periodic_rate", summary="定期收益率（收益稳定度）", tags=["业绩表现"])
async def periodic_rate(
    fund_code: str,
    group: str = Query("day", description="day/week/month/quarter/year"),
):
    """获取定期收益率（收益稳定度）"""
    if group not in PERIODIC_RATE_TYPES:
        raise HTTPException(400, f"group 必须是 {'/'.join(sorted(PERIODIC_RATE_TYPES))}")
    return await safe_call(client.get_periodic_rate(fund_code, f"{group}PeriodicRate"))


@router.get("/api/fund/{fund_code}/profit", summary="收益贡献", tags=["业绩表现"])
async def profit_contribution(
    fund_code: str,
    time_type: str = Query("threeMonth", description="threeMonth/halfYear/year"),
):
    """获取收益贡献分析"""
    return await safe_call(client.get_profit_contribution(fund_code, time_type))


# ==================== 持仓信息 ====================

@router.get("/api/fund/{fund_code}/holdings", summary="前十大持仓", tags=["持仓信息"])
async def top10_holdings(fund_code: str):
    """获取前十大持仓"""
    return await safe_call(client.get_top10_holdings(fund_code))


@router.get("/api/fund/{fund_code}/holdings/overview", summary="持仓概览", tags=["持仓信息"])
async def holding_overview(fund_code: str):
    """获取持仓概览"""
    return await safe_call(client.get_holding_overview(fund_code))


@router.get("/api/fund/{fund_code}/asset_allocation", summary="资产配置", tags=["持仓信息"])
async def asset_allocation(fund_code: str, manager_id: str = ""):
    """获取资产配置"""
    return await safe_call(client.get_asset_allocation(fund_code, manager_id))


@router.get("/api/fund/{fund_code}/style", summary="投资风格偏好", tags=["持仓信息"])
async def style_preference(fund_code: str):
    """获取投资风格偏好"""
    return await safe_call(client.get_style_preference(fund_code))


@router.get("/api/fund/{fund_code}/position/dates", summary="持仓回顾日期列表", tags=["持仓信息"])
async def position_dates(fund_code: str):
    """获取持仓回顾可用的季度日期列表"""
    return await safe_call(client.get_position_dates(fund_code))


@router.get("/api/fund/{fund_code}/position/detail", summary="季度持仓明细", tags=["持仓信息"])
async def position_detail(
    fund_code: str,
    end_date: str = Query("", description="季度末日期 YYYYMMDD，如 20251231"),
):
    """获取指定季度的前十大持仓明细"""
    return await safe_call(client.get_position_detail(fund_code, end_date))


# ==================== 基金经理 ====================

@router.get("/api/fund/{fund_code}/manager", summary="基金经理信息", tags=["基金经理"])
async def manager_info(fund_code: str, manager_id: str = Query(..., description="基金经理ID，如 T191488300")):
    """获取基金经理详细信息"""
    return await safe_call(client.get_manager_info(fund_code, manager_id))


@router.get("/api/manager/{manager_id}/profile", summary="经理完整档案", tags=["基金经理"])
async def manager_profile(manager_id: str):
    """获取基金经理完整档案（简历、雷达图、管理基金列表）"""
    return await safe_call(client.get_manager_profile(manager_id))


@router.get("/api/manager/{manager_id}/invest_history", summary="经理投资历史", tags=["基金经理"])
async def manager_invest_history(manager_id: str):
    """获取基金经理投资历史（所有基金业绩、重仓股）"""
    return await safe_call(client.get_manager_invest_history(manager_id))


@router.get("/api/manager/{manager_id}/diagnose", summary="经理诊断评分", tags=["基金经理"])
async def manager_diagnose(manager_id: str):
    """获取基金经理诊断评分（历史规模、回撤、年化收益）"""
    return await safe_call(client.get_manager_diagnose(manager_id))


@router.get("/api/manager/{manager_id}/industry_prefer", summary="经理行业偏好", tags=["基金经理"])
async def manager_industry_prefer(manager_id: str):
    """获取基金经理行业偏好"""
    return await safe_call(client.get_manager_industry_prefer(manager_id))


@router.get("/api/manager/{manager_id}/represent_fund", summary="经理代表基金", tags=["基金经理"])
async def manager_represent_fund(manager_id: str):
    """获取基金经理代表基金"""
    return await safe_call(client.get_manager_represent_fund(manager_id))


# ==================== 技术面 & 大盘 & 资金流 ====================

@router.get("/api/fund/{fund_code}/nav_technical", summary="净值技术面分析", tags=["决策辅助"])
async def nav_technical(fund_code: str):
    """基金净值技术面分析（RSI14/MA5/MA20/MA60/偏离度/信号）"""
    return await safe_call(client.get_nav_technical(fund_code))


@router.get("/api/market/overview", summary="A股大盘总览", tags=["决策辅助"])
async def market_overview():
    """A股大盘总览：指数行情 + 涨跌家数 + 成交额 + 资金流向 + 涨跌停 + 大小盘对比"""
    return await safe_call(client.get_market_overview())


@router.get("/api/market/yesterday_limit", summary="昨日涨停今日表现", tags=["决策辅助"])
async def yesterday_limit():
    """昨日涨停股票今日涨跌表现（涨跌幅/振幅/换手率/连板数/封板时间）"""
    return await safe_call(client.get_yesterday_limit_performance())


@router.get("/api/market/stock_changes", summary="盘中异动", tags=["决策辅助"])
async def stock_changes(
    change_type: str = Query("all", description="异动类型: all/竞价/拉升/跳水/大单/涨停/跌停/缺口/新高新低/大幅，或具体类型如'火箭发射'"),
    count: int = Query(50, description="返回条数"),
):
    """盘中异动（火箭发射/竞价上涨/大笔买入/封涨停 等22种类型）"""
    return await safe_call(client.get_stock_changes(change_type, min(count, 200)))


@router.get("/api/market/stock_ranking", summary="个股涨跌幅排行", tags=["决策辅助"])
async def stock_ranking(
    sort: str = Query("rise", description="排序: rise=涨幅榜, fall=跌幅榜, volume=成交量, turnover=成交额, turnover_rate=换手率"),
    count: int = Query(20, description="返回条数"),
):
    """个股涨跌幅排行（涨幅榜/跌幅榜/成交榜/换手率榜）"""
    valid = {"rise", "fall", "volume", "turnover", "amplitude", "turnover_rate"}
    if sort not in valid:
        raise HTTPException(400, f"sort 必须是 {'/'.join(sorted(valid))}")
    return await safe_call(client.get_stock_ranking(sort, min(count, 50)))


@router.get("/api/market/sector_ranking", summary="板块涨跌排行", tags=["决策辅助"])
async def sector_ranking(
    sector_type: str = Query("concept", description="类型: concept=概念板块, industry=行业板块"),
    count: int = Query(20, description="返回条数"),
):
    """板块涨跌排行（概念板块/行业板块 涨跌幅榜）"""
    if sector_type not in {"concept", "industry"}:
        raise HTTPException(400, "sector_type 必须是 concept/industry")
    return await safe_call(client.get_sector_ranking(sector_type, min(count, 50)))


@router.get("/api/market/dragon_tiger", summary="龙虎榜", tags=["决策辅助"])
async def dragon_tiger(
    tab: str = Query("stock", description="维度: stock=个股明细, dept=活跃营业部(游资), org=机构买卖"),
    days: int = Query(3, description="回溯天数"),
    count: int = Query(30, description="返回条数"),
):
    """龙虎榜数据（个股明细/活跃营业部/机构买卖）"""
    if tab not in {"stock", "dept", "org"}:
        raise HTTPException(400, "tab 必须是 stock/dept/org")
    return await safe_call(client.get_dragon_tiger(tab, days, min(count, 50)))


@router.get("/api/market/ths_dragon_tiger", summary="同花顺游资龙虎榜", tags=["决策辅助"])
async def ths_dragon_tiger(
    tab: str = Query("youzi", description="维度: youzi=游资(一线+知名), jigou=机构, gfgs=跟风高手, all=全部"),
    count: int = Query(30, description="返回条数"),
):
    """同花顺龙虎榜 - 带游资/机构分类标签"""
    if tab not in {"youzi", "jigou", "gfgs", "gansidui", "all"}:
        raise HTTPException(400, "tab 必须是 youzi/jigou/gfgs/all")
    return await safe_call(client.get_ths_dragon_tiger(tab, min(count, 50)))


@router.get("/api/market/capital_flow", summary="资金流向", tags=["决策辅助"])
async def capital_flow(
    tab: str = Query("market", description="维度: market=大盘资金, north=北向资金"),
    days: int = Query(20, description="回溯天数"),
):
    """资金流向（大盘主力资金净流入 / 北向资金成交额）"""
    if tab not in {"market", "north"}:
        raise HTTPException(400, "tab 必须是 market/north")
    return await safe_call(client.get_capital_flow(tab, min(days, 60)))


@router.get("/api/market/hot_board", summary="热点板块排行", tags=["决策辅助"])
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


@router.get("/api/market/currency", summary="货币风向", tags=["决策辅助"])
async def currency_data(
    tab: str = Query("usdcny", description="维度: usdcny=美元/离岸人民币, shibor=Shibor利率+LPR"),
    days: int = Query(120, description="回溯天数"),
):
    """货币风向（美元/离岸人民币汇率走势 / Shibor利率+LPR变化）"""
    if tab not in {"usdcny", "shibor"}:
        raise HTTPException(400, "tab 必须是 usdcny/shibor")
    return await safe_call(client.get_currency_data(tab, min(days, 500)))


@router.get("/api/market/environment", summary="大盘与行业环境", tags=["决策辅助"])
async def market_environment():
    """获取沪深300趋势 + 北向资金活跃度"""
    return await safe_call(client.get_market_environment())


@router.get("/api/fund/{fund_code}/fund_flow", summary="基金申赎资金流", tags=["决策辅助"])
async def fund_flow(fund_code: str):
    """基金申赎资金流趋势（季度净申赎 + 机构占比变化）"""
    return await safe_call(client.get_fund_flow_trend(fund_code))


# ==================== 持仓股估值 ====================

@router.get("/api/fund/{fund_code}/holdings/valuation", summary="持仓股估值", tags=["持仓信息"])
async def holdings_valuation(fund_code: str):
    """获取前十大持仓股的估值数据（PE/PB/市值/ROE）"""
    return await safe_call(client.get_holdings_valuation(fund_code))


@router.get("/api/fund/{fund_code}/holdings/pe_percentile", summary="持仓股估值百分位", tags=["持仓信息"])
async def holdings_pe_percentile(
    fund_code: str,
    years: int = Query(3, description="回溯年数，默认3"),
):
    """获取前十大持仓股的估值百分位（近N年PE/PB历史分位）"""
    if years < 1 or years > 10:
        raise HTTPException(400, "years 必须在 1-10 之间")
    return await safe_call(client.get_holdings_valuation_percentile(fund_code, years))


# ==================== 交易规则与费率 ====================

@router.get("/api/fund/{fund_code}/trade_rule", summary="交易规则与费率", tags=["交易规则"])
async def trade_rule(fund_code: str):
    """获取交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
    return await safe_call(client.get_trade_rule(fund_code))


# ==================== 规模与持有人 ====================

@router.get("/api/fund/{fund_code}/scale_change", summary="规模变动历史", tags=["规模与持有人"])
async def scale_change(fund_code: str):
    """获取规模变动历史（季度净资产、申购赎回金额、份额变动）"""
    return await safe_call(client.get_scale_change(fund_code))


@router.get("/api/fund/{fund_code}/holder_ratio", summary="机构持仓比例", tags=["规模与持有人"])
async def holder_ratio(fund_code: str):
    """获取机构持仓比例历史（半年度机构持有占比变化）"""
    return await safe_call(client.get_holder_ratio(fund_code))


# ==================== 分红历史 ====================

@router.get("/api/fund/{fund_code}/dividend", summary="分红历史", tags=["分红"])
async def dividend_history(fund_code: str):
    """获取分红历史和拆分记录"""
    return await safe_call(client.get_dividend_history(fund_code))


# ==================== 指标与追踪 ====================

@router.get("/api/fund/{fund_code}/rsi", summary="RSI买卖指标", tags=["指标"])
async def rsi_indicator(fund_code: str):
    """获取RSI买卖区间指标"""
    return await safe_call(client.get_rsi_indicator(fund_code))


@router.get("/api/fund/{fund_code}/track", summary="基金追踪", tags=["指标"])
async def fund_track(fund_code: str):
    """获取基金追踪数据"""
    return await safe_call(client.get_fund_track(fund_code))


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
    return await safe_call(client.get_announcements(fund_code, category, page, page_size))


@router.get("/api/fund/{fund_code}/news", summary="基金资讯", tags=["其他"])
async def fund_news(
    fund_code: str,
    limit: int = Query(10, description="返回条数"),
):
    """获取基金相关资讯"""
    return await safe_call(client.get_news(fund_code, limit))


# ==================== 基金排行与筛选 ====================

@router.post("/api/fund/ranking", summary="基金排行", tags=["基金排行"])
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


@router.get("/api/fund/ranking/boards", summary="排行榜配置", tags=["基金排行"])
async def rank_board_config():
    """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
    return await safe_call(client.get_rank_board_config())


@router.get("/api/fund/ranking/filters", summary="筛选策略配置", tags=["基金排行"])
async def rank_filter_config():
    """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
    return await safe_call(client.get_rank_filter_config())


@router.get("/api/fund/ranking/distribution", summary="收益率分布", tags=["基金排行"])
async def rank_distribution():
    """获取收益率分布统计（各周期的收益率分布）"""
    return await safe_call(client.get_rank_distribution())


# ==================== 同类基金对比 ====================

@router.get("/api/fund/{fund_code}/similar", summary="发现同赛道基金", tags=["基金对比"])
async def find_similar_funds(
    fund_code: str,
    top_n: int = Query(5, description="返回数量，默认5"),
):
    """自动发现同赛道基金（基于行业分布相似度）"""
    return await safe_call(client.find_similar_funds(fund_code, top_n))


@router.get("/api/funds/compare", summary="多基金横向对比", tags=["基金对比"])
async def fund_compare(
    codes: str = Query(..., description="基金代码，逗号分隔，如 006888,022364"),
):
    """多基金横向对比数据"""
    fund_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not fund_codes or len(fund_codes) > 10:
        raise HTTPException(400, "需要 1-10 只基金代码")
    return await safe_call(client.get_fund_compare_data(fund_codes))


# ==================== 热榜数据 ====================

@router.get("/api/hotlist/stocks", summary="个股热榜", tags=["热榜"])
async def hot_stocks(
    market: str = Query("a", description="市场: a=A股, hk=港股, us=美股"),
):
    """获取个股热榜（热度排名前100）"""
    if market not in HOTLIST_MARKETS:
        raise HTTPException(400, f"market 必须是 {'/'.join(sorted(HOTLIST_MARKETS))}")
    return await safe_call(client.get_hot_stocks(market))


@router.get("/api/hotlist/plate", summary="概念/行业热榜", tags=["热榜"])
async def hot_plate(
    plate_type: str = Query("concept", description="类型: concept=概念, industry=行业"),
):
    """获取概念或行业热榜"""
    if plate_type not in HOTLIST_PLATE_TYPES:
        raise HTTPException(400, f"plate_type 必须是 {'/'.join(sorted(HOTLIST_PLATE_TYPES))}")
    return await safe_call(client.get_hot_plate(plate_type))


@router.get("/api/hotlist/etf", summary="ETF 热榜", tags=["热榜"])
async def hot_etf():
    """获取 ETF 热度排行"""
    return await safe_call(client.get_hot_etf())


@router.get("/api/hotlist/futures", summary="期货热榜", tags=["热榜"])
async def hot_futures():
    """获取期货热度排行"""
    return await safe_call(client.get_hot_futures())


@router.get("/api/hotlist/bond", summary="可转债热榜", tags=["热榜"])
async def hot_bond():
    """获取可转债热度排行"""
    return await safe_call(client.get_hot_bond())


@router.get("/api/hotlist/topics", summary="热榜话题", tags=["热榜"])
async def hot_topics():
    """获取同花顺热榜话题（15条）"""
    return await safe_call(client.get_hot_topics())


@router.get("/api/hotlist/posts", summary="热门文章", tags=["热榜"])
async def hot_posts(
    page: int = Query(1, description="页码"),
    page_size: int = Query(10, description="每页条数"),
):
    """获取热门文章"""
    return await safe_call(client.get_hot_posts(page, page_size))


@router.get("/api/hotlist/topic/{code}", summary="话题详情", tags=["热榜"])
async def topic_detail(
    code: str,
    page: int = Query(1, description="帖子页码"),
    page_size: int = Query(10, description="每页帖子数"),
):
    """获取话题详情及推荐帖子"""
    return await safe_call(client.get_topic_detail(code, page, page_size))


@router.get("/api/hotlist/special/{code}", summary="专题详情", tags=["热榜"])
async def special_detail(code: str):
    """获取专题详情（从 HTML 解析）"""
    return await safe_call(client.get_special_detail(code))


@router.get("/api/headlines", summary="推荐头条", tags=["热榜"])
async def headlines():
    """获取推荐头条（首页推荐tab头条模块）"""
    return await safe_call(client.get_headlines())


@router.get("/api/article/{encoded_seq}", summary="新闻文章详情", tags=["热榜"])
async def article_detail(encoded_seq: str):
    """获取新闻文章详情（type=1 类型的新闻）"""
    return await safe_call(client.get_article_detail(encoded_seq))


@router.get("/api/news_themes", summary="新闻主题分类", tags=["热榜"])
async def news_themes():
    """获取新闻主题分类列表（资讯->头条 tab 栏的主题标签，如小金属、有色金属、算力租赁等）"""
    return await safe_call(client.get_news_themes())


@router.get("/api/theme/{theme_id}/articles", summary="主题文章列表", tags=["热榜"])
async def theme_articles(
    theme_id: str,
    page: int = Query(1, description="页码"),
    size: int = Query(15, description="每页条数"),
):
    """获取主题下的文章列表（含标题、来源、阅读量、相关股票）"""
    return await safe_call(client.get_theme_articles(theme_id, page, size))


@router.get("/api/flash_news/tabs", summary="快讯分类标签", tags=["热榜"])
async def flash_news_tabs():
    """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
    return await safe_call(client.get_flash_news_tabs())


@router.get("/api/flash_news/list", summary="分类快讯列表", tags=["热榜"])
async def flash_news_list(
    tag_id: int = Query(21101, description="分类ID: 21101=A股, 62857=重要, 34843=公告, 33775=期货, 21111=异动, 21105=港股, 21107=美股"),
    seq: int = Query(0, description="翻页游标，0=最新，传上一页最后一条的seq加载更早"),
):
    """获取指定分类的快讯列表（每次约20条）"""
    return await safe_call(client.get_flash_news_list(tag_id, seq))


@router.get("/api/news_feed", summary="滚动快讯", tags=["热榜"])
async def news_feed(page: int = Query(1, description="页码")):
    """获取滚动快讯（财经要闻，每页20条）"""
    return await safe_call(client.get_news_feed(page))


@router.get("/api/news_overview", summary="新闻概览", tags=["热榜"])
async def news_overview(limit: int = Query(10, description="每类新闻的条数限制")):
    """获取新闻概览（重要快讯 + A股快讯 + 滚动快讯）"""
    try:
        # 并发获取三类新闻
        important_news, a_stock_news, feed_news = await asyncio.gather(
            client.get_flash_news_list(tag_id=62857, seq=0),  # 重要
            client.get_flash_news_list(tag_id=21101, seq=0),  # A股
            client.get_news_feed(page=1),  # 滚动快讯
            return_exceptions=True
        )

        # 提取新闻列表
        def extract_news(data, news_limit=limit):
            if isinstance(data, Exception):
                return {"error": str(data), "items": []}

            # 快讯格式: {status_code, status_msg, data: {list: [...]}}
            if "data" in data and "list" in data["data"]:
                items = data["data"]["list"][:news_limit]
                return {
                    "count": len(items),
                    "items": items
                }
            # 滚动快讯格式可能不同
            elif "result" in data:
                result = data["result"]
                if "data" in result and "items" in result["data"]:
                    items = result["data"]["items"][:news_limit]
                    return {
                        "count": len(items),
                        "items": items
                    }

            return {"error": "Unknown format", "items": []}

        return {
            "status": "success",
            "data": {
                "important": extract_news(important_news),
                "a_stock": extract_news(a_stock_news),
                "feed": extract_news(feed_news)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取新闻概览失败: {e}")


# ==================== 认证管理 ====================

logger = logging.getLogger("auth")


def _decode_jwt_exp(jwt_token: str):
    """解析同花顺 JWT 获取过期时间（秒级时间戳）"""
    if not jwt_token:
        return None
    for part_idx in (1, 0):
        try:
            parts = jwt_token.split(".")
            if len(parts) <= part_idx:
                continue
            segment = parts[part_idx]
            pad = 4 - len(segment) % 4
            if pad != 4:
                segment += "=" * pad
            decoded = base64.urlsafe_b64decode(segment)
            data = json.loads(decoded)
            exp = data.get("exp")
            if exp is not None:
                if exp > 10000000000:
                    exp = exp // 1000
                return exp
        except Exception:
            continue
    return None


def _get_auth_expires_at():
    """读取 auth_cache.json 中的过期时间，返回 (expires_at, is_expired)"""
    cache_file = Path(__file__).parent.parent / "auth_cache.json"
    if not cache_file.exists():
        return None, True
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = data.get("expires_at")
        if not expires_at:
            return None, False  # 无过期信息，假设有效
        return expires_at, int(time.time()) >= expires_at
    except Exception:
        return None, True


async def _login_by_password():
    """使用密码登录获取 token（会踢掉手机端登录）

    Returns:
        成功返回 auth 数据 dict，失败返回 None
    """
    import httpx

    config_file = Path(__file__).parent.parent / "config.json"
    if not config_file.exists():
        logger.warning("config.json 不存在，无法使用密码登录")
        return None

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    account = config.get("trade_account")
    password = config.get("trade_password")
    if not account or not password:
        logger.warning("config.json 中未设置 trade_account 或 trade_password")
        return None

    password_md5 = hashlib.md5(password.encode()).hexdigest().upper()
    device_id = "7246091a5f126b63"
    device_sign = "2293a78f6581c12bbb334759458d4de3"

    url = "https://trade.5ifund.com/rz/account/login/noauth/v1/result/safe/check"
    headers = {
        "token": "-1",
        "custId": "-1",
        "source": "SDK",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
        "Client-Referer": "",
        "Host": "trade.5ifund.com",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
    }
    form_data = {
        "key1": device_id,
        "uId": device_id,
        "key2": device_sign,
        "password": password_md5,
        "ipAddress": "null",
        "thsUserId": "690359103",
        "device": "OnePlus PLQ110",
        "deviceName": "OnePlus ",
        "account": account,
        "loginSource": "SDK",
        "operator": "145",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, headers=headers, data=form_data)
            resp.raise_for_status()
            result = resp.json()

        if result.get("code") != "0000":
            logger.warning(f"密码登录失败: {result.get('message', '未知错误')}")
            return None

        data_field = result.get("data", {})
        key3 = data_field.get("key3") or data_field.get("custId") or account
        key5 = data_field.get("key5")
        if not key5:
            logger.warning("登录响应中未找到 key5")
            return None

        cookie = ""
        if "set-cookie" in resp.headers:
            cookie = resp.headers["set-cookie"]

        expires_at = _decode_jwt_exp(key5)

        return {
            "auth": {
                "key1": device_id,
                "key2": device_sign,
                "key3": key3,
                "key4": data_field.get("key4", "auth"),
                "key5": key5,
                "userId": key3,
                "sessionId": "",
                "cookie": cookie,
                "account": key3,
            },
            "expires_at": expires_at,
            "sync_source": "server_password_login",
        }
    except Exception as e:
        logger.error(f"密码登录异常: {e}")
        return None


def _save_auth_cache(cache_data: dict):
    """保存认证数据到 auth_cache.json 并刷新内存"""
    cache_file = Path(__file__).parent.parent / "auth_cache.json"
    cache_data.setdefault("last_sync", int(time.time()))
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, ensure_ascii=False)
    if client:
        client.reload_auth_if_updated()


# ---------- 后台自动刷新 ----------

_auto_refresh_task = None


async def start_auth_auto_refresh():
    """启动后台定时检查 token 是否快过期，自动密码登录续期"""
    global _auto_refresh_task
    if _auto_refresh_task is not None:
        return
    _auto_refresh_task = asyncio.create_task(_auth_auto_refresh_loop())
    logger.info("Auth 自动刷新后台任务已启动")


async def _auth_auto_refresh_loop():
    """每小时检查一次，token 剩余 < 3 天时自动密码登录续期"""
    REFRESH_THRESHOLD = 3 * 24 * 3600  # 3天
    CHECK_INTERVAL = 3600  # 1小时

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            expires_at, is_expired = _get_auth_expires_at()
            if expires_at is None:
                continue

            remaining = expires_at - int(time.time())
            if remaining > REFRESH_THRESHOLD:
                continue

            status = "已过期" if is_expired else f"剩余 {remaining // 3600} 小时"
            logger.info(f"Token {status}，自动密码登录续期...")

            result = await _login_by_password()
            if result:
                _save_auth_cache(result)
                logger.info(f"自动续期成功，新过期时间: {result.get('expires_at')}")
            else:
                logger.error("自动续期失败，密码登录未返回有效数据")
        except Exception as e:
            logger.error(f"自动刷新异常: {e}")


# ---------- API 端点 ----------

@router.get("/api/auth/status", summary="认证状态", tags=["交易账户"])
async def auth_status():
    """查看认证参数缓存状态"""
    try:
        cache_file = Path(__file__).parent.parent / "auth_cache.json"
        if not cache_file.exists():
            return {
                "status": "error",
                "message": "认证缓存不存在"
            }

        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        expires_at = data.get("expires_at")
        if expires_at:
            now = int(time.time())
            remaining_seconds = expires_at - now
            remaining_days = remaining_seconds // (24 * 3600)
            is_expired = now >= expires_at
            expires_time = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        else:
            remaining_days = None
            is_expired = None
            expires_time = None

        return {
            "status": "success",
            "data": {
                "expires_at": expires_time,
                "remaining_days": remaining_days,
                "is_expired": is_expired,
                "status": "expired" if is_expired else "valid",
                "sync_source": data.get("sync_source", "unknown"),
                "last_sync": data.get("last_sync"),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取认证状态失败: {e}")


@router.post("/api/auth/refresh", summary="推送认证Token", tags=["交易账户"])
async def auth_refresh(body: dict = Body(...)):
    """从 Hook 或客户端推送 token 数据并保存到 auth_cache.json

    请求体格式:
    {
        "auth": {"key1": "...", "key2": "...", "key3": "...", "key4": "auth", "key5": "...", "userId": "...", "sessionId": "...", "cookie": "..."},
        "expires_at": 1234567890,
        "sync_source": "zygisk_auto"
    }
    """
    try:
        auth = body.get("auth")
        if not isinstance(auth, dict) or not auth.get("key1") or not auth.get("key5"):
            raise HTTPException(status_code=400, detail="缺少必要字段: auth.key1, auth.key5")

        cache_data = {
            "auth": auth,
            "expires_at": body.get("expires_at"),
            "last_sync": int(time.time()),
            "sync_source": body.get("sync_source", "client_push")
        }

        _save_auth_cache(cache_data)

        return {
            "status": "success",
            "message": "认证参数已更新",
            "account": auth.get("key3", "")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新认证失败: {e}")


@router.post("/api/auth/login", summary="密码登录获取Token", tags=["交易账户"])
async def auth_login():
    """服务端直接使用 config.json 中的账号密码登录获取 token（会踢掉手机端）"""
    result = await _login_by_password()
    if not result:
        raise HTTPException(status_code=500, detail="密码登录失败，请检查 config.json 中的 trade_account/trade_password")

    _save_auth_cache(result)

    expires_at = result.get("expires_at")
    expires_time = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S") if expires_at else "未知"

    return {
        "status": "success",
        "message": "密码登录成功，token 已更新",
        "account": result["auth"].get("key3", ""),
        "expires_at": expires_time,
    }


# ==================== 交易账户 ====================

@router.get("/api/trade/overview", summary="账户总览", tags=["交易账户"])
async def trade_overview():
    """账户总览（总资产、累计盈亏、当日盈亏、风险等级等）"""
    return await safe_call(client.get_account_overview())


@router.get("/api/trade/positions", summary="基金持仓", tags=["交易账户"])
async def trade_positions():
    """基金持仓列表（持仓基金明细、市值、收益等）"""
    return await safe_call(client.get_fund_positions())


@router.get("/api/trade/wallet", summary="活期宝", tags=["交易账户"])
async def trade_wallet():
    """活期宝/超级T+0（货币基金收益、可用余额等）"""
    return await safe_call(client.get_wallet_info())


@router.get("/api/trade/wallet/home", summary="钱包首页", tags=["交易账户"])
async def trade_wallet_home():
    """钱包首页（活期宝余额、冻结金额、累计收益等）"""
    return await safe_call(client.get_wallet_home())


@router.get("/api/trade/autoinvest/list", summary="定投计划列表", tags=["交易账户"])
async def trade_autoinvest_list(
    page_size: int = Query(20, description="每页数量"),
    status: str = Query("N", description="状态: N=全部, 1=执行中, 2=暂停"),
):
    """定投计划列表"""
    return await safe_call(client.get_auto_invest_list(page_size, status))


@router.get("/api/trade/autoinvest/summary", summary="定投汇总", tags=["交易账户"])
async def trade_autoinvest_summary():
    """定投汇总（总金额、下次执行日期、正常/暂停计划数等）"""
    return await safe_call(client.get_auto_invest_summary())


@router.get("/api/trade/binding", summary="账户绑定信息", tags=["交易账户"])
async def trade_binding():
    """账户绑定信息（客户ID、姓名、身份证号等）"""
    return await safe_call(client.get_account_binding())


@router.get("/api/trade/all", summary="全部交易数据", tags=["交易账户"])
async def trade_all():
    """一次性获取所有交易账户数据"""
    return await safe_call(client.get_trade_account_all())


@router.post("/api/trade/auth", summary="更新交易认证", tags=["交易账户"])
async def trade_auth_update(req: TradeAuthUpdate = Body(...)):
    """更新交易认证参数（token 过期后需要从 Hook 重新捕获）"""
    client.update_trade_auth(
        key1=req.key1, key2=req.key2, key3=req.key3, key5=req.key5,
        user_id=req.user_id, session_id=req.session_id, cookie=req.cookie,
    )
    return {"status": "ok", "message": "交易认证参数已更新"}


# ==================== 基金交易 ====================

@router.post("/api/trade/password", summary="设置交易密码", tags=["基金交易"])
async def trade_password_update(req: TradePasswordUpdate):
    """设置交易密码（明文）"""
    client.update_trade_password(req.password)
    return {"status": "ok", "message": "交易密码已设置"}


@router.post("/api/trade/buy", summary="买入基金", tags=["基金交易"])
async def trade_buy(req: BuyFundRequest):
    """买入基金（完整流程：初始化->检查->下单）"""
    # Step 0: 买入初始化，检查 maxBuy
    try:
        init_resp = await client._proxy_request(
            "/rz/trade/dubbo/subscribe/init",
            body=f"fundCode={req.fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        # _proxy_request 已经解析好了 result 层，直接取 data
        init_data = init_resp.get("data", init_resp)

        # 调试输出
        print(f"init_resp keys: {list(init_resp.keys())}")
        print(f"init_data keys: {list(init_data.keys())[:10]}")
        print(f"maxBuy raw: {init_data.get('maxBuy')}")
        print(f"minBuy raw: {init_data.get('minBuy')}")

        max_buy = float(init_data.get("maxBuy", 0))
        min_buy = float(init_data.get("minBuy", 0))
        fund_name = init_data.get("paramOpenFundAccBean", {}).get("fundName", req.fund_code)

        # 保存限额信息到数据库（无论是否能买入都保存）
        try:
            # max_buy 是当前可买金额（会随待确认订单变化）
            # 如果 max_buy 很大（如 99999999999999.99），说明是基金本身的每日限额
            daily_limit = max_buy if max_buy > 1000000 else 0
            fund_db.save_fund_limit(
                fund_code=req.fund_code,
                fund_name=fund_name,
                min_buy=min_buy,
                max_buy=max_buy,
                daily_limit=daily_limit,
                is_suspended=False
            )
        except Exception as e:
            print(f"保存限额信息失败: {e}")

        # 检查待确认额度
        if max_buy < req.amount:
            return {
                "status": "error",
                "message": f"买入失败：待确认额度不足",
                "details": {
                    "fund_code": req.fund_code,
                    "fund_name": fund_name,
                    "requested_amount": req.amount,
                    "max_buy": max_buy,
                    "min_buy": min_buy,
                    "reason": "待确认订单占用额度，需等待现有订单确认后才能继续购买"
                }
            }

        # 检查最小买入金额
        if req.amount < min_buy:
            return {
                "status": "error",
                "message": f"买入失败：低于最小买入金额",
                "details": {
                    "fund_code": req.fund_code,
                    "fund_name": fund_name,
                    "requested_amount": req.amount,
                    "min_buy": min_buy
                }
            }
    except Exception as e:
        # 初始化失败，可能是暂停申购，记录下来
        try:
            fund_db.mark_fund_suspended(req.fund_code, reason=str(e))
        except:
            pass
        raise HTTPException(status_code=500, detail=f"买入初始化失败: {e}")

    # 调用同花顺 API 买入
    result = await safe_call(client.buy_fund(req.fund_code, req.amount, req.use_wallet, req.password))

    # 解析结果
    # buy_fund 返回格式: {"fund_code", "fund_name", "amount", "app_sheet_serial_no", "raw_response": {...}}
    # 如果 buy_fund 能正常返回（不抛异常），说明购买已成功
    order_no = result.get("app_sheet_serial_no") or result.get("appSheetSerialNo")
    fund_name = result.get("fund_name", "")

    # 检查订单号是否存在
    if not order_no:
        # 从 raw_response 中提取错误信息
        raw_resp = result.get("raw_response", {})
        error_code = -1
        code = ""
        message = raw_resp.get("message", "未知错误")

        return {
            "status": "error",
            "message": f"买入失败: {message}",
            "details": {
                "fund_code": req.fund_code,
                "error_code": error_code,
                "code": code,
                "raw_message": message,
                "raw_result": result
            }
        }

    # 保存交易记录到数据库
    try:
        fund_db.save_trade(
            fund_code=req.fund_code,
            fund_name=fund_name,
            action="buy",
            amount=req.amount,
            shares=None,
            order_no=order_no,
            reason=req.reason,
            api_response=result
        )
    except Exception as e:
        # 即使保存失败也返回成功结果（因为买入已经成功了）
        pass

    return {
        "status": "success",
        "message": f"买入成功: {fund_name}",
        "data": {
            "fund_code": req.fund_code,
            "fund_name": fund_name,
            "amount": req.amount,
            "order_no": order_no,
            "raw_result": result
        }
    }


@router.get("/api/trade/order/{order_no}", summary="查询订单", tags=["基金交易"])
async def trade_order_detail(order_no: str):
    """查询交易订单详情"""
    return await safe_call(client.get_order_detail(order_no))


@router.get("/api/trade/orders", summary="订单列表", tags=["基金交易"])
async def trade_order_list(
    days: int = Query(30, description="查询天数，默认30天"),
    op_type: str = Query("all", description="操作类型: all=全部"),
    limit: int = Query(20, description="每页条数"),
    offset: int = Query(1, description="页码（从1开始）"),
):
    """查询交易订单列表"""
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    raw_result = await safe_call(client.get_order_list(start_date, end_date, op_type, limit, offset))

    # 包装成统一格式
    return {
        "status": "success",
        "data": raw_result
    }


@router.post("/api/trade/sell", summary="赎回基金", tags=["基金交易"])
async def trade_sell(req: SellFundRequest):
    """赎回基金（完整流程：获取持仓->初始化->提交赎回）"""
    share_vol_str = f"{req.share_vol:.2f}" if req.share_vol else None
    return await safe_call(client.sell_fund(req.fund_code, share_vol_str, req.sell_all, req.password))


@router.post("/api/trade/cancel", summary="撤销订单", tags=["基金交易"])
async def trade_cancel(req: CancelOrderRequest):
    """撤销交易订单"""
    return await safe_call(client.cancel_order(req.order_no, req.password))


# ==================== 个股查询 ====================

@router.get("/api/stock/quote", summary="个股实时行情", tags=["个股查询"])
async def stock_quote(
    codes: str = Query(..., description="股票代码，逗号分隔，如 600519,000001"),
):
    """获取个股实时行情（腾讯证券数据源，支持批量最多20只）"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(400, "codes 不能为空")
    return await safe_call(client.get_stock_quote(code_list))


@router.get("/api/stock/{code}/kline", summary="个股K线", tags=["个股查询"])
async def stock_kline(
    code: str,
    period: str = Query("101", description="周期: 101=日K, 102=周K, 103=月K"),
    limit: int = Query(60, description="返回条数"),
):
    """获取个股K线数据（前复权）"""
    if period not in {"101", "102", "103"}:
        raise HTTPException(400, "period 必须是 101/102/103")
    return await safe_call(client.get_stock_kline(code, period, min(limit, 500)))


@router.get("/api/stock/{code}/capital_flow", summary="个股资金流", tags=["个股查询"])
async def stock_capital_flow(
    code: str,
    days: int = Query(20, description="回溯天数"),
):
    """获取个股资金流向（主力/超大单/大单/中单/小单）"""
    return await safe_call(client.get_stock_capital_flow(code, min(days, 120)))


@router.get("/api/stock/{code}/valuation", summary="个股估值历史", tags=["个股查询"])
async def stock_valuation(
    code: str,
    years: int = Query(3, description="回溯年数"),
):
    """获取个股 PE_TTM/PB 历史数据"""
    data = await safe_call(client.get_stock_valuation_history(code, min(years, 10)))
    return {"status_code": 0, "data": {"code": code, "total": len(data), "items": data}}


@router.get("/api/stock/{code}/financial", summary="个股财务数据", tags=["个股查询"])
async def stock_financial(
    code: str,
    limit: int = Query(10, description="返回报告期数"),
):
    """获取个股财务数据（EPS/营收/净利/ROE/毛利率等）"""
    return await safe_call(client.get_stock_financial(code, min(limit, 50)))


# ==================== 风控模块 ====================

@router.get("/api/risk/snapshot", summary="风控快照", tags=["风控模块"])
async def risk_snapshot():
    """输出当前持仓/仓位/可用资金/各基金风控状态"""
    # risk_manager will be moved to services/ later
    from services import risk_manager

    # 捕获 print 输出
    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.snapshot()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        # 包装成统一格式
        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"风控快照失败: {e}")


@router.post("/api/risk/check", summary="校验决策", tags=["风控模块"])
async def risk_check(decisions: dict = Body(...)):
    """校验 LLM 决策是否违反硬约束"""
    # risk_manager will be moved to services/ later
    from services import risk_manager

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.check(decisions)
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return json.loads(output)
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"决策校验失败: {e}")


@router.get("/api/risk/preflight", summary="交易前置检查", tags=["风控模块"])
async def risk_preflight():
    """交易前置检查：交易时间、交易日、熔断机制"""
    # risk_manager will be moved to services/ later
    from services import risk_manager

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.preflight()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        # 包装成统一格式
        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"前置检查失败: {e}")


# ==================== 量化信号模块 ====================

@router.post("/api/indicators/evaluate", summary="计算量化信号", tags=["量化信号"])
async def evaluate_indicators():
    """对基金池中每只基金计算量化信号"""
    # indicators will be moved to services/ later
    from services import indicators

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        indicators.cmd_evaluate()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        # 包装成统一格式
        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"量化信号计算失败: {e}")


# ==================== 决策复盘模块 ====================

@router.post("/api/review/execute", summary="执行决策复盘", tags=["决策复盘"])
async def execute_review(limit: int = Query(30, description="复盘数量"), days_back: int = Query(7, description="回溯天数")):
    """执行待复盘决策的复盘，返回复盘统计结果"""
    # review_decision_executor will be moved to services/ later
    from services import review_decision_executor

    try:
        result = review_decision_executor.execute_decision_review(limit, days_back)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"决策复盘失败: {e}")


@router.post("/api/review/create", summary="创建待复盘记录", tags=["决策复盘"])
async def create_reviews(decision_date: str = Query(None, description="决策日期 YYYY-MM-DD")):
    """从 ft_decisions 创建待复盘记录到 ft_reviews"""
    try:
        count = fund_db.create_reviews_from_decisions(decision_date)
        return {
            "status": "success",
            "message": f"创建了 {count} 条待复盘记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建待复盘记录失败: {e}")


@router.get("/api/review/pending", summary="获取待复盘决策", tags=["决策复盘"])
async def get_pending_reviews(days_back: int = Query(3, description="回溯天数")):
    """获取待复盘的决策列表"""
    try:
        reviews = fund_db.get_pending_reviews(days_back)
        return {
            "status": "success",
            "data": reviews,
            "count": len(reviews)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取待复盘决策失败: {e}")


@router.get("/api/review/stats", summary="获取复盘统计", tags=["决策复盘"])
async def get_review_statistics(days: int = Query(30, description="统计天数")):
    """获取复盘统计数据"""
    try:
        stats = fund_db.get_review_stats(days)
        return {
            "status": "success",
            "data": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取复盘统计失败: {e}")


@router.get("/api/lessons", summary="获取经验知识库", tags=["决策复盘"])
async def get_lessons(
    category: str = Query(None, description="经验分类"),
    min_confidence: str = Query(None, description="最低可信度"),
    include_deprecated: bool = Query(False, description="包含已废弃的经验"),
    limit: int = Query(20, description="返回数量限制")
):
    """获取经验知识库"""
    try:
        lessons = fund_db.get_lessons(category, min_confidence, include_deprecated, limit)
        return {
            "status": "success",
            "data": lessons,
            "count": len(lessons)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取经验知识库失败: {e}")


@router.post("/api/lessons/save", summary="保存经验教训", tags=["决策复盘"])
async def save_lesson(lesson: dict):
    """保存一条经验教训到 ft_lessons 表"""
    try:
        lesson_id = fund_db.save_lesson(
            category=lesson["category"],
            trigger_pattern=lesson["trigger_pattern"],
            expected_outcome=lesson["expected_outcome"],
            actual_outcome=lesson["actual_outcome"],
            lesson_text=lesson["lesson_text"],
            confidence=lesson.get("confidence", "low"),
            related_sectors=lesson.get("related_sectors"),
            tags=lesson.get("tags"),
            source_review_ids=lesson.get("source_review_ids")
        )
        return {
            "status": "success",
            "message": "经验教训保存成功",
            "lesson_id": lesson_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存经验教训失败: {e}")


@router.post("/api/lessons/update-confidence/{lesson_id}", summary="更新经验可信度", tags=["决策复盘"])
async def update_lesson_confidence(lesson_id: int, success: bool):
    """更新经验教训的可信度"""
    try:
        fund_db.update_lesson_confidence(lesson_id, success)
        return {
            "status": "success",
            "message": f"经验 {lesson_id} 可信度已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新经验可信度失败: {e}")


@router.post("/api/review/update/{review_id}", summary="更新复盘结果", tags=["决策复盘"])
async def update_review(review_id: int, review_data: dict):
    """更新复盘结果到 ft_reviews 表"""
    try:
        fund_db.update_review(
            review_id=review_id,
            nav_at_decision=review_data.get("nav_at_decision"),
            nav_t1=review_data.get("nav_t1"),
            nav_t2=review_data.get("nav_t2"),
            change_t1_pct=review_data.get("change_t1_pct"),
            change_t2_pct=review_data.get("change_t2_pct"),
            outcome=review_data.get("outcome"),
            review_notes=review_data.get("review_notes")
        )
        return {
            "status": "success",
            "message": f"复盘结果 {review_id} 已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新复盘结果失败: {e}")


@router.post("/api/review/mark-extracted/{review_id}", summary="标记经验已提取", tags=["决策复盘"])
async def mark_lesson_extracted(review_id: int):
    """标记复盘记录的经验已提取"""
    try:
        fund_db.mark_lesson_extracted(review_id)
        return {
            "status": "success",
            "message": f"复盘 {review_id} 已标记为经验已提取"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"标记经验已提取失败: {e}")


# ==================== 决策管理模块 ====================

@router.post("/api/decisions/save", summary="保存决策", tags=["决策管理"])
async def save_decision(decision: dict):
    """保存决策到 ft_decisions 表"""
    try:
        fund_db.save_decision(
            fund_code=decision["fund_code"],
            fund_name=decision["fund_name"],
            action=decision["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
            market_view=decision.get("market_view"),
            amount=decision.get("amount"),
            sell_pct=decision.get("sell_pct"),
            risk_notes=decision.get("risk_notes"),
            referenced_lesson_ids=decision.get("referenced_lesson_ids", [])
        )
        return {
            "status": "success",
            "message": "决策保存成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存决策失败: {e}")


@router.post("/api/decisions/save-pending", summary="保存待确认决策", tags=["决策管理"])
async def save_pending_decision(decision: dict):
    """保存待确认的决策到 ft_pending_decisions 表"""
    try:
        fund_db.save_pending_decision(
            fund_code=decision["fund_code"],
            fund_name=decision["fund_name"],
            action=decision["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
            market_view=decision.get("market_view"),
            market_phase=decision.get("market_phase"),
            amount=decision.get("amount"),
            sell_pct=decision.get("sell_pct"),
            risk_notes=decision.get("risk_notes")
        )
        return {
            "status": "success",
            "message": "待确认决策保存成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存待确认决策失败: {e}")


@router.post("/api/decisions/execute-pending/{pending_id}", summary="执行待确认决策", tags=["决策管理"])
async def execute_pending_decision(pending_id: int):
    """标记待确认决策为已执行"""
    try:
        fund_db.execute_pending_decision(pending_id)
        return {
            "status": "success",
            "message": f"待确认决策 {pending_id} 已标记为执行"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行待确认决策失败: {e}")


@router.get("/api/decisions/today", summary="获取今日决策", tags=["决策管理"])
async def get_today_decisions():
    """获取今日所有决策记录"""
    try:
        decisions = fund_db.get_today_decisions()
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取今日决策失败: {e}")


@router.get("/api/decisions/recent", summary="获取最近决策", tags=["决策管理"])
async def get_recent_decisions(
    days: int = Query(5, description="回溯天数"),
    exclude_today: bool = Query(False, description="排除今日决策")
):
    """获取最近几天的决策记录"""
    try:
        decisions = fund_db.get_recent_decisions(days, exclude_today)
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最近决策失败: {e}")


@router.get("/api/decisions/watch-streaks", summary="获取连续观望天数", tags=["决策管理"])
async def get_watch_streaks():
    """获取各基金的连续观望天数"""
    try:
        streaks = fund_db.get_watch_streaks()
        return {
            "status": "success",
            "data": streaks
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取观望天数失败: {e}")


# ==================== 持仓查询模块 ====================

@router.get("/api/position", summary="查询所有持仓", tags=["持仓管理"])
async def get_all_positions():
    """从 ft_positions 表查询所有持仓"""
    try:
        positions = fund_db.get_positions()
        return {
            "status": "success",
            "data": positions,
            "count": len(positions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


@router.get("/api/position/{fund_code}", summary="查询指定基金持仓", tags=["持仓管理"])
async def get_single_position(fund_code: str):
    """从 ft_positions 表查询指定基金持仓"""
    try:
        positions = fund_db.get_positions()
        for p in positions:
            if p.get("fund_code") == fund_code:
                return {
                    "status": "success",
                    "data": p
                }
        return {
            "status": "error",
            "message": f"未找到基金 {fund_code} 的持仓记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


# ==================== 本地订单查询 ====================

@router.get("/api/trades", summary="查询本地交易记录", tags=["持仓管理"])
async def get_local_trades(
    days: int = Query(30, description="查询天数"),
    limit: int = Query(100, description="返回条数")
):
    """从 ft_trades 表查询本地交易记录（不需要同花顺登录）"""
    try:
        trades = fund_db.get_trades(days=days, limit=limit)
        return {
            "status": "success",
            "data": trades,
            "count": len(trades)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询交易记录失败: {e}")


# ==================== 基金限额模块 ====================

@router.get("/api/limits/summary", summary="限额统计摘要", tags=["基金限额"])
async def get_limits_summary():
    """获取限额统计信息"""
    try:
        summary = fund_db.get_fund_limits_summary()
        return {
            "status": "ok",
            "data": {
                "total": summary["total"],
                "suspended": summary["suspended"],
                "min_buy_10": summary["min_buy_10"],
                "min_buy_100": summary["min_buy_100"],
                "max_buy_1000": summary["max_buy_1000"],
                "total_available": float(summary["total_available"]) if summary["total_available"] else 0,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询统计失败: {e}")


@router.get("/api/limits", summary="批量查询限额", tags=["基金限额"])
async def get_fund_limits(
    codes: str = Query(None, description="基金代码列表，逗号分隔"),
    min_buy_lte: float = Query(None, description="最小买入金额上限"),
    include_suspended: bool = Query(False, description="是否包含暂停申购的基金")
):
    """批量查询基金限额信息"""
    try:
        fund_codes = codes.split(",") if codes else None
        limits = fund_db.get_fund_limits(
            fund_codes=fund_codes,
            min_buy_lte=min_buy_lte,
            include_suspended=include_suspended
        )
        return {
            "status": "ok",
            "count": len(limits),
            "data": [
                {
                    "fund_code": l["fund_code"],
                    "fund_name": l["fund_name"],
                    "min_buy": float(l["min_buy"]) if l["min_buy"] else 0,
                    "max_buy": float(l["max_buy"]) if l["max_buy"] else 0,
                    "is_suspended": l["is_suspended"],
                    "last_checked_at": str(l["last_checked_at"]) if l["last_checked_at"] else None,
                }
                for l in limits
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询限额失败: {e}")


@router.get("/api/limits/{fund_code}", summary="查询单个基金限额", tags=["基金限额"])
async def get_fund_limit(fund_code: str):
    """查询单个基金的买入限额信息"""
    try:
        limit_info = fund_db.get_fund_limit(fund_code)
        if not limit_info:
            return {"status": "not_found", "fund_code": fund_code, "message": "暂无限额信息，需要先尝试买入获取"}
        return {
            "status": "ok",
            "data": {
                "fund_code": limit_info["fund_code"],
                "fund_name": limit_info["fund_name"],
                "min_buy": float(limit_info["min_buy"]) if limit_info["min_buy"] else 0,
                "max_buy": float(limit_info["max_buy"]) if limit_info["max_buy"] else 0,
                "daily_limit": float(limit_info["daily_limit"]) if limit_info["daily_limit"] else 0,
                "is_suspended": limit_info["is_suspended"],
                "suspend_reason": limit_info["suspend_reason"],
                "last_checked_at": str(limit_info["last_checked_at"]) if limit_info["last_checked_at"] else None,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询限额失败: {e}")


@router.post("/api/limits/plan", summary="智能分配购买计划", tags=["基金限额"])
async def plan_buy(
    target_amount: float = Body(..., description="目标总金额"),
    exclude_codes: list = Body(default=[], description="排除的基金代码"),
    fund_codes: list = Body(default=None, description="限定的基金代码范围")
):
    """根据已知限额智能分配购买计划"""
    try:
        result, remaining = fund_db.get_buyable_funds(
            target_amount=target_amount,
            exclude_codes=exclude_codes
        )

        # 如果指定了基金代码范围，只保留范围内的
        if fund_codes:
            result = [r for r in result if r["fund_code"] in fund_codes]
            # 重新计算剩余
            allocated = sum(r["suggested_amount"] for r in result)
            remaining = target_amount - allocated

        return {
            "status": "ok",
            "target_amount": target_amount,
            "allocated_amount": target_amount - remaining,
            "remaining_amount": remaining,
            "plan": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成购买计划失败: {e}")


# ==================== 数据采集和同步模块 ====================

@router.post("/api/sync/positions", summary="同步持仓", tags=["数据同步"])
async def sync_positions():
    """从同花顺同步持仓到本地数据库"""
    try:
        positions_data = await client.get_fund_positions()

        # 同步到数据库
        synced_count = 0

        # 解析实际的数据结构
        result = positions_data.get("result", {})
        single_data = result.get("singleData", {})
        fund_general = single_data.get("fundGeneral", {})
        fund_list = fund_general.get("fundPositonCombinedList", [])

        for pos in fund_list:
            fund_db.update_position(
                fund_code=pos["fundCode"],
                fund_name=pos["fundName"],
                shares=float(pos.get("holdVol") or 0),
                total_cost=float(pos.get("totalAmount") or 0),
                market_value=float(pos.get("shareValue") or 0),
                profit_pct=float(pos.get("holdIncomeRate") or 0)
            )
            synced_count += 1

        return {
            "status": "success",
            "message": f"同步了 {synced_count} 条持仓记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步持仓失败: {e}")


@router.get("/api/account/overview", summary="账户总览", tags=["账户信息"])
async def get_account_overview():
    """获取账户总览（总资产、收益等）"""
    try:
        overview = await client.get_account_overview()
        return {
            "status": "success",
            "data": overview
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取账户总览失败: {e}")


@router.get("/api/wallet/info", summary="钱包信息", tags=["账户信息"])
async def get_wallet_info():
    """获取钱包余额信息"""
    try:
        wallet = await client.get_wallet_info()
        return {
            "status": "success",
            "data": wallet
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取钱包信息失败: {e}")


@router.get("/api/wallet/home", summary="钱包首页", tags=["账户信息"])
async def get_wallet_home():
    """获取钱包首页完整信息"""
    try:
        home = await client.get_wallet_home()
        return {
            "status": "success",
            "data": home
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取钱包首页失败: {e}")


@router.get("/api/funds/scan", summary="基金扫描（完整版）", tags=["基金数据"])
async def scan_all_funds():
    """扫描基金池中所有基金的详细数据（完整版，数据量大）"""
    try:
        # 这里需要从 config.json 或数据库获取基金池
        # 简化实现：从 ft_positions 获取持有的基金
        positions = fund_db.get_positions()
        fund_codes = [p["fund_code"] for p in positions]

        funds_data = []
        for code in fund_codes:
            try:
                detail = await client.get_fund_detail(code)
                funds_data.append(detail)
            except:
                continue

        return {
            "status": "success",
            "data": {"funds": funds_data},
            "count": len(funds_data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"基金扫描失败: {e}")


@router.get("/api/funds/scan-summary", summary="基金扫描（精简版）", tags=["基金数据"])
async def scan_funds_summary():
    """扫描基金池中所有基金的关键数据（精简版，约2KB）"""
    try:
        positions = fund_db.get_positions()
        fund_codes = [p["fund_code"] for p in positions]

        funds_summary = []
        for code in fund_codes:
            try:
                base = await client.get_fund_base(code)
                info = await client.get_fund_info(code)

                base_data = base.get("data", {})
                info_data = info.get("data", {})

                # 尝试从多个可能的字段获取净值
                nav = info_data.get("net")
                if nav:
                    try:
                        nav = float(nav)
                    except (ValueError, TypeError):
                        nav = None

                # 获取近一周涨跌幅
                rate = info_data.get("week")
                if rate:
                    try:
                        rate = float(rate)
                    except (ValueError, TypeError):
                        rate = None

                # 获取近一月涨跌幅
                yield_month = info_data.get("month")
                if yield_month:
                    try:
                        yield_month = float(yield_month)
                    except (ValueError, TypeError):
                        yield_month = None

                funds_summary.append({
                    "code": code,
                    "name": base_data.get("simpleName") or info_data.get("name"),
                    "type": base_data.get("fundType"),
                    "nav": nav,
                    "rate": rate,  # 近一周涨跌幅
                    "yield_month": yield_month,  # 近一月涨跌幅
                    "risk": base_data.get("riskLevel")
                })
            except:
                continue

        return {
            "status": "success",
            "data": {"funds": funds_summary},
            "count": len(funds_summary)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"基金扫描失败: {e}")


# ==================== 异步任务 ====================

@router.post("/api/tasks", summary="创建异步任务", tags=["任务"])
async def create_task(request: Request):
    """接收截图或命令，创建后台异步任务，立即返回 task_id"""
    import base64
    from pathlib import Path as _Path
    from services import task_db as _task_db
    from services.task_executor import executor as _executor

    data = await request.json()
    task_type = data.get("task_type", data.get("action", "ocr"))
    image_base64 = data.get("imageBase64", "")
    client_id = data.get("client_id", request.headers.get("X-Client-Id", "android"))

    # 保存截图
    image_path = None
    if image_base64:
        images_dir = _Path(__file__).parent.parent / "images"
        images_dir.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        filepath = images_dir / f"screenshot_{ts}.jpg"
        filepath.write_bytes(base64.b64decode(image_base64))
        image_path = str(filepath)

    # 自定义配置（提示词/规则）
    config = None
    system_prompt = data.get("system_prompt")
    rules = data.get("rules")
    if system_prompt or rules:
        config = {}
        if system_prompt:
            config["system_prompt"] = system_prompt
        if rules:
            config["rules"] = rules

    task_id = _task_db.create_task(
        task_type=task_type,
        input_type="screenshot" if image_base64 else "command",
        image_path=image_path,
        client_id=client_id,
        config=config,
    )

    _executor.submit(task_id)

    return {"status": "success", "task_id": task_id, "message": "任务已提交，正在处理中"}


@router.get("/api/tasks", summary="查询任务列表", tags=["任务"])
async def list_tasks(
    status: str = Query(None, description="按状态筛选: pending/processing/completed/failed/all"),
    task_type: str = Query(None, description="按类型筛选: fund_holdings/chat_reply/ocr/..."),
    limit: int = Query(20, description="每页数量", ge=1, le=100),
    offset: int = Query(0, description="偏移量", ge=0),
):
    """查询任务列表，支持状态和类型筛选"""
    from services import task_db as _task_db
    items, total = _task_db.list_tasks(status=status, task_type=task_type, limit=limit, offset=offset)
    return {"status": "success", "data": {"total": total, "items": items}}


@router.get("/api/tasks/{task_id}", summary="查询任务详情", tags=["任务"])
async def get_task(task_id: int):
    """查询单个任务的完整详情（含 result Markdown）"""
    from services import task_db as _task_db
    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return {"status": "success", "data": task}


@router.get("/api/tasks/{task_id}/stream", summary="任务实时事件流(SSE)", tags=["任务"])
async def stream_task(task_id: int):
    """SSE 端点，实时推送任务执行事件（tool_call / text_delta / done）"""
    import asyncio
    import queue as _queue
    from starlette.responses import StreamingResponse
    from services import task_db as _task_db
    from services.event_bus import event_bus as _event_bus

    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    # 任务已完成/失败，直接返回终态
    if task["status"] in ("completed", "failed"):
        async def done_stream():
            event = {
                "type": "done",
                "status": task["status"],
                "result": task.get("result"),
                "error_msg": task.get("error_msg"),
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(done_stream(), media_type="text/event-stream")

    q = _event_bus.subscribe(task_id)

    async def event_stream():
        try:
            while True:
                try:
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=1)
                    )
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") == "done":
                        break
                except _queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            _event_bus.unsubscribe(task_id, q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ==================== 截图处理 ====================

@router.get("/api/ocr/records", summary="查询OCR记录", tags=["截图"])
async def get_ocr_records(
    action: str = Query(None, description="按action筛选，如ocr/fund_holdings"),
    limit: int = Query(20, description="返回条数"),
):
    """查询OCR识别记录"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=limit)
    # datetime 转字符串
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    return {"status": "success", "data": records, "total": len(records)}


@router.get("/api/ocr/records/{record_id}", summary="查询单条OCR记录", tags=["截图"])
async def get_ocr_record_by_id(record_id: int):
    """根据ID查询单条OCR记录"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(limit=1000)
    for r in records:
        if r["id"] == record_id:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
            return {"status": "success", "data": r}
    raise HTTPException(status_code=404, detail=f"OCR记录 {record_id} 不存在")


@router.get("/api/ocr/latest", summary="最新OCR记录", tags=["截图"])
async def get_latest_ocr(
    action: str = Query(None, description="按action筛选"),
    count: int = Query(1, description="返回条数"),
):
    """获取最新的OCR识别记录（支付宝决策用）"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=count)
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    if count == 1 and records:
        return {"status": "success", "data": records[0]}
    return {"status": "success", "data": records}


@router.post("/api/screenshot", summary="截图处理（SSE流式）", tags=["截图"])
async def process_screenshot(request: Request):
    """接收截图并进行 OCR + AI 结构化处理，通过 SSE 推送进度"""
    import json as _json
    from starlette.responses import StreamingResponse
    from handlers.screenshot_handler import ScreenshotHandler

    data = await request.json()
    client_id = request.headers.get("X-Client-Id", "android")
    handler = ScreenshotHandler()

    async def event_stream():
        async for event in handler.process_stream(data, client_id=client_id):
            event_type = event.get("event", "message")
            event_data = _json.dumps(event.get("data", {}), ensure_ascii=False)
            yield f"event: {event_type}\ndata: {event_data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
