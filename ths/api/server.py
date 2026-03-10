"""同花顺基金 API 服务"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ths_fund_client import THSFundClient
import fund_db

client: THSFundClient
auth_refresh_task = None


async def auth_refresh_background_task():
    """后台任务：只在 token 即将过期时才尝试刷新

    策略：
    1. 每 30 分钟检查一次 token 是否即将过期
    2. 只有在即将过期（提前 3 天）时才尝试刷新
    3. 刷新优先级：Zygisk → 密码登录
    """
    global client

    while True:
        try:
            await asyncio.sleep(30 * 60)  # 30 分钟检查一次

            import json
            import time
            from pathlib import Path
            cache_file = Path(__file__).parent / "auth_cache.json"

            # 检查 token 是否即将过期
            need_refresh = False
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        cache_data = json.load(f)
                        expires_at = cache_data.get("expires_at")

                        if expires_at:
                            now = int(time.time())
                            buffer_seconds = 3 * 24 * 3600  # 提前 3 天
                            need_refresh = now + buffer_seconds >= expires_at
                except Exception:
                    pass

            if not need_refresh:
                continue  # token 还没过期，跳过

            print("⚠️ Token 即将过期，尝试刷新...")

            loop = asyncio.get_event_loop()

            # 策略 1: 尝试从 Zygisk 获取
            token_data = await loop.run_in_executor(
                None,
                client._fetch_token_from_zygisk
            )

            # 策略 2: Zygisk 失败，使用密码登录
            if not token_data:
                print("⚠️ Zygisk 不可用，尝试使用密码登录...")
                token_data = await loop.run_in_executor(
                    None,
                    client._login_by_password
                )

            if token_data:
                # 保存新的 token
                sync_source = "zygisk_server" if token_data.get("sessionId") else "password_server"

                def save_token():
                    new_cache = {
                        "auth": {
                            "key1": token_data["key1"],
                            "key2": token_data["key2"],
                            "key3": token_data["key3"],
                            "key4": token_data["key4"],
                            "key5": token_data["key5"],
                            "userId": token_data.get("userId", ""),
                            "sessionId": token_data.get("sessionId", ""),
                            "cookie": token_data.get("cookie", ""),
                            "account": token_data["key3"],
                        },
                        "expires_at": token_data.get("expires_at"),
                        "last_sync": int(time.time()),
                        "sync_source": sync_source
                    }
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(new_cache, f, indent=2, ensure_ascii=False)

                await loop.run_in_executor(None, save_token)
                print(f"✅ Token 刷新成功（来源: {sync_source}）")

                # 重新加载认证参数
                if client.reload_auth_if_updated():
                    print("✅ Client 已加载新的认证参数")
            else:
                print("❌ Token 刷新失败（Zygisk 和密码登录都失败）")

        except Exception as e:
            print(f"⚠️ 认证刷新后台任务异常: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, auth_refresh_task

    # 自动初始化数据库表
    try:
        import fund_db
        fund_db.init_tables()
        print("✅ 数据库表已初始化")
    except Exception as e:
        print(f"⚠️ 数据库表初始化失败: {e}")

    # 使用直接 HTTP 模式（使用缓存的认证参数）
    # 这样不需要保持手机连接，只有在 token 过期时才需要刷新
    client = THSFundClient(use_jsbridge=False)

    # 启动认证参数自动刷新后台任务
    auth_refresh_task = asyncio.create_task(auth_refresh_background_task())
    print("✅ 认证参数自动刷新后台任务已启动（每 30 分钟检查一次）")

    yield

    # 清理：取消后台任务
    if auth_refresh_task:
        auth_refresh_task.cancel()
        try:
            await auth_refresh_task
        except asyncio.CancelledError:
            pass

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


@app.get("/health", summary="健康检查", tags=["系统"])
async def health_check():
    """服务健康检查"""
    return {"status": "ok"}


# ==================== 基金公司（必须在 /api/fund/{fund_code} 之前） ====================

@app.get("/api/fund/companies", summary="基金公司列表", tags=["基金排行"])
async def fund_company_list():
    """获取基金公司列表"""
    return await safe_call(client.get_fund_company_list())


@app.get("/api/fund/search", summary="基金搜索", tags=["基金排行"])
async def fund_search(
    keyword: str = Query(..., description="搜索关键词（如 标普500、纳斯达克）"),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100),
):
    """搜索基金（按名称关键词）"""
    return await safe_call(client.search_fund(keyword, limit))


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


@app.get("/api/news_overview", summary="新闻概览", tags=["热榜"])
async def news_overview(limit: int = Query(10, description="每类新闻的条数限制")):
    """获取新闻概览（重要快讯 + A股快讯 + 滚动快讯）"""
    try:
        # 并发获取三类新闻
        import asyncio
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


# ==================== 交易账户 ====================


@app.get("/api/auth/status", summary="认证状态", tags=["交易账户"])
async def auth_status():
    """查看认证参数缓存状态"""
    try:
        import json
        from pathlib import Path
        from datetime import datetime

        cache_file = Path(__file__).parent / "auth_cache.json"
        if not cache_file.exists():
            return {
                "status": "error",
                "message": "认证缓存不存在"
            }

        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        expires_at = data.get("expires_at")
        if expires_at:
            import time
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


@app.post("/api/auth/refresh", summary="刷新认证", tags=["交易账户"])
async def auth_refresh():
    """强制从 Zygisk 刷新认证参数"""
    try:
        global client

        # 从 Zygisk 获取 token
        loop = asyncio.get_event_loop()
        token_data = await loop.run_in_executor(
            None,
            client._fetch_token_from_zygisk
        )

        if token_data:
            # 保存到文件
            import json
            import time
            from pathlib import Path

            cache_file = Path(__file__).parent / "auth_cache.json"
            cache_data = {
                "auth": {
                    "key1": token_data["key1"],
                    "key2": token_data["key2"],
                    "key3": token_data["key3"],
                    "key4": token_data["key4"],
                    "key5": token_data["key5"],
                    "userId": token_data["userId"],
                    "sessionId": token_data["sessionId"],
                    "cookie": token_data["cookie"],
                    "account": token_data["key3"],
                },
                "expires_at": token_data["expires_at"],
                "last_sync": int(time.time()),
                "sync_source": "manual_refresh"
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

            # 重新加载
            client.reload_auth_if_updated()

            return {
                "status": "success",
                "message": "认证参数刷新成功",
                "account": token_data["key3"]
            }
        else:
            raise HTTPException(status_code=502, detail="刷新失败：Zygisk 未捕获到 token，请确保同花顺 App 已打开并在交易页面")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新认证失败: {e}")


@app.post("/api/auth/auto-refresh", summary="自动刷新认证", tags=["交易账户"])
async def auth_auto_refresh():
    """自动刷新认证参数（从 Zygisk 获取）"""
    # 直接调用 /api/auth/refresh（功能相同）
    return await auth_refresh()


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
    reason: str = Field(None, description="买入理由")


class TradePasswordUpdate(BaseModel):
    password: str = Field(..., description="交易密码（明文）")


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
    """设置交易密码（明文）"""
    client.update_trade_password(req.password)
    return {"status": "ok", "message": "交易密码已设置"}


@app.post("/api/trade/buy", summary="买入基金", tags=["基金交易"])
async def trade_buy(req: BuyFundRequest):
    """买入基金（完整流程：初始化→检查→下单）"""
    import fund_db

    # Step 0: 买入初始化，检查 maxBuy
    try:
        init_resp = await client._proxy_request(
            "/rz/trade/dubbo/subscribe/init",
            body=f"fundCode={req.fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        # _proxy_request 已经解析好了 result 层，直接取 data
        init_data = init_resp.get("data", {}) if client.use_jsbridge else init_resp.get("data", init_resp)

        # 调试输出
        import json
        print(f"🔍 init_resp keys: {list(init_resp.keys())}")
        print(f"🔍 init_data keys: {list(init_data.keys())[:10]}")
        print(f"🔍 maxBuy raw: {init_data.get('maxBuy')}")
        print(f"🔍 minBuy raw: {init_data.get('minBuy')}")

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
            print(f"⚠️ 保存限额信息失败: {e}")

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
        if client.use_jsbridge:
            error_code = raw_resp.get("errorCode", -1)
            result_obj = raw_resp.get("result", {})
            code = result_obj.get("code", "")
            message = result_obj.get("message", "")
        else:
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
    raw_result = await safe_call(client.get_order_list(start_date, end_date, op_type, limit, offset))

    # 包装成统一格式
    return {
        "status": "success",
        "data": raw_result
    }


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
    share_vol_str = f"{req.share_vol:.2f}" if req.share_vol else None
    return await safe_call(client.sell_fund(req.fund_code, share_vol_str, req.sell_all, req.password))


@app.post("/api/trade/cancel", summary="撤销订单", tags=["基金交易"])
async def trade_cancel(req: CancelOrderRequest):
    """撤销交易订单"""
    return await safe_call(client.cancel_order(req.order_no, req.password))


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


# ==================== 风控模块 ====================

@app.get("/api/risk/snapshot", summary="风控快照", tags=["风控模块"])
async def risk_snapshot():
    """输出当前持仓/仓位/可用资金/各基金风控状态"""
    import risk_manager
    import json
    from io import StringIO
    import sys

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


@app.post("/api/risk/check", summary="校验决策", tags=["风控模块"])
async def risk_check(decisions: dict = Body(...)):
    """校验 LLM 决策是否违反硬约束"""
    import risk_manager
    import json
    from io import StringIO
    import sys

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


@app.get("/api/risk/preflight", summary="交易前置检查", tags=["风控模块"])
async def risk_preflight():
    """交易前置检查：交易时间、交易日、熔断机制"""
    import risk_manager
    import json
    from io import StringIO
    import sys

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

@app.post("/api/indicators/evaluate", summary="计算量化信号", tags=["量化信号"])
async def evaluate_indicators():
    """对基金池中每只基金计算量化信号"""
    import indicators
    import json
    from io import StringIO
    import sys

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

@app.post("/api/review/execute", summary="执行决策复盘", tags=["决策复盘"])
async def execute_review(limit: int = Query(30, description="复盘数量"), days_back: int = Query(7, description="回溯天数")):
    """执行待复盘决策的复盘，返回复盘统计结果"""
    import review_decision_executor

    try:
        result = review_decision_executor.execute_decision_review(limit, days_back)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"决策复盘失败: {e}")


@app.post("/api/review/create", summary="创建待复盘记录", tags=["决策复盘"])
async def create_reviews(decision_date: str = Query(None, description="决策日期 YYYY-MM-DD")):
    """从 ft_decisions 创建待复盘记录到 ft_reviews"""
    import fund_db

    try:
        count = fund_db.create_reviews_from_decisions(decision_date)
        return {
            "status": "success",
            "message": f"创建了 {count} 条待复盘记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建待复盘记录失败: {e}")


@app.get("/api/review/pending", summary="获取待复盘决策", tags=["决策复盘"])
async def get_pending_reviews(days_back: int = Query(3, description="回溯天数")):
    """获取待复盘的决策列表"""
    import fund_db

    try:
        reviews = fund_db.get_pending_reviews(days_back)
        return {
            "status": "success",
            "data": reviews,
            "count": len(reviews)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取待复盘决策失败: {e}")


@app.get("/api/review/stats", summary="获取复盘统计", tags=["决策复盘"])
async def get_review_statistics(days: int = Query(30, description="统计天数")):
    """获取复盘统计数据"""
    import fund_db

    try:
        stats = fund_db.get_review_stats(days)
        return {
            "status": "success",
            "data": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取复盘统计失败: {e}")


@app.get("/api/lessons", summary="获取经验知识库", tags=["决策复盘"])
async def get_lessons(
    category: str = Query(None, description="经验分类"),
    min_confidence: str = Query(None, description="最低可信度"),
    include_deprecated: bool = Query(False, description="包含已废弃的经验"),
    limit: int = Query(20, description="返回数量限制")
):
    """获取经验知识库"""
    import fund_db

    try:
        lessons = fund_db.get_lessons(category, min_confidence, include_deprecated, limit)
        return {
            "status": "success",
            "data": lessons,
            "count": len(lessons)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取经验知识库失败: {e}")


@app.post("/api/lessons/save", summary="保存经验教训", tags=["决策复盘"])
async def save_lesson(lesson: dict):
    """保存一条经验教训到 ft_lessons 表"""
    import fund_db

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


@app.post("/api/lessons/update-confidence/{lesson_id}", summary="更新经验可信度", tags=["决策复盘"])
async def update_lesson_confidence(lesson_id: int, success: bool):
    """更新经验教训的可信度"""
    import fund_db

    try:
        fund_db.update_lesson_confidence(lesson_id, success)
        return {
            "status": "success",
            "message": f"经验 {lesson_id} 可信度已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新经验可信度失败: {e}")


@app.post("/api/review/update/{review_id}", summary="更新复盘结果", tags=["决策复盘"])
async def update_review(review_id: int, review_data: dict):
    """更新复盘结果到 ft_reviews 表"""
    import fund_db

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


@app.post("/api/review/mark-extracted/{review_id}", summary="标记经验已提取", tags=["决策复盘"])
async def mark_lesson_extracted(review_id: int):
    """标记复盘记录的经验已提取"""
    import fund_db

    try:
        fund_db.mark_lesson_extracted(review_id)
        return {
            "status": "success",
            "message": f"复盘 {review_id} 已标记为经验已提取"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"标记经验已提取失败: {e}")


# ==================== 决策管理模块 ====================

@app.post("/api/decisions/save", summary="保存决策", tags=["决策管理"])
async def save_decision(decision: dict):
    """保存决策到 ft_decisions 表"""
    import fund_db

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


@app.post("/api/decisions/save-pending", summary="保存待确认决策", tags=["决策管理"])
async def save_pending_decision(decision: dict):
    """保存待确认的决策到 ft_pending_decisions 表"""
    import fund_db

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


@app.post("/api/decisions/execute-pending/{pending_id}", summary="执行待确认决策", tags=["决策管理"])
async def execute_pending_decision(pending_id: int):
    """标记待确认决策为已执行"""
    import fund_db

    try:
        fund_db.execute_pending_decision(pending_id)
        return {
            "status": "success",
            "message": f"待确认决策 {pending_id} 已标记为执行"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行待确认决策失败: {e}")


@app.get("/api/decisions/today", summary="获取今日决策", tags=["决策管理"])
async def get_today_decisions():
    """获取今日所有决策记录"""
    import fund_db

    try:
        decisions = fund_db.get_today_decisions()
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取今日决策失败: {e}")


@app.get("/api/decisions/recent", summary="获取最近决策", tags=["决策管理"])
async def get_recent_decisions(
    days: int = Query(5, description="回溯天数"),
    exclude_today: bool = Query(False, description="排除今日决策")
):
    """获取最近几天的决策记录"""
    import fund_db

    try:
        decisions = fund_db.get_recent_decisions(days, exclude_today)
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最近决策失败: {e}")


@app.get("/api/decisions/watch-streaks", summary="获取连续观望天数", tags=["决策管理"])
async def get_watch_streaks():
    """获取各基金的连续观望天数"""
    import fund_db

    try:
        streaks = fund_db.get_watch_streaks()
        return {
            "status": "success",
            "data": streaks
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取观望天数失败: {e}")


# ==================== 持仓查询模块 ====================

@app.get("/api/position", summary="查询所有持仓", tags=["持仓管理"])
async def get_all_positions():
    """从 ft_positions 表查询所有持仓"""
    import fund_db

    try:
        positions = fund_db.get_positions()
        return {
            "status": "success",
            "data": positions,
            "count": len(positions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


@app.get("/api/position/{fund_code}", summary="查询指定基金持仓", tags=["持仓管理"])
async def get_single_position(fund_code: str):
    """从 ft_positions 表查询指定基金持仓"""
    import fund_db

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

@app.get("/api/trades", summary="查询本地交易记录", tags=["持仓管理"])
async def get_local_trades(
    days: int = Query(30, description="查询天数"),
    limit: int = Query(100, description="返回条数")
):
    """从 ft_trades 表查询本地交易记录（不需要同花顺登录）"""
    import fund_db

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

@app.get("/api/limits/summary", summary="限额统计摘要", tags=["基金限额"])
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


@app.get("/api/limits", summary="批量查询限额", tags=["基金限额"])
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


@app.get("/api/limits/{fund_code}", summary="查询单个基金限额", tags=["基金限额"])
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


@app.post("/api/limits/plan", summary="智能分配购买计划", tags=["基金限额"])
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

@app.post("/api/sync/positions", summary="同步持仓", tags=["数据同步"])
async def sync_positions():
    """从同花顺同步持仓到本地数据库"""
    try:
        positions_data = await client.get_fund_positions()

        # 同步到数据库
        import fund_db
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


@app.get("/api/account/overview", summary="账户总览", tags=["账户信息"])
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


@app.get("/api/wallet/info", summary="钱包信息", tags=["账户信息"])
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


@app.get("/api/wallet/home", summary="钱包首页", tags=["账户信息"])
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


@app.get("/api/funds/scan", summary="基金扫描（完整版）", tags=["基金数据"])
async def scan_all_funds():
    """扫描基金池中所有基金的详细数据（完整版，数据量大）"""
    import fund_db

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


@app.get("/api/funds/scan-summary", summary="基金扫描（精简版）", tags=["基金数据"])
async def scan_funds_summary():
    """扫描基金池中所有基金的关键数据（精简版，约2KB）"""
    import fund_db

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8900, reload=True)
