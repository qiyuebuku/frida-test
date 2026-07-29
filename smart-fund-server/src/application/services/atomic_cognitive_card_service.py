"""原子 Cognitive Card 的提取、校验和发布服务。"""

from __future__ import annotations

import asyncio
import hashlib
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
ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION = "atomic_card_intra_chunk_relation_v14"
ATOMIC_RELATION_PROBE_GENERATOR_VERSION = "atomic_relation_probe_v29"


ATOMIC_CARD_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "local_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": (
                "可独立检索的完整事实陈述；不得保留无法脱离原文定位的"
                "代词、泛化事件名称或其他回指，必须改写为原文中唯一对应的具体前件。"
            ),
        },
        "focus_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "description": "包含独立证明 summary 及其回指对象所需的最小 Ref 集合。",
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
        "relation_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
        "source_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "target_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "decision_class": {
            "type": "string",
            "enum": ["observed", "inferred"],
        },
        "relation_kind": {
            "type": "string",
            "enum": sorted(INTRA_CHUNK_RELATION_KINDS),
        },
    },
    "required": [
        "relation_evidence_refs",
        "source_card_id",
        "target_card_id",
        "decision_class",
        "relation_kind",
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
        "chunk_summary": {"type": "string", "minLength": 1, "maxLength": 1200},
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
            "minItems": 1,
            "maxItems": 2,
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

输入首行是新闻发布时间；其后按原文句子换行，`<title>` 是标题，`[sNNNN]` 是证据坐标。一个 Ref 可能包含多个事实，只是引用地址，不代表 Card 边界，也不是需要逐项分类的清单。仅在 user 明确切换阶段后执行 Relation Probe。

Card：
1. Card summary 是后续跨 Chunk 关系判断能够看到的唯一正文，判断器看不到同 Chunk 的其他 Card 或原文。先消解指代再写 summary：任何代词、泛化事件名称或省略成分，只要无法仅凭本 summary 唯一识别现实主体和事件类型，就必须替换为原文中唯一对应的具体前件。状态更新按“前件的具体主体 + 前件的具体动作或状态对象 + 当前新状态”重写；focus_evidence_refs 同时引用前件和当前更新，前件 Ref 不是冗余证据。无法唯一恢复时不建 Card，禁止照抄仍需回看前文才能理解的原句。
2. 原文直接支持、能独立判断真假且对关系发现有意义的现实事件或事实命题各生成一张 Card，保留主体、核心动作/状态/指标、对象、时间和必要口径；修饰与无关细节不建卡。状态恢复、变化、终止等更新必须明确复述被更新的具体主体及状态对象。
3. Card 边界按现实事件划分，不按主体数量或列举项机械拆分。同一报道把板块、指数或行业整体涨跌与同向成员清单描述为一次市场表现时，只生成一张集合 Card；极值、不同涨跌幅及跟涨跟跌只是成员属性，即使可分别核验也不拆卡。
4. 集合规则只适用于共同描述同一观测状态的成员清单，不适用于原因、条件、措施、预测或传导链。成员具有独立触发原因、公司行为、时间阶段、立场、相反方向或其他关系角色时必须拆卡；关系连接只进 Relation。
5. 标题、导语、正文和结论重复描述同一事件时，只留证据最完整的一张并合并必要 Ref。整体状态及其成员表现若只是同一事件的概括与实例，不得同时生成整体 Card 和成员 Card；不得重复生成总述版与明细版。
6. 已发生事实和当前状态直接陈述。原文明示的计划、预测、风险或条件判断只有构成独立主张时才建 Card，summary 必须保留声明者及原文不确定强度，不能写成已发生事实。若一句分析的全部信息只是把已经建卡的事实连接起来，它只生成 Relation，不再生成“关系结论 Card”；完全由具体 Card 推导出的宽泛评价也不建卡。广告、署名、重复标题、无声明者的作者归纳和宽泛评价不建卡。
7. focus_evidence_refs 只保留能够独立证明 summary 的最小 `sNNNN` 集合。标题只补足正文省略内容；published_at 只解释相对时间，不补造年份或事实。

Relation：
1. Cards 确定后只映射原文明示的不同事实端点，Relation 提供 Card 未含的连接事实。两端落在同一 Card 时先拆卡；source_card_id 与 target_card_id 不得相同。
2. 文本相邻、普通先后、共同主题和模型补全机制不建边。
3. 重复、并列、概括与明细、总量与分项不建边。common_driver 的 source 必须是独立于结果的外部事实；板块、指数或行业整体涨跌只是成员表现的汇总，不是驱动事实。同一底层事实应在 Card 阶段去重。
4. relation_kind 选择原文最窄语义：confirmation/contradiction 要求两张 Card 对同一个可判真假的命题形成支持或冲突；命题对象不同的声明不能算冲突，报道后出现回应也不能自动算因果。temporal_progression 要求同一事件的后续事实更新前一状态，不能连接事实与宽泛评价，也不能仅连接两个预测时点；causal_influence 要求原文明确把 source 写成 target 的原因、影响或传导因素；common_driver 仅连接由原文同一个独立因素共同驱动的两个结果，若驱动因素本身已建 Card，应改为从驱动 Card 指向各结果的 causal_influence；constraint 要求 source 明确限制 target 的成立范围、实现条件或作用强度；market_co_movement 只连接原文分别明确描述的同一市场对象或直接资产暴露在不同市场载体上的同步或背离表现，它是对称关系，不表示因果、传导或共同驱动。报道动作、回应动作、章节过渡和叙述顺序本身都不是关系；无法归入这些语义就不建边。
5. 只有两端均为已发生事实或当前状态、且连接也是原文陈述的现实关系时才是 observed；计划、预测、条件和分析性传导统一为 inferred。inferred 只表示原文明示关系自身带有分析或不确定强度，不允许用来补造原文没有的连接。不得把时间顺序、并列状态或后一个事实默认写成因果或进展。
6. relation_evidence_refs 只引用原文中直接陈述两端连接的最小连续上下文，不能只分别证明两端存在。先确定证据，再映射 source/target Card 和关系分类；每对 Card 最多一条。若引用内容不能让读者直接判断两张 Card 为何相连，就删除 Relation。
7. 多个原因、条件或支撑因素共同指向一个结果时，各因素只分别连接该结果，因素之间不建边；多个限制因素共同约束一个判断时也同理。原文列出 A、B、C 并称它们共同支撑 D，只能输出 A→D、B→D、C→D，不能输出 A→B、A→C 或 B→C。
8. 新闻把某项消息列为市场表现的背景、分析师同时介绍公司优势或文章把两个事实并列，不等于原文明示因果；这类关系不得仅凭标题、段落位置或投资观点补全。
9. Relations 不承担把所有 Cards 连成图的任务；多 Card 文档完全可以 relations 为空。同一句宽泛叙事或段落总结不能复制成多条 pair 关系，除非原文分别明确陈述它与每一对端点的连接。

输出前自检：
- 模拟跨 Chunk 判断器，逐张只读 summary 且不查看当前 Chunk、其他 Card 或 chunk_summary。若无法确定该事实发生在哪个具体主体、系统或业务对象上，或者无法知道被更新的是哪一种具体动作或状态，该 summary 无效：按“具体主体 + 具体动作或状态对象 + 当前状态”重写并补齐前件及当前证据 Ref，否则删除该 Card。
- Card 若仍含两个可独立进入关系链的端点及连接，改为两个 Card 和 Relation；若原文只有一次市场共同表现，cards 只能有一张且 relations 为空。
- `temporal_progression` 只连接同一事件在不同时间的状态变化；“第一/第二/第三”式并列因素不是进展。共同驱动因素已单独成 Card 时，从驱动 Card 分别连向结果，不在结果之间另建 common_driver。
- observed 不得包含预测、条件或作者分析。逐条只阅读 relation_evidence_refs：如果看不到两端及连接，删除 Relation。
- 只有生成两张及以上 Card 时才生成 chunk_summary；它只能中性汇总 Cards 已表达的事实，不得新增连接，不得把 inferred 关系改写为确定因果。只有零张或一张 Card 时完全省略 chunk_summary，单张 Card 的 summary 已承担同一职责。

JSON 输出：
- 顶层按顺序先输出 `cards`、`relations`、`skip_reason`。只有生成两张及以上 Card 时，最后再输出 `chunk_summary`，用一到三句话概括 Cards 覆盖范围；零张或一张 Card 时不得输出该字段。
- Card 仅含 `local_card_id`、`summary`、`focus_evidence_refs`；按原文顺序编号 c1、c2、c3，不得使用字母后缀或跳号。
- Relation 按顺序仅含 `relation_evidence_refs`、`source_card_id`、`target_card_id`、`decision_class`、`relation_kind`；basis 由程序按引用原文回填。
- cards 非空时 skip_reason 为 `""`；否则说明原因且 relations 为空。只输出 JSON。"""


_ATOMIC_RELATION_PROBE_SYSTEM_SECTION = """Relation Probe 阶段：

仅在 user 明确切换阶段后执行，冻结上一轮 Cards 和 Relations。Probe 寻找当前材料未回答、但其他 Chunk 历史 Card 可能补充的一跳端点，用于补全关键原因、阶段、影响、独立印证或反证。
1. Summary 已承担同义召回。先检查 Cards、Relations 和 chunk_summary；已回答的目标不再检索。upstream 寻找重大行动、监管处置或状态变化缺失的独立触发事件；downstream 只找截至 published_at 已可能发生的后续行动或结果；confirmation/contradiction 寻找独立来源对同一命题的支持或反证。
2. 候选事件已在当前 Chunk 出现时必须删除，换近义词或粒度不能绕过。计划、预测及其未来兑现结果也不检索；未来新 Card 应以 upstream 反向连接当前 Card。
3. 目标必须能独立建 Card 并补上关系图缺失的一跳。例行数值、背景、概念说明、细粒度补充和完整综述已覆盖的子事实不生成；但缺失的重大监管触发事实、被引用主张的独立证据或已发生处置结果可以生成。
4. 每个事件最多一个 Probe，不按公司、地区或分项重复。query 必须是可独立检索的肯定式事件描述，包含缺失端点的主体、动作或状态；不得写成问题、未知槽位名称或“寻找原因/触发事件/后续结果”式任务说明。无法在不补造事实的前提下写出具体端点时不生成。

删除复述当前 Card、已回答端点和 published_at 之后才可能发生的事件；没有合格项时输出空数组。

JSON 输出：顶层为 `probe_plans`，只列 Probe 非空的 Card；没有则写 `[]`。每项含 `local_card_id`、`relation_probes`，每条含 `role`、`query`。输出 JSON。"""


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
    chunk_summary: str
    discarded_card_count: int = 0
    discarded_relation_count: int = 0
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ValidatedProbeResponse:
    probes_by_local_id: dict[str, list[RelationProbe]]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AtomicCardModelRoute:
    model: str
    tier: str


def _response_cannot_be_safely_repaired(response: Any) -> bool:
    raw_payload = getattr(response, "raw_payload", None) or {}
    if str(raw_payload.get("finish_reason") or "").strip().lower() == "length":
        return True
    proxy = getattr(response, "proxy", None) or {}
    return bool(proxy.get("json_prefix_continuation_attempted")) and not bool(
        proxy.get("json_prefix_continuation_success")
    )


def _response_message_content(response: Any) -> str:
    content = str(getattr(response, "text", "") or "").strip()
    if content:
        return content
    return json.dumps(
        getattr(response, "structured_output", None),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _response_usage_diagnostics(response: Any) -> dict[str, Any]:
    usage = dict(getattr(response, "usage", None) or {})
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
        "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
        "reasoning_chars": len(str(getattr(response, "reasoning_content", "") or "")),
        "duration_ms": int(getattr(response, "duration_ms", 0) or 0),
        "cache_hit": bool(getattr(response, "cache_hit", False)),
    }


class AtomicCognitiveCardExtractor:
    """先提取 Card 与同 Chunk 关系，再在同一对话中续问跨 Chunk Probe。"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        provider: str | None = None,
        thinking_type: str | None = None,
        relation_probe_thinking_type: str | None = None,
        system_prompt: str | None = None,
        relation_probe_followup_prompt: str | None = None,
        prompt_profile: str | None = None,
        concurrency: int = 4,
        segmenter: StableSpanSegmenter | None = None,
    ) -> None:
        self._llm = llm or get_llm_gateway_service()
        explicit_model = str(model or "").strip()
        forced_model = str(settings.KG_LLM_FORCE_MODEL or "").strip()
        self._model_override = explicit_model or forced_model or None
        self._model_override_tier = (
            "explicit_override"
            if explicit_model
            else ("global_force" if forced_model else "")
        )
        fallback_model = resolve_kg_llm_model("kg_cognitive_card")
        self._simple_model = str(
            settings.KG_COGNITIVE_CARD_SIMPLE_MODEL or fallback_model
        ).strip()
        self._complex_model = str(
            settings.KG_COGNITIVE_CARD_COMPLEX_MODEL or fallback_model
        ).strip()
        self._simple_max_sentence_blocks = max(
            1, int(settings.KG_COGNITIVE_CARD_SIMPLE_MAX_SENTENCE_BLOCKS)
        )
        self._simple_max_chars = max(
            1, int(settings.KG_COGNITIVE_CARD_SIMPLE_MAX_CHARS)
        )
        self._provider = str(provider or "").strip() or None
        normalized_thinking_type = str(
            thinking_type
            if thinking_type is not None
            else settings.KG_COGNITIVE_CARD_THINKING_TYPE
        ).strip().lower()
        if normalized_thinking_type not in {"", "enabled", "disabled"}:
            raise ValueError("thinking_type 只允许 enabled、disabled 或空值")
        normalized_probe_thinking_type = str(
            relation_probe_thinking_type
            if relation_probe_thinking_type is not None
            else settings.KG_RELATION_PROBE_THINKING_TYPE
        ).strip().lower()
        if normalized_probe_thinking_type not in {"", "enabled", "disabled"}:
            raise ValueError(
                "relation_probe_thinking_type 只允许 enabled、disabled 或空值"
            )
        self._card_thinking_type = normalized_thinking_type or None
        self._probe_thinking_type = (
            normalized_probe_thinking_type or self._card_thinking_type
        )
        self._custom_system_prompt = str(system_prompt or "").strip() or None
        self._system_prompt = self._custom_system_prompt or ATOMIC_CARD_SYSTEM_PROMPT
        self._relation_probe_followup_prompt = str(
            relation_probe_followup_prompt or ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT
        ).strip()
        self._prompt_profile = str(prompt_profile or "production").strip()
        self._prompt_fingerprint = hashlib.sha256(
            self._system_prompt.encode("utf-8")
        ).hexdigest()[:16]
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
                input_text_chars=len(chunk.content),
            )

        payload = dict(chunk.payload or {})
        model_route = self._select_model_route(
            sentence_block_count=len(sentence_blocks),
            text_chars=len(chunk.content),
        )
        prompt_input = render_atomic_card_prompt_input(
            source_published_at=payload.get("published_at") or "",
            source_title=payload.get("title") or "",
            sentence_blocks=sentence_blocks,
        )
        source_messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt_input},
        ]
        with langfuse_observation(
            name="kg.atomic_card.extract_cards_and_relations",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "text_chars": len(chunk.content),
                "sentence_block_count": len(sentence_blocks),
                "span_count": len(spans),
                "selected_model": model_route.model,
                "model_route": model_route.tier,
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            prefix_scope = self._prefix_warm_scope(model_route.model)
            warmup_lock = await self._claim_prefix_warmup_lock(prefix_scope)
            try:
                stage_usage: dict[str, dict[str, Any]] = {}
                request = self._card_request(
                    chunk=chunk,
                    payload=payload,
                    messages=source_messages,
                    model_route=model_route,
                    span_count=len(spans),
                )
                logger.info(
                    "原子 Card 请求已构建 chunk_id=%s model=%s messages=%s",
                    chunk.chunk_id,
                    model_route.model,
                    len(source_messages),
                )
                response = await self._llm.generate(request)
                logger.info("原子 Card 模型响应完成 chunk_id=%s", chunk.chunk_id)
                stage_usage["cards_and_relations"] = _response_usage_diagnostics(response)
                await self._mark_prefix_warmed(
                    prefix_scope,
                    settle=warmup_lock is not None,
                )
                validated, repaired, repair_attempted, accepted_card_response = (
                    await self._cards_from_response(
                        chunk=chunk,
                        spans=spans,
                        request=request,
                        response=response,
                    )
                )
                stage_usage["cards_and_relations"] = _response_usage_diagnostics(
                    accepted_card_response
                )
                (
                    cards_with_probes,
                    probe_repaired,
                    probe_repair_attempted,
                    probe_issues,
                    probe_usage,
                ) = (
                    await self._plan_relation_probes(
                        chunk=chunk,
                        cards_by_local_id=validated.cards_by_local_id,
                        relation_count=len(validated.relations),
                        base_messages=source_messages,
                        card_response=accepted_card_response,
                        model_route=model_route,
                    )
                )
                stage_usage["relation_probes"] = probe_usage
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
                        "selected_model": model_route.model,
                        "model_route": model_route.tier,
                        "llm_stage_usage": stage_usage,
                    },
                    status_message="completed",
                )
                return AtomicCardExtractionResult(
                    chunk_id=chunk.chunk_id,
                    spans=spans,
                    cards=cards_with_probes,
                    relations=validated.relations,
                    chunk_summary=validated.chunk_summary,
                    selected_model=model_route.model,
                    model_route=model_route.tier,
                    input_text_chars=len(chunk.content),
                    repaired=repaired,
                    repair_attempted=repair_attempted,
                    discarded_card_count=validated.discarded_card_count,
                    discarded_relation_count=validated.discarded_relation_count,
                    validation_issues=all_issues,
                    skip_reason=validated.skip_reason,
                    llm_stage_usage=stage_usage,
                )
            finally:
                if warmup_lock is not None:
                    await self._release_prefix_warmup_lock(warmup_lock)

    def _card_request(
        self,
        *,
        chunk: EvidenceChunk,
        payload: dict[str, Any],
        messages: list[dict[str, Any]],
        model_route: _AtomicCardModelRoute,
        span_count: int,
    ) -> LLMProxyRequest:
        return LLMProxyRequest(
            model=model_route.model,
            provider=self._provider,
            messages=messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_CARD_SCHEMA,
            provider_options=self._provider_options(self._card_thinking_type),
            metadata={
                "task": "kg_cognitive_card",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "source_type": payload.get("source_type") or "",
                "source_id": payload.get("source_id") or "",
                "chunk_id": chunk.chunk_id,
                "model_route": model_route.tier,
                "span_count": span_count,
                "input_text_chars": len(chunk.content),
                "thinking_type": self._card_thinking_type or "provider_default",
                "prompt_profile": self._prompt_profile,
                "prompt_fingerprint": self._prompt_fingerprint,
                "_cache_key_metadata": {
                    "task": "kg_cognitive_card",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                    "model_route": model_route.tier,
                    "thinking_type": self._card_thinking_type or "provider_default",
                    "prompt_profile": self._prompt_profile,
                    "prompt_fingerprint": self._prompt_fingerprint,
                },
            },
            use_cache=True,
        )

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
        required_top_level = {"cards", "relations", "skip_reason"}
        allowed_top_level = {*required_top_level, "chunk_summary"}
        if not required_top_level.issubset(data) or not set(data).issubset(
            allowed_top_level
        ):
            raise ValueError(
                "顶层字段不符合契约: "
                f"missing={sorted(required_top_level.difference(data))}, "
                f"extra={sorted(set(data).difference(allowed_top_level))}"
            )
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("cards 必须是数组")
        chunk_summary = str(data.get("chunk_summary") or "").strip()
        if len(raw_cards) >= 2 and not chunk_summary:
            raise ValueError("两张及以上 Card 时 chunk_summary 不能为空")
        if len(raw_cards) <= 1 and "chunk_summary" in data:
            raise ValueError("零张或一张 Card 时不得输出 chunk_summary")
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
                card = atomic_card_from_llm_item(
                    chunk, raw_card, spans=spans, chunk_summary=chunk_summary
                )
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
            chunk_summary=chunk_summary,
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
        model_route: _AtomicCardModelRoute,
    ) -> tuple[
        list[AtomicCognitiveCard],
        bool,
        bool,
        tuple[str, ...],
        dict[str, Any],
    ]:
        if not cards_by_local_id:
            return [], False, False, (), {}

        assistant_content = _response_message_content(card_response)
        conversation_messages = [
            *base_messages,
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": self._relation_probe_followup_prompt},
        ]
        request = LLMProxyRequest(
            model=model_route.model,
            provider=self._provider,
            messages=conversation_messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_RELATION_PROBE_SCHEMA,
            provider_options=self._provider_options(self._probe_thinking_type),
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
                "model_route": model_route.tier,
                "thinking_type": self._probe_thinking_type or "provider_default",
                "prompt_profile": self._prompt_profile,
                "prompt_fingerprint": self._prompt_fingerprint,
                "_cache_key_metadata": {
                    "task": "kg_relation_probe",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_RELATION_PROBE_GENERATOR_VERSION,
                    "model_route": model_route.tier,
                    "thinking_type": self._probe_thinking_type or "provider_default",
                    "prompt_profile": self._prompt_profile,
                    "prompt_fingerprint": self._prompt_fingerprint,
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
                "history_message_count": len(conversation_messages),
                "selected_model": model_route.model,
                "model_route": model_route.tier,
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
                    "usage": _response_usage_diagnostics(response),
                },
                status_message="completed",
            )
            return (
                cards,
                repaired,
                repair_attempted,
                validated.issues,
                _response_usage_diagnostics(response),
            )

    @staticmethod
    def _provider_options(thinking_type: str | None) -> dict[str, Any]:
        options: dict[str, Any] = {"inject_json_schema_instruction": False}
        if thinking_type:
            options["thinking_type"] = thinking_type
            if thinking_type != "disabled":
                options["reasoning_effort"] = "medium"
        return options

    def _select_model_route(
        self,
        *,
        sentence_block_count: int,
        text_chars: int,
    ) -> _AtomicCardModelRoute:
        if self._model_override:
            return _AtomicCardModelRoute(
                model=self._model_override,
                tier=self._model_override_tier,
            )
        if (
            sentence_block_count <= self._simple_max_sentence_blocks
            and text_chars <= self._simple_max_chars
        ):
            return _AtomicCardModelRoute(model=self._simple_model, tier="simple")
        return _AtomicCardModelRoute(model=self._complex_model, tier="complex")

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
                self._validate_probe_response(
                    cards_by_local_id,
                    response.structured_output,
                ),
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
                "只修复 probe_plans 的 JSON 结构、Card ID 和 Probe 字段；"
                "只保留 relation_probes 非空的 Card，并保持它们在输入中的相对顺序，"
                "不得修改 Card 或 Relation，不得新增原文没有的事实。"
            ),
            retry_reason="atomic_relation_probe_validation_invalid",
        )
        try:
            return (
                self._validate_probe_response(
                    cards_by_local_id,
                    repaired.structured_output,
                ),
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
        expected_fields = {"probe_plans"}
        if set(data) != expected_fields:
            raise ValueError(
                "Relation Probe 顶层字段不符合契约: "
                f"missing={sorted(expected_fields.difference(data))}, "
                f"extra={sorted(set(data).difference(expected_fields))}"
            )
        raw_plans = data.get("probe_plans")
        if not isinstance(raw_plans, list):
            raise ValueError("probe_plans 必须是数组")

        expected_ids = list(cards_by_local_id)
        actual_ids: list[str] = []
        probes_by_local_id: dict[str, list[RelationProbe]] = {
            local_card_id: [] for local_card_id in expected_ids
        }
        for index, item in enumerate(raw_plans, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"probe_plan[{index}] 必须是对象")
            if set(item) != {"local_card_id", "relation_probes"}:
                raise ValueError(f"probe_plan[{index}] 字段不符合契约")
            local_card_id = str(item.get("local_card_id") or "").strip()
            if local_card_id not in cards_by_local_id:
                raise ValueError(
                    f"probe_plan[{index}] 引用了未知 Card: {local_card_id}"
                )
            if local_card_id in actual_ids:
                raise ValueError(
                    f"probe_plan[{index}] 重复输出 Card: {local_card_id}"
                )
            actual_ids.append(local_card_id)
            probes = relation_probes_from_llm_items(
                item.get("relation_probes")
            )
            if not probes:
                raise ValueError(
                    f"probe_plan[{index}] relation_probes 为空，应省略该 Card"
                )
            if len(probes) > 2:
                raise ValueError(
                    f"probe_plan[{index}] 每个 Card 最多允许两个 Relation Probe"
                )
            probes_by_local_id[local_card_id] = probes
        expected_sparse_order = [
            local_card_id
            for local_card_id in expected_ids
            if local_card_id in actual_ids
        ]
        if actual_ids != expected_sparse_order:
            raise ValueError(
                "probe_plans 必须保持 Cards 的相对顺序: "
                f"expected={expected_sparse_order}, actual={actual_ids}"
            )
        return _ValidatedProbeResponse(
            probes_by_local_id=probes_by_local_id,
        )

    async def _claim_prefix_warmup_lock(self, prefix_scope: str) -> Any | None:
        if settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS <= 0:
            return None
        if await self._prefix_recently_warmed(prefix_scope):
            return None
        lock = self._prefix_warmup_lock(prefix_scope)
        acquired = await self._try_acquire_prefix_warmup_lock(lock)
        if acquired is None:
            return None
        if not acquired:
            return await self._wait_for_prefix_warmup(prefix_scope)
        if await self._prefix_recently_warmed(prefix_scope):
            await self._release_prefix_warmup_lock(lock)
            return None
        return lock

    def _prefix_warmup_lock(self, prefix_scope: str) -> Any:
        return self._redis_client().lock(
            f"{ATOMIC_CARD_PREFIX_WARM_LOCK_KEY}:{prefix_scope}",
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

    async def _wait_for_prefix_warmup(self, prefix_scope: str) -> Any | None:
        deadline = time.monotonic() + max(
            1,
            settings.KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS,
        )
        while time.monotonic() < deadline:
            if await self._prefix_recently_warmed(prefix_scope):
                return None
            lock = self._prefix_warmup_lock(prefix_scope)
            acquired = await self._try_acquire_prefix_warmup_lock(lock)
            if acquired is None:
                return None
            if acquired:
                if await self._prefix_recently_warmed(prefix_scope):
                    await self._release_prefix_warmup_lock(lock)
                    return None
                return lock
            await asyncio.sleep(ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS)
        return None

    async def _prefix_recently_warmed(self, prefix_scope: str) -> bool:
        try:
            return bool(
                await asyncio.to_thread(
                    self._redis_client().exists,
                    f"{ATOMIC_CARD_PREFIX_WARM_MARK_KEY}:{prefix_scope}",
                )
            )
        except Exception:
            return False

    async def _mark_prefix_warmed(
        self,
        prefix_scope: str,
        *,
        settle: bool = False,
    ) -> None:
        if settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS <= 0:
            return
        try:
            if settle and settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS > 0:
                await asyncio.sleep(settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS)
            await asyncio.to_thread(
                self._redis_client().setex,
                f"{ATOMIC_CARD_PREFIX_WARM_MARK_KEY}:{prefix_scope}",
                max(1, settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS),
                str(int(time.time())),
            )
        except Exception:
            return

    def _prefix_warm_scope(self, model: str) -> str:
        raw_scope = ":".join(
            (
                self._provider or "auto",
                model,
                self._card_thinking_type or "provider_default",
                self._prompt_fingerprint,
            )
        )
        return hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:16]

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

                fact_id_by_card = {
                    str(card_id): str(fact_id)
                    for card_id, fact_id in (
                        persistence.get("fact_id_by_card") or {}
                    ).items()
                    if card_id and fact_id
                }
                if fact_id_by_card:
                    cards = [
                        replace(
                            card,
                            fact_id=fact_id_by_card.get(
                                card.cognitive_card_id,
                                card.fact_id,
                            ),
                        )
                        for card in cards
                    ]
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
