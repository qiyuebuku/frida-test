"""Community Insight 高级认知报告异步刷新服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.infrastructure.clients.embedding import EMBEDDING_MODEL, embed_texts
from src.infrastructure.connections import get_session
from src.infrastructure.db import redis_lock
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeCommunityInsight,
    KnowledgeGraphCommunity,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MILVUS_COLLECTION_COMMUNITY_INSIGHT,
    MilvusHybridDocument,
    MilvusTypedHybridStore,
)

logger = logging.getLogger(__name__)

COMMUNITY_INSIGHT_LOCK_NAME = "kg:community_insight_refresh"
COMMUNITY_INSIGHT_LOCK_TTL_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_LOCK_TTL_SECONDS", "300"))
COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS", "30"))
COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS", "300"))
COMMUNITY_INSIGHT_SCAN_LIMIT = int(os.getenv("KG_COMMUNITY_INSIGHT_SCAN_LIMIT", "200"))
COMMUNITY_INSIGHT_BATCH_LIMIT = int(os.getenv("KG_COMMUNITY_INSIGHT_BATCH_LIMIT", "5"))
COMMUNITY_INSIGHT_MAX_MATERIALS = int(os.getenv("KG_COMMUNITY_INSIGHT_MAX_MATERIALS", "80"))
COMMUNITY_INSIGHT_MAX_TOKENS = int(os.getenv("KG_COMMUNITY_INSIGHT_MAX_TOKENS", "4500"))


_INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["insight_full_report", "report_json"],
    "properties": {
        "insight_full_report": {"type": "string", "minLength": 120},
        "report_json": {
            "type": "object",
            "additionalProperties": True,
            "required": ["core_thesis", "basis", "reversal_conditions", "quality_flags", "use_boundary"],
            "properties": {
                "core_thesis": {"type": "string"},
                "basis": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "weak_signals": {"type": "array", "items": {"type": "string"}},
                "conflicts": {"type": "array", "items": {"type": "string"}},
                "reversal_conditions": {"type": "array", "items": {"type": "string"}},
                "quality_flags": {"type": "array", "items": {"type": "string"}},
                "use_boundary": {"type": "string"},
            },
        },
    },
}


@dataclass(frozen=True)
class _InsightCandidate:
    community: KnowledgeGraphCommunity
    existing: KnowledgeCommunityInsight | None
    source_count: int
    cognitive_card_count: int
    assignment_count: int


class CommunityInsightService:
    """扫描并刷新 Community Insight 的应用层用例。"""

    def __init__(
        self,
        *,
        target: str = "prod",
        vector_store: MilvusTypedHybridStore | None = None,
    ):
        self._target = target
        self._llm = get_llm_gateway_service()
        self._vector_store = vector_store or MilvusTypedHybridStore()

    async def refresh_due_insights(
        self,
        *,
        limit: int = COMMUNITY_INSIGHT_BATCH_LIMIT,
        scan_limit: int = COMMUNITY_INSIGHT_SCAN_LIMIT,
    ) -> dict[str, Any]:
        """刷新一批需要生成或更新的 Community Insight。"""

        t0 = time.time()
        metadata = {
            "task": "kg_community_insight_refresh",
            "target": self._target,
            "limit": limit,
            "scan_limit": scan_limit,
        }
        with langfuse_propagation_context(
            trace_name="kg.community_insight.refresh_due",
            tags=["kg", "community_insight", "refresh"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.community_insight.refresh_due",
                as_type="span",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    with redis_lock.acquire(COMMUNITY_INSIGHT_LOCK_NAME, ttl=COMMUNITY_INSIGHT_LOCK_TTL_SECONDS) as lock:
                        if not lock:
                            result = {
                                "skipped": True,
                                "reason": "lock_held",
                                "refreshed": 0,
                                "failed": 0,
                            }
                            langfuse_update_span(output=result, status_message="lock_held")
                            return result
                        langfuse_update_span(metadata={"lock_acquired": True})

                        stop_renew = asyncio.Event()
                        lock_lost = asyncio.Event()
                        renew_task = asyncio.create_task(_renew_lock_loop(lock, stop_renew, lock_lost))
                        try:
                            result = await self._refresh_due_insights_locked(
                                limit=limit,
                                scan_limit=scan_limit,
                                lock_lost=lock_lost,
                            )
                            result["duration_seconds"] = round(time.time() - t0, 3)
                            langfuse_update_span(output=result, status_message="completed")
                            return result
                        finally:
                            stop_renew.set()
                            await renew_task
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    async def refresh_community_ids(
        self,
        community_ids: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """刷新指定 community，供验收脚本和人工诊断使用。"""

        selected_ids = [community_id for community_id in dict.fromkeys(community_ids) if community_id]
        if not selected_ids:
            return {
                "skipped": True,
                "reason": "no_community_ids",
                "refreshed": 0,
                "failed": 0,
            }

        t0 = time.time()
        metadata = {
            "task": "kg_community_insight_refresh_ids",
            "target": self._target,
            "requested": len(selected_ids),
            "force": force,
        }
        with langfuse_propagation_context(
            trace_name="kg.community_insight.refresh_ids",
            tags=["kg", "community_insight", "refresh_ids"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.community_insight.refresh_ids",
                as_type="span",
                input={"community_ids": selected_ids, **metadata},
                metadata=metadata,
            ):
                try:
                    with redis_lock.acquire(COMMUNITY_INSIGHT_LOCK_NAME, ttl=COMMUNITY_INSIGHT_LOCK_TTL_SECONDS) as lock:
                        if not lock:
                            result = {
                                "skipped": True,
                                "reason": "lock_held",
                                "requested": len(selected_ids),
                                "refreshed": 0,
                                "failed": 0,
                            }
                            langfuse_update_span(output=result, status_message="lock_held")
                            return result
                        langfuse_update_span(metadata={"lock_acquired": True})

                        stop_renew = asyncio.Event()
                        lock_lost = asyncio.Event()
                        renew_task = asyncio.create_task(_renew_lock_loop(lock, stop_renew, lock_lost))
                        try:
                            candidates = self._load_candidates_by_ids(selected_ids, force=force)
                            refreshed = 0
                            failed = 0
                            errors: list[dict[str, str]] = []
                            for candidate in candidates:
                                if lock_lost.is_set():
                                    raise RuntimeError("kg_community_insight_refresh 在指定刷新前丢失分布式锁")
                                try:
                                    await self._refresh_one(candidate)
                                    refreshed += 1
                                except Exception as exc:
                                    failed += 1
                                    logger.exception(
                                        "[kg_community_insight_refresh] 指定 community 刷新失败: %s",
                                        candidate.community.community_id,
                                    )
                                    errors.append({"community_id": candidate.community.community_id, "error": str(exc)[:500]})
                                    self._mark_failed(candidate.community, str(exc))

                            result = {
                                "skipped": False,
                                "requested": len(selected_ids),
                                "selected": len(candidates),
                                "refreshed": refreshed,
                                "failed": failed,
                                "errors": errors[:10],
                                "duration_seconds": round(time.time() - t0, 3),
                            }
                            langfuse_update_span(output=result, status_message="completed")
                            return result
                        finally:
                            stop_renew.set()
                            await renew_task
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    async def _refresh_due_insights_locked(
        self,
        *,
        limit: int,
        scan_limit: int,
        lock_lost: asyncio.Event,
    ) -> dict[str, Any]:
        candidates = self._load_due_candidates(limit=limit, scan_limit=scan_limit)
        refreshed = 0
        failed = 0
        errors: list[dict[str, str]] = []

        for candidate in candidates:
            if lock_lost.is_set():
                raise RuntimeError("kg_community_insight_refresh 在刷新前丢失分布式锁")
            try:
                await self._refresh_one(candidate)
                refreshed += 1
            except Exception as exc:
                failed += 1
                logger.exception("[kg_community_insight_refresh] community 刷新失败: %s", candidate.community.community_id)
                errors.append({"community_id": candidate.community.community_id, "error": str(exc)[:500]})
                self._mark_failed(candidate.community, str(exc))

        return {
            "skipped": False,
            "scanned": scan_limit,
            "due": len(candidates),
            "refreshed": refreshed,
            "failed": failed,
            "errors": errors[:10],
        }

    def _load_due_candidates(self, *, limit: int, scan_limit: int) -> list[_InsightCandidate]:
        now = datetime.now(timezone.utc)
        with langfuse_observation(
            name="kg.community_insight.pg.load_due_candidates",
            as_type="span",
            input={"limit": limit, "scan_limit": scan_limit},
        ):
            with get_session(self._target) as session:
                communities = session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(KnowledgeGraphCommunity.status == "active")
                    .order_by(KnowledgeGraphCommunity.updated_at.asc())
                    .limit(scan_limit)
                ).all()
                community_ids = [community.community_id for community in communities]
                insights = session.scalars(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(community_ids))
                ).all() if community_ids else []
                insight_by_community = {insight.community_id: insight for insight in insights}

                candidates: list[_InsightCandidate] = []
                for community in communities:
                    source_count, card_count, assignment_count = _community_counts(community)
                    if max(source_count, card_count) <= 1:
                        continue
                    existing = insight_by_community.get(community.community_id)
                    if not _community_needs_insight_refresh(community, existing, now=now):
                        continue
                    candidates.append(
                        _InsightCandidate(
                            community=community,
                            existing=existing,
                            source_count=source_count,
                            cognitive_card_count=card_count,
                            assignment_count=assignment_count,
                        )
                    )
                    if len(candidates) >= limit:
                        break
                langfuse_update_span(
                    output={
                        "scanned": len(communities),
                        "existing_insights": len(insights),
                        "selected": len(candidates),
                        "candidate_ids": [item.community.community_id for item in candidates[:20]],
                    },
                    status_message="completed",
                )
                return candidates

    def _load_candidates_by_ids(self, community_ids: list[str], *, force: bool) -> list[_InsightCandidate]:
        now = datetime.now(timezone.utc)
        with langfuse_observation(
            name="kg.community_insight.pg.load_candidates_by_ids",
            as_type="span",
            input={"requested": len(community_ids), "force": force, "community_ids": community_ids[:50]},
        ):
            with get_session(self._target) as session:
                communities = session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(KnowledgeGraphCommunity.community_id.in_(community_ids))
                    .where(KnowledgeGraphCommunity.status == "active")
                ).all()
                insights = session.scalars(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(community_ids))
                ).all()
                insight_by_community = {insight.community_id: insight for insight in insights}
                order = {community_id: index for index, community_id in enumerate(community_ids)}

                candidates: list[_InsightCandidate] = []
                for community in sorted(communities, key=lambda item: order.get(item.community_id, len(order))):
                    source_count, card_count, assignment_count = _community_counts(community)
                    if max(source_count, card_count) <= 1:
                        continue
                    existing = insight_by_community.get(community.community_id)
                    if not force and not _community_needs_insight_refresh(community, existing, now=now):
                        continue
                    candidates.append(
                        _InsightCandidate(
                            community=community,
                            existing=existing,
                            source_count=source_count,
                            cognitive_card_count=card_count,
                            assignment_count=assignment_count,
                        )
                    )
                langfuse_update_span(
                    output={
                        "loaded": len(communities),
                        "existing_insights": len(insights),
                        "selected": len(candidates),
                        "candidate_ids": [item.community.community_id for item in candidates[:20]],
                    },
                    status_message="completed",
                )
                return candidates

    async def _refresh_one(self, candidate: _InsightCandidate) -> None:
        community = candidate.community
        metadata = {
            "community_id": community.community_id,
            "title": community.title,
            "source_count": candidate.source_count,
            "cognitive_card_count": candidate.cognitive_card_count,
            "assignment_count": candidate.assignment_count,
        }
        with langfuse_observation(
            name="kg.community_insight.refresh_one",
            as_type="span",
            input=metadata,
            metadata=metadata,
        ):
            try:
                materials = self._load_materials(community)
                if len(materials) <= 1:
                    logger.info("[kg_community_insight_refresh] community 材料不足，跳过: %s", community.community_id)
                    langfuse_update_span(
                        output={"skipped": True, "reason": "insufficient_materials", "materials": len(materials)},
                        status_message="insufficient_materials",
                    )
                    return

                data, response = await self._generate_insight(community=community, materials=materials)
                report = str(data.get("insight_full_report") or "").strip()
                report_json = data.get("report_json") if isinstance(data.get("report_json"), dict) else {}
                retried = False
                if len(report) < 80:
                    retried = True
                    data, response = await self._generate_insight(
                        community=community,
                        materials=materials,
                        retry_reason=f"上一版报告无效或极短: chars={len(report)}",
                    )
                    report = str(data.get("insight_full_report") or "").strip()
                    report_json = data.get("report_json") if isinstance(data.get("report_json"), dict) else {}
                if len(report) < 80:
                    raise RuntimeError(f"community insight report invalid or too short: {community.community_id}; chars={len(report)}")

                insight = self._save_insight(candidate, report=report, report_json=report_json, response=response)
                await self._upsert_milvus(community=community, insight=insight)
                output = {
                    "community_id": community.community_id,
                    "insight_id": insight.insight_id,
                    "insight_version": insight.insight_version,
                    "materials": len(materials),
                    "report_chars": len(report),
                    "cache_hit": response.cache_hit,
                    "retried": retried,
                }
                langfuse_update_span(output=output, status_message="completed")
                logger.info(
                    "[kg_community_insight_refresh] 完成 community=%s version=%s report_chars=%s cache_hit=%s",
                    community.community_id,
                    insight.insight_version,
                    len(report),
                    response.cache_hit,
                )
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _generate_insight(
        self,
        *,
        community: KnowledgeGraphCommunity,
        materials: list[dict[str, Any]],
        retry_reason: str = "",
    ) -> tuple[dict[str, Any], LLMProxyResponse]:
        prompt = _build_prompt(community, materials, retry_reason=retry_reason)
        metadata = {
            "task": "kg_community_insight",
            "adapter_name": community.adapter_name,
            "projection": community.projection,
            "community_id": community.community_id,
            "material_count": len(materials),
            "retry_reason": retry_reason,
        }
        model = resolve_kg_llm_model("kg_community_insight")
        response = await self._llm.generate(
            LLMProxyRequest(
                prompt=prompt,
                model=model,
                json_schema=_INSIGHT_SCHEMA,
                temperature=0.0,
                max_tokens=COMMUNITY_INSIGHT_MAX_TOKENS,
                metadata=metadata,
                use_cache=not retry_reason,
            )
        )
        data = _parse_json_object_from_response(response)
        if data is None:
            raise RuntimeError(f"community insight output is not object: {community.community_id}")
        return data, response

    def _load_materials(self, community: KnowledgeGraphCommunity) -> list[dict[str, Any]]:
        with langfuse_observation(
            name="kg.community_insight.materials.load",
            as_type="span",
            input={"community_id": community.community_id, "max_materials": COMMUNITY_INSIGHT_MAX_MATERIALS},
        ):
            metrics = community.metrics or {}
            assigned_intents = metrics.get("assigned_intents")
            assignment_contexts = _assignment_contexts(metrics.get("assignments"))
            if isinstance(assigned_intents, list) and assigned_intents:
                materials = [
                    _trim_material(_with_assignment_context(item, assignment_contexts))
                    for item in assigned_intents[:COMMUNITY_INSIGHT_MAX_MATERIALS]
                    if isinstance(item, dict)
                ]
                langfuse_update_span(
                    output={
                        "source": "community.metrics.assigned_intents",
                        "materials": len(materials),
                        "assignment_contexts": len(assignment_contexts),
                    },
                    status_message="completed",
                )
                return materials

            card_ids = [str(item) for item in (metrics.get("cognitive_card_ids") or []) if item]
            with get_session(self._target) as session:
                assignments = session.scalars(
                    select(KnowledgeCommunityAssignment)
                    .where(KnowledgeCommunityAssignment.community_id == community.community_id)
                    .where(KnowledgeCommunityAssignment.status == "active")
                    .limit(COMMUNITY_INSIGHT_MAX_MATERIALS)
                ).all()
                if assignments:
                    materials = [
                        _trim_material(_material_from_assignment(assignment))
                        for assignment in assignments
                    ]
                    langfuse_update_span(
                        output={
                            "source": "pg.assignments.topic_intent",
                            "assignments": len(assignments),
                            "materials": len(materials),
                        },
                        status_message="completed",
                    )
                    return materials
                if not card_ids:
                    card_ids = [assignment.cognitive_card_id for assignment in assignments if assignment.cognitive_card_id]
                cards = session.scalars(
                    select(KnowledgeCognitiveCard)
                    .where(KnowledgeCognitiveCard.cognitive_card_id.in_(card_ids[:COMMUNITY_INSIGHT_MAX_MATERIALS]))
                ).all() if card_ids else []

            materials = [
                _trim_material(
                    {
                        "cognitive_card_id": card.cognitive_card_id,
                        "source_id": card.source_id,
                        "evidence_id": card.evidence_id,
                        "primary_chunk_id": card.primary_chunk_id,
                        "summary": card.summary,
                        "title_candidates": card.title_candidates,
                        "topic_intents": card.topic_intents,
                        "risk_signals": card.risk_signals,
                        "local_impact_signals": card.local_impact_signals,
                        "actor_signals": card.actor_signals,
                        "payload": card.payload,
                    }
                )
                for card in cards
            ]
            langfuse_update_span(
                output={
                    "source": "community.metrics.cognitive_card_ids",
                    "card_ids": len(card_ids),
                    "cards": len(cards),
                    "materials": len(materials),
                },
                status_message="completed",
            )
            return materials

    def _save_insight(
        self,
        candidate: _InsightCandidate,
        *,
        report: str,
        report_json: dict[str, Any],
        response: LLMProxyResponse,
    ) -> KnowledgeCommunityInsight:
        community = candidate.community
        now = datetime.now(timezone.utc)
        insight_id = _insight_id(community.community_id)
        with langfuse_observation(
            name="kg.community_insight.pg.save",
            as_type="span",
            input={
                "community_id": community.community_id,
                "insight_id": insight_id,
                "report_chars": len(report),
            },
        ):
            with get_session(self._target) as session:
                existing = session.scalar(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id == community.community_id)
                )
                created = existing is None
                if existing is None:
                    existing = KnowledgeCommunityInsight(
                        insight_id=insight_id,
                        community_id=community.community_id,
                        adapter_name=community.adapter_name,
                        projection=community.projection,
                        insight_version=1,
                    )
                    session.add(existing)
                else:
                    existing.insight_version = int(existing.insight_version or 0) + 1

                existing.title = community.title
                existing.insight_full_report = report
                existing.report_json = report_json
                existing.source_count = candidate.source_count
                existing.cognitive_card_count = candidate.cognitive_card_count
                existing.assignment_count = candidate.assignment_count
                existing.evidence_ids = list(community.evidence_ids or [])
                existing.chunk_ids = list(community.chunk_ids or [])
                existing.cognitive_card_ids = [str(item) for item in (community.metrics or {}).get("cognitive_card_ids") or [] if item]
                existing.status = "active"
                existing.error_message = ""
                existing.payload = {
                    "llm_usage": dict(response.usage or {}),
                    "cache_hit": response.cache_hit,
                    "model": resolve_kg_llm_model("kg_community_insight"),
                    "generated_at": now.isoformat(),
                    "quality_diagnostics": _quality_diagnostics(report=report, report_json=report_json),
                }
                existing.updated_at = now

                db_community = session.get(KnowledgeGraphCommunity, community.community_id)
                if db_community is not None:
                    db_community.last_insight_generated_at = now

                session.flush()
                session.refresh(existing)
                langfuse_update_span(
                    output={
                        "created": created,
                        "insight_id": existing.insight_id,
                        "insight_version": existing.insight_version,
                        "quality_diagnostics": existing.payload.get("quality_diagnostics"),
                    },
                    status_message="completed",
                )
                session.expunge(existing)
                return existing

    def _mark_failed(self, community: KnowledgeGraphCommunity, error: str) -> None:
        with langfuse_observation(
            name="kg.community_insight.pg.mark_failed",
            as_type="span",
            input={"community_id": community.community_id, "error": error[:500]},
            level="ERROR",
        ):
            with get_session(self._target) as session:
                existing = session.scalar(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id == community.community_id)
                )
                created = existing is None
                if existing is None:
                    existing = KnowledgeCommunityInsight(
                        insight_id=_insight_id(community.community_id),
                        community_id=community.community_id,
                        adapter_name=community.adapter_name,
                        projection=community.projection,
                        title=community.title,
                        status="error",
                    )
                    session.add(existing)
                existing.status = "error"
                existing.error_message = error[:2000]
                langfuse_update_span(
                    output={"created": created, "status": "error"},
                    level="ERROR",
                    status_message=error[:500],
                )

    async def _upsert_milvus(self, *, community: KnowledgeGraphCommunity, insight: KnowledgeCommunityInsight) -> None:
        with langfuse_observation(
            name="kg.community_insight.milvus.upsert",
            as_type="span",
            input={
                "community_id": community.community_id,
                "insight_id": insight.insight_id,
                "collection_role": MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                "report_chars": len(insight.insight_full_report or ""),
            },
        ):
            search_text = _insight_retrieval_text(insight)
            document = MilvusHybridDocument(
                chunk_id=insight.insight_id,
                evidence_id=insight.evidence_ids[0] if insight.evidence_ids else "",
                text=search_text,
                metadata={
                    "target_id": insight.insight_id,
                    "target_type": "community_insight",
                    "document_type": "community_insight",
                    "source_type": "kg_community_insight",
                    "source_id": community.community_id,
                    "community_id": community.community_id,
                    "community_title": community.title,
                    "projection": community.projection,
                    "insight_version": insight.insight_version,
                    "source_count": insight.source_count,
                    "cognitive_card_count": insight.cognitive_card_count,
                    "assignment_count": insight.assignment_count,
                    "insight_full_report_chars": len(insight.insight_full_report or ""),
                    "retrieval_text_chars": len(search_text),
                    "retrieval_text_source": "insight_full_report+report_json",
                    "cited_evidence_ids": insight.evidence_ids,
                    "cited_chunk_ids": insight.chunk_ids,
                    "cognitive_card_ids": insight.cognitive_card_ids,
                    "latest_evidence_at": (community.metrics or {}).get("latest_source_published_at") or "",
                    "earliest_evidence_at": (community.metrics or {}).get("earliest_source_published_at") or "",
                    "event_time_start": (community.metrics or {}).get("earliest_source_published_at") or "",
                    "event_time_end": (community.metrics or {}).get("latest_source_published_at") or "",
                },
            )
            vectors = await embed_texts([document.text])
            if len(vectors) != 1:
                raise RuntimeError(f"community insight embedding failed: {community.community_id}")
            self._vector_store.upsert_documents_by_role(
                adapter_name=community.adapter_name,
                target=self._target,
                documents_by_role={MILVUS_COLLECTION_COMMUNITY_INSIGHT: [document]},
                vectors_by_role={MILVUS_COLLECTION_COMMUNITY_INSIGHT: vectors},
                embedding_model=EMBEDDING_MODEL,
                kg_version="community_insight_v1",
            )
            langfuse_update_span(
                output={
                    "documents": 1,
                    "vectors": len(vectors),
                    "retrieval_text_chars": len(search_text),
                    "embedding_model": EMBEDDING_MODEL,
                    "collection_role": MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                },
                status_message="completed",
            )


async def _renew_lock_loop(lock, stop_event: asyncio.Event, lost_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS)
        except asyncio.TimeoutError:
            renewed = lock.renew()
            if renewed:
                logger.info("[kg_community_insight_refresh] 分布式锁续租成功 ttl=%ss", COMMUNITY_INSIGHT_LOCK_TTL_SECONDS)
                continue
            logger.error("[kg_community_insight_refresh] 分布式锁续租失败，当前锁持有者可能已经变化")
            lost_event.set()
            return


def _community_counts(community: KnowledgeGraphCommunity) -> tuple[int, int, int]:
    metrics = community.metrics or {}
    source_count = _int_metric(metrics.get("unique_source_count") or metrics.get("source_count"), len(community.evidence_ids or []))
    card_count = _int_metric(
        metrics.get("cognitive_card_count") or metrics.get("assigned_intent_count"),
        len(metrics.get("cognitive_card_ids") or []),
    )
    assignment_count = _int_metric(metrics.get("assignment_count") or metrics.get("assigned_intent_count"), 0)
    return source_count, card_count, assignment_count


def _community_needs_insight_refresh(
    community: KnowledgeGraphCommunity,
    existing: KnowledgeCommunityInsight | None,
    *,
    now: datetime,
) -> bool:
    if existing is None or existing.status != "active":
        return True
    generated_at = community.last_insight_generated_at or existing.updated_at
    if generated_at is None:
        return True
    updated_at = community.updated_at
    if updated_at is None or updated_at <= generated_at:
        return False
    age_seconds = (now - updated_at).total_seconds()
    return age_seconds >= COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS


def _build_prompt(
    community: KnowledgeGraphCommunity,
    materials: list[dict[str, Any]],
    *,
    retry_reason: str = "",
) -> str:
    metrics = community.metrics or {}
    payload = {
        "community": {
            "community_id": community.community_id,
            "title": community.title,
            "scope": metrics.get("scope") or "",
            "source_count": metrics.get("source_count") or len(community.evidence_ids or []),
            "cognitive_card_count": metrics.get("cognitive_card_count") or len(metrics.get("cognitive_card_ids") or []),
            "assignment_count": metrics.get("assignment_count") or metrics.get("assigned_intent_count") or 0,
            "time_range": {
                "start": metrics.get("earliest_source_published_at") or "",
                "end": metrics.get("latest_source_published_at") or "",
            },
        },
        "community_signals": _community_signal_summary(metrics),
        "materials_format": (
            "每行一条材料；字段含义：src=来源ID, time=发布时间, fit=匹配类型, w=归属权重, t=认知主题, s=摘要, "
            "sig=少量关键驱动/影响/风险信号, why=仅特殊归属或低置信材料的归属理由摘要。"
        ),
        "materials_text": _compact_materials_text(materials),
    }
    retry_lines = []
    if retry_reason:
        retry_lines = [
            "",
            f"重试原因：{retry_reason}",
            "本次需要输出有效报告，但不要为了篇幅扩写。",
        ]
    lines = [
        "请基于以下 community 上下文生成 Community Insight 高级认知报告。",
        "",
        "你的任务不是总结单条新闻，也不是罗列 community 成员。",
        "所有判断只能基于输入材料中明确提供的信息；不得引入输入之外的行情、价格、公司、时间、政策或外部知识。",
        "",
        "报告必须回答：这些来源合在一起说明了什么；支撑判断的输入信号类型是什么；关键驱动、传导链条和影响对象是什么；限制条件、不确定性或反转条件是什么；Agent 应如何使用这份报告。",
        "",
        "输出对象是检索和决策 Agent，不是普通消费者、投资者或交易员。不要写“消费者应关注”“建议投资者”等表述。",
        "报告长度由输入材料复杂度自然决定。不要为了凑字数扩写；但如果 community 包含大量来源、多个强主线或多个产业环节，不要压缩成几句短行情评论。",
        "信息保真优先于短小。凡是对 Agent 后续检索、判断边界、识别强弱信号有帮助的代表性证据、传导链条和限制条件，都应该保留。",
        "",
        "写作要求：",
        "- 输出一份完整高级认知报告，不要逐条复述输入，也不要只输出口号式结论。",
        "- 先归纳由多条材料共同支撑的主线。主线数量由材料决定：少量材料可以 2-3 条，大型 community 可以 4-8 条。",
        "- 每条主线需要说明：代表性证据、输入信号类型、驱动因素、传导链条、影响对象、确定性强弱和边界。",
        "- 对代表性证据要保留可检索细节，例如关键公司、产品、技术路线、价格/订单/产能/资本开支/政策/业绩等输入中已经出现的信息。",
        "- 引用代表性证据时必须使用材料中的 src，例如 ft_news:123，不要使用材料行号或 #1/#2 这类序号引用。",
        "- 输入材料行中的 time、fit、w、sig、why 表示该材料与当前 community 的时间、匹配类型、归属强弱、关键信号和必要归属解释；它们可用于判断边界，但不是原始事实证据。",
        "- why 只会出现在新建主题、父主题上提、相邻上下文、宽泛父类、低权重或低置信材料中；没有 why 的材料按 t/s/sig 判断即可。",
        "- 对强主线、中等主线、弱信号或相邻上下文要分清楚；不要把低权重、单条、缺少机制的材料提升成核心结论。",
        "- 如果同一 community 横跨多个产业环节，需要说明这些环节之间是否存在传导关系；没有传导关系时要明确只是并列或相邻上下文。",
        "- 不要把 parent_themes、broad_topics、risk_type 简单拼接成报告。",
        "- 不要把短期行情、概念股涨跌或单条研报观点写成主结论；除非输入中存在机制、供需、订单、价格或产能等支撑。",
        "- 如果 community 边界偏宽，报告应说明哪些主线较强、哪些只是相邻上下文，并在 report_json.quality_flags 中标记。",
        "- 强弱、冲突、噪声是条件触发的判断维度，不是固定输出章节。",
        "- 只有输入材料支持时，才解释冲突、弱信号或噪声；不要为了格式强行生成这些内容。",
        "- 如果输入材料显示某些信号只有表面变化描述，缺少驱动、机制、影响路径或证据支撑，应明确降权。",
        "- 风险和反转条件必须来自输入材料可支持的机制推演，并说明它们会影响哪条主线。",
        "- 不得创造输入中不存在的时间、数字、公司名、政策名、交易结论或因果关系。不要把输入中的“预期、观点、研报判断”改写成已经发生的事实。",
        "- 不输出买入、卖出、持仓建议。",
    ]
    lines.extend(retry_lines)
    lines.extend(
        [
            "",
            "请输出 JSON 对象，字段为 insight_full_report 和 report_json。",
            "",
            "输入材料：",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    return "\n".join(lines)


def _compact_materials_text(materials: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for material in materials:
        parts = [
            f"src={_compact_text(material.get('source_id'), 64)}",
        ]
        time_text = _compact_text(material.get("source_published_at"), 32)
        if time_text:
            parts.append(f"time={time_text}")
        fit_type = _compact_text(material.get("assignment_fit_type") or material.get("fit_type"), 32)
        if fit_type:
            parts.append(f"fit={fit_type}")
        if material.get("assignment_weight") is not None:
            parts.append(f"w={material.get('assignment_weight')}")
        title = _compact_text(material.get("title_candidate"), 90)
        if title:
            parts.append(f"t={title}")
        summary = _compact_text(material.get("summary"), 0)
        if summary:
            parts.append(f"s={summary}")
        signal = _compact_material_signal(material)
        if signal:
            parts.append(f"sig={signal}")
        reason = _compact_assignment_reason(material)
        if reason:
            parts.append(f"why={reason}")
        lines.append(" | ".join(part for part in parts if part))
    return "\n".join(lines)


def _compact_material_signal(material: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("driver", "event_thread", "impact_target", "risk_type"):
        values.extend(_compact_list(material.get(key), item_limit=2, item_chars=22))
        if len(values) >= 3:
            break
    return ",".join(_ordered_unique_texts(values)[:3])


def _compact_assignment_reason(material: dict[str, Any]) -> str:
    reason = _compact_text(material.get("assignment_reason"), 160)
    if not reason:
        return ""
    action = str(material.get("assignment_action") or "")
    fit_type = str(material.get("assignment_fit_type") or "")
    weight = _as_float(material.get("assignment_weight"))
    confidence = _as_float(material.get("assignment_confidence"))
    keep_reason = (
        action in {"create_new", "create_parent_and_absorb_existing"}
        or fit_type in {"adjacent_context", "broader_parent", "new_parent_topic"}
        or (weight is not None and weight < 0.5)
        or (confidence is not None and confidence < 0.75)
    )
    return reason if keep_reason else ""


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _compact_list(value: Any, *, item_limit: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact_text(item, item_chars)
        if text:
            result.append(text)
        if len(result) >= item_limit:
            break
    return result


def _ordered_unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except Exception:
        return None


def _community_signal_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_themes": _top_items(metrics.get("parent_themes"), 40),
        "topic_tags": _top_items(metrics.get("topic_tags"), 40),
        "event_threads": _top_items(metrics.get("event_threads"), 40),
        "impact_tags": _top_items(metrics.get("impact_tags"), 40),
        "risk_tags": _top_items(metrics.get("risk_tags"), 40),
        "future_coverage": _top_items(metrics.get("future_coverage"), 40),
        "coverage_contract": str(metrics.get("coverage_contract") or "")[:1200],
        "maturity_level": metrics.get("maturity_level") or "",
        "topic_diversity_count": metrics.get("topic_diversity_count") or 0,
        "high_weight_assignment_count": metrics.get("high_weight_assignment_count") or 0,
        "medium_weight_assignment_count": metrics.get("medium_weight_assignment_count") or 0,
        "low_weight_assignment_count": metrics.get("low_weight_assignment_count") or 0,
    }


def _top_items(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _quality_diagnostics(*, report: str, report_json: dict[str, Any]) -> dict[str, Any]:
    basis = report_json.get("basis")
    reversal_conditions = report_json.get("reversal_conditions")
    use_boundary = report_json.get("use_boundary")
    core_thesis = report_json.get("core_thesis")
    warnings: list[str] = []
    if not str(core_thesis or "").strip():
        warnings.append("missing_core_thesis")
    if not isinstance(basis, list) or not basis:
        warnings.append("missing_basis")
    if not isinstance(reversal_conditions, list):
        warnings.append("missing_reversal_conditions")
    if not str(use_boundary or "").strip():
        warnings.append("missing_use_boundary")
    if len(report.strip()) < 120:
        warnings.append("report_too_short_to_use")
    return {
        "report_chars": len(report),
        "basis_count": len(basis) if isinstance(basis, list) else 0,
        "reversal_condition_count": len(reversal_conditions) if isinstance(reversal_conditions, list) else 0,
        "warnings": warnings,
    }


def _insight_retrieval_text(insight: KnowledgeCommunityInsight) -> str:
    """把报告和结构化字段机械拼接成 Milvus 可检索文本。"""

    report_json = insight.report_json if isinstance(insight.report_json, dict) else {}
    lines = [
        f"Community Insight: {insight.title or insight.community_id}",
        "",
        str(insight.insight_full_report or "").strip(),
    ]
    core_thesis = str(report_json.get("core_thesis") or "").strip()
    if core_thesis:
        lines.extend(["", "核心论点:", core_thesis])
    _append_structured_items(lines, "依据:", report_json.get("basis"), keys=("signal_type", "source", "support"))
    _append_structured_items(lines, "弱信号:", report_json.get("weak_signals"))
    _append_structured_items(lines, "冲突:", report_json.get("conflicts"))
    _append_structured_items(lines, "反转条件:", report_json.get("reversal_conditions"))
    _append_structured_items(lines, "质量边界:", report_json.get("quality_flags"))
    use_boundary = str(report_json.get("use_boundary") or "").strip()
    if use_boundary:
        lines.extend(["", "Agent使用边界:", use_boundary])
    return "\n".join(line for line in lines if line is not None).strip()


def _append_structured_items(
    lines: list[str],
    title: str,
    value: Any,
    *,
    keys: tuple[str, ...] = (),
) -> None:
    if not isinstance(value, list) or not value:
        return
    lines.extend(["", title])
    for item in value:
        if isinstance(item, dict):
            parts = [str(item.get(key) or "").strip() for key in keys]
            text = "；".join(part for part in parts if part)
        else:
            text = str(item or "").strip()
        if text:
            lines.append(f"- {text}")


def _assignment_contexts(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    contexts: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        context = _assignment_context(item)
        for key in _assignment_context_keys(item):
            if key and key not in contexts:
                contexts[key] = context
    return contexts


def _with_assignment_context(item: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not contexts:
        return item
    key_candidates = _assignment_context_keys(item)
    for key in key_candidates:
        context = contexts.get(key)
        if context:
            return {**item, **context}
    return item


def _material_from_assignment(assignment: KnowledgeCommunityAssignment) -> dict[str, Any]:
    topic_intent = assignment.topic_intent if isinstance(assignment.topic_intent, dict) else {}
    return {
        **topic_intent,
        "intent_id": assignment.intent_id,
        "cognitive_card_id": assignment.cognitive_card_id,
        **_assignment_context(
            {
                "action": assignment.action,
                "weight": assignment.weight,
                "confidence": assignment.confidence,
                "fit_type": _fit_type_from_reason(assignment.reason),
                "reason": assignment.reason,
                "resolved_community_id": assignment.community_id,
                "assignment_id": assignment.assignment_id,
            }
        ),
    }


def _assignment_context(item: dict[str, Any]) -> dict[str, Any]:
    action = _clean_context_text(item.get("action"))
    return {
        "assignment_action": action,
        "assignment_relation_type": _assignment_relation_type(action),
        "assignment_weight": item.get("weight"),
        "assignment_confidence": item.get("confidence"),
        "assignment_fit_type": _clean_context_text(item.get("fit_type")) or _fit_type_from_reason(str(item.get("reason") or "")),
        "assignment_reason": _clean_context_text(item.get("reason")),
        "assignment_resolved_community_id": _clean_context_text(
            item.get("resolved_community_id") or item.get("community_id")
        ),
        "assignment_absorb_community_ids": item.get("absorb_community_ids") if isinstance(item.get("absorb_community_ids"), list) else [],
        "assignment_absorbed_from_community_id": _clean_context_text(item.get("absorbed_from_community_id")),
    }


def _assignment_context_keys(item: dict[str, Any]) -> list[str]:
    intent_id = str(item.get("intent_id") or "").strip()
    card_id = str(item.get("cognitive_card_id") or "").strip()
    intent_index = item.get("intent_index")
    keys = []
    if intent_id:
        keys.append(f"intent:{intent_id}")
        return keys
    if card_id and intent_index not in (None, ""):
        keys.append(f"card_intent:{card_id}:{intent_index}")
        return keys
    if card_id:
        keys.append(f"card:{card_id}")
    return keys


def _fit_type_from_reason(reason: str) -> str:
    prefix = "fit_type="
    if not reason.startswith(prefix):
        return ""
    value = reason[len(prefix) :].split(";", 1)[0].strip()
    return value


def _assignment_relation_type(action: str) -> str:
    if action == "create_new":
        return "created_new_topic"
    if action == "attach_existing":
        return "attached_to_existing_topic"
    if action == "create_parent_and_absorb_existing":
        return "created_parent_and_absorbed_existing_topics"
    return action or ""


def _clean_context_text(value: Any) -> str:
    return str(value or "").strip()[:500]


def _trim_material(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "assignment_action",
        "assignment_relation_type",
        "assignment_weight",
        "assignment_confidence",
        "assignment_fit_type",
        "assignment_reason",
        "assignment_resolved_community_id",
        "assignment_absorb_community_ids",
        "assignment_absorbed_from_community_id",
        "cognitive_card_id",
        "intent_id",
        "source_id",
        "source_published_at",
        "assignment_weight",
        "fit_type",
        "title_candidate",
        "summary",
        "parent_themes",
        "broad_topics",
        "mid_topics",
        "event_thread",
        "driver",
        "event_action",
        "impact_target",
        "risk_type",
        "importance",
        "topic_intents",
        "risk_signals",
        "local_impact_signals",
        "actor_signals",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in item:
            continue
        value = item.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "summary" and isinstance(value, str):
            result[key] = value.replace("\n", " ").replace("\r", " ").strip()
            continue
        result[key] = _clip_value(value)
    return result


def _clip_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_clip_value(item) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key): _clip_value(item) for key, item in list(value.items())[:20]}
    return value


def _parse_json_object_from_response(response: LLMProxyResponse) -> dict[str, Any] | None:
    if isinstance(response.structured_output, dict):
        return response.structured_output
    if isinstance(response.structured_output, str):
        return _parse_json_object(response.structured_output)
    return _parse_json_object(response.text)


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _insight_id(community_id: str) -> str:
    return f"kgi:{community_id}"[:220]


def _int_metric(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
