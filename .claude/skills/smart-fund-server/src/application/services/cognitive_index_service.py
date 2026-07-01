"""Application service for Cognitive Card based community indexing."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.cognitive_index import (
    ASSIGNMENT_SCHEMA,
    ASSIGNMENT_SYSTEM_PROMPT,
    ASSIGNMENT_MAX_TOKENS,
    COGNITIVE_CARD_MAX_TOKENS,
    COGNITIVE_CARD_SCHEMA,
    COGNITIVE_CARD_SYSTEM_PROMPT,
    COMPLEX_MAX_ATTACH,
    DEFAULT_MAX_ATTACH,
    MAX_ASSIGNMENT_CANDIDATES,
    MAX_SEMANTIC_ASSIGNMENT_CANDIDATES,
    RERANK_MIN_ASSIGNMENT_CANDIDATES,
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
    assignment_query_lanes,
    assignment_query_text,
    assignment_prompt_topic_intent,
    cognitive_card_from_llm,
    merge_seed_community_drafts,
    seed_community_drafts,
    validate_assignment_decision,
)
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.clients.reranker import RerankerClient
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_COMMUNITY
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config.settings import REDIS_URL
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import langfuse_observation, langfuse_update_span
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusTypedHybridStore


ASSIGNMENT_LEDGER_SCHEMA_VERSION = "candidate_append_log_v1"
ASSIGNMENT_LEDGER_TTL_SECONDS = 7 * 24 * 60 * 60
ASSIGNMENT_LEDGER_MAX_BASE_CANDIDATES = 30
ASSIGNMENT_LEDGER_KEEP_BASE_CANDIDATES = 10
ASSIGNMENT_LEDGER_MAX_CHARS = 24_000
ASSIGNMENT_LEDGER_CHECKPOINT_REUSE_OVERLAP = 0.7


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

        tasks = [asyncio.create_task(extract_one(chunk)) for chunk in chunks]
        try:
            return await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

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
            max_tokens=COGNITIVE_CARD_MAX_TOKENS,
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
            card = await self._card_from_response(chunk, request, response)
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

    async def _card_from_response(
        self,
        chunk: EvidenceChunk,
        request: LLMProxyRequest,
        response: Any,
    ) -> CognitiveCard:
        issues: list[str] = []
        data = response.structured_output
        if not isinstance(data, dict):
            issues.append(f"cognitive card output must be JSON object; actual={type(data).__name__}")
        else:
            try:
                return cognitive_card_from_llm(chunk, data)
            except Exception as exc:
                issues.append(str(exc))

        repaired = await self._llm.repair_with_feedback(
            request,
            response,
            issues,
            instruction=(
                "上一轮 Cognitive Card 输出未通过业务校验。"
                "只修复 JSON 结构和字段合规性，不要新增外部事实。"
                "顶层必须是 JSON object，且必须包含 summary、title_candidates、topic_intents、"
                "risk_signals、local_impact_signals、actor_signals、supporting_text。"
                "topic_intents 必须是非空对象数组。"
            ),
            retry_reason="cognitive_card_validation_invalid",
        )
        repaired_data = repaired.structured_output
        if not isinstance(repaired_data, dict):
            raise RuntimeError(
                f"cognitive card repair output is not object: chunk_id={chunk.chunk_id}; issues={issues}"
            )
        return cognitive_card_from_llm(chunk, repaired_data)


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
        query_lanes = assignment_query_lanes(topic_intent)
        if not query_lanes:
            return []
        with langfuse_observation(
            name="kg.community_assignment.semantic_recall",
            as_type="retriever",
            input={
                "query_lanes": [{"lane": item["lane"], "query_chars": len(item["query"])} for item in query_lanes],
                "adapter_name": adapter_name,
                "target": target,
                "limit": limit,
            },
            metadata={"collection_role": SEMANTIC_COLLECTION_COMMUNITY},
        ):
            query_texts = [item["query"] for item in query_lanes]
            vectors = await embed_texts(query_texts)
            merged_hits: dict[str, dict[str, Any]] = {}
            raw_hits = 0
            per_lane_hits: dict[str, int] = {}
            for lane, query, vector in zip(query_lanes, query_texts, vectors, strict=False):
                if not query.strip() or not vector:
                    continue
                hits = self._store.hybrid_search(
                    collection_role=SEMANTIC_COLLECTION_COMMUNITY,
                    query_text=query,
                    query_vector=vector,
                    adapter_name=adapter_name,
                    target=target,
                    limit=max(limit, 1),
                )
                raw_hits += len(hits)
                per_lane_hits[lane["lane"]] = len(hits)
                for hit in hits:
                    community_id = str(hit.metadata.get("community_id") or hit.metadata.get("source_id") or hit.target_id)
                    if community_id not in communities:
                        continue
                    current = merged_hits.get(community_id)
                    score = float(hit.score or 0.0)
                    if current is None:
                        merged_hits[community_id] = {
                            "community_id": community_id,
                            "score": score,
                            "lanes": {lane["lane"]},
                        }
                    else:
                        current["score"] = max(float(current["score"]), score)
                        current["lanes"].add(lane["lane"])
            candidates: list[dict[str, Any]] = []
            for item in sorted(
                merged_hits.values(),
                key=lambda value: (len(value["lanes"]), float(value["score"])),
                reverse=True,
            ):
                community_id = str(item["community_id"])
                community = communities[community_id]
                candidates.append(
                    community.to_assignment_candidate(
                        score=float(item["score"] or 0.0),
                        lane="semantic:" + ",".join(sorted(item["lanes"])),
                    )
                )
                if len(candidates) >= limit:
                    break
            langfuse_update_span(
                output={
                    "raw_hits": raw_hits,
                    "per_lane_hits": per_lane_hits,
                    "candidates": len(candidates),
                    "candidate_titles": [item.get("title") for item in candidates[:8]],
                },
                status_message="completed",
            )
            return candidates


class AssignmentCandidateOrderStore:
    """Redis-backed append-only candidate ledger for assignment prompts."""

    def __init__(
        self,
        *,
        target: str = "prod",
        redis_client: Any | None = None,
        ttl_seconds: int = ASSIGNMENT_LEDGER_TTL_SECONDS,
        max_base_candidates: int = ASSIGNMENT_LEDGER_MAX_BASE_CANDIDATES,
        keep_base_candidates: int = ASSIGNMENT_LEDGER_KEEP_BASE_CANDIDATES,
        max_chars: int = ASSIGNMENT_LEDGER_MAX_CHARS,
        checkpoint_reuse_overlap: float = ASSIGNMENT_LEDGER_CHECKPOINT_REUSE_OVERLAP,
    ) -> None:
        self._target = target
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._max_base_candidates = max_base_candidates
        self._keep_base_candidates = keep_base_candidates
        self._max_chars = max_chars
        self._checkpoint_reuse_overlap = checkpoint_reuse_overlap

    def prepare_append_log(
        self,
        *,
        adapter_name: str,
        candidates: list[dict[str, Any]],
        allow_checkpoint: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ordered_candidates = _dedupe_assignment_candidates(
            sorted(candidates, key=_stable_candidate_prompt_sort_key)
        )
        diagnostics: dict[str, Any] = {
            "redis_available": True,
            "appended_base": 0,
            "appended_update": 0,
            "checkpointed": False,
            "checkpoint_skipped_by_overlap": False,
        }
        redis_client = self._redis_client()
        ledger = self._load_ledger(redis_client, adapter_name=adapter_name)
        append_log = _compact_ledger_append_log(
            item for item in ledger.get("candidate_append_log") or [] if isinstance(item, dict)
        )
        stats = {
            str(key): value
            for key, value in (ledger.get("candidate_stats") or {}).items()
            if isinstance(value, dict)
        }
        counters = {
            str(key): value
            for key, value in (ledger.get("candidate_counters") or {}).items()
            if isinstance(value, dict)
        }
        checkpoint_meta = dict(ledger.get("checkpoint_meta") or {})
        base_ids = _candidate_append_log_base_ids(append_log)
        for candidate in ordered_candidates:
            community_id = str(candidate.get("community_id") or "")
            if not community_id:
                continue
            counters.setdefault(community_id, {"retrieved": 0, "accepted": 0})
            counters[community_id]["retrieved"] = int(counters[community_id].get("retrieved") or 0) + 1
            current_stats = _candidate_stats(candidate)
            if community_id not in base_ids:
                append_log.append(_candidate_base_entry(candidate))
                stats[community_id] = current_stats
                base_ids.add(community_id)
                diagnostics["appended_base"] += 1
                continue
            update = _candidate_update_entry(candidate, previous=stats.get(community_id) or {}, current=current_stats)
            if update:
                append_log.append(update)
                stats[community_id] = current_stats
                diagnostics["appended_update"] += 1
        checkpointed = False
        skipped_by_overlap = False
        if allow_checkpoint:
            append_log, stats, counters, checkpointed, skipped_by_overlap = self._maybe_checkpoint(
                append_log=append_log,
                stats=stats,
                counters=counters,
                current_candidates=ordered_candidates,
            )
        diagnostics["checkpointed"] = checkpointed
        diagnostics["checkpoint_skipped_by_overlap"] = skipped_by_overlap
        checkpoint_meta = _updated_checkpoint_meta(
            checkpoint_meta,
            checkpointed=checkpointed,
            skipped_by_overlap=skipped_by_overlap,
            base_count=_candidate_append_log_base_count(append_log),
            update_count=_candidate_append_log_update_count(append_log),
        )
        diagnostics["candidate_append_log_entries"] = len(append_log)
        diagnostics["candidate_append_log_base_count"] = _candidate_append_log_base_count(append_log)
        diagnostics["candidate_append_log_update_count"] = _candidate_append_log_update_count(append_log)
        diagnostics["checkpoint_meta"] = checkpoint_meta
        self._save_ledger(
            redis_client,
            adapter_name=adapter_name,
            ledger={
                "schema_version": ASSIGNMENT_LEDGER_SCHEMA_VERSION,
                "candidate_append_log": append_log,
                "candidate_stats": stats,
                "candidate_counters": counters,
                "checkpoint_meta": checkpoint_meta,
            },
        )
        return append_log, diagnostics

    def checkpoint_if_needed(self, *, adapter_name: str) -> dict[str, Any]:
        redis_client = self._redis_client()
        ledger = self._load_ledger(redis_client, adapter_name=adapter_name)
        append_log = _compact_ledger_append_log(
            item for item in ledger.get("candidate_append_log") or [] if isinstance(item, dict)
        )
        stats = {
            str(key): value
            for key, value in (ledger.get("candidate_stats") or {}).items()
            if isinstance(value, dict)
        }
        counters = {
            str(key): value
            for key, value in (ledger.get("candidate_counters") or {}).items()
            if isinstance(value, dict)
        }
        checkpoint_meta = dict(ledger.get("checkpoint_meta") or {})
        append_log, stats, counters, checkpointed, skipped_by_overlap = self._maybe_checkpoint(
            append_log=append_log,
            stats=stats,
            counters=counters,
            current_candidates=[],
        )
        checkpoint_meta = _updated_checkpoint_meta(
            checkpoint_meta,
            checkpointed=checkpointed,
            skipped_by_overlap=skipped_by_overlap,
            base_count=_candidate_append_log_base_count(append_log),
            update_count=_candidate_append_log_update_count(append_log),
        )
        diagnostics = {
            "redis_available": True,
            "appended_base": 0,
            "appended_update": 0,
            "checkpointed": checkpointed,
            "checkpoint_skipped_by_overlap": skipped_by_overlap,
            "candidate_append_log_entries": len(append_log),
            "candidate_append_log_base_count": _candidate_append_log_base_count(append_log),
            "candidate_append_log_update_count": _candidate_append_log_update_count(append_log),
            "checkpoint_meta": checkpoint_meta,
            "phase": "checkpoint",
        }
        self._save_ledger(
            redis_client,
            adapter_name=adapter_name,
            ledger={
                "schema_version": ASSIGNMENT_LEDGER_SCHEMA_VERSION,
                "candidate_append_log": append_log,
                "candidate_stats": stats,
                "candidate_counters": counters,
                "checkpoint_meta": checkpoint_meta,
            },
        )
        return diagnostics

    def record_assignment_decision(
        self,
        *,
        adapter_name: str,
        decision: dict[str, Any],
        topic_intent: dict[str, Any] | None = None,
    ) -> None:
        redis_client = self._redis_client()
        ledger = self._load_ledger(redis_client, adapter_name=adapter_name)
        append_log = _compact_ledger_append_log(
            item for item in ledger.get("candidate_append_log") or [] if isinstance(item, dict)
        )
        stats = {
            str(key): value
            for key, value in (ledger.get("candidate_stats") or {}).items()
            if isinstance(value, dict)
        }
        counters = {
            str(key): value
            for key, value in (ledger.get("candidate_counters") or {}).items()
            if isinstance(value, dict)
        }
        changed = False
        for assignment in decision.get("assignments") or []:
            if not isinstance(assignment, dict) or assignment.get("action") != "attach_existing":
                continue
            community_id = str(assignment.get("community_id") or "")
            if not community_id:
                continue
            counters.setdefault(community_id, {"retrieved": 0, "accepted": 0})
            counters[community_id]["accepted"] = int(counters[community_id].get("accepted") or 0) + 1
            update = _candidate_assignment_update_entry(
                community_id=community_id,
                assignment=assignment,
                previous=stats.get(community_id) or {},
                topic_intent=topic_intent or {},
            )
            if update:
                append_log.append(update)
                previous_topics = _candidate_list((stats.get(community_id) or {}).get("future_coverage"))
                stats.setdefault(community_id, {})
                stats[community_id]["future_coverage"] = _ordered_unique(
                    [*previous_topics, *update.get("absorbed", [])]
                )[:32]
            changed = True
        if not changed:
            return
        ledger["candidate_append_log"] = append_log
        ledger["candidate_stats"] = stats
        ledger["candidate_counters"] = counters
        self._save_ledger(redis_client, adapter_name=adapter_name, ledger=ledger)

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    def _ledger_key(self, *, adapter_name: str) -> str:
        return f"kg:assignment_candidate_ledger:{self._target}:{adapter_name}"

    def _load_ledger(self, redis_client: Any, *, adapter_name: str) -> dict[str, Any]:
        raw = redis_client.get(self._ledger_key(adapter_name=adapter_name))
        if not raw:
            return {
                "schema_version": ASSIGNMENT_LEDGER_SCHEMA_VERSION,
                "candidate_append_log": [],
                "candidate_stats": {},
                "candidate_counters": {},
                "checkpoint_meta": {},
            }
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") != ASSIGNMENT_LEDGER_SCHEMA_VERSION:
            return {
                "schema_version": ASSIGNMENT_LEDGER_SCHEMA_VERSION,
                "candidate_append_log": [],
                "candidate_stats": {},
                "candidate_counters": {},
                "checkpoint_meta": {},
            }
        return data

    def _save_ledger(self, redis_client: Any, *, adapter_name: str, ledger: dict[str, Any]) -> None:
        redis_client.setex(
            self._ledger_key(adapter_name=adapter_name),
            self._ttl_seconds,
            json.dumps(ledger, ensure_ascii=False, separators=(",", ":")),
        )

    def _maybe_checkpoint(
        self,
        *,
        append_log: list[dict[str, Any]],
        stats: dict[str, dict[str, Any]],
        counters: dict[str, dict[str, Any]],
        current_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool, bool]:
        base_count = _candidate_append_log_base_count(append_log)
        base_entries = [item for item in append_log if item.get("entry_type") == "candidate_base"]
        base_payload_chars = len(json.dumps(base_entries, ensure_ascii=False, separators=(",", ":")))
        if (
            base_count <= self._max_base_candidates
            and base_payload_chars <= self._max_chars
        ):
            return append_log, stats, counters, False, False
        old_base_ids = _candidate_append_log_base_ids_in_order(append_log)
        first_order = {community_id: index for index, community_id in enumerate(old_base_ids)}
        current_ids = [str(candidate.get("community_id") or "") for candidate in current_candidates if candidate.get("community_id")]
        ranked_ids = sorted(
            first_order,
            key=lambda community_id: (
                -int((counters.get(community_id) or {}).get("accepted") or 0),
                -int((counters.get(community_id) or {}).get("retrieved") or 0),
                first_order[community_id],
            ),
        )
        selected_ids = _ordered_unique([*ranked_ids[: self._keep_base_candidates], *current_ids])
        if base_payload_chars <= self._max_chars and _candidate_prefix_overlap_ratio(old_base_ids, selected_ids) >= self._checkpoint_reuse_overlap:
            return append_log, stats, counters, False, True
        base_by_id = {
            str(item.get("community_id") or ""): item
            for item in append_log
            if item.get("entry_type") == "candidate_base" and item.get("community_id")
        }
        sorted_selected_ids = sorted(selected_ids, key=_stable_community_id_sort_key)
        rebuilt = [
            dict(base_by_id[community_id])
            for community_id in sorted_selected_ids
            if community_id in base_by_id
        ]
        selected = {str(item.get("community_id") or "") for item in rebuilt if item.get("community_id")}
        return (
            rebuilt,
            {community_id: value for community_id, value in stats.items() if community_id in selected},
            {},
            True,
            False,
        )


def _stable_candidate_prompt_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, str]:
    community_id = str(candidate.get("community_id") or "")
    id_key = _stable_community_id_sort_key(community_id)
    return (
        int(candidate.get("level") or 0),
        *id_key,
    )


def _stable_community_id_sort_key(community_id: str) -> tuple[int, int, str]:
    sequence = _community_id_sequence_order(community_id)
    return (
        0 if sequence is not None else 1,
        sequence or 0,
        community_id,
    )


def _community_id_sequence_order(community_id: str) -> int | None:
    match = re.search(r":(\d+)$", community_id)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _candidate_append_log_base_count(append_log: list[dict[str, Any]]) -> int:
    return sum(1 for item in append_log if item.get("entry_type") == "candidate_base")


def _candidate_append_log_update_count(append_log: list[dict[str, Any]]) -> int:
    return sum(1 for item in append_log if item.get("entry_type") == "candidate_update")


def _candidate_append_log_base_ids(append_log: list[dict[str, Any]]) -> set[str]:
    return set(_candidate_append_log_base_ids_in_order(append_log))


def _candidate_append_log_base_ids_in_order(append_log: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in append_log:
        if item.get("entry_type") != "candidate_base":
            continue
        community_id = str(item.get("community_id") or "")
        if not community_id or community_id in seen:
            continue
        seen.add(community_id)
        result.append(community_id)
    return result


def _compact_ledger_append_log(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry_type = item.get("entry_type")
        if entry_type == "candidate_base":
            compacted = _compact_append_log_entry(_legacy_candidate_base_entry(item))
        elif entry_type == "candidate_update":
            compacted = _compact_append_log_entry(_legacy_candidate_update_entry(item))
        else:
            continue
        if compacted.get("entry_type") == "candidate_base" and compacted.get("community_id"):
            result.append(compacted)
        elif compacted.get("entry_type") == "candidate_update" and compacted.get("community_id") and compacted.get("absorbed"):
            result.append(compacted)
    return result


def _legacy_candidate_base_entry(item: dict[str, Any]) -> dict[str, Any]:
    dynamic = item.get("dynamic_context") if isinstance(item.get("dynamic_context"), dict) else {}
    payload = {
        "entry_type": "candidate_base",
        "community_id": str(item.get("community_id") or ""),
        "title": str(item.get("title") or ""),
        "scope": str(item.get("scope") or ""),
        "include_rules": _candidate_list(item.get("include_rules")),
        "exclude_rules": _candidate_list(item.get("exclude_rules")),
        "canonical_labels": _candidate_list(item.get("canonical_labels")),
        "granularity_note": str(item.get("granularity_note") or ""),
        "absorbed_subtopics": _candidate_list(item.get("absorbed_subtopics") or dynamic.get("absorbed_subtopics")),
        "maturity": str(item.get("maturity") or dynamic.get("maturity") or ""),
    }
    return payload


def _legacy_candidate_update_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_type": "candidate_update",
        "community_id": str(item.get("community_id") or ""),
        "absorbed": _candidate_list(item.get("absorbed") or item.get("new_absorbed_subtopics")),
    }


def _candidate_prefix_overlap_ratio(old_base_ids: list[str], selected_ids: list[str]) -> float:
    if not selected_ids:
        return 0.0
    old_prefix = set(old_base_ids[: len(selected_ids)])
    if not old_prefix:
        return 0.0
    return len(old_prefix.intersection(selected_ids)) / len(selected_ids)


def _updated_checkpoint_meta(
    checkpoint_meta: dict[str, Any],
    *,
    checkpointed: bool,
    skipped_by_overlap: bool,
    base_count: int,
    update_count: int,
) -> dict[str, Any]:
    result = dict(checkpoint_meta)
    if checkpointed:
        result["checkpoint_count"] = int(result.get("checkpoint_count") or 0) + 1
    else:
        result.setdefault("checkpoint_count", int(result.get("checkpoint_count") or 0))
    result["last_checkpointed"] = bool(checkpointed)
    result["last_checkpoint_skipped_by_overlap"] = bool(skipped_by_overlap)
    result["last_base_count"] = int(base_count)
    result["last_update_count"] = int(update_count)
    return result


def _candidate_base_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    alias_map, prompt_candidates = _candidate_aliases([candidate])
    _ = alias_map
    payload = dict(prompt_candidates[0]) if prompt_candidates else {
        "community_id": str(candidate.get("community_id") or ""),
        "title": str(candidate.get("title") or ""),
        "scope": str(candidate.get("scope") or ""),
    }
    payload["entry_type"] = "candidate_base"
    return _compact_append_log_entry(payload)


def _candidate_stats(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(candidate.get("title") or ""),
        "source_count": int(candidate.get("source_count") or 0),
        "assigned_intent_count": int(candidate.get("assigned_intent_count") or 0),
        "maturity": str(candidate.get("maturity") or ""),
        "future_coverage": _candidate_list(candidate.get("future_coverage"))[:10],
        "mid_topics": _candidate_list(candidate.get("mid_topics"))[:10],
        "specific_topics": _candidate_list(candidate.get("specific_topics"))[:10],
    }


def _candidate_update_entry(
    candidate: dict[str, Any],
    *,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any] | None:
    community_id = str(candidate.get("community_id") or "")
    if not community_id or not previous:
        return None
    previous_topics = set(_candidate_list(previous.get("future_coverage")))
    current_topics = _candidate_list(current.get("future_coverage"))
    new_topics = [item for item in current_topics if item not in previous_topics][:4]
    if not new_topics:
        return None
    return {
        "entry_type": "candidate_update",
        "community_id": community_id,
        "absorbed": new_topics,
    }


def _candidate_assignment_update_entry(
    *,
    community_id: str,
    assignment: dict[str, Any],
    previous: dict[str, Any],
    topic_intent: dict[str, Any],
) -> dict[str, Any] | None:
    if not _assignment_should_update_candidate_context(assignment):
        return None
    previous_topics = set(_candidate_list(previous.get("future_coverage")))
    current_topics = _assignment_absorbed_topics(topic_intent)
    new_topics = [item for item in current_topics if item not in previous_topics][:4]
    if not community_id or not new_topics:
        return None
    return {
        "entry_type": "candidate_update",
        "community_id": community_id,
        "absorbed": new_topics,
    }


def _assignment_absorbed_topics(topic_intent: dict[str, Any]) -> list[str]:
    return _ordered_unique(
        [
            *_candidate_list(topic_intent.get("parent_themes")),
            *_candidate_list(topic_intent.get("broad_topics")),
            *_candidate_list(topic_intent.get("mid_topics")),
        ]
    )[:4]


def _assignment_should_update_candidate_context(assignment: dict[str, Any]) -> bool:
    fit_type = str(assignment.get("fit_type") or "")
    if fit_type == "adjacent_context":
        return False
    try:
        weight = float(assignment.get("weight") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    if weight < 0.65:
        return False
    return fit_type in {"existing_direction", "new_subtopic", "broader_parent"}


def _compact_append_log_entry(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            if value.strip():
                result[key] = value
            continue
        if isinstance(value, list):
            if value:
                result[key] = value
            continue
        if value is not None:
            result[key] = value
    return result


class CommunityCardBuilder:
    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        candidate_provider: CommunitySemanticCandidateProvider | None = None,
        reranker_client: RerankerClient | None = None,
        target: str = "prod",
        on_communities_updated: Callable[[list[GraphIndexCommunity]], Awaitable[None]] | None = None,
        candidate_order_store: AssignmentCandidateOrderStore | None = None,
        community_id_factory: Callable[[str, int, str], str] | None = None,
    ):
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_community_assignment")
        self._candidate_provider = candidate_provider
        self._reranker_client = reranker_client
        self._target = target
        self._on_communities_updated = on_communities_updated
        self._candidate_order_store = candidate_order_store
        self._community_id_factory = community_id_factory

    async def build(
        self,
        *,
        adapter_name: str,
        cards: list[CognitiveCard],
        existing_communities: list[GraphIndexCommunity],
    ) -> CognitiveCommunityBuildResult:
        communities = _drafts_from_existing(existing_communities)
        merge_seed_community_drafts(communities, seed_community_drafts(adapter_name))
        assignments: list[CommunityAssignment] = []
        intent_count = 0
        validation_errors = 0
        candidate_ledger_diagnostics: list[dict[str, Any]] = []
        self._candidate_ledger_diagnostics = candidate_ledger_diagnostics
        with langfuse_observation(
            name="kg.community_card.build",
            as_type="span",
            input={"cards": len(cards), "existing_communities": len(existing_communities)},
        ):
            if self._candidate_order_store is not None:
                candidate_ledger_diagnostics.append(
                    self._candidate_order_store.checkpoint_if_needed(adapter_name=adapter_name)
                )
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
                        decision = await self._decide_assignment(card, topic_intent, candidates, communities)
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
                        community_id_factory=self._community_id_factory,
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
            if self._candidate_order_store is not None:
                candidate_ledger_diagnostics.append(
                    self._candidate_order_store.checkpoint_if_needed(adapter_name=adapter_name)
                )
            graph_communities = [
                _graph_community_from_draft(adapter_name, community)
                for community in communities.values()
                if community.assigned_intents or getattr(community, "origin", "") == "seed"
            ]
            documents = [_community_document(community) for community in graph_communities]
            diagnostics = {
                "cards": len(cards),
                "intents": intent_count,
                "assignments": len(assignments),
                "communities": len(graph_communities),
                "assignment_validation_errors": validation_errors,
                "candidate_recall": "semantic_community",
                "seed_candidates": len([item for item in communities.values() if getattr(item, "origin", "") == "seed"]),
                "candidate_rerank": "external_reranker" if self._reranker_client is not None else "disabled",
                "community_builder": "cognitive_card_assignment_v1",
                "candidate_ledger": _aggregate_candidate_ledger_diagnostics(candidate_ledger_diagnostics),
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
                limit=MAX_SEMANTIC_ASSIGNMENT_CANDIDATES,
            )
            for candidate in semantic_candidates:
                community_id = str(candidate.get("community_id") or "")
                if community_id and community_id not in seen:
                    seen.add(community_id)
                    candidates.append(candidate)
        return await self._rerank_candidates(topic_intent, candidates)

    async def _rerank_candidates(
        self,
        topic_intent: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(candidates) < RERANK_MIN_ASSIGNMENT_CANDIDATES or self._reranker_client is None:
            return candidates[:MAX_ASSIGNMENT_CANDIDATES]
        query = assignment_query_text(topic_intent)
        documents = [_candidate_rerank_text(candidate) for candidate in candidates]
        with langfuse_observation(
            name="kg.community_assignment.rerank_candidates",
            as_type="span",
            input={
                "candidate_count": len(candidates),
                "top_n": min(MAX_ASSIGNMENT_CANDIDATES, len(candidates)),
                "candidate_titles": [candidate.get("title") for candidate in candidates[:20]],
            },
        ):
            response = await self._reranker_client.rerank(
                query=query,
                documents=documents,
                top_n=min(MAX_ASSIGNMENT_CANDIDATES, len(candidates)),
            )
            ranked: list[dict[str, Any]] = []
            seen_indexes: set[int] = set()
            for result in response.results:
                if 0 <= result.index < len(candidates):
                    candidate = dict(candidates[result.index])
                    candidate["rerank_score"] = round(float(result.relevance_score), 6)
                    candidate["retrieval_lane"] = str(candidate.get("retrieval_lane") or "") + "|reranked"
                    ranked.append(candidate)
                    seen_indexes.add(result.index)
            if len(ranked) < min(MAX_ASSIGNMENT_CANDIDATES, len(candidates)):
                ranked.extend(
                    candidate
                    for index, candidate in enumerate(candidates)
                    if index not in seen_indexes
                )
            selected = ranked[:MAX_ASSIGNMENT_CANDIDATES]
            langfuse_update_span(
                output={
                    "raw_candidates": len(candidates),
                    "reranked_candidates": len(ranked),
                    "selected_candidates": len(selected),
                    "dropped_candidates": max(0, len(candidates) - len(selected)),
                    "top_candidates": [
                        {
                            "community_id": candidate.get("community_id"),
                            "title": candidate.get("title"),
                            "origin": candidate.get("origin"),
                            "retrieval_score": candidate.get("retrieval_score"),
                            "rerank_score": candidate.get("rerank_score"),
                        }
                        for candidate in selected[:10]
                    ],
                },
                status_message="completed",
            )
            return selected

    async def _decide_assignment(
        self,
        card: CognitiveCard,
        topic_intent: dict[str, Any],
        candidates: list[dict[str, Any]],
        communities: dict[str, Any],
    ) -> dict[str, Any]:
        max_attach = COMPLEX_MAX_ATTACH if _is_complex_intent(topic_intent) else DEFAULT_MAX_ATTACH
        deduped_candidates = _dedupe_assignment_candidates(candidates)
        if self._candidate_order_store is None:
            raise RuntimeError("assignment candidate ledger is required; configure Redis-backed AssignmentCandidateOrderStore")
        candidate_append_log, ledger_diagnostics = self._candidate_order_store.prepare_append_log(
            adapter_name=card.adapter_name,
            candidates=deduped_candidates,
            allow_checkpoint=False,
        )
        active_candidate_ids = set(communities)
        candidate_append_log = [
            item
            for item in candidate_append_log
            if str(item.get("community_id") or "") in active_candidate_ids
        ]
        validation_candidates = _validation_candidates_from_append_log(candidate_append_log)
        candidate_ids = [str(candidate.get("community_id") or "") for candidate in candidates if candidate.get("community_id")]
        deduped_candidate_ids = [
            str(candidate.get("community_id") or "")
            for candidate in deduped_candidates
            if candidate.get("community_id")
        ]
        prompt_candidate_ids = [
            str(candidate.get("community_id") or "")
            for candidate in validation_candidates
            if candidate.get("community_id")
        ]
        prompt = {
            "candidate_append_log": candidate_append_log,
            "topic_intent": assignment_prompt_topic_intent(topic_intent, max_attach=max_attach),
            "max_attach": max_attach,
        }
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=ASSIGNMENT_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=ASSIGNMENT_MAX_TOKENS,
            json_schema=ASSIGNMENT_SCHEMA,
            metadata={
                "task": "kg_community_assignment",
                "raw_candidate_count": len(candidate_ids),
                "deduped_candidate_count": len(deduped_candidate_ids),
                "prompt_candidate_count": len(validation_candidates),
                "duplicate_candidate_count": max(0, len(candidate_ids) - len(set(candidate_ids))),
                "prompt_candidate_id_sample": prompt_candidate_ids[:5],
                "candidate_ledger": ledger_diagnostics,
            },
            use_cache=True,
        )
        ledger_sink = getattr(self, "_candidate_ledger_diagnostics", None)
        if isinstance(ledger_sink, list):
            ledger_sink.append(dict(ledger_diagnostics))
        response = await self._llm.generate(request)
        decision = response.structured_output
        if not isinstance(decision, dict):
            raise RuntimeError(f"assignment output is not object: card={card.cognitive_card_id}")
        try:
            validate_assignment_decision(decision, validation_candidates, topic_intent=topic_intent)
        except Exception as exc:
            repaired = await self._llm.repair_with_feedback(
                request,
                response,
                [str(exc)],
                instruction=(
                    "上一轮 Community Assignment 输出未通过业务校验。"
                    "只修复 JSON 结构和字段合规性，不改变业务裁决含义。"
                    "顶层只能包含 assignments 和 new_communities。"
                    "action=attach_existing 时 community_id 必须引用 candidate_append_log 中真实存在的 candidate_base community_id；"
                    "action=create_new 时 community_id 必须引用 new_communities 中的 client_id。"
                    "每条 assignment 必须包含 fit_type；attach_existing 不能使用 new_parent_topic，"
                    "create_new 必须使用 new_parent_topic。"
                    "不要创建只表示行情表现的 L0 community，例如市场行情、板块异动、个股涨跌、成交放量、"
                    "资金流向、ETF表现、概念异动或盘面表现；如果 intent 是上涨、下跌、反弹、涨停、"
                    "成交放量或资金流入，必须挂入已有驱动主题，或创建更具体且可长期复用的驱动型父主题。"
                ),
                retry_reason="community_assignment_validation_invalid",
            )
            decision = repaired.structured_output
            if not isinstance(decision, dict):
                raise RuntimeError(f"assignment repair output is not object: card={card.cognitive_card_id}") from exc
            validate_assignment_decision(decision, validation_candidates, topic_intent=topic_intent)
        self._candidate_order_store.record_assignment_decision(
            adapter_name=card.adapter_name,
            decision=decision,
            topic_intent=topic_intent,
        )
        return decision


def _validation_candidates_from_append_log(candidate_append_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidate_append_log:
        if item.get("entry_type") != "candidate_base":
            continue
        community_id = str(item.get("community_id") or "")
        if not community_id or community_id in seen:
            continue
        seen.add(community_id)
        result.append({"community_id": community_id, "title": str(item.get("title") or "")})
    return result


def _aggregate_candidate_ledger_diagnostics(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "calls": 0,
            "redis_available_count": 0,
            "redis_unavailable_count": 0,
            "appended_base_total": 0,
            "appended_update_total": 0,
            "checkpointed_count": 0,
            "checkpoint_skipped_by_overlap_count": 0,
            "max_append_log_entries": 0,
            "max_base_count": 0,
            "max_update_count": 0,
            "error_types": {},
        }
    error_types: dict[str, int] = {}
    for item in items:
        error = str(item.get("error") or "").strip()
        if error:
            error_types[error] = error_types.get(error, 0) + 1
    return {
        "calls": len(items),
        "redis_available_count": sum(1 for item in items if item.get("redis_available") is True),
        "redis_unavailable_count": sum(1 for item in items if item.get("redis_available") is not True),
        "appended_base_total": sum(int(item.get("appended_base") or 0) for item in items),
        "appended_update_total": sum(int(item.get("appended_update") or 0) for item in items),
        "checkpointed_count": sum(1 for item in items if item.get("checkpointed") is True),
        "checkpoint_skipped_by_overlap_count": sum(1 for item in items if item.get("checkpoint_skipped_by_overlap") is True),
        "max_append_log_entries": max(int(item.get("candidate_append_log_entries") or 0) for item in items),
        "max_base_count": max(int(item.get("candidate_append_log_base_count") or 0) for item in items),
        "max_update_count": max(int(item.get("candidate_append_log_update_count") or 0) for item in items),
        "error_types": error_types,
    }


def _dedupe_assignment_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        community_id = str(candidate.get("community_id") or "")
        if not community_id:
            continue
        current = deduped.get(community_id)
        if current is None:
            deduped[community_id] = dict(candidate)
            continue
        deduped[community_id] = _merge_assignment_candidate(current, candidate)
    return list(deduped.values())


def _merge_assignment_candidate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if value in (None, "", [], {}):
            continue
        if key in {"retrieval_score", "rerank_score"}:
            merged[key] = max(float(merged.get(key) or 0.0), float(value or 0.0))
            continue
        if key == "retrieval_lane":
            lanes = [
                item
                for item in [str(merged.get(key) or ""), str(value or "")]
                if item
            ]
            merged[key] = "|".join(_ordered_unique(lanes))
            continue
        if key in {"canonical_labels", "parent_themes", "broad_topics", "mid_topics", "specific_topics", "future_coverage"}:
            merged[key] = _ordered_unique([*_candidate_list(merged.get(key)), *_candidate_list(value)])
            continue
        if not merged.get(key):
            merged[key] = value
    return merged


def _candidate_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    text = str(value).strip()
    return [text] if text else []


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _candidate_rerank_text(candidate: dict[str, Any]) -> str:
    parts = [
        f"title: {candidate.get('title') or ''}",
        f"origin: {candidate.get('origin') or ''}",
        f"scope: {candidate.get('scope') or candidate.get('directory_scope') or ''}",
        f"canonical_labels: {'；'.join(candidate.get('canonical_labels') or [])}",
        f"coverage: {candidate.get('coverage_contract') or candidate.get('coverage_summary') or ''}",
        f"parent_themes: {'；'.join(candidate.get('parent_themes') or [])}",
        f"broad_topics: {'；'.join(candidate.get('broad_topics') or [])}",
        f"mid_topics: {'；'.join(candidate.get('mid_topics') or [])}",
        f"future_coverage: {'；'.join(candidate.get('future_coverage') or [])}",
        f"include_rules: {'；'.join(candidate.get('include_rules') or [])}",
        f"exclude_rules: {'；'.join(candidate.get('exclude_rules') or [])}",
        f"granularity_note: {candidate.get('granularity_note') or ''}",
        f"recent_examples: {'；'.join(str(item.get('title') or '') for item in candidate.get('recent_examples') or [] if isinstance(item, dict))}",
    ]
    return "\n".join(part for part in parts if not part.endswith(": "))
