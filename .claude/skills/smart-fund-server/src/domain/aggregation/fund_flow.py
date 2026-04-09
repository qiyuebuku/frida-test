"""资金流聚合 (P0)

数据源: 北向资金(EM)、板块资金流(EM+Sina)、个股主力资金(Tencent)、
        龙虎榜(EM+THS)、盘中异动(EM)
目标表: ft_market_flow
"""

import json
import logging
from datetime import datetime, date

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS ft_market_flow (
    id          SERIAL PRIMARY KEY,
    data_type   VARCHAR(32) NOT NULL,
    trade_date  DATE NOT NULL,
    data        JSONB NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ft_market_flow_type_date ON ft_market_flow(data_type, trade_date);
"""

# ==================== Normalize 函数 ====================


def _today() -> str:
    return date.today().isoformat()


def normalize_northbound(raw) -> list[dict]:
    """东方财富北向资金 → 统一格式

    get_northbound_recent 返回 dict: {status_code, data: {data: [{TRADE_DATE, DEAL_AMT, ...}]}}
    """
    items_list = []
    if isinstance(raw, dict):
        inner = raw.get("data", raw)
        if isinstance(inner, dict):
            items_list = inner.get("data", [])
        elif isinstance(inner, list):
            items_list = inner
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        trade_date = (item.get("TRADE_DATE") or "")[:10]
        if not trade_date:
            continue
        results.append({
            "data_type": "northbound",
            "trade_date": trade_date,
            "data": {
                "net_flow": item.get("DEAL_AMT"),
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

    get_sector_money_flow 返回 dict: {status_code, data: [{name, inflows, outflows, net, ...}]}
    """
    items_list = []
    if isinstance(raw, dict):
        items_list = raw.get("data", [])
        if isinstance(items_list, dict):
            items_list = items_list.get("data", [])
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
                "net_amount": item.get("net") or item.get("net_flow") or 0,
                "big_in": item.get("big_in") or 0,
                "big_out": item.get("big_out") or 0,
                "source": "sina",
                "raw": item,
            },
        })
    return results


def normalize_stock_flow(raw) -> list[dict]:
    """腾讯个股主力资金 → 统一格式

    get_stock_fund_flow 返回 dict: {status_code, data: {today:{...}, minutes:[...], ...}}
    """
    if not raw or not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    return [{
        "data_type": "stock_flow",
        "trade_date": _today(),
        "data": data,
    }]


def normalize_dragon_tiger_em(raw) -> list[dict]:
    """东方财富龙虎榜 → 统一格式

    get_dragon_tiger 返回 dict: {status_code, data: {data: [...]}}
    """
    items_list = []
    if isinstance(raw, dict):
        inner = raw.get("data", raw)
        if isinstance(inner, dict):
            items_list = inner.get("data", [])
        elif isinstance(inner, list):
            items_list = inner
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        trade_date = (item.get("TRADE_DATE") or item.get("tradeDate") or "")[:10] or _today()
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": trade_date,
            "data": {"source": "eastmoney", "raw": item},
        })
    return results if results else []


def normalize_dragon_tiger_ths(raw) -> list[dict]:
    """同花顺龙虎榜 → 统一格式"""
    items_list = []
    if isinstance(raw, dict):
        items_list = raw.get("data") or raw.get("result") or []
        if isinstance(items_list, dict):
            items_list = items_list.get("data", [])
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": _today(),
            "data": {"source": "ths", "raw": item},
        })
    return results


# ==================== 聚合器 ====================


class FundFlowAggregator(BaseAggregator):
    """资金流聚合

    6 个数据源，统一采集到 ft_market_flow。
    """

    data_domain = "fund_flow"
    task_interval = 300  # 5 分钟

    def __init__(self):
        super().__init__()
        self._init_sources()
        self._exec_ddl(DDL)

    def _init_sources(self):
        from src.interfaces.api.routes import _utils

        self.sources = [
            # 北向资金 — 10 分钟
            SourceDef(
                "northbound",
                lambda cp: _utils.eastmoney.get_northbound_recent(page_size=5),
                600,
                normalize_northbound,
            ),
            # 板块资金流（新浪，含超大/大/中/小单分项）— 30 分钟
            SourceDef(
                "sector_flow_sina",
                lambda cp: _utils.sina.get_sector_money_flow(),
                1800,
                normalize_sector_flow_sina,
            ),
            # 个股主力资金 — 30 分钟（采集沪深 300 指数 000300 为例）
            SourceDef(
                "stock_flow",
                lambda cp: _utils.tencent.get_stock_fund_flow("000300"),
                1800,
                normalize_stock_flow,
            ),
            # 龙虎榜 — 东方财富，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_em",
                lambda cp: _utils.eastmoney.get_dragon_tiger(),
                21600,
                normalize_dragon_tiger_em,
            ),
            # 龙虎榜 — 同花顺，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_ths",
                lambda cp: _utils.ths.get_ths_dragon_tiger(),
                21600,
                normalize_dragon_tiger_ths,
            ),
        ]

    def _get_checkpoint(self, source_name: str):
        return None  # 资金流数据无需增量断点

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
        return self._insert_many("ft_market_flow", columns, rows)

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
            order_by="captured_at DESC",
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
