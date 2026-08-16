"""券商账户投影服务 — Research Exposure Pack 的权威数据源。

按需直调（2026-08-17 用户决策）：不挂调度、不建快照表，每次工具调用
直接经 THSTradeClient 请求真机端点（positions 1891 + funds 1807），
在内存中组装暴露摘要与单标的持仓视图。

失败降级：设备端不可达 / 预热未完成 / 登录门未过时返回 unavailable
（携带原因码），绝不用市场数据伪造持仓——与 Research Agent 的
"不伪造事实"原则一致（替换此前的静态 exposure_unavailable）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.infrastructure.clients.ths_trade import (
    THSTradeClient,
    THSTradeError,
    parse_money,
)

logger = logging.getLogger(__name__)

_UNAVAILABLE_REASONS = {
    "trade_endpoint_unreachable": "trade_device_unreachable",
    "trade_runtime_not_ready": "trade_runtime_warming_up",
    "trade_account_not_logged_in": "trade_account_not_logged_in",
    "trade_account_not_recovered": "trade_account_not_recovered",
    "trade_response_timeout": "trade_response_timeout",
}


def _unavailable(
    *,
    operation: str,
    cutoff_at: datetime,
    account_scope: list[str],
    reason_code: str,
    reason: str,
    instrument_id: str = "",
) -> dict[str, Any]:
    return {
        "operation": operation,
        "status": "unavailable",
        "cutoff_at": cutoff_at.isoformat(),
        "account_scope": account_scope,
        "instrument_id": instrument_id or None,
        "reason_code": reason_code,
        "reason": reason,
    }


class TradeAccountProjectionService:
    """账户暴露投影（持仓 + 资金 → 暴露摘要 / 单标的视图）。"""

    def __init__(self, client: THSTradeClient | None = None) -> None:
        self._client = client or THSTradeClient.shared()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _open_snapshot(
        self,
        *,
        operation: str,
        cutoff_at: datetime,
        account_scope: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, Any]:
        """一次取 positions + funds（各走各的短 TTL 缓存）。失败返回 unavailable。"""
        try:
            positions_payload = self._client.positions()
            funds_payload = self._client.funds()
        except THSTradeError as exc:
            logger.warning("trade projection %s unavailable: %s", operation, exc)
            return _unavailable(
                operation=operation,
                cutoff_at=cutoff_at,
                account_scope=account_scope,
                reason_code=_UNAVAILABLE_REASONS.get(
                    exc.reason_code, "trade_endpoint_error"
                ),
                reason=f"券商账户端点当前不可用：{exc}",
            )
        return positions_payload, funds_payload

    @staticmethod
    def _position_rows(positions_payload: dict[str, Any]) -> list[dict[str, Any]]:
        """持仓行提取：优先 records（键序已校准的语义对象），否则 rows+columns。"""
        data = positions_payload.get("data") or {}
        records = data.get("records")
        if isinstance(records, list):
            return [dict(item) for item in records if isinstance(item, dict)]
        rows = data.get("rows")
        columns = data.get("columns")
        if isinstance(rows, list) and isinstance(columns, list):
            return [
                dict(zip(columns, row)) if isinstance(row, list) else dict(row)
                for row in rows
            ]
        return []

    @staticmethod
    def _funds_view(funds_payload: dict[str, Any]) -> dict[str, Any]:
        data = funds_payload.get("data") or {}
        fields = data.get("fields") or {}
        return {
            key: parse_money(fields.get(key))
            for key in (
                "total_assets",
                "float_profit",
                "available_amount",
                "total_market_value",
                "withdrawable_amount",
            )
            if key in fields or fields
        }

    # ------------------------------------------------------------------
    # 公开投影（Research Exposure Pack 契约）
    # ------------------------------------------------------------------

    def exposure_summary(
        self,
        *,
        cutoff_at: datetime,
        account_ids: tuple[str, ...] | list[str],
    ) -> dict[str, Any]:
        """账户暴露摘要（资金字段 + 持仓行 + 聚合数字）。"""
        account_scope = list(account_ids)
        snapshot = self._open_snapshot(
            operation="research_exposure_summary_open",
            cutoff_at=cutoff_at,
            account_scope=account_scope,
        )
        if not isinstance(snapshot, tuple):
            return snapshot
        positions_payload, funds_payload = snapshot
        positions = self._position_rows(positions_payload)
        funds_view = self._funds_view(funds_payload)
        market_value = funds_view.get("total_market_value")
        total_assets = funds_view.get("total_assets")
        return {
            "operation": "research_exposure_summary_open",
            "status": "available",
            "cutoff_at": cutoff_at.isoformat(),
            "account_scope": account_scope,
            "funds": funds_view,
            "position_count": len(positions),
            "positions": positions,
            "summary": {
                "total_assets": total_assets,
                "total_market_value": market_value,
                "float_profit": funds_view.get("float_profit"),
                "available_amount": funds_view.get("available_amount"),
                "position_count": len(positions),
                "cash_ratio": (
                    None
                    if not total_assets
                    else round(1 - (market_value or 0.0) / total_assets, 4)
                ),
            },
            "source": {
                "provider": "ths_trade_sdk",
                "protocols": {"funds": 1807, "positions": 1891},
                "positions_elapsed_ms": positions_payload.get("elapsed_ms"),
                "funds_elapsed_ms": funds_payload.get("elapsed_ms"),
                "positions_from_cache": positions_payload.get("from_cache", False),
                "funds_from_cache": funds_payload.get("from_cache", False),
            },
        }

    def position_open(
        self,
        *,
        cutoff_at: datetime,
        account_ids: tuple[str, ...] | list[str],
        instrument_id: str,
    ) -> dict[str, Any]:
        """单标的持仓视图（按 代码/名称 字段过滤持仓行）。"""
        account_scope = list(account_ids)
        snapshot = self._open_snapshot(
            operation="research_position_open",
            cutoff_at=cutoff_at,
            account_scope=account_scope,
        )
        if not isinstance(snapshot, tuple):
            return snapshot
        positions_payload, funds_payload = snapshot
        positions = self._position_rows(positions_payload)
        instrument_id = (instrument_id or "").strip()
        matched = [
            row
            for row in positions
            if instrument_id
            and (
                instrument_id in (str(row.get("代码", "")), str(row.get("code", "")))
                or instrument_id in str(row.get("名称", ""))
            )
        ]
        if not matched:
            return {
                "operation": "research_position_open",
                "status": "not_found",
                "cutoff_at": cutoff_at.isoformat(),
                "account_scope": account_scope,
                "instrument_id": instrument_id,
                "position_count": len(positions),
                "reason": "当前账户无该标的持仓（或持仓为空）。",
            }
        return {
            "operation": "research_position_open",
            "status": "available",
            "cutoff_at": cutoff_at.isoformat(),
            "account_scope": account_scope,
            "instrument_id": instrument_id,
            "positions": matched,
            "funds": self._funds_view(funds_payload),
            "source": {
                "provider": "ths_trade_sdk",
                "protocols": {"funds": 1807, "positions": 1891},
            },
        }

    def position_performance(
        self,
        *,
        cutoff_at: datetime,
        account_ids: tuple[str, ...] | list[str],
        instrument_id: str,
    ) -> dict[str, Any]:
        """单标的持仓表现（盈亏/盈亏率/市值，来自持仓行现成字段）。"""
        account_scope = list(account_ids)
        snapshot = self._open_snapshot(
            operation="research_position_performance_open",
            cutoff_at=cutoff_at,
            account_scope=account_scope,
        )
        if not isinstance(snapshot, tuple):
            return snapshot
        positions_payload, _funds_payload = snapshot
        positions = self._position_rows(positions_payload)
        instrument_id = (instrument_id or "").strip()
        matched = [
            row
            for row in positions
            if instrument_id
            and (
                instrument_id in (str(row.get("代码", "")), str(row.get("code", "")))
                or instrument_id in str(row.get("名称", ""))
            )
        ]
        if not matched:
            return {
                "operation": "research_position_performance_open",
                "status": "not_found",
                "cutoff_at": cutoff_at.isoformat(),
                "account_scope": account_scope,
                "instrument_id": instrument_id,
                "position_count": len(positions),
                "reason": "当前账户无该标的持仓，无权威盈亏数据。",
            }
        performance = []
        for row in matched:
            performance.append(
                {
                    "code": row.get("代码", row.get("code")),
                    "name": row.get("名称", row.get("name")),
                    "market_value": parse_money(row.get("市值")),
                    "float_profit": parse_money(row.get("盈亏")),
                    "profit_ratio": parse_money(row.get("盈亏率")),
                    "cost": parse_money(row.get("成本")),
                    "current_price": parse_money(row.get("现价")),
                    "qty": parse_money(row.get("数量")),
                }
            )
        return {
            "operation": "research_position_performance_open",
            "status": "available",
            "cutoff_at": cutoff_at.isoformat(),
            "account_scope": account_scope,
            "instrument_id": instrument_id,
            "performance": performance,
            "source": {
                "provider": "ths_trade_sdk",
                "protocols": {"positions": 1891},
                "note": "字段来自券商持仓表（名称/盈亏/市值/盈亏率/成本/现价/数量）。",
            },
        }
