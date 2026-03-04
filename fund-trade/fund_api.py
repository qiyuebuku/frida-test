#!/usr/bin/env python3
"""基金数据采集层 - 封装 server.py:8900 调用，PG 透明缓存"""

import json
import os
import sys

import requests

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
}

FUND_SCAN_PARAMS = {
    "nav": {"period": "nowyear"},
}

MARKET_ENDPOINTS = {
    "market_overview": "/api/market/overview",
    "capital_flow": "/api/market/capital_flow",
    "sector_ranking": "/api/market/sector_ranking",
}

NEWS_MARKET_ENDPOINTS = {
    "flash_news": ("/api/flash_news/list", {"tag": "重要"}),
    "headlines": ("/api/headlines", None),
    "hotlist_topics": ("/api/hotlist/topics", {"market": "a"}),
    "sector_ranking": ("/api/market/sector_ranking", None),
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

    print(json.dumps(result, ensure_ascii=False, indent=2))


# ==================== news 子命令 ====================

def _classify_news(fund_pool, fund_news, flash_news, headlines, hotlist, sector_ranking):
    """将新闻按 宏观政策/行业动态/个基相关 分类组织"""
    classified = {
        "宏观政策": [],
        "行业动态": [],
        "个基相关": {},
    }

    # 快讯和头条归入宏观政策
    if flash_news and "error" not in flash_news:
        classified["宏观政策"].append({"source": "flash_news", "data": flash_news})
    if headlines and "error" not in headlines:
        classified["宏观政策"].append({"source": "headlines", "data": headlines})

    # 热点话题和板块排名归入行业动态
    if hotlist and "error" not in hotlist:
        classified["行业动态"].append({"source": "hotlist_topics", "data": hotlist})
    if sector_ranking and "error" not in sector_ranking:
        classified["行业动态"].append({"source": "sector_ranking", "data": sector_ranking})

    # 基金新闻归入个基相关
    for code in fund_pool:
        news = fund_news.get(code)
        if news and "error" not in news:
            classified["个基相关"][code] = news

    return classified


def cmd_news():
    """采集新闻数据"""
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

    # 采集市场级新闻
    market_news = {}
    for data_type, (endpoint, params) in NEWS_MARKET_ENDPOINTS.items():
        market_news[data_type] = _cached_market_fetch(
            data_type=data_type,
            endpoint=endpoint,
            params=params,
            ttl=NEWS_TTL,
            config=config,
        )

    classified = _classify_news(
        fund_pool=fund_pool,
        fund_news=fund_news,
        flash_news=market_news.get("flash_news"),
        headlines=market_news.get("headlines"),
        hotlist=market_news.get("hotlist_topics"),
        sector_ranking=market_news.get("sector_ranking"),
    )

    print(json.dumps(classified, ensure_ascii=False, indent=2))


# ==================== market 子命令 ====================

def cmd_market():
    """采集大盘数据"""
    config = _load_config()
    result = {}
    for data_type, endpoint in MARKET_ENDPOINTS.items():
        result[data_type] = _cached_market_fetch(
            data_type=data_type,
            endpoint=endpoint,
            ttl=MARKET_TTL,
            config=config,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


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
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ==================== CLI ====================

COMMANDS = {
    "scan": cmd_scan,
    "news": cmd_news,
    "market": cmd_market,
    "sync": cmd_sync,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({
            "usage": "python fund_api.py <command>",
            "commands": {
                "scan": "采集基金池多维度数据 (TTL 4h)",
                "news": "采集新闻数据，按 宏观政策/行业动态/个基相关 分类 (TTL 30min)",
                "market": "采集大盘数据 (TTL 30min)",
                "sync": "从同花顺账户同步真实持仓到 ft_positions",
            },
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
