"""Application service for Cognitive Card based community indexing."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from contextlib import nullcontext
from dataclasses import replace
from collections.abc import Awaitable, Callable
from typing import Any

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.infrastructure.config import settings
from src.domain.knowledge.cognitive_index import (
    ASSIGNMENT_SCHEMA,
    ASSIGNMENT_SYSTEM_PROMPT,
    ASSIGNMENT_MAX_TOKENS,
    ASSIGNMENT_RERANK_MIN_KEEP,
    ASSIGNMENT_RETRIEVAL_SCORE_FLOOR,
    COGNITIVE_CARD_MAX_TOKENS,
    COGNITIVE_CARD_SCHEMA,
    COGNITIVE_CARD_SYSTEM_PROMPT,
    COMPLEX_MAX_ATTACH,
    DEFAULT_MAX_ATTACH,
    MAX_ASSIGNMENT_CANDIDATES,
    MAX_SEMANTIC_ASSIGNMENT_CANDIDATES,
    RERANK_MIN_ASSIGNMENT_CANDIDATES,
    ASSIGNMENT_RERANK_SCORE_FLOOR,
    ASSIGNMENT_RERANK_TOP_DELTA,
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
from src.domain.knowledge.retrieval_profile import profile_span
from src.infrastructure.clients.reranker import RerankerClient
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET, SEMANTIC_COLLECTION_COMMUNITY
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config.settings import REDIS_URL
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import langfuse_observation, langfuse_update_span
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusTypedHybridStore
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridDocument


ASSIGNMENT_LEDGER_SCHEMA_VERSION = "candidate_append_log_v1"
ASSIGNMENT_LEDGER_TTL_SECONDS = 7 * 24 * 60 * 60
ASSIGNMENT_LEDGER_MAX_BASE_CANDIDATES = 50
ASSIGNMENT_LEDGER_KEEP_BASE_CANDIDATES = 10
ASSIGNMENT_LEDGER_CHECKPOINT_REUSE_OVERLAP = 0.85
ASSIGNMENT_BUCKET_SCHEMA_VERSION = "assignment_bucket_planning_v1"
ASSIGNMENT_BUCKET_TTL_SECONDS = 7 * 24 * 60 * 60
ASSIGNMENT_BUCKET_MAX_COUNT = 80
ASSIGNMENT_BUCKET_AUTO_MERGE_CANDIDATE_LIMIT = 24
ASSIGNMENT_BUCKET_AUTO_MERGE_BATCH_SIZE = 15
ASSIGNMENT_BUCKET_CONCURRENCY = 20
ASSIGNMENT_BUCKET_PLANNING_BATCH_SIZE = 20
ASSIGNMENT_BUCKET_LOCK_TIMEOUT_SECONDS = 900
ASSIGNMENT_BUCKET_LOCK_BLOCKING_TIMEOUT_SECONDS = 900
ASSIGNMENT_BUCKET_SEMANTIC_DIRECT_THRESHOLD = 0.60
ASSIGNMENT_BUCKET_SEMANTIC_DIRECT_MARGIN = 0.12
ASSIGNMENT_BUCKET_SEMANTIC_CANDIDATE_THRESHOLD = 0.25
ASSIGNMENT_BUCKET_SEMANTIC_LIMIT = 5
ASSIGNMENT_BUCKET_SEMANTIC_TARGET_TYPE = "assignment_bucket"
ASSIGNMENT_BUCKET_SEMANTIC_SOURCE_TYPE = "assignment_bucket_cache"
BUCKET_PLANNING_MAX_TOKENS = 2000
BUCKET_MERGE_MAX_TOKENS = 1200
BUCKET_REPLAY_MAX_TOKENS = 5000

BUCKET_PLANNING_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_theme": {"type": "string"},
                    "bucket_id": {"type": "string"},
                },
                "required": [
                    "canonical_theme",
                    "bucket_id",
                ],
                "additionalProperties": False,
            },
        },
        "new_buckets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bucket_id": {"type": "string"},
                    "bucket_title": {"type": "string"},
                    "scope": {"type": "string"},
                    "canonical_themes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["bucket_id", "bucket_title", "scope", "canonical_themes"],
                "additionalProperties": False,
            },
        },
        "theme_bucket_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_theme": {"type": "string"},
                    "bucket_id": {"type": "string"},
                },
                "required": ["canonical_theme", "bucket_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["assignments", "new_buckets", "theme_bucket_updates"],
    "additionalProperties": False,
}

BUCKET_MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "merge_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_bucket_id": {"type": "string"},
                    "target_bucket_id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "source_bucket_id",
                    "target_bucket_id",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "rejected_merge_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_bucket_id": {"type": "string"},
                    "target_bucket_id": {"type": "string"},
                },
                "required": ["source_bucket_id", "target_bucket_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merge_actions", "rejected_merge_candidates"],
    "additionalProperties": False,
}

BUCKET_REPLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "needs_review"]},
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "conflict_type": {
                        "type": "string",
                        "enum": ["high_risk_conflict", "possible_conflict", "merge_needed"],
                    },
                    "intent_ids": {"type": "array", "items": {"type": "string"}},
                    "community_ids": {"type": "array", "items": {"type": "string"}},
                    "bucket_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "recommended_action": {
                        "type": "string",
                        "enum": ["merge_buckets", "adjust_bucket_scope", "accept_difference"],
                    },
                },
                "required": [
                    "conflict_type",
                    "intent_ids",
                    "community_ids",
                    "bucket_ids",
                    "reason",
                    "recommended_action",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["status", "conflicts", "summary"],
    "additionalProperties": False,
}

BUCKET_PLANNING_SYSTEM_PROMPT = """你是金融知识图谱的 Community Assignment 并发分桶规划器。

你的任务不是创建 community，也不是判断最终归档结果。
你的任务是根据一批 topic intent 的语义关系，快速规划哪些 intent 必须放在同一个串行执行 bucket 中，哪些 intent 可以放到不同 bucket 并发处理。

核心原则：
- bucket 是执行调度通道，不是业务主题，不是检索对象，不是 community。
- 同一 bucket 内的 intent 会串行执行，目的是避免相近主题并发创建重复 community。
- 不同 bucket 之间会并发执行，因此只有相互影响较小的 intent 才能拆开。
- 如果多个 intent 可能竞争同一批 community，必须放入同一个 bucket。
- 如果多个 intent 只是同一父主题下的不同子方向，必须放入同一个 bucket。
- 分桶目标是降低并发写冲突，同时保持中等粒度；不是越粗越安全，也不是越细越好。
- 允许创建多个可复用 bucket。100 个 intent 通常不应只形成几个大桶；如果大量 intent 被塞进同一 bucket，说明你可能在使用兜底桶。
- 单个 bucket 不应承接本批过多互不竞争的 intent；除非它们高度同质且会竞争同一批 community，否则应拆成多个更具体、可复用的写冲突域。
- 可能竞争同一 community 的 intent 必须放入同一 bucket；明显不会竞争同一 community 的 intent 不应为了“略粗”而放入同一 bucket。
- 如果多个 intent 来自同一 event_thread、同一交易动作或同一政策链条，默认应放入同一个 bucket，除非它们明确属于互不影响的长期主线。
- 交易/动作属性优先于行业属性。比如“跨界半导体存储并购”既有半导体标签也有并购动作，但它会竞争并购重组 community，应优先进入并购 bucket，而不是单独创建半导体并购 bucket。
- 风险/政策/融资/并购/供应冲击/业绩这些会改变写入路径的主流程信号，优先用于分桶；行业、公司、产品只是辅助信号，不能单独决定拆桶。
- 如果一个 intent 同时属于两个方向，应选择更可能与已有 intent 竞争同一 community 的 bucket；不要因为多了一个行业标签就拆出新 bucket。
- bucket 粒度应大于具体新闻主题，小于全市场大类。
- bucket 是写冲突域，不是新闻大类、行业大类或兜底分类；只有可能竞争同一批 community 的 intent 才应进入同一 bucket。
- 禁止创建或复用“宏观/政策/市场/海外/综合/其他”这类兜底桶来承接互不竞争的杂项 intent。
- “宏观流动性”“产业政策”“市场行情”这类 bucket 只有在当前 intent 确实会竞争同一类 community 时才能使用；普通事故、外交人事、区域数据、单条市场监测、泛政策新闻不应被硬塞进去。
- 如果一个 intent 与所有已有 bucket 都没有明显写冲突，但未来可稳定承接同类 intent，可以创建新的可复用 bucket；不要为了避免单 intent bucket 而放进不相关的大桶。
- 如果某个 bucket 同时承接风险、ETF、估值修复、行业经营等不同子方向，bucket_title 和 scope 必须用中性父级表达，不能只用“风险”等窄词。
- 不要把单家公司、单个项目、单次行情、单个产品、单笔交易创建成 bucket。
- 不要创建只承接单个 intent 的一次性 bucket；但当它与所有已有 bucket 都没有写冲突风险，且未来可稳定承接同类 intent 时，应创建单 intent 的新可复用 bucket，而不是硬塞进不相关大桶。
- bucket_id 必须是稳定调度 ID，只能表达长期语义边界；禁止包含日期、时间、批次号、新闻 ID、公司名或一次性事件。
- bucket_id 使用短英文 snake_case，例如 bucket_ma、bucket_broker_risk、bucket_ai_infra、bucket_macro_liquidity。
- 如果已有 bucket 能承接新增 theme，应优先复用已有 bucket；但已有 bucket 过宽或只弱相关时，不要为了复用而把不相关 intent 塞进去。
- 如果新增 theme 与已有 bucket 都不匹配，可以创建新 bucket。
- 首轮没有已有 bucket 时，也要创建可复用的中等粒度 bucket；不要为单个 intent 创建过细 bucket，也不要创建能吞下大量互不竞争 intent 的大桶。
- 不要在本任务中判断 bucket 合并；bucket merge 是独立任务，不在 planning 输出中处理。
- topic_intent_signatures 只包含分桶所需的压缩主题信号，不包含证据 ID、source ID、chunk_index、长 summary 或完整细粒度标签。
- context_hint 是从 summary/raw_theme 压缩出的短上下文，只用于帮助上提父级 bucket 边界，不能当作新闻全文。
- semantic_bucket_candidates 是 Milvus 语义缓存召回的中等相似 bucket，只是优先复用参考；如果候选 scope 不匹配，仍应创建新 bucket。
- existing_bucket_catalog 只包含已有 bucket 的 bucket_id、bucket_title、scope；历史 canonical_themes 只用于系统缓存命中，不传入 LLM。
- 对相近 theme 必须输出 canonical_theme，后续会缓存 canonical_theme -> bucket_id。
- canonical_theme 必须是简短中文主题名，优先使用输入里的中文 parent_themes / raw_theme 归一化结果；不要输出英文短码、拼音、snake_case 或内部 ID。
- 这是执行调度的轻量分类任务，不要展开分析过程，不要输出解释，只做最小必要判断。

输出要求：
- assignments 必须与输入 topic_intent_signatures 一一对应，并严格保持相同顺序。
- assignments 每项只输出 canonical_theme、bucket_id 两个字段。
- 不要给 assignment 输出 intent_id、reason、confidence、action、bucket_title 或其他解释字段。
- new_buckets 只在确实创建新 bucket 时输出；已有 bucket 不要重复输出。
- new_buckets 的 scope 必须描述可长期复用的并发调度边界，不能只描述某个细行业、某家公司或某次事件。
- theme_bucket_updates 只输出额外同义 theme 映射；如果没有额外映射，输出空数组。
- 不允许输出 merge_suggestions、reason、confidence 或任何解释字段。
- 输出必须尽量紧凑，禁止冗长解释。

你必须只输出符合 JSON Schema 的 JSON 对象，不要输出 Markdown、解释文字或代码块。"""

BUCKET_MERGE_SYSTEM_PROMPT = """你是金融知识图谱的 Bucket 合并规划器。

bucket 是 Community Assignment 的并发调度通道，不是 community，不是业务主题，不是检索对象。

你的任务是只判断输入 merge_candidates 中列出的候选 bucket pair 是否应该合并，从而减少并发执行时的重复 community 创建风险。

合并原则：
- 如果多个 bucket 的 topic intent 经常归入同一个 community，应合并。
- 如果多个 bucket 只是同一父主题下的子方向，应合并。
- 如果 bucket 只代表单家公司、单个项目、单次行情、单笔交易或单个产品，应合并到更大的父级 bucket。
- 如果两个 bucket 的 scope 高度重叠，应合并。
- 如果两个 bucket 只是名称不同但承接同一类 intent，应合并。
- 不要为了减少 bucket 数量而强行合并语义边界明显不同的 bucket。
- 合并后只影响未来并发调度，不要求重算历史 assignment，不修改历史 community。
- 合并时直接删除旧 bucket，将旧 bucket 的 canonical themes 迁移到目标 bucket。
- 不输出 merged_to，不保留旧 bucket 重定向。
- 只能在输入 merge_candidates 给出的 source_bucket_id / target_bucket_id 对中选择是否合并。
- 不要输出 updated_buckets、theme_bucket_updates 或任何 catalog 重写内容；系统会自动迁移 source 的 canonical themes。
- merge_actions 只输出 source_bucket_id、target_bucket_id、confidence。
- rejected_merge_candidates 只输出被拒绝候选对的 source_bucket_id、target_bucket_id。
- 不要输出 reason、解释或长文本。

你必须只输出符合 JSON Schema 的 JSON 对象，不要输出 Markdown、解释文字或代码块。"""

BUCKET_REPLAY_SYSTEM_PROMPT = """你是金融知识图谱的 Bucket Planning 一致性审查器。

你会收到：
1. 当前已有 Cognitive Card 的 topic intent 摘要；
2. 当前串行 Community Assignment 的结果；
3. Bucket Planning 回放产生的 bucket assignment；
4. 当前已有 community 摘要。

你的任务是判断 bucket 并发规划是否会破坏串行归档质量。

审查原则：
- bucket 只是并发执行通道，不是 community。
- 你不需要要求 bucket 名称与 community 名称一致。
- 你需要检查：串行结果中最终归入同一 community 的相近 intent，是否被 bucket 回放分到了互相隔离的 bucket。
- 如果相近 intent 被拆到不同 bucket，且它们并发执行时可能创建重复 community，必须标记为 high_risk_conflict。
- 如果多个 bucket 高频指向同一 community 或同一批 canonical theme，说明 bucket 过细，应标记为 merge_needed。
- 如果差异只是名称不同，但不会影响并发安全，不应标记为冲突。
- 不允许根据固定样本名称做特殊判断，必须基于输入中的 intent、community 和 bucket 语义关系判断。

你必须只输出符合 JSON Schema 的 JSON 对象，不要输出 Markdown、解释文字或代码块。"""


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
            "time_grounding_instruction": (
                "source_published_at 是当前新闻发布时间。"
                "chunk_text 中出现今年、去年、明年、本月、上月或未带年份的月份时，可结合 source_published_at 理解时间；"
                "如果无法从 chunk_text 或 source_published_at 推导出年份，不要补年份。"
            ),
            "source_published_at": payload.get("published_at") or "",
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
        max_chars: int | None = None,
        checkpoint_reuse_overlap: float = ASSIGNMENT_LEDGER_CHECKPOINT_REUSE_OVERLAP,
    ) -> None:
        self._target = target
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._max_base_candidates = max_base_candidates
        self._keep_base_candidates = keep_base_candidates
        self._checkpoint_reuse_overlap = checkpoint_reuse_overlap

    def prepare_append_log(
        self,
        *,
        adapter_name: str,
        candidates: list[dict[str, Any]],
        allow_checkpoint: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        redis_client = self._redis_client()
        with self._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="candidate_ledger"),
        ):
            return self._prepare_append_log_locked(
                redis_client=redis_client,
                adapter_name=adapter_name,
                candidates=candidates,
                allow_checkpoint=allow_checkpoint,
            )

    def _prepare_append_log_locked(
        self,
        *,
        redis_client: Any,
        adapter_name: str,
        candidates: list[dict[str, Any]],
        allow_checkpoint: bool,
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
            stats[community_id] = current_stats
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
        diagnostics["candidate_append_log_redirect_count"] = _candidate_append_log_redirect_count(append_log)
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
        with self._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="candidate_ledger"),
        ):
            return self._checkpoint_if_needed_locked(redis_client, adapter_name=adapter_name)

    def _checkpoint_if_needed_locked(self, redis_client: Any, *, adapter_name: str) -> dict[str, Any]:
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
            "candidate_append_log_redirect_count": _candidate_append_log_redirect_count(append_log),
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
        with self._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="candidate_ledger"),
        ):
            self._record_assignment_decision_locked(
                redis_client,
                adapter_name=adapter_name,
                decision=decision,
                topic_intent=topic_intent,
            )

    def _record_assignment_decision_locked(
        self,
        redis_client: Any,
        *,
        adapter_name: str,
        decision: dict[str, Any],
        topic_intent: dict[str, Any] | None = None,
    ) -> None:
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
            if not isinstance(assignment, dict):
                continue
            accepted_ids: list[str] = []
            if assignment.get("action") == "attach_existing":
                accepted_ids.append(str(assignment.get("community_id") or ""))
            elif assignment.get("action") == "create_parent_and_absorb_existing":
                accepted_ids.extend(str(item) for item in assignment.get("absorb_community_ids") or [])
            for community_id in _ordered_unique(item.strip() for item in accepted_ids if item.strip()):
                counters.setdefault(community_id, {"retrieved": 0, "accepted": 0})
                counters[community_id]["accepted"] = int(counters[community_id].get("accepted") or 0) + 1
                changed = True
        if not changed:
            return
        ledger["candidate_append_log"] = append_log
        ledger["candidate_stats"] = stats
        ledger["candidate_counters"] = counters
        self._save_ledger(redis_client, adapter_name=adapter_name, ledger=ledger)

    def record_community_redirects(
        self,
        *,
        adapter_name: str,
        redirects: list[dict[str, Any]],
        target_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        redis_client = self._redis_client()
        with self._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="candidate_ledger"),
        ):
            return self._record_community_redirects_locked(
                redis_client,
                adapter_name=adapter_name,
                redirects=redirects,
                target_candidates=target_candidates,
            )

    def _record_community_redirects_locked(
        self,
        redis_client: Any,
        *,
        adapter_name: str,
        redirects: list[dict[str, Any]],
        target_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
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
        base_ids = _candidate_append_log_base_ids(append_log)
        redirect_keys = _candidate_append_log_redirect_keys(append_log)
        target_by_id = {
            str(candidate.get("community_id") or ""): candidate
            for candidate in target_candidates
            if str(candidate.get("community_id") or "")
        }
        valid_redirects: list[dict[str, Any]] = []
        for redirect in redirects:
            from_id = str(redirect.get("from_community_id") or "").strip()
            to_id = str(redirect.get("to_community_id") or "").strip()
            if not from_id or not to_id or from_id == to_id:
                continue
            if from_id not in base_ids:
                continue
            valid_redirects.append({"from_community_id": from_id, "to_community_id": to_id})
        appended_base = 0
        appended_redirect = 0
        for redirect in valid_redirects:
            to_id = redirect["to_community_id"]
            target_candidate = target_by_id.get(to_id)
            if target_candidate and to_id not in base_ids:
                append_log.append(_candidate_base_entry(target_candidate))
                stats[to_id] = _candidate_stats(target_candidate)
                counters.setdefault(to_id, {"retrieved": 0, "accepted": 0})
                base_ids.add(to_id)
                appended_base += 1
            key = (redirect["from_community_id"], to_id)
            if key in redirect_keys:
                continue
            append_log.append(
                _compact_append_log_entry(
                    {
                        "entry_type": "candidate_redirect",
                        "from_community_id": redirect["from_community_id"],
                        "to_community_id": to_id,
                        "to_title": str((target_by_id.get(to_id) or {}).get("title") or ""),
                        "reason": "merged_into_parent",
                    }
                )
            )
            redirect_keys.add(key)
            appended_redirect += 1
        diagnostics = {
            "redis_available": True,
            "requested_redirects": len(redirects),
            "valid_redirects": len(valid_redirects),
            "appended_base": appended_base,
            "appended_redirect": appended_redirect,
            "candidate_append_log_entries": len(append_log),
            "candidate_append_log_base_count": _candidate_append_log_base_count(append_log),
            "candidate_append_log_update_count": _candidate_append_log_update_count(append_log),
            "candidate_append_log_redirect_count": _candidate_append_log_redirect_count(append_log),
            "phase": "record_redirects",
        }
        if appended_base or appended_redirect:
            ledger["candidate_append_log"] = append_log
            ledger["candidate_stats"] = stats
            ledger["candidate_counters"] = counters
            self._save_ledger(redis_client, adapter_name=adapter_name, ledger=ledger)
        return diagnostics

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    def _ledger_key(self, *, adapter_name: str) -> str:
        return f"kg:assignment_candidate_ledger:{self._target}:{adapter_name}"

    def lock_assignment_update(
        self,
        *,
        adapter_name: str,
        bucket_id: str,
        community_ids: list[str],
    ) -> Any:
        redis_client = self._redis_client()
        lock_names = [
            self._lock_key(adapter_name=adapter_name, name=f"bucket:{bucket_id}")
        ]
        lock_names.extend(
            self._lock_key(adapter_name=adapter_name, name=f"community:{community_id}")
            for community_id in sorted(set(community_ids), key=_stable_community_id_sort_key)
            if community_id
        )
        return _AsyncRedisMultiLock(redis_client, lock_names)

    def _lock_key(self, *, adapter_name: str, name: str) -> str:
        return f"kg:assignment_lock:{self._target}:{adapter_name}:{name}"

    @staticmethod
    def _redis_lock(redis_client: Any, name: str) -> Any:
        if not hasattr(redis_client, "lock"):
            return nullcontext()
        return redis_client.lock(
            name,
            timeout=ASSIGNMENT_BUCKET_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=ASSIGNMENT_BUCKET_LOCK_BLOCKING_TIMEOUT_SECONDS,
        )

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
        if base_count <= self._max_base_candidates:
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
        if _candidate_prefix_overlap_ratio(old_base_ids, selected_ids) >= self._checkpoint_reuse_overlap:
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


class _AsyncNoopLock:
    async def __aenter__(self) -> "_AsyncNoopLock":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _AsyncRedisMultiLock:
    def __init__(self, redis_client: Any, lock_names: list[str]) -> None:
        self._redis_client = redis_client
        self._lock_names = _ordered_unique(lock_names)
        self._locks: list[Any] = []

    async def __aenter__(self) -> "_AsyncRedisMultiLock":
        if not hasattr(self._redis_client, "lock"):
            return self
        deadline = time.monotonic() + ASSIGNMENT_BUCKET_LOCK_BLOCKING_TIMEOUT_SECONDS
        wait_seconds = 0.25
        last_blocked_name = ""
        while True:
            acquired: list[Any] = []
            blocked_name = ""
            try:
                for name in self._lock_names:
                    lock = self._redis_client.lock(
                        name,
                        timeout=ASSIGNMENT_BUCKET_LOCK_TIMEOUT_SECONDS,
                        blocking_timeout=0,
                    )
                    if lock.acquire(blocking=False):
                        acquired.append(lock)
                        continue
                    blocked_name = name
                    last_blocked_name = name
                    break
                if not blocked_name:
                    self._locks = acquired
                    return self
            except Exception:
                self._release_locks(acquired)
                raise
            self._release_locks(acquired)
            if time.monotonic() >= deadline:
                suffix = f": {last_blocked_name}" if last_blocked_name else ""
                raise redis.exceptions.LockError(f"Unable to acquire lock within the time specified{suffix}")
            await asyncio.sleep(wait_seconds)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._release_locks(self._locks)
        self._locks = []
        return False

    @staticmethod
    def _release_locks(locks: list[Any]) -> None:
        for lock in reversed(locks):
            try:
                lock.release()
            except redis.exceptions.LockError:
                pass
            except Exception:
                pass


class AssignmentBucketSemanticCache:
    """Milvus-backed semantic cache for assignment bucket routing."""

    def __init__(
        self,
        *,
        target: str = "prod",
        store: MilvusTypedHybridStore,
        direct_threshold: float = ASSIGNMENT_BUCKET_SEMANTIC_DIRECT_THRESHOLD,
        candidate_threshold: float = ASSIGNMENT_BUCKET_SEMANTIC_CANDIDATE_THRESHOLD,
        limit: int = ASSIGNMENT_BUCKET_SEMANTIC_LIMIT,
        direct_margin: float = ASSIGNMENT_BUCKET_SEMANTIC_DIRECT_MARGIN,
    ) -> None:
        self._target = target
        self._store = store
        self._direct_threshold = float(direct_threshold)
        self._candidate_threshold = float(candidate_threshold)
        self._limit = max(1, int(limit or ASSIGNMENT_BUCKET_SEMANTIC_LIMIT))
        self._direct_margin = float(direct_margin)

    def has_entries(self, *, adapter_name: str) -> bool:
        target_ids = self._store.list_target_ids(
            collection_role=SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET,
            adapter_name=adapter_name,
            target=self._target,
            source_type=ASSIGNMENT_BUCKET_SEMANTIC_SOURCE_TYPE,
            limit=1,
        )
        return bool(target_ids)

    async def resolve(
        self,
        *,
        adapter_name: str,
        topic_intent: dict[str, Any],
        catalog: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        catalog = catalog or {}
        query_text = _bucket_semantic_query_text(topic_intent)
        if not query_text:
            return {"direct": None, "candidates": [], "raw_hits": 0, "reason": "empty_query"}
        with profile_span("assignment_bucket.semantic_cache.embed_query"):
            vectors = await embed_texts([query_text])
        query_vector = vectors[0] if vectors and vectors[0] else []
        if not query_vector:
            return {"direct": None, "candidates": [], "raw_hits": 0, "reason": "empty_vector"}
        hits = self._store.vector_search(
            collection_role=SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET,
            query_vector=query_vector,
            adapter_name=adapter_name,
            target=self._target,
            limit=self._limit,
        )
        theme_keys = {_theme_cache_key(item) for item in _bucket_theme_candidates(topic_intent) if _theme_cache_key(item)}
        candidates: list[dict[str, Any]] = []
        for hit in sorted(hits, key=lambda item: (-float(item.score or 0.0), item.target_id)):
            bucket_id = _safe_bucket_id(hit.metadata.get("bucket_id") or hit.metadata.get("source_id"))
            if not bucket_id:
                continue
            bucket = catalog.get(bucket_id) or {}
            score = float(hit.score or 0.0)
            if score < self._candidate_threshold:
                continue
            canonical_themes = _candidate_list(bucket.get("canonical_themes") or hit.metadata.get("canonical_themes"))
            exact_theme_match = bool(theme_keys & {_theme_cache_key(item) for item in canonical_themes})
            candidates.append(
                {
                    "bucket_id": bucket_id,
                    "bucket_title": str(bucket.get("bucket_title") or hit.metadata.get("bucket_title") or bucket_id),
                    "scope": str(bucket.get("scope") or hit.metadata.get("scope") or ""),
                    "canonical_themes": canonical_themes,
                    "semantic_score": round(score, 4),
                    "semantic_exact_theme_match": exact_theme_match,
                    "semantic_cache_source": "catalog" if bucket_id in catalog else "milvus_metadata",
                }
            )
        direct = None
        if candidates:
            top = candidates[0]
            top_score = float(top.get("semantic_score") or 0.0)
            second_score = float(candidates[1].get("semantic_score") or 0.0) if len(candidates) > 1 else 0.0
            if bool(top.get("semantic_exact_theme_match")):
                direct = top
                top["semantic_direct_reason"] = "exact_theme_match"
            elif top_score >= self._direct_threshold and top_score - second_score >= self._direct_margin:
                direct = top
                top["semantic_direct_reason"] = "score_margin"
        return {
            "direct": direct,
            "candidates": candidates,
            "query_text": query_text,
            "raw_hits": len(hits),
        }

    async def upsert_buckets(
        self,
        *,
        adapter_name: str,
        buckets: list[dict[str, Any]],
        kg_version: str = "",
    ) -> int:
        documents: list[MilvusHybridDocument] = []
        for bucket in buckets:
            bucket_id = _safe_bucket_id(bucket.get("bucket_id"), bucket.get("bucket_title"))
            if not bucket_id:
                continue
            text = _bucket_semantic_document_text(bucket)
            if not text:
                continue
            documents.append(
                MilvusHybridDocument(
                    chunk_id=_bucket_semantic_target_id(adapter_name=adapter_name, bucket_id=bucket_id),
                    text=text,
                    evidence_id="",
                    metadata={
                        "target_id": _bucket_semantic_target_id(adapter_name=adapter_name, bucket_id=bucket_id),
                        "target_type": ASSIGNMENT_BUCKET_SEMANTIC_TARGET_TYPE,
                        "document_type": ASSIGNMENT_BUCKET_SEMANTIC_TARGET_TYPE,
                        "source_type": ASSIGNMENT_BUCKET_SEMANTIC_SOURCE_TYPE,
                        "source_id": bucket_id,
                        "bucket_id": bucket_id,
                        "bucket_title": str(bucket.get("bucket_title") or bucket_id),
                        "scope": str(bucket.get("scope") or ""),
                        "canonical_themes": _candidate_list(bucket.get("canonical_themes")),
                    },
                )
            )
        if not documents:
            return 0
        with profile_span("assignment_bucket.semantic_cache.embed_documents", documents=len(documents)):
            vectors = await embed_texts([document.text for document in documents])
        with profile_span("assignment_bucket.semantic_cache.upsert", documents=len(documents)):
            return self._store.upsert_documents_by_role(
                adapter_name=adapter_name,
                target=self._target,
                documents_by_role={SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET: documents},
                vectors_by_role={SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET: vectors},
                embedding_model=settings.EMBEDDING_MODEL,
                kg_version=kg_version,
            )

    def delete_buckets(self, *, adapter_name: str, bucket_ids: list[str]) -> None:
        target_ids = [
            _bucket_semantic_target_id(adapter_name=adapter_name, bucket_id=bucket_id)
            for bucket_id in _ordered_unique([_safe_bucket_id(item) for item in bucket_ids if item])
        ]
        if not target_ids:
            return
        self._store.delete_documents_by_role(
            collection_role=SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET,
            adapter_name=adapter_name,
            target=self._target,
            target_ids=target_ids,
        )


class AssignmentBucketStore:
    """Redis-backed bucket catalog and theme mapping for assignment scheduling."""

    def __init__(
        self,
        *,
        target: str = "prod",
        redis_client: Any | None = None,
        ttl_seconds: int = ASSIGNMENT_BUCKET_TTL_SECONDS,
        max_buckets: int = ASSIGNMENT_BUCKET_MAX_COUNT,
    ) -> None:
        self._target = target
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._max_buckets = max_buckets

    def snapshot(self, *, adapter_name: str) -> dict[str, Any]:
        redis_client = self._redis_client()
        with AssignmentCandidateOrderStore._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="bucket_catalog"),
        ):
            return self._load_state(redis_client, adapter_name=adapter_name)

    def clear(self, *, adapter_name: str, force_stale_lock: bool = False) -> dict[str, Any]:
        redis_client = self._redis_client()
        state_key = self._state_key(adapter_name=adapter_name)
        lock_key = self._lock_key(adapter_name=adapter_name, name="bucket_catalog")
        if force_stale_lock:
            lock_deleted = int(redis_client.delete(lock_key) or 0)
            deleted = int(redis_client.delete(state_key) or 0)
            return {
                "state_key": state_key,
                "deleted": deleted,
                "lock_key": lock_key,
                "lock_deleted": lock_deleted,
                "force_stale_lock": True,
            }
        with AssignmentCandidateOrderStore._redis_lock(
            redis_client,
            lock_key,
        ):
            deleted = redis_client.delete(state_key)
            return {
                "state_key": state_key,
                "deleted": int(deleted or 0),
                "lock_key": lock_key,
                "lock_deleted": 0,
                "force_stale_lock": False,
            }

    def resolve_known_bucket(self, *, adapter_name: str, themes: list[str]) -> dict[str, Any] | None:
        state = self.snapshot(adapter_name=adapter_name)
        theme_map = _normal_theme_map(state.get("theme_bucket_map"))
        catalog = _normal_bucket_catalog(state.get("bucket_catalog"))
        for theme in themes:
            bucket_id = theme_map.get(_theme_cache_key(theme))
            if bucket_id and bucket_id in catalog:
                bucket = dict(catalog[bucket_id])
                return {
                    "bucket_id": bucket_id,
                    "bucket_title": str(bucket.get("bucket_title") or bucket_id),
                    "canonical_theme": theme,
                    "from_cache": True,
                }
        return None

    def apply_planning_decision(self, *, adapter_name: str, decision: dict[str, Any]) -> dict[str, Any]:
        redis_client = self._redis_client()
        with AssignmentCandidateOrderStore._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="bucket_catalog"),
        ):
            state = self._load_state(redis_client, adapter_name=adapter_name)
            catalog = _normal_bucket_catalog(state.get("bucket_catalog"))
            theme_map = _normal_theme_map(state.get("theme_bucket_map"))
            for bucket in decision.get("new_buckets") or []:
                if not isinstance(bucket, dict):
                    continue
                bucket_id = _safe_bucket_id(bucket.get("bucket_id"), bucket.get("bucket_title"))
                if not bucket_id:
                    continue
                catalog[bucket_id] = _bucket_entry(
                    bucket_id=bucket_id,
                    bucket_title=str(bucket.get("bucket_title") or bucket_id),
                    scope=str(bucket.get("scope") or ""),
                    canonical_themes=_candidate_list(bucket.get("canonical_themes")),
                )
            for assignment in decision.get("assignments") or []:
                if not isinstance(assignment, dict):
                    continue
                bucket_id = _safe_bucket_id(assignment.get("bucket_id"), assignment.get("bucket_title"))
                if not bucket_id:
                    continue
                if bucket_id not in catalog:
                    catalog[bucket_id] = _bucket_entry(
                        bucket_id=bucket_id,
                        bucket_title=str(assignment.get("bucket_title") or bucket_id),
                        scope=str(assignment.get("reason") or ""),
                        canonical_themes=[str(assignment.get("canonical_theme") or "")],
                    )
                canonical_theme = str(assignment.get("canonical_theme") or "").strip()
                if canonical_theme:
                    theme_map[_theme_cache_key(canonical_theme)] = bucket_id
                    catalog[bucket_id]["canonical_themes"] = _ordered_unique(
                        [*catalog[bucket_id].get("canonical_themes", []), canonical_theme]
                    )
            for update in decision.get("theme_bucket_updates") or []:
                if not isinstance(update, dict):
                    continue
                canonical_theme = str(update.get("canonical_theme") or "").strip()
                bucket_id = _safe_bucket_id(update.get("bucket_id"))
                if canonical_theme and bucket_id and bucket_id in catalog:
                    theme_map[_theme_cache_key(canonical_theme)] = bucket_id
                    catalog[bucket_id]["canonical_themes"] = _ordered_unique(
                        [*catalog[bucket_id].get("canonical_themes", []), canonical_theme]
                    )
            state["bucket_catalog"] = catalog
            state["theme_bucket_map"] = theme_map
            self._save_state(redis_client, adapter_name=adapter_name, state=state)
            return {
                "bucket_count": len(catalog),
                "theme_map_count": len(theme_map),
                "over_limit": len(catalog) > self._max_buckets,
            }

    def apply_merge_decision(self, *, adapter_name: str, decision: dict[str, Any]) -> dict[str, Any]:
        redis_client = self._redis_client()
        with AssignmentCandidateOrderStore._redis_lock(
            redis_client,
            self._lock_key(adapter_name=adapter_name, name="bucket_catalog"),
        ):
            state = self._load_state(redis_client, adapter_name=adapter_name)
            catalog = _normal_bucket_catalog(state.get("bucket_catalog"))
            theme_map = _normal_theme_map(state.get("theme_bucket_map"))
            merged = 0
            for action in decision.get("merge_actions") or []:
                if not isinstance(action, dict):
                    continue
                source_id = _safe_bucket_id(action.get("source_bucket_id"))
                target_id = _safe_bucket_id(action.get("target_bucket_id"))
                if not source_id or not target_id or source_id == target_id or target_id not in catalog:
                    continue
                source = catalog.pop(source_id, {})
                migrated = _ordered_unique([
                    *_candidate_list(source.get("canonical_themes")),
                ])
                catalog[target_id]["canonical_themes"] = _ordered_unique(
                    [*catalog[target_id].get("canonical_themes", []), *migrated]
                )
                for theme in migrated:
                    theme_map[_theme_cache_key(theme)] = target_id
                merged += 1
            state["bucket_catalog"] = catalog
            state["theme_bucket_map"] = theme_map
            self._save_state(redis_client, adapter_name=adapter_name, state=state)
            return {"merged": merged, "bucket_count": len(catalog), "theme_map_count": len(theme_map)}

    def lock_assignment_update(
        self,
        *,
        adapter_name: str,
        bucket_id: str,
        community_ids: list[str],
    ) -> Any:
        redis_client = self._redis_client()
        lock_names = [self._lock_key(adapter_name=adapter_name, name=f"bucket:{bucket_id}")]
        lock_names.extend(
            self._lock_key(adapter_name=adapter_name, name=f"community:{community_id}")
            for community_id in sorted(set(community_ids), key=_stable_community_id_sort_key)
            if community_id
        )
        return _AsyncRedisMultiLock(redis_client, lock_names)

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    def _state_key(self, *, adapter_name: str) -> str:
        return f"kg:assignment_bucket_catalog:{self._target}:{adapter_name}"

    def _lock_key(self, *, adapter_name: str, name: str) -> str:
        return f"kg:assignment_bucket_lock:{self._target}:{adapter_name}:{name}"

    def _load_state(self, redis_client: Any, *, adapter_name: str) -> dict[str, Any]:
        raw = redis_client.get(self._state_key(adapter_name=adapter_name))
        if not raw:
            return {
                "schema_version": ASSIGNMENT_BUCKET_SCHEMA_VERSION,
                "bucket_catalog": {},
                "theme_bucket_map": {},
            }
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") != ASSIGNMENT_BUCKET_SCHEMA_VERSION:
            return {
                "schema_version": ASSIGNMENT_BUCKET_SCHEMA_VERSION,
                "bucket_catalog": {},
                "theme_bucket_map": {},
            }
        data["bucket_catalog"] = _normal_bucket_catalog(data.get("bucket_catalog"))
        data["theme_bucket_map"] = _normal_theme_map(data.get("theme_bucket_map"))
        return data

    def _save_state(self, redis_client: Any, *, adapter_name: str, state: dict[str, Any]) -> None:
        state["schema_version"] = ASSIGNMENT_BUCKET_SCHEMA_VERSION
        redis_client.setex(
            self._state_key(adapter_name=adapter_name),
            self._ttl_seconds,
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        )


class CommunityBucketPlanner:
    def __init__(
        self,
        *,
        store: AssignmentBucketStore,
        llm: Any | None = None,
        model: str | None = None,
        planning_batch_size: int = ASSIGNMENT_BUCKET_PLANNING_BATCH_SIZE,
        use_cache: bool = True,
        auto_merge_threshold: int = ASSIGNMENT_BUCKET_MAX_COUNT,
        auto_merge_candidate_limit: int = ASSIGNMENT_BUCKET_AUTO_MERGE_CANDIDATE_LIMIT,
        bucket_thinking: str = "disabled",
        semantic_bucket_cache: AssignmentBucketSemanticCache | None = None,
    ) -> None:
        self._store = store
        self._llm = llm or get_llm_gateway_service()
        self._model = model or settings.KG_ASSIGNMENT_BUCKET_MODEL
        self._planning_batch_size = max(1, int(planning_batch_size or ASSIGNMENT_BUCKET_PLANNING_BATCH_SIZE))
        self._use_cache = bool(use_cache)
        self._auto_merge_threshold = max(1, int(auto_merge_threshold or ASSIGNMENT_BUCKET_MAX_COUNT))
        self._auto_merge_candidate_limit = max(1, int(auto_merge_candidate_limit or ASSIGNMENT_BUCKET_AUTO_MERGE_CANDIDATE_LIMIT))
        self._bucket_thinking = "disabled" if str(bucket_thinking).lower() == "disabled" else "enabled"
        self._semantic_bucket_cache = semantic_bucket_cache

    async def plan(
        self,
        *,
        adapter_name: str,
        intent_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        assignments: dict[str, dict[str, Any]] = {}
        unknown: list[dict[str, Any]] = []
        semantic_stats = {
            "redis_exact_hits": 0,
            "semantic_requests": 0,
            "semantic_raw_hits": 0,
            "semantic_candidate_hits": 0,
            "semantic_direct_hits": 0,
            "semantic_direct_exact_theme_hits": 0,
            "semantic_direct_score_margin_hits": 0,
            "semantic_skipped_empty_scope": 0,
            "semantic_upserted": 0,
        }
        semantic_cache_available = False
        if self._semantic_bucket_cache is not None:
            semantic_cache_available = self._semantic_bucket_cache.has_entries(adapter_name=adapter_name)
        semantic_stats["semantic_scope_has_entries_initial"] = int(semantic_cache_available)
        for ref in intent_refs:
            themes = _bucket_theme_candidates(ref["topic_intent"])
            known = self._store.resolve_known_bucket(adapter_name=adapter_name, themes=themes)
            if known is not None:
                semantic_stats["redis_exact_hits"] += 1
                assignments[ref["intent_id"]] = known
                continue
            semantic_result = None
            if self._semantic_bucket_cache is not None and semantic_cache_available:
                current_catalog = _normal_bucket_catalog(
                    self._store.snapshot(adapter_name=adapter_name).get("bucket_catalog")
                )
                semantic_result = await self._semantic_bucket_cache.resolve(
                    adapter_name=adapter_name,
                    topic_intent=ref["topic_intent"],
                    catalog=current_catalog,
                )
                _record_semantic_bucket_stats(semantic_stats, semantic_result)
                direct = semantic_result.get("direct") if isinstance(semantic_result, dict) else None
                if isinstance(direct, dict) and direct.get("bucket_id"):
                    canonical_theme = _bucket_theme_candidates(ref["topic_intent"])[0] if _bucket_theme_candidates(ref["topic_intent"]) else str(ref["topic_intent"].get("title_candidate") or "")
                    bucket_id = _safe_bucket_id(direct.get("bucket_id"))
                    semantic_stats["semantic_direct_hits"] += 1
                    if direct.get("semantic_direct_reason") == "exact_theme_match":
                        semantic_stats["semantic_direct_exact_theme_hits"] += 1
                    if direct.get("semantic_direct_reason") == "score_margin":
                        semantic_stats["semantic_direct_score_margin_hits"] += 1
                    self._store.apply_planning_decision(
                        adapter_name=adapter_name,
                        decision=_semantic_direct_bucket_decision(
                            canonical_theme=canonical_theme,
                            direct=direct,
                            catalog=current_catalog,
                        ),
                    )
                    assignments[ref["intent_id"]] = {
                        "bucket_id": bucket_id,
                        "bucket_title": str(direct.get("bucket_title") or bucket_id),
                        "canonical_theme": canonical_theme,
                        "from_cache": True,
                        "cache_source": "semantic",
                        "semantic_score": float(direct.get("semantic_score") or 0.0),
                    }
                    continue
            elif self._semantic_bucket_cache is not None:
                semantic_stats["semantic_skipped_empty_scope"] += 1
            signature = _bucket_intent_signature(ref)
            candidates = semantic_result.get("candidates") if isinstance(semantic_result, dict) else None
            if candidates:
                signature["semantic_bucket_candidates"] = candidates
            unknown.append(signature)

        llm_decision: dict[str, Any] = {
            "assignments": [],
            "new_buckets": [],
            "theme_bucket_updates": [],
        }
        if unknown:
            for batch_index, batch in enumerate(_chunks(unknown, self._planning_batch_size), start=1):
                still_unknown: list[dict[str, Any]] = []
                for signature in batch:
                    ref = _ref_by_intent_id(intent_refs, str(signature.get("intent_id") or ""))
                    if ref is None:
                        still_unknown.append(signature)
                        continue
                    known = self._store.resolve_known_bucket(
                        adapter_name=adapter_name,
                        themes=_bucket_theme_candidates(ref["topic_intent"]),
                    )
                    if known is not None:
                        semantic_stats["redis_exact_hits"] += 1
                        assignments[ref["intent_id"]] = known
                    else:
                        refreshed_catalog = _normal_bucket_catalog(
                            self._store.snapshot(adapter_name=adapter_name).get("bucket_catalog")
                        )
                        semantic_result = None
                        if self._semantic_bucket_cache is not None and semantic_cache_available:
                            semantic_result = await self._semantic_bucket_cache.resolve(
                                adapter_name=adapter_name,
                                topic_intent=ref["topic_intent"],
                                catalog=refreshed_catalog,
                            )
                            _record_semantic_bucket_stats(semantic_stats, semantic_result)
                            direct = semantic_result.get("direct") if isinstance(semantic_result, dict) else None
                            if isinstance(direct, dict) and direct.get("bucket_id"):
                                canonical_theme = (
                                    _bucket_theme_candidates(ref["topic_intent"])[0]
                                    if _bucket_theme_candidates(ref["topic_intent"])
                                    else str(ref["topic_intent"].get("title_candidate") or "")
                                )
                                bucket_id = _safe_bucket_id(direct.get("bucket_id"))
                                semantic_stats["semantic_direct_hits"] += 1
                                if direct.get("semantic_direct_reason") == "exact_theme_match":
                                    semantic_stats["semantic_direct_exact_theme_hits"] += 1
                                if direct.get("semantic_direct_reason") == "score_margin":
                                    semantic_stats["semantic_direct_score_margin_hits"] += 1
                                self._store.apply_planning_decision(
                                    adapter_name=adapter_name,
                                    decision=_semantic_direct_bucket_decision(
                                        canonical_theme=canonical_theme,
                                        direct=direct,
                                        catalog=refreshed_catalog,
                                    ),
                                )
                                assignments[ref["intent_id"]] = {
                                    "bucket_id": bucket_id,
                                    "bucket_title": str(direct.get("bucket_title") or bucket_id),
                                    "canonical_theme": canonical_theme,
                                    "from_cache": True,
                                    "cache_source": "semantic",
                                    "semantic_score": float(direct.get("semantic_score") or 0.0),
                                }
                                continue
                        elif self._semantic_bucket_cache is not None:
                            semantic_stats["semantic_skipped_empty_scope"] += 1
                        refreshed_signature = dict(signature)
                        candidates = semantic_result.get("candidates") if isinstance(semantic_result, dict) else None
                        if candidates:
                            refreshed_signature["semantic_bucket_candidates"] = candidates
                        still_unknown.append(refreshed_signature)
                if not still_unknown:
                    continue
                batch_decision = await self._plan_unknown_batch(
                    adapter_name=adapter_name,
                    unknown=still_unknown,
                    batch_index=batch_index,
                    batch_count=(len(unknown) + self._planning_batch_size - 1) // self._planning_batch_size,
                )
                _merge_bucket_planning_decision(llm_decision, batch_decision)
                self._store.apply_planning_decision(adapter_name=adapter_name, decision=batch_decision)
                bucket_catalog = _normal_bucket_catalog(
                    self._store.snapshot(adapter_name=adapter_name).get("bucket_catalog")
                )
                semantic_upserted = await self._sync_semantic_bucket_cache(
                    adapter_name=adapter_name,
                    bucket_catalog=bucket_catalog,
                    bucket_ids=_bucket_ids_from_planning_decision(batch_decision),
                )
                semantic_stats["semantic_upserted"] += int(semantic_upserted or 0)
                if semantic_upserted:
                    semantic_cache_available = True
                for item in batch_decision.get("assignments") or []:
                    intent_id = str(item.get("intent_id") or "")
                    if not intent_id:
                        continue
                    bucket_id = _safe_bucket_id(item.get("bucket_id"), item.get("bucket_title")) or "default"
                    bucket_entry = bucket_catalog.get(bucket_id, {})
                    assignments[intent_id] = {
                        "bucket_id": bucket_id,
                        "bucket_title": str(bucket_entry.get("bucket_title") or item.get("bucket_title") or bucket_id),
                        "canonical_theme": str(item.get("canonical_theme") or ""),
                        "from_cache": False,
                    }

        for ref in intent_refs:
            assignments.setdefault(
                ref["intent_id"],
                {
                    "bucket_id": "default",
                    "bucket_title": "默认执行桶",
                    "canonical_theme": str(ref["topic_intent"].get("title_candidate") or ""),
                    "from_cache": False,
                },
            )
        buckets: dict[str, list[dict[str, Any]]] = {}
        for ref in intent_refs:
            bucket = assignments[ref["intent_id"]]
            bucket_id = str(bucket.get("bucket_id") or "default")
            item = dict(ref)
            item["bucket"] = bucket
            buckets.setdefault(bucket_id, []).append(item)
        merge_result = await self._auto_merge_if_needed(adapter_name=adapter_name)
        return {
            "buckets": buckets,
            "assignments": assignments,
            "unknown_intents": len(unknown),
            "llm_assignments": len(llm_decision.get("assignments") or []),
            "new_buckets": len(llm_decision.get("new_buckets") or []),
            "merge_suggestions": 0,
            "planning_batches": (len(unknown) + self._planning_batch_size - 1) // self._planning_batch_size if unknown else 0,
            "planning_batch_size": self._planning_batch_size,
            "semantic_cache": semantic_stats,
            "merge_result": merge_result,
            "merge_results": [merge_result] if merge_result.get("triggered") else [],
        }

    async def _sync_semantic_bucket_cache(
        self,
        *,
        adapter_name: str,
        bucket_catalog: dict[str, dict[str, Any]],
        bucket_ids: list[str],
    ) -> int:
        if self._semantic_bucket_cache is None:
            return 0
        buckets = [
            bucket_catalog[bucket_id]
            for bucket_id in _ordered_unique([_safe_bucket_id(item) for item in bucket_ids if item])
            if bucket_id in bucket_catalog
        ]
        if not buckets:
            return 0
        return await self._semantic_bucket_cache.upsert_buckets(adapter_name=adapter_name, buckets=buckets)

    async def _auto_merge_if_needed(self, *, adapter_name: str) -> dict[str, Any]:
        snapshot = self._store.snapshot(adapter_name=adapter_name)
        catalog = _normal_bucket_catalog(snapshot.get("bucket_catalog"))
        bucket_count = len(catalog)
        if bucket_count <= self._auto_merge_threshold:
            return {
                "triggered": False,
                "reason": "under_threshold",
                "bucket_count": bucket_count,
                "threshold": self._auto_merge_threshold,
                "candidate_count": 0,
                "merged": 0,
            }
        merge_candidates = _bucket_auto_merge_candidates(
            catalog,
            max_candidates=self._auto_merge_candidate_limit,
        )
        if not merge_candidates:
            return {
                "triggered": False,
                "reason": "no_candidates",
                "bucket_count": bucket_count,
                "threshold": self._auto_merge_threshold,
                "candidate_count": 0,
                "merged": 0,
            }
        merged = 0
        final_bucket_count = bucket_count
        final_theme_map_count = len(_normal_theme_map(snapshot.get("theme_bucket_map")))
        batch_results: list[dict[str, Any]] = []
        for batch in _chunks(merge_candidates, ASSIGNMENT_BUCKET_AUTO_MERGE_BATCH_SIZE):
            result = await self.merge_buckets(
                adapter_name=adapter_name,
                merge_candidates=batch,
                communities=[],
            )
            merged += int(result.get("merged") or 0)
            final_bucket_count = int(result.get("bucket_count") or final_bucket_count)
            final_theme_map_count = int(result.get("theme_map_count") or final_theme_map_count)
            batch_results.append(
                {
                    "candidate_count": len(batch),
                    "merged": int(result.get("merged") or 0),
                    "bucket_count": final_bucket_count,
                }
            )
        return {
            "triggered": True,
            "reason": "over_threshold",
            "bucket_count_before": bucket_count,
            "threshold": self._auto_merge_threshold,
            "candidate_count": len(merge_candidates),
            "merged": merged,
            "bucket_count": final_bucket_count,
            "theme_map_count": final_theme_map_count,
            "batch_size": ASSIGNMENT_BUCKET_AUTO_MERGE_BATCH_SIZE,
            "batches": batch_results,
        }

    async def _plan_unknown_batch(
        self,
        *,
        adapter_name: str,
        unknown: list[dict[str, Any]],
        batch_index: int,
        batch_count: int,
    ) -> dict[str, Any]:
        snapshot = self._store.snapshot(adapter_name=adapter_name)
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=BUCKET_PLANNING_SYSTEM_PROMPT,
            prompt=json.dumps(
                    {
                        "existing_bucket_catalog": _bucket_prompt_catalog(
                            _normal_bucket_catalog(snapshot.get("bucket_catalog"))
                        ),
                        "topic_intent_signatures": _bucket_prompt_signatures(unknown),
                        "planning_context": {
                            "batch_index": batch_index,
                            "batch_count": batch_count,
                            "batch_intent_count": len(unknown),
                            "planning_goal": "中等粒度写冲突域；避免少数大桶吞并互不竞争的 intent。",
                        },
                        "target": "为每个 intent 选择 bucket，必要时创建新 bucket，并输出 canonical_theme。",
                    },
                ensure_ascii=False,
                indent=2,
            ),
            temperature=0,
            max_tokens=BUCKET_PLANNING_MAX_TOKENS,
            json_schema=BUCKET_PLANNING_SCHEMA,
            provider_options={"thinking_type": self._bucket_thinking},
            metadata={
                "task": "kg_assignment_bucket_planning",
                "unknown_intents": len(unknown),
                "batch_index": batch_index,
                "batch_count": batch_count,
                "batch_size": self._planning_batch_size,
                "llm_use_cache": self._use_cache,
                "thinking_type": self._bucket_thinking,
                "merge_decoupled": True,
            },
            use_cache=self._use_cache,
        )
        response = await self._llm.generate(request)
        return await self._validated_bucket_decision(
            request=request,
            response=response,
            validator=lambda data: _validate_bucket_planning_decision_for_batch(data, unknown),
            label="bucket_planning",
        )

    async def merge_buckets(
        self,
        *,
        adapter_name: str,
        merge_candidates: list[dict[str, Any]],
        communities: list[GraphIndexCommunity],
    ) -> dict[str, Any]:
        if not merge_candidates:
            return {"merged": 0, "bucket_count": len(_normal_bucket_catalog(self._store.snapshot(adapter_name=adapter_name).get("bucket_catalog")))}
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=BUCKET_MERGE_SYSTEM_PROMPT,
            prompt=json.dumps(
                {
                    "merge_candidates": merge_candidates,
                },
                ensure_ascii=False,
                indent=2,
            ),
            temperature=0,
            max_tokens=BUCKET_MERGE_MAX_TOKENS,
            json_schema=BUCKET_MERGE_SCHEMA,
            provider_options={"thinking_type": "disabled"},
            metadata={
                "task": "kg_assignment_bucket_merge",
                "merge_candidates": len(merge_candidates),
                "thinking_type": "disabled",
                "catalog_elided": True,
                "llm_use_cache": self._use_cache,
            },
            use_cache=self._use_cache,
        )
        response = await self._llm.generate(request)
        decision = await self._validated_bucket_decision(
            request=request,
            response=response,
            validator=lambda data: _validate_bucket_merge_decision_for_candidates(data, merge_candidates),
            label="bucket_merge",
        )
        result = self._store.apply_merge_decision(adapter_name=adapter_name, decision=decision)
        if self._semantic_bucket_cache is not None:
            source_ids = [
                _safe_bucket_id(item.get("source_bucket_id"))
                for item in decision.get("merge_actions") or []
                if isinstance(item, dict)
            ]
            target_ids = [
                _safe_bucket_id(item.get("target_bucket_id"))
                for item in decision.get("merge_actions") or []
                if isinstance(item, dict)
            ]
            self._semantic_bucket_cache.delete_buckets(adapter_name=adapter_name, bucket_ids=source_ids)
            bucket_catalog = _normal_bucket_catalog(self._store.snapshot(adapter_name=adapter_name).get("bucket_catalog"))
            await self._sync_semantic_bucket_cache(
                adapter_name=adapter_name,
                bucket_catalog=bucket_catalog,
                bucket_ids=target_ids,
            )
        return result

    async def replay_consistency(
        self,
        *,
        adapter_name: str,
        cards: list[CognitiveCard],
        serial_assignments: list[CommunityAssignment],
        communities: list[GraphIndexCommunity],
        bucket_assignments: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=BUCKET_REPLAY_SYSTEM_PROMPT,
            prompt=json.dumps(
                {
                    "topic_intents": _bucket_replay_intents(cards),
                    "serial_assignment_baseline": _bucket_replay_serial_assignments(serial_assignments),
                    "bucket_replay_result": _bucket_replay_assignments(bucket_assignments),
                    "community_summaries": [_community_summary_for_bucket_replay(item) for item in communities],
                },
                ensure_ascii=False,
                indent=2,
            ),
            temperature=0,
            max_tokens=BUCKET_REPLAY_MAX_TOKENS,
            json_schema=BUCKET_REPLAY_SCHEMA,
            metadata={
                "task": "kg_assignment_bucket_consistency_replay",
                "llm_use_cache": self._use_cache,
            },
            use_cache=self._use_cache,
        )
        response = await self._llm.generate(request)
        return await self._validated_bucket_decision(
            request=request,
            response=response,
            validator=_validate_bucket_replay_decision,
            label="bucket_replay",
        )

    def lock_assignment_update(self, *, adapter_name: str, bucket_id: str, community_ids: list[str]) -> Any:
        return self._store.lock_assignment_update(
            adapter_name=adapter_name,
            bucket_id=bucket_id,
            community_ids=community_ids,
        )

    async def _validated_bucket_decision(
        self,
        *,
        request: LLMProxyRequest,
        response: Any,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        label: str,
    ) -> dict[str, Any]:
        try:
            return _validate_bucket_response(response, validator=validator, label=label)
        except Exception as exc:
            issues = [str(exc)]
        repair = getattr(self._llm, "repair_with_feedback", None)
        if repair is None:
            raise RuntimeError(f"{label} output is invalid and llm repair is unavailable: {issues}") from None
        repaired = await repair(
            request,
            response,
            issues,
            instruction=(
                "请只修复上一轮输出，使其成为符合 JSON Schema 和业务校验要求的 JSON 对象。"
                "不要改写输入事实，不要输出 Markdown，不要解释。"
            ),
            retry_reason=f"{label}_validation_invalid",
        )
        try:
            return _validate_bucket_response(repaired, validator=validator, label=label)
        except Exception as repaired_exc:
            raise RuntimeError(
                f"{label} repair output is invalid: original_issues={issues}; "
                f"repair_issue={repaired_exc}"
            ) from repaired_exc


def _normal_bucket_catalog(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_id, raw_bucket in value.items():
        if not isinstance(raw_bucket, dict):
            continue
        bucket_id = _safe_bucket_id(raw_bucket.get("bucket_id") or raw_id, raw_bucket.get("bucket_title"))
        if not bucket_id:
            continue
        result[bucket_id] = _bucket_entry(
            bucket_id=bucket_id,
            bucket_title=str(raw_bucket.get("bucket_title") or raw_bucket.get("title") or bucket_id),
            scope=str(raw_bucket.get("scope") or ""),
            canonical_themes=_candidate_list(raw_bucket.get("canonical_themes")),
        )
    return result


def _bucket_prompt_catalog(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for bucket in sorted(catalog.values(), key=lambda item: str(item.get("bucket_id") or "")):
        entry = {
            "bucket_id": str(bucket.get("bucket_id") or ""),
            "bucket_title": str(bucket.get("bucket_title") or ""),
            "scope": str(bucket.get("scope") or ""),
        }
        result.append({key: value for key, value in entry.items() if value})
    return result


def _normal_theme_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for theme, bucket_id in value.items():
        theme_key = _theme_cache_key(theme)
        safe_bucket_id = _safe_bucket_id(bucket_id)
        if theme_key and safe_bucket_id:
            result[theme_key] = safe_bucket_id
    return result


def _bucket_entry(
    *,
    bucket_id: str,
    bucket_title: str,
    scope: str,
    canonical_themes: list[str],
) -> dict[str, Any]:
    return {
        "bucket_id": bucket_id,
        "bucket_title": bucket_title,
        "scope": scope,
        "canonical_themes": _ordered_unique(canonical_themes),
    }


def _bucket_auto_merge_candidates(
    catalog: dict[str, dict[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    buckets = sorted(catalog.values(), key=lambda item: str(item.get("bucket_id") or ""))
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for index, left in enumerate(buckets):
        for right in buckets[index + 1 :]:
            left_id = str(left.get("bucket_id") or "")
            right_id = str(right.get("bucket_id") or "")
            if not left_id or not right_id:
                continue
            score = _bucket_similarity_score(left, right)
            if score <= 0:
                continue
            shared_signals = _bucket_shared_signals(left, right)
            if not shared_signals:
                continue
            source, target = _bucket_merge_source_target(left, right)
            candidate = {
                "source_bucket_id": source["bucket_id"],
                "source_bucket_title": source["bucket_title"],
                "target_bucket_id": target["bucket_id"],
                "target_bucket_title": target["bucket_title"],
                "shared_signals": shared_signals,
                "source_scope": str(source.get("scope") or "")[:160],
                "target_scope": str(target.get("scope") or "")[:160],
            }
            scored.append((score, str(target["bucket_id"]), str(source["bucket_id"]), candidate))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in scored[:max_candidates]]


def _bucket_similarity_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_themes = {_theme_cache_key(item) for item in _candidate_list(left.get("canonical_themes")) if _has_cjk(str(item or "")) and _theme_cache_key(item)}
    right_themes = {_theme_cache_key(item) for item in _candidate_list(right.get("canonical_themes")) if _has_cjk(str(item or "")) and _theme_cache_key(item)}
    theme_overlap = len(left_themes & right_themes)
    left_title_terms = _bucket_semantic_terms([left.get("bucket_title")])
    right_title_terms = _bucket_semantic_terms([right.get("bucket_title")])
    left_scope_terms = _bucket_semantic_terms([left.get("scope")])
    right_scope_terms = _bucket_semantic_terms([right.get("scope")])
    title_overlap = len(left_title_terms & right_title_terms)
    scope_overlap = len(left_scope_terms & right_scope_terms)
    cross_overlap = len((left_title_terms | left_scope_terms | left_themes) & (right_title_terms | right_scope_terms | right_themes))
    return theme_overlap * 4.0 + title_overlap * 2.0 + scope_overlap * 1.0 + cross_overlap * 0.5


def _bucket_merge_source_target(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    left_themes = len(_candidate_list(left.get("canonical_themes")))
    right_themes = len(_candidate_list(right.get("canonical_themes")))
    if left_themes < right_themes:
        return left, right
    if right_themes < left_themes:
        return right, left
    if len(str(left.get("scope") or "")) < len(str(right.get("scope") or "")):
        return left, right
    if len(str(right.get("scope") or "")) < len(str(left.get("scope") or "")):
        return right, left
    return (right, left) if str(left.get("bucket_id") or "") < str(right.get("bucket_id") or "") else (left, right)


def _bucket_shared_signals(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    result: list[str] = []
    left_themes = {
        _theme_cache_key(item): item
        for item in _candidate_list(left.get("canonical_themes"))
        if _has_cjk(str(item or ""))
    }
    for item in _candidate_list(right.get("canonical_themes")):
        key = _theme_cache_key(item)
        if key and key in left_themes:
            result.append(str(left_themes[key]))
    if not result:
        left_terms = _bucket_semantic_terms(
            [
                left.get("bucket_title"),
                left.get("scope"),
                *_candidate_list(left.get("canonical_themes")),
            ]
        )
        right_terms = _bucket_semantic_terms(
            [
                right.get("bucket_title"),
                right.get("scope"),
                *_candidate_list(right.get("canonical_themes")),
            ]
        )
        result.extend(sorted(left_terms & right_terms)[:5])
    return _ordered_unique(result)[:8]


_BUCKET_MERGE_STOP_SIGNALS = {
    "承接",
    "可能",
    "竞争",
    "行业",
    "主题",
    "风险",
    "事件",
    "动态",
    "市场",
    "相关",
}


def _bucket_semantic_terms(values: list[Any]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not _has_cjk(text):
            continue
        normalized = _theme_cache_key(text)
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            if len(run) >= 2 and run not in _BUCKET_MERGE_STOP_SIGNALS:
                terms.add(run[:16])
            terms.update(
                item
                for item in _char_ngrams(run, n=2)
                if item not in _BUCKET_MERGE_STOP_SIGNALS
            )
            terms.update(
                item
                for item in _char_ngrams(run, n=3)
                if item not in _BUCKET_MERGE_STOP_SIGNALS
            )
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,8}", normalized):
            if token.lower() in {"ai", "ipo", "etf", "pmi", "gdp", "cpo", "dram", "nand"}:
                terms.add(token.lower())
    return terms


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _char_ngram_jaccard(left: str, right: str, *, n: int = 2) -> float:
    left_set = set(_char_ngrams(left, n=n))
    right_set = set(_char_ngrams(right, n=n))
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _common_char_ngrams(left: str, right: str, *, n: int = 2) -> list[str]:
    left_set = set(_char_ngrams(left, n=n))
    right_set = set(_char_ngrams(right, n=n))
    return sorted(left_set & right_set)


def _char_ngrams(value: str, *, n: int = 2) -> list[str]:
    text = _theme_cache_key(value)
    if len(text) <= n:
        return [text] if text else []
    return [text[index : index + n] for index in range(0, len(text) - n + 1)]


def _safe_bucket_id(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        safe = re.sub(r"[^0-9A-Za-z_\-:\u4e00-\u9fff]+", "_", text).strip("_")
        if safe:
            return safe[:96]
    return ""


def _theme_cache_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _bucket_theme_candidates(topic_intent: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("parent_themes", "broad_topics", "event_thread", "title_candidate", "raw_theme"):
        values.extend(_candidate_list(topic_intent.get(key)))
    return _ordered_unique(values)


def _bucket_semantic_query_text(topic_intent: dict[str, Any]) -> str:
    signature = _bucket_intent_signature({"intent_id": "-", "topic_intent": topic_intent})
    parts: list[str] = []
    for key in ("parent_themes", "title_candidate", "event_thread", "routing_signals", "context_hint"):
        value = signature.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
        elif str(value or "").strip():
            parts.append(str(value).strip())
    return "；".join(_ordered_unique(parts))


def _bucket_semantic_document_text(bucket: dict[str, Any]) -> str:
    parts = [
        str(bucket.get("bucket_title") or ""),
        str(bucket.get("scope") or ""),
        *_candidate_list(bucket.get("canonical_themes"))[:12],
    ]
    return "；".join(_ordered_unique([part for part in parts if str(part).strip()]))


def _bucket_semantic_target_id(*, adapter_name: str, bucket_id: str) -> str:
    return f"kg_bucket_cache:{adapter_name}:{_safe_bucket_id(bucket_id)}"


def _bucket_intent_signature(ref: dict[str, Any]) -> dict[str, Any]:
    topic_intent = ref["topic_intent"]
    routing_signals = _ordered_unique(
        [
            *_candidate_list(topic_intent.get("event_action")),
            *_candidate_list(topic_intent.get("driver")),
            *_candidate_list(topic_intent.get("risk_type")),
        ]
    )
    return {
        "intent_id": ref["intent_id"],
        "parent_themes": _candidate_list(topic_intent.get("parent_themes"))[:3],
        "title_candidate": str(topic_intent.get("title_candidate") or "")[:80],
        "event_thread": _candidate_list(topic_intent.get("event_thread"))[:2],
        "routing_signals": routing_signals[:4],
        "context_hint": _bucket_context_hint(topic_intent),
    }


def _bucket_context_hint(topic_intent: dict[str, Any]) -> str:
    for key in ("summary", "raw_theme", "title_candidate"):
        text = re.sub(r"\s+", " ", str(topic_intent.get(key) or "")).strip()
        if text:
            return text[:60]
    return ""


def _bucket_prompt_signatures(signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in signature.items()
            if key != "intent_id" and value not in ("", [], None)
        }
        for signature in signatures
    ]


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if not items:
        return []
    chunk_size = max(1, int(size or 1))
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _ref_by_intent_id(intent_refs: list[dict[str, Any]], intent_id: str) -> dict[str, Any] | None:
    for ref in intent_refs:
        if str(ref.get("intent_id") or "") == intent_id:
            return ref
    return None


def _merge_bucket_planning_decision(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("assignments", "new_buckets", "theme_bucket_updates"):
        target.setdefault(key, [])
        target[key].extend(item for item in source.get(key) or [] if isinstance(item, dict))


def _bucket_ids_from_planning_decision(decision: dict[str, Any]) -> list[str]:
    bucket_ids: list[str] = []
    for key in ("assignments", "new_buckets", "theme_bucket_updates"):
        for item in decision.get(key) or []:
            if isinstance(item, dict):
                bucket_ids.append(_safe_bucket_id(item.get("bucket_id"), item.get("bucket_title")))
    return _ordered_unique([item for item in bucket_ids if item])


def _validate_bucket_planning_decision(data: dict[str, Any]) -> dict[str, Any]:
    assignments = data.get("assignments")
    new_buckets = data.get("new_buckets")
    theme_updates = data.get("theme_bucket_updates")
    if not isinstance(assignments, list):
        raise ValueError("bucket planning assignments must be array")
    if not isinstance(new_buckets, list):
        raise ValueError("bucket planning new_buckets must be array")
    if not isinstance(theme_updates, list):
        raise ValueError("bucket planning theme_bucket_updates must be array")
    for item in assignments:
        if not isinstance(item, dict):
            raise ValueError("bucket planning assignment must be object")
        if not str(item.get("canonical_theme") or "").strip():
            raise ValueError("bucket planning assignment missing canonical_theme")
        if not _safe_bucket_id(item.get("bucket_id"), item.get("bucket_title")):
            raise ValueError("bucket planning assignment missing bucket_id")
    return {
        "assignments": assignments,
        "new_buckets": new_buckets,
        "theme_bucket_updates": theme_updates,
    }


def _validate_bucket_planning_decision_for_batch(
    data: dict[str, Any],
    unknown: list[dict[str, Any]],
) -> dict[str, Any]:
    decision = _validate_bucket_planning_decision(data)
    assignments = decision.get("assignments") or []
    if len(assignments) != len(unknown):
        raise ValueError(
            "bucket planning assignments must match topic_intent_signatures length "
            f"and order; expected={len(unknown)} actual={len(assignments)}"
        )
    bound_assignments: list[dict[str, Any]] = []
    for signature, assignment in zip(unknown, assignments, strict=True):
        bound = dict(assignment)
        bound["intent_id"] = str(signature.get("intent_id") or "")
        bound_assignments.append(bound)
    return {
        **decision,
        "assignments": bound_assignments,
    }


def _validate_bucket_merge_decision(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("merge_actions", "rejected_merge_candidates"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"bucket merge {key} must be array")
    return {
        "merge_actions": data["merge_actions"],
        "rejected_merge_candidates": data["rejected_merge_candidates"],
    }


def _validate_bucket_merge_decision_for_candidates(
    data: dict[str, Any],
    merge_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    decision = _validate_bucket_merge_decision(data)
    allowed_pairs = {
        (
            _safe_bucket_id(item.get("source_bucket_id")),
            _safe_bucket_id(item.get("target_bucket_id")),
        )
        for item in merge_candidates
        if isinstance(item, dict)
    }
    allowed_pairs.discard(("", ""))
    for action in decision["merge_actions"]:
        if not isinstance(action, dict):
            raise ValueError("bucket merge action must be object")
        pair = (
            _safe_bucket_id(action.get("source_bucket_id")),
            _safe_bucket_id(action.get("target_bucket_id")),
        )
        if pair not in allowed_pairs:
            raise ValueError(f"bucket merge action must use input candidate pair: {pair}")
    for item in decision["rejected_merge_candidates"]:
        if not isinstance(item, dict):
            raise ValueError("bucket merge rejected candidate must be object")
        pair = (
            _safe_bucket_id(item.get("source_bucket_id")),
            _safe_bucket_id(item.get("target_bucket_id")),
        )
        if pair not in allowed_pairs:
            raise ValueError(f"bucket merge rejected candidate must use input candidate pair: {pair}")
    return decision


def _validate_bucket_replay_decision(data: dict[str, Any]) -> dict[str, Any]:
    status = str(data.get("status") or "")
    if status not in {"pass", "fail", "needs_review"}:
        raise ValueError("bucket replay status must be pass/fail/needs_review")
    if not isinstance(data.get("conflicts"), list):
        raise ValueError("bucket replay conflicts must be array")
    return {
        "status": status,
        "conflicts": data["conflicts"],
        "summary": str(data.get("summary") or ""),
    }


def _validate_bucket_response(
    response: Any,
    *,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    data = getattr(response, "structured_output", None)
    if not isinstance(data, dict):
        text = _clip_local(str(getattr(response, "text", "") or ""), 240)
        raise ValueError(f"{label} output must be JSON object; actual={type(data).__name__}; text={text}")
    return validator(data)


def _clip_local(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _builder_intent_refs(cards: list[CognitiveCard]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for card in sorted(cards, key=lambda item: (item.source_id, item.chunk_index, item.cognitive_card_id)):
        for index, intent in enumerate(card.topic_intents, start=1):
            topic_intent = _assignment_topic_intent(card, intent)
            result.append(
                {
                    "intent_id": _intent_ref_id(card, index),
                    "card": card,
                    "intent_index": index,
                    "topic_intent": topic_intent,
                }
            )
    return result


def _assignment_existing_community_ids(decision: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for assignment in decision.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("action") == "create_parent_and_absorb_existing":
            result.extend(str(item).strip() for item in assignment.get("absorb_community_ids") or [] if str(item).strip())
            continue
        if assignment.get("action") != "attach_existing":
            continue
        community_id = str(assignment.get("community_id") or "").strip()
        if community_id:
            result.append(community_id)
    return _ordered_unique(result)


def _assignment_absorbed_community_ids(decision: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for assignment in decision.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("action") != "create_parent_and_absorb_existing":
            continue
        result.extend(str(item).strip() for item in assignment.get("absorb_community_ids") or [] if str(item).strip())
    return _ordered_unique(result)


def _rewrite_absorbed_community_assignments(
    assignments: list[CommunityAssignment],
    communities: dict[str, Any],
) -> list[CommunityAssignment]:
    absorbed_to_parent: dict[str, str] = {}
    for parent_id, community in communities.items():
        for absorbed_id in getattr(community, "absorbed_community_ids", []) or []:
            absorbed_text = str(absorbed_id or "").strip()
            if absorbed_text and absorbed_text != str(parent_id):
                absorbed_to_parent[absorbed_text] = str(parent_id)
    if not absorbed_to_parent:
        return assignments
    rewritten: list[CommunityAssignment] = []
    for assignment in assignments:
        parent_id = absorbed_to_parent.get(assignment.community_id)
        if not parent_id:
            rewritten.append(assignment)
            continue
        assignment_id = "kg_community_assignment:" + hashlib.sha256(
            "|".join(
                [
                    assignment.cognitive_card_id,
                    str(assignment.intent_index),
                    parent_id,
                    assignment.action,
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
        rewritten.append(
            replace(
                assignment,
                assignment_id=assignment_id,
                community_id=parent_id,
                matched_reason=f"{assignment.matched_reason}；absorbed_from={assignment.community_id}",
                reason=f"{assignment.reason}；absorbed_from={assignment.community_id}",
            )
        )
    return rewritten


def _bucket_plan_diagnostics(bucket_plan: dict[str, Any] | None) -> dict[str, Any]:
    if not bucket_plan:
        return {
            "enabled": False,
            "bucket_count": 1,
            "unknown_intents": 0,
            "new_buckets": 0,
            "merge_suggestions": 0,
        }
    buckets = bucket_plan.get("buckets") or {}
    return {
        "enabled": True,
        "bucket_count": len(buckets),
        "bucket_sizes": {str(key): len(value or []) for key, value in buckets.items()},
        "unknown_intents": int(bucket_plan.get("unknown_intents") or 0),
        "llm_assignments": int(bucket_plan.get("llm_assignments") or 0),
        "new_buckets": int(bucket_plan.get("new_buckets") or 0),
        "merge_suggestions": int(bucket_plan.get("merge_suggestions") or 0),
        "semantic_cache": bucket_plan.get("semantic_cache"),
        "merge_result": bucket_plan.get("merge_result"),
    }


def _record_semantic_bucket_stats(stats: dict[str, int], semantic_result: Any) -> None:
    if not isinstance(semantic_result, dict):
        return
    stats["semantic_requests"] = int(stats.get("semantic_requests") or 0) + 1
    stats["semantic_raw_hits"] = int(stats.get("semantic_raw_hits") or 0) + int(semantic_result.get("raw_hits") or 0)
    stats["semantic_candidate_hits"] = int(stats.get("semantic_candidate_hits") or 0) + len(semantic_result.get("candidates") or [])


def _semantic_direct_bucket_decision(
    *,
    canonical_theme: str,
    direct: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    bucket_id = _safe_bucket_id(direct.get("bucket_id"))
    bucket_title = str(direct.get("bucket_title") or bucket_id)
    scope = str(direct.get("scope") or "")
    canonical_themes = _ordered_unique([canonical_theme, *_candidate_list(direct.get("canonical_themes"))])
    new_buckets = []
    if bucket_id and bucket_id not in catalog:
        new_buckets.append(
            {
                "bucket_id": bucket_id,
                "bucket_title": bucket_title,
                "scope": scope,
                "canonical_themes": canonical_themes,
            }
        )
    return {
        "assignments": [
            {
                "canonical_theme": canonical_theme,
                "bucket_id": bucket_id,
                "bucket_title": bucket_title,
            }
        ],
        "new_buckets": new_buckets,
        "theme_bucket_updates": [
            {"canonical_theme": theme, "bucket_id": bucket_id}
            for theme in canonical_themes
            if theme and bucket_id
        ],
    }


def _bucket_replay_intents(cards: list[CognitiveCard]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for card in cards:
        for index, intent in enumerate(card.topic_intents, start=1):
            topic_intent = _assignment_topic_intent(card, intent)
            result.append(
                {
                    "intent_id": _intent_ref_id(card, index),
                    "source_id": card.source_id,
                    "cognitive_card_id": card.cognitive_card_id,
                    "signature": _bucket_intent_signature({"intent_id": _intent_ref_id(card, index), "topic_intent": topic_intent}),
                }
            )
    return result


def _bucket_replay_serial_assignments(assignments: list[CommunityAssignment]) -> list[dict[str, Any]]:
    return [
        {
            "intent_id": item.intent_id,
            "community_id": item.community_id,
            "weight": item.weight,
            "fit_type": (item.decision.get("assignments") or [{}])[0].get("fit_type") if isinstance(item.decision, dict) else "",
        }
        for item in assignments
    ]


def _bucket_replay_assignments(bucket_assignments: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "intent_id": intent_id,
            "bucket_id": str(item.get("bucket_id") or ""),
            "bucket_title": str(item.get("bucket_title") or ""),
            "canonical_theme": str(item.get("canonical_theme") or ""),
        }
        for intent_id, item in sorted(bucket_assignments.items())
    ]


def _community_summary_for_bucket_replay(community: GraphIndexCommunity) -> dict[str, Any]:
    metrics = community.metrics or {}
    return {
        "community_id": community.community_id,
        "title": community.title,
        "summary": community.summary[:300],
        "scope": str(metrics.get("scope") or "")[:240],
        "absorbed_themes": _ordered_unique([
            *_candidate_list(metrics.get("canonical_labels")),
            *_candidate_list(metrics.get("future_coverage")),
        ])[:16],
    }


def _intent_ref_id(card: CognitiveCard, intent_index: int) -> str:
    return f"{card.cognitive_card_id}:{intent_index}"


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


def _candidate_append_log_redirect_count(append_log: list[dict[str, Any]]) -> int:
    return sum(1 for item in append_log if item.get("entry_type") == "candidate_redirect")


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


def _candidate_append_log_redirect_keys(append_log: list[dict[str, Any]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for item in append_log:
        if item.get("entry_type") != "candidate_redirect":
            continue
        from_id = str(item.get("from_community_id") or "").strip()
        to_id = str(item.get("to_community_id") or "").strip()
        if from_id and to_id:
            result.add((from_id, to_id))
    return result


def _compact_ledger_append_log(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry_type = item.get("entry_type")
        if entry_type == "candidate_base":
            compacted = _compact_append_log_entry(_legacy_candidate_base_entry(item))
        elif entry_type == "candidate_redirect":
            compacted = _compact_append_log_entry(
                {
                    "entry_type": "candidate_redirect",
                    "from_community_id": str(item.get("from_community_id") or ""),
                    "to_community_id": str(item.get("to_community_id") or ""),
                    "to_title": str(item.get("to_title") or ""),
                    "reason": str(item.get("reason") or "merged_into_parent"),
                }
            )
        else:
            continue
        if compacted.get("entry_type") == "candidate_base" and compacted.get("community_id"):
            result.append(compacted)
        elif (
            compacted.get("entry_type") == "candidate_redirect"
            and compacted.get("from_community_id")
            and compacted.get("to_community_id")
        ):
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
        on_communities_updated: Callable[[list[GraphIndexCommunity], list[str]], Awaitable[None]] | None = None,
        candidate_order_store: AssignmentCandidateOrderStore | None = None,
        bucket_planner: CommunityBucketPlanner | None = None,
        bucket_concurrency: int = ASSIGNMENT_BUCKET_CONCURRENCY,
        reranker_concurrency: int = settings.KG_ASSIGNMENT_RERANKER_CONCURRENCY,
        community_id_factory: Callable[[str, int, str], str] | None = None,
    ):
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_community_assignment")
        self._candidate_provider = candidate_provider
        self._reranker_client = reranker_client
        self._target = target
        self._on_communities_updated = on_communities_updated
        self._candidate_order_store = candidate_order_store
        self._bucket_planner = bucket_planner
        self._bucket_concurrency = max(1, int(bucket_concurrency or 1))
        self._reranker_concurrency = max(1, int(reranker_concurrency or 1))
        self._reranker_semaphore = asyncio.Semaphore(self._reranker_concurrency)
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
        bucket_plan: dict[str, Any] | None = None
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
            intent_refs = _builder_intent_refs(cards)
            intent_count = len(intent_refs)
            use_bucket_planner = self._bucket_planner is not None and intent_count > 1
            if not use_bucket_planner:
                for ref in intent_refs:
                    try:
                        assignments.extend(
                            await self._process_assignment_intent(
                                adapter_name=adapter_name,
                                ref=ref,
                                bucket_id="serial",
                                communities=communities,
                            )
                        )
                    except Exception:
                        validation_errors += 1
                        raise
            else:
                with langfuse_observation(
                    name="kg.community_assignment.bucket_planning",
                    as_type="span",
                    input={"intents": len(intent_refs)},
                ):
                    bucket_plan = await self._bucket_planner.plan(
                        adapter_name=adapter_name,
                        intent_refs=intent_refs,
                    )
                    langfuse_update_span(
                        output={
                            "bucket_count": len(bucket_plan.get("buckets") or {}),
                            "unknown_intents": bucket_plan.get("unknown_intents"),
                            "new_buckets": bucket_plan.get("new_buckets"),
                            "merge_suggestions": bucket_plan.get("merge_suggestions"),
                            "semantic_cache": bucket_plan.get("semantic_cache"),
                            "merge_result": bucket_plan.get("merge_result"),
                        },
                        status_message="completed",
                    )
                sem = asyncio.Semaphore(self._bucket_concurrency)
                bucket_results: list[list[CommunityAssignment]] = []
                bucket_errors = 0

                async def run_bucket(bucket_id: str, refs: list[dict[str, Any]]) -> list[CommunityAssignment]:
                    async with sem:
                        bucket_assignments: list[CommunityAssignment] = []
                        ordered_refs = sorted(
                            refs,
                            key=lambda item: (
                                item["card"].source_id,
                                item["card"].chunk_index,
                                item["card"].cognitive_card_id,
                                item["intent_index"],
                            ),
                        )
                        with langfuse_observation(
                            name="kg.community_assignment.bucket",
                            as_type="span",
                            input={"bucket_id": bucket_id, "intents": len(ordered_refs)},
                        ):
                            for ref in ordered_refs:
                                bucket_assignments.extend(
                                    await self._process_assignment_intent(
                                        adapter_name=adapter_name,
                                        ref=ref,
                                        bucket_id=bucket_id,
                                        communities=communities,
                                    )
                                )
                            langfuse_update_span(
                                output={"assignments": len(bucket_assignments)},
                                status_message="completed",
                            )
                        return bucket_assignments

                tasks = [
                    asyncio.create_task(run_bucket(bucket_id, refs))
                    for bucket_id, refs in sorted((bucket_plan.get("buckets") or {}).items())
                ]
                try:
                    bucket_results = await asyncio.gather(*tasks)
                except Exception:
                    bucket_errors += 1
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    validation_errors += bucket_errors
                    raise
                for item in bucket_results:
                    assignments.extend(item)
            if self._candidate_order_store is not None:
                candidate_ledger_diagnostics.append(
                    self._candidate_order_store.checkpoint_if_needed(adapter_name=adapter_name)
                )
            graph_communities = [
                _graph_community_from_draft(adapter_name, community)
                for community in communities.values()
                if community.assigned_intents or getattr(community, "origin", "") == "seed"
            ]
            assignments = _rewrite_absorbed_community_assignments(assignments, communities)
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
                "community_builder": "cognitive_card_assignment_bucket_v1" if use_bucket_planner else "cognitive_card_assignment_v1",
                "bucket_planning": _bucket_plan_diagnostics(bucket_plan),
                "bucket_concurrency": self._bucket_concurrency if use_bucket_planner else 1,
                "reranker_concurrency": self._reranker_concurrency,
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

    async def _process_assignment_intent(
        self,
        *,
        adapter_name: str,
        ref: dict[str, Any],
        bucket_id: str,
        communities: dict[str, Any],
    ) -> list[CommunityAssignment]:
        card = ref["card"]
        intent_index = int(ref["intent_index"])
        topic_intent = ref["topic_intent"]
        candidates = await self._recall_candidates(
            adapter_name=adapter_name,
            topic_intent=topic_intent,
            communities=communities,
        )
        decision = await self._decide_assignment(card, topic_intent, candidates, communities)
        lock_ids = _assignment_existing_community_ids(decision)
        remove_community_ids = _assignment_absorbed_community_ids(decision)
        lock_context = (
            self._bucket_planner.lock_assignment_update(
                adapter_name=adapter_name,
                bucket_id=bucket_id,
                community_ids=lock_ids,
            )
            if self._bucket_planner is not None
            else _AsyncNoopLock()
        )
        async with lock_context:
            applied = _apply_assignment(
                adapter_name=adapter_name,
                card=card,
                intent_index=intent_index,
                topic_intent=topic_intent,
                decision=decision,
                communities=communities,
                community_id_factory=self._community_id_factory,
            )
            if applied and self._on_communities_updated is not None:
                updated_ids = sorted({assignment.community_id for assignment in applied})
                updated = [
                    _graph_community_from_draft(adapter_name, communities[community_id])
                    for community_id in updated_ids
                    if community_id in communities and communities[community_id].assigned_intents
                ]
                if updated:
                    await _call_communities_updated(
                        self._on_communities_updated,
                        updated,
                        remove_community_ids,
                    )
                    if self._candidate_order_store is not None and remove_community_ids:
                        redirect_diagnostics = self._candidate_order_store.record_community_redirects(
                            adapter_name=adapter_name,
                            redirects=_community_redirects_from_updates(updated, remove_community_ids),
                            target_candidates=[
                                _assignment_candidate_from_graph_community(community)
                                for community in updated
                            ],
                        )
                        ledger_sink = getattr(self, "_candidate_ledger_diagnostics", None)
                        if isinstance(ledger_sink, list):
                            ledger_sink.append(redirect_diagnostics)
        return applied

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
        candidates, retrieval_filter = _filter_assignment_retrieval_candidates(candidates)
        return await self._rerank_candidates(topic_intent, candidates, retrieval_filter=retrieval_filter)

    async def _rerank_candidates(
        self,
        topic_intent: dict[str, Any],
        candidates: list[dict[str, Any]],
        retrieval_filter: dict[str, Any] | None = None,
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
                "reranker_concurrency": self._reranker_concurrency,
                "retrieval_filter": retrieval_filter,
            },
        ):
            async with self._reranker_semaphore:
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
            selected = _filter_assignment_rerank_candidates(ranked)
            langfuse_update_span(
                output={
                    "raw_candidates": len(candidates),
                    "reranked_candidates": len(ranked),
                    "selected_candidates": len(selected),
                    "dropped_candidates": max(0, len(candidates) - len(selected)),
                    "rerank_filter": {
                        "score_floor": ASSIGNMENT_RERANK_SCORE_FLOOR,
                        "top_delta": ASSIGNMENT_RERANK_TOP_DELTA,
                        "min_keep": ASSIGNMENT_RERANK_MIN_KEEP,
                        "max_keep": MAX_ASSIGNMENT_CANDIDATES,
                        "top_score": ranked[0].get("rerank_score") if ranked else None,
                        "lowest_selected_score": selected[-1].get("rerank_score") if selected else None,
                    },
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
            if _candidate_append_log_entry_is_active(item, active_candidate_ids)
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
                    "candidate_redirect.from_community_id 已失效，不能作为 community_id 输出；"
                    "action=create_new 时 community_id 必须引用 new_communities 中的 client_id。"
                    "action=create_parent_and_absorb_existing 时 community_id 必须引用 new_communities 中的 client_id，"
                    "并且 absorb_community_ids 必须列出要吸收的 candidate_base community_id。"
                    "每条 assignment 必须包含 fit_type；attach_existing 不能使用 new_parent_topic，"
                    "create_new 和 create_parent_and_absorb_existing 必须使用 new_parent_topic。"
                    "如果新建 L0 community，标题必须具备清晰的对象和机制边界；"
                    "不能只是描述泛市场状态或一次性行情表现。"
                    "当 intent 主要描述成交、资金、板块轮动或风险偏好时，"
                    "必须判断其背后的产业、政策、风险、宏观变量、资本市场制度或工具边界，"
                    "并据此 attach_existing 或创建可长期复用的驱动型父主题。"
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


async def _call_communities_updated(
    callback: Callable[..., Awaitable[None]],
    communities: list[GraphIndexCommunity],
    remove_community_ids: list[str],
) -> None:
    parameters = list(inspect.signature(callback).parameters.values())
    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        await callback(communities, remove_community_ids)
        return
    positional_parameters = [
        parameter
        for parameter in parameters
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if len(positional_parameters) <= 1:
        await callback(communities)
    else:
        await callback(communities, remove_community_ids)


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
        result.append(
            {
                "community_id": community_id,
                "title": str(item.get("title") or ""),
                "scope": str(item.get("scope") or ""),
                "include_rules": _candidate_list(item.get("include_rules"))[:8],
                "exclude_rules": _candidate_list(item.get("exclude_rules"))[:8],
                "canonical_labels": _candidate_list(item.get("canonical_labels"))[:16],
                "granularity_note": str(item.get("granularity_note") or ""),
                "absorbed_subtopics": _candidate_list(item.get("absorbed_subtopics"))[:16],
                "maturity": str(item.get("maturity") or ""),
            }
        )
    return result


def _candidate_append_log_entry_is_active(item: dict[str, Any], active_candidate_ids: set[str]) -> bool:
    entry_type = item.get("entry_type")
    if entry_type == "candidate_base":
        return str(item.get("community_id") or "") in active_candidate_ids
    if entry_type == "candidate_redirect":
        return str(item.get("to_community_id") or "") in active_candidate_ids
    return False


def _community_redirects_from_updates(
    communities: list[GraphIndexCommunity],
    remove_community_ids: list[str],
) -> list[dict[str, str]]:
    remove_ids = {str(item) for item in remove_community_ids if str(item)}
    redirects: list[dict[str, str]] = []
    for community in communities:
        for previous_id in community.previous_community_ids or []:
            old_id = str(previous_id or "").strip()
            if old_id and old_id in remove_ids and old_id != community.community_id:
                redirects.append(
                    {
                        "from_community_id": old_id,
                        "to_community_id": community.community_id,
                    }
                )
    return redirects


def _assignment_candidate_from_graph_community(community: GraphIndexCommunity) -> dict[str, Any]:
    metrics = dict(community.metrics or {})
    return {
        "community_id": community.community_id,
        "title": community.title,
        "scope": str(metrics.get("scope") or community.summary or ""),
        "summary": community.summary,
        "canonical_labels": _candidate_list(metrics.get("canonical_labels"))[:16],
        "future_coverage": _candidate_list(metrics.get("future_coverage"))[:12],
        "maturity": str(metrics.get("maturity_level") or ""),
    }


def _aggregate_candidate_ledger_diagnostics(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "calls": 0,
            "redis_available_count": 0,
            "redis_unavailable_count": 0,
            "appended_base_total": 0,
            "appended_update_total": 0,
            "appended_redirect_total": 0,
            "checkpointed_count": 0,
            "checkpoint_skipped_by_overlap_count": 0,
            "max_append_log_entries": 0,
            "max_base_count": 0,
            "max_update_count": 0,
            "max_redirect_count": 0,
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
        "appended_redirect_total": sum(int(item.get("appended_redirect") or 0) for item in items),
        "checkpointed_count": sum(1 for item in items if item.get("checkpointed") is True),
        "checkpoint_skipped_by_overlap_count": sum(1 for item in items if item.get("checkpoint_skipped_by_overlap") is True),
        "max_append_log_entries": max(int(item.get("candidate_append_log_entries") or 0) for item in items),
        "max_base_count": max(int(item.get("candidate_append_log_base_count") or 0) for item in items),
        "max_update_count": max(int(item.get("candidate_append_log_update_count") or 0) for item in items),
        "max_redirect_count": max(int(item.get("candidate_append_log_redirect_count") or 0) for item in items),
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


def _filter_assignment_rerank_candidates(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ranked:
        return []
    top_score = _optional_float(ranked[0].get("rerank_score"))
    threshold = None if top_score is None else top_score - ASSIGNMENT_RERANK_TOP_DELTA
    selected: list[dict[str, Any]] = []
    for candidate in ranked[:MAX_ASSIGNMENT_CANDIDATES]:
        score = _optional_float(candidate.get("rerank_score"))
        if score is None:
            continue
        if score < ASSIGNMENT_RERANK_SCORE_FLOOR:
            continue
        if threshold is not None and score < threshold:
            continue
        selected.append(candidate)
    if len(selected) < ASSIGNMENT_RERANK_MIN_KEEP:
        selected_ids = {str(item.get("community_id") or "") for item in selected}
        for candidate in ranked[:MAX_ASSIGNMENT_CANDIDATES]:
            community_id = str(candidate.get("community_id") or "")
            if community_id in selected_ids:
                continue
            selected.append(candidate)
            selected_ids.add(community_id)
            if len(selected) >= min(ASSIGNMENT_RERANK_MIN_KEEP, MAX_ASSIGNMENT_CANDIDATES, len(ranked)):
                break
    return selected[:MAX_ASSIGNMENT_CANDIDATES]


def _filter_assignment_retrieval_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics = {
        "score_floor": ASSIGNMENT_RETRIEVAL_SCORE_FLOOR,
        "raw_candidates": len(candidates),
        "kept_candidates": len(candidates),
        "dropped_candidates": 0,
        "fallback_to_raw": False,
        "lowest_raw_score": None,
    }
    if not candidates:
        return candidates, diagnostics
    kept: list[dict[str, Any]] = []
    scores: list[float] = []
    for candidate in candidates:
        score = _optional_float(candidate.get("retrieval_score"))
        if score is not None:
            scores.append(score)
        if score is None or score >= ASSIGNMENT_RETRIEVAL_SCORE_FLOOR:
            kept.append(candidate)
    if not kept:
        kept = candidates
        diagnostics["fallback_to_raw"] = True
    diagnostics["kept_candidates"] = len(kept)
    diagnostics["dropped_candidates"] = max(0, len(candidates) - len(kept))
    diagnostics["lowest_raw_score"] = min(scores) if scores else None
    return kept, diagnostics


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
