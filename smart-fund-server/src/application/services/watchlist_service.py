"""Tracked-instrument watchlist management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.watchlist_instrument import normalize_instrument
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import CollectionState


AGGREGATOR = "watchlist"
MAX_ACTIVE_INSTRUMENTS = 500
DEFAULT_CONFIG = {
    "interval": 1800,
    "target_days": 10,
}


@dataclass(frozen=True)
class WatchlistMutation:
    code: str
    status: str
    instrument_type: str

    @property
    def should_collect_now(self) -> bool:
        return self.status in {"created", "reactivated"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "type": self.instrument_type,
            "collect_now": self.should_collect_now,
        }


@dataclass(frozen=True)
class WatchlistUpdateMutation:
    code: str
    updated: bool
    reactivated: bool = False
    reason: str = ""

    @property
    def should_collect_now(self) -> bool:
        return self.updated and self.reactivated

    def to_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "updated": self.updated,
            "reactivated": self.reactivated,
            "collect_now": self.should_collect_now,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass
class WatchlistItem:
    code: str
    name: str
    type: str
    source: str
    reason: str
    enabled: bool
    mode: str
    newest_time: str | None
    oldest_time: str | None
    backfill_status: str | None
    config: dict
    total_runs: int
    total_saved: int
    last_run_at: Any
    last_success_at: Any
    last_error: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "source": self.source,
            "reason": self.reason,
            "enabled": self.enabled,
            "mode": self.mode,
            "newest_time": self.newest_time,
            "oldest_time": self.oldest_time,
            "backfill_status": self.backfill_status,
            "config": self.config,
            "total_runs": self.total_runs,
            "total_saved": self.total_saved,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }


def _row_to_item(row: CollectionState) -> WatchlistItem:
    config = row.config or {}
    return WatchlistItem(
        code=row.source_name,
        name=config.get("name", ""),
        type=config.get("type", "stock"),
        source=config.get("source", "manual"),
        reason=config.get("reason", ""),
        enabled=row.enabled if row.enabled is not None else True,
        mode=row.mode or "incremental",
        newest_time=row.newest_time,
        oldest_time=row.oldest_time,
        backfill_status=row.backfill_status,
        config=config,
        total_runs=row.total_runs or 0,
        total_saved=row.total_saved or 0,
        last_run_at=row.last_run_at,
        last_success_at=row.last_success_at,
        last_error=row.last_error or "",
    )


class WatchlistService:
    def list_all(self, enabled_only: bool = False) -> list[WatchlistItem]:
        with get_session() as session:
            stmt = (
                select(CollectionState)
                .where(CollectionState.aggregator == AGGREGATOR)
                .order_by(CollectionState.source_name)
            )
            if enabled_only:
                stmt = stmt.where(CollectionState.enabled.is_(True))
            rows = session.scalars(stmt).all()
            return [_row_to_item(row) for row in rows]

    def get(self, code: str) -> WatchlistItem | None:
        normalized = str(code or "").strip().lower()
        with get_session() as session:
            row = session.scalars(
                select(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == normalized,
                )
            ).first()
            return _row_to_item(row) if row else None

    def upsert_batch(self, items: list[dict[str, Any]]) -> list[WatchlistMutation]:
        """Create, update, or reactivate tracked instruments in one statement."""

        normalized_items = self._normalize_items(items)
        if not normalized_items:
            return []

        codes = [item["code"] for item in normalized_items]
        with get_session() as session:
            existing_rows = session.scalars(
                select(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name.in_(codes),
                )
            ).all()
            existing = {row.source_name: row for row in existing_rows}
            active_count = session.scalar(
                select(func.count(CollectionState.id)).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.enabled.is_(True),
                )
            ) or 0
            additions = sum(
                1
                for item in normalized_items
                if item["code"] not in existing
                or existing[item["code"]].enabled is False
            )
            if active_count + additions > MAX_ACTIVE_INSTRUMENTS:
                raise ValueError(
                    f"启用标的将超过上限 {MAX_ACTIVE_INSTRUMENTS}，请先停用不再跟踪的标的"
                )

            values: list[dict[str, Any]] = []
            mutations: list[WatchlistMutation] = []
            for item in normalized_items:
                old = existing.get(item["code"])
                status = _mutation_status(old, item["config"], item["interval"])
                mutations.append(
                    WatchlistMutation(
                        code=item["code"],
                        status=status,
                        instrument_type=item["config"]["type"],
                    )
                )
                values.append(
                    {
                        "aggregator": AGGREGATOR,
                        "source_name": item["code"],
                        "mode": "backfill",
                        "target_time": item["target_time"],
                        "enabled": True,
                        "interval_override": item["interval"],
                        "config": item["config"],
                    }
                )

            stmt = pg_insert(CollectionState).values(values)
            was_disabled = CollectionState.enabled.is_(False)
            stmt = stmt.on_conflict_do_update(
                index_elements=["aggregator", "source_name"],
                set_={
                    "config": stmt.excluded.config,
                    "enabled": True,
                    "interval_override": stmt.excluded.interval_override,
                    "mode": case(
                        (was_disabled, stmt.excluded.mode),
                        else_=CollectionState.mode,
                    ),
                    "target_time": case(
                        (was_disabled, stmt.excluded.target_time),
                        else_=CollectionState.target_time,
                    ),
                    "backfill_status": case(
                        (was_disabled, None),
                        else_=CollectionState.backfill_status,
                    ),
                    "last_run_at": case(
                        (was_disabled, None),
                        else_=CollectionState.last_run_at,
                    ),
                    "last_error": case(
                        (was_disabled, ""),
                        else_=CollectionState.last_error,
                    ),
                    "consecutive_failures": case(
                        (was_disabled, 0),
                        else_=CollectionState.consecutive_failures,
                    ),
                    "updated_at": func.now(),
                },
            )
            session.execute(stmt)
            return mutations

    def add(
        self,
        code: str,
        name: str = "",
        type: str = "stock",
        source: str = "manual",
        target_days: int | None = None,
        interval: int | None = None,
        reason: str = "",
    ) -> bool:
        """Compatibility wrapper. True means a new or reactivated instrument."""

        mutation = self.upsert_batch(
            [
                {
                    "code": code,
                    "name": name,
                    "type": type,
                    "source": source,
                    "target_days": target_days,
                    "interval": interval,
                    "reason": reason,
                }
            ]
        )[0]
        return mutation.status in {"created", "reactivated"}

    def add_batch(self, items: list[dict]) -> int:
        return sum(
            mutation.status in {"created", "reactivated"}
            for mutation in self.upsert_batch(items)
        )

    def remove(self, code: str) -> bool:
        normalized = str(code or "").strip().lower()
        with get_session() as session:
            result = session.execute(
                delete(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == normalized,
                )
            )
            return (result.rowcount or 0) > 0

    def update(self, code: str, **kwargs) -> bool:
        result = self.update_batch([{"code": code, **kwargs}])[0]
        return result.updated

    def update_batch(
        self,
        updates: list[dict[str, Any]],
    ) -> list[WatchlistUpdateMutation]:
        """Update tracked instruments with one multi-row upsert."""

        if not updates:
            return []
        requested: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for raw in updates:
            code = str(raw.get("code") or "").strip().lower()
            if not code:
                raise ValueError("update.code 不能为空")
            if code in seen:
                continue
            seen.add(code)
            requested.append(
                (
                    code,
                    {
                        key: value
                        for key, value in raw.items()
                        if key != "code" and value is not None
                    },
                )
            )

        with get_session() as session:
            rows = session.scalars(
                select(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name.in_(
                        [code for code, _kwargs in requested]
                    ),
                )
            ).all()
            existing = {row.source_name: row for row in rows}
            values: list[dict[str, Any]] = []
            results: list[WatchlistUpdateMutation] = []
            for code, kwargs in requested:
                row = existing.get(code)
                if row is None:
                    results.append(
                        WatchlistUpdateMutation(
                            code=code,
                            updated=False,
                            reason="not_found",
                        )
                    )
                    continue

                config = dict(row.config or {})
                self._validate_update(row=row, config=config, kwargs=kwargs)
                for key in (
                    "name",
                    "interval",
                    "target_days",
                    "source",
                    "reason",
                ):
                    if key in kwargs:
                        config[key] = kwargs[key]
                if "type" in kwargs:
                    identity = normalize_instrument(
                        code=code,
                        instrument_type=kwargs["type"],
                        name=str(config.get("name") or ""),
                    )
                    config["type"] = identity.instrument_type
                    config["exchange_traded"] = identity.exchange_traded

                enabled = (
                    bool(kwargs["enabled"])
                    if "enabled" in kwargs
                    else bool(row.enabled)
                )
                reactivated = bool(enabled and row.enabled is False)
                interval = _positive_int(
                    config.get("interval", DEFAULT_CONFIG["interval"]),
                    "interval",
                    minimum=300,
                )
                target_days = _positive_int(
                    config.get("target_days", DEFAULT_CONFIG["target_days"]),
                    "target_days",
                )
                config["interval"] = interval
                config["target_days"] = target_days
                values.append(
                    {
                        "aggregator": AGGREGATOR,
                        "source_name": code,
                        "config": config,
                        "enabled": enabled,
                        "interval_override": interval,
                        "mode": "backfill" if reactivated else row.mode,
                        "target_time": (
                            (date.today() - timedelta(days=target_days)).isoformat()
                            if reactivated
                            else row.target_time
                        ),
                        "newest_time": row.newest_time,
                        "oldest_time": row.oldest_time,
                        "backfill_status": (
                            None if reactivated else row.backfill_status
                        ),
                        "cursor": None if reactivated else row.cursor,
                        "last_run_at": None if reactivated else row.last_run_at,
                        "last_success_at": row.last_success_at,
                        "last_error": "" if reactivated else row.last_error,
                        "consecutive_failures": (
                            0 if reactivated else row.consecutive_failures
                        ),
                        "total_runs": row.total_runs,
                        "total_saved": row.total_saved,
                    }
                )
                results.append(
                    WatchlistUpdateMutation(
                        code=code,
                        updated=True,
                        reactivated=reactivated,
                    )
                )

            if values:
                stmt = pg_insert(CollectionState).values(values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["aggregator", "source_name"],
                    set_={
                        "config": stmt.excluded.config,
                        "enabled": stmt.excluded.enabled,
                        "interval_override": stmt.excluded.interval_override,
                        "mode": stmt.excluded.mode,
                        "target_time": stmt.excluded.target_time,
                        "newest_time": stmt.excluded.newest_time,
                        "oldest_time": stmt.excluded.oldest_time,
                        "backfill_status": stmt.excluded.backfill_status,
                        "cursor": stmt.excluded.cursor,
                        "last_run_at": stmt.excluded.last_run_at,
                        "last_success_at": stmt.excluded.last_success_at,
                        "last_error": stmt.excluded.last_error,
                        "consecutive_failures": stmt.excluded.consecutive_failures,
                        "total_runs": stmt.excluded.total_runs,
                        "total_saved": stmt.excluded.total_saved,
                        "updated_at": func.now(),
                    },
                )
                session.execute(stmt)
            return results

    def remove_batch(self, codes: list[str]) -> list[str]:
        normalized = [
            code
            for code in dict.fromkeys(
                str(code).strip().lower() for code in codes
            )
            if code
        ]
        if not normalized:
            return []
        with get_session() as session:
            existing = session.scalars(
                select(CollectionState.source_name).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name.in_(normalized),
                )
            ).all()
            session.execute(
                delete(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name.in_(normalized),
                )
            )
            return list(existing)

    def sync_from_positions_batch(self) -> list[WatchlistMutation]:
        from src.infrastructure.persistence.models.trading import Position

        with get_session() as session:
            positions = session.scalars(
                select(Position).where(Position.shares > 0)
            ).all()
        mutations = self.upsert_batch(
            [
                {
                    "code": position.fund_code,
                    "name": position.fund_name or "",
                    "type": "fund",
                    "source": "position",
                    "reason": "当前持仓需要持续跟踪",
                }
                for position in positions
            ]
        )
        return mutations

    def sync_from_positions(self) -> int:
        return sum(
            item.should_collect_now for item in self.sync_from_positions_batch()
        )

    @staticmethod
    def _validate_update(
        *,
        row: CollectionState,
        config: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> None:
        if "interval" in kwargs:
            _positive_int(kwargs["interval"], "interval", minimum=300)
        if "target_days" in kwargs:
            _positive_int(kwargs["target_days"], "target_days")
        if "source" in kwargs:
            source = str(kwargs["source"]).strip().lower()
            if source not in {"manual", "position", "event", "agent"}:
                raise ValueError(
                    "source 必须是 manual、position、event 或 agent"
                )
            kwargs["source"] = source
        if "type" in kwargs:
            identity = normalize_instrument(
                code=row.source_name,
                instrument_type=str(kwargs["type"]),
                name=str(kwargs.get("name", config.get("name", ""))),
            )
            if identity.code != row.source_name:
                raise ValueError("更新 type 后的规范化代码与现有标的不一致")

    def _normalize_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in items:
            identity = normalize_instrument(
                code=raw.get("code", ""),
                instrument_type=raw.get("type", "auto"),
                name=raw.get("name", ""),
            )
            if identity.code in seen:
                continue
            seen.add(identity.code)
            target_days = _positive_int(
                raw.get("target_days")
                if raw.get("target_days") is not None
                else DEFAULT_CONFIG["target_days"],
                "target_days",
            )
            interval = _positive_int(
                raw.get("interval")
                if raw.get("interval") is not None
                else DEFAULT_CONFIG["interval"],
                "interval",
                minimum=300,
            )
            source = str(raw.get("source") or "manual").strip().lower()
            if source not in {"manual", "position", "event", "agent"}:
                raise ValueError("source 必须是 manual、position、event 或 agent")
            config = {
                "name": identity.name,
                "type": identity.instrument_type,
                "source": source,
                "reason": str(raw.get("reason") or "").strip(),
                "interval": interval,
                "target_days": target_days,
                "exchange_traded": identity.exchange_traded,
            }
            result.append(
                {
                    "code": identity.code,
                    "config": config,
                    "interval": interval,
                    "target_time": (
                        date.today() - timedelta(days=target_days)
                    ).isoformat(),
                }
            )
        return result


def _mutation_status(
    row: CollectionState | None,
    config: dict[str, Any],
    interval: int,
) -> str:
    if row is None:
        return "created"
    if row.enabled is False:
        return "reactivated"
    if dict(row.config or {}) != config or row.interval_override != interval:
        return "updated"
    return "unchanged"


def _positive_int(value: Any, field: str, *, minimum: int = 1) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if result < minimum:
        raise ValueError(f"{field} 必须大于或等于 {minimum}")
    return result
