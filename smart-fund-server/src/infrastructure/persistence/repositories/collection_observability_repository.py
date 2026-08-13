"""Read-only inventory and pagination for persisted collection data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import (
    CollectionRun,
    CollectionState,
    EtfDailyShare,
    InstrumentDisclosure,
    InstrumentObservation,
    InstrumentProfile,
    MacroIndicator,
    MacroRegime,
    MarketCache,
    MarketFlow,
    MarketSnapshot,
    News,
    Sentiment,
    SentimentSignal,
)


@dataclass(frozen=True)
class CollectionDomain:
    key: str
    title: str
    model: type[DeclarativeBase]
    group_column: InstrumentedAttribute
    latest_column: InstrumentedAttribute
    order_columns: tuple[InstrumentedAttribute, ...]
    search_columns: tuple[InstrumentedAttribute, ...]


COLLECTION_DOMAINS = {
    domain.key: domain
    for domain in (
        CollectionDomain(
            "news",
            "新闻原始数据",
            News,
            News.source,
            News.published_at,
            (News.published_at, News.id),
            (News.title, News.source, News.source_name, News.category),
        ),
        CollectionDomain(
            "market_flow",
            "资金流历史",
            MarketFlow,
            MarketFlow.data_type,
            MarketFlow.trade_date,
            (MarketFlow.trade_date, MarketFlow.id),
            (MarketFlow.data_type,),
        ),
        CollectionDomain(
            "market_cache",
            "市场最新缓存",
            MarketCache,
            MarketCache.data_type,
            MarketCache.created_at,
            (MarketCache.created_at, MarketCache.id),
            (MarketCache.data_type,),
        ),
        CollectionDomain(
            "sentiment",
            "市场情绪",
            Sentiment,
            Sentiment.data_type,
            Sentiment.trade_date,
            (Sentiment.trade_date, Sentiment.id),
            (Sentiment.data_type,),
        ),
        CollectionDomain(
            "sentiment_signal",
            "情绪派生信号",
            SentimentSignal,
            SentimentSignal.market_level,
            SentimentSignal.snapshot_date,
            (SentimentSignal.snapshot_date,),
            (SentimentSignal.market_level, SentimentSignal.market_trend),
        ),
        CollectionDomain(
            "macro_indicator",
            "宏观指标",
            MacroIndicator,
            MacroIndicator.indicator,
            MacroIndicator.published_at,
            (MacroIndicator.published_at, MacroIndicator.id),
            (
                MacroIndicator.indicator,
                MacroIndicator.period,
                MacroIndicator.dim_tag,
                MacroIndicator.source,
            ),
        ),
        CollectionDomain(
            "macro_regime",
            "宏观状态",
            MacroRegime,
            MacroRegime.regime,
            MacroRegime.snapshot_date,
            (MacroRegime.snapshot_date, MacroRegime.id),
            (MacroRegime.regime,),
        ),
        CollectionDomain(
            "collection_state",
            "采集检查点",
            CollectionState,
            CollectionState.aggregator,
            CollectionState.updated_at,
            (CollectionState.updated_at, CollectionState.id),
            (
                CollectionState.aggregator,
                CollectionState.source_name,
                CollectionState.mode,
            ),
        ),
        CollectionDomain("instrument_profile", "标的当前资料", InstrumentProfile, InstrumentProfile.data_type, InstrumentProfile.updated_at, (InstrumentProfile.updated_at, InstrumentProfile.id), (InstrumentProfile.code, InstrumentProfile.data_type, InstrumentProfile.provider)),
        CollectionDomain("instrument_disclosure", "标的定期披露", InstrumentDisclosure, InstrumentDisclosure.data_type, InstrumentDisclosure.report_date, (InstrumentDisclosure.report_date, InstrumentDisclosure.id), (InstrumentDisclosure.code, InstrumentDisclosure.data_type, InstrumentDisclosure.provider)),
        CollectionDomain("instrument_observation", "标的日级观察", InstrumentObservation, InstrumentObservation.data_type, InstrumentObservation.observation_date, (InstrumentObservation.observation_date, InstrumentObservation.id), (InstrumentObservation.code, InstrumentObservation.data_type, InstrumentObservation.provider)),
        CollectionDomain(
            "market_snapshot",
            "市场行情快照",
            MarketSnapshot,
            MarketSnapshot.data_type,
            MarketSnapshot.bucket_at,
            (MarketSnapshot.bucket_at, MarketSnapshot.id),
            (
                MarketSnapshot.data_type,
                MarketSnapshot.subject_id,
                MarketSnapshot.subject_type,
                MarketSnapshot.provider,
                MarketSnapshot.market,
            ),
        ),
        CollectionDomain(
            "etf_daily_share",
            "ETF 日份额",
            EtfDailyShare,
            EtfDailyShare.exchange,
            EtfDailyShare.trade_date,
            (EtfDailyShare.trade_date, EtfDailyShare.id),
            (EtfDailyShare.code, EtfDailyShare.name, EtfDailyShare.exchange),
        ),
        CollectionDomain(
            "collection_run",
            "采集执行记录",
            CollectionRun,
            CollectionRun.task_name,
            CollectionRun.started_at,
            (CollectionRun.started_at, CollectionRun.id),
            (
                CollectionRun.task_name,
                CollectionRun.source_name,
                CollectionRun.status,
                CollectionRun.error_message,
            ),
        ),
    )
}


class CollectionObservabilityRepository:
    """Expose every persisted collection domain through one read model."""

    def inventory(
        self,
        *,
        cutoff_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self._domain_inventory(domain, cutoff_at=cutoff_at)
            for domain in COLLECTION_DOMAINS.values()
        ]

    def dashboard_context(self) -> dict[str, Any]:
        cache_types = {"market_overview", "market_environment"}
        sentiment_types = {
            "limit_pool",
            "xueqiu_hot_stocks",
        }
        with get_session() as session:
            cache_rows = session.scalars(
                select(MarketCache).where(MarketCache.data_type.in_(cache_types))
            ).all()
            sentiment_rows = {}
            for data_type in sentiment_types:
                row = session.scalars(
                    select(Sentiment)
                    .where(Sentiment.data_type == data_type)
                    .order_by(
                        Sentiment.trade_date.desc(),
                        Sentiment.created_at.desc(),
                        Sentiment.id.desc(),
                    )
                    .limit(1)
                ).first()
                if row is not None:
                    sentiment_rows[data_type] = _serialize_model(row)
        return {
            "market_cache": {
                row.data_type: _serialize_model(row)
                for row in cache_rows
            },
            "sentiment": sentiment_rows,
        }

    def list_records(
        self,
        *,
        domain_key: str,
        group: str | None = None,
        query: str | None = None,
        cutoff_at: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        domain = COLLECTION_DOMAINS.get(domain_key)
        if domain is None:
            raise ValueError(f"unknown collection domain: {domain_key}")
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))
        filters = []
        if cutoff_at is not None:
            filters.append(
                domain.latest_column <= _domain_cutoff(domain, cutoff_at)
            )
        if group:
            filters.append(cast(domain.group_column, String) == str(group))
        normalized_query = str(query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            filters.append(
                or_(
                    *[
                        cast(column, String).ilike(pattern)
                        for column in domain.search_columns
                    ]
                )
            )

        try:
            with get_session() as session:
                total_statement = select(func.count()).select_from(domain.model)
                rows_statement = select(domain.model)
                if filters:
                    total_statement = total_statement.where(*filters)
                    rows_statement = rows_statement.where(*filters)
                total = int(session.scalar(total_statement) or 0)
                rows = session.scalars(
                    rows_statement.order_by(
                        *[column.desc().nullslast() for column in domain.order_columns]
                    )
                    .offset(normalized_offset)
                    .limit(normalized_limit)
                ).all()
        except ProgrammingError as exc:
            return {
                "domain": domain.key,
                "title": domain.title,
                "available": False,
                "total": 0,
                "limit": normalized_limit,
                "offset": normalized_offset,
                "items": [],
                "error": _database_error(exc),
            }

        return {
            "domain": domain.key,
            "title": domain.title,
            "available": True,
            "total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "items": [_serialize_model(row) for row in rows],
        }

    @staticmethod
    def domain_keys() -> list[str]:
        return list(COLLECTION_DOMAINS)

    @staticmethod
    def record_identity(
        *,
        domain_key: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        domain = COLLECTION_DOMAINS.get(domain_key)
        if domain is None:
            raise ValueError(f"unknown collection domain: {domain_key}")
        identity = {
            column.name: record.get(column.name)
            for column in domain.model.__table__.primary_key.columns
        }
        if not identity or any(value is None for value in identity.values()):
            raise ValueError(f"record has no complete identity: {domain_key}")
        return identity

    def get_record_at(
        self,
        *,
        domain_key: str,
        identity: Mapping[str, Any],
        cutoff_at: datetime,
    ) -> dict[str, Any] | None:
        domain = COLLECTION_DOMAINS.get(domain_key)
        if domain is None:
            raise ValueError(f"unknown collection domain: {domain_key}")
        primary_keys = {
            column.name: column
            for column in domain.model.__table__.primary_key.columns
        }
        if set(identity) != set(primary_keys):
            raise ValueError(f"invalid record identity for domain: {domain_key}")
        filters = [
            column == _coerce_identity_value(column, identity[name])
            for name, column in primary_keys.items()
        ]
        filters.append(
            domain.latest_column <= _domain_cutoff(domain, cutoff_at)
        )
        with get_session() as session:
            row = session.scalar(select(domain.model).where(*filters))
            return _serialize_model(row) if row is not None else None

    def _domain_inventory(
        self,
        domain: CollectionDomain,
        *,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            with get_session() as session:
                cutoff_filter = (
                    domain.latest_column <= _domain_cutoff(domain, cutoff_at)
                    if cutoff_at is not None
                    else None
                )
                total_statement = select(func.count()).select_from(domain.model)
                latest_statement = select(func.max(domain.latest_column))
                groups_statement = select(
                    domain.group_column,
                    func.count(),
                )
                if cutoff_filter is not None:
                    total_statement = total_statement.where(cutoff_filter)
                    latest_statement = latest_statement.where(cutoff_filter)
                    groups_statement = groups_statement.where(cutoff_filter)
                total = int(
                    session.scalar(total_statement)
                    or 0
                )
                latest_at = session.scalar(latest_statement)
                groups = session.execute(
                    groups_statement
                    .group_by(domain.group_column)
                    .order_by(func.count().desc())
                ).all()
        except ProgrammingError as exc:
            return {
                "domain": domain.key,
                "title": domain.title,
                "table": domain.model.__tablename__,
                "available": False,
                "total": 0,
                "latest_at": None,
                "groups": [],
                "error": _database_error(exc),
            }
        return {
            "domain": domain.key,
            "title": domain.title,
            "table": domain.model.__tablename__,
            "available": True,
            "total": total,
            "latest_at": latest_at,
            "groups": [
                {
                    "name": str(name or "未分类"),
                    "count": int(count or 0),
                }
                for name, count in groups
            ],
        }


def _serialize_model(row: Any) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def _domain_cutoff(
    domain: CollectionDomain,
    cutoff_at: datetime,
) -> datetime | date:
    """Match an aware Agent cutoff to DATE or TIMESTAMP domain columns."""

    try:
        python_type = domain.latest_column.type.python_type
    except (AttributeError, NotImplementedError):
        python_type = datetime
    return cutoff_at.date() if python_type is date else cutoff_at


def _coerce_identity_value(column, value: Any) -> Any:
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return value
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return python_type(value) if not isinstance(value, python_type) else value


def _database_error(exc: ProgrammingError) -> str:
    message = str(getattr(exc, "orig", exc)).splitlines()[0]
    return message or type(exc).__name__
