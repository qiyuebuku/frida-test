"""新闻仓储 SQLAlchemy 实现。"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.news_repository import NewsRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import News
from src.infrastructure.time_utils import APP_TZ, app_today

logger = logging.getLogger(__name__)


class NewsRepositoryImpl(NewsRepository):
    def upsert_batch(self, items: list[dict]) -> int:
        return len(self.upsert_batch_returning_ids(items))

    def upsert_batch_returning_ids(self, items: list[dict]) -> list[int]:
        if not items:
            return []
        with get_session() as s:
            stmt = pg_insert(News).values(items).on_conflict_do_nothing(
                index_elements=["fingerprint"]
            ).returning(News.id)
            result = s.execute(stmt)
            return [int(row[0]) for row in result.fetchall()]

    def find_today_titles(self, today: date | None = None) -> list[str]:
        from datetime import time
        if today is None:
            today = app_today()
        # 业务日期按 Asia/Shanghai 计算；published_at 在数据库中按 TIMESTAMPTZ 存储。
        start = datetime.combine(today, time.min, tzinfo=APP_TZ).astimezone(timezone.utc)
        end = datetime.combine(today, time.max, tzinfo=APP_TZ).astimezone(timezone.utc)
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

    def query_by_tag(self, tag: str, since) -> list[dict]:
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import ARRAY, TEXT
        with get_session() as s:
            rows = s.scalars(
                select(News)
                .where(
                    News.tags.op("@>")(cast([tag], ARRAY(TEXT))),
                    News.published_at >= since,
                )
                .order_by(News.published_at.desc())
                .limit(200)
            ).all()
            return [
                {"title": r.title, "summary": r.summary, "published_at": r.published_at}
                for r in rows
            ]

    def query_by_category(self, categories: list[str], since) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(News)
                .where(
                    News.category.in_(categories),
                    News.published_at >= since,
                )
                .order_by(News.published_at.desc())
                .limit(200)
            ).all()
            return [
                {"title": r.title, "summary": r.summary, "published_at": r.published_at}
                for r in rows
            ]

    def find_unclassified(self, limit: int = 200) -> list[dict]:
        """L1a 专用：读取未分类的新闻（event_extracted=false AND l1_classified_at IS NULL）"""
        with get_session() as s:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            rows = s.scalars(
                select(News)
                .where(
                    News.event_extracted.is_(False),
                    News.l1_classified_at.is_(None),
                    News.published_at >= cutoff,
                )
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
                    "related_stocks": r.related_stocks,
                }
                for r in rows
            ]

    def mark_classified(self, ids: list[int]) -> int:
        """标记新闻为 L1a 已分类（设置 l1_classified_at = now()）"""
        if not ids:
            return 0
        with get_session() as s:
            result = s.execute(
                update(News).where(News.id.in_(ids)).values(l1_classified_at=func.now())
            )
            return result.rowcount or 0
