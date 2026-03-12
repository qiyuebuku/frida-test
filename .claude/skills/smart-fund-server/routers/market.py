"""市场行情路由：决策辅助/热榜/新闻/个股查询"""

import asyncio

from fastapi import APIRouter, HTTPException, Query

import routers._utils as _utils
from routers._utils import safe_call
from routers._models import HOTLIST_MARKETS, HOTLIST_PLATE_TYPES

router = APIRouter()


# ==================== 技术面 & 大盘 & 资金流 ====================

@router.get("/api/fund/{fund_code}/nav_technical", summary="净值技术面分析", tags=["决策辅助"])
async def nav_technical(fund_code: str):
    """基金净值技术面分析（RSI14/MA5/MA20/MA60/偏离度/信号）"""
    return await safe_call(_utils.client.get_nav_technical(fund_code))


@router.get("/api/market/overview", summary="A股大盘总览", tags=["决策辅助"])
async def market_overview():
    """A股大盘总览：指数行情 + 涨跌家数 + 成交额 + 资金流向 + 涨跌停 + 大小盘对比"""
    return await safe_call(_utils.client.get_market_overview())


@router.get("/api/market/yesterday_limit", summary="昨日涨停今日表现", tags=["决策辅助"])
async def yesterday_limit():
    """昨日涨停股票今日涨跌表现（涨跌幅/振幅/换手率/连板数/封板时间）"""
    return await safe_call(_utils.client.get_yesterday_limit_performance())


@router.get("/api/market/stock_changes", summary="盘中异动", tags=["决策辅助"])
async def stock_changes(
    change_type: str = Query("all", description="异动类型: all/竞价/拉升/跳水/大单/涨停/跌停/缺口/新高新低/大幅，或具体类型如'火箭发射'"),
    count: int = Query(50, description="返回条数"),
):
    """盘中异动（火箭发射/竞价上涨/大笔买入/封涨停 等22种类型）"""
    return await safe_call(_utils.client.get_stock_changes(change_type, min(count, 200)))


@router.get("/api/market/stock_ranking", summary="个股涨跌幅排行", tags=["决策辅助"])
async def stock_ranking(
    sort: str = Query("rise", description="排序: rise=涨幅榜, fall=跌幅榜, volume=成交量, turnover=成交额, turnover_rate=换手率"),
    count: int = Query(20, description="返回条数"),
):
    """个股涨跌幅排行（涨幅榜/跌幅榜/成交榜/换手率榜）"""
    valid = {"rise", "fall", "volume", "turnover", "amplitude", "turnover_rate"}
    if sort not in valid:
        raise HTTPException(400, f"sort 必须是 {'/'.join(sorted(valid))}")
    return await safe_call(_utils.client.get_stock_ranking(sort, min(count, 50)))


@router.get("/api/market/sector_ranking", summary="板块涨跌排行", tags=["决策辅助"])
async def sector_ranking(
    sector_type: str = Query("concept", description="类型: concept=概念板块, industry=行业板块"),
    count: int = Query(20, description="返回条数"),
):
    """板块涨跌排行（概念板块/行业板块 涨跌幅榜）"""
    if sector_type not in {"concept", "industry"}:
        raise HTTPException(400, "sector_type 必须是 concept/industry")
    return await safe_call(_utils.client.get_sector_ranking(sector_type, min(count, 50)))


@router.get("/api/market/dragon_tiger", summary="龙虎榜", tags=["决策辅助"])
async def dragon_tiger(
    tab: str = Query("stock", description="维度: stock=个股明细, dept=活跃营业部(游资), org=机构买卖"),
    days: int = Query(3, description="回溯天数"),
    count: int = Query(30, description="返回条数"),
):
    """龙虎榜数据（个股明细/活跃营业部/机构买卖）"""
    if tab not in {"stock", "dept", "org"}:
        raise HTTPException(400, "tab 必须是 stock/dept/org")
    return await safe_call(_utils.client.get_dragon_tiger(tab, days, min(count, 50)))


@router.get("/api/market/ths_dragon_tiger", summary="同花顺游资龙虎榜", tags=["决策辅助"])
async def ths_dragon_tiger(
    tab: str = Query("youzi", description="维度: youzi=游资(一线+知名), jigou=机构, gfgs=跟风高手, all=全部"),
    count: int = Query(30, description="返回条数"),
):
    """同花顺龙虎榜 - 带游资/机构分类标签"""
    if tab not in {"youzi", "jigou", "gfgs", "gansidui", "all"}:
        raise HTTPException(400, "tab 必须是 youzi/jigou/gfgs/all")
    return await safe_call(_utils.client.get_ths_dragon_tiger(tab, min(count, 50)))


@router.get("/api/market/capital_flow", summary="资金流向", tags=["决策辅助"])
async def capital_flow(
    tab: str = Query("market", description="维度: market=大盘资金, north=北向资金"),
    days: int = Query(20, description="回溯天数"),
):
    """资金流向（大盘主力资金净流入 / 北向资金成交额）"""
    if tab not in {"market", "north"}:
        raise HTTPException(400, "tab 必须是 market/north")
    return await safe_call(_utils.client.get_capital_flow(tab, min(days, 60)))


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
    return await safe_call(_utils.client.get_hot_board(board_type, sort, min(count, 30)))


@router.get("/api/market/currency", summary="货币风向", tags=["决策辅助"])
async def currency_data(
    tab: str = Query("usdcny", description="维度: usdcny=美元/离岸人民币, shibor=Shibor利率+LPR"),
    days: int = Query(120, description="回溯天数"),
):
    """货币风向（美元/离岸人民币汇率走势 / Shibor利率+LPR变化）"""
    if tab not in {"usdcny", "shibor"}:
        raise HTTPException(400, "tab 必须是 usdcny/shibor")
    return await safe_call(_utils.client.get_currency_data(tab, min(days, 500)))


@router.get("/api/market/environment", summary="大盘与行业环境", tags=["决策辅助"])
async def market_environment():
    """获取沪深300趋势 + 北向资金活跃度"""
    return await safe_call(_utils.client.get_market_environment())


@router.get("/api/fund/{fund_code}/fund_flow", summary="基金申赎资金流", tags=["决策辅助"])
async def fund_flow(fund_code: str):
    """基金申赎资金流趋势（季度净申赎 + 机构占比变化）"""
    return await safe_call(_utils.client.get_fund_flow_trend(fund_code))


# ==================== 热榜数据 ====================

@router.get("/api/hotlist/stocks", summary="个股热榜", tags=["热榜"])
async def hot_stocks(
    market: str = Query("a", description="市场: a=A股, hk=港股, us=美股"),
):
    """获取个股热榜（热度排名前100）"""
    if market not in HOTLIST_MARKETS:
        raise HTTPException(400, f"market 必须是 {'/'.join(sorted(HOTLIST_MARKETS))}")
    return await safe_call(_utils.client.get_hot_stocks(market))


@router.get("/api/hotlist/plate", summary="概念/行业热榜", tags=["热榜"])
async def hot_plate(
    plate_type: str = Query("concept", description="类型: concept=概念, industry=行业"),
):
    """获取概念或行业热榜"""
    if plate_type not in HOTLIST_PLATE_TYPES:
        raise HTTPException(400, f"plate_type 必须是 {'/'.join(sorted(HOTLIST_PLATE_TYPES))}")
    return await safe_call(_utils.client.get_hot_plate(plate_type))


@router.get("/api/hotlist/etf", summary="ETF 热榜", tags=["热榜"])
async def hot_etf():
    """获取 ETF 热度排行"""
    return await safe_call(_utils.client.get_hot_etf())


@router.get("/api/hotlist/futures", summary="期货热榜", tags=["热榜"])
async def hot_futures():
    """获取期货热度排行"""
    return await safe_call(_utils.client.get_hot_futures())


@router.get("/api/hotlist/bond", summary="可转债热榜", tags=["热榜"])
async def hot_bond():
    """获取可转债热度排行"""
    return await safe_call(_utils.client.get_hot_bond())


@router.get("/api/hotlist/topics", summary="热榜话题", tags=["热榜"])
async def hot_topics():
    """获取同花顺热榜话题（15条）"""
    return await safe_call(_utils.client.get_hot_topics())


@router.get("/api/hotlist/posts", summary="热门文章", tags=["热榜"])
async def hot_posts(
    page: int = Query(1, description="页码"),
    page_size: int = Query(10, description="每页条数"),
):
    """获取热门文章"""
    return await safe_call(_utils.client.get_hot_posts(page, page_size))


@router.get("/api/hotlist/topic/{code}", summary="话题详情", tags=["热榜"])
async def topic_detail(
    code: str,
    page: int = Query(1, description="帖子页码"),
    page_size: int = Query(10, description="每页帖子数"),
):
    """获取话题详情及推荐帖子"""
    return await safe_call(_utils.client.get_topic_detail(code, page, page_size))


@router.get("/api/hotlist/special/{code}", summary="专题详情", tags=["热榜"])
async def special_detail(code: str):
    """获取专题详情（从 HTML 解析）"""
    return await safe_call(_utils.client.get_special_detail(code))


@router.get("/api/headlines", summary="推荐头条", tags=["热榜"])
async def headlines():
    """获取推荐头条（首页推荐tab头条模块）"""
    return await safe_call(_utils.client.get_headlines())


@router.get("/api/article/{encoded_seq}", summary="新闻文章详情", tags=["热榜"])
async def article_detail(encoded_seq: str):
    """获取新闻文章详情（type=1 类型的新闻）"""
    return await safe_call(_utils.client.get_article_detail(encoded_seq))


@router.get("/api/news_themes", summary="新闻主题分类", tags=["热榜"])
async def news_themes():
    """获取新闻主题分类列表（资讯->头条 tab 栏的主题标签，如小金属、有色金属、算力租赁等）"""
    return await safe_call(_utils.client.get_news_themes())


@router.get("/api/theme/{theme_id}/articles", summary="主题文章列表", tags=["热榜"])
async def theme_articles(
    theme_id: str,
    page: int = Query(1, description="页码"),
    size: int = Query(15, description="每页条数"),
):
    """获取主题下的文章列表（含标题、来源、阅读量、相关股票）"""
    return await safe_call(_utils.client.get_theme_articles(theme_id, page, size))


@router.get("/api/flash_news/tabs", summary="快讯分类标签", tags=["热榜"])
async def flash_news_tabs():
    """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
    return await safe_call(_utils.client.get_flash_news_tabs())


@router.get("/api/flash_news/list", summary="分类快讯列表", tags=["热榜"])
async def flash_news_list(
    tag_id: int = Query(21101, description="分类ID: 21101=A股, 62857=重要, 34843=公告, 33775=期货, 21111=异动, 21105=港股, 21107=美股"),
    seq: int = Query(0, description="翻页游标，0=最新，传上一页最后一条的seq加载更早"),
):
    """获取指定分类的快讯列表（每次约20条）"""
    return await safe_call(_utils.client.get_flash_news_list(tag_id, seq))


@router.get("/api/news_feed", summary="滚动快讯", tags=["热榜"])
async def news_feed(page: int = Query(1, description="页码")):
    """获取滚动快讯（财经要闻，每页20条）"""
    return await safe_call(_utils.client.get_news_feed(page))


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


# ==================== 个股查询 ====================

@router.get("/api/stock/quote", summary="个股实时行情", tags=["个股查询"])
async def stock_quote(
    codes: str = Query(..., description="股票代码，逗号分隔，如 600519,000001"),
):
    """获取个股实时行情（腾讯证券数据源，支持批量最多20只）"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(400, "codes 不能为空")
    return await safe_call(_utils.client.get_stock_quote(code_list))


@router.get("/api/stock/{code}/kline", summary="个股K线", tags=["个股查询"])
async def stock_kline(
    code: str,
    period: str = Query("101", description="周期: 101=日K, 102=周K, 103=月K"),
    limit: int = Query(60, description="返回条数"),
):
    """获取个股K线数据（前复权）"""
    if period not in {"101", "102", "103"}:
        raise HTTPException(400, "period 必须是 101/102/103")
    return await safe_call(_utils.client.get_stock_kline(code, period, min(limit, 500)))


@router.get("/api/stock/{code}/capital_flow", summary="个股资金流", tags=["个股查询"])
async def stock_capital_flow(
    code: str,
    days: int = Query(20, description="回溯天数"),
):
    """获取个股资金流向（主力/超大单/大单/中单/小单）"""
    return await safe_call(_utils.client.get_stock_capital_flow(code, min(days, 120)))


@router.get("/api/stock/{code}/valuation", summary="个股估值历史", tags=["个股查询"])
async def stock_valuation(
    code: str,
    years: int = Query(3, description="回溯年数"),
):
    """获取个股 PE_TTM/PB 历史数据"""
    data = await safe_call(_utils.client.get_stock_valuation_history(code, min(years, 10)))
    return {"status_code": 0, "data": {"code": code, "total": len(data), "items": data}}


@router.get("/api/stock/{code}/financial", summary="个股财务数据", tags=["个股查询"])
async def stock_financial(
    code: str,
    limit: int = Query(10, description="返回报告期数"),
):
    """获取个股财务数据（EPS/营收/净利/ROE/毛利率等）"""
    return await safe_call(_utils.client.get_stock_financial(code, min(limit, 50)))
