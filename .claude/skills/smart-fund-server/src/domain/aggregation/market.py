"""基础市场数据聚合 (P1)

数据源: 基金净值(THS+Sina)、指数行情(EM+Sina)、个股行情(Tencent+Sina)、
        港美股(Tencent)、全球指数(Sina)、期货/外汇(Sina+Tencent+PBOC)、
        板块涨跌(Sina)、大盘总览(Aggregator)
目标表: ft_market_cache（已有）
"""

import json
import logging
from datetime import date, datetime

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)

# ft_market_cache 已由 fund_db.py 创建，此处只确保索引
DDL = """
CREATE TABLE IF NOT EXISTS ft_market_cache (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) NOT NULL UNIQUE,
    data JSONB NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def _today() -> str:
    return date.today().isoformat()


def _expires_seconds(seconds: int) -> str:
    return (datetime.now() + __import__("datetime").timedelta(seconds=seconds)).isoformat()


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


def normalize_sector_ranking(raw) -> list[dict]:
    return _wrap_cache("sector_ranking", raw, 300)


# ==================== 聚合器 ====================


class MarketAggregator(BaseAggregator):
    """基础市场数据聚合

    高频更新市场快照到 ft_market_cache，供业务层读取。
    """

    data_domain = "market"
    task_interval = 60  # 1 分钟

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

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
            # 板块涨跌排行 — 5 分钟
            SourceDef(
                "sector_ranking",
                lambda cp: clients.sina.get_sector_ranking(),
                300,
                normalize_sector_ranking,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        """写入 ft_market_cache（UPSERT by data_type）"""
        if not items:
            return 0
        saved = 0
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for item in items:
                        data_type = item.get("data_type")
                        if not data_type:
                            continue
                        ttl = item.get("ttl", 300)
                        expires_at = datetime.now() + __import__("datetime").timedelta(seconds=ttl)
                        data_json = json.dumps(item.get("data", {}), ensure_ascii=False, default=str)
                        cur.execute("""
                            INSERT INTO ft_market_cache (data_type, data, expires_at, created_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (data_type) DO UPDATE SET
                                data = EXCLUDED.data,
                                expires_at = EXCLUDED.expires_at,
                                created_at = NOW()
                        """, (data_type, data_json, expires_at))
                        saved += 1
                conn.commit()
        except Exception as e:
            logger.warning(f"ft_market_cache 写入失败: {e}")
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
