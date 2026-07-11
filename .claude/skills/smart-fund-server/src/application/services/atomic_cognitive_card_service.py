"""原子 Cognitive Card 的提取、校验和发布服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.atomic_cognitive_card import (
    ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
    ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
    RELATION_PROBE_ROLES,
    AtomicCardExtractionResult,
    AtomicCognitiveCard,
    StableSpanSegmenter,
    atomic_card_document,
    atomic_card_from_llm_item,
)
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.config import settings
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever


logger = logging.getLogger(__name__)

ATOMIC_CARD_MAX_TOKENS = 5000
ATOMIC_CARD_PREFIX_WARM_MARK_KEY = (
    f"{JETTASK_PREFIX}:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmed"
)
ATOMIC_CARD_PREFIX_WARM_LOCK_KEY = (
    f"{JETTASK_PREFIX}:lock:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmup"
)
ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS = 0.05


ATOMIC_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                    "focus_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "factual_anchors": {
                        "type": "object",
                        "properties": {
                            "actors": {
                                "type": "array",
                                "maxItems": 8,
                                "items": {"type": "string", "maxLength": 80},
                            },
                            "action": {"type": "string", "maxLength": 32},
                            "objects": {
                                "type": "array",
                                "maxItems": 8,
                                "items": {"type": "string", "maxLength": 100},
                            },
                            "event_time": {"type": "string", "maxLength": 80},
                            "explicit_causes": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string", "maxLength": 160},
                            },
                            "explicit_effects": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string", "maxLength": 160},
                            },
                        },
                        "required": [
                            "actors",
                            "action",
                            "objects",
                            "event_time",
                            "explicit_causes",
                            "explicit_effects",
                        ],
                        "additionalProperties": False,
                    },
                    "relation_probes": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": sorted(RELATION_PROBE_ROLES)},
                                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                            },
                            "required": ["role", "query"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "summary",
                    "focus_evidence_refs",
                    "factual_anchors",
                    "relation_probes",
                ],
                "additionalProperties": False,
            },
        },
        "skip_reason": {"type": "string", "maxLength": 240},
    },
    "required": ["cards", "skip_reason"],
    "additionalProperties": False,
}


ATOMIC_CARD_SYSTEM_PROMPT = """你是知识图谱的原子 Cognitive Card 抽取器。

你的输入包含程序按原文顺序切分并编号的 Span；全部 Span 共同构成当前 chunk 的完整正文，是唯一的正文来源。你必须在一次输出中完成事件边界判断、原子 Card 拆分、Summary、焦点证据、事实锚点和 Relation Probe。

原子 Card 规则：
- 一个 Card 只能表达一个可以独立理解的事件或事实主张。
- 不要按句子数、公司数、数字数机械拆分；同一事件的背景、动作和直接结果可以属于同一 Card。
- 不同主体的独立动作、不同事件阶段或互不依赖的事实必须拆开。
- 当前 chunk 没有可独立验证的事件或事实时允许 cards=[]，并说明 skip_reason；不得强行制造 Card。
- Summary 必须忠实概括当前 Card，不写主题标签，不添加原文没有的主体、数字、时间、原因、结果或预测。

证据和事实锚点规则：
- focus_evidence_refs 只能引用输入中存在的 Span Ref，至少一个；应选择能够独立验证当前 Card 的最小证据闭合集合。证据闭合优先于引用数量少。
- Summary 以及 factual_anchors 中的主体、动作、对象、时间、原因和结果，都必须能从 focus_evidence_refs 指向的 Span 中直接得到；缺少支撑时，必须补充相应 Span Ref 或删除未被支撑的表述。
- source_published_at 只用于解释相对时间，不是事实证据；不得从 Chunk 标识或其他元数据补充正文 Span 没有的事实。
- factual_anchors 必须能直接回到焦点 Span；可以删除虚词形成紧凑短语，但不能改变事实含义、拼接无关位置或补充常识，数字必须保留原文。
- factual_anchors 是供后续关系发现使用的最小事实锚点，不是对 Summary 的结构化复述；完整事实细节保留在 Summary 和焦点证据中。
- actors 是事件主体；action 只保留最短的核心动作或状态谓词；objects 只保留该动作直接作用的对象。
- 不要把主体、对象、时间、金额、比例、变化幅度或完整事件描述塞入 action。
- 不要把金额、数值、比例、时间、变化幅度、排名、趋势、原因或结果塞入 objects。产品、政策、指标或资产只有在它本身是动作直接对象时才进入 objects。
- 原文明示的原因进入 explicit_causes，原文明示的结果进入 explicit_effects；同一信息不要在 objects、causes 和 effects 之间重复。
- event_time 只填写原文明示且能够确定的时间原文；source_published_at 仅用于解释原文中的相对时间，无法确定时保留原文表达，不补造时间。
- explicit_causes 和 explicit_effects 只填写原文明示的原因和结果原文；没有则为空数组。
- 多个 Card 可以引用同一个 Span，但仅限该 Span 对每个 Card 都是不可缺少的直接证据；不要为了制造互斥边界而强行拆开证据，也不要仅因共享背景或主题而重复引用。
- 生成每个 Card 时，先确定 Summary 和事实锚点，再分别定位支撑其中每一项信息的 Span Ref，最后将这些 Ref 的并集作为 focus_evidence_refs。
- 同一事实由连续上下文共同表达时，不要跳过承载主体、动作对象、指标名称或指代衔接的中间 Span；“最小”不等于只选择包含数字或关键词的 Span。
- 输出前仅阅读每个 Card 的 focus_evidence_refs，逐项核对 Summary 和全部事实锚点；如果不能独立确认，先补充引用或删除相应内容，再输出结果。不要输出核对过程。

Relation Probe 规则：
- Probe 是寻找历史候选 Card 的搜索假设，不代表关系已经成立。role 只能是 same_event、upstream、downstream、confirmation、contradiction。
- 一条 Probe 只描述一个候选事件。query 使用完整、通顺、可独立检索的事件描述，保留当前事实的必要身份约束；禁止关键词堆叠、空泛“相关”表达，以及用“或”罗列多个候选结果。
- Probe 用于当前处理时搜索已经存在的历史 Card，不是未来订阅条件。候选事件应当在当前事实之前或同时已经发生；不要搜索尚未发生的最终值、后续结果或未来反应。未来事件入库时，由未来 Card 反向发现当前 Card。
- 按以下顺序生成互不重复的 Probe：
  1. same_event：保持主体、核心对象和目标时间范围不变，分别寻找有价值的前序披露、修订、执行状态或最终结果；发布时间可以不同，目标期间不能替换为相邻时期。
  2. upstream：对 factual_anchors 中每个可独立检索的原因，分别描述该原因作用于当前对象的机制；不要把多个原因合成一条。
  3. confirmation：为可由外部材料验证的事实，寻找独立观察、指标、经营数据或其他判断提供的一致证据；同一消息的转载、复述或仅确认原报告存在不属于有效 confirmation。
  4. contradiction：为可以被推翻的事实，寻找否定、反向变化、下调、未兑现或使其失效的材料；不同反证状态分别描述。
  5. downstream：仅在当前事件本身可能引发后续结果时生成，并且每条只包含一个受影响对象和一种可观察变化。
- 先判断 Card 描述的是现实事件还是信息事件。预测、报告、观点和数据发布是信息事件，不会造成其所描述的订单、经营或行业状态变化；这些现实数据只能用于 confirmation 或 contradiction。信息事件的 downstream 只包括由信息发布本身触发的反应。
- 角色没有有意义的候选事件时可以省略，不要机械填满五类；同一角色可有多条。不要把尚未验证的关系写进 Summary 或 factual_anchors，也不要输出生成过程。

输出前最终核对：
- Card：action 是最短谓词，objects 只有动作直接对象，不含数值、幅度或结果。
- 证据：每项 Summary 和事实锚点都能由 focus_evidence_refs 独立验证。
- Probe：每条 query 是单一候选事件的陈述，不是提问或关键词列表；信息事件没有被误写成现实经营变化的原因。

不要输出主题目录、Community、Assignment、Edge、未来预测、风险标签或其他旧 Card 字段。只输出符合 JSON Schema 的 JSON，不要输出 Markdown、解释文字或自检过程。"""


@dataclass(frozen=True)
class AtomicCardStageResult:
    status: str
    cards: list[AtomicCognitiveCard]
    diagnostics: dict[str, Any]


class AtomicCognitiveCardExtractor:
    """一次 LLM 调用完成单个 Chunk 的全部原子 Card 提取工作。"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        concurrency: int = 4,
        segmenter: StableSpanSegmenter | None = None,
    ) -> None:
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_cognitive_card")
        self._concurrency = max(1, concurrency)
        self._segmenter = segmenter or StableSpanSegmenter()
        self._redis: Any | None = None

    async def extract(self, chunks: list[EvidenceChunk]) -> list[AtomicCognitiveCard]:
        results = await self.extract_with_diagnostics(chunks)
        return [card for result in results for card in result.cards]

    async def extract_with_diagnostics(
        self,
        chunks: list[EvidenceChunk],
    ) -> list[AtomicCardExtractionResult]:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run(chunk: EvidenceChunk) -> AtomicCardExtractionResult:
            async with semaphore:
                return await self._extract_one(chunk)

        tasks = [asyncio.create_task(run(chunk)) for chunk in chunks]
        try:
            return await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _extract_one(self, chunk: EvidenceChunk) -> AtomicCardExtractionResult:
        with langfuse_observation(
            name="kg.atomic_card.segment_chunk",
            as_type="span",
            input={"chunk_id": chunk.chunk_id, "text_chars": len(chunk.content)},
        ):
            spans = self._segmenter.segment(chunk.content)
            langfuse_update_span(
                output={"chunk_id": chunk.chunk_id, "span_count": len(spans)},
                status_message="completed",
            )
        if not spans:
            return AtomicCardExtractionResult(chunk_id=chunk.chunk_id, spans=[], cards=[])

        payload = dict(chunk.payload or {})
        prompt_payload = {
            "source_published_at": payload.get("published_at") or "",
            "chunk_id": chunk.chunk_id,
            "spans": [span.llm_payload() for span in spans],
        }
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=ATOMIC_CARD_SYSTEM_PROMPT,
            prompt=json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_CARD_SCHEMA,
            provider_options={"reasoning_effort": "high"},
            metadata={
                "task": "kg_cognitive_card",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "source_type": payload.get("source_type") or "",
                "source_id": payload.get("source_id") or "",
                "chunk_id": chunk.chunk_id,
                "_cache_key_metadata": {
                    "task": "kg_cognitive_card",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                },
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.atomic_card.extract",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "text_chars": len(chunk.content),
                "span_count": len(spans),
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            warmup_lock = await self._claim_prefix_warmup_lock()
            try:
                response = await self._llm.generate(request)
                await self._mark_prefix_warmed(settle=warmup_lock is not None)
                cards, repaired, skip_reason = await self._cards_from_response(
                    chunk=chunk,
                    spans=spans,
                    request=request,
                    response=response,
                )
                langfuse_update_span(
                    output={
                        "chunk_id": chunk.chunk_id,
                        "card_count": len(cards),
                        "card_ids": [card.cognitive_card_id for card in cards],
                        "summary_chars": [len(card.summary) for card in cards],
                        "focus_ref_counts": [len(card.focus_evidence_refs) for card in cards],
                        "probe_counts": [len(card.relation_probes) for card in cards],
                        "probe_roles": [
                            [probe.role for probe in card.relation_probes] for card in cards
                        ],
                        "repaired": repaired,
                        "skip_reason": skip_reason,
                        "prefix_warmup_owner": warmup_lock is not None,
                    },
                    status_message="completed",
                )
                return AtomicCardExtractionResult(
                    chunk_id=chunk.chunk_id,
                    spans=spans,
                    cards=cards,
                    repaired=repaired,
                    skip_reason=skip_reason,
                )
            finally:
                if warmup_lock is not None:
                    await self._release_prefix_warmup_lock(warmup_lock)

    async def _cards_from_response(
        self,
        *,
        chunk: EvidenceChunk,
        spans: list[Any],
        request: LLMProxyRequest,
        response: Any,
    ) -> tuple[list[AtomicCognitiveCard], bool, str]:
        issues: list[str] = []
        with langfuse_observation(
            name="kg.atomic_card.validate",
            as_type="span",
            input={"chunk_id": chunk.chunk_id},
        ):
            try:
                cards, skip_reason = self._validate_response(chunk, spans, response.structured_output)
                langfuse_update_span(
                    output={"valid": True, "card_count": len(cards)},
                    status_message="completed",
                )
                return cards, False, skip_reason
            except Exception as exc:
                issues.append(str(exc))
                langfuse_update_span(
                    output={"valid": False, "issues": issues},
                    level="WARNING",
                    status_message="repair_required",
                )

        with langfuse_observation(
            name="kg.atomic_card.repair",
            as_type="span",
            input={"chunk_id": chunk.chunk_id, "issues": issues},
        ):
            repaired = await self._llm.repair_with_feedback(
                request,
                response,
                issues,
                instruction=(
                    "上一轮原子 Cognitive Card 输出未通过契约校验。"
                    "只修复事件边界、JSON 结构、Span Ref、原文事实锚点和 Probe 合规性；"
                    "修复后重新检查每个 Card 的焦点证据是否完整支撑 Summary 和全部事实锚点，"
                    "缺少支撑时补充已有 Span Ref 或删除对应表述；"
                    "不得新增外部事实，不得恢复旧 topic_intents 或主题标签字段。"
                ),
                retry_reason="atomic_cognitive_card_validation_invalid",
            )
        try:
            cards, skip_reason = self._validate_response(chunk, spans, repaired.structured_output)
            return cards, True, skip_reason
        except Exception as exc:
            raise RuntimeError(
                f"原子 Cognitive Card 修复后仍未通过校验: chunk_id={chunk.chunk_id}; "
                f"first_issues={issues}; repair_issue={exc}"
            ) from exc

    @staticmethod
    def _validate_response(
        chunk: EvidenceChunk,
        spans: list[Any],
        data: Any,
    ) -> tuple[list[AtomicCognitiveCard], str]:
        if not isinstance(data, dict):
            raise ValueError(f"顶层输出必须是 JSON object，实际为 {type(data).__name__}")
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("cards 必须是数组")
        if len(raw_cards) > 12:
            raise ValueError("cards 不能超过 12 项")
        skip_reason = str(data.get("skip_reason") or "").strip()
        if not raw_cards and not skip_reason:
            raise ValueError("cards 为空时必须提供 skip_reason")
        cards = [
            atomic_card_from_llm_item(chunk, item, spans=spans)
            for item in raw_cards
            if isinstance(item, dict)
        ]
        if len(cards) != len(raw_cards):
            raise ValueError("cards 中存在非对象元素")
        ids = [card.cognitive_card_id for card in cards]
        if len(ids) != len(set(ids)):
            raise ValueError("同一 Chunk 输出了身份相同的重复原子 Card")
        return cards, skip_reason

    async def _claim_prefix_warmup_lock(self) -> Any | None:
        if settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS <= 0:
            return None
        if await self._prefix_recently_warmed():
            return None
        lock = self._prefix_warmup_lock()
        acquired = await self._try_acquire_prefix_warmup_lock(lock)
        if acquired is None:
            return None
        if not acquired:
            await self._wait_for_prefix_warmup()
            return None
        if await self._prefix_recently_warmed():
            await self._release_prefix_warmup_lock(lock)
            return None
        return lock

    def _prefix_warmup_lock(self) -> Any:
        return self._redis_client().lock(
            ATOMIC_CARD_PREFIX_WARM_LOCK_KEY,
            timeout=max(1, settings.KG_COGNITIVE_CARD_PREFIX_WARM_LOCK_TIMEOUT_SECONDS),
            blocking_timeout=0,
            sleep=ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS,
        )

    async def _try_acquire_prefix_warmup_lock(self, lock: Any) -> bool | None:
        try:
            return bool(await asyncio.to_thread(lambda: lock.acquire(blocking=False)))
        except Exception as exc:
            logger.warning("原子 Card 前缀预热锁不可用: %s", exc)
            return None

    async def _wait_for_prefix_warmup(self) -> None:
        deadline = time.monotonic() + max(
            1,
            settings.KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS,
        )
        while time.monotonic() < deadline:
            if await self._prefix_recently_warmed():
                return
            await asyncio.sleep(ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS)

    async def _prefix_recently_warmed(self) -> bool:
        try:
            return bool(await asyncio.to_thread(self._redis_client().exists, ATOMIC_CARD_PREFIX_WARM_MARK_KEY))
        except Exception:
            return False

    async def _mark_prefix_warmed(self, *, settle: bool = False) -> None:
        try:
            if settle and settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS > 0:
                await asyncio.sleep(settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS)
            await asyncio.to_thread(
                self._redis_client().setex,
                ATOMIC_CARD_PREFIX_WARM_MARK_KEY,
                max(1, settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS),
                str(int(time.time())),
            )
        except Exception:
            return

    async def _release_prefix_warmup_lock(self, lock: Any) -> None:
        try:
            await asyncio.to_thread(lock.release)
        except Exception:
            return

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis


class AtomicCognitiveCardStageService:
    """完成 Card 提取、PG manifest 替换和 Milvus 发布后停止。"""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        semantic_retriever: MilvusSemanticHybridRetriever,
        extractor: AtomicCognitiveCardExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._semantic_retriever = semantic_retriever
        self._extractor = extractor or AtomicCognitiveCardExtractor(
            concurrency=max(
                1,
                min(4, int(getattr(settings, "CLAUDE_PROXY_MAX_CONCURRENCY", 2) or 2)),
            )
        )

    async def refresh(
        self,
        *,
        adapter_name: str,
        target: str,
        kg_version: str,
        changed_chunks: list[EvidenceChunk],
        persist: bool = True,
    ) -> AtomicCardStageResult:
        with langfuse_observation(
            name="kg.atomic_card.stage",
            as_type="chain",
            input={
                "adapter_name": adapter_name,
                "target": target,
                "changed_chunks": len(changed_chunks),
                "persist": persist,
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            if not changed_chunks:
                diagnostics = self._diagnostics([], {}, 0, 0, assignment_executed=False)
                langfuse_update_span(output=diagnostics, status_message="cards_ready")
                return AtomicCardStageResult(status="cards_ready", cards=[], diagnostics=diagnostics)

            extraction_results = await self._extractor.extract_with_diagnostics(changed_chunks)
            cards = [card for result in extraction_results for card in result.cards]
            persistence: dict[str, Any] = {}
            documents_written = 0
            stale_documents_deleted = 0

            if persist:
                evidence_ids = list(dict.fromkeys(chunk.evidence_id for chunk in changed_chunks))
                with langfuse_observation(
                    name="kg.atomic_card.pg_replace",
                    as_type="span",
                    input={"evidence_count": len(evidence_ids), "card_count": len(cards)},
                ):
                    persistence = self._repository.replace_atomic_cognitive_cards_for_evidence(
                        adapter_name,
                        evidence_ids=evidence_ids,
                        cards=cards,
                    )
                    langfuse_update_span(output=persistence, status_message="completed")

                documents = [atomic_card_document(card) for card in cards]
                with langfuse_observation(
                    name="kg.atomic_card.milvus_upsert",
                    as_type="span",
                    input={"document_count": len(documents)},
                ):
                    documents_written = await self._semantic_retriever.upsert_semantic_documents(
                        adapter_name=adapter_name,
                        target=target,
                        documents=documents,
                        kg_version=kg_version,
                    )
                    langfuse_update_span(
                        output={"documents_written": documents_written},
                        status_message="completed",
                    )

                current_ids = {card.cognitive_card_id for card in cards}
                stale_ids = [
                    card_id
                    for card_id in persistence.get("deleted_card_ids") or []
                    if card_id and card_id not in current_ids
                ]
                with langfuse_observation(
                    name="kg.atomic_card.delete_stale",
                    as_type="span",
                    input={"target_ids": stale_ids},
                ):
                    stale_documents_deleted = await self._semantic_retriever.delete_documents_by_role(
                        collection_role="cognitive_card",
                        adapter_name=adapter_name,
                        target=target,
                        target_ids=stale_ids,
                    )
                    langfuse_update_span(
                        output={"deleted": stale_documents_deleted},
                        status_message="completed",
                    )

            diagnostics = self._diagnostics(
                extraction_results,
                persistence,
                documents_written,
                stale_documents_deleted,
                assignment_executed=False,
            )
            langfuse_update_span(output=diagnostics, status_message="cards_ready")
            return AtomicCardStageResult(status="cards_ready", cards=cards, diagnostics=diagnostics)

    @staticmethod
    def _diagnostics(
        extraction_results: list[AtomicCardExtractionResult],
        persistence: dict[str, Any],
        documents_written: int,
        stale_documents_deleted: int,
        *,
        assignment_executed: bool,
    ) -> dict[str, Any]:
        card_count = sum(len(result.cards) for result in extraction_results)
        chunk_count = len(extraction_results)
        return {
            "status": "cards_ready",
            "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
            "input_chunks": chunk_count,
            "successful_chunks": chunk_count,
            "zero_card_chunks": sum(1 for result in extraction_results if not result.cards),
            "repaired_chunks": sum(1 for result in extraction_results if result.repaired),
            "zero_card_reasons": [
                {"chunk_id": result.chunk_id, "reason": result.skip_reason}
                for result in extraction_results
                if not result.cards and result.skip_reason
            ],
            "cards": card_count,
            "average_cards_per_chunk": round(card_count / chunk_count, 3) if chunk_count else 0.0,
            "pg_inserted_cards": int(persistence.get("inserted_cards") or 0),
            "pg_deleted_cards": int(persistence.get("deleted_cards") or 0),
            "milvus_documents_written": documents_written,
            "milvus_stale_documents_deleted": stale_documents_deleted,
            "assignment_executed": assignment_executed,
        }
