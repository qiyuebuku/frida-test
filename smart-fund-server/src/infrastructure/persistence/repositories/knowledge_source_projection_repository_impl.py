"""SQLAlchemy implementation for KG source projection raw-row reads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Text, inspect, or_, select
from sqlalchemy.orm import Session

from src.domain.knowledge.repositories import KnowledgeSourceProjectionRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import (
    MacroIndicator,
    MarketCache,
    MarketFlow,
    News,
    Sentiment,
)

Target = Literal["prod", "test"]


class KnowledgeSourceProjectionRepositoryImpl(KnowledgeSourceProjectionRepository):
    """Reads collection tables and returns plain Raw Row dictionaries."""

    def __init__(self, session: Session | None = None, target: Target | None = None):
        self._session = session
        self._target = target

    def fetch_rows(
        self,
        source: str,
        *,
        limit: int,
        codes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if source == "ft_news":
            return self.fetch_ft_news(limit=limit, codes=codes)
        if source == "ft_market_flow":
            return self.fetch_ft_market_flow(limit=limit)
        if source == "ft_market_cache":
            return self.fetch_ft_market_cache(limit=limit)
        if source == "ft_sentiment":
            return self.fetch_ft_sentiment(limit=limit)
        if source == "ft_macro_indicators":
            return self.fetch_ft_macro_indicators(limit=limit)
        raise ValueError(f"unsupported source: {source}")

    def fetch_ft_news(self, *, limit: int, codes: list[str] | None = None) -> list[dict[str, Any]]:
        if not self._table_exists("ft_news"):
            return []
        with self._session_scope() as session:
            stmt = select(News).order_by(News.published_at.desc(), News.id.desc()).limit(_limit(limit))
            patterns = [f"%{code}%" for code in _normalize_codes(codes)]
            if patterns:
                stmt = stmt.where(or_(*[News.related_stocks.cast(Text).like(pattern) for pattern in patterns]))
            return [_news_row(row) for row in session.scalars(stmt).all()]

    def fetch_ft_market_flow(self, *, limit: int) -> list[dict[str, Any]]:
        if not self._table_exists("ft_market_flow"):
            return []
        with self._session_scope() as session:
            rows = session.scalars(
                select(MarketFlow)
                .order_by(MarketFlow.trade_date.desc(), MarketFlow.id.desc())
                .limit(_limit(limit))
            ).all()
            return [_market_flow_row(row) for row in rows]

    def fetch_ft_market_cache(self, *, limit: int) -> list[dict[str, Any]]:
        if not self._table_exists("ft_market_cache"):
            return []
        with self._session_scope() as session:
            rows = session.scalars(
                select(MarketCache)
                .order_by(MarketCache.created_at.desc(), MarketCache.id.desc())
                .limit(_limit(limit))
            ).all()
            return [_market_cache_row(row) for row in rows]

    def fetch_ft_sentiment(self, *, limit: int) -> list[dict[str, Any]]:
        if not self._table_exists("ft_sentiment"):
            return []
        with self._session_scope() as session:
            rows = session.scalars(
                select(Sentiment)
                .order_by(Sentiment.trade_date.desc(), Sentiment.id.desc())
                .limit(_limit(limit))
            ).all()
            return [_sentiment_row(row) for row in rows]

    def fetch_ft_macro_indicators(self, *, limit: int) -> list[dict[str, Any]]:
        if not self._table_exists("ft_macro_indicators"):
            return []
        with self._session_scope() as session:
            rows = session.scalars(
                select(MacroIndicator)
                .order_by(MacroIndicator.published_at.desc().nullslast(), MacroIndicator.id.desc())
                .limit(_limit(limit))
            ).all()
            return [_macro_indicator_row(row) for row in rows]

    def _table_exists(self, table_name: str) -> bool:
        with self._session_scope() as session:
            return bool(inspect(session.bind).has_table(table_name))

    def _session_scope(self):
        if self._session is not None:
            return _ExistingSessionScope(self._session)
        return get_session(self._target)


class _ExistingSessionScope:
    def __init__(self, session: Session):
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _news_row(row: News) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "source": row.source,
        "source_name": row.source_name,
        "source_reliability": row.source_reliability,
        "category": row.category,
        "url": row.url,
        "tags": row.tags,
        "related_stocks": row.related_stocks,
        "published_at": row.published_at,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at,
    }


def _market_flow_row(row: MarketFlow) -> dict[str, Any]:
    return {
        "id": row.id,
        "data_type": row.data_type,
        "trade_date": row.trade_date,
        "data": row.data,
        "created_at": row.created_at,
    }


def _market_cache_row(row: MarketCache) -> dict[str, Any]:
    return {
        "id": row.id,
        "data_type": row.data_type,
        "data": row.data,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
    }


def _sentiment_row(row: Sentiment) -> dict[str, Any]:
    return {
        "id": row.id,
        "data_type": row.data_type,
        "trade_date": row.trade_date,
        "data": row.data,
        "created_at": row.created_at,
    }


def _macro_indicator_row(row: MacroIndicator) -> dict[str, Any]:
    return {
        "id": row.id,
        "indicator": row.indicator,
        "period": row.period,
        "value": row.value,
        "unit": row.unit,
        "prev_value": row.prev_value,
        "source": row.source,
        "published_at": row.published_at,
        "dim_tag": row.dim_tag,
        "yoy": row.yoy,
        "mom": row.mom,
        "created_at": row.created_at,
    }


def _normalize_codes(codes: list[str] | None) -> list[str]:
    result: list[str] = []
    for code in codes or []:
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        if len(digits) >= 6:
            result.append(digits[-6:])
    return sorted(set(result))


def _limit(value: int) -> int:
    return max(1, min(int(value), 5000))
