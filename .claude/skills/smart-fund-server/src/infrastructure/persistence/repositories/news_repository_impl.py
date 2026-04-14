"""NewsRepository SQLAlchemy 实现"""
import logging
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.news_repository import NewsRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import News

logger = logging.getLogger(__name__)


class NewsRepositoryImpl(NewsRepository):
    def upsert_batch(self, items: list[dict]) -> int:
        if not items:
            return 0
        with get_session() as s:
            stmt = pg_insert(News).values(items).on_conflict_do_nothing(
                index_elements=["fingerprint"]
            )
            result = s.execute(stmt)
            return result.rowcount or 0

    def find_today_titles(self, today: date | None = None) -> list[str]:
        from datetime import datetime, time, timezone
        if today is None:
            today = date.today()
        # 用 published_at 过滤当日(timezone-aware)
        start = datetime.combine(today, time.min, tzinfo=timezone.utc)
        end = datetime.combine(today, time.max, tzinfo=timezone.utc)
        with get_session() as s:
            rows = s.scalars(
                select(News.title).where(
                    News.published_at >= start,
                    News.published_at <= end,
                )
            ).all()
            return list(rows)

    def find_unextracted(self, limit: int = 30) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(News)
                .where(News.event_extracted.is_(False))
                .order_by(News.published_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "summary": r.summary,
                    "content": r.content,
                    "source": r.source,
                    "source_name": r.source_name,
                    "source_reliability": r.source_reliability,
                    "category": r.category,
                    "url": r.url,
                    "published_at": r.published_at,
                }
                for r in rows
            ]

    def mark_extracted(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with get_session() as s:
            result = s.execute(
                update(News).where(News.id.in_(ids)).values(event_extracted=True)
            )
            return result.rowcount or 0

    def find_recent_titles(self, days: int = 3) -> list[str]:
        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with get_session() as s:
            rows = s.scalars(
                select(News.title).where(News.published_at >= since)
            ).all()
            return list(rows)

    def count(self) -> int:
        from sqlalchemy import func
        with get_session() as s:
            return s.scalar(select(func.count()).select_from(News)) or 0
