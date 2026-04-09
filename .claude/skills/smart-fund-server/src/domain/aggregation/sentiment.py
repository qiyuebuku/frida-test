"""情绪舆情聚合 (P1)

数据源: 股吧人气(EM)、涨跌停池(THS)、雪球热门话题/热股(Xueqiu)、
        腾讯热门股(Tencent)、问财选股(THS)
目标表: ft_sentiment
"""

import json
import logging
from datetime import date

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS ft_sentiment (
    id          SERIAL PRIMARY KEY,
    data_type   VARCHAR(32) NOT NULL,
    trade_date  DATE NOT NULL,
    data        JSONB NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ft_sentiment_type_date ON ft_sentiment(data_type, trade_date);
"""


def _today() -> str:
    return date.today().isoformat()


# ==================== Normalize 函数 ====================


def normalize_guba_popularity(raw) -> list[dict]:
    """东方财富股吧人气排行 → 统一格式

    get_guba_popularity 返回 list: [{sc, rk, rc, hisRc}, ...]
    """
    items = raw if isinstance(raw, list) else []
    if not items:
        return []
    return [{
        "data_type": "guba_popularity",
        "trade_date": _today(),
        "data": items,
    }]


def normalize_limit_pool(raw) -> list[dict]:
    """同花顺涨停/跌停池 → 统一格式

    get_limit_pool 返回 dict: {status_code, data: {...}}
    """
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    return [{
        "data_type": "limit_pool",
        "trade_date": _today(),
        "data": data,
    }]


def normalize_xueqiu_hot_topics(raw) -> list[dict]:
    """雪球热门话题 → 统一格式

    get_hot_topics 返回 dict: {status_code, data: {items: [...]}}
    """
    if not raw:
        return []
    data = raw
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            data = data.get("items") or data.get("list") or data
    return [{
        "data_type": "xueqiu_hot_topics",
        "trade_date": _today(),
        "data": data,
    }]


def normalize_xueqiu_hot_stocks(raw) -> list[dict]:
    """雪球热股排行 → 统一格式

    get_hot_stocks 返回 dict: {status_code, data: {items: [...]}}
    """
    if not raw:
        return []
    data = raw
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            data = data.get("items") or data.get("stock_list") or data
    return [{
        "data_type": "xueqiu_hot_stocks",
        "trade_date": _today(),
        "data": data,
    }]


def normalize_tencent_hot_stocks(raw) -> list[dict]:
    """腾讯热门股 → 统一格式

    get_hot_stocks 返回 dict: {status_code, data: {5min:[...], 1hour:[...], ...}}
    """
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    return [{
        "data_type": "tencent_hot_stocks",
        "trade_date": _today(),
        "data": data,
    }]


def normalize_guba_posts(raw) -> list[dict]:
    """东方财富股吧帖子 → 统一格式

    get_guba_posts 返回 dict: {status_code, data: {posts: [...]}}
    """
    if not raw:
        return []
    data = raw
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            data = data.get("posts") or data.get("list") or data
    return [{
        "data_type": "guba_posts",
        "trade_date": _today(),
        "data": data,
    }]


# ==================== 聚合器 ====================


class SentimentAggregator(BaseAggregator):
    """情绪舆情聚合

    8 个数据源，统一采集到 ft_sentiment。
    """

    data_domain = "sentiment"
    task_interval = 900  # 15 分钟

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

    def _init_sources(self):
        from src.interfaces.api.routes import _utils

        self.sources = [
            # 股吧人气排行 — 30 分钟
            SourceDef(
                "guba_popularity",
                lambda cp: _utils.eastmoney.get_guba_popularity(),
                1800,
                normalize_guba_popularity,
            ),
            # 涨停/跌停池 — 盘后（3 小时间隔）
            SourceDef(
                "limit_pool_up",
                lambda cp: _utils.ths.get_limit_pool("up"),
                10800,
                normalize_limit_pool,
            ),
            SourceDef(
                "limit_pool_down",
                lambda cp: _utils.ths.get_limit_pool("down"),
                10800,
                normalize_limit_pool,
            ),
            # 雪球热门话题 — 30 分钟
            SourceDef(
                "xueqiu_hot_topics",
                lambda cp: _utils.xueqiu.get_hot_topics(),
                1800,
                normalize_xueqiu_hot_topics,
            ),
            # 雪球热股排行 — 30 分钟
            SourceDef(
                "xueqiu_hot_stocks",
                lambda cp: _utils.xueqiu.get_hot_stocks(),
                1800,
                normalize_xueqiu_hot_stocks,
            ),
            # 腾讯热门股 — 30 分钟
            SourceDef(
                "tencent_hot_stocks",
                lambda cp: _utils.tencent.get_hot_stocks(),
                1800,
                normalize_tencent_hot_stocks,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None  # 情绪数据全量覆盖，无需增量断点

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        if not items:
            return 0
        columns = ["data_type", "trade_date", "data"]
        rows = []
        for item in items:
            if not item.get("data_type") or not item.get("trade_date"):
                continue
            rows.append((
                item["data_type"],
                item["trade_date"],
                json.dumps(item.get("data", {}), ensure_ascii=False, default=str),
            ))
        return self._insert_many("ft_sentiment", columns, rows)

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
            order_by="captured_at DESC",
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
