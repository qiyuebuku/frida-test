"""旧 Community Assignment 的原文证据回填服务。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.application.services.community_insight_service import CommunityInsightService
from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.cognitive_index import cognitive_card_document
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_CHUNK,
    SemanticVectorDocument,
)
from src.infrastructure.connections import get_session
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
    KnowledgeGraphCommunity,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusTypedHybridStore
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever

logger = logging.getLogger(__name__)


ASSIGNMENT_EVIDENCE_BACKFILL_SYSTEM_PROMPT = "\n".join(
    [
        "你负责为已有 topic intent 选择可核验的原文证据。",
        "你不能修改主题意图、重新分类、判断 community 归属，也不能复述 Assignment reason。",
        "source_segments 按原文顺序编号；只选择能直接支持该 topic intent 的最小连续句段范围。",
        "范围内的每个句段都必须直接支持 topic intent；不要为了上下文完整而加入标题、相邻新闻或背景句。",
        "最多选择 3 个连续句段；如果意图只能由多处分散原文拼凑支持，必须输出 supported=false。",
        "禁止改写、摘要或生成事实判断；程序会根据句段编号从原文复制 evidence_span。",
        "如果 source_segments 不能直接支持该 topic intent，supported=false，start_segment=0，end_segment=0。",
        "只输出符合 JSON Schema 的对象，不要输出解释过程。",
    ]
)


_GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supported", "start_segment", "end_segment"],
    "properties": {
        "supported": {"type": "boolean"},
        "start_segment": {"type": "integer", "minimum": 0},
        "end_segment": {"type": "integer", "minimum": 0},
    },
}


@dataclass(frozen=True)
class AssignmentEvidenceBackfillCommand:
    adapter_name: str = "financial"
    target: str = "prod"
    community_ids: tuple[str, ...] = ()
    reprocess_source_ids: tuple[str, ...] = ()
    limit: int = 100
    scan_limit: int = 5000
    concurrency: int = 4
    dry_run: bool = True
    refresh_insights: bool = False


@dataclass(frozen=True)
class _BackfillCandidate:
    cognitive_card_id: str
    intent_index: int
    intent_id: str
    primary_chunk_id: str
    source_id: str
    topic_intent: dict[str, Any]
    card_topic_intent: dict[str, Any]
    assignment_ids: tuple[str, ...]
    community_ids: tuple[str, ...]
    existing_grounding: dict[str, str] | None


@dataclass(frozen=True)
class _BackfillOutcome:
    candidate: _BackfillCandidate
    status: str
    evidence_span: str = ""
    error: str = ""
    llm_calls: int = 0


@dataclass(frozen=True)
class _SourceSegment:
    segment_id: int
    start: int
    end: int
    text: str


class CommunityAssignmentEvidenceBackfillService:
    """按旧 intent 回填证据，不重新执行 community 归属裁决。"""

    def __init__(
        self,
        *,
        target: str = "prod",
        llm_service: Any | None = None,
        vector_store: Any | None = None,
        semantic_retriever: Any | None = None,
        repository: KnowledgeRepositoryImpl | None = None,
    ) -> None:
        self._target = target
        self._llm = llm_service or get_llm_gateway_service()
        self._vector_store = vector_store or MilvusTypedHybridStore()
        self._semantic_retriever = semantic_retriever or MilvusSemanticHybridRetriever(store=self._vector_store)
        self._repository = repository or KnowledgeRepositoryImpl(target=target)

    async def backfill(self, command: AssignmentEvidenceBackfillCommand) -> dict[str, Any]:
        if command.target != self._target:
            raise ValueError(f"service target={self._target} 与 command target={command.target} 不一致")
        if command.limit <= 0 or command.scan_limit <= 0:
            raise ValueError("limit 和 scan_limit 必须大于 0")

        metadata = {
            "adapter_name": command.adapter_name,
            "target": command.target,
            "community_ids": list(command.community_ids),
            "reprocess_source_ids": list(command.reprocess_source_ids),
            "limit": command.limit,
            "scan_limit": command.scan_limit,
            "concurrency": command.concurrency,
            "dry_run": command.dry_run,
            "refresh_insights": command.refresh_insights,
        }
        with langfuse_propagation_context(
            trace_name="kg.assignment_evidence_backfill",
            tags=["kg", "assignment", "evidence_backfill"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.assignment_evidence_backfill",
                as_type="chain",
                input=metadata,
                metadata=metadata,
            ):
                candidates, scan_stats = self._load_candidates(command)
                chunk_texts = self._load_chunk_texts(candidates, command)
                outcomes = await self._resolve_candidates(candidates, chunk_texts, command)

                ready = [item for item in outcomes if item.status in {"reused", "generated"}]
                unsupported = [item for item in outcomes if item.status == "unsupported"]
                persistable = [*ready, *unsupported]
                persistence = {
                    "cards_updated": 0,
                    "assignments_updated": 0,
                    "communities_marked_stale": 0,
                    "community_ids": [],
                }
                milvus_cards_upserted = 0
                insight_refresh: dict[str, Any] | None = None
                if not command.dry_run and persistable:
                    persistence = self._persist_outcomes(persistable, command)
                    card_ids = [item.candidate.cognitive_card_id for item in persistable]
                    milvus_cards_upserted = await self._upsert_cognitive_cards(card_ids, command)
                    self._clear_pending_index_markers(card_ids, command)
                    if command.refresh_insights and persistence["community_ids"]:
                        insight_refresh = await CommunityInsightService(target=command.target).refresh_community_ids(
                            persistence["community_ids"],
                            force=True,
                        )

                result = {
                    **metadata,
                    "scan": scan_stats,
                    "candidates": len(candidates),
                    "chunk_documents_found": len(chunk_texts),
                    "candidate_chunks_resolved": sum(
                        1 for item in candidates if item.primary_chunk_id in chunk_texts
                    ),
                    "status_counts": _status_counts(outcomes),
                    "llm_calls": sum(item.llm_calls for item in outcomes),
                    "persistence": persistence,
                    "milvus_cards_upserted": milvus_cards_upserted,
                    "insight_refresh": insight_refresh,
                    "samples": [
                        {
                            "cognitive_card_id": item.candidate.cognitive_card_id,
                            "intent_index": item.candidate.intent_index,
                            "source_id": item.candidate.source_id,
                            "community_ids": list(item.candidate.community_ids),
                            "status": item.status,
                            "evidence_span": item.evidence_span[:180],
                            "error": item.error[:300],
                        }
                        for item in outcomes[:20]
                    ],
                }
                langfuse_update_span(output=result, status_message="completed")
                return result

    def _load_candidates(
        self,
        command: AssignmentEvidenceBackfillCommand,
    ) -> tuple[list[_BackfillCandidate], dict[str, int]]:
        with langfuse_observation(
            name="kg.assignment_evidence_backfill.pg.load_candidates",
            as_type="span",
            input={
                "adapter_name": command.adapter_name,
                "community_ids": list(command.community_ids),
                "scan_limit": command.scan_limit,
            },
        ):
            with get_session(command.target) as session:
                query = (
                    select(KnowledgeCommunityAssignment, KnowledgeCognitiveCard)
                    .join(
                        KnowledgeCognitiveCard,
                        KnowledgeCognitiveCard.cognitive_card_id
                        == KnowledgeCommunityAssignment.cognitive_card_id,
                    )
                    .where(KnowledgeCommunityAssignment.adapter_name == command.adapter_name)
                    .where(KnowledgeCommunityAssignment.status == "active")
                    .where(KnowledgeCognitiveCard.status == "active")
                    .order_by(
                        KnowledgeCommunityAssignment.cognitive_card_id,
                        KnowledgeCommunityAssignment.intent_index,
                        KnowledgeCommunityAssignment.assignment_id,
                    )
                    .limit(command.scan_limit)
                )
                if command.community_ids:
                    query = query.where(
                        KnowledgeCommunityAssignment.community_id.in_(list(command.community_ids))
                    )
                rows = session.execute(query).all()

        grouped: dict[tuple[str, int], dict[str, Any]] = {}
        for assignment, card in rows:
            key = (assignment.cognitive_card_id, int(assignment.intent_index or 0))
            group = grouped.setdefault(
                key,
                {
                    "card": card,
                    "assignments": [],
                },
            )
            group["assignments"].append(assignment)

        missing_candidates: list[_BackfillCandidate] = []
        already_complete = 0
        unsupported_complete = 0
        force_sources = set(command.reprocess_source_ids)
        for (_card_id, intent_index), group in grouped.items():
            card: KnowledgeCognitiveCard = group["card"]
            assignments: list[KnowledgeCommunityAssignment] = group["assignments"]
            card_intent = _card_intent_at(card.topic_intents, intent_index)
            assignment_intents = [
                dict(item.topic_intent or {})
                for item in assignments
                if isinstance(item.topic_intent, dict)
            ]
            existing_grounding = _best_existing_grounding([*assignment_intents, card_intent])
            every_assignment_grounded = bool(assignments) and all(
                _has_grounding(item.topic_intent) for item in assignments
            )
            card_grounded = _has_grounding(card_intent)
            pending_index = bool((card.payload or {}).get("assignment_evidence_backfill_pending"))
            force_reprocess = card.source_id in force_sources
            every_assignment_unsupported = bool(assignments) and all(
                _grounding_status(item.topic_intent) == "unsupported"
                for item in assignments
            )
            card_unsupported = _grounding_status(card_intent) == "unsupported"
            if every_assignment_unsupported and card_unsupported and not force_reprocess:
                unsupported_complete += 1
                continue
            if every_assignment_grounded and card_grounded and not pending_index and not force_reprocess:
                already_complete += 1
                continue
            topic_intent = next(
                (item for item in assignment_intents if _intent_has_semantics(item)),
                card_intent,
            )
            missing_candidates.append(
                _BackfillCandidate(
                    cognitive_card_id=card.cognitive_card_id,
                    intent_index=intent_index,
                    intent_id=str(assignments[0].intent_id or "") if assignments else "",
                    primary_chunk_id=card.primary_chunk_id,
                    source_id=card.source_id,
                    topic_intent=topic_intent,
                    card_topic_intent=card_intent,
                    assignment_ids=tuple(item.assignment_id for item in assignments),
                    community_ids=tuple(dict.fromkeys(item.community_id for item in assignments if item.community_id)),
                    existing_grounding=None if force_reprocess else existing_grounding,
                )
            )
        candidates = missing_candidates[: command.limit]
        stats = {
            "assignment_rows": len(rows),
            "unique_intents": len(grouped),
            "already_complete_intents": already_complete,
            "unsupported_intents": unsupported_complete,
            "missing_intents": len(missing_candidates),
            "selected_missing_intents": len(candidates),
        }
        return candidates, stats

    def _load_chunk_texts(
        self,
        candidates: list[_BackfillCandidate],
        command: AssignmentEvidenceBackfillCommand,
    ) -> dict[str, str]:
        chunk_ids = list(dict.fromkeys(item.primary_chunk_id for item in candidates if item.primary_chunk_id))
        if not chunk_ids:
            return {}
        with langfuse_observation(
            name="kg.assignment_evidence_backfill.milvus.load_chunks",
            as_type="retriever",
            input={"chunk_ids": chunk_ids[:50], "chunk_count": len(chunk_ids)},
        ):
            hits = self._vector_store.get_documents(
                collection_role=SEMANTIC_COLLECTION_CHUNK,
                adapter_name=command.adapter_name,
                target=command.target,
                target_ids=chunk_ids,
            )
            result = {
                str(hit.target_id): chunk_text
                for hit in hits
                for chunk_text in [_extract_chunk_text(str(hit.text or ""))]
                if chunk_text
            }
            langfuse_update_span(
                output={
                    "requested": len(chunk_ids),
                    "found": len(result),
                    "missing": [item for item in chunk_ids if item not in result][:50],
                },
                status_message="completed",
            )
            return result

    async def _resolve_candidates(
        self,
        candidates: list[_BackfillCandidate],
        chunk_texts: dict[str, str],
        command: AssignmentEvidenceBackfillCommand,
    ) -> list[_BackfillOutcome]:
        semaphore = asyncio.Semaphore(max(1, int(command.concurrency or 1)))

        async def resolve(candidate: _BackfillCandidate) -> _BackfillOutcome:
            chunk_text = chunk_texts.get(candidate.primary_chunk_id, "")
            if not chunk_text:
                return _BackfillOutcome(candidate=candidate, status="missing_chunk")
            if candidate.existing_grounding and _grounding_is_valid(candidate.existing_grounding, chunk_text):
                return _BackfillOutcome(
                    candidate=candidate,
                    status="reused",
                    evidence_span=candidate.existing_grounding["evidence_span"],
                )
            if command.dry_run:
                return _BackfillOutcome(candidate=candidate, status="planned")
            async with semaphore:
                try:
                    return await self._generate_grounding(candidate, chunk_text)
                except Exception as exc:
                    logger.exception(
                        "[assignment_evidence_backfill] LLM 回填失败 card=%s intent=%s",
                        candidate.cognitive_card_id,
                        candidate.intent_index,
                    )
                    return _BackfillOutcome(
                        candidate=candidate,
                        status="error",
                        error=f"{exc.__class__.__name__}: {exc}",
                    )

        return list(await asyncio.gather(*(resolve(candidate) for candidate in candidates)))

    async def _generate_grounding(
        self,
        candidate: _BackfillCandidate,
        chunk_text: str,
    ) -> _BackfillOutcome:
        validation_error = ""
        for attempt in range(1, 3):
            prompt = _build_grounding_prompt(
                candidate.topic_intent,
                chunk_text,
                validation_error=validation_error,
            )
            response = await self._llm.generate(
                LLMProxyRequest(
                    prompt=prompt,
                    system_prompt=ASSIGNMENT_EVIDENCE_BACKFILL_SYSTEM_PROMPT,
                    model=resolve_kg_llm_model("kg_cognitive_card"),
                    json_schema=_GROUNDING_SCHEMA,
                    temperature=0.0,
                    max_tokens=600,
                    metadata={
                        "task": "kg_assignment_evidence_backfill",
                        "cognitive_card_id": candidate.cognitive_card_id,
                        "intent_index": candidate.intent_index,
                        "source_id": candidate.source_id,
                        "attempt": attempt,
                    },
                    provider_options={"reasoning_effort": "low"},
                    use_cache=attempt == 1,
                )
            )
            data = _response_object(response)
            if not data:
                validation_error = "上一次没有返回有效 JSON 对象。"
                continue
            if not bool(data.get("supported")):
                return _BackfillOutcome(
                    candidate=candidate,
                    status="unsupported",
                    llm_calls=attempt,
                )
            evidence_span = _evidence_span_from_segment_selection(chunk_text, data)
            if evidence_span:
                return _BackfillOutcome(
                    candidate=candidate,
                    status="generated",
                    evidence_span=evidence_span,
                    llm_calls=attempt,
                )
            validation_error = "上一次句段范围无效。请选择存在、连续且不超过 3 个句段的范围。"
        return _BackfillOutcome(
            candidate=candidate,
            status="invalid_grounding",
            error=validation_error,
            llm_calls=2,
        )

    def _persist_outcomes(
        self,
        outcomes: list[_BackfillOutcome],
        command: AssignmentEvidenceBackfillCommand,
    ) -> dict[str, Any]:
        card_ids = list(dict.fromkeys(item.candidate.cognitive_card_id for item in outcomes))
        assignment_ids = list(
            dict.fromkeys(
                assignment_id
                for item in outcomes
                for assignment_id in item.candidate.assignment_ids
            )
        )
        community_ids = list(
            dict.fromkeys(
                community_id
                for item in outcomes
                for community_id in item.candidate.community_ids
            )
        )
        outcome_by_key = {
            (item.candidate.cognitive_card_id, item.candidate.intent_index): item
            for item in outcomes
        }
        now = datetime.now(timezone.utc)
        with get_session(command.target) as session:
            cards = session.scalars(
                select(KnowledgeCognitiveCard)
                .where(KnowledgeCognitiveCard.cognitive_card_id.in_(card_ids))
                .with_for_update()
            ).all()
            assignments = session.scalars(
                select(KnowledgeCommunityAssignment)
                .where(KnowledgeCommunityAssignment.assignment_id.in_(assignment_ids))
                .with_for_update()
            ).all()
            communities = session.scalars(
                select(KnowledgeGraphCommunity)
                .where(KnowledgeGraphCommunity.community_id.in_(community_ids))
                .with_for_update()
            ).all()

            cards_updated = 0
            for card in cards:
                intents = copy.deepcopy(list(card.topic_intents or []))
                changed = False
                card_outcomes = [
                    item
                    for key, item in outcome_by_key.items()
                    if key[0] == card.cognitive_card_id
                ]
                for outcome in card_outcomes:
                    position = (
                        outcome.candidate.intent_index - 1
                        if outcome.candidate.intent_index > 0
                        else outcome.candidate.intent_index
                    )
                    if position < 0 or position >= len(intents):
                        continue
                    if outcome.status in {"reused", "generated"}:
                        intents[position] = _set_card_intent_grounding(
                            intents[position],
                            evidence_span=outcome.evidence_span,
                        )
                    else:
                        intents[position] = _clear_card_intent_grounding(intents[position])
                    changed = True
                if not changed:
                    continue
                card.topic_intents = intents
                payload = dict(card.payload or {})
                payload["topic_intents"] = copy.deepcopy(intents)
                payload["assignment_evidence_backfill_pending"] = True
                card.payload = payload
                card.supporting_text = _supporting_text_from_card_intents(intents)
                card.updated_at = now
                cards_updated += 1

            assignments_updated = 0
            for assignment in assignments:
                outcome = outcome_by_key.get(
                    (assignment.cognitive_card_id, int(assignment.intent_index or 0))
                )
                if outcome is None:
                    continue
                topic_intent = dict(assignment.topic_intent or {})
                if outcome.status in {"reused", "generated"}:
                    topic_intent["evidence_span"] = outcome.evidence_span
                    topic_intent.pop("evidence_grounding_status", None)
                else:
                    topic_intent.pop("evidence_span", None)
                    topic_intent["evidence_grounding_status"] = "unsupported"
                topic_intent.pop("evidence_claim", None)
                assignment.topic_intent = topic_intent
                assignment.updated_at = now
                assignments_updated += 1

            for community in communities:
                community.updated_at = now

        return {
            "cards_updated": cards_updated,
            "assignments_updated": assignments_updated,
            "communities_marked_stale": len(communities),
            "community_ids": community_ids,
        }

    async def _upsert_cognitive_cards(
        self,
        cognitive_card_ids: list[str],
        command: AssignmentEvidenceBackfillCommand,
    ) -> int:
        unique_ids = list(dict.fromkeys(cognitive_card_ids))
        cards = self._repository.list_cognitive_cards_by_ids(
            command.adapter_name,
            cognitive_card_ids=unique_ids,
        )
        documents: list[SemanticVectorDocument] = []
        for card in cards:
            document = cognitive_card_document(card)
            documents.append(
                SemanticVectorDocument(
                    document_id=document.document_id,
                    document_type=document.document_type,
                    collection_role=document.collection_role,
                    source_type=document.source_type,
                    source_id=document.source_id,
                    text=document.text,
                    evidence_id=document.evidence_id,
                    metadata=dict(document.metadata or {}),
                )
            )
        return await self._semantic_retriever.upsert_semantic_documents(
            adapter_name=command.adapter_name,
            target=command.target,
            documents=documents,
            kg_version="assignment_evidence_backfill_v1",
        )

    def _clear_pending_index_markers(
        self,
        cognitive_card_ids: list[str],
        command: AssignmentEvidenceBackfillCommand,
    ) -> None:
        unique_ids = list(dict.fromkeys(cognitive_card_ids))
        if not unique_ids:
            return
        with get_session(command.target) as session:
            cards = session.scalars(
                select(KnowledgeCognitiveCard)
                .where(KnowledgeCognitiveCard.cognitive_card_id.in_(unique_ids))
                .with_for_update()
            ).all()
            for card in cards:
                payload = dict(card.payload or {})
                payload.pop("assignment_evidence_backfill_pending", None)
                card.payload = payload


def _card_intent_at(topic_intents: Any, intent_index: int) -> dict[str, Any]:
    values = [item for item in topic_intents or [] if isinstance(item, dict)]
    index = intent_index - 1 if intent_index > 0 else intent_index
    if 0 <= index < len(values):
        return dict(values[index])
    return {}


def _grounding_from_intent(intent: Any) -> dict[str, str] | None:
    if not isinstance(intent, dict):
        return None
    material = intent.get("cognitive_material") if isinstance(intent.get("cognitive_material"), dict) else {}
    evidence_span = str(intent.get("evidence_span") or material.get("evidence_span") or "").strip()
    if not evidence_span:
        return None
    return {"evidence_span": evidence_span}


def _best_existing_grounding(intents: list[dict[str, Any]]) -> dict[str, str] | None:
    candidates = [item for intent in intents for item in [_grounding_from_intent(intent)] if item]
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item["evidence_span"]))


def _has_grounding(intent: Any) -> bool:
    return _grounding_from_intent(intent) is not None


def _grounding_status(intent: Any) -> str:
    if not isinstance(intent, dict):
        return ""
    material = intent.get("cognitive_material") if isinstance(intent.get("cognitive_material"), dict) else {}
    return str(intent.get("evidence_grounding_status") or material.get("evidence_grounding_status") or "").strip()


def _intent_has_semantics(intent: dict[str, Any]) -> bool:
    if not isinstance(intent, dict):
        return False
    assignment_profile = intent.get("assignment_profile")
    if isinstance(assignment_profile, dict):
        return bool(assignment_profile.get("title_candidate") or assignment_profile.get("raw_theme"))
    return bool(intent.get("title_candidate") or intent.get("raw_theme"))


def _intent_prompt_context(intent: dict[str, Any]) -> dict[str, Any]:
    assignment_profile = intent.get("assignment_profile") if isinstance(intent.get("assignment_profile"), dict) else {}
    cognitive_material = intent.get("cognitive_material") if isinstance(intent.get("cognitive_material"), dict) else {}
    event_classification = intent.get("event_classification") if isinstance(intent.get("event_classification"), dict) else {}
    merged = {
        **{key: value for key, value in intent.items() if key not in {"evidence_span", "evidence_claim", "cognitive_material", "assignment_profile"}},
        **assignment_profile,
        **{key: value for key, value in cognitive_material.items() if key not in {"evidence_span", "evidence_claim", "evidence_support"}},
    }
    if event_classification:
        merged["event_classification"] = event_classification
    excluded = {
        "action",
        "assignment_id",
        "assignment_reason",
        "community_id",
        "cognitive_card_id",
        "fit_type",
        "intent_id",
        "matched_reason",
        "reason",
        "resolved_community_id",
        "evidence_id",
        "chunk_ids",
        "primary_chunk_id",
        "source_id",
        "source_published_at",
    }
    return {
        key: value
        for key, value in merged.items()
        if key not in excluded and value not in (None, "", [], {})
    }


def _build_grounding_prompt(
    topic_intent: dict[str, Any],
    chunk_text: str,
    *,
    validation_error: str = "",
) -> str:
    payload: dict[str, Any] = {
        "topic_intent": _intent_prompt_context(topic_intent),
        "source_segments": [
            {"segment_id": item.segment_id, "text": item.text}
            for item in _source_segments(chunk_text)
        ],
    }
    if validation_error:
        payload["validation_error"] = validation_error
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_chunk_text(document_text: str) -> str:
    marker = "Evidence Text:"
    marker_index = document_text.find(marker)
    if marker_index < 0:
        return ""
    return document_text[marker_index + len(marker) :].strip()


def _source_segments(chunk_text: str, *, max_chars: int = 300) -> list[_SourceSegment]:
    """按原文边界分段，同时保留偏移量供程序精确回取。"""

    if not chunk_text:
        return []
    raw_ranges: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[。！？!?；;，,：:][”’〉』】）)]*|[\r\n]+", chunk_text):
        end = match.end()
        if end > start:
            raw_ranges.append((start, end))
        start = end
    if start < len(chunk_text):
        raw_ranges.append((start, len(chunk_text)))

    bounded_ranges: list[tuple[int, int]] = []
    for range_start, range_end in raw_ranges:
        cursor = range_start
        while range_end - cursor > max_chars:
            bounded_ranges.append((cursor, cursor + max_chars))
            cursor += max_chars
        if range_end > cursor:
            bounded_ranges.append((cursor, range_end))

    segments: list[_SourceSegment] = []
    for range_start, range_end in bounded_ranges:
        while range_start < range_end and chunk_text[range_start].isspace():
            range_start += 1
        while range_end > range_start and chunk_text[range_end - 1].isspace():
            range_end -= 1
        if range_end <= range_start:
            continue
        segments.append(
            _SourceSegment(
                segment_id=len(segments) + 1,
                start=range_start,
                end=range_end,
                text=chunk_text[range_start:range_end],
            )
        )
    return segments


def _evidence_span_from_segment_selection(chunk_text: str, selection: dict[str, Any]) -> str:
    segments = _source_segments(chunk_text)
    try:
        start_segment = int(selection.get("start_segment") or 0)
        end_segment = int(selection.get("end_segment") or 0)
    except (TypeError, ValueError):
        return ""
    if start_segment < 1 or end_segment < start_segment or end_segment - start_segment >= 3:
        return ""
    if end_segment > len(segments):
        return ""
    first = segments[start_segment - 1]
    last = segments[end_segment - 1]
    evidence_span = chunk_text[first.start : last.end].strip()
    return evidence_span if _grounding_is_valid({"evidence_span": evidence_span}, chunk_text) else ""


def _grounding_is_valid(grounding: dict[str, str], chunk_text: str) -> bool:
    evidence_span = str(grounding.get("evidence_span") or "").strip()
    if not evidence_span:
        return False
    start = chunk_text.find(evidence_span)
    if start < 0:
        return False
    end = start + len(evidence_span)
    if end >= len(chunk_text):
        return True
    boundary_chars = "。！？!?；;，,：:\r\n\t "
    return evidence_span[-1] in boundary_chars or chunk_text[end] in boundary_chars


def _set_card_intent_grounding(
    intent: dict[str, Any],
    *,
    evidence_span: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(intent or {}))
    if isinstance(result.get("cognitive_material"), dict):
        material = dict(result["cognitive_material"])
        material["evidence_span"] = evidence_span
        material.pop("evidence_claim", None)
        material.pop("evidence_grounding_status", None)
        result["cognitive_material"] = material
    else:
        result["evidence_span"] = evidence_span
        result.pop("evidence_claim", None)
        result.pop("evidence_grounding_status", None)
    return result


def _clear_card_intent_grounding(intent: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(intent or {}))
    if isinstance(result.get("cognitive_material"), dict):
        material = dict(result["cognitive_material"])
        material.pop("evidence_span", None)
        material.pop("evidence_claim", None)
        material["evidence_grounding_status"] = "unsupported"
        result["cognitive_material"] = material
    result.pop("evidence_span", None)
    result.pop("evidence_claim", None)
    if not isinstance(result.get("cognitive_material"), dict):
        result["evidence_grounding_status"] = "unsupported"
    return result


def _supporting_text_from_card_intents(intents: list[dict[str, Any]]) -> list[str]:
    spans: list[str] = []
    for intent in intents:
        grounding = _grounding_from_intent(intent)
        if grounding and grounding["evidence_span"] not in spans:
            spans.append(grounding["evidence_span"])
    return spans[:5]


def _response_object(response: LLMProxyResponse) -> dict[str, Any] | None:
    if isinstance(response.structured_output, dict):
        return response.structured_output
    candidate = response.structured_output if isinstance(response.structured_output, str) else response.text
    try:
        value = json.loads(str(candidate or ""))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _status_counts(outcomes: list[_BackfillOutcome]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in outcomes:
        result[item.status] = result.get(item.status, 0) + 1
    return result
