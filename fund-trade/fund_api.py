#!/usr/bin/env python3
"""基金数据采集层 - 封装 server.py:8900 调用，PG 透明缓存"""

import json
import os
import sys

import requests

# 清除代理环境变量，避免 requests 走代理导致请求 localhost:8900 失败
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_key, None)

from fund_db import (
    get_cache,
    get_market_cache,
    set_cache,
    set_market_cache,
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# 端点 -> data_type 映射
FUND_ENDPOINTS = {
    "detail": "/api/fund/{code}",
    "nav": "/api/fund/{code}/nav",
    "holdings": "/api/fund/{code}/holdings",
    "rank": "/api/fund/{code}/rank",
    "rsi": "/api/fund/{code}/rsi",
    "drawdown": "/api/fund/{code}/drawdown",
    "pe_percentile": "/api/fund/{code}/holdings/pe_percentile",
    "scale_change": "/api/fund/{code}/scale_change",
    "holder_ratio": "/api/fund/{code}/holder_ratio",
    "manager": "/api/fund/{code}/manager",
    "trade_rule": "/api/fund/{code}/trade_rule",
    "announcements": "/api/fund/{code}/announcements",
    "buy_limits": "/api/trade/buy_limits/{code}",  # 购买限制（min_buy/max_buy/can_buy）
}

FUND_SCAN_PARAMS = {
    "nav": {"period": "nowyear"},
}

MARKET_ENDPOINTS = {
    "market_overview": ("/api/market/overview", None),         # 大盘总览（指数/涨跌家数/成交额）
    "capital_flow": ("/api/market/capital_flow", None),        # 大盘主力资金流向
    "sector_ranking": ("/api/market/sector_ranking", None),    # 板块涨跌排行
    "hot_board": ("/api/hotlist/plate", {"plate_type": "concept"}),  # 概念板块热度
}

NEWS_MARKET_ENDPOINTS = {
    # === 重要性最高：热门文章 + 头条 + 重要快讯 ===
    "hotlist_posts": ("/api/hotlist/posts", None),         # 热门文章（含重磅新闻标题、点赞数、评论数、关联标的）
    "headlines": ("/api/headlines", None),                  # 推荐头条（首页置顶重要新闻/专题）
    "flash_news": ("/api/flash_news/list", {"tag_id": 62857}),# 重要快讯
    # === 市场动态 ===
    "hotlist_topics": ("/api/hotlist/topics", {"market": "a"}),  # 热榜话题（社区热议）
    "news_feed": ("/api/news_feed", None),                 # 滚动快讯（财经要闻实时流）
    # === 异动信号 ===
    "market_changes": ("/api/market/changes", None),       # 大盘异动（板块异动时间线）
}

SCAN_TTL = 14400  # 4 小时
NEWS_TTL = 1800   # 30 分钟
MARKET_TTL = 1800 # 30 分钟


def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_server_url(config=None):
    if config is None:
        config = _load_config()
    return config.get("server_url", "http://127.0.0.1:8900")


def _get_fund_pool(config=None):
    if config is None:
        config = _load_config()
    pool = config.get("fund_pool", [])
    # 支持两种格式: ["006888", ...] 或 [{"code": "006888", ...}, ...]
    return [item["code"] if isinstance(item, dict) else item for item in pool]


def _fetch(endpoint, params=None, config=None):
    """通用 HTTP GET 请求 + 错误处理"""
    base_url = _get_server_url(config)
    url = base_url.rstrip("/") + endpoint
    try:
        resp = requests.get(url, params=params, timeout=30, proxies={"http": None, "https": None})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"请求超时: {endpoint}"}
    except requests.exceptions.ConnectionError:
        return {"error": f"连接失败: {url}，请确认 server.py 已启动"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP 错误: {e}"}
    except json.JSONDecodeError:
        return {"error": f"响应非 JSON: {endpoint}"}


def _cached_fetch(fund_code, data_type, endpoint, params=None, ttl=SCAN_TTL, config=None):
    """缓存透明访问 - 基金级"""
    cached = get_cache(fund_code, data_type)
    if cached is not None:
        return cached
    data = _fetch(endpoint, params=params, config=config)
    if "error" not in data:
        set_cache(fund_code, data_type, data, ttl_seconds=ttl)
    return data


def _cached_market_fetch(data_type, endpoint, params=None, ttl=MARKET_TTL, config=None):
    """缓存透明访问 - 市场级"""
    cached = get_market_cache(data_type)
    if cached is not None:
        return cached
    data = _fetch(endpoint, params=params, config=config)
    if "error" not in data:
        set_market_cache(data_type, data, ttl_seconds=ttl)
    return data


# ==================== scan 子命令 ====================

def cmd_scan():
    """对基金池每只基金采集多维度数据并缓存"""
    config = _load_config()
    fund_pool = _get_fund_pool(config)
    if not fund_pool:
        print(json.dumps({"message": "基金池为空，请先在 config.json 中配置 fund_pool"}, ensure_ascii=False))
        return

    result = {}
    for code in fund_pool:
        fund_data = {}
        for data_type, endpoint_tpl in FUND_ENDPOINTS.items():
            endpoint = endpoint_tpl.replace("{code}", code)
            params = FUND_SCAN_PARAMS.get(data_type)
            fund_data[data_type] = _cached_fetch(
                fund_code=code,
                data_type=data_type,
                endpoint=endpoint,
                params=params,
                ttl=SCAN_TTL,
                config=config,
            )
        result[code] = fund_data

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def cmd_scan_summary():
    """scan 的精简版，只输出决策所需的关键信息（<15KB）"""
    config = _load_config()
    fund_pool = _get_fund_pool(config)
    if not fund_pool:
        print(json.dumps({"message": "基金池为空"}, ensure_ascii=False))
        return

    funds = []
    for code in fund_pool:
        fund = {"code": code}

        # detail: 基金名称、类型、净值、涨跌
        detail = _cached_fetch(code, "detail", f"/api/fund/{code}", ttl=SCAN_TTL, config=config)
        if detail and "data" in detail:
            d = detail["data"]
            fund["name"] = d.get("name", "")
            fund["type"] = d.get("type", "")
            fund["nav"] = d.get("net", "")
            fund["rate"] = d.get("rate", "")  # 今日涨跌
            fund["risk"] = d.get("levelOfRisk", "")

        # rsi: RSI 指标
        rsi = _cached_fetch(code, "rsi", f"/api/fund/{code}/rsi", ttl=SCAN_TTL, config=config)
        if rsi and "data" in rsi:
            fund["rsi"] = rsi["data"].get("rsi6", "")

        # drawdown: 最大回撤（取近一年数据）
        dd = _cached_fetch(code, "drawdown", f"/api/fund/{code}/drawdown", ttl=SCAN_TTL, config=config)
        if dd and "data" in dd and isinstance(dd["data"], list):
            for item in dd["data"]:
                if item.get("time") == "近一年":
                    fund["drawdown_1y"] = round(item.get("drawdown", 0), 2)
                    fund["drawdown_rank"] = item.get("rank", "")
                    break

        # pe_percentile: 估值百分位
        pe = _cached_fetch(code, "pe_percentile", f"/api/fund/{code}/holdings/pe_percentile", ttl=SCAN_TTL, config=config)
        if pe and "data" in pe:
            fund["pe_pct"] = pe["data"].get("weighted_pe_percentile", "")

        # rank: 同类排名（取近一月和近一年）
        rank = _cached_fetch(code, "rank", f"/api/fund/{code}/rank", ttl=SCAN_TTL, config=config)
        if rank and "data" in rank and isinstance(rank["data"], list):
            for item in rank["data"]:
                if item.get("time") == "近一月":
                    fund["rank_month"] = item.get("rank", "")
                    fund["yield_month"] = item.get("yield", "")
                elif item.get("time") == "近一年":
                    fund["rank_year"] = item.get("rank", "")
                    fund["yield_year"] = item.get("yield", "")

        # buy_limits: 购买限制
        bl = _cached_fetch(code, "buy_limits", f"/api/trade/buy_limits/{code}", ttl=SCAN_TTL, config=config)
        if bl and "data" in bl:
            fund["min_buy"] = bl["data"].get("min_buy", 10)
            fund["max_buy"] = bl["data"].get("max_buy")
            fund["can_buy"] = bl["data"].get("can_buy", True)

        # holdings: 只取前 3 大持仓
        holdings = _cached_fetch(code, "holdings", f"/api/fund/{code}/holdings", ttl=SCAN_TTL, config=config)
        if holdings and "data" in holdings:
            hd = holdings["data"]
            fund["stock_pct"] = hd.get("stockPositionTotal", "")
            top3 = hd.get("stockList", [])[:3]
            fund["top3_holdings"] = [{"name": s.get("stockName", ""), "pct": s.get("ratio", "")} for s in top3]

        funds.append(fund)

    print(json.dumps({"funds": funds}, ensure_ascii=False, separators=(",", ":")))


# ==================== news 子命令 ====================

def _classify_news(fund_pool, fund_news, market_news):
    """将新闻按 重磅头条/宏观快讯/行业动态/市场异动/个基相关 分类组织"""
    classified = {
        "重磅头条": [],
        "宏观快讯": [],
        "行业动态": [],
        "市场异动": [],
        "个基相关": {},
    }

    def _add(category, source, data):
        if data and "error" not in data:
            classified[category].append({"source": source, "data": data})

    # 重磅头条：热门文章 + 推荐头条（最重要！包含重大突发事件）
    _add("重磅头条", "hotlist_posts", market_news.get("hotlist_posts"))
    _add("重磅头条", "headlines", market_news.get("headlines"))

    # 宏观快讯
    _add("宏观快讯", "flash_news", market_news.get("flash_news"))
    _add("宏观快讯", "news_feed", market_news.get("news_feed"))

    # 行业动态
    _add("行业动态", "hotlist_topics", market_news.get("hotlist_topics"))

    # 市场异动
    _add("市场异动", "market_changes", market_news.get("market_changes"))

    # 基金新闻归入个基相关
    for code in fund_pool:
        news = fund_news.get(code)
        if news and "error" not in news:
            classified["个基相关"][code] = news

    return classified


def cmd_news():
    """采集新闻数据（7 个市场源 + 基金池个基新闻）"""
    config = _load_config()
    fund_pool = _get_fund_pool(config)
    if not fund_pool:
        print(json.dumps({"message": "基金池为空，请先在 config.json 中配置 fund_pool"}, ensure_ascii=False))
        return

    # 采集基金级新闻
    fund_news = {}
    for code in fund_pool:
        endpoint = f"/api/fund/{code}/news"
        fund_news[code] = _cached_fetch(
            fund_code=code,
            data_type="news",
            endpoint=endpoint,
            ttl=NEWS_TTL,
            config=config,
        )

    # 采集市场级新闻（7 个源）
    market_news = {}
    for data_type, (endpoint, params) in NEWS_MARKET_ENDPOINTS.items():
        market_news[data_type] = _cached_market_fetch(
            data_type=data_type,
            endpoint=endpoint,
            params=params,
            ttl=NEWS_TTL,
            config=config,
        )

    classified = _classify_news(fund_pool, fund_news, market_news)

    print(json.dumps(classified, ensure_ascii=False, separators=(",", ":")))


# ==================== market 子命令 ====================

def cmd_market():
    """采集大盘数据"""
    config = _load_config()
    result = {}
    for data_type, (endpoint, params) in MARKET_ENDPOINTS.items():
        result[data_type] = _cached_market_fetch(
            data_type=data_type,
            endpoint=endpoint,
            params=params,
            ttl=MARKET_TTL,
            config=config,
        )

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


# ==================== sync 子命令 ====================

def cmd_sync():
    """从同花顺真实账户同步持仓到 ft_positions，确保数据一致"""
    from fund_db import update_position, delete_position, get_positions

    config = _load_config()

    # 1. 获取真实持仓
    positions_resp = _fetch("/api/trade/positions", config=config)
    if "error" in positions_resp:
        print(json.dumps({"error": f"获取持仓失败: {positions_resp['error']}"}, ensure_ascii=False))
        return

    # 检查同花顺业务状态码（登录失效、session 过期等）
    # 正常成功码: "0000" 或无 code 字段；异常码如 "LT99" 表示登录失效
    resp_code = positions_resp.get("code", "")
    if resp_code and resp_code not in ("", "0000"):
        msg = positions_resp.get("message", "未知错误")
        print(json.dumps({"error": f"同花顺账户异常(code={resp_code}): {msg}"}, ensure_ascii=False))
        return

    sd = positions_resp.get("singleData", {})
    general = sd.get("fundGeneral", {})
    real_positions = general.get("fundPositonCombinedList", [])

    # 2. 获取待确认订单（已提交但份额未到账的买入）
    pending_orders = []
    try:
        orders_resp = _fetch("/api/trade/orders?days=30&limit=50", config=config)
        if "error" not in orders_resp:
            orders_data = orders_resp.get("singleData", {}).get("data", [])
            for o in orders_data:
                if o.get("endFlag") == "0" and o.get("confirmFlag") == "0":
                    pending_orders.append({
                        "fund_code": o.get("fundCode", ""),
                        "fund_name": o.get("fundName", ""),
                        "amount": float(o.get("totalFee", 0) or 0),
                        "order_date": o.get("orderDate", ""),
                    })
    except Exception:
        pass

    # 3. 同步真实持仓到 ft_positions
    synced_codes = set()
    synced = []
    for pos in real_positions:
        fund_code = pos.get("fundCode", "")
        if not fund_code:
            continue
        fund_name = pos.get("fundName", "")
        # shareValue 或 value 为市值
        market_value = float(pos.get("shareValue") or pos.get("value") or 0)
        total_cost = float(pos.get("buyAmount") or 0)
        shares = float(pos.get("availableVol") or pos.get("availableShare") or pos.get("totalVol") or 0)
        hold_income = float(pos.get("holdIncome") or 0)
        # 计算净值和收益率
        current_nav = market_value / shares if shares > 0 else 0
        avg_cost = total_cost / shares if shares > 0 else 0
        profit_pct = (hold_income / total_cost * 100) if total_cost > 0 else 0

        update_position(
            fund_code=fund_code,
            fund_name=fund_name,
            total_cost=total_cost,
            shares=shares,
            avg_cost=round(avg_cost, 4),
            current_nav=round(current_nav, 4),
            market_value=market_value,
            profit_pct=round(profit_pct, 4),
        )
        synced_codes.add(fund_code)
        synced.append({"fund_code": fund_code, "fund_name": fund_name, "market_value": market_value, "total_cost": total_cost})

    # 4. 待确认订单也记入持仓（有成本但份额为 0）
    pending_synced = []
    for po in pending_orders:
        fund_code = po["fund_code"]
        if fund_code in synced_codes:
            continue  # 已在持仓中，跳过
        # 待确认的买入：有成本，份额和市值暂时为 0
        update_position(
            fund_code=fund_code,
            fund_name=po["fund_name"],
            total_cost=po["amount"],
            shares=0,
            avg_cost=0,
            current_nav=0,
            market_value=0,
            profit_pct=0,
        )
        synced_codes.add(fund_code)
        pending_synced.append(po)

    # 5. 清理本地有但真实已不存在的持仓（已全部赎回）
    local_positions = get_positions()
    removed = []
    for lp in local_positions:
        if lp["fund_code"] not in synced_codes:
            delete_position(lp["fund_code"])
            removed.append(lp["fund_code"])

    result = {
        "synced": len(synced),
        "pending": len(pending_synced),
        "removed": len(removed),
        "positions": synced,
        "pending_orders": pending_synced,
        "removed_codes": removed,
        "account_summary": {
            "total_asset": general.get("sumValue"),
            "total_buy_amount": general.get("sumBuyAmount"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


# ==================== news-overview 子命令 (Stage 1) ====================

NEWS_OVERVIEW_ENDPOINTS = {
    "hotlist_posts": ("/api/hotlist/posts", {"page_size": 15}),
    "flash_important": ("/api/flash_news/list", {"tag_id": 62857}),
    "market_overview": ("/api/market/overview", None),
    "sector_ranking": ("/api/market/sector_ranking", None),
}

OVERVIEW_TTL = 1800  # 30 分钟


def _slim_hotlist_posts(data):
    """精简热门文章，去掉图片和用户详情，只保留决策相关字段"""
    if "error" in data or "data" not in data:
        return data
    feed = data.get("data", {}).get("feed", [])
    slim_feed = []
    for p in feed:
        content = p.get("content", "")
        item = {
            "title": p.get("title", ""),
            "content": content[:100] if content else "",
            "stat": p.get("stat"),
        }
        # 保留关联标的（板块/个股）
        att_forum = p.get("ext", {}).get("att_forum", {}).get("forum_list", [])
        if att_forum:
            item["related"] = [{"code": f.get("code"), "name": f.get("name")} for f in att_forum]
        slim_feed.append(item)
    return {"data": {"feed": slim_feed}}


def _slim_flash_news(data):
    """精简快讯，只保留标题和时间"""
    if "error" in data or "data" not in data:
        return data
    news_list = data.get("data", {}).get("list", [])
    slim_list = []
    for n in news_list:
        slim_list.append({
            "seq": n.get("seq"),
            "title": n.get("title", ""),
            "time": n.get("time", ""),
        })
    return {"data": {"list": slim_list}}


def _slim_sector_ranking(data):
    """精简板块排行，只保留名称、涨跌幅、领涨股"""
    if "error" in data or "data" not in data:
        return data
    raw = data.get("data", {})
    result = {}
    for key in ("topRise", "topFall"):
        items = raw.get(key, [])
        result[key] = [{
            "name": s.get("name", ""),
            "changeRate": s.get("changeRate"),
            "leadStock": s.get("leadStock", {}).get("name", ""),
        } for s in items[:10]]  # 只取前 10
    return {"data": result}


OVERVIEW_SLIMMERS = {
    "hotlist_posts": _slim_hotlist_posts,
    "flash_important": _slim_flash_news,
    "sector_ranking": _slim_sector_ranking,
}


def cmd_news_overview():
    """Stage 1: 概览扫描 - 4 个请求获取今日全貌"""
    config = _load_config()
    result = {}
    for data_type, (endpoint, params) in NEWS_OVERVIEW_ENDPOINTS.items():
        data = _cached_market_fetch(
            data_type=f"overview_{data_type}",
            endpoint=endpoint,
            params=params,
            ttl=OVERVIEW_TTL,
            config=config,
        )
        # 精简大数据源，减少 LLM 噪声
        slimmer = OVERVIEW_SLIMMERS.get(data_type)
        if slimmer:
            data = slimmer(data)
        result[data_type] = data

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


# ==================== news-drill 子命令 (Stage 2) ====================

FLASH_TAG_MAP = {
    "a股": 21101, "重要": 62857, "公告": 34843, "期货": 33775,
    "异动": 21111, "港股": 21105, "美股": 21107,
}

DRILL_TTL_MAP = {
    "themes": 14400,       # 4h
    "theme": 1800,         # 30min
    "article": 14400,      # 4h
    "topic": 1800,         # 30min
    "headlines": 1800,     # 30min
    "flash": 900,          # 15min
    "fund-news": 1800,     # 30min
    "changes": 900,        # 15min
    "hot-board": 1800,     # 30min
    "dragon-tiger": 1800,  # 30min
}


def cmd_news_drill():
    """Stage 2: 定向深入 - Claude 根据概览动态决定深入方向"""
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "用法: python fund_api.py news-drill <mode> [args]",
            "modes": {
                "themes": "新闻主题列表",
                "theme <id>": "主题下的文章列表",
                "article <seq>": "文章全文",
                "topic <code>": "话题详情",
                "headlines": "推荐头条",
                "flash <tag>": f"快讯 (tag: {', '.join(FLASH_TAG_MAP.keys())})",
                "fund-news <code>": "基金相关新闻",
                "changes": "大盘异动",
                "hot-board [sort]": "热门板块 (sort: rise/fall/turnover)",
                "dragon-tiger": "龙虎榜",
            },
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    config = _load_config()
    mode = sys.argv[2]
    ttl = DRILL_TTL_MAP.get(mode, 1800)

    if mode == "themes":
        data = _cached_market_fetch("drill_themes", "/api/news_themes", ttl=ttl, config=config)
    elif mode == "theme":
        theme_id = sys.argv[3] if len(sys.argv) > 3 else None
        if not theme_id:
            print(json.dumps({"error": "需要 theme_id，用法: news-drill theme <id>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_market_fetch(f"drill_theme_{theme_id}", f"/api/theme/{theme_id}/articles", ttl=ttl, config=config)
    elif mode == "article":
        seq = sys.argv[3] if len(sys.argv) > 3 else None
        if not seq:
            print(json.dumps({"error": "需要 article seq，用法: news-drill article <seq>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_market_fetch(f"drill_article_{seq}", f"/api/article/{seq}", ttl=ttl, config=config)
    elif mode == "topic":
        code = sys.argv[3] if len(sys.argv) > 3 else None
        if not code:
            print(json.dumps({"error": "需要 topic code，用法: news-drill topic <code>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_market_fetch(f"drill_topic_{code}", f"/api/hotlist/topic/{code}", ttl=ttl, config=config)
    elif mode == "headlines":
        data = _cached_market_fetch("drill_headlines", "/api/headlines", ttl=ttl, config=config)
    elif mode == "flash":
        tag = sys.argv[3] if len(sys.argv) > 3 else None
        if not tag or tag not in FLASH_TAG_MAP:
            print(json.dumps({"error": f"需要 tag，可选: {', '.join(FLASH_TAG_MAP.keys())}"}, ensure_ascii=False))
            sys.exit(1)
        tag_id = FLASH_TAG_MAP[tag]
        data = _cached_market_fetch(f"drill_flash_{tag}", "/api/flash_news/list", params={"tag_id": tag_id}, ttl=ttl, config=config)
    elif mode == "fund-news":
        code = sys.argv[3] if len(sys.argv) > 3 else None
        if not code:
            print(json.dumps({"error": "需要基金代码，用法: news-drill fund-news <code>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_fetch(code, "drill_news", f"/api/fund/{code}/news", ttl=ttl, config=config)
    elif mode == "changes":
        data = _cached_market_fetch("drill_changes", "/api/market/stock_changes", ttl=ttl, config=config)
    elif mode == "hot-board":
        sort_type = sys.argv[3] if len(sys.argv) > 3 else None
        params = {"sort": sort_type} if sort_type else None
        data = _cached_market_fetch("drill_hot_board", "/api/market/hot_board", params=params, ttl=ttl, config=config)
    elif mode == "dragon-tiger":
        data = _cached_market_fetch("drill_dragon_tiger", "/api/market/dragon_tiger", ttl=ttl, config=config)
    else:
        print(json.dumps({"error": f"未知模式: {mode}"}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


# ==================== drill-deep 子命令 (Stage 3) ====================

def cmd_drill_deep():
    """Stage 3: 决策补充 - 验证假设"""
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "用法: python fund_api.py drill-deep <mode> [args]",
            "modes": {
                "holdings <code>": "基金持仓详情",
                "pe <code>": "持仓 PE 百分位",
                "yesterday-limit": "昨日涨跌停分析",
                "currency [tab]": "汇率数据 (tab: usdcny/offshore 等)",
                "capital-flow [tab]": "资金流向 (tab: north/south 等)",
            },
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    config = _load_config()
    mode = sys.argv[2]

    if mode == "holdings":
        code = sys.argv[3] if len(sys.argv) > 3 else None
        if not code:
            print(json.dumps({"error": "需要基金代码，用法: drill-deep holdings <code>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_fetch(code, "holdings", f"/api/fund/{code}/holdings", ttl=SCAN_TTL, config=config)
    elif mode == "pe":
        code = sys.argv[3] if len(sys.argv) > 3 else None
        if not code:
            print(json.dumps({"error": "需要基金代码，用法: drill-deep pe <code>"}, ensure_ascii=False))
            sys.exit(1)
        data = _cached_fetch(code, "pe_percentile", f"/api/fund/{code}/holdings/pe_percentile", ttl=SCAN_TTL, config=config)
    elif mode == "yesterday-limit":
        data = _cached_market_fetch("drill_yesterday_limit", "/api/market/yesterday_limit", ttl=1800, config=config)
    elif mode == "currency":
        tab = sys.argv[3] if len(sys.argv) > 3 else None
        params = {"tab": tab} if tab else None
        data = _cached_market_fetch("drill_currency", "/api/market/currency", params=params, ttl=1800, config=config)
    elif mode == "capital-flow":
        tab = sys.argv[3] if len(sys.argv) > 3 else None
        params = {"tab": tab} if tab else None
        data = _cached_market_fetch("drill_capital_flow", "/api/market/capital_flow", params=params, ttl=1800, config=config)
    else:
        print(json.dumps({"error": f"未知模式: {mode}"}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


# ==================== CLI ====================

COMMANDS = {
    "scan": cmd_scan,
    "news": cmd_news,
    "market": cmd_market,
    "sync": cmd_sync,
    "news-overview": cmd_news_overview,
    "news-drill": cmd_news_drill,
    "drill-deep": cmd_drill_deep,
    "scan-summary": cmd_scan_summary,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({
            "usage": "python fund_api.py <command>",
            "commands": {
                "scan": "采集基金池多维度数据-完整版 (TTL 4h, ~150KB)",
                "scan-summary": "采集基金池多维度数据-精简版 (TTL 4h, <15KB) ★推荐",
                "news": "采集新闻数据，按 宏观政策/行业动态/个基相关 分类 (TTL 30min)",
                "market": "采集大盘数据 (TTL 30min)",
                "sync": "从同花顺账户同步真实持仓到 ft_positions",
                "news-overview": "Stage 1: 概览扫描 (热门文章+重要快讯+大盘+板块)",
                "news-drill": "Stage 2: 定向深入 (themes/article/flash/...)",
                "drill-deep": "Stage 3: 决策补充 (holdings/pe/currency/...)",
            },
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
