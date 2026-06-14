"""Application service for Cognitive Card based community indexing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.cognitive_index import (
    ASSIGNMENT_SCHEMA,
    ASSIGNMENT_SYSTEM_PROMPT,
    COGNITIVE_CARD_SCHEMA,
    COGNITIVE_CARD_SYSTEM_PROMPT,
    COMPLEX_MAX_ATTACH,
    DEFAULT_MAX_ATTACH,
    CognitiveCard,
    CognitiveCommunityBuildResult,
    CommunityAssignment,
    _apply_assignment,
    _assignment_topic_intent,
    _candidate_aliases,
    _community_document,
    _drafts_from_existing,
    _graph_community_from_draft,
    _is_complex_intent,
    _resolve_aliases,
    assignment_query_text,
    cognitive_card_from_llm,
    validate_assignment_decision,
)
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_COMMUNITY
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import langfuse_observation, langfuse_update_span
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusTypedHybridStore


class CognitiveCardExtractor:
    def __init__(self, llm: Any | None = None, *, model: str | None = None, concurrency: int = 4):
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_cognitive_card")
        self._concurrency = max(1, concurrency)

    async def extract(self, chunks: list[EvidenceChunk]) -> list[CognitiveCard]:
        sem = asyncio.Semaphore(self._concurrency)

        async def extract_one(chunk: EvidenceChunk) -> CognitiveCard:
            async with sem:
                return await self._extract_one(chunk)

        return await asyncio.gather(*(extract_one(chunk) for chunk in chunks))

    async def _extract_one(self, chunk: EvidenceChunk) -> CognitiveCard:
        payload = dict(chunk.payload or {})
        prompt = {
            "title": payload.get("title") or "",
            "chunk_text": chunk.content,
        }
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=COGNITIVE_CARD_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=1800,
            json_schema=COGNITIVE_CARD_SCHEMA,
            metadata={
                "task": "kg_cognitive_card",
                "source_type": payload.get("source_type") or "",
                "source_id": payload.get("source_id") or "",
                "chunk_id": chunk.chunk_id,
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.cognitive_card.extract",
            as_type="span",
            input={"chunk_id": chunk.chunk_id, "text_chars": len(chunk.content)},
        ):
            response = await self._llm.generate(request)
            data = response.structured_output
            if not isinstance(data, dict):
                raise RuntimeError(f"cognitive card output is not object: chunk_id={chunk.chunk_id}")
            card = cognitive_card_from_llm(chunk, data)
            langfuse_update_span(
                output={
                    "cognitive_card_id": card.cognitive_card_id,
                    "topic_intents": len(card.topic_intents),
                    "risk_signals": len(card.risk_signals),
                    "local_impact_signals": len(card.local_impact_signals),
                },
                status_message="completed",
            )
            return card


class CommunitySemanticCandidateProvider:
    def __init__(self, *, store: MilvusTypedHybridStore):
        self._store = store

    async def recall(
        self,
        *,
        adapter_name: str,
        target: str,
        topic_intent: dict[str, Any],
        communities: dict[str, Any],
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if not communities:
            return []
        query = assignment_query_text(topic_intent)
        if not query.strip():
            return []
        with langfuse_observation(
            name="kg.community_assignment.semantic_recall",
            as_type="retriever",
            input={
                "query_chars": len(query),
                "adapter_name": adapter_name,
                "target": target,
                "limit": limit,
            },
            metadata={"collection_role": SEMANTIC_COLLECTION_COMMUNITY},
        ):
            vectors = await embed_texts([query])
            query_vector = vectors[0] if vectors and vectors[0] else []
            hits = self._store.hybrid_search(
                collection_role=SEMANTIC_COLLECTION_COMMUNITY,
                query_text=query,
                query_vector=query_vector,
                adapter_name=adapter_name,
                target=target,
                limit=max(limit, 1),
            )
            candidates: list[dict[str, Any]] = []
            seen: set[str] = set()
            for hit in hits:
                community_id = str(hit.metadata.get("community_id") or hit.metadata.get("source_id") or hit.target_id)
                community = communities.get(community_id)
                if community is None or community_id in seen:
                    continue
                seen.add(community_id)
                candidates.append(
                    community.to_assignment_candidate(score=float(hit.score or 0.0), lane="semantic_community")
                )
                if len(candidates) >= limit:
                    break
            langfuse_update_span(
                output={
                    "raw_hits": len(hits),
                    "candidates": len(candidates),
                    "candidate_titles": [item.get("title") for item in candidates[:8]],
                },
                status_message="completed",
            )
            return candidates


class CommunityCardBuilder:
    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        candidate_provider: CommunitySemanticCandidateProvider | None = None,
        target: str = "prod",
        on_communities_updated: Callable[[list[GraphIndexCommunity]], Awaitable[None]] | None = None,
    ):
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_community_assignment")
        self._candidate_provider = candidate_provider
        self._target = target
        self._on_communities_updated = on_communities_updated

    async def build(
        self,
        *,
        adapter_name: str,
        cards: list[CognitiveCard],
        existing_communities: list[GraphIndexCommunity],
    ) -> CognitiveCommunityBuildResult:
        communities = _drafts_from_existing(existing_communities)
        assignments: list[CommunityAssignment] = []
        intent_count = 0
        validation_errors = 0
        with langfuse_observation(
            name="kg.community_card.build",
            as_type="span",
            input={"cards": len(cards), "existing_communities": len(existing_communities)},
        ):
            for card in sorted(cards, key=lambda item: (item.source_id, item.chunk_index, item.cognitive_card_id)):
                for index, intent in enumerate(card.topic_intents, start=1):
                    intent_count += 1
                    topic_intent = _assignment_topic_intent(card, intent)
                    candidates = await self._recall_candidates(
                        adapter_name=adapter_name,
                        topic_intent=topic_intent,
                        communities=communities,
                    )
                    try:
                        decision = await self._decide_assignment(card, topic_intent, candidates)
                    except Exception:
                        validation_errors += 1
                        raise
                    applied = _apply_assignment(
                        adapter_name=adapter_name,
                        card=card,
                        intent_index=index,
                        topic_intent=topic_intent,
                        decision=decision,
                        communities=communities,
                    )
                    assignments.extend(applied)
                    if applied and self._on_communities_updated is not None:
                        updated_ids = sorted({assignment.community_id for assignment in applied})
                        updated = [
                            _graph_community_from_draft(adapter_name, communities[community_id])
                            for community_id in updated_ids
                            if community_id in communities and communities[community_id].assigned_intents
                        ]
                        if updated:
                            await self._on_communities_updated(updated)
            graph_communities = [
                _graph_community_from_draft(adapter_name, community)
                for community in communities.values()
                if community.assigned_intents
            ]
            documents = [_community_document(community) for community in graph_communities]
            diagnostics = {
                "cards": len(cards),
                "intents": intent_count,
                "assignments": len(assignments),
                "communities": len(graph_communities),
                "assignment_validation_errors": validation_errors,
                "candidate_recall": "semantic_community",
                "community_builder": "cognitive_card_assignment_v1",
            }
            langfuse_update_span(output=diagnostics, status_message="completed")
            return CognitiveCommunityBuildResult(
                cards=cards,
                assignments=assignments,
                communities=graph_communities,
                documents=documents,
                diagnostics=diagnostics,
            )

    async def _recall_candidates(
        self,
        *,
        adapter_name: str,
        topic_intent: dict[str, Any],
        communities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        if self._candidate_provider is not None:
            semantic_candidates = await self._candidate_provider.recall(
                adapter_name=adapter_name,
                target=self._target,
                topic_intent=topic_intent,
                communities=communities,
                limit=12,
            )
            for candidate in semantic_candidates:
                community_id = str(candidate.get("community_id") or "")
                if community_id and community_id not in seen:
                    seen.add(community_id)
                    candidates.append(candidate)
        return candidates[:12]

    async def _decide_assignment(
        self,
        card: CognitiveCard,
        topic_intent: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        max_attach = COMPLEX_MAX_ATTACH if _is_complex_intent(topic_intent) else DEFAULT_MAX_ATTACH
        alias_map, prompt_candidates = _candidate_aliases(candidates)
        prompt = {
            "max_attach": max_attach,
            "candidate_communities": prompt_candidates,
            "source": {"title": (card.payload or {}).get("title") or ""},
            "topic_intent": {**topic_intent, "max_attach": max_attach},
        }
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=ASSIGNMENT_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=2200,
            json_schema=ASSIGNMENT_SCHEMA,
            metadata={"task": "kg_community_assignment", "source_id": card.source_id},
            use_cache=True,
        )
        response = await self._llm.generate(request)
        decision = response.structured_output
        if not isinstance(decision, dict):
            raise RuntimeError(f"assignment output is not object: card={card.cognitive_card_id}")
        decision = _resolve_aliases(decision, alias_map)
        try:
            validate_assignment_decision(decision, candidates, topic_intent=topic_intent)
        except Exception as exc:
            repaired = await self._llm.repair_with_feedback(
                request,
                response,
                [str(exc)],
                instruction=(
                    "上一轮 Community Assignment 输出未通过业务校验。"
                    "只修复 JSON 结构和字段合规性，不改变业务裁决含义。"
                    "action=attach_existing 时 new_community 必须为 null；"
                    "action=create_new_l0 时 community_id 必须为 null 且 new_community 必须是完整对象。"
                ),
                retry_reason="community_assignment_validation_invalid",
            )
            decision = repaired.structured_output
            if not isinstance(decision, dict):
                raise RuntimeError(f"assignment repair output is not object: card={card.cognitive_card_id}") from exc
            decision = _resolve_aliases(decision, alias_map)
            validate_assignment_decision(decision, candidates, topic_intent=topic_intent)
        return decision
