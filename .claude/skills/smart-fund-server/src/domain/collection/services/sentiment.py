"""情绪舆情聚合 (P1)

数据源: 股吧人气(EM)、涨跌停池(THS)、雪球热门话题/热股(Xueqiu)、
        腾讯热门股(Tencent)、问财选股(THS)
目标表: ft_sentiment
"""

import json
import logging
from datetime import date

from src.domain.collection.services.base import BaseAggregator, SourceDef

logger = logging.getLogger(__name__)

def _today() -> str:
    return date.today().isoformat()


def _is_empty(data) -> bool:
    """判断 data 是否为空（空 dict / 空 list / None）"""
    if data is None:
        return True
    if isinstance(data, (dict, list, str)) and len(data) == 0:
        return True
    return False


def _wrap(data_type: str, data) -> list[dict]:
    """封装为聚合结果，空数据直接跳过"""
    if _is_empty(data):
        return []
    return [{
        "data_type": data_type,
        "trade_date": _today(),
        "data": data,
    }]


# ==================== Normalize 函数 ====================


def normalize_guba_popularity(raw) -> list[dict]:
    """东方财富股吧人气排行 → 统一格式

    get_guba_popularity 返回 list: [{sc, rk, rc, hisRc}, ...]
    """
    items = raw if isinstance(raw, list) else []
    return _wrap("guba_popularity", items)


def normalize_limit_pool(raw) -> list[dict]:
    """同花顺涨停/跌停池 → 统一格式

    get_limit_pool 返回 dict: {status_code, data: {info:[...], date, ...}}
    """
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    # 判断 data 内部是否真的有内容（info 列表非空）
    if isinstance(data, dict):
        info = data.get("info") or data.get("list") or data.get("items")
        if _is_empty(info):
            return []
    return _wrap("limit_pool", data)


def normalize_xueqiu_hot_topics(raw) -> list[dict]:
    """雪球热门话题 → 统一格式

    get_hot_topics 返回 dict: {data: {count, topics:[...]}, status_code}
    """
    if not raw:
        return []
    items = None
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items = data.get("topics") or data.get("items") or data.get("list")
    return _wrap("xueqiu_hot_topics", items)


def normalize_xueqiu_hot_stocks(raw) -> list[dict]:
    """雪球热股排行 → 统一格式

    get_hot_stocks 返回 dict: {data: {count, stocks:[...]}, status_code}
    """
    if not raw:
        return []
    items = None
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items = data.get("stocks") or data.get("items") or data.get("stock_list")
    return _wrap("xueqiu_hot_stocks", items)


def normalize_tencent_hot_stocks(raw) -> list[dict]:
    """腾讯热门股 → 统一格式

    get_hot_stocks 返回 dict: {data: {5min:[...], 1hour:[...]}, status_code}
    """
    if not raw:
        return []
    data = None
    if isinstance(raw, dict):
        data = raw.get("data")
        # 如果 data 是 dict 且所有 value 都为空，认为无数据
        if isinstance(data, dict):
            has_content = any(not _is_empty(v) for v in data.values())
            if not has_content:
                return []
    return _wrap("tencent_hot_stocks", data)


def normalize_guba_posts(raw) -> list[dict]:
    """东方财富股吧帖子 → 统一格式

    支持两种输入：
    1. 单个 dict：{status_code, data: {code, posts: [...]}}
    2. dict 列表：[{...}, {...}]（来自 _fetch_guba_posts_for_held_stocks 的批量结果）
    每只股票生成一条 ft_sentiment 记录（data_type=guba_posts），data 包含股票代码 + 帖子列表
    """
    if not raw:
        return []

    # 列表输入：递归处理每个元素
    if isinstance(raw, list):
        results = []
        for item in raw:
            results.extend(normalize_guba_posts(item))
        return results

    if not isinstance(raw, dict):
        return []
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        return []
    posts = data.get("posts") or data.get("list") or []
    if _is_empty(posts):
        return []
    code = data.get("code") or ""
    return [{
        "data_type": "guba_posts",
        "trade_date": _today(),
        "data": {
            "code": code,
            "count": len(posts),
            "posts": posts,
        },
    }]


async def _fetch_guba_posts_for_held_stocks(em_client, ths_client, max_stocks: int = 10) -> list:
    """从 watchlist 读取自选股拉取股吧帖子"""
    import asyncio as _asyncio
    from src.infrastructure.db import checkpoint_store

    all_items = checkpoint_store.list_all("watchlist")
    codes = [
        item["source_name"]
        for item in all_items
        if item.get("enabled", True) and item.get("config", {}).get("type") in ("stock", None)
    ][:max_stocks]

    if not codes:
        return []
    # 去掉前缀（sh/sz/bj），股吧 API 用纯数字代码
    pure_codes = [c[2:] if c[:2] in ("sh", "sz", "bj") else c for c in codes]

    tasks = [em_client.get_guba_posts(code) for code in pure_codes]
    results = await _asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception) and r]


# ==================== 聚合器 ====================


class SentimentAggregator(BaseAggregator):
    """情绪舆情聚合

    8 个数据源，统一采集到 ft_sentiment。
    """

    data_domain = "sentiment"
    task_interval = 900  # 15 分钟

    SOURCE_CONFIGS = {
        "guba_popularity":    {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "limit_pool_up":      {"target_days": 0, "interval": 10800, "default_mode": "incremental"},
        "limit_pool_down":    {"target_days": 0, "interval": 10800, "default_mode": "incremental"},
        "xueqiu_hot_topics":  {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "xueqiu_hot_stocks":  {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "tencent_hot_stocks": {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
        "guba_posts":         {"target_days": 0, "interval": 1800, "default_mode": "incremental"},
    }

    def __init__(self):
        super().__init__()
        self._init_sources()

    def _init_sources(self):
        from src.infrastructure import clients

        self.sources = [
            # 股吧人气排行 — 30 分钟
            SourceDef(
                "guba_popularity",
                lambda cp: clients.eastmoney.get_guba_popularity(),
                1800,
                normalize_guba_popularity,
            ),
            # 涨停/跌停池 — 盘后（3 小时间隔）
            SourceDef(
                "limit_pool_up",
                lambda cp: clients.ths.get_limit_pool("up"),
                10800,
                normalize_limit_pool,
            ),
            SourceDef(
                "limit_pool_down",
                lambda cp: clients.ths.get_limit_pool("down"),
                10800,
                normalize_limit_pool,
            ),
            # 雪球热门话题 — 30 分钟
            SourceDef(
                "xueqiu_hot_topics",
                lambda cp: clients.xueqiu.get_hot_topics(),
                1800,
                normalize_xueqiu_hot_topics,
            ),
            # 雪球热股排行 — 30 分钟
            SourceDef(
                "xueqiu_hot_stocks",
                lambda cp: clients.xueqiu.get_hot_stocks(),
                1800,
                normalize_xueqiu_hot_stocks,
            ),
            # 腾讯热门股 — 30 分钟
            SourceDef(
                "tencent_hot_stocks",
                lambda cp: clients.tencent.get_hot_stocks(),
                1800,
                normalize_tencent_hot_stocks,
            ),
            # 个股股吧帖子 — 30 分钟（动态从基金池关注的基金重仓股拉取）
            SourceDef(
                "guba_posts",
                lambda cp: _fetch_guba_posts_for_held_stocks(clients.eastmoney, clients.ths),
                1800,
                normalize_guba_posts,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None  # 情绪数据全量覆盖，无需增量断点

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        """改造后: 走 SentimentRepository (R2.5)"""
        if not items:
            return 0
        clean = []
        for item in items:
            if not item.get("data_type") or not item.get("trade_date"):
                continue
            data = item.get("data")
            if _is_empty(data):
                continue
            clean.append({
                "data_type": item["data_type"],
                "trade_date": item["trade_date"],
                "data": data,
            })
        from src.infrastructure.persistence.repositories import SentimentRepositoryImpl
        return SentimentRepositoryImpl().upsert_batch(clean)

    # ==================== 查询 ====================

    async def query(
        self,
        data_type: str | None = None,
        trade_date: str | None = None,
        limit: int = 50,
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
            "ft_sentiment",
            conditions=conditions or None,
            values=values or None,
            order_by="created_at DESC",
            limit=limit,
        )

    # ==================== 便捷方法 ====================

    async def get_market_temperature(self) -> dict:
        """计算市场情绪温度（综合多指标）

        Returns:
            {"temperature": 0-100, "level": "cold/warm/hot/extreme", "signals": [...]}
        """
        today = _today()
        signals = []
        temperature = 50  # 中性起点

        # 1. 涨停/跌停比
        limit_rows = await self.query(data_type="limit_pool_up", trade_date=today, limit=1)
        if limit_rows:
            data = limit_rows[0].get("data", {})
            if isinstance(data, dict):
                up_count = data.get("total") or data.get("count") or 0
                if up_count > 80:
                    temperature += 20
                    signals.append(f"涨停 {up_count} 只，市场活跃")
                elif up_count < 20:
                    temperature -= 15
                    signals.append(f"涨停仅 {up_count} 只，市场冷淡")

        # 2. 热股重叠度
        xq_rows = await self.query(data_type="xueqiu_hot_stocks", trade_date=today, limit=1)
        tc_rows = await self.query(data_type="tencent_hot_stocks", trade_date=today, limit=1)
        if xq_rows and tc_rows:
            signals.append("雪球+腾讯热股数据已采集")

        # 3. 股吧人气
        guba_rows = await self.query(data_type="guba_popularity", trade_date=today, limit=1)
        if guba_rows:
            signals.append("股吧人气数据已采集")

        # 分级
        temperature = max(0, min(100, temperature))
        if temperature >= 80:
            level = "extreme"
        elif temperature >= 60:
            level = "hot"
        elif temperature >= 40:
            level = "warm"
        else:
            level = "cold"

        return {"temperature": temperature, "level": level, "signals": signals}
