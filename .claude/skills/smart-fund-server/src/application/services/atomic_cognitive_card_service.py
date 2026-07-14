"""原子 Cognitive Card 的提取、校验和发布服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.atomic_cognitive_card import (
    ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
    ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
    INTRA_CHUNK_RELATION_KINDS,
    AtomicCardExtractionResult,
    AtomicCognitiveCard,
    StableSpanSegmenter,
    atomic_card_focus_document,
    atomic_card_summary_document,
    atomic_card_from_llm_item,
    intra_chunk_relation_from_llm_item,
)
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.relation_discovery import VerifiedRelationDecision
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
ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION = "atomic_card_intra_chunk_relation_v1"


ATOMIC_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "local_card_id": {
                        "type": "string",
                        "pattern": "^c([1-9]|1[0-2])$",
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                    "focus_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "local_card_id",
                    "summary",
                    "focus_evidence_refs",
                ],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "maxItems": 66,
            "items": {
                "type": "object",
                "properties": {
                    "source_card_id": {
                        "type": "string",
                        "pattern": "^c([1-9]|1[0-2])$",
                    },
                    "target_card_id": {
                        "type": "string",
                        "pattern": "^c([1-9]|1[0-2])$",
                    },
                    "decision_class": {
                        "type": "string",
                        "enum": ["observed"],
                    },
                    "relation_kind": {
                        "type": "string",
                        "enum": sorted(INTRA_CHUNK_RELATION_KINDS),
                    },
                    "relation_type": {"type": "string", "minLength": 1, "maxLength": 120},
                    "direction": {"type": "string", "minLength": 1, "maxLength": 160},
                    "basis": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "source_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "target_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "inference_mechanism": {"type": "string", "maxLength": 1000},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "source_card_id",
                    "target_card_id",
                    "decision_class",
                    "relation_kind",
                    "relation_type",
                    "direction",
                    "basis",
                    "source_evidence_refs",
                    "target_evidence_refs",
                    "inference_mechanism",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "skip_reason": {"type": "string", "maxLength": 240},
    },
    "required": ["cards", "relations", "skip_reason"],
    "additionalProperties": False,
}


ATOMIC_CARD_SYSTEM_PROMPT = """你是知识图谱的原子 Cognitive Card 抽取器。

你的输入包含程序按原文顺序切分并编号的 Span；全部 Span 共同构成当前 chunk 的完整正文，是唯一的正文来源。你必须在一次输出中完成事件边界判断、原子 Card 拆分、Summary、焦点证据，以及最终 Card 之间由当前原文直接支持的关系。

在输出 JSON 前，先在内部完成候选事实枚举、重复事实合并、最终 Card 集合确定和关系检查；不要输出草稿、分析过程或被放弃的 Card。JSON 必须先完整输出 cards，再输出 relations。

原子 Card 规则：
- 一个 Card 只能表达一个可以独立理解、信息闭合并可供后续关系发现使用的事件或状态单元。原子化目标是“最小但完整”，不是把正文切成最小语法命题或最小字段。
- 在生成 Card 前，先在内部识别原文包含的事件、状态和事实主张，再按核心谓词确定 Card 边界；不要输出分析过程。
- 每张 Card 只保留一个核心谓词，以及识别该事实不可缺少的主体、对象、时间、数值、条件和限定语。
- 核心谓词表示事件身份、动作或状态变化，不等于句子中的语法动词。用于报告当前水平、数值、比例、幅度、排名、范围或结果状态的表达，如果附着于同一事件身份或同一观测快照，只是核心事实的属性，不形成新的 Card。
- “可以分别判断真假”不是拆分的充分条件。若多个陈述共享同一主体、同一核心对象、同一目标时间和同一核心谓词，并共同描述该事件或状态的方向、幅度、绝对水平、比例、数量、范围或必要限定，它们属于同一 Card 的互补属性，应合并为一个完整事实。
- 只有原文包含两个不同的核心谓词，形成能够分别参与后续关系判断的独立动作、状态变化或事实端点时，才拆成不同 Card。原文明确写出的前后、因果、印证、冲突、共同驱动或约束应通过 relations 连接这些独立端点。
- 同一主体、同一来源、同一句话、相邻出现、同时发生或围绕同一宽泛事件，都不能单独作为合并事实的理由。
- 用于补全当前核心谓词的指代、对象、数值、条件、结果状态或必要限定，应保留在同一 Card；不要把一个完整观测拆成“变化事实”和“观测值事实”。
- 核心动作与定义该动作的期限、范围、幅度、最终值、相对基准或调整结果属于同一事实闭包。原文以“原方案/原估计/原要求”对比最终结果时，应合并成一张变化 Card，不能把基准、调整动作和最终结果拆成多张 Card。
- 不要按句子数、公司数、数字数或字段数机械拆分；拆分依据是是否存在多个语义上独立的核心谓词，而不是信息能否单独摘出来陈述。
- 当前 chunk 没有可独立验证的事件或事实时允许 cards=[]，并说明 skip_reason；不得强行制造 Card。
- Summary 必须忠实概括当前 Card，不写主题标签，不添加原文没有的主体、数字、时间、原因、结果或预测。
- 每张最终 Card 按输出顺序使用 c1、c2、c3 这样的 local_card_id；编号必须连续且唯一，只用于本次 relations 引用。
- 如果两个候选 Card 只是同一原子事实的总述、局部举例、数字细节、改写或重复表达，应在输出前合并；不得保留两个 Card 后再用 same_event 掩盖重复拆分。
- 在确定最终 Card 集合后，逐对执行合并检查：如果两张候选 Card 的主体、核心对象和目标时间相同，拆开后只能解释为同一观测的不同指标、不同数值字段、方向与水平、总述与细节，或者只能建立“同一事件/属性属于该事件”的连接，就必须合并。只有它们代表不同事实端点，或彼此确实无关，才允许同时保留。

证据规则：
- focus_evidence_refs 只能引用输入中存在的 Span Ref，至少一个；应选择能够独立验证当前 Card 的最小证据闭合集合。证据闭合优先于引用数量少。
- Summary 中的主体、动作、对象、时间、原因和结果，都必须能从 focus_evidence_refs 指向的 Span 中直接得到；缺少支撑时，必须补充相应 Span Ref 或删除未被支撑的表述。
- source_published_at 只用于解释相对时间，不是事实证据；不得从 Chunk 标识或其他元数据补充正文 Span 没有的事实。
- 多个 Card 可以引用同一个 Span，但仅限该 Span 对每个 Card 都是不可缺少的直接证据；不要为了制造互斥边界而强行拆开证据，也不要仅因共享背景或主题而重复引用。
- 生成每个 Card 时，先确定 Summary，再分别定位支撑其中每一项信息的 Span Ref，最后将这些 Ref 的并集作为 focus_evidence_refs。
- 同一事实由连续上下文共同表达时，不要跳过承载主体、动作对象、指标名称或指代衔接的中间 Span；“最小”不等于只选择包含数字或关键词的 Span。
- 输出前仅阅读每个 Card 的 focus_evidence_refs，逐项核对 Summary；如果不能确认某条信息为何属于当前 Card，先补充引用或删除相应内容，再输出结果。不要输出核对过程。

同 Chunk Relation 规则：
- 关系质量以精度优先，漏掉一条弱关系好于制造一条伪关系。relations 不需要覆盖所有 Card，存在任何不确定性时直接省略该关系。
- relations 只能引用本次 cards 中已经完整输出的 local_card_id，且两端必须是不同 Card；只输出有正关系的 Card 对，没有关系的组合不要输出。
- 每一对 Card 最多输出一条能够完整表达其核心连接的关系。不要因为同篇出现、位置相邻、共享标题、共享主体、共享背景或属于同一宽泛事件就创建关系。
- relations 必须在全部 cards 之后生成，以最终 Card 集合为准进行整体比较，不按 Card 输出顺序制造单向偏差。
- 本阶段只输出 observed：当前原文必须直接写明双方是明确前后关系、直接因果、印证、冲突、共同具体驱动或共同具体约束。需要模型补充连接机制的 inferred 关系一律省略，不得为了满足 Schema 把它改标为 observed。
- relation_kind 只能是 confirmation、contradiction、temporal_progression、causal_influence、common_driver、constraint。同一原子事实不建立 same_event，而应在 Card 阶段合并。
- relation_type、direction 和 basis 必须描述当前两个原子事实之间的具体连接，不能只说“相关”“同属某主题”或“在同一报道中出现”。
- basis 只陈述 source_evidence_refs 和 target_evidence_refs 直接写明的端点事实及连接依据，不得把原文未写明的诉讼触发、决策动机、传导环节或其他中间事件补进 basis。需要组合端点才能成立的最短推理只能写入 inference_mechanism。
- basis 应使用可读事实语义，不在正文中列举 s0001 等 Ref 标签；证据标签只放在 source_evidence_refs 和 target_evidence_refs。
- causal_influence 的措辞强度必须服从证据。只有原文明示因果连接时，才能使用“直接导致、触发、引发、促使、影响、支撑、直接原因、重要理由”等确定性措辞；可解释的事实前提、同一主体或时间先后不能写成已经证实的原因、动机或决策依据。
- observed 关系必须能在 source_evidence_refs 与 target_evidence_refs 指向的原文中定位到直接连接双方的表述；端点事实分别存在但连接语义只出现在模型改写中时，不属于 observed。
- “A 发生在前、B 发生在后”“B 针对与 A 相同的主体”或“从常识看 A 可以解释 B”都不足以建立 causal_influence。原文没有直接连接双方时不输出关系。
- 同一公告、裁决、报告或叙述中同时出现两个事实，不代表其中一个是另一个的原因；原文没有给出连接时，应输出无关系，而不是用“隐含因果、合理前提、可能影响”等措辞补足。
- 如果关系成立必须依赖原文没有形成 Card、也没有被双方最小充分证据直接支持的第三个事件或中间环节，则不要创建该关系。
- local_card_id 只用于关系端点引用；relation_type、direction、basis 和 inference_mechanism 必须使用事实语义表述，不能把 c1、c2 或“source/target Card”写入永久关系说明。
- source_evidence_refs 和 target_evidence_refs 必须分别属于对应 Card 的 focus_evidence_refs，并且只引用证明关系成立的最小充分集合；公共标题和共享背景不能作为主要关系证据。
- inference_mechanism 必须输出空字符串。confidence 只表示原文对关系的支持强度。
- 如果某个总述与局部细节之间只能成立“举例、包含、细化”关系，说明两者没有形成独立原子事件，应优先合并 Card，不要创建近义 Edge。

输出前最终核对：
- Card：每张 Card 只有一个核心谓词；围绕同一谓词的方向、幅度、绝对水平和限定信息必须保留为完整事实，不能拆成多张残缺 Card。
- Card 边界：只有不同核心谓词形成独立事实端点时才拆分，并检查这些端点之间是否需要输出 relation。
- Card 集合：任意两个 Card 都不是同一原子事实的总述与细节、复述或近义改写；如果是，先合并再输出最终 cards。
- 合并闸门：若两张 Card 共享同一事件身份或观测快照，且差异只来自指标字段或数值表达，必须重新合并并联合引用相应证据；不能以 relations=[] 结束这种拆分。
- 证据：每项 Summary 都能由 focus_evidence_refs 独立验证。
- Relation：仅在两个不同原子事实之间存在当前原文可证明的具体连接时输出；端点、方向、证据引用和推理机制彼此一致。
- Relation 措辞：basis 不包含未被引用证据写明的中间事实或 Ref 标签；不把时间先后、同一主体或可能的事实前提夸大成直接原因。
- Relation 精度：逐条尝试从引用 Span 中找到连接表述，并检查推理是否只使用两端 Summary；任一检查失败就删除该关系，不为提高关系数量保留弱连接。

不要输出主题目录、Community、Assignment、未来预测、风险标签或其他旧 Card 字段。只输出符合 JSON Schema 的 cards、relations 和 skip_reason，不要输出 Markdown、解释文字或自检过程。"""


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
            return AtomicCardExtractionResult(
                chunk_id=chunk.chunk_id,
                spans=[],
                cards=[],
                relations=[],
            )

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
            provider_options={"reasoning_effort": "medium"},
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
                cards, relations, repaired, skip_reason = await self._cards_from_response(
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
                        "intra_chunk_relation_count": len(relations),
                        "intra_chunk_relation_kinds": [
                            relation.relation_kind for relation in relations
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
                    relations=relations,
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
    ) -> tuple[list[AtomicCognitiveCard], list[VerifiedRelationDecision], bool, str]:
        issues: list[str] = []
        with langfuse_observation(
            name="kg.atomic_card.validate",
            as_type="span",
            input={"chunk_id": chunk.chunk_id},
        ):
            try:
                cards, relations, skip_reason = self._validate_response(
                    chunk,
                    spans,
                    response.structured_output,
                )
                langfuse_update_span(
                    output={
                        "valid": True,
                        "card_count": len(cards),
                        "relation_count": len(relations),
                    },
                    status_message="completed",
                )
                return cards, relations, False, skip_reason
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
                    "只修复事件边界、JSON 结构和 Span Ref 合规性；"
                    "先确定去重后的最终 cards，再修复只引用这些 Card 的 relations；"
                    "修复后重新检查每个 Card 的焦点证据是否完整支撑 Summary，"
                    "缺少支撑时补充已有 Span Ref 或删除对应表述；"
                    "不得新增外部事实，不得恢复旧 topic_intents 或主题标签字段。"
                ),
                retry_reason="atomic_cognitive_card_validation_invalid",
            )
        try:
            cards, relations, skip_reason = self._validate_response(
                chunk,
                spans,
                repaired.structured_output,
            )
            return cards, relations, True, skip_reason
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
    ) -> tuple[list[AtomicCognitiveCard], list[VerifiedRelationDecision], str]:
        if not isinstance(data, dict):
            raise ValueError(f"顶层输出必须是 JSON object，实际为 {type(data).__name__}")
        expected_top_level = {"cards", "relations", "skip_reason"}
        if set(data) != expected_top_level:
            raise ValueError(
                "顶层字段不符合契约: "
                f"missing={sorted(expected_top_level.difference(data))}, "
                f"extra={sorted(set(data).difference(expected_top_level))}"
            )
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("cards 必须是数组")
        if len(raw_cards) > 12:
            raise ValueError("cards 不能超过 12 项")
        skip_reason = str(data.get("skip_reason") or "").strip()
        if not raw_cards and not skip_reason:
            raise ValueError("cards 为空时必须提供 skip_reason")
        cards: list[AtomicCognitiveCard] = []
        cards_by_local_id: dict[str, AtomicCognitiveCard] = {}
        for index, item in enumerate(raw_cards, start=1):
            if not isinstance(item, dict):
                raise ValueError("cards 中存在非对象元素")
            local_card_id = str(item.get("local_card_id") or "").strip()
            expected_local_id = f"c{index}"
            if local_card_id != expected_local_id:
                raise ValueError(
                    f"local_card_id 必须按 cards 顺序连续编号: "
                    f"expected={expected_local_id}, actual={local_card_id}"
                )
            raw_card = {key: value for key, value in item.items() if key != "local_card_id"}
            card = atomic_card_from_llm_item(chunk, raw_card, spans=spans)
            cards.append(card)
            cards_by_local_id[local_card_id] = card
        ids = [card.cognitive_card_id for card in cards]
        if len(ids) != len(set(ids)):
            raise ValueError("同一 Chunk 输出了身份相同的重复原子 Card")

        raw_relations = data.get("relations")
        if not isinstance(raw_relations, list):
            raise ValueError("relations 必须是数组")
        if not cards and raw_relations:
            raise ValueError("cards 为空时不能输出 relations")
        relations = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in raw_relations:
            if not isinstance(item, dict):
                raise ValueError("relations 中存在非对象元素")
            local_pair = tuple(
                sorted(
                    (
                        str(item.get("source_card_id") or "").strip(),
                        str(item.get("target_card_id") or "").strip(),
                    )
                )
            )
            if local_pair in seen_pairs:
                raise ValueError(f"同一 Card 对只能输出一条关系: {local_pair}")
            seen_pairs.add(local_pair)
            relation = intra_chunk_relation_from_llm_item(
                item,
                cards_by_local_id=cards_by_local_id,
            )
            if relation.decision_class != "observed":
                raise ValueError("同 Chunk 快速路径只允许 observed Relation")
            relations.append(relation)
        return cards, relations, skip_reason

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
            return await self._wait_for_prefix_warmup()
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

    async def _wait_for_prefix_warmup(self) -> Any | None:
        deadline = time.monotonic() + max(
            1,
            settings.KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS,
        )
        while time.monotonic() < deadline:
            if await self._prefix_recently_warmed():
                return None
            lock = self._prefix_warmup_lock()
            acquired = await self._try_acquire_prefix_warmup_lock(lock)
            if acquired is None:
                return None
            if acquired:
                if await self._prefix_recently_warmed():
                    await self._release_prefix_warmup_lock(lock)
                    return None
                return lock
            await asyncio.sleep(ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS)
        return None

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
        relation_writer: Any | None = None,
    ) -> None:
        self._repository = repository
        self._semantic_retriever = semantic_retriever
        self._relation_writer = relation_writer
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
            inactive_cleanup = {
                "card_ids": [],
                "deleted_documents": 0,
                "deleted_manifests": 0,
                "relation_result": {},
            }
            if persist:
                inactive_cleanup = await self._cleanup_inactive_evidence_cards(
                    adapter_name=adapter_name,
                    target=target,
                )
            if not changed_chunks:
                diagnostics = self._diagnostics(
                    [],
                    {
                        "deleted_cards": inactive_cleanup["deleted_manifests"],
                        "deleted_card_ids": inactive_cleanup["card_ids"],
                    },
                    0,
                    inactive_cleanup["deleted_documents"],
                    inactive_cleanup["relation_result"],
                    {},
                    assignment_executed=False,
                )
                langfuse_update_span(output=diagnostics, status_message="cards_ready")
                return AtomicCardStageResult(status="cards_ready", cards=[], diagnostics=diagnostics)

            extraction_results = await self._extractor.extract_with_diagnostics(changed_chunks)
            cards = [card for result in extraction_results for card in result.cards]
            intra_chunk_relations = [
                relation
                for result in extraction_results
                for relation in result.relations
            ]
            intra_chunk_sync_decisions = _intra_chunk_relation_sync_decisions(
                extraction_results
            )
            persistence: dict[str, Any] = {}
            documents_written = 0
            stale_documents_deleted = int(inactive_cleanup["deleted_documents"])
            stale_relation_result: dict[str, Any] = dict(inactive_cleanup["relation_result"])
            intra_chunk_relation_result: dict[str, Any] = {}

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

                chunks_by_id = {chunk.chunk_id: chunk for chunk in changed_chunks}
                summary_documents = [atomic_card_summary_document(card) for card in cards]
                focus_documents = [
                    atomic_card_focus_document(
                        card,
                        chunk_content=chunks_by_id[card.primary_chunk_id].content,
                    )
                    for card in cards
                ]
                documents = [*summary_documents, *focus_documents]
                with langfuse_observation(
                    name="kg.atomic_card.milvus_upsert",
                    as_type="span",
                    input={
                        "summary_documents": len(summary_documents),
                        "focus_evidence_documents": len(focus_documents),
                    },
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
                    deleted_summary = await self._semantic_retriever.delete_documents_by_role(
                        collection_role="cognitive_card",
                        adapter_name=adapter_name,
                        target=target,
                        target_ids=stale_ids,
                    )
                    deleted_focus = await self._semantic_retriever.delete_documents_by_role(
                        collection_role="cognitive_card_focus",
                        adapter_name=adapter_name,
                        target=target,
                        target_ids=stale_ids,
                    )
                    stale_documents_deleted = deleted_summary + deleted_focus
                    langfuse_update_span(
                        output={
                            "summary_deleted": deleted_summary,
                            "focus_evidence_deleted": deleted_focus,
                            "deleted": stale_documents_deleted,
                        },
                        status_message="completed",
                    )
                if stale_ids and self._relation_writer is not None:
                    with langfuse_observation(
                        name="kg.atomic_card.invalidate_relations",
                        as_type="span",
                        input={"stale_card_ids": stale_ids},
                    ):
                        replacement_relation_result = await self._relation_writer.invalidate_cards(
                            stale_ids,
                            adapter_name=adapter_name,
                            target=target,
                        )
                        stale_relation_result = _merge_relation_write_results(
                            stale_relation_result,
                            replacement_relation_result,
                        )
                        langfuse_update_span(
                            output=stale_relation_result,
                            status_message="completed",
                        )
                if intra_chunk_sync_decisions:
                    if self._relation_writer is None:
                        raise RuntimeError("同 Chunk Relation 已生成，但 CardRelationWriteService 未配置")
                    with langfuse_observation(
                        name="kg.atomic_card.persist_intra_chunk_relations",
                        as_type="span",
                        input={
                            "positive_relations": len(intra_chunk_relations),
                            "synchronized_pairs": len(intra_chunk_sync_decisions),
                        },
                    ):
                        intra_chunk_relation_result = (
                            await self._relation_writer.persist_verified_decisions(
                                intra_chunk_sync_decisions,
                                adapter_name=adapter_name,
                                target=target,
                                pipeline_version=ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION,
                                model_name=resolve_kg_llm_model("kg_cognitive_card"),
                                prompt_version=ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                            )
                        )
                        langfuse_update_span(
                            output=intra_chunk_relation_result,
                            status_message="completed",
                        )

            diagnostics = self._diagnostics(
                extraction_results,
                persistence,
                documents_written,
                stale_documents_deleted,
                stale_relation_result,
                intra_chunk_relation_result,
                assignment_executed=False,
            )
            langfuse_update_span(output=diagnostics, status_message="cards_ready")
            return AtomicCardStageResult(status="cards_ready", cards=cards, diagnostics=diagnostics)

    async def _cleanup_inactive_evidence_cards(
        self,
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, Any]:
        """清理已失效 Evidence 留下的 Card、语义视图和正式 Edge。"""

        card_ids = self._repository.list_atomic_cognitive_card_ids_for_inactive_evidence(
            adapter_name
        )
        if not card_ids:
            return {
                "card_ids": [],
                "deleted_documents": 0,
                "deleted_manifests": 0,
                "relation_result": {},
            }
        if self._relation_writer is None:
            raise RuntimeError("清理失效 Evidence Card 需要正式 Relation Writer")
        with langfuse_observation(
            name="kg.atomic_card.cleanup_inactive_evidence",
            as_type="span",
            input={"card_ids": card_ids},
        ):
            deleted_summary = await self._semantic_retriever.delete_documents_by_role(
                collection_role="cognitive_card",
                adapter_name=adapter_name,
                target=target,
                target_ids=card_ids,
            )
            deleted_focus = await self._semantic_retriever.delete_documents_by_role(
                collection_role="cognitive_card_focus",
                adapter_name=adapter_name,
                target=target,
                target_ids=card_ids,
            )
            relation_result = await self._relation_writer.invalidate_cards(
                card_ids,
                adapter_name=adapter_name,
                target=target,
            )
            deleted_manifests = self._repository.delete_atomic_cognitive_cards_by_ids(
                adapter_name,
                cognitive_card_ids=card_ids,
            )
            result = {
                "card_ids": card_ids,
                "deleted_documents": deleted_summary + deleted_focus,
                "deleted_manifests": deleted_manifests,
                "relation_result": relation_result,
            }
            langfuse_update_span(output=result, status_message="completed")
            return result

    @staticmethod
    def _diagnostics(
        extraction_results: list[AtomicCardExtractionResult],
        persistence: dict[str, Any],
        documents_written: int,
        stale_documents_deleted: int,
        stale_relation_result: dict[str, Any],
        intra_chunk_relation_result: dict[str, Any],
        *,
        assignment_executed: bool,
    ) -> dict[str, Any]:
        card_count = sum(len(result.cards) for result in extraction_results)
        intra_chunk_relation_count = sum(
            len(result.relations) for result in extraction_results
        )
        intra_chunk_observed = sum(
            relation.decision_class == "observed"
            for result in extraction_results
            for relation in result.relations
        )
        intra_chunk_inferred = sum(
            relation.decision_class == "inferred"
            for result in extraction_results
            for relation in result.relations
        )
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
            "invalidated_relation_edge_ids": list(
                stale_relation_result.get("changed_edge_ids") or []
            ),
            "intra_chunk_relations": intra_chunk_relation_count,
            "intra_chunk_observed": intra_chunk_observed,
            "intra_chunk_inferred": intra_chunk_inferred,
            "intra_chunk_changed_edge_ids": list(
                intra_chunk_relation_result.get("changed_edge_ids") or []
            ),
            "intra_chunk_graph_event_ids": list(
                intra_chunk_relation_result.get("graph_event_ids") or []
            ),
            "assignment_executed": assignment_executed,
        }


def _merge_relation_write_results(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    result = {**left, **right}
    for key in (
        "touched_edge_ids",
        "changed_edge_ids",
        "milvus_upserted_edge_ids",
        "milvus_deleted_edge_ids",
        "graph_event_ids",
        "affected_card_ids",
    ):
        result[key] = list(
            dict.fromkeys([*(left.get(key) or []), *(right.get(key) or [])])
        )
    return result


def _intra_chunk_relation_sync_decisions(
    extraction_results: list[AtomicCardExtractionResult],
) -> list[VerifiedRelationDecision]:
    """把每个 Chunk 的正关系补成完整 pair 状态，确保重跑可撤销旧 Edge。"""

    decisions: list[VerifiedRelationDecision] = []
    for result in extraction_results:
        positive_by_pair = {
            tuple(sorted((item.source_card_id, item.target_card_id))): item
            for item in result.relations
        }
        for left, right in combinations(result.cards, 2):
            pair = tuple(sorted((left.cognitive_card_id, right.cognitive_card_id)))
            positive = positive_by_pair.get(pair)
            if positive is not None:
                decisions.append(positive)
                continue
            decisions.append(
                VerifiedRelationDecision(
                    source_card_id=left.cognitive_card_id,
                    target_card_id=right.cognitive_card_id,
                    decision_class="no_relation",
                    relation_kind="",
                    relation_type="",
                    direction="",
                    basis="",
                    source_evidence_refs=[],
                    target_evidence_refs=[],
                    inference_mechanism="",
                    confidence=0.0,
                )
            )
    return decisions
