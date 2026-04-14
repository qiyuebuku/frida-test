"""资金流聚合 (P0)

数据源: 北向资金(EM)、板块资金流(EM+Sina)、个股主力资金(Tencent)、
        龙虎榜(EM+THS)、盘中异动(EM)
目标表: ft_market_flow
"""

import json
import logging
from datetime import datetime, date

from src.domain.collection.services.base import BaseAggregator, SourceDef

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS ft_market_flow (
    id          SERIAL PRIMARY KEY,
    data_type   VARCHAR(32) NOT NULL,
    trade_date  DATE NOT NULL,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ft_market_flow_type_date ON ft_market_flow(data_type, trade_date);
"""

# ==================== Normalize 函数 ====================


def _today() -> str:
    return date.today().isoformat()


def normalize_northbound(raw) -> list[dict]:
    """东方财富北向资金 → 统一格式

    get_northbound_recent 返回: {code, result:{data:[{TRADE_DATE, DEAL_AMT, MUTUAL_TYPE}], count, pages}, message, success}
    """
    items_list = []
    if isinstance(raw, dict):
        # 优先 result.data（真实返回），回退 data.data
        result = raw.get("result") or {}
        if isinstance(result, dict):
            items_list = result.get("data") or []
        if not items_list:
            data = raw.get("data") or {}
            if isinstance(data, dict):
                items_list = data.get("data") or []
            elif isinstance(data, list):
                items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        trade_date = (item.get("TRADE_DATE") or "")[:10]
        if not trade_date:
            continue
        results.append({
            "data_type": "northbound",
            "trade_date": trade_date,
            "data": {
                "net_flow": item.get("DEAL_AMT"),
                "mutual_type": item.get("MUTUAL_TYPE"),
                "raw": {k: v for k, v in item.items() if v is not None},
            },
        })
    return results


def normalize_sector_flow_em(raw) -> list[dict]:
    """东方财富板块资金流 → 统一格式

    get_index_capital_flow_daily 返回 dict
    """
    if not raw or not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    items_list = data.get("data", []) if isinstance(data, dict) else []
    if not items_list and isinstance(data, list):
        items_list = data

    results = []
    for item in items_list:
        results.append({
            "data_type": "sector_flow",
            "trade_date": _today(),
            "data": {
                "name": item.get("BOARD_NAME") or item.get("TRADE_NAME") or "",
                "net_amount": item.get("NET_INFLOW") or item.get("CHANGE_RATE") or 0,
                "source": "eastmoney",
                "raw": item,
            },
        })
    return results if results else [{
        "data_type": "sector_flow",
        "trade_date": _today(),
        "data": {"source": "eastmoney", "raw": raw},
    }]


def normalize_sector_flow_sina(raw) -> list[dict]:
    """新浪板块资金流 → 统一格式

    get_sector_money_flow 返回: {data:{type, count, sectors:[{name, bigIn, midIn, bigOut, midOut,
                                 largeIn, smallIn, largeOut, smallOut, netAmount}]}, status_code}
    """
    items_list = []
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("sectors") or data.get("data") or []
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        results.append({
            "data_type": "sector_flow",
            "trade_date": _today(),
            "data": {
                "name": item.get("name") or "",
                "net_amount": item.get("netAmount") or item.get("net") or 0,
                "big_in": item.get("bigIn") or 0,
                "big_out": item.get("bigOut") or 0,
                "mid_in": item.get("midIn") or 0,
                "mid_out": item.get("midOut") or 0,
                "large_in": item.get("largeIn") or 0,
                "large_out": item.get("largeOut") or 0,
                "small_in": item.get("smallIn") or 0,
                "small_out": item.get("smallOut") or 0,
                "source": "sina",
            },
        })
    return results


def _market_prefix(market_id: int | str) -> str:
    """同花顺 marketid → 腾讯接口前缀

    17 = 上交所主板 → sh
    33 = 深交所主板/创业板 → sz
    151 = 北交所 → bj
    """
    try:
        mid = int(market_id) if market_id else 0
    except (ValueError, TypeError):
        mid = 0
    if mid == 17:
        return "sh"
    elif mid in (33,):
        return "sz"
    elif mid == 151:
        return "bj"
    return "sh"  # 默认沪市


def _code_with_prefix(sec_code: str, market_id: int | str = None) -> str:
    """secCode → 带前缀的代码（如 sh600183）"""
    if not sec_code:
        return ""
    if sec_code.startswith(("sh", "sz", "bj")):
        return sec_code
    if market_id:
        return f"{_market_prefix(market_id)}{sec_code}"
    # 按代码前缀推断
    if sec_code.startswith("6"):
        return f"sh{sec_code}"
    elif sec_code.startswith(("0", "3")):
        return f"sz{sec_code}"
    elif sec_code.startswith(("4", "8", "9")):
        return f"bj{sec_code}"
    return f"sh{sec_code}"


def _is_a_share_code(sec_code: str) -> bool:
    """判断是否为 A 股代码（6 位数字，沪/深/北）

    腾讯个股资金流接口只支持 A 股，需要过滤掉港股(5 位)、美股(字母)、QDII 持仓等。
    """
    if not sec_code or not isinstance(sec_code, str):
        return False
    code = sec_code.strip()
    if len(code) != 6 or not code.isdigit():
        return False
    # 沪市 6/9 / 深市 0/3 / 北交所 4/8
    return code[0] in ("0", "3", "4", "6", "8", "9")



# ==================== 自选标的采集 ====================

# 基金采集维度配置
# high_freq: 每次 tick 都采（盘中数据）
# daily: 每天采一次（日频数据）
# low_freq: 每周/每月采一次（变化慢的数据）
FUND_DIMENSIONS_HIGH = ["nav", "realtime"]
FUND_DIMENSIONS_DAILY = ["holdings", "performance", "flow_trend"]
FUND_DIMENSIONS_LOW = [
    "scale", "holder_ratio", "dividend", "year_return",
    "max_drawdown", "holding_overview", "fund_detail",
    "style_preference",
]


def _is_exchange_traded(code: str) -> bool:
    """判断是否场内基金（ETF/LOF）"""
    return code.startswith(("15", "51", "16"))


async def _fetch_watchlist_data(ths_client, tencent_client, sina_client=None, em_client=None) -> list[dict]:
    """遍历 watchlist，按 type 采集各维度数据

    返回 [{"code", "data_type", "trade_date", "data"}, ...]
    直接写入 ft_watchlist_data（不走 ft_market_flow）。
    """
    import asyncio as _asyncio
    from src.infrastructure.db import checkpoint_store

    all_items = checkpoint_store.list_all("watchlist")
    if not all_items:
        return []

    results = []
    today = date.today()

    for item in all_items:
        if not item.get("enabled", True):
            continue

        code = item["source_name"]
        config = item.get("config", {})
        item_type = config.get("type", "fund")

        try:
            code_results = []

            if item_type == "fund":
                # 构建采集任务列表
                dim_tasks = {}
                # 高频：每次都采
                dim_tasks["nav"] = ths_client.get_nav_trend(code, period="year")
                dim_tasks["realtime"] = ths_client.get_realtime_trend(code)
                # 日频
                dim_tasks["holdings"] = ths_client.get_top10_holdings(code)
                dim_tasks["performance"] = ths_client.get_performance_rank(code)
                dim_tasks["flow_trend"] = ths_client.get_fund_flow_trend(code)
                # 低频（每次都采，但数据变化慢，ON CONFLICT 会自动去重）
                dim_tasks["scale"] = ths_client.get_scale_change(code)
                dim_tasks["holder_ratio"] = ths_client.get_holder_ratio(code)
                dim_tasks["dividend"] = ths_client.get_dividend_history(code)
                dim_tasks["year_return"] = ths_client.get_year_return(code)
                dim_tasks["max_drawdown"] = ths_client.get_max_drawdown(code)
                dim_tasks["holding_overview"] = ths_client.get_holding_overview(code)
                dim_tasks["fund_detail"] = ths_client.get_fund_detail(code)
                dim_tasks["style_preference"] = ths_client.get_style_preference(code)

                # 并发采集所有维度
                keys = list(dim_tasks.keys())
                raw_results = await _asyncio.gather(*dim_tasks.values(), return_exceptions=True)

                for dim, raw in zip(keys, raw_results):
                    if isinstance(raw, Exception):
                        logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                        continue
                    data = raw.get("data", raw) if isinstance(raw, dict) else raw
                    if data:
                        code_results.append({
                            "code": code, "data_type": dim,
                            "trade_date": today, "data": data,
                        })

                # 场内基金追加个股维度（资金流/行情/K线）
                if _is_exchange_traded(code):
                    prefix = "sz" if code.startswith("15") else "sh"
                    full_code = f"{prefix}{code}"
                    stock_tasks = {
                        "stock_flow": tencent_client.get_stock_fund_flow(full_code),
                        "quote": tencent_client.get_stock_quote([full_code]),
                        "minute_data": tencent_client.get_minute_data(full_code),
                    }
                    if sina_client:
                        stock_tasks["kline"] = sina_client.get_kline(full_code, scale=240, datalen=60)

                    stock_keys = list(stock_tasks.keys())
                    stock_results = await _asyncio.gather(*stock_tasks.values(), return_exceptions=True)
                    for dim, raw in zip(stock_keys, stock_results):
                        if isinstance(raw, Exception):
                            logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                            continue
                        data = raw.get("data", raw) if isinstance(raw, dict) else raw
                        if data:
                            code_results.append({
                                "code": code, "data_type": dim,
                                "trade_date": today, "data": data,
                            })

            elif item_type == "stock":
                # 个股维度：资金流 + 行情 + K线 + 估值
                stock_tasks = {
                    "stock_flow": tencent_client.get_stock_fund_flow(code),
                    "quote": tencent_client.get_stock_quote([code]),
                    "minute_data": tencent_client.get_minute_data(code),
                }
                if sina_client:
                    stock_tasks["kline"] = sina_client.get_kline(code, scale=240, datalen=60)
                if em_client:
                    # 估值历史（纯数字代码）
                    pure_code = code.replace("sh", "").replace("sz", "")
                    stock_tasks["valuation"] = em_client.get_stock_valuation_history(pure_code, years=1)

                stock_keys = list(stock_tasks.keys())
                stock_results = await _asyncio.gather(*stock_tasks.values(), return_exceptions=True)
                for dim, raw in zip(stock_keys, stock_results):
                    if isinstance(raw, Exception):
                        logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                        continue
                    data = raw.get("data", raw) if isinstance(raw, dict) else raw
                    if isinstance(data, list) and data:
                        code_results.append({
                            "code": code, "data_type": dim,
                            "trade_date": today, "data": data,
                        })
                    elif isinstance(data, dict) and data:
                        code_results.append({
                            "code": code, "data_type": dim,
                            "trade_date": today, "data": data,
                        })

            results.extend(code_results)

            # 更新该标的的采集状态
            if code_results:
                checkpoint_store.update_success("watchlist", code, {
                    "mode": "incremental",
                    "newest_time": today.isoformat(),
                }, saved_count=len(code_results))

        except Exception as e:
            logger.warning(f"[watchlist:{code}] 采集异常: {e}")

    # ── QDII 关联：全市场级数据（只采一次，code='_market'） ──
    try:
        market_tasks = {}
        if sina_client:
            market_tasks["global_index"] = sina_client.get_global_index()
            market_tasks["forex"] = sina_client.get_forex()
        if tencent_client:
            market_tasks["intl_futures"] = tencent_client.get_intl_futures()

        if market_tasks:
            mkeys = list(market_tasks.keys())
            mresults = await _asyncio.gather(*market_tasks.values(), return_exceptions=True)
            for dim, raw in zip(mkeys, mresults):
                if isinstance(raw, Exception):
                    logger.debug(f"[watchlist:_market] {dim} 失败: {raw}")
                    continue
                data = raw.get("data", raw) if isinstance(raw, dict) else raw
                if data:
                    results.append({
                        "code": "_market", "data_type": dim,
                        "trade_date": today, "data": data,
                    })
    except Exception as e:
        logger.warning(f"[watchlist:_market] QDII 关联采集异常: {e}")

    if results:
        logger.info(f"[watchlist] 采集完成: {len(all_items)} 只标的, {len(results)} 条数据")

    return results


def _normalize_watchlist_data(raw_items: list) -> list[dict]:
    """watchlist 数据不需要额外 normalize，直接透传"""
    return raw_items if isinstance(raw_items, list) else []


async def fetch_stock_flow_dynamic(tencent_client, ths_client) -> list[dict]:
    """从 ft_collection_state (aggregator=watchlist) 驱动采集个股资金流

    遍历自选标的中 enabled=True 的股票，并发拉取资金流。
    """
    import asyncio as _asyncio
    from src.infrastructure.db import checkpoint_store

    # 从 watchlist 读取自选股
    all_items = checkpoint_store.list_all("watchlist")
    codes = [
        item["source_name"]
        for item in all_items
        if item.get("enabled", True) and item.get("config", {}).get("type") in ("stock", None)
    ]

    if not codes:
        return []

    # 并发拉取所有股票的资金流
    tasks = [tencent_client.get_stock_fund_flow(code) for code in codes]
    results = await _asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception) and r]


def normalize_stock_flow(raw) -> list[dict]:
    """腾讯个股主力资金 → 展开为每个(code, 日期)一行

    腾讯接口返回:
        {data: {
            code: "sh603979",
            today: {mainNetIn, mainOut, mainInRate, ...},     # 当天实时
            history: [                                          # 近 ~10 天 K 线
                {date: "2026-04-10", price: "60.75", mainNetIn: -6049725},
                ...
            ],
            fiveDayList: [...],
            minuteTrend: [...]
        }}

    展开策略:
        - history 每条 → 一行 (code, date, mainNetIn, price)
        - today → 一行 (code, today_date, today 全字段)
        history 通常包含到 T-1，today 是 T，组合后覆盖最近 10 个交易日

    支持两种输入:
        1. 单个 dict (单只股票)
        2. dict 列表 (来自 fetch_stock_flow_dynamic 批量)
    """
    if not raw:
        return []

    if isinstance(raw, list):
        results = []
        for item in raw:
            results.extend(normalize_stock_flow(item))
        return results

    if not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return []

    today = data.get("today")
    if not isinstance(today, dict) or not today:
        return []
    # 全 0 跳过（指数或非交易日，code 无效）
    numeric_vals = [v for v in today.values() if isinstance(v, (int, float))]
    if numeric_vals and all(v == 0 for v in numeric_vals):
        return []

    code = data.get("code") or ""
    if not code:
        return []

    results: list[dict] = []

    # 1) history 数组展开成每天一行
    history = data.get("history") or []
    if isinstance(history, list):
        for h in history:
            if not isinstance(h, dict):
                continue
            h_date = (h.get("date") or "")[:10]
            if not h_date:
                continue
            results.append({
                "data_type": "stock_flow",
                "trade_date": h_date,
                "data": {
                    "code": code,
                    "date": h_date,
                    "price": h.get("price"),
                    "mainNetIn": h.get("mainNetIn"),
                    "source": "tencent",
                    "from": "history",
                },
            })

    # 2) today 实时值作为今日一行（同 code+今日 已有则被 ON CONFLICT 跳过）
    results.append({
        "data_type": "stock_flow",
        "trade_date": _today(),
        "data": {
            "code": code,
            "date": _today(),
            "today": today,
            "source": "tencent",
            "from": "today",
        },
    })

    return results


def normalize_dragon_tiger_em(raw) -> list[dict]:
    """东方财富龙虎榜 → 统一格式

    get_dragon_tiger 返回: {data:{tab:"stock", items:[{code, date, name, close, buyAmt, sellAmt,
                           netAmt, reason, explain, changeRate, turnoverRate, ...}]}}
    """
    items_list = []
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("items") or data.get("data") or []
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        trade_date = (item.get("date") or item.get("TRADE_DATE") or "")[:10] or _today()
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": trade_date,
            "data": {
                "source": "eastmoney",
                "code": item.get("code"),
                "name": item.get("name"),
                "close": item.get("close"),
                "change_rate": item.get("changeRate"),
                "turnover_rate": item.get("turnoverRate"),
                "buy_amt": item.get("buyAmt"),
                "sell_amt": item.get("sellAmt"),
                "net_amt": item.get("netAmt"),
                "reason": item.get("reason"),
                "explain": item.get("explain"),
            },
        })
    return results


def normalize_dragon_tiger_ths(raw) -> list[dict]:
    """同花顺龙虎榜 → 统一格式

    get_ths_dragon_tiger 返回: {data:{tab:"youzi", date:"...", items:[{code, name, chg, days, price,
                               seats:[{dept, side, buy, sell, net, label}]}]}}
    """
    items_list = []
    trade_date = _today()
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("items") or data.get("list") or []
            trade_date = (data.get("date") or trade_date)[:10]
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": trade_date,
            "data": {
                "source": "ths",
                "code": item.get("code"),
                "name": item.get("name"),
                "price": item.get("price"),
                "chg": item.get("chg"),
                "days": item.get("days"),
                "seats": item.get("seats", []),
            },
        })
    return results


# ==================== 聚合器 ====================


class FundFlowAggregator(BaseAggregator):
    """资金流聚合

    6 个数据源，统一采集到 ft_market_flow。
    """

    data_domain = "fund_flow"
    task_interval = 300  # 5 分钟

    SOURCE_CONFIGS = {
        "northbound":       {"target_days": 60, "page_size": 60, "interval": 600},
        "sector_flow_sina": {"target_days": 0, "page_size": 50, "interval": 1800, "default_mode": "incremental"},
        "stock_flow":       {"target_days": 0, "page_size": 50, "interval": 1800, "default_mode": "incremental"},
        "dragon_tiger_em":  {"target_days": 0, "page_size": 50, "interval": 21600, "default_mode": "incremental"},
        "dragon_tiger_ths": {"target_days": 0, "page_size": 50, "interval": 21600, "default_mode": "incremental"},
        "watchlist_data":   {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
    }

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

    def _init_sources(self):
        from src.infrastructure import clients

        # checkpoint 是 dict（来自 ft_collection_state）
        # cp == {} → 首次拉取（全量）
        # cp == {"max_trade_date": "..."} → 增量

        def _northbound_fetch(cp):
            # 首次：拉 60 天历史；增量：拉 7 天即可（防漏掉补录）
            is_first = not cp or not cp.get("max_trade_date")
            page_size = 60 if is_first else 7
            return clients.eastmoney.get_northbound_recent(page_size=page_size)

        self.sources = [
            # 北向资金 — 10 分钟
            SourceDef(
                "northbound",
                _northbound_fetch,
                600,
                normalize_northbound,
            ),
            # 板块资金流（新浪，含超大/大/中/小单分项）— 30 分钟
            # 新浪接口只有当前快照，无历史端点，靠 cron 持续累积
            SourceDef(
                "sector_flow_sina",
                lambda cp: clients.sina.get_sector_money_flow(),
                1800,
                normalize_sector_flow_sina,
            ),
            # 个股主力资金 — 30 分钟（动态从基金池关注的基金的重仓股拉取）
            # 腾讯接口一次返回 today + history(~10 天)，normalize 已展开成多行
            SourceDef(
                "stock_flow",
                lambda cp: fetch_stock_flow_dynamic(clients.tencent, clients.ths),
                1800,
                normalize_stock_flow,
            ),
            # 龙虎榜 — 东方财富，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_em",
                lambda cp: clients.eastmoney.get_dragon_tiger(),
                21600,
                normalize_dragon_tiger_em,
            ),
            # 龙虎榜 — 同花顺，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_ths",
                lambda cp: clients.ths.get_ths_dragon_tiger(),
                21600,
                normalize_dragon_tiger_ths,
            ),
            # 自选标的数据采集 — watchlist 驱动，30 分钟
            SourceDef(
                "watchlist_data",
                lambda cp: _fetch_watchlist_data(
                    clients.ths, clients.tencent, clients.sina, clients.eastmoney,
                ),
                1800,
                _normalize_watchlist_data,
            ),
        ]

    # checkpoint 现在统一由 base.py 的 checkpoint_store 管理
    # 子类无需再实现 _get_checkpoint
    # _compute_checkpoint 用 base.py 的默认实现（取 items 最大 trade_date）即可

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        """路由写入：有 code 字段 → ft_watchlist_data，否则 → ft_market_flow"""
        if not items:
            return 0

        watchlist_items = []
        market_items = []

        for item in items:
            data = item.get("data")
            if data is None or (isinstance(data, (dict, list, str)) and len(data) == 0):
                continue

            if item.get("code"):
                # watchlist 数据 → ft_watchlist_data
                watchlist_items.append({
                    "code": item["code"],
                    "data_type": item["data_type"],
                    "trade_date": item.get("trade_date"),
                    "data": data,
                })
            else:
                # 全市场数据 → ft_market_flow
                if not item.get("data_type") or not item.get("trade_date"):
                    continue
                market_items.append({
                    "data_type": item["data_type"],
                    "trade_date": item["trade_date"],
                    "data": data,
                })

        saved = 0
        if market_items:
            from src.infrastructure.persistence.repositories import MarketFlowRepositoryImpl
            saved += MarketFlowRepositoryImpl().upsert_batch(market_items)
        if watchlist_items:
            from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import WatchlistDataRepositoryImpl
            saved += WatchlistDataRepositoryImpl().save_batch(watchlist_items)

        return saved

    # ==================== 查询 ====================

    async def query(
        self,
        data_type: str | None = None,
        trade_date: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        values = []
        if data_type:
            conditions.append("data_type = %s")
            values.append(data_type)
        if trade_date:
            conditions.append("trade_date = %s")
            values.append(trade_date)
        return self._query_table(
            "ft_market_flow",
            conditions=conditions or None,
            values=values or None,
            order_by="created_at DESC",
            limit=limit,
        )

    # ==================== 便捷方法 ====================

    async def get_latest_northbound(self) -> list[dict]:
        """获取最新北向资金数据"""
        return await self.query(data_type="northbound", limit=5)

    async def get_sector_flow(self, trade_date: str | None = None) -> list[dict]:
        """获取板块资金流"""
        return await self.query(data_type="sector_flow", trade_date=trade_date or _today())

    async def get_dragon_tiger(self, trade_date: str | None = None) -> list[dict]:
        """获取龙虎榜"""
        return await self.query(data_type="dragon_tiger", trade_date=trade_date or _today())
