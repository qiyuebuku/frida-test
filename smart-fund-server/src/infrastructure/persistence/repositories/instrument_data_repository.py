"""Unified repository over profile, disclosure and observation fact tables."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.instrument_data_classification import instrument_data_class
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import (
    InstrumentDisclosure,
    InstrumentObservation,
    InstrumentProfile,
)


class InstrumentDataRepository:
    CHUNK_SIZE = 1000

    def save_batch(self, items: list[dict]) -> int:
        groups = {"profile": [], "disclosure": [], "observation": []}
        now = datetime.now(timezone.utc)
        for item in items:
            kind = instrument_data_class(item["data_type"])
            payload = item["data"]
            provider = _provider(item["data_type"], payload)
            business_date = _date(item.get("trade_date"), now.date())
            common = {
                "code": str(item["code"]).strip().lower(),
                "data_type": str(item["data_type"]).strip(),
                "provider": provider,
                "observed_at": now,
                "fetched_at": now,
                "data": payload,
            }
            if kind == "profile":
                groups[kind].append(common)
            else:
                common["payload_hash"] = _hash(payload)
                common[
                    "report_date" if kind == "disclosure" else "observation_date"
                ] = business_date
                groups[kind].append(common)
        return (
            self._upsert_profiles(groups["profile"])
            + self._upsert_dated(InstrumentDisclosure, groups["disclosure"], "report_date")
            + self._upsert_dated(InstrumentObservation, groups["observation"], "observation_date")
        )

    def _upsert_profiles(self, rows: list[dict]) -> int:
        return self._upsert(
            InstrumentProfile,
            rows,
            "uq_ft_instrument_profiles_identity",
            {"data", "observed_at", "fetched_at", "updated_at"},
        )

    def _upsert_dated(self, model, rows: list[dict], _date_field: str) -> int:
        constraint = (
            "uq_ft_instrument_disclosures_identity"
            if model is InstrumentDisclosure
            else "uq_ft_instrument_observations_identity"
        )
        return self._upsert(
            model,
            rows,
            constraint,
            {"data", "payload_hash", "observed_at", "fetched_at", "updated_at"},
        )

    def _upsert(self, model, rows, constraint, update_fields) -> int:
        saved = 0
        with get_session() as session:
            for index in range(0, len(rows), self.CHUNK_SIZE):
                statement = pg_insert(model).values(rows[index:index + self.CHUNK_SIZE])
                statement = statement.on_conflict_do_update(
                    constraint=constraint,
                    set_={
                        field: func.now() if field == "updated_at" else getattr(statement.excluded, field)
                        for field in update_fields
                    },
                )
                result = session.execute(statement)
                saved += result.rowcount or 0
        return saved

    def query_latest_by_codes(
        self,
        codes: list[str],
        data_types: list[str] | None = None,
        cutoff_at: datetime | None = None,
    ) -> list[dict]:
        normalized = [value for value in dict.fromkeys(str(c).strip().lower() for c in codes) if value]
        rows = self._query_all(
            normalized,
            data_types,
            latest_only=True,
            cutoff_at=cutoff_at,
        )
        return sorted(rows, key=lambda row: (row["code"], row["data_type"]))

    def query_latest(
        self,
        code: str,
        data_type: str,
        cutoff_at: datetime | None = None,
    ) -> dict | None:
        rows = self.query_latest_by_codes(
            [code],
            [data_type],
            cutoff_at=cutoff_at,
        )
        return rows[0] if rows else None

    def query_history(self, *, code: str, data_type: str, date_start: date | None = None, date_end: date | None = None, cutoff_at: datetime | None = None, limit: int = 120) -> list[dict]:
        rows = self._query_all([str(code).strip().lower()], [data_type], latest_only=False, date_start=date_start, date_end=date_end, cutoff_at=cutoff_at)
        return sorted(rows, key=lambda row: (row["trade_date"] or date.min), reverse=True)[:max(1, min(int(limit), 500))]

    def _query_all(self, codes, data_types, *, latest_only, date_start=None, date_end=None, cutoff_at=None):
        result = []
        with get_session() as session:
            for model, date_field, kind in (
                (InstrumentProfile, None, "profile"),
                (InstrumentDisclosure, InstrumentDisclosure.report_date, "disclosure"),
                (InstrumentObservation, InstrumentObservation.observation_date, "observation"),
            ):
                stmt = select(model).where(model.code.in_(codes))
                if data_types:
                    stmt = stmt.where(model.data_type.in_(data_types))
                if cutoff_at is not None:
                    stmt = stmt.where(model.fetched_at <= cutoff_at)
                if date_field is not None and date_start is not None:
                    stmt = stmt.where(date_field >= date_start)
                if date_field is not None and date_end is not None:
                    stmt = stmt.where(date_field <= date_end)
                stmt = stmt.order_by(model.updated_at.desc(), model.id.desc())
                rows = session.scalars(stmt).all()
                serialized = [_serialize(row, kind) for row in rows]
                if latest_only:
                    latest = {}
                    for row in serialized:
                        latest.setdefault((row["code"], row["data_type"]), row)
                    serialized = list(latest.values())
                result.extend(serialized)
        return result

    def delete_by_code(self, code: str) -> int:
        deleted = 0
        with get_session() as session:
            for model in (InstrumentProfile, InstrumentDisclosure, InstrumentObservation):
                result = session.execute(delete(model).where(model.code == str(code).strip().lower()))
                deleted += result.rowcount or 0
        return deleted


def _serialize(row, kind: str) -> dict:
    business_date = None
    if kind == "disclosure":
        business_date = row.report_date
    elif kind == "observation":
        business_date = row.observation_date
    return {
        "id": row.id,
        "code": row.code,
        "data_type": row.data_type,
        "data_class": kind,
        "provider": row.provider,
        "trade_date": business_date,
        "data": row.data,
        "observed_at": row.observed_at,
        "fetched_at": row.fetched_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _date(value, fallback: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return fallback


def _provider(data_type: str, payload) -> str:
    if isinstance(payload, dict) and payload.get("source"):
        return str(payload["source"])
    if data_type == "nav_sina":
        return "sina"
    if data_type in {"valuation", "guba_posts", "research"}:
        return "eastmoney"
    return "ths"


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
