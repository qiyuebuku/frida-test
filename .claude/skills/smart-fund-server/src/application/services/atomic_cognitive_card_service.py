"""原子 Cognitive Card 的提取、校验和发布服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace
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
    relation_probes_from_llm_items,
    render_atomic_card_prompt_input,
)
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.relation_discovery import RelationProbe, VerifiedRelationDecision
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

ATOMIC_CARD_MAX_TOKENS = 20000
ATOMIC_CARD_PREFIX_WARM_MARK_KEY = (
    f"{JETTASK_PREFIX}:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmed"
)
ATOMIC_CARD_PREFIX_WARM_LOCK_KEY = (
    f"{JETTASK_PREFIX}:lock:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmup"
)
ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS = 0.05
ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION = "atomic_card_intra_chunk_relation_v1"
ATOMIC_RELATION_PROBE_GENERATOR_VERSION = "atomic_relation_probe_v11"


ATOMIC_CARD_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "local_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "focus_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
    },
    "required": [
        "local_card_id",
        "summary",
        "focus_evidence_refs",
    ],
    "additionalProperties": False,
}


ATOMIC_RELATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "target_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
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
            "items": ATOMIC_CARD_ITEM_SCHEMA,
        },
        "relations": {
            "type": "array",
            "items": ATOMIC_RELATION_ITEM_SCHEMA,
        },
        "skip_reason": {"type": ["string", "null"], "maxLength": 240},
    },
    "required": ["cards", "relations", "skip_reason"],
    "additionalProperties": False,
}


ATOMIC_RELATION_PROBE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "local_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "relation_probes": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": [
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
    "required": ["local_card_id", "relation_probes"],
    "additionalProperties": False,
}


ATOMIC_RELATION_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "probe_plans": {
            "type": "array",
            "items": ATOMIC_RELATION_PROBE_ITEM_SCHEMA,
        }
    },
    "required": ["probe_plans"],
    "additionalProperties": False,
}


_ATOMIC_CARD_STAGE_SYSTEM_PROMPT = """你是知识图谱的原子 Cognitive Card 与同 Chunk Relation 抽取器。

首次收到新闻原文时只执行本 Card 阶段。输入首行是新闻发布时间；其后每行是一条完整原文语句，`<title>` 表示标题，`[sNNNN]` 是紧随文本的证据坐标。同一行连续 Ref 的文本按顺序拼接阅读。后续用户可能在同一对话中继续追问 Relation Probe；在收到明确的阶段切换指令前，不得思考或规划 Probe。

只沿原文顺序扫描一次生成最终 Cards，再扫描最终 Cards 一次生成 Relations。不要在思考中抄写全文、建立多版事实清单、反复重编号或逐对枚举无关系组合。

1. 形成最终 Card。沿正文 Ref 顺序识别每个可独立判断真假的事实元组：主体、核心谓词、对象或状态、时间或范围，并直接放入最终 Card 序列。保留原文中的消息来源、声明者、预测者、认定者以及可能、预计、据称等不确定性。纯广告、来源署名、无独立命题的修辞不建卡；定性表达只要明确断言了独立主体、谓词和状态，就不是可被数值自动替代的“总述”。
2. 确定 Card 边界。每张 Card 只表达一个最小但完整的事实元组，并且脱离其他 Card 仍能读懂。核心谓词必须是原文中可以被单独查询、更新或证伪的具体动作、状态或测量项；不得为了合并而创造一个上位集合谓词。只有主体、核心谓词、对象、时间范围和口径相同，且一项只是另一项的复述或数值限定时，才允许合并或去重。不同核心谓词、不同指标名称或不同事件动作必须分别建卡，即使它们来自同一句、同一张表、同一次公告或共同描述一种局面。删除候选前，必须确认其主体、谓词、对象、时间、范围和数值均能从保留 Card 中完整恢复，否则不构成严格蕴含。若两个命题可以分别为真或为假，就必须拆分；输出长度、Card 数量和叙事完整性不得参与边界判断，边界不确定时优先拆分。
3. 保留事件链端点。原因、前置条件、执行动作、监管处置、市场反应、结果状态和后续措施只要能独立判断，就分别建卡，再通过 Relation 表达连接；不得把连接语义藏进一张 Summary。原文若在一个句子中表达两个事实之间的因果、依据、回应、约束、确认、冲突或进展，也必须先拆成两张端点 Card，再输出 Relation。Summary 只能陈述一个端点，不能同时陈述该端点及其原因、后果或后续步骤。程序中的提议、回应、表决、执行、撤销、辞任等步骤同理。一张 Summary 如果需要两个完整主谓结构才能成立，就继续拆分。数值、同比或环比只在共同限定同一个指标谓词时保留在一张 Card；不同指标、不同状态、不同动作或不同风险结论不能用集合表达合并。
4. 绑定证据。Summary 必须写出原文可确定的完整主体和事实，不使用依赖上下文的代词或泛称。focus_evidence_refs 是能够独立验证该 Summary 的最小完整证据闭包，数组元素只写 `sNNNN`。标题只用于补足正文省略的信息；正文已完整表达时不要重复引用标题。published_at 只用于理解相对时间，不能补造原文没有的年份或事实。
5. 映射同 Chunk Relation。Cards 完整冻结后，只扫描原文中明确表达的因果、依据、回应、约束、确认、冲突和事件进展连接，再把连接两端映射到已有 Card；不要枚举没有连接证据的 Card 组合，也不能为了生成关系修改 Card。

Relation 规则：
- 只允许 confirmation、contradiction、temporal_progression、causal_influence、common_driver、constraint。只输出原文能够直接证明的正关系，不依赖外部常识补充中间机制。
- temporal_progression 可以表示同一事件、程序或状态的后续、回应、推进、执行或结果；两端主体和动作可以不同，但后项必须明确引用、回应、执行、改变或结束前项所涉及的同一事件对象。仅仅并列列出多个动作或结果，即使位于同一句、文档相邻或时间先后，也不是 progression；“随后”“最终”等词只连接其语法上实际承接的步骤，不能向后扩展到独立句子。对于数值或指标变化，原文必须明确比较或更新同一主体、同一指标和同一口径，不能仅凭两个报告期自行制造趋势。
- causal_influence 必须由原文表达原因、依据、触发或影响，原因时间不能晚于已经发生的结果。confirmation 针对同一事实命题的独立支持；contradiction 针对同一主体、对象、谓词、时间和范围下互不相容的结论；common_driver 和 constraint 也必须直接指向准确端点。
- relation_evidence_refs 必须是同时能够定位 source、定位 target 并证明连接的最小原文集合。只拼接两端各自证据、只有时间相邻、并列列举或只有主题相关都不构成关系。basis 不能自行添加原文没有表达的先后、因果、回应或影响语义；如果只有在 basis 中补入这类连接词才能让两端产生关系，就必须删除该 Relation。原文把多个事实共同概括为一个结论时，不能把该集合关系任意归给其中一个成员；当前 pairwise Relation 无法表达这种成组关系时直接不输出。basis 用自然语言复述原文连接，不出现 Card ID、Ref 或模型推测。每对 Card 最多一条关系。

最终只做一次核对：事实清单中的每个独立命题都已进入 Card，或确实是重复/非事实内容；每张 Card 只有一个核心谓词；任意被删除候选都被另一张 Card 严格蕴含；每条 Relation 的证据同时覆盖 source、target 和连接。不要输出核对过程。

Card 阶段 JSON 输出契约：只输出一个 JSON 对象，并严格按 `cards`、`relations`、`skip_reason` 顺序输出。每张 Card 严格且仅包含 `local_card_id`、`summary`、`focus_evidence_refs`；每条 Relation 严格且仅包含 `source_card_id`、`target_card_id`、`relation_kind`、`basis`、`relation_evidence_refs`。先完成全部 Card 的拆分、去重和排序，再废弃内部草稿编号，严格按最终 cards 数组位置重新编号为连续且唯一的 c1、c2、c3；禁止字母后缀、跳号或保留拆分前编号。cards 非空时 skip_reason 必须为字符串空值 `""`；cards 为空时填写具体原因且 relations 必须为空。不要输出 null、额外字段、分析过程、主题标签、Community、预测或 Markdown。"""


_ATOMIC_RELATION_PROBE_SYSTEM_SECTION = """Relation Probe 阶段规则：

只有后续 user 明确要求“进入 Relation Probe 阶段”时才执行本节。首次收到新闻原文时严禁思考、规划或输出 Probe，本节规则不得影响 Card 边界和同 Chunk Relation。

进入 Probe 阶段后，冻结上一轮 Cards 和 Relations。为每张 Card 判断是否存在能够搜索其他 Chunk 历史 Card、并与当前事实形成一跳直接关系的候选事件方向。不得修改 Card，不得新增同 Chunk Relation，不得把兄弟 Card 的事实混入当前 Card 的 query。

按以下门槛一次完成判断：
1. 当前 Card 的 Summary 已经用于基础语义召回，同一事实、同一事件进展、复述或近义改写不需要 Probe。
2. Probe 必须指向 Summary 中尚不存在的另一个可观察事件端点，并锚定当前 Card 的已知主体、动作或状态和作用对象。query 可以是简洁的关系型检索问题或候选事件描述，但不能是关键词列表、宽泛研究主题或检索指令。另一端信息未知时直接询问其原因、结果、独立观测或冲突事实，不得补写原文没有提供的候选主体、事件类别、作用机制和既成结果。
3. 如果 query 与 Summary 仍是相同主体、核心谓词、对象和时间范围，只增加“其他来源确认”“后续情况”“最新进展”等包装，就没有召回增量，必须删除。confirmation 只有在搜索不同观察材料或不同指标、且该材料能够独立支持当前命题时才保留；不能搜索同一数值或同一陈述的再次发布。
4. upstream 寻找直接原因、前置动作或约束；downstream 寻找截至 published_at 已经可能发生、并由当前事实直接触发的可观察结果；confirmation 和 contradiction 必须针对同一命题形成独立支持或不相容证据。只允许一跳，不补造日期、数值、专有主体、动机、中间机制或未来结论，也不能机械否定当前谓词。query 必须保持原文的事实强度和不确定性，不能把差错、嫌疑、风险或可能性升级成已经成立的违法、造假、处罚或结果。
5. 先找出当前事实仍缺失的直接原因、前置约束、已发生结果、独立观测或冲突命题，再判断是否值得搜索。只有缺失端点会显著改变当前事实的解释，并且该端点可能作为独立新闻事件存在时才生成 Probe；不能仅因某种后续在现实中可能发生，就把它列为搜索方向。兄弟 Card 或已确认 Relation 只排除与其事实元组相同的那个目标端点，不能据此删除其他尚未出现的端点。当前 Chunk 已经完整给出某个端点时不重复生成。零 Probe 是正常结果，不按 role 凑数；每张 Card 最多保留三条真正不同且增量最高的方向。

输出前只检查四件事：query 单独可识别候选事件；当前 Card 能解释搜索原因；query 与 Summary 的核心谓词不同；二者无需中间事件即可形成一跳关系。任一条件不成立就删除。

JSON 输出契约：只输出一个 JSON 对象，顶层严格且仅包含 `probe_plans`。每个输入 Card 必须按原顺序恰好输出一项，严格且仅包含 `local_card_id`、`relation_probes`；没有有效 Probe 时输出空数组。每条 Probe 严格且仅包含 `role`、`query`，role 只允许 upstream、downstream、confirmation、contradiction。不要输出 same_event、Cards、Relations、分析过程、额外字段或 Markdown。"""


ATOMIC_CARD_SYSTEM_PROMPT = "\n\n".join(
    [
        _ATOMIC_CARD_STAGE_SYSTEM_PROMPT,
        _ATOMIC_RELATION_PROBE_SYSTEM_SECTION,
    ]
)


ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT = """现在进入 Relation Probe 阶段。严格使用 System Prompt 中的 Relation Probe 阶段规则，冻结上一轮 Cards 和 Relations，只输出 probe_plans JSON。"""


@dataclass(frozen=True)
class AtomicCardStageResult:
    status: str
    cards: list[AtomicCognitiveCard]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _ValidatedAtomicCardResponse:
    cards: list[AtomicCognitiveCard]
    cards_by_local_id: dict[str, AtomicCognitiveCard]
    relations: list[VerifiedRelationDecision]
    skip_reason: str
    discarded_card_count: int = 0
    discarded_relation_count: int = 0
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ValidatedProbeResponse:
    probes_by_local_id: dict[str, list[RelationProbe]]
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
    """先提取 Card 与同 Chunk 关系，再在同一对话中续问跨 Chunk Probe。"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        provider: str | None = None,
        concurrency: int = 4,
        segmenter: StableSpanSegmenter | None = None,
    ) -> None:
        self._llm = llm or get_llm_gateway_service()
        self._model = model or resolve_kg_llm_model("kg_cognitive_card")
        self._provider = str(provider or "").strip() or None
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
            provider=self._provider,
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
            name="kg.atomic_card.extract_cards_and_relations",
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
                validated, repaired, repair_attempted, accepted_card_response = (
                    await self._cards_from_response(
                        chunk=chunk,
                        spans=spans,
                        request=request,
                        response=response,
                    )
                )
                cards_with_probes, probe_repaired, probe_repair_attempted, probe_issues = (
                    await self._plan_relation_probes(
                        chunk=chunk,
                        cards_by_local_id=validated.cards_by_local_id,
                        relation_count=len(validated.relations),
                        base_messages=base_messages,
                        card_response=accepted_card_response,
                    )
                )
                repaired = repaired or probe_repaired
                repair_attempted = repair_attempted or probe_repair_attempted
                all_issues = [*validated.issues, *probe_issues]
                langfuse_update_span(
                    output={
                        "chunk_id": chunk.chunk_id,
                        "card_count": len(cards_with_probes),
                        "card_ids": [card.cognitive_card_id for card in cards_with_probes],
                        "summary_chars": [len(card.summary) for card in cards_with_probes],
                        "focus_ref_counts": [len(card.focus_evidence_refs) for card in cards_with_probes],
                        "relation_probe_counts": [
                            len(card.relation_probes) for card in cards_with_probes
                        ],
                        "relation_probe_roles": [
                            [probe.role for probe in card.relation_probes]
                            for card in cards_with_probes
                        ],
                        "relation_probes": [
                            {
                                "cognitive_card_id": card.cognitive_card_id,
                                "summary": card.summary,
                                "items": [
                                    probe.as_dict() for probe in card.relation_probes
                                ],
                            }
                            for card in cards_with_probes
                            if card.relation_probes
                        ],
                        "intra_chunk_relation_count": len(validated.relations),
                        "intra_chunk_relation_kinds": [
                            relation.relation_kind for relation in validated.relations
                        ],
                        "repaired": repaired,
                        "repair_attempted": repair_attempted,
                        "discarded_card_count": validated.discarded_card_count,
                        "discarded_relation_count": validated.discarded_relation_count,
                        "validation_issues": all_issues,
                        "skip_reason": validated.skip_reason,
                        "prefix_warmup_owner": warmup_lock is not None,
                    },
                    status_message="completed",
                )
                return AtomicCardExtractionResult(
                    chunk_id=chunk.chunk_id,
                    spans=spans,
                    cards=cards_with_probes,
                    relations=validated.relations,
                    repaired=repaired,
                    repair_attempted=repair_attempted,
                    discarded_card_count=validated.discarded_card_count,
                    discarded_relation_count=validated.discarded_relation_count,
                    validation_issues=all_issues,
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
                    "先修复去重后的最终 cards，再修复只引用这些 Card 的 relations；"
                    "完成 Card 拆分后废弃所有草稿编号，严格按最终 cards 数组顺序"
                    "重新编号为 c1、c2、c3，禁止字母后缀、跳号或保留拆分前编号；"
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
        issues: list[str] = []
        skip_reason = str(data.get("skip_reason") or "").strip()
        if not raw_cards and not skip_reason:
            raise ValueError("cards 为空时必须提供 skip_reason")
        cards: list[AtomicCognitiveCard] = []
        cards_by_local_id: dict[str, AtomicCognitiveCard] = {}
        card_ids: set[str] = set()
        for index, item in enumerate(raw_cards, start=1):
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
                raise ValueError(f"card[{index}] 不符合基础契约: {exc}") from exc
            cards.append(card)
            card_ids.add(card.cognitive_card_id)
            cards_by_local_id[local_card_id] = card

        raw_relations = data.get("relations")
        if not isinstance(raw_relations, list):
            issues.append("relations 不是数组，已丢弃全部关系")
            raw_relations = []
            discarded_relation_count = 1
        else:
            discarded_relation_count = 0
        relations: list[VerifiedRelationDecision] = []
        seen_pairs: set[tuple[str, str]] = set()
        for index, item in enumerate(raw_relations, start=1):
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
        return _ValidatedAtomicCardResponse(
            cards=cards,
            cards_by_local_id=cards_by_local_id,
            relations=relations,
            skip_reason=skip_reason,
            discarded_card_count=0,
            discarded_relation_count=discarded_relation_count,
            issues=tuple(issues),
        )

    async def _plan_relation_probes(
        self,
        *,
        chunk: EvidenceChunk,
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        relation_count: int,
        base_messages: list[dict[str, Any]],
        card_response: Any,
    ) -> tuple[list[AtomicCognitiveCard], bool, bool, tuple[str, ...]]:
        if not cards_by_local_id:
            return [], False, False, ()

        assistant_content = str(getattr(card_response, "text", "") or "").strip()
        if not assistant_content:
            assistant_content = json.dumps(
                getattr(card_response, "structured_output", None),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        conversation_messages = [
            *base_messages,
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT},
        ]
        request = LLMProxyRequest(
            model=self._model,
            provider=self._provider,
            messages=conversation_messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_RELATION_PROBE_SCHEMA,
            provider_options={
                "reasoning_effort": "medium",
                "inject_json_schema_instruction": False,
            },
            metadata={
                "task": "kg_relation_probe",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_RELATION_PROBE_GENERATOR_VERSION,
                "card_generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "chunk_id": chunk.chunk_id,
                "source_type": str((chunk.payload or {}).get("source_type") or ""),
                "source_id": str((chunk.payload or {}).get("source_id") or ""),
                "card_count": len(cards_by_local_id),
                "relation_count": relation_count,
                "_cache_key_metadata": {
                    "task": "kg_relation_probe",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_RELATION_PROBE_GENERATOR_VERSION,
                },
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.atomic_card.plan_relation_probes",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "card_count": len(cards_by_local_id),
                "relation_count": relation_count,
                "continuation_mode": "multi_turn_follow_up",
                "history_message_count": len(base_messages) + 1,
            },
        ):
            response = await self._llm.generate(request)
            validated, repaired, repair_attempted = await self._probes_from_response(
                chunk=chunk,
                cards_by_local_id=cards_by_local_id,
                request=request,
                response=response,
            )
            cards = [
                replace(
                    card,
                    relation_probes=list(validated.probes_by_local_id[local_id]),
                )
                for local_id, card in cards_by_local_id.items()
            ]
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "probe_count": sum(
                        len(items) for items in validated.probes_by_local_id.values()
                    ),
                    "cards_without_probes": sum(
                        not items for items in validated.probes_by_local_id.values()
                    ),
                    "repaired": repaired,
                    "repair_attempted": repair_attempted,
                    "issues": list(validated.issues),
                },
                status_message="completed",
            )
            return cards, repaired, repair_attempted, validated.issues

    async def _probes_from_response(
        self,
        *,
        chunk: EvidenceChunk,
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        request: LLMProxyRequest,
        response: Any,
    ) -> tuple[_ValidatedProbeResponse, bool, bool]:
        if _response_cannot_be_safely_repaired(response):
            raise RuntimeError(
                "Relation Probe 输出在 Prefix Completion 后仍未完成，"
                f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
            )
        try:
            return (
                self._validate_probe_response(cards_by_local_id, response.structured_output),
                False,
                False,
            )
        except Exception as exc:
            issues = [str(exc)]

        repaired = await self._llm.repair_with_feedback(
            request,
            response,
            issues,
            instruction=(
                "上一轮 Relation Probe 输出未通过契约校验。"
                "只修复 probe_plans 的 JSON 结构、Card 覆盖和 Probe 字段；"
                "必须按输入顺序为每个 local_card_id 恰好输出一项，"
                "不得修改 Card，不得新增事实或同 Chunk Relation。"
            ),
            retry_reason="atomic_relation_probe_validation_invalid",
        )
        try:
            return (
                self._validate_probe_response(cards_by_local_id, repaired.structured_output),
                True,
                True,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Relation Probe 修复后仍未通过校验: chunk_id={chunk.chunk_id}; "
                f"first_issues={issues}; repair_issue={exc}"
            ) from exc

    @staticmethod
    def _validate_probe_response(
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        data: Any,
    ) -> _ValidatedProbeResponse:
        if not isinstance(data, dict):
            raise ValueError(
                f"Relation Probe 顶层输出必须是 JSON object，实际为 {type(data).__name__}"
            )
        if set(data) != {"probe_plans"}:
            raise ValueError(
                "Relation Probe 顶层字段不符合契约: "
                f"missing={sorted({'probe_plans'}.difference(data))}, "
                f"extra={sorted(set(data).difference({'probe_plans'}))}"
            )
        raw_plans = data.get("probe_plans")
        if not isinstance(raw_plans, list):
            raise ValueError("probe_plans 必须是数组")

        expected_ids = list(cards_by_local_id)
        actual_ids: list[str] = []
        probes_by_local_id: dict[str, list[RelationProbe]] = {}
        for index, item in enumerate(raw_plans, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"probe_plan[{index}] 必须是对象")
            if set(item) != {"local_card_id", "relation_probes"}:
                raise ValueError(f"probe_plan[{index}] 字段不符合契约")
            local_card_id = str(item.get("local_card_id") or "").strip()
            actual_ids.append(local_card_id)
            probes_by_local_id[local_card_id] = relation_probes_from_llm_items(
                item.get("relation_probes")
            )
        if actual_ids != expected_ids:
            raise ValueError(
                "probe_plans 必须按输入顺序完整覆盖 Cards: "
                f"expected={expected_ids}, actual={actual_ids}"
            )
        return _ValidatedProbeResponse(probes_by_local_id=probes_by_local_id)

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
