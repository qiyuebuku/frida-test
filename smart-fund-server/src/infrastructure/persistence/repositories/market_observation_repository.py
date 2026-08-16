"""Market observation persistence."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import (
    CollectionRun,
    EtfDailyShare,
    MarketSnapshot,
)


def _snapshot_order_key(item: dict[str, Any]) -> tuple[int, datetime, datetime]:
    """Order colliding frames so delayed callbacks cannot win a bucket."""
    minimum = datetime.min.replace(tzinfo=item["fetched_at"].tzinfo)
    observed_at = item.get("observed_at")
    return (
        1 if observed_at is not None else 0,
        observed_at or minimum,
        item["fetched_at"],
    )


_UNCHANGED_PAYLOAD_DEDUP_DATA_TYPES = frozenset(
    {
        "stock_dynamic_group",
        "ths_industry_opportunity",
        "ths_sector_commodity_linkage",
        "ths_etf_home_ranking",
        "ths_etf_zone",
        "ths_etf_ranking_universe",
        "ths_etf_cross_border",
        "ths_etf_hot_ranking",
    }
)


def _drop_consecutive_unchanged_snapshots(
    items: list[dict[str, Any]],
    latest_payloads: dict[tuple[str, str, str], tuple[date | None, str]],
) -> list[dict[str, Any]]:
    """Keep a new bucket only when its business payload actually changes.

    This is deliberately limited to A-share app-page snapshots. Other feeds
    may use repeated payloads as heartbeats or have cross-market calendars.
    """
    kept: list[dict[str, Any]] = []
    ordered = sorted(
        items,
        key=lambda item: (
            item["data_type"],
            item["subject_id"],
            item["provider"],
            item["bucket_at"],
        ),
    )
    for item in ordered:
        if item["data_type"] not in _UNCHANGED_PAYLOAD_DEDUP_DATA_TYPES:
            kept.append(item)
            continue
        key = (item["data_type"], item["subject_id"], item["provider"])
        current = (item.get("trade_date"), item["payload_hash"])
        if latest_payloads.get(key) == current:
            continue
        kept.append(item)
        latest_payloads[key] = current
    return kept


class MarketSnapshotRepository:
    CHUNK_SIZE = 1000

    def upsert_batch(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        unique_items: dict[tuple[object, ...], dict[str, Any]] = {}
        for item in items:
            key = (
                item["data_type"],
                item["subject_id"],
                item["provider"],
                item["bucket_at"],
            )
            current = unique_items.get(key)
            if current is None or _snapshot_order_key(item) >= _snapshot_order_key(
                current
            ):
                unique_items[key] = item
        values_to_save = list(unique_items.values())
        saved = 0
        with get_session() as session:
            dedup_candidates = [
                item
                for item in values_to_save
                if item["data_type"] in _UNCHANGED_PAYLOAD_DEDUP_DATA_TYPES
            ]
            latest_payloads: dict[
                tuple[str, str, str], tuple[date | None, str]
            ] = {}
            for start in range(0, len(dedup_candidates), self.CHUNK_SIZE):
                chunk = dedup_candidates[start : start + self.CHUNK_SIZE]
                keys = {
                    (item["data_type"], item["subject_id"], item["provider"])
                    for item in chunk
                }
                statement = (
                    select(
                        MarketSnapshot.data_type,
                        MarketSnapshot.subject_id,
                        MarketSnapshot.provider,
                        MarketSnapshot.trade_date,
                        MarketSnapshot.payload_hash,
                    )
                    .where(
                        tuple_(
                            MarketSnapshot.data_type,
                            MarketSnapshot.subject_id,
                            MarketSnapshot.provider,
                        ).in_(keys)
                    )
                    .order_by(
                        MarketSnapshot.data_type,
                        MarketSnapshot.subject_id,
                        MarketSnapshot.provider,
                        MarketSnapshot.bucket_at.desc(),
                    )
                    .distinct(
                        MarketSnapshot.data_type,
                        MarketSnapshot.subject_id,
                        MarketSnapshot.provider,
                    )
                )
                for row in session.execute(statement):
                    latest_payloads[(row[0], row[1], row[2])] = (row[3], row[4])
            values_to_save = _drop_consecutive_unchanged_snapshots(
                values_to_save,
                latest_payloads,
            )
            for start in range(0, len(values_to_save), self.CHUNK_SIZE):
                values = values_to_save[start : start + self.CHUNK_SIZE]
                statement = pg_insert(MarketSnapshot).values(values)
                statement = statement.on_conflict_do_update(
                    constraint="uq_ft_market_snapshots_bucket",
                    set_={
                        "subject_type": statement.excluded.subject_type,
                        "market": statement.excluded.market,
                        "trade_date": statement.excluded.trade_date,
                        "observed_at": statement.excluded.observed_at,
                        "fetched_at": statement.excluded.fetched_at,
                        "freshness_status": statement.excluded.freshness_status,
                        "source_latency_seconds": (
                            statement.excluded.source_latency_seconds
                        ),
                        "payload_hash": statement.excluded.payload_hash,
                        "data": statement.excluded.data,
                        "updated_at": func.now(),
                    },
                    where=or_(
                        and_(
                            statement.excluded.observed_at.is_not(None),
                            or_(
                                MarketSnapshot.observed_at.is_(None),
                                statement.excluded.observed_at
                                > MarketSnapshot.observed_at,
                                and_(
                                    statement.excluded.observed_at
                                    == MarketSnapshot.observed_at,
                                    statement.excluded.fetched_at
                                    >= MarketSnapshot.fetched_at,
                                ),
                            ),
                        ),
                        and_(
                            statement.excluded.observed_at.is_(None),
                            MarketSnapshot.observed_at.is_(None),
                            statement.excluded.fetched_at
                            >= MarketSnapshot.fetched_at,
                        ),
                    ),
                )
                result = session.execute(statement)
                saved += result.rowcount or 0
        return saved
    def query_latest(
        self,
        *,
        subject_ids: list[str],
        data_types: list[str] | None = None,
        cutoff_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not subject_ids:
            return []
        with get_session() as session:
            statement = (
                select(MarketSnapshot)
                .where(MarketSnapshot.subject_id.in_(subject_ids))
                .order_by(
                    MarketSnapshot.subject_id,
                    MarketSnapshot.data_type,
                    MarketSnapshot.bucket_at.desc(),
                )
                .distinct(
                    MarketSnapshot.subject_id,
                    MarketSnapshot.data_type,
                )
            )
            if data_types:
                statement = statement.where(
                    MarketSnapshot.data_type.in_(data_types)
                )
            if cutoff_at is not None:
                statement = statement.where(
                    MarketSnapshot.bucket_at <= cutoff_at
                )
            return [
                self._snapshot_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    def list_latest(
        self,
        *,
        data_types: list[str] | None = None,
        subject_type: str | None = None,
        cutoff_at: datetime | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Return the latest observation for every source subject.

        ``cutoff_at`` is the business-time boundary used by Agent replay and
        decision runs.  Applying it in SQL prevents a historical run from
        seeing a snapshot that was persisted later.
        """

        with get_session() as session:
            statement = (
                select(MarketSnapshot)
                .order_by(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.provider,
                    MarketSnapshot.bucket_at.desc(),
                )
                .distinct(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.provider,
                )
            )
            if data_types:
                statement = statement.where(
                    MarketSnapshot.data_type.in_(data_types)
                )
            if subject_type:
                statement = statement.where(
                    MarketSnapshot.subject_type == subject_type
                )
            if cutoff_at is not None:
                statement = statement.where(
                    MarketSnapshot.bucket_at <= cutoff_at
                )
            statement = statement.limit(max(1, min(int(limit), 10000)))
            return [
                self._snapshot_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    def list_latest_metadata(
        self,
        *,
        cutoff_at: datetime,
        data_types: list[str] | None = None,
        limit: int = 20_000,
    ) -> list[dict[str, Any]]:
        """Return latest snapshot identities without loading provider JSON."""

        with get_session() as session:
            statement = (
                select(
                    MarketSnapshot.id,
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.market,
                    MarketSnapshot.provider,
                    MarketSnapshot.trade_date,
                    MarketSnapshot.observed_at,
                    MarketSnapshot.fetched_at,
                    MarketSnapshot.bucket_at,
                    MarketSnapshot.freshness_status,
                    MarketSnapshot.source_latency_seconds,
                )
                .where(MarketSnapshot.bucket_at <= cutoff_at)
                .order_by(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.provider,
                    MarketSnapshot.bucket_at.desc(),
                )
                .distinct(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.provider,
                )
            )
            if data_types:
                statement = statement.where(
                    MarketSnapshot.data_type.in_(data_types)
                )
            rows = session.execute(
                statement.limit(max(1, min(int(limit), 50_000)))
            ).mappings()
            return [dict(row) for row in rows]

    def list_latest_for_agent(
        self,
        *,
        cutoff_at: datetime,
        data_types: list[str],
        limit: int = 40,
    ) -> dict[str, Any]:
        """Read the newest bounded facts after database-side deduplication."""

        if not data_types:
            return {"total": 0, "items": []}
        normalized_limit = max(1, min(int(limit), 100))
        latest = (
            select(
                MarketSnapshot.id.label("snapshot_id"),
                MarketSnapshot.bucket_at.label("bucket_at"),
            )
            .where(
                MarketSnapshot.bucket_at <= cutoff_at,
                MarketSnapshot.data_type.in_(data_types),
            )
            .order_by(
                MarketSnapshot.data_type,
                MarketSnapshot.subject_id,
                MarketSnapshot.provider,
                MarketSnapshot.bucket_at.desc(),
            )
            .distinct(
                MarketSnapshot.data_type,
                MarketSnapshot.subject_id,
                MarketSnapshot.provider,
            )
            .subquery()
        )
        with get_session() as session:
            total = int(
                session.scalar(select(func.count()).select_from(latest)) or 0
            )
            selected_ids = list(
                session.scalars(
                    select(latest.c.snapshot_id)
                    .order_by(latest.c.bucket_at.desc())
                    .limit(normalized_limit)
                ).all()
            )
            if not selected_ids:
                return {"total": total, "items": []}
            rows = session.scalars(
                select(MarketSnapshot)
                .where(MarketSnapshot.id.in_(selected_ids))
                .order_by(MarketSnapshot.bucket_at.desc())
            ).all()
            return {
                "total": total,
                "items": [self._snapshot_to_dict(row) for row in rows],
            }

    def list_latest_per_data_type_for_agent(
        self,
        *,
        cutoff_at: datetime,
        data_types: list[str],
        per_data_type_limit: int = 1,
    ) -> dict[str, Any]:
        """Return a bounded representative sample without source starvation.

        A newest-N query can be filled by a high-frequency feed and hide every
        slower data family in the same research dimension.  Rank after the
        normal subject/provider deduplication so each requested data type gets
        an equal opportunity to appear in an Agent navigation response.
        """

        ordered_types = list(dict.fromkeys(str(item) for item in data_types if item))
        if not ordered_types:
            return {"total": 0, "items": [], "counts_by_data_type": {}}
        normalized_per_type = max(1, min(int(per_data_type_limit), 5))
        latest = (
            select(
                MarketSnapshot.id.label("snapshot_id"),
                MarketSnapshot.data_type.label("data_type"),
                MarketSnapshot.bucket_at.label("bucket_at"),
            )
            .where(
                MarketSnapshot.bucket_at <= cutoff_at,
                MarketSnapshot.data_type.in_(ordered_types),
            )
            .order_by(
                MarketSnapshot.data_type,
                MarketSnapshot.subject_id,
                MarketSnapshot.provider,
                MarketSnapshot.bucket_at.desc(),
            )
            .distinct(
                MarketSnapshot.data_type,
                MarketSnapshot.subject_id,
                MarketSnapshot.provider,
            )
            .subquery()
        )
        ranked = select(
            latest.c.snapshot_id,
            latest.c.data_type,
            latest.c.bucket_at,
            func.row_number()
            .over(
                partition_by=latest.c.data_type,
                order_by=latest.c.bucket_at.desc(),
            )
            .label("type_rank"),
            func.count()
            .over(partition_by=latest.c.data_type)
            .label("type_total"),
        ).subquery()
        with get_session() as session:
            selected = list(
                session.execute(
                    select(
                        ranked.c.snapshot_id,
                        ranked.c.data_type,
                        ranked.c.type_total,
                    ).where(ranked.c.type_rank <= normalized_per_type)
                ).all()
            )
            counts = {str(row.data_type): int(row.type_total) for row in selected}
            selected_ids = [int(row.snapshot_id) for row in selected]
            if not selected_ids:
                return {"total": 0, "items": [], "counts_by_data_type": {}}
            snapshots = {
                row.id: row
                for row in session.scalars(
                    select(MarketSnapshot).where(MarketSnapshot.id.in_(selected_ids))
                ).all()
            }
            type_order = {data_type: index for index, data_type in enumerate(ordered_types)}
            items = [
                self._snapshot_to_dict(snapshots[snapshot_id])
                for snapshot_id in selected_ids
                if snapshot_id in snapshots
            ]
            items.sort(
                key=lambda item: type_order.get(
                    str(item.get("data_type")), len(type_order)
                )
            )
            return {
                "total": sum(counts.values()),
                "items": items,
                "counts_by_data_type": counts,
            }

    def query_latest_series_at(
        self,
        *,
        series: list[tuple[str, str]],
        cutoff_at: datetime,
        available_at: datetime | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Read the latest known fact per exact data-type/subject series.

        ``cutoff_at`` limits the market fact time. ``available_at`` separately
        limits when that fact entered this system, preventing look-ahead during
        replay while still allowing a current run to compare with a late-arriving
        prior-close record.
        """

        unique_series = list(dict.fromkeys(series))
        if not unique_series:
            return {}
        filters = [
            and_(
                MarketSnapshot.data_type == data_type,
                MarketSnapshot.subject_id == subject_id,
            )
            for data_type, subject_id in unique_series
        ]
        fact_time = func.coalesce(
            MarketSnapshot.observed_at,
            MarketSnapshot.bucket_at,
        )
        availability_filters = (
            [MarketSnapshot.fetched_at <= available_at]
            if available_at is not None
            else []
        )
        with get_session() as session:
            rows = session.scalars(
                select(MarketSnapshot)
                .where(
                    fact_time <= cutoff_at,
                    *availability_filters,
                    or_(*filters),
                )
                .order_by(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    fact_time.desc(),
                    MarketSnapshot.fetched_at.desc(),
                )
                .distinct(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                )
            ).all()
            return {
                (row.data_type, row.subject_id): self._snapshot_to_dict(row)
                for row in rows
            }

    def get_by_id_at(
        self,
        *,
        snapshot_id: int,
        cutoff_at: datetime,
    ) -> dict[str, Any] | None:
        """Open one exact persisted snapshot without crossing the run cutoff."""

        with get_session() as session:
            row = session.scalar(
                select(MarketSnapshot).where(
                    MarketSnapshot.id == int(snapshot_id),
                    MarketSnapshot.bucket_at <= cutoff_at,
                )
            )
            return self._snapshot_to_dict(row) if row is not None else None

    def summarize_since(self, since: datetime) -> dict[str, Any]:
        """Aggregate snapshot volume and freshness for observability."""

        with get_session() as session:
            base_filter = MarketSnapshot.bucket_at >= since
            total = session.scalar(
                select(func.count(MarketSnapshot.id)).where(base_filter)
            ) or 0
            subject_count = session.scalar(
                select(func.count(func.distinct(MarketSnapshot.subject_id))).where(
                    base_filter
                )
            ) or 0
            latest_bucket_at = session.scalar(
                select(func.max(MarketSnapshot.bucket_at)).where(base_filter)
            )
            type_rows = session.execute(
                select(
                    MarketSnapshot.data_type,
                    func.count(MarketSnapshot.id),
                    func.max(MarketSnapshot.bucket_at),
                )
                .where(base_filter)
                .group_by(MarketSnapshot.data_type)
                .order_by(func.count(MarketSnapshot.id).desc())
            ).all()
            freshness_rows = session.execute(
                select(
                    MarketSnapshot.freshness_status,
                    func.count(MarketSnapshot.id),
                )
                .where(base_filter)
                .group_by(MarketSnapshot.freshness_status)
            ).all()
            return {
                "total": int(total),
                "subject_count": int(subject_count),
                "latest_bucket_at": latest_bucket_at,
                "by_data_type": [
                    {
                        "data_type": data_type,
                        "count": int(count or 0),
                        "latest_bucket_at": latest,
                    }
                    for data_type, count, latest in type_rows
                ],
                "by_freshness": {
                    str(status or "unknown"): int(count or 0)
                    for status, count in freshness_rows
                },
            }

    def query_history(
        self,
        *,
        subject_id: str,
        data_type: str,
        date_start: date | None = None,
        date_end: date | None = None,
        cutoff_at: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with get_session() as session:
            statement = select(MarketSnapshot).where(
                MarketSnapshot.subject_id == subject_id,
                MarketSnapshot.data_type == data_type,
            )
            if date_start:
                statement = statement.where(
                    MarketSnapshot.trade_date >= date_start
                )
            if date_end:
                statement = statement.where(
                    MarketSnapshot.trade_date <= date_end
                )
            if cutoff_at is not None:
                statement = statement.where(
                    MarketSnapshot.bucket_at <= cutoff_at
                )
            statement = statement.order_by(
                MarketSnapshot.bucket_at.desc()
            ).limit(max(1, min(int(limit), 5000)))
            return [
                self._snapshot_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    def query_histories(
        self,
        *,
        series: list[tuple[str, str]],
        date_windows: dict[
            tuple[str, str], tuple[date | None, date | None]
        ] | None = None,
        cutoff_at: datetime | None = None,
        limit_per_series: int = 600,
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Read several independent histories with one database round trip."""

        if not series:
            return {}
        unique_series = list(dict.fromkeys(series))
        normalized_limit = max(1, min(int(limit_per_series), 2000))
        windows = date_windows or {}
        filters = []
        for key in unique_series:
            data_type, subject_id = key
            conditions = [
                MarketSnapshot.data_type == data_type,
                MarketSnapshot.subject_id == subject_id,
            ]
            date_start, date_end = windows.get(key, (None, None))
            if date_start is not None:
                conditions.append(MarketSnapshot.trade_date >= date_start)
            if date_end is not None:
                conditions.append(MarketSnapshot.trade_date <= date_end)
            filters.append(and_(*conditions))
        if cutoff_at is not None:
            filters = [
                and_(condition, MarketSnapshot.bucket_at <= cutoff_at)
                for condition in filters
            ]
        with get_session() as session:
            ranked = (
                select(
                    MarketSnapshot.id.label("snapshot_id"),
                    func.row_number()
                    .over(
                        partition_by=(
                            MarketSnapshot.data_type,
                            MarketSnapshot.subject_id,
                        ),
                        order_by=MarketSnapshot.bucket_at.desc(),
                    )
                    .label("series_rank"),
                )
                .where(or_(*filters))
                .subquery()
            )
            rows = session.scalars(
                select(MarketSnapshot)
                .join(
                    ranked,
                    ranked.c.snapshot_id == MarketSnapshot.id,
                )
                .where(ranked.c.series_rank <= normalized_limit)
                .order_by(
                    MarketSnapshot.data_type,
                    MarketSnapshot.subject_id,
                    MarketSnapshot.bucket_at.desc(),
                )
            ).all()
        grouped = {key: [] for key in unique_series}
        for row in rows:
            key = (row.data_type, row.subject_id)
            if key in grouped and len(grouped[key]) < normalized_limit:
                grouped[key].append(self._snapshot_to_dict(row))
        return grouped

    def latest_trade_dates(
        self,
        *,
        data_type: str,
        subject_ids: list[str],
    ) -> dict[str, date]:
        if not subject_ids:
            return {}
        with get_session() as session:
            rows = session.execute(
                select(
                    MarketSnapshot.subject_id,
                    func.max(MarketSnapshot.trade_date),
                )
                .where(
                    MarketSnapshot.data_type == data_type,
                    MarketSnapshot.subject_id.in_(subject_ids),
                )
                .group_by(MarketSnapshot.subject_id)
            ).all()
            return {
                str(subject_id): trade_date
                for subject_id, trade_date in rows
                if trade_date is not None
            }

    def query_previous_trade_date_history(
        self,
        *,
        subject_id: str,
        data_type: str,
        before_date: date,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with get_session() as session:
            previous_trade_date = session.scalar(
                select(func.max(MarketSnapshot.trade_date)).where(
                    MarketSnapshot.subject_id == subject_id,
                    MarketSnapshot.data_type == data_type,
                    MarketSnapshot.trade_date < before_date,
                )
            )
            if previous_trade_date is None:
                return []
            statement = (
                select(MarketSnapshot)
                .where(
                    MarketSnapshot.subject_id == subject_id,
                    MarketSnapshot.data_type == data_type,
                    MarketSnapshot.trade_date == previous_trade_date,
                )
                .order_by(MarketSnapshot.bucket_at.desc())
                .limit(max(1, min(int(limit), 2000)))
            )
            return [
                self._snapshot_to_dict(row)
                for row in session.scalars(statement).all()
            ]

    @staticmethod
    def _snapshot_to_dict(row: MarketSnapshot) -> dict[str, Any]:
        return {
            "id": row.id,
            "data_type": row.data_type,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "market": row.market,
            "provider": row.provider,
            "trade_date": row.trade_date,
            "observed_at": row.observed_at,
            "fetched_at": row.fetched_at,
            "bucket_at": row.bucket_at,
            "freshness_status": row.freshness_status,
            "source_latency_seconds": row.source_latency_seconds,
            "payload_hash": row.payload_hash,
            "data": row.data,
        }


class EtfDailyShareRepository:
    CHUNK_SIZE = 1000

    def upsert_batch(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        saved = 0
        with get_session() as session:
            for start in range(0, len(items), self.CHUNK_SIZE):
                values = [
                    {
                        **item,
                        "shares": Decimal(str(item["shares"])),
                    }
                    for item in items[start : start + self.CHUNK_SIZE]
                ]
                statement = pg_insert(EtfDailyShare).values(values)
                statement = statement.on_conflict_do_update(
                    constraint="uq_ft_etf_daily_shares_identity",
                    set_={
                        "name": statement.excluded.name,
                        "shares": statement.excluded.shares,
                        "share_unit": statement.excluded.share_unit,
                        "provider": statement.excluded.provider,
                        "observed_at": statement.excluded.observed_at,
                        "fetched_at": statement.excluded.fetched_at,
                        "data": statement.excluded.data,
                        "updated_at": func.now(),
                    },
                )
                result = session.execute(statement)
                saved += result.rowcount or 0
        return saved

    def list_complete_dates(self, dates: list[date]) -> set[date]:
        if not dates:
            return set()
        with get_session() as session:
            rows = session.execute(
                select(
                    EtfDailyShare.trade_date,
                    func.count(func.distinct(EtfDailyShare.exchange)),
                )
                .where(EtfDailyShare.trade_date.in_(dates))
                .group_by(EtfDailyShare.trade_date)
            ).all()
            return {
                trade_date
                for trade_date, exchange_count in rows
                if int(exchange_count or 0) >= 2
            }

    def previous_shares(
        self,
        *,
        before_date: date,
    ) -> tuple[date | None, dict[tuple[str, str], Decimal]]:
        with get_session() as session:
            previous_date = session.scalar(
                select(func.max(EtfDailyShare.trade_date)).where(
                    EtfDailyShare.trade_date < before_date
                )
            )
            if previous_date is None:
                return None, {}
            rows = session.execute(
                select(
                    EtfDailyShare.exchange,
                    EtfDailyShare.code,
                    EtfDailyShare.shares,
                ).where(EtfDailyShare.trade_date == previous_date)
            ).all()
            return previous_date, {
                (str(exchange), str(code)): shares
                for exchange, code, shares in rows
            }

    def latest_summary(self) -> dict[str, Any]:
        with get_session() as session:
            latest_date = session.scalar(select(func.max(EtfDailyShare.trade_date)))
            if latest_date is None:
                return {
                    "trade_date": None,
                    "fund_count": 0,
                    "exchange_count": 0,
                    "fetched_at": None,
                }
            row = session.execute(
                select(
                    func.count(EtfDailyShare.id),
                    func.count(func.distinct(EtfDailyShare.exchange)),
                    func.max(EtfDailyShare.fetched_at),
                ).where(EtfDailyShare.trade_date == latest_date)
            ).one()
            return {
                "trade_date": latest_date,
                "fund_count": int(row[0] or 0),
                "exchange_count": int(row[1] or 0),
                "fetched_at": row[2],
            }


class CollectionRunRepository:
    def finish_interrupted_running(self) -> int:
        """Close audit rows left running when the collection worker restarted."""

        with get_session() as session:
            result = session.execute(
                update(CollectionRun)
                .where(CollectionRun.status == "running")
                .values(
                    status="failed",
                    finished_at=func.now(),
                    error_type="WorkerRestart",
                    error_message=(
                        "collection worker restarted before the run finished"
                    ),
                )
            )
            return int(result.rowcount or 0)

    def start(
        self,
        *,
        task_name: str,
        source_name: str,
        event_id: str = "",
        scheduled_at: datetime | None = None,
        checkpoint_before: dict[str, Any] | None = None,
        retry_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> int:
        with get_session() as session:
            row = CollectionRun(
                task_name=task_name,
                source_name=source_name,
                event_id=event_id,
                scheduled_at=scheduled_at,
                checkpoint_before=checkpoint_before or {},
                retry_count=retry_count,
                details=details or {},
            )
            session.add(row)
            session.flush()
            return row.id

    def finish(
        self,
        run_id: int,
        *,
        status: str,
        fetched_count: int = 0,
        valid_count: int = 0,
        saved_count: int = 0,
        skipped_count: int = 0,
        source_time_min: datetime | None = None,
        source_time_max: datetime | None = None,
        checkpoint_after: dict[str, Any] | None = None,
        error_type: str = "",
        error_message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": func.now(),
            "fetched_count": fetched_count,
            "valid_count": valid_count,
            "saved_count": saved_count,
            "skipped_count": skipped_count,
            "source_time_min": source_time_min,
            "source_time_max": source_time_max,
            "checkpoint_after": checkpoint_after or {},
            "error_type": error_type,
            "error_message": (error_message or "")[:4000],
        }
        if details is not None:
            values["details"] = details
        with get_session() as session:
            session.execute(
                update(CollectionRun)
                .where(CollectionRun.id == run_id)
                .values(**values)
            )

    def summarize_since(self, since: datetime) -> dict[str, Any]:
        with get_session() as session:
            rows = session.execute(
                select(
                    CollectionRun.status,
                    func.count(CollectionRun.id),
                )
                .where(CollectionRun.started_at >= since)
                .group_by(CollectionRun.status)
            ).all()
            status_counts = {
                str(status or "unknown"): int(count or 0)
                for status, count in rows
            }
            return {
                "total": sum(status_counts.values()),
                "by_status": status_counts,
                "failed": status_counts.get("failed", 0),
                "partial_success": status_counts.get("partial_success", 0),
                "running": status_counts.get("running", 0),
            }

    def list_latest(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the latest run for every task/source pair."""

        with get_session() as session:
            statement = (
                select(CollectionRun)
                .order_by(
                    CollectionRun.task_name,
                    CollectionRun.source_name,
                    CollectionRun.started_at.desc(),
                )
                .distinct(
                    CollectionRun.task_name,
                    CollectionRun.source_name,
                )
                .limit(max(1, min(int(limit), 1000)))
            )
            return [
                {
                    "id": row.id,
                    "task_name": row.task_name,
                    "source_name": row.source_name,
                    "status": row.status,
                    "started_at": row.started_at,
                    "finished_at": row.finished_at,
                    "fetched_count": row.fetched_count,
                    "valid_count": row.valid_count,
                    "saved_count": row.saved_count,
                    "skipped_count": row.skipped_count,
                    "source_time_min": row.source_time_min,
                    "source_time_max": row.source_time_max,
                    "error_type": row.error_type,
                    "error_message": row.error_message,
                    "details": row.details,
                }
                for row in session.scalars(statement).all()
            ]
