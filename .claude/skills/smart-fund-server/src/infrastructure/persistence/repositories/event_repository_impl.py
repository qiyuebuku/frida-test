"""EventRepository SQLAlchemy 实现"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.extraction.repositories.event_repository import EventRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.extraction import Event

logger = logging.getLogger(__name__)


class EventRepositoryImpl(EventRepository):
    def upsert_event(
        self,
        event_data: dict,
        embedding_blob: bytes | None = None,
        embedding_model: str = "",
    ) -> bool:
        """单条事件入库 (fingerprint 冲突跳过)"""
        # 兼容 event 结构(扁平字段或嵌套 entities/impact)
        title = event_data.get("title") or ""
        event_type = event_data.get("event_type") or ""
        if not title or not event_type:
            return False

        impact = event_data.get("impact") or {}
        entities = event_data.get("entities") or {}

        values = {
            "title": title,
            "summary": event_data.get("summary") or "",
            "event_type": event_type,
            "event_subtype": event_data.get("event_subtype") or "",
            "industries": entities.get("industries") or event_data.get("industries") or [],
            "companies": entities.get("companies") or event_data.get("companies") or [],
            "organizations": entities.get("organizations") or event_data.get("organizations") or [],
            "regions": entities.get("regions") or event_data.get("regions") or [],
            "impact_direction": impact.get("direction") or event_data.get("impact_direction") or "neutral",
            "impact_strength": float(impact.get("strength") or event_data.get("impact_strength") or 0),
            "impact_scope": impact.get("scope") or event_data.get("impact_scope") or "",
            "impact_duration": impact.get("duration") or event_data.get("impact_duration") or "",
            "sentiment": float(event_data.get("sentiment") or 0.5),
            "novelty": float(event_data.get("novelty") or 0.5),
            "certainty": float(event_data.get("certainty") or 0.5),
            "source_news_ids": event_data.get("source_news_ids") or [],
            "event_time": event_data["event_time"],
            "fingerprint": event_data["fingerprint"],
            "embedding": embedding_blob,
            "embedding_model": (embedding_model or "")[:32],
        }

        try:
            with get_session() as s:
                stmt = pg_insert(Event).values(**values).on_conflict_do_nothing(
                    index_elements=["fingerprint"]
                )
                result = s.execute(stmt)
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"upsert_event 失败: {e}")
            return False

    def find_recent_with_embedding(self, hours: int = 24, limit: int = 500) -> list[dict]:
        with get_session() as s:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            rows = s.scalars(
                select(Event)
                .where(
                    Event.event_time >= cutoff,
                    Event.embedding.isnot(None),
                )
                .order_by(Event.event_time.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "summary": r.summary,
                    "event_type": r.event_type,
                    "industries": r.industries or [],
                    "impact_strength": r.impact_strength,
                    "sentiment": r.sentiment,
                    "event_time": r.event_time,
                    "embedding": bytes(r.embedding) if r.embedding else None,
                }
                for r in rows
            ]

    def find_unfilled_market_reaction(self, days: int = 3) -> list[dict]:
        """获取近 N 天未回填市场反应的事件。

        筛选条件为 feedback_at IS NULL（从未尝试过回填），而非"字段不完整"。
        """
        with get_session() as s:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            rows = s.scalars(
                select(Event)
                .where(
                    Event.event_time >= cutoff,
                    Event.feedback_at.is_(None),
                    Event.event_time < datetime.now(timezone.utc) - timedelta(days=1),
                )
                .order_by(Event.event_time.desc())
                .limit(100)
            ).all()
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "industries": r.industries or [],
                    "event_time": r.event_time,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    def update_market_reaction(self, event_id: int, reaction: dict | None = None) -> bool:
        """回填事件的市场反应字段。

        即使 reaction 为空（无有效字段），也会写入 feedback_at 标记为已尝试。
        feedback_at 语义为"首次尝试回填时间"，非"所有字段回填完成"。
        下游 reflection 模块若依赖某些字段，必须显式校验目标列非空。
        """
        valid_fields = {
            "sector_change_1d", "sector_change_3d", "sector_volume_change",
            "north_flow_1d", "reaction_delay_minutes",
        }
        update_set = {}
        if reaction:
            update_set = {k: v for k, v in reaction.items() if k in valid_fields}
        update_set["feedback_at"] = func.now()
        try:
            with get_session() as s:
                result = s.execute(
                    update(Event).where(Event.id == event_id).values(**update_set)
                )
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"update_market_reaction({event_id}) 失败: {e}")
            return False

    def count_with_embedding(self) -> int:
        with get_session() as s:
            return s.scalar(
                select(func.count()).select_from(Event).where(Event.embedding.isnot(None))
            ) or 0

    def upsert_l1_event(self, event_data: dict) -> bool:
        """L1 事件入库 (dedup_key 冲突时合并 evidence_refs)"""
        title = event_data.get("title") or ""
        event_type = event_data.get("event_type") or ""
        dedup_key = event_data.get("dedup_key")
        if not title or not event_type:
            return False

        values = {
            "title": title,
            "summary": event_data.get("summary") or "",
            "event_type": event_type,
            "event_subtype": event_data.get("event_subtype") or "",
            "industries": event_data.get("affected_industries") or event_data.get("industries") or [],
            "companies": event_data.get("affected_stocks") or event_data.get("companies") or [],
            "organizations": event_data.get("organizations") or [],
            "regions": event_data.get("affected_regions") or event_data.get("regions") or [],
            "impact_direction": event_data.get("direction") or event_data.get("impact_direction") or "neutral",
            "impact_strength": float(event_data.get("strength") or event_data.get("impact_strength") or 0),
            "impact_scope": event_data.get("scope") or event_data.get("impact_scope") or "",
            "impact_duration": event_data.get("duration") or event_data.get("impact_duration") or "",
            "sentiment": float(event_data.get("sentiment") or 0.5),
            "novelty": float(event_data.get("novelty") or 0.5),
            "certainty": float(event_data.get("certainty") or 0.5),
            "event_time": event_data["event_time"],
            "fingerprint": event_data.get("fingerprint") or (dedup_key or "")[:64],
            "source_type": event_data.get("source_type", "text"),
            "source_table": event_data.get("source_table", ""),
            "evidence_refs": event_data.get("evidence_refs") or [],
            "schema_version": event_data.get("schema_version", "v1.0"),
            "extractor_version": event_data.get("extractor_version", ""),
            "affected_stocks": event_data.get("affected_stocks") or [],
            "affected_industries": event_data.get("affected_industries") or [],
            "affected_concepts": event_data.get("affected_concepts") or [],
            "affected_regions": event_data.get("affected_regions") or [],
            "quality_flags": event_data.get("quality_flags") or [],
            "dedup_key": dedup_key,
        }
        if event_data.get("source_news_ids"):
            values["source_news_ids"] = event_data["source_news_ids"]

        try:
            with get_session() as s:
                stmt = pg_insert(Event).values(**values).on_conflict_do_update(
                    index_elements=["dedup_key"],
                    set_={
                        "evidence_refs": text(
                            "COALESCE(ft_events.evidence_refs, '[]'::jsonb) || EXCLUDED.evidence_refs"
                        ),
                    },
                )
                result = s.execute(stmt)
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"upsert_l1_event 失败: {e}")
            return False

    def find_by_dedup_key(self, dedup_key: str) -> dict | None:
        """按 dedup_key 查找已存在的事件"""
        with get_session() as s:
            row = s.scalar(
                select(Event).where(Event.dedup_key == dedup_key).limit(1)
            )
            if not row:
                return None
            return {
                "id": row.id,
                "dedup_key": row.dedup_key,
                "evidence_refs": row.evidence_refs or [],
                "event_type": row.event_type,
                "title": row.title,
            }
