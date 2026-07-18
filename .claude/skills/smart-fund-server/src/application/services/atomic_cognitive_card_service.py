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
    render_atomic_card_prompt_input,
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

ATOMIC_CARD_MAX_TOKENS = 10000
ATOMIC_CARD_PREFIX_WARM_MARK_KEY = (
    f"{JETTASK_PREFIX}:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmed"
)
ATOMIC_CARD_PREFIX_WARM_LOCK_KEY = (
    f"{JETTASK_PREFIX}:lock:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmup"
)
ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS = 0.05
ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION = "atomic_card_intra_chunk_relation_v1"


ATOMIC_CARD_ITEM_SCHEMA: dict[str, Any] = {
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
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
        "relation_probes": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": [
                            "same_event",
                            "upstream",
                            "downstream",
                            "confirmation",
                            "contradiction",
                        ],
                    },
                    "query": {"type": "string", "minLength": 1, "maxLength": 300},
                },
                "required": ["role", "query"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "local_card_id",
        "summary",
        "focus_evidence_refs",
        "relation_probes",
    ],
    "additionalProperties": False,
}


ATOMIC_RELATION_ITEM_SCHEMA: dict[str, Any] = {
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
        "relation_kind": {
            "type": "string",
            "enum": sorted(INTRA_CHUNK_RELATION_KINDS),
        },
        "basis": {"type": "string", "minLength": 1, "maxLength": 1000},
        "relation_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
    },
    "required": [
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "basis",
        "relation_evidence_refs",
    ],
    "additionalProperties": False,
}


ATOMIC_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "maxItems": 12,
            "items": ATOMIC_CARD_ITEM_SCHEMA,
        },
        "skip_reason": {"type": "string", "maxLength": 240},
    },
    "required": ["cards", "skip_reason"],
    "additionalProperties": False,
}


ATOMIC_RELATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "maxItems": 66,
            "items": ATOMIC_RELATION_ITEM_SCHEMA,
        },
    },
    "required": ["relations"],
    "additionalProperties": False,
}


ATOMIC_CARD_SYSTEM_PROMPT = """你是知识图谱的原子 Cognitive Card 抽取器。

输入首行是新闻发布时间；其后每行是一条完整原文语句，`<title>` 表示标题，`[sNNNN]` 是紧随文本的证据坐标。同一行连续 Ref 的文本按顺序拼接阅读。当前阶段只负责完整提取、合并重复表达并输出最终 Cards 及其 Relation Probe，按输出顺序分配连续 local_card_id。不要分析或输出 Cards 之间的关系。JSON 必须按 cards、skip_reason 顺序输出；只输出 JSON Schema 要求的字段。

Card：
- 每张 Card 表达一个可独立参与后续关系判断的事件或事实端点，粒度应“最小但完整”。按核心谓词拆分，不按句子、数字、公司或 Ref 数量机械拆分。
- 原文明示两个可独立验证的事实端点时，必须分别形成 Card；不能把“两端事实 + 连接语义”整体写进一张 Card。Card 只表达自身事实，不负责解释它与其他 Card 的关系。
- 同一主体、对象、时间和核心谓词下的方向、幅度、数值、比例、范围、条件与结果状态属于同一事实，应合并；不同主体或不同核心谓词分别形成 Card。
- 同一次观测、统计或披露快照中用于共同描述一个现象的总体值、地区明细、指标值、极值与分项数据，应合并为一个 Card；只有某项本身构成独立动作或关系端点时才拆分。
- 同一主体在不同年度、季度或月度的统计值，如果原文只是把它们共同用于说明一个趋势，应合并为一张保留各期关键数据的 Card，不能把不同报告窗口机械拆成多张 Card。
- 同一来源先给出定性结论、紧接着用数值或分项解释该结论时，应生成一张保留结论和关键明细的完整 Card；不要同时保留一张定性总述 Card 和多张仅用于解释它的明细 Card。只有明细本身构成可被独立引用的完整事实时才独立拆出。
- 总述如果没有增加独立事实，且已被具体 Card 完整表达，就不要重复保留；近义改写、重复表达和局部细节必须合并。不能额外创建一张只汇总其他 Cards 的总述 Card。
- 输出前进行信息包含检查：如果一张 Card 的全部事实只是其他 Card 的改写、并集或概括，二者不能同时保留。
- 同一句或同一段中的并列事实，如果主体、谓词、对象或可验证结论不同，仍是独立端点；方向相同、服务同一策略或属于同一主题不能作为合并理由。
- 同一次会议、交易、诉讼、调查、治理争议或其他完整程序中的提议、回应、表决和结果，如果共同描述一个不可分割的事件，应保留为一张 Card；不能把程序步骤机械拆成多张 Card。只有某一步本身会被其他文档独立引用时才拆分。
- 每个 Summary 必须脱离上下文仍能读懂，明确写出主体和事实，不能输出只能依赖上一张 Card 才成立的残句。
- Summary 只写原文明确表达的完整事实。必须保留消息来源、声明者、预测者、认定者以及“可能、预计、据称”等归因和不确定性，不得把主张改写成客观事实。
- 当前 chunk 没有可独立验证的事实时允许 cards=[]，并填写 skip_reason。Card 按最终顺序使用连续且唯一的 c1、c2、c3。

Card 证据：
- focus_evidence_refs 是能够独立验证 Summary 的最小完整证据闭包；主体、动作、对象、时间、数值、因果和限定语都必须直接出现。Ref 不是事件边界，事实跨多个 Ref 时联合引用。数组元素只写 `sNNNN`，不要带方括号。
- 标题属于原文；标题提供正文未重复的主体、因果、结果或范围时，必须纳入对应 Card 证据。
- published_at 只用于理解相对时间，不能补造正文没有的年份或事实，也不是事实证据。

Relation Probe：
- relation_probes 是当前 Card 用来搜索其他 Chunk 历史 Card 的候选事件描述，不是已经成立的关系，也不能修改当前 Card 事实。只依据当前 Card 的 Summary 和 focus_evidence_refs 生成，不混入本 Chunk 其他 Card 的事实。
- 生成某张 Card 的 Probe 时，暂时忽略输入中不属于该 Card focus_evidence_refs 的 Span 和本次其他 Card。若 query 中的具体日期、数字、主体限定或已发生动作只能从兄弟 Card 得到，说明发生了跨 Card 污染，必须删除该 Probe；同 Chunk 内部关联不通过 Probe 搜索。
- Summary 本身已经是基础语义召回路由。只是复述当前 Summary、替换近义词或罗列当前事实关键词的 Probe 没有增量价值，必须省略。零 Probe 是正常结果；不要为了让 Card 看起来完整而填充 role。
- Probe 必须寻找当前 Card 尚未包含的另一个独立事件。先检查候选 query 是否已经能由当前 Card 的 Summary 或 focus_evidence_refs 直接证明；如果能，它只是 Card 内部事实、局部原因或局部结果，不是跨 Chunk 候选，必须省略。role 描述的是候选事件与整张当前 Card 的关系，不能把当前 Card 内部已经合并的原因重新标成 upstream，也不能把其中已经写明的结果重新标成 downstream。
- Probe 是后续召回使用的关系假设，不是当前原文已经证明的事实。允许描述原文尚未出现、但若历史 Card 存在就能与当前 Card 形成该 role 的候选事件；不能仅因当前原文没有证明候选事件就全部省略，真实性由后续召回和原文核验负责。新增内容只能是建立该关系所必需的事件类型和作用对象，不能编造具体日期、数值、专有主体、已发生结论或中间机制。
- 根据当前 Card 的事实形态选择少量有价值方向：动作或结果可考虑其前置事件、约束和已发生影响；状态或测量可考虑同口径前态、独立确认和不相容状态；声明、报告或预测可考虑被其引用的事实依据和独立反证。这里只是选择方向，不要求每类 Card 都生成 Probe。
- 先写出希望召回的“一张历史 Card 会描述什么候选事件”。query 可以是完整事实句，也可以是面向语义召回的简洁事件描述，但必须脱离上下文仍能识别主体或作用对象、动作或状态以及关系方向；不能是检索指令、问题、无主体标题、原因/影响占位符、括号举例或关键词堆砌。
- Probe 不需要猜中历史 Card 的具体日期、数值或最终结论。缺少这些未知细节时应省略未知值，保留当前 Card 已提供的主体、对象和可观察事件类型，而不是直接放弃整个关系方向。只有连候选主体或作用对象、事件类型都无法从当前 Card 合理约束时才省略。
- query 不得把“更早、此前、后续”等未知范围擅自改成具体年份、月份或期间，也不能增加当前 Card 没有提供的数值与专有主体。
- role 只能是 same_event、upstream、downstream、confirmation、contradiction。same_event 只寻找同一主体、对象和目标期间下不同阶段的前序披露、修订、执行状态或最终结果，不能把相邻年度或季度替换成同一事件，也不能重复当前 Summary。
- upstream 和 downstream 必须描述一个具体可观察事件及其作用对象，不能只写“原因、背景、影响、后续、风险、变化、相关措施”等待补全槽位。缺少足够事实约束、只能依靠模型任意猜测候选事件时省略。
- confirmation 与 contradiction 必须围绕当前 Summary 的同一事实命题、主体、对象、目标期间和统计口径，分别寻找独立支持或不相容状态；不能转去验证其他 Probe，也不能把不同期间或不同指标当作支持或反证。
- 对具有明确主体、目标期间和可证伪状态的 Card，如果能够在不引入新主体、新期间或新指标的前提下写出一个具体的不相容状态，可以生成 contradiction；不要因为其他角色不适用而放弃这条有独立召回价值的反证路径。
- Probe 只搜索在 published_at 时已经可能存在的材料，不是未来订阅条件。当前刚发生的事件通常没有可搜索的后续结果；任何尚未发生的后续状态、处置、反应或影响都必须省略，未来事件入库时会反向发现当前 Card。零 Probe 仍然允许，但只应出现在所有角色都会重复当前 Card、依赖未来事实或无法形成受约束候选事件的情况下；不能因为“候选事件不在当前原文中”而把有明确关系方向的 Card 机械置空。
- 报告、预测、观点或数据发布首先是信息事件。它所描述的现实状态只能作为 confirmation 或 contradiction 的搜索对象；只有在 published_at 前已经发生、且由信息发布本身触发的反应才可能成为 downstream。

输出前核对：每张 Card 均完整且不重复；每项 Summary 都可由自身证据验证；逐条删除不能直接作为历史 Card Summary 的 Probe。只要 Probe 依赖占位推测就省略，不以数量为目标。不要输出 Relations、分析过程、主题标签、Community、预测或 Markdown。"""


ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT = """现在只处理上一轮已经确定的 Cards 之间的同 Chunk 正关系。Cards 已冻结，不得修改、合并、拆分、补充或重新编号。顶层 JSON 对象只包含 `relations` 数组；没有满足证据门槛的关系时该数组为空。

- 检查原文明示的连接及其准确事实端点，并根据上一轮已有 local_card_id 建立关系。不要枚举没有连接证据的 Card 组合。只输出正关系；同篇出现、相邻、同主体、同领域、时间先后或常识上可能相关都不构成关系。
- 同一个完整程序性事件内部的步骤顺序属于该 Card 的事实结构，不单独输出 temporal_progression；Relation 用于连接能够独立复用的事实端点，不用于复述 Card 内部流程。
- 只输出原文直接写明的 confirmation、contradiction、temporal_progression、causal_influence、common_driver、constraint；需要补充中间机制或外部常识的关系省略。
- temporal_progression 必须有原文明示的前后、后续、更新、演进或同一事实状态变化，并且两端主体、指标、统计口径和时间具有可比性。年度、季度、月度等不同观察窗口，除非原文直接比较并明确认定变化，否则不能由模型自行比较比例、幅度或数值后生成关系。
- “进一步、继续、再度”等词只说明当前语句存在延续含义，不能据此任意选择前文 Card 作为基线；原文必须在连接语句中明确指向该 Card 所表达的具体前态，否则不输出 temporal_progression。
- confirmation 必须是针对同一事实命题的独立支持；不同指标、不同统计口径、背景数据或仅方向相近的材料不能互相确认。
- contradiction 必须针对同一主体、对象、谓词、时间和范围下互不相容的结论；不同机构谈论不同命题不是冲突。
- causal_influence 只有原文明示因果、影响或贡献时才成立，且原因发生时间不能晚于已发生的结果；不能把先后顺序、背景事实、共同出现或可能动机自行连接成因果。
- relation_evidence_refs 引用直接证明连接的最小原文集合，可以与两端 Card 的 focus_evidence_refs 重叠；标题、承接句或连接词所在 Ref 也可以成为关系证据。仅把两组互不连接的端点证据拼在一起不构成关系证据。
- 每条 Relation 在输出前必须通过三项证明门槛：relation_evidence_refs 中有文本能唯一定位 source 端点；有文本能唯一定位 target 端点；还有文本直接写明二者的连接。三项可以位于同一 Ref，也可以位于一个最小连续上下文，但缺少任意一项就删除 Relation。
- 暂时隐藏两端 Summary，只阅读 relation_evidence_refs 并分别指出上述 source 定位、target 定位和连接；无需输出这三个中间答案。如果无法仅凭所引原文完成，relations 保持为空。叙述顺序、相邻句、两个数值以及程度或延续副词都不能替代明确的端点绑定。
- temporal_progression 跨年度、季度或月度观察窗口时，关系原文必须明确同时指认前态与后态并直接作出比较或状态迁移判断；仅在后态使用“进一步、继续、再度”等表达仍不满足条件。
- 年度、季度、月度等不同报告窗口中的同比、环比、金额或比例不是同一状态快照；即使叙述使用“进一步、继续”等趋势词，也应合并为趋势 Card，而不是在这些统计 Card 之间输出 temporal_progression。
- 关系连接语句中的 source 与 target 必须分别和两端 Card 表达同一个具体事实，主体、谓词、对象、时间和范围不能被更宽或更窄的概念替换。连接语句只提到未展开的宽泛集合时，不得任选集合中的局部指标或个体作为关系端点。
- 原文用“这些措施、这种策略”等集合指代多个事实时，只有连接语句能够逐项对应的成员才分别建立关系；无法逐项对应时省略关系，不能把集合关系摊派给局部 Card。
- 连接语句指向整体事件时，只能连接上一轮已有的准确 Card；没有对应 Card 时省略关系，不能用局部观测代替。
- basis 用自然语言准确复述原文写明的连接，不出现 Card ID、Ref 标签或模型推测。每对 Card 最多一条关系。

输出前逐条删除需要把独立端点证据拼接后才能成立的 Relation。只要连接指代、统计口径或事实端点存在歧义，就省略，不以数量为目标。不要输出 Cards、Relation Probe、分析过程、主题标签、Community、预测或 Markdown。"""


@dataclass(frozen=True)
class AtomicCardStageResult:
    status: str
    cards: list[AtomicCognitiveCard]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _ValidatedAtomicCardResponse:
    cards: list[AtomicCognitiveCard]
    cards_by_local_id: dict[str, AtomicCognitiveCard]
    skip_reason: str
    discarded_card_count: int = 0
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ValidatedRelationResponse:
    relations: list[VerifiedRelationDecision]
    discarded_relation_count: int = 0
    issues: tuple[str, ...] = ()


def _response_cannot_be_safely_repaired(response: Any) -> bool:
    raw_payload = getattr(response, "raw_payload", None) or {}
    if str(raw_payload.get("finish_reason") or "").strip().lower() == "length":
        return True
    proxy = getattr(response, "proxy", None) or {}
    return bool(proxy.get("json_prefix_continuation_attempted")) and not bool(
        proxy.get("json_prefix_continuation_success")
    )


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
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._segmenter = segmenter or StableSpanSegmenter()
        self._redis: Any | None = None

    async def extract(self, chunks: list[EvidenceChunk]) -> list[AtomicCognitiveCard]:
        results = await self.extract_with_diagnostics(chunks)
        return [card for result in results for card in result.cards]

    async def extract_with_diagnostics(
        self,
        chunks: list[EvidenceChunk],
    ) -> list[AtomicCardExtractionResult]:
        async def run(chunk: EvidenceChunk) -> AtomicCardExtractionResult:
            async with self._semaphore:
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
            sentence_blocks = self._segmenter.segment_blocks(chunk.content)
            spans = [part for block in sentence_blocks for part in block.parts]
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "sentence_block_count": len(sentence_blocks),
                    "span_count": len(spans),
                },
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
        prompt_input = render_atomic_card_prompt_input(
            source_published_at=payload.get("published_at") or "",
            source_title=payload.get("title") or "",
            sentence_blocks=sentence_blocks,
        )
        base_messages = [
            {"role": "system", "content": ATOMIC_CARD_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_input},
        ]
        request = LLMProxyRequest(
            model=self._model,
            messages=base_messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_CARD_SCHEMA,
            provider_options={
                "reasoning_effort": "medium",
                "inject_json_schema_instruction": False,
            },
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
            name="kg.atomic_card.extract_cards",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "text_chars": len(chunk.content),
                "sentence_block_count": len(sentence_blocks),
                "span_count": len(spans),
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            warmup_lock = await self._claim_prefix_warmup_lock()
            try:
                response = await self._llm.generate(request)
                await self._mark_prefix_warmed(settle=warmup_lock is not None)
                validated, repaired, repair_attempted, card_response = (
                    await self._cards_from_response(
                        chunk=chunk,
                        spans=spans,
                        request=request,
                        response=response,
                    )
                )
                relation_result = await self._extract_relations(
                    chunk=chunk,
                    spans=spans,
                    card_request=request,
                    card_response=card_response,
                    cards=validated.cards,
                    cards_by_local_id=validated.cards_by_local_id,
                )
                langfuse_update_span(
                    output={
                        "chunk_id": chunk.chunk_id,
                        "card_count": len(validated.cards),
                        "card_ids": [card.cognitive_card_id for card in validated.cards],
                        "summary_chars": [len(card.summary) for card in validated.cards],
                        "focus_ref_counts": [len(card.focus_evidence_refs) for card in validated.cards],
                        "relation_probe_counts": [
                            len(card.relation_probes) for card in validated.cards
                        ],
                        "relation_probe_roles": [
                            [probe.role for probe in card.relation_probes]
                            for card in validated.cards
                        ],
                        "relation_probes": [
                            {
                                "cognitive_card_id": card.cognitive_card_id,
                                "summary": card.summary,
                                "items": [
                                    probe.as_dict() for probe in card.relation_probes
                                ],
                            }
                            for card in validated.cards
                            if card.relation_probes
                        ],
                        "intra_chunk_relation_count": len(relation_result.relations),
                        "intra_chunk_relation_kinds": [
                            relation.relation_kind for relation in relation_result.relations
                        ],
                        "repaired": repaired,
                        "repair_attempted": repair_attempted,
                        "discarded_card_count": validated.discarded_card_count,
                        "discarded_relation_count": relation_result.discarded_relation_count,
                        "validation_issues": [
                            *validated.issues,
                            *relation_result.issues,
                        ],
                        "skip_reason": validated.skip_reason,
                        "prefix_warmup_owner": warmup_lock is not None,
                    },
                    status_message="completed",
                )
                return AtomicCardExtractionResult(
                    chunk_id=chunk.chunk_id,
                    spans=spans,
                    cards=validated.cards,
                    relations=relation_result.relations,
                    repaired=repaired,
                    repair_attempted=repair_attempted,
                    discarded_card_count=validated.discarded_card_count,
                    discarded_relation_count=relation_result.discarded_relation_count,
                    validation_issues=[*validated.issues, *relation_result.issues],
                    skip_reason=validated.skip_reason,
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
    ) -> tuple[_ValidatedAtomicCardResponse, bool, bool, Any]:
        if _response_cannot_be_safely_repaired(response):
            raise RuntimeError(
                "原子 Cognitive Card 输出在 Prefix Completion 后仍未完成，"
                f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
            )
        issues: list[str] = []
        with langfuse_observation(
            name="kg.atomic_card.validate",
            as_type="span",
            input={"chunk_id": chunk.chunk_id},
        ):
            try:
                validated = self._validate_card_response(
                    chunk,
                    spans,
                    response.structured_output,
                )
                langfuse_update_span(
                    output={
                        "valid": True,
                        "card_count": len(validated.cards),
                        "discarded_card_count": validated.discarded_card_count,
                        "issues": list(validated.issues),
                    },
                    status_message="completed",
                )
                return validated, False, False, response
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
                    "只修复去重后的最终 cards、relation_probes 和 skip_reason；"
                    "修复后重新检查每个 Card 的焦点证据是否完整支撑 Summary，"
                    "缺少支撑时补充已有 Span Ref 或删除对应表述；"
                    "不得新增外部事实，不得恢复旧 topic_intents 或主题标签字段。"
                ),
                retry_reason="atomic_cognitive_card_validation_invalid",
            )
        try:
            validated = self._validate_card_response(
                chunk,
                spans,
                repaired.structured_output,
            )
            return validated, True, True, repaired
        except Exception as exc:
            raise RuntimeError(
                f"原子 Cognitive Card 修复后仍未通过校验: chunk_id={chunk.chunk_id}; "
                f"first_issues={issues}; repair_issue={exc}"
            ) from exc

    @staticmethod
    def _validate_card_response(
        chunk: EvidenceChunk,
        spans: list[Any],
        data: Any,
    ) -> _ValidatedAtomicCardResponse:
        if not isinstance(data, dict):
            raise ValueError(f"顶层输出必须是 JSON object，实际为 {type(data).__name__}")
        expected_top_level = {"cards", "skip_reason"}
        if set(data) != expected_top_level:
            raise ValueError(
                "顶层字段不符合契约: "
                f"missing={sorted(expected_top_level.difference(data))}, "
                f"extra={sorted(set(data).difference(expected_top_level))}"
            )
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("cards 必须是数组")
        issues: list[str] = []
        discarded_card_count = max(0, len(raw_cards) - 12)
        if discarded_card_count:
            issues.append(f"cards 超过 12 项，已丢弃尾部 {discarded_card_count} 项")
        skip_reason = str(data.get("skip_reason") or "").strip()
        if not raw_cards and not skip_reason:
            raise ValueError("cards 为空时必须提供 skip_reason")
        cards: list[AtomicCognitiveCard] = []
        cards_by_local_id: dict[str, AtomicCognitiveCard] = {}
        card_ids: set[str] = set()
        for index, item in enumerate(raw_cards[:12], start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("元素不是对象")
                local_card_id = str(item.get("local_card_id") or "").strip()
                expected_local_id = f"c{index}"
                if local_card_id != expected_local_id:
                    raise ValueError(
                        f"local_card_id 应为 {expected_local_id}，实际为 {local_card_id}"
                    )
                raw_card = {key: value for key, value in item.items() if key != "local_card_id"}
                card = atomic_card_from_llm_item(chunk, raw_card, spans=spans)
                if card.cognitive_card_id in card_ids:
                    raise ValueError("与前序 Card 身份重复")
            except Exception as exc:
                discarded_card_count += 1
                issues.append(f"card[{index}] 已丢弃: {exc}")
                continue
            cards.append(card)
            card_ids.add(card.cognitive_card_id)
            cards_by_local_id[local_card_id] = card
        if raw_cards and not cards:
            raise ValueError("所有 Card 均未通过基础契约校验")

        return _ValidatedAtomicCardResponse(
            cards=cards,
            cards_by_local_id=cards_by_local_id,
            skip_reason=skip_reason,
            discarded_card_count=discarded_card_count,
            issues=tuple(issues),
        )

    async def _extract_relations(
        self,
        *,
        chunk: EvidenceChunk,
        spans: list[Any],
        card_request: LLMProxyRequest,
        card_response: Any,
        cards: list[AtomicCognitiveCard],
        cards_by_local_id: dict[str, AtomicCognitiveCard],
    ) -> _ValidatedRelationResponse:
        if len(cards_by_local_id) <= 1:
            return _ValidatedRelationResponse(relations=[])

        assistant_content = str(getattr(card_response, "text", "") or "").strip()
        if not assistant_content:
            assistant_content = json.dumps(
                getattr(card_response, "structured_output", None) or {},
                ensure_ascii=False,
            )
        relation_messages = [
            *card_request.messages,
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT},
        ]
        relation_request = LLMProxyRequest(
            model=self._model,
            messages=relation_messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_RELATION_SCHEMA,
            provider_options={
                "reasoning_effort": "medium",
                "inject_json_schema_instruction": False,
            },
            metadata={
                "task": "kg_cognitive_card_relation",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "chunk_id": chunk.chunk_id,
                "card_count": len(cards),
                "_cache_key_metadata": {
                    "task": "kg_cognitive_card_relation",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                },
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.atomic_card.extract_relations",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "card_count": len(cards),
                "continued_message_count": len(relation_messages),
            },
        ):
            response = await self._llm.generate(relation_request)
            if _response_cannot_be_safely_repaired(response):
                raise RuntimeError(
                    "同 Chunk Relation 输出在 Prefix Completion 后仍未完成，"
                    f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
                )
            validated = self._validate_relation_response(
                chunk,
                spans,
                cards_by_local_id,
                response.structured_output,
            )
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "relation_count": len(validated.relations),
                    "relation_kinds": [
                        relation.relation_kind for relation in validated.relations
                    ],
                    "discarded_relation_count": validated.discarded_relation_count,
                    "issues": list(validated.issues),
                },
                status_message="completed",
            )
            return validated

    @staticmethod
    def _validate_relation_response(
        chunk: EvidenceChunk,
        spans: list[Any],
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        data: Any,
    ) -> _ValidatedRelationResponse:
        if not isinstance(data, dict):
            raise ValueError(f"Relation 顶层输出必须是 JSON object，实际为 {type(data).__name__}")
        if set(data) != {"relations"}:
            raise ValueError(
                "Relation 顶层字段不符合契约: "
                f"missing={sorted({'relations'}.difference(data))}, "
                f"extra={sorted(set(data).difference({'relations'}))}"
            )
        issues: list[str] = []
        raw_relations = data.get("relations")
        if not isinstance(raw_relations, list):
            issues.append("relations 不是数组，已丢弃全部关系")
            raw_relations = []
            discarded_relation_count = 1
        else:
            discarded_relation_count = max(0, len(raw_relations) - 66)
            if discarded_relation_count:
                issues.append(
                    f"relations 超过 66 项，已丢弃尾部 {discarded_relation_count} 项"
                )
        relations: list[VerifiedRelationDecision] = []
        seen_pairs: set[tuple[str, str]] = set()
        for index, item in enumerate(raw_relations[:66], start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("元素不是对象")
                local_pair = tuple(
                    sorted(
                        (
                            str(item.get("source_card_id") or "").strip(),
                            str(item.get("target_card_id") or "").strip(),
                        )
                    )
                )
                if local_pair in seen_pairs:
                    raise ValueError(f"同一 Card 对重复输出: {local_pair}")
                relation = intra_chunk_relation_from_llm_item(
                    item,
                    chunk=chunk,
                    spans=spans,
                    cards_by_local_id=cards_by_local_id,
                )
            except Exception as exc:
                discarded_relation_count += 1
                issues.append(f"relation[{index}] 已丢弃: {exc}")
                continue
            seen_pairs.add(local_pair)
            relations.append(relation)
        return _ValidatedRelationResponse(
            relations=relations,
            discarded_relation_count=discarded_relation_count,
            issues=tuple(issues),
        )

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
        relation_probe_count = sum(
            len(card.relation_probes)
            for result in extraction_results
            for card in result.cards
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
            "repair_attempted_chunks": sum(
                1 for result in extraction_results if result.repair_attempted
            ),
            "discarded_cards": sum(
                result.discarded_card_count for result in extraction_results
            ),
            "discarded_relations": sum(
                result.discarded_relation_count for result in extraction_results
            ),
            "validation_issues": [
                {"chunk_id": result.chunk_id, "issues": list(result.validation_issues)}
                for result in extraction_results
                if result.validation_issues
            ],
            "zero_card_reasons": [
                {"chunk_id": result.chunk_id, "reason": result.skip_reason}
                for result in extraction_results
                if not result.cards and result.skip_reason
            ],
            "cards": card_count,
            "relation_probes": relation_probe_count,
            "cards_without_relation_probes": sum(
                not card.relation_probes
                for result in extraction_results
                for card in result.cards
            ),
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
