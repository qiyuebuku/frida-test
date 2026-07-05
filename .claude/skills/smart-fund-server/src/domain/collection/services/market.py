"""基础市场数据聚合 (P1)

数据源: 基金净值(THS+Sina)、指数行情(EM+Sina)、个股行情(Tencent+Sina)、
        港美股(Tencent)、全球指数(Sina)、期货/外汇(Sina+Tencent+PBOC)、
        板块涨跌(Sina)、板块K线(EM)、大盘总览(Aggregator)
目标表: ft_market_cache（已有）
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime

from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.infrastructure.time_utils import app_now, app_today_iso

logger = logging.getLogger(__name__)

# ft_market_cache 已由 fund_db.py 创建，此处只确保索引
def _today() -> str:
    return app_today_iso()


def _expires_seconds(seconds: int) -> str:
    return (app_now() + __import__("datetime").timedelta(seconds=seconds)).isoformat()


# ==================== Normalize 函数 ====================
# 市场数据聚合不做 normalize 转换，直接以 data_type + 原始数据存入 ft_market_cache


def _wrap_cache(data_type: str, data, ttl: int = 300) -> list[dict]:
    """将原始数据包装为 ft_market_cache 格式"""
    if data is None:
        return []
    return [{
        "data_type": data_type,
        "data": data,
        "ttl": ttl,
    }]


def normalize_market_overview(raw) -> list[dict]:
    return _wrap_cache("market_overview", raw, 120)


def normalize_market_environment(raw) -> list[dict]:
    return _wrap_cache("market_environment", raw, 300)


def normalize_global_index(raw) -> list[dict]:
    return _wrap_cache("global_index", raw, 300)


def normalize_futures_domestic(raw) -> list[dict]:
    return _wrap_cache("futures_domestic", raw, 300)


def normalize_futures_intl(raw) -> list[dict]:
    return _wrap_cache("futures_intl", raw, 300)


def normalize_forex(raw) -> list[dict]:
    return _wrap_cache("forex", raw, 300)


def _calc_cumulative_change(klines: list, days: int) -> float | None:
    """从 K 线数据计算最近 N 天累计涨跌幅（%）

    klines 是 EM K 线返回的 dict 列表：[{date, open, close, high, low, volume, turnover}, ...]
    """
    if not klines or len(klines) < 2:
        return None
    try:
        # 取最近 N+1 条（含今日和 N 天前）
        recent = klines[-(days + 1):] if len(klines) > days else klines
        first = recent[0]
        last = recent[-1]
        first_close = float(first.get("close") if isinstance(first, dict) else first.split(",")[2])
        last_close = float(last.get("close") if isinstance(last, dict) else last.split(",")[2])
        if first_close == 0:
            return None
        return round((last_close - first_close) / first_close * 100, 2)
    except (ValueError, IndexError, AttributeError, TypeError):
        return None


def _strip_market_prefix(code: str) -> str:
    """去掉 sh/sz/bj 前缀，得到纯数字代码"""
    if not code:
        return ""
    if code[:2] in ("sh", "sz", "bj"):
        return code[2:]
    return code


async def _enrich_sector_with_change(em_client, sector_data) -> dict:
    """为每个板块添加 chg_3d / chg_5d 估算（用领涨股代理）"""
    if not isinstance(sector_data, dict):
        return sector_data
    data = sector_data.get("data") or {}
    if not isinstance(data, dict):
        return sector_data

    import asyncio as _asyncio

    # 收集所有板块的领涨股代码（去前缀）
    all_sectors = (data.get("topRise") or []) + (data.get("topFall") or [])
    lead_codes = []
    sector_to_code = {}
    for i, sec in enumerate(all_sectors):
        if not isinstance(sec, dict):
            continue
        lead = sec.get("leadStock") or {}
        raw_code = lead.get("code") if isinstance(lead, dict) else None
        pure_code = _strip_market_prefix(raw_code)
        if pure_code:
            lead_codes.append(pure_code)
            sector_to_code[i] = pure_code

    if not lead_codes:
        return sector_data

    # 并发拉取领涨股的 6 日 K 线
    tasks = [em_client.get_stock_kline(code, period="101", limit=6) for code in lead_codes]
    klines_list = await _asyncio.gather(*tasks, return_exceptions=True)
    code_to_klines = {}
    for code, kr in zip(lead_codes, klines_list):
        if isinstance(kr, Exception):
            continue
        if isinstance(kr, dict):
            kdata = kr.get("data") or {}
            klines = kdata.get("klines") or []
            code_to_klines[code] = klines

    # 给每个 sector 添加 chg_3d / chg_5d
    for i, sec in enumerate(all_sectors):
        if not isinstance(sec, dict):
            continue
        code = sector_to_code.get(i)
        klines = code_to_klines.get(code, []) if code else []
        sec["chg_3d_proxy"] = _calc_cumulative_change(klines, 3)
        sec["chg_5d_proxy"] = _calc_cumulative_change(klines, 5)
        # 过热标记：3 天累计涨幅 > 5%
        chg_3d = sec.get("chg_3d_proxy")
        sec["is_overheated"] = chg_3d is not None and chg_3d > 5.0
    return sector_data


async def _fetch_sector_ranking_enriched(sina_client, em_client) -> dict:
    """先拉板块排行，再为每个板块用领涨股 K 线计算 3d/5d 累计涨幅"""
    raw = await sina_client.get_sector_ranking()
    return await _enrich_sector_with_change(em_client, raw)


def normalize_sector_ranking(raw) -> list[dict]:
    return _wrap_cache("sector_ranking", raw, 300)


# ==================== 板块 K 线 ====================


async def _fetch_sector_kline(em_client) -> dict:
    """拉取板块日 K 线（行业+概念），写入 ft_market_cache

    返回结构: {updated_at, trade_date, sectors: {bk_code: {name, sector_type, klines, change_1d, change_3d}}}
    以及 name_map: {板块名: bk_code} 用于下游映射
    """
    all_sectors = []
    for sector_type in ("industry", "concept"):
        raw = await em_client.get_sector_list(sector_type)
        for s in raw.get("sectors", []):
            s["sector_type"] = sector_type
        all_sectors.extend(raw.get("sectors", []))

    if not all_sectors:
        logger.warning("[sector_kline] EM 板块列表为空，跳过")
        return None

    semaphore = asyncio.Semaphore(10)

    async def fetch_one(sector):
        async with semaphore:
            resp = await em_client.get_sector_kline(sector["bk_code"], period="101", limit=60)
            klines = resp.get("data", {}).get("klines", []) if resp.get("status_code") == 0 else []
            return sector["bk_code"], {
                "name": sector["name"],
                "sector_type": sector["sector_type"],
                "klines": klines,
                "change_1d": _calc_cumulative_change(klines, 1),
                "change_3d": _calc_cumulative_change(klines, 3),
            }

    results = await asyncio.gather(
        *[fetch_one(s) for s in all_sectors],
        return_exceptions=True,
    )

    sectors = {}
    name_map = {}
    success = 0
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[sector_kline] 单板块拉取失败: {r}")
            continue
        bk_code, payload = r
        if payload["klines"]:
            sectors[bk_code] = payload
            name_map[payload["name"]] = bk_code
            success += 1

    logger.info(f"[sector_kline] 完成: {success}/{len(all_sectors)} 板块有 K 线数据")

    data = {
        "updated_at": app_now().isoformat(),
        "trade_date": _today(),
        "sectors": sectors,
        "name_map": name_map,
    }

    # 同时写入 sector_map 供回填查询用
    from src.infrastructure.persistence.repositories import MarketCacheRepositoryImpl
    from datetime import timedelta as _td
    repo = MarketCacheRepositoryImpl()
    expires_at = (app_now() + _td(seconds=900)).replace(tzinfo=None)
    try:
        repo.upsert("sector_map", {"name_map": name_map, "updated_at": data["updated_at"]}, expires_at)
    except Exception as e:
        logger.warning(f"[sector_kline] sector_map 写入失败: {e}")

    return data


def normalize_sector_kline(raw) -> list[dict]:
    return _wrap_cache("sector_kline", raw, 900)


# ==================== 聚合器 ====================


class MarketAggregator(BaseAggregator):
    """基础市场数据聚合

    高频更新市场快照到 ft_market_cache，供业务层读取。
    """

    data_domain = "market"
    task_interval = 60  # 1 分钟

    SOURCE_CONFIGS = {
        "market_overview":    {"target_days": 0, "interval": 120, "default_mode": "incremental"},
        "market_environment": {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "global_index":       {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "futures_domestic":   {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "futures_intl":       {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "forex":              {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "sector_ranking":     {"target_days": 0, "interval": 300, "default_mode": "incremental"},
        "sector_kline":       {"target_days": 0, "interval": 900, "default_mode": "incremental"},
    }

    def __init__(self):
        super().__init__()
        self._init_sources()

    def _init_sources(self):
        from src.infrastructure import clients

        self.sources = [
            # 大盘总览 — 2 分钟
            SourceDef(
                "market_overview",
                lambda cp: clients.aggregator.get_market_overview(),
                120,
                normalize_market_overview,
            ),
            # 市场环境 — 5 分钟
            SourceDef(
                "market_environment",
                lambda cp: clients.aggregator.get_market_environment(),
                300,
                normalize_market_environment,
            ),
            # 全球指数 — 5 分钟
            SourceDef(
                "global_index",
                lambda cp: clients.sina.get_global_index(),
                300,
                normalize_global_index,
            ),
            # 国内期货 — 5 分钟
            SourceDef(
                "futures_domestic",
                lambda cp: clients.sina.get_futures(),
                300,
                normalize_futures_domestic,
            ),
            # 国际期货 — 5 分钟
            SourceDef(
                "futures_intl",
                lambda cp: clients.tencent.get_intl_futures(),
                300,
                normalize_futures_intl,
            ),
            # 外汇 — 5 分钟
            SourceDef(
                "forex",
                lambda cp: clients.sina.get_forex(),
                300,
                normalize_forex,
            ),
            # 板块涨跌排行 — 5 分钟（含 3d/5d 累计涨幅估算 + 过热标记）
            SourceDef(
                "sector_ranking",
                lambda cp: _fetch_sector_ranking_enriched(clients.sina, clients.eastmoney),
                300,
                normalize_sector_ranking,
            ),
            # 板块日 K 线 — 15 分钟（EM 行业+概念板块 K 线，含 change_1d/3d）
            SourceDef(
                "sector_kline",
                lambda cp: _fetch_sector_kline(clients.eastmoney),
                900,
                normalize_sector_kline,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        """写入 ft_market_cache (UPSERT by data_type) — R2.5 改用 MarketCacheRepository"""
        if not items:
            return 0
        from datetime import timedelta as _td
        from src.infrastructure.persistence.repositories import MarketCacheRepositoryImpl
        repo = MarketCacheRepositoryImpl()
        saved = 0
        for item in items:
            data_type = item.get("data_type")
            if not data_type:
                continue
            ttl = item.get("ttl", 300)
            expires_at = (app_now() + _td(seconds=ttl)).replace(tzinfo=None)
            try:
                repo.upsert(data_type, item.get("data", {}), expires_at)
                saved += 1
            except Exception as e:
                logger.warning(f"ft_market_cache {data_type} 写入失败: {e}")
        return saved

    # ==================== 查询 ====================

    async def query(
        self,
        data_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
        values = []
        if data_type:
            conditions.append("data_type = %s")
            values.append(data_type)
        return self._query_table(
            "ft_market_cache",
            conditions=conditions or None,
            values=values or None,
            order_by="created_at DESC",
            limit=limit,
        )

    # ==================== 便捷方法 ====================

    async def get_market_overview(self) -> dict | None:
        """获取最新大盘总览"""
        rows = await self.query(data_type="market_overview", limit=1)
        return rows[0]["data"] if rows else None

    async def get_global_markets(self) -> dict:
        """全球市场快照（指数+期货+外汇）"""
        result = {}
        for dt in ("global_index", "futures_domestic", "futures_intl", "forex"):
            rows = await self.query(data_type=dt, limit=1)
            if rows:
                result[dt] = rows[0]["data"]
        return result

    async def get_sector_overview(self) -> dict | None:
        """板块涨跌排行"""
        rows = await self.query(data_type="sector_ranking", limit=1)
        return rows[0]["data"] if rows else None

    async def get_fund_snapshot(self, fund_code: str) -> dict:
        """基金快照（净值+持仓+详情），按需从客户端获取"""
        from src.infrastructure import clients

        result = {}
        try:
            detail = await clients.ths.get_fund_detail(fund_code)
            result["detail"] = detail
        except Exception as e:
            logger.debug(f"获取基金详情失败: {e}")

        try:
            base = await clients.ths.get_fund_base(fund_code)
            result["base"] = base
        except Exception as e:
            logger.debug(f"获取基金基础信息失败: {e}")

        return result

    async def get_stock_detail(self, code: str) -> dict:
        """个股详情（行情+估值+行业排名+所属板块）"""
        from src.infrastructure import clients

        result = {}
        try:
            quote = await clients.tencent.get_stock_quote([code])
            result["quote"] = quote
        except Exception as e:
            logger.debug(f"获取行情失败: {e}")

        try:
            rank = await clients.tencent.get_industry_rank(code)
            result["industry_rank"] = rank
        except Exception as e:
            logger.debug(f"获取行业排名失败: {e}")

        try:
            plates = await clients.tencent.get_stock_plates(code)
            result["plates"] = plates
        except Exception as e:
            logger.debug(f"获取板块失败: {e}")

        return result

    async def get_holdings_valuation(self, fund_code: str) -> dict:
        """基金重仓股估值（代理到 AggregatorClient）"""
        from src.infrastructure import clients
        return await clients.aggregator.get_holdings_valuation(fund_code)

    # ==================== 板块分钟 K 缓存 ====================

    def __init_memory_cache(self):
        """初始化分钟 K 内存缓存"""
        if not hasattr(self, "_minute_kline_cache"):
            self._minute_kline_cache: dict[tuple[str, date], tuple[float, list]] = {}

    async def get_sector_minute_kline(self, bk_code: str, trade_date: date) -> list:
        """获取板块当日分钟 K 线（5 分钟粒度），带 300s 内存 TTL

        不持久化，按需拉取。用于 event_feedback 计算 reaction_delay_minutes。
        """
        self.__init_memory_cache()
        key = (bk_code, trade_date)
        now = time.time()
        cached = self._minute_kline_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        from src.infrastructure import clients
        resp = await clients.eastmoney.get_sector_minute_kline(bk_code, trade_date)
        klines = resp.get("klines", [])
        self._minute_kline_cache[key] = (now + 300, klines)
        # 清理过期缓存（简单遍历，key 数量可控）
        expired = [k for k, v in self._minute_kline_cache.items() if v[0] <= now]
        for k in expired:
            del self._minute_kline_cache[k]
        return klines

    async def resolve_sector_name_to_bk(self, industry_name: str) -> str | None:
        """将行业名称映射为 EM BK 代码

        优先查 sector_map 缓存（最新），其次查 sector_kline 数据。
        """
        from src.infrastructure.persistence.repositories import MarketCacheRepositoryImpl
        repo = MarketCacheRepositoryImpl()

        # 先查 sector_map
        cache = repo.find_by_type("sector_map")
        if cache and "name_map" in cache:
            bk = cache["name_map"].get(industry_name)
            if bk:
                return bk

        # 回退到 sector_kline 的 name_map
        cache = repo.find_by_type("sector_kline")
        if cache and "data" and "name_map" in cache.get("data", {}):
            bk = cache["data"]["name_map"].get(industry_name)
            if bk:
                return bk

        return None
