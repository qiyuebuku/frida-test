"""原子 Cognitive Card 第一阶段测试。"""

from __future__ import annotations

import json
import inspect
import asyncio
import threading
from pathlib import Path

import pytest

from src.application.services.atomic_cognitive_card_service import (
    ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT,
    ATOMIC_CARD_SCHEMA,
    ATOMIC_CARD_SYSTEM_PROMPT,
    ATOMIC_RELATION_SCHEMA,
    AtomicCognitiveCardExtractor,
    AtomicCognitiveCardStageService,
    _intra_chunk_relation_sync_decisions,
)
from src.domain.knowledge.atomic_cognitive_card import (
    ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
    StableSpanSegmenter,
    atomic_card_focus_document,
    atomic_card_summary_document,
    atomic_card_from_llm_item,
    materialize_focus_evidence_context,
    render_atomic_card_prompt_input,
)
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.types import LLMProxyResponse
from src.infrastructure.persistence.models.knowledge import KnowledgeCognitiveCard
from src.infrastructure.persistence.repositories import knowledge_repository_impl as repository_module
from src.infrastructure.vector_store import semantic_hybrid_retriever as retriever_module
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever


def _chunk(content: str | None = None) -> EvidenceChunk:
    text = content or "甲公司发布公告，拟收购乙公司。该交易尚需监管机构批准。"
    return EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news_articles:test:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news_articles:test:1",
        content=text,
        chunk_index=0,
        start_offset=0,
        end_offset=len(text),
        text_hash="hash-1",
        chunker_version="test-v1",
        payload={
            "source_type": "news_articles",
            "source_id": "ft_news:1",
            "title": "甲公司拟收购乙公司",
            "published_at": "2026-07-11T09:00:00+08:00",
        },
    )


def _card_item(
    *,
    summary: str = "甲公司拟收购乙公司，交易尚需监管机构批准。",
    refs: list[str] | None = None,
    probes: list[dict] | None = None,
) -> dict:
    return {
        "summary": summary,
        "focus_evidence_refs": refs or ["s0001", "s0002", "s0003"],
        "relation_probes": list(probes or []),
    }


def _extraction_output(
    cards: list[dict],
    *,
    skip_reason: str = "",
) -> dict:
    return {
        "cards": [
            {"local_card_id": f"c{index}", **card}
            for index, card in enumerate(cards, start=1)
        ],
        "skip_reason": skip_reason,
    }


def _relation_output(relations: list[dict] | None = None) -> dict:
    return {"relations": list(relations or [])}


def test_focus_evidence_context_preserves_context_and_one_to_one_refs() -> None:
    context, refs = materialize_focus_evidence_context(
        "abcdefghij",
        focus_span_offsets=[
            {"ref": "s0001", "start_offset": 2, "end_offset": 5},
            {"ref": "s0002", "start_offset": 6, "end_offset": 9},
        ],
    )

    assert context == [
        {"text": "ab", "evidence_ref": None},
        {"text": "cde", "evidence_ref": "s0001"},
        {"text": "f", "evidence_ref": None},
        {"text": "ghi", "evidence_ref": "s0002"},
        {"text": "j", "evidence_ref": None},
    ]
    assert refs == ["s0001", "s0002"]
    plain_text = "".join(item["text"] for item in context)
    assert plain_text == "abcdefghij"


def test_atomic_card_prompt_uses_core_predicate_boundary() -> None:
    assert ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION == "atomic_card_extractor_v68"
    assert list(ATOMIC_CARD_SCHEMA["properties"]) == [
        "cards",
        "skip_reason",
    ]
    assert ATOMIC_CARD_SCHEMA["required"] == [
        "cards",
        "skip_reason",
    ]
    assert "<title>" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "[sNNNN]" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "最小但完整" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "按核心谓词拆分" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "必须分别形成 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "Card 只表达自身事实" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "同一次观测、统计或披露快照" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "定性总述 Card 和多张仅用于解释它的明细 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "脱离上下文仍能读懂" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "必须保留消息来源、声明者、预测者、认定者" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不同机构谈论不同命题不是冲突" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不同年度、季度或月度" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不能据此任意选择前文 Card 作为基线" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "信息包含检查" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不能额外创建一张只汇总其他 Cards 的总述 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "独立端点证据拼接后才能成立" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "原因发生时间不能晚于已发生的结果" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不同指标、不同统计口径" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "relation_evidence_refs 引用直接证明连接" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不得任选集合中的局部指标或个体作为关系端点" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不能由模型自行比较比例、幅度或数值" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "cards、skip_reason" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不要分析或输出 Cards 之间的关系" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "Cards 已冻结" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不要枚举没有连接证据的 Card 组合" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "Relation Probe" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不要为了让 Card 看起来完整而填充 role" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "当前 Card 尚未包含的另一个独立事件" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "Probe 是后续召回使用的关系假设" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不能把程序步骤机械拆成多张 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "同一个完整程序性事件内部的步骤顺序" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "不能把不同报告窗口机械拆成多张 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "数组元素只写 `sNNNN`" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "面向语义召回的简洁事件描述" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不是直接放弃整个关系方向" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "三项证明门槛" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    assert "暂时忽略输入中不属于该 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "source 定位、target 定位和连接" in ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT
    card_schema = ATOMIC_CARD_SCHEMA["properties"]["cards"]["items"]
    assert "relation_probes" in card_schema["properties"]
    assert "relation_probes" in card_schema["required"]
    relation_schema = ATOMIC_RELATION_SCHEMA["properties"]["relations"]["items"]
    assert set(relation_schema["properties"]) == {
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "basis",
        "relation_evidence_refs",
    }


def test_focus_evidence_context_rejects_overlapping_refs() -> None:
    with pytest.raises(ValueError, match="不能重叠"):
        materialize_focus_evidence_context(
            "abcdefghij",
            focus_span_offsets=[
                {"ref": "s0001", "start_offset": 2, "end_offset": 7},
                {"ref": "s0002", "start_offset": 5, "end_offset": 9},
            ],
        )


class _LLM:
    def __init__(self, outputs: list[object]):
        self.outputs = list(outputs)
        self.requests = []
        self.repairs = []

    async def generate(self, request):
        self.requests.append(request)
        return self._response(self.outputs.pop(0))

    async def repair_with_feedback(self, request, response, validation_issues, **kwargs):
        self.repairs.append(
            {
                "request": request,
                "response": response,
                "validation_issues": validation_issues,
                "kwargs": kwargs,
            }
        )
        return self._response(self.outputs.pop(0))

    @staticmethod
    def _response(output):
        return LLMProxyResponse(
            text=json.dumps(output, ensure_ascii=False),
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


class _DelayedLLM(_LLM):
    def __init__(self, outputs: list[object]) -> None:
        super().__init__(outputs)
        self.first_done = asyncio.Event()
        self.started_after_first_done: list[bool] = []

    async def generate(self, request):
        self.started_after_first_done.append(self.first_done.is_set())
        await asyncio.sleep(0.02)
        response = await super().generate(request)
        if len(self.requests) == 1:
            self.first_done.set()
        return response


class _TruncatedLLM(_LLM):
    async def generate(self, request):
        self.requests.append(request)
        return LLMProxyResponse(
            text='{"cards":[',
            structured_output=None,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={"finish_reason": "length"},
            proxy={
                "json_prefix_continuation_attempted": True,
                "json_prefix_continuation_success": False,
            },
        )

    async def repair_with_feedback(self, *args, **kwargs):
        raise AssertionError("截断输出不应进入业务 repair")


class _RedisLock:
    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock
        self._acquired = False

    def acquire(self, *_, **kwargs):
        self._acquired = self._lock.acquire(blocking=bool(kwargs.get("blocking", True)))
        return self._acquired

    def release(self):
        if self._acquired:
            self._acquired = False
            self._lock.release()


class _Redis:
    def __init__(self) -> None:
        self.values = {}
        self.shared_lock = threading.Lock()

    def exists(self, key):
        return int(key in self.values)

    def setex(self, key, _ttl, value):
        self.values[key] = value
        return True

    def lock(self, *_args, **_kwargs):
        return _RedisLock(self.shared_lock)


class _RecoveringRedis(_Redis):
    def __init__(self, failed_attempts: int) -> None:
        super().__init__()
        self.failed_attempts = failed_attempts
        self.acquire_attempts = 0

    def lock(self, *_args, **_kwargs):
        owner = self

        class RecoveringLock:
            def __init__(self) -> None:
                self.acquired = False

            def acquire(self, *_, **__):
                owner.acquire_attempts += 1
                if owner.acquire_attempts <= owner.failed_attempts:
                    return False
                self.acquired = True
                return True

            def release(self):
                self.acquired = False

        return RecoveringLock()


def test_span_segmenter_is_stable_and_preserves_offsets() -> None:
    text = "第一句。\n第二句！第三句没有句号"
    segmenter = StableSpanSegmenter()

    first = segmenter.segment(text)
    second = segmenter.segment(text)

    assert first == second
    assert [span.ref for span in first] == ["s0001", "s0002", "s0003"]
    assert [text[span.start_offset : span.end_offset] for span in first] == [
        span.text for span in first
    ]


def test_span_segmenter_drops_exact_duplicate_body_blocks() -> None:
    text = "【标题】同一事实重复出现。同一事实重复出现。另一项事实。"

    blocks = StableSpanSegmenter().segment_blocks(text)

    assert ["".join(part.text for part in block.parts) for block in blocks] == [
        "【标题】",
        "同一事实重复出现。",
        "另一项事实。",
    ]
    assert blocks[-1].parts[0].start_offset == text.rindex("另一项事实")


def test_span_segmenter_keeps_title_and_complete_semantic_sentences() -> None:
    text = (
        "【机构：元器件成本持续攀升 智能手机存储配置两极分化加剧】"
        "财联社7月13日电，据Omdia，随着存储成本持续上涨带来的财务压力不断加大，"
        "智能手机厂商正进一步缩减入门级产品布局，并将产品组合向利润率更高的中高端机型倾斜。"
        "与此同时，为满足消费者对高端产品不断提升的性能预期，并支撑零售价格上涨，"
        "高端及旗舰智能手机仍持续提升存储配置。"
        "Omdia高级研究经理Jusy Hong表示，这种差异化产品策略将进一步加剧今年智能手机市场的"
        "存储配置两极分化：高端机型将继续提升存储容量，而入门级机型则面临存储配置下调的趋势。"
    )

    blocks = StableSpanSegmenter().segment_blocks(text)
    spans = [part for block in blocks for part in block.parts]

    assert [block.role for block in blocks] == ["title", "body", "body", "body"]
    assert [[part.ref for part in block.parts] for block in blocks] == [
        ["s0001"],
        ["s0002", "s0003", "s0004", "s0005", "s0006"],
        ["s0007", "s0008", "s0009", "s0010"],
        ["s0011", "s0012", "s0013", "s0014"],
    ]
    assert ["".join(part.text for part in block.parts) for block in blocks] == [
        "【机构：元器件成本持续攀升 智能手机存储配置两极分化加剧】",
        "财联社7月13日电，据Omdia，随着存储成本持续上涨带来的财务压力不断加大，"
        "智能手机厂商正进一步缩减入门级产品布局，并将产品组合向利润率更高的中高端机型倾斜。",
        "与此同时，为满足消费者对高端产品不断提升的性能预期，并支撑零售价格上涨，"
        "高端及旗舰智能手机仍持续提升存储配置。",
        "Omdia高级研究经理Jusy Hong表示，这种差异化产品策略将进一步加剧今年智能手机市场的"
        "存储配置两极分化：高端机型将继续提升存储容量，而入门级机型则面临存储配置下调的趋势。",
    ]
    assert all(span.text == text[span.start_offset : span.end_offset] for span in spans)

    prompt_input = render_atomic_card_prompt_input(
        source_published_at="2026-07-13T02:56:59+00:00",
        sentence_blocks=blocks,
    )
    prompt_lines = prompt_input.splitlines()
    assert prompt_lines[0] == "published_at=2026-07-13T02:56:59+00:00"
    assert prompt_lines[1].startswith("<title>[s0001]【机构：")
    assert prompt_lines[2].startswith("[s0002]财联社7月13日电，[s0003]据Omdia，")
    assert "[s0004]随着存储成本持续上涨" in prompt_lines[2]
    assert len(prompt_lines) == 5


def test_prompt_renderer_marks_plain_leading_source_title() -> None:
    blocks = StableSpanSegmenter().segment_blocks("普通标题\n第一句正文。")

    prompt_input = render_atomic_card_prompt_input(
        source_published_at="2026-07-13T02:56:59+00:00",
        source_title="普通标题",
        sentence_blocks=blocks,
    )

    assert prompt_input.splitlines() == [
        "published_at=2026-07-13T02:56:59+00:00",
        "<title>[s0001]普通标题",
        "[s0002]第一句正文。",
    ]


def test_span_segmenter_keeps_fine_refs_inside_one_long_sentence_block() -> None:
    clauses = [
        f"第{index}个分句描述同一项长期事实并补充必要上下文"
        for index in range(1, 18)
    ]
    text = "，".join(clauses) + "。"

    blocks = StableSpanSegmenter().segment_blocks(text)
    spans = [part for block in blocks for part in block.parts]

    assert len(blocks) == 1
    assert len(spans) == len(clauses)
    assert "".join(span.text for span in spans) == text


def test_atomic_card_builds_stable_id_independent_of_ref_gaps() -> None:
    chunk = _chunk()
    spans = StableSpanSegmenter().segment(chunk.content)
    first = _card_item(refs=["s0001", "s0002", "s0003"])
    second = _card_item(refs=["s0001", "s0003"])

    card_a = atomic_card_from_llm_item(chunk, first, spans=spans)
    card_b = atomic_card_from_llm_item(chunk, second, spans=spans)

    assert card_a.cognitive_card_id == card_b.cognitive_card_id
    assert card_a.summary
    assert card_a.focus_span_offsets[0]["ref"] == "s0001"


def test_atomic_cards_with_same_refs_but_different_facts_have_distinct_ids() -> None:
    chunk = _chunk("存储芯片概念持续下挫，德明利触及跌停。")
    spans = StableSpanSegmenter().segment(chunk.content)
    first = _card_item(
        summary="存储芯片概念持续下挫。",
        refs=["s0001"],
    )
    second = _card_item(
        summary="德明利触及跌停。",
        refs=["s0001"],
    )

    card_a = atomic_card_from_llm_item(chunk, first, spans=spans)
    card_b = atomic_card_from_llm_item(chunk, second, spans=spans)

    assert card_a.cognitive_card_id != card_b.cognitive_card_id


def test_atomic_card_rejects_unknown_span_ref() -> None:
    chunk = _chunk()
    item = _card_item(refs=["s9999"])

    with pytest.raises(ValueError, match="不存在的 Span Ref"):
        atomic_card_from_llm_item(chunk, item, spans=StableSpanSegmenter().segment(chunk.content))


def test_atomic_card_normalizes_bracketed_span_refs() -> None:
    chunk = _chunk()
    spans = StableSpanSegmenter().segment(chunk.content)
    item = _card_item(refs=["[s0001]", "[s0002]"])

    card = atomic_card_from_llm_item(chunk, item, spans=spans)

    assert card.focus_evidence_refs == ["s0001", "s0002"]


def test_atomic_card_normalizes_relation_probes_for_pg_manifest() -> None:
    chunk = _chunk()
    item = _card_item(
        probes=[
            {
                "role": "same_event",
                "query": "甲公司收购乙公司的监管审批结果或交易终止公告。",
            },
            {
                "role": "contradiction",
                "query": "甲公司否认收购乙公司或取消该交易。",
            },
        ]
    )

    card = atomic_card_from_llm_item(
        chunk,
        item,
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    assert [probe.as_dict() for probe in card.relation_probes] == item["relation_probes"]
    assert [probe.as_dict() for probe in card.manifest().relation_probes] == item[
        "relation_probes"
    ]


def test_atomic_card_leaves_summary_factual_quality_to_model_evaluation() -> None:
    chunk = _chunk()
    item = _card_item(summary="甲公司拟以100亿元收购乙公司。")

    card = atomic_card_from_llm_item(
        chunk,
        item,
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    assert card.summary == "甲公司拟以100亿元收购乙公司。"


def test_atomic_card_accepts_equivalent_percentage_format() -> None:
    chunk = _chunk("甲公司收入同比增长26.0%。")
    item = {
        "summary": "甲公司收入同比增长26%。",
        "focus_evidence_refs": ["s0001"],
        "relation_probes": [],
    }

    card = atomic_card_from_llm_item(
        chunk,
        item,
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    assert card.summary == "甲公司收入同比增长26%。"


def test_atomic_card_allows_year_grounded_by_relative_time_and_publish_time() -> None:
    chunk = _chunk("甲公司今年5月发布新产品。")
    item = {
        "summary": "甲公司于2026年5月发布新产品。",
        "focus_evidence_refs": ["s0001"],
        "relation_probes": [],
    }

    card = atomic_card_from_llm_item(
        chunk,
        item,
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    assert "2026年5月" in card.summary


@pytest.mark.asyncio
async def test_extractor_generates_cards_then_relations_in_same_conversation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("甲公司发布公告，拟收购乙公司。丙公司宣布终止丁项目。")
    output = _extraction_output(
        [
            {
                **_card_item(summary="甲公司拟收购乙公司。", refs=["s0001", "s0002"]),
            },
            {
                "summary": "丙公司宣布终止丁项目。",
                "focus_evidence_refs": ["s0003"],
                "relation_probes": [],
            },
        ]
    )
    llm = _LLM([output, _relation_output()])

    cards = await AtomicCognitiveCardExtractor(llm=llm, model="test", concurrency=1).extract([chunk])

    assert len(cards) == 2
    assert len(llm.requests) == 2
    assert len(llm.repairs) == 0
    prompt_input = llm.requests[0].messages[1]["content"]
    assert prompt_input == (
        "published_at=2026-07-11T09:00:00+08:00\n"
        "[s0001]甲公司发布公告，[s0002]拟收购乙公司。\n"
        "[s0003]丙公司宣布终止丁项目。"
    )
    assert chunk.chunk_id not in prompt_input
    assert "chunk_text" not in prompt_input
    assert "source_title" not in prompt_input
    assert llm.requests[0].messages[0]["content"] == ATOMIC_CARD_SYSTEM_PROMPT
    assert "published_at" in llm.requests[0].messages[0]["content"]
    assert "Relation Probe" in llm.requests[0].messages[0]["content"]
    assert "候选事件" in llm.requests[0].messages[0]["content"]
    card_schema = llm.requests[0].json_schema["properties"]["cards"]["items"]
    assert "relation_probes" in card_schema["properties"]
    assert "2026-07-11T09:00:00+08:00" not in llm.requests[0].messages[0]["content"]
    assert llm.requests[0].metadata["chunk_id"] == chunk.chunk_id
    assert llm.requests[0].provider_options == {
        "reasoning_effort": "medium",
        "inject_json_schema_instruction": False,
    }
    assert llm.requests[0].metadata["_cache_key_metadata"]["generator_version"].startswith(
        "atomic_card_extractor_"
    )
    relation_request = llm.requests[1]
    assert relation_request.messages[:2] == llm.requests[0].messages
    assert relation_request.messages[2] == {
        "role": "assistant",
        "content": json.dumps(output, ensure_ascii=False),
    }
    assert relation_request.messages[3] == {
        "role": "user",
        "content": ATOMIC_CARD_RELATION_FOLLOWUP_PROMPT,
    }
    assert relation_request.json_schema == ATOMIC_RELATION_SCHEMA
    assert relation_request.provider_options["inject_json_schema_instruction"] is False


@pytest.mark.asyncio
async def test_extractor_maps_intra_chunk_relations_to_final_card_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。受强降雨影响，多地停课。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002", "s0003"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "causal_influence",
                "basis": "原文明确使用“受强降雨影响”连接降雨与停课。",
                "relation_evidence_refs": ["[s0001]", "[s0002]", "[s0003]"],
            }
        ],
    )

    results = await AtomicCognitiveCardExtractor(
        llm=_LLM([output, relation_output]),
        model="test",
        concurrency=1,
    ).extract_with_diagnostics([chunk])

    assert len(results[0].cards) == 2
    assert len(results[0].relations) == 1
    relation = results[0].relations[0]
    assert relation.source_card_id == results[0].cards[0].cognitive_card_id
    assert relation.target_card_id == results[0].cards[1].cognitive_card_id
    assert relation.relation_kind == "causal_influence"
    assert relation.source_evidence_refs == ["s0001"]
    assert relation.target_evidence_refs == ["s0002", "s0003"]
    assert relation.relation_evidence_refs == [
        {"chunk_id": chunk.chunk_id, "refs": ["s0001", "s0002", "s0003"]}
    ]


@pytest.mark.asyncio
async def test_intra_chunk_relation_sync_marks_missing_pairs_as_no_relation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。多地因强降雨停课。甲公司发布年度报告。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002", "s0003"]),
            _card_item(summary="甲公司发布年度报告。", refs=["s0003"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "causal_influence",
                "basis": "原文明确说明停课由强降雨引起。",
                "relation_evidence_refs": ["s0001", "s0002", "s0003"],
            }
        ],
    )
    results = await AtomicCognitiveCardExtractor(
        llm=_LLM([output, relation_output]),
        model="test",
        concurrency=1,
    ).extract_with_diagnostics([chunk])

    decisions = _intra_chunk_relation_sync_decisions(results)

    assert len(decisions) == 3
    assert [item.decision_class for item in decisions].count("observed") == 1
    assert [item.decision_class for item in decisions].count("no_relation") == 2
    assert all(
        item.relation_kind == ""
        for item in decisions
        if item.decision_class == "no_relation"
    )


def test_intra_chunk_relation_discards_same_event_without_losing_cards() -> None:
    chunk = _chunk("辽宁出现强降雨。沈阳出现特大暴雨。")
    card_output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="沈阳出现特大暴雨。", refs=["s0002"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "same_event",
                "basis": "属于同一场降雨",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )

    card_result = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_output,
    )
    validated = AtomicCognitiveCardExtractor._validate_relation_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_result.cards_by_local_id,
        relation_output,
    )

    assert validated.relations == []
    assert validated.discarded_relation_count == 1


def test_intra_chunk_fast_path_discards_inferred_contract_without_losing_cards() -> None:
    chunk = _chunk("员工离开甲公司。员工随后加入乙公司。")
    card_output = _extraction_output(
        [
            _card_item(summary="员工离开甲公司。", refs=["s0001"]),
            _card_item(summary="员工加入乙公司。", refs=["s0002"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "decision_class": "inferred",
                "relation_kind": "temporal_progression",
                "basis": "员工离开甲公司后加入乙公司。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )

    card_result = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_output,
    )
    validated = AtomicCognitiveCardExtractor._validate_relation_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_result.cards_by_local_id,
        relation_output,
    )

    assert validated.relations == []
    assert validated.discarded_relation_count == 1


@pytest.mark.asyncio
async def test_invalid_relation_does_not_trigger_repair_or_discard_cards(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="沈阳出现特大暴雨。", refs=["s0002"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "same_event",
                "basis": "同一场降雨。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )
    llm = _LLM([output, relation_output])

    result = (
        await AtomicCognitiveCardExtractor(llm=llm, model="test").extract_with_diagnostics(
            [_chunk("辽宁出现强降雨。沈阳出现特大暴雨。")]
        )
    )[0]

    assert len(result.cards) == 2
    assert result.relations == []
    assert result.discarded_relation_count == 1
    assert result.repair_attempted is False
    assert llm.repairs == []


@pytest.mark.asyncio
async def test_extractor_allows_zero_cards_with_reason(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([_extraction_output([], skip_reason="只有网页导航信息")])

    results = await AtomicCognitiveCardExtractor(llm=llm, model="test").extract_with_diagnostics(
        [_chunk("首页。返回顶部。")]
    )

    assert results[0].cards == []
    assert results[0].repaired is False
    assert len(llm.requests) == 1


@pytest.mark.asyncio
async def test_extractor_single_flights_cold_prefix_then_runs_concurrently(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 3600)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_LOCK_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS", 0)
    chunks = [
        _chunk().model_copy(
            update={
                "chunk_id": f"kg_chunk:kg_ev:financial:news_articles:test:{index}:0",
                "evidence_id": f"kg_ev:financial:news_articles:test:{index}",
                "text_hash": f"hash-{index}",
            }
        )
        for index in range(3)
    ]
    llm = _DelayedLLM([_extraction_output([_card_item()]) for _ in chunks])
    extractor = AtomicCognitiveCardExtractor(llm=llm, model="test", concurrency=3)
    extractor._redis = _Redis()

    cards = await extractor.extract(chunks)

    assert len(cards) == 3
    assert llm.started_after_first_done[0] is False
    assert llm.started_after_first_done[1:] == [True, True]


@pytest.mark.asyncio
async def test_extractor_shares_concurrency_limit_across_separate_calls(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)

    class TrackingLLM(_LLM):
        def __init__(self) -> None:
            super().__init__([_extraction_output([_card_item()]) for _ in range(2)])
            self.active = 0
            self.max_active = 0

        async def generate(self, request):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)
            try:
                return await super().generate(request)
            finally:
                self.active -= 1

    llm = TrackingLLM()
    extractor = AtomicCognitiveCardExtractor(llm=llm, model="test", concurrency=1)
    chunks = [
        _chunk().model_copy(
            update={
                "chunk_id": f"kg_chunk:test:{index}",
                "evidence_id": f"kg_ev:test:{index}",
                "text_hash": f"hash-{index}",
            }
        )
        for index in range(2)
    ]

    await asyncio.gather(
        extractor.extract_with_diagnostics([chunks[0]]),
        extractor.extract_with_diagnostics([chunks[1]]),
    )

    assert llm.max_active == 1


@pytest.mark.asyncio
async def test_extractor_reclaims_expired_prefix_warmup_lock(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 3600)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS", 0)
    monkeypatch.setattr(
        "src.application.services.atomic_cognitive_card_service.ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS",
        0.001,
    )
    llm = _LLM([_extraction_output([_card_item()])])
    redis_client = _RecoveringRedis(failed_attempts=2)
    extractor = AtomicCognitiveCardExtractor(llm=llm, model="test")
    extractor._redis = redis_client

    cards = await extractor.extract([_chunk()])

    assert len(cards) == 1
    assert redis_client.acquire_attempts == 3
    assert len(llm.requests) == 1


@pytest.mark.asyncio
async def test_extractor_repairs_invalid_output_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    valid = _extraction_output([_card_item()])
    llm = _LLM([_extraction_output([]), valid])

    results = await AtomicCognitiveCardExtractor(llm=llm, model="test").extract_with_diagnostics([_chunk()])

    assert len(results[0].cards) == 1
    assert results[0].repaired is True
    assert results[0].repair_attempted is True
    assert len(llm.requests) == 1
    assert len(llm.repairs) == 1


@pytest.mark.asyncio
async def test_extractor_fails_when_repair_is_still_invalid(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM(
        [
            _extraction_output([]),
            {
                "cards": [{"local_card_id": "c1", "summary": "仍然缺少必要字段"}],
                "skip_reason": "",
            },
        ]
    )

    with pytest.raises(RuntimeError, match="修复后仍未通过校验"):
        await AtomicCognitiveCardExtractor(llm=llm, model="test").extract([_chunk()])


@pytest.mark.asyncio
async def test_extractor_does_not_restart_business_repair_after_truncation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _TruncatedLLM([])

    with pytest.raises(RuntimeError, match="Prefix Completion 后仍未完成"):
        await AtomicCognitiveCardExtractor(llm=llm, model="test").extract([_chunk()])

    assert len(llm.requests) == 1
    assert llm.repairs == []


def test_milvus_documents_separate_summary_and_focus_without_pg_only_fields() -> None:
    chunk = _chunk()
    card = atomic_card_from_llm_item(
        chunk,
        _card_item(),
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    summary_document = atomic_card_summary_document(card)
    focus_document = atomic_card_focus_document(card, chunk_content=chunk.content)

    assert summary_document.document_id == card.cognitive_card_id
    assert summary_document.collection_role == "cognitive_card"
    assert summary_document.text == card.summary
    assert focus_document.collection_role == "cognitive_card_focus"
    assert focus_document.text.replace("\n", "") == chunk.content
    for document in (summary_document, focus_document):
        assert "relation_probes" not in document.metadata
        assert "focus_evidence_refs" not in document.metadata
        assert "focus_span_offsets" not in document.metadata


@pytest.mark.asyncio
async def test_stage_stops_at_cards_ready_and_cleans_stale_targets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([_extraction_output([_card_item()])])

    class Repository:
        def __init__(self):
            self.calls = []

        def replace_atomic_cognitive_cards_for_evidence(self, adapter_name, *, evidence_ids, cards):
            self.calls.append((adapter_name, evidence_ids, cards))
            return {
                "inserted_cards": len(cards),
                "deleted_cards": 1,
                "deleted_card_ids": ["kg_cognitive_card:stale"],
            }

        def list_atomic_cognitive_card_ids_for_inactive_evidence(self, _adapter_name):
            return []

    class Retriever:
        def __init__(self):
            self.upserts = []
            self.deletes = []

        async def upsert_semantic_documents(self, **kwargs):
            self.upserts.append(kwargs)
            return len(kwargs["documents"])

        async def delete_documents_by_role(self, **kwargs):
            self.deletes.append(kwargs)
            return len(kwargs["target_ids"])

    repository = Repository()
    retriever = Retriever()

    class RelationWriter:
        def __init__(self):
            self.invalidations = []

        async def invalidate_cards(self, card_ids, **kwargs):
            self.invalidations.append((list(card_ids), kwargs))
            return {"changed_edge_ids": ["kg_card_relation:stale"]}

    relation_writer = RelationWriter()
    service = AtomicCognitiveCardStageService(
        repository=repository,
        semantic_retriever=retriever,
        extractor=AtomicCognitiveCardExtractor(llm=llm, model="test"),
        relation_writer=relation_writer,
    )

    result = await service.refresh(
        adapter_name="financial",
        target="test",
        kg_version="v1",
        changed_chunks=[_chunk()],
    )

    assert result.status == "cards_ready"
    assert result.diagnostics["assignment_executed"] is False
    assert result.diagnostics["milvus_documents_written"] == 2
    assert [item["collection_role"] for item in retriever.deletes] == [
        "cognitive_card",
        "cognitive_card_focus",
    ]
    assert retriever.deletes[0]["target_ids"] == ["kg_cognitive_card:stale"]
    assert relation_writer.invalidations[0][0] == ["kg_cognitive_card:stale"]
    assert result.diagnostics["invalidated_relation_edge_ids"] == ["kg_card_relation:stale"]


@pytest.mark.asyncio
async def test_stage_persists_intra_chunk_relations_after_card_documents(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。受强降雨影响，多地停课。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002"]),
        ]
    )
    relation_output = _relation_output(
        [
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "causal_influence",
                "basis": "原文明确说明停课受强降雨影响。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )
    calls = []

    class Repository:
        def list_atomic_cognitive_card_ids_for_inactive_evidence(self, _adapter_name):
            return []

        def replace_atomic_cognitive_cards_for_evidence(self, _adapter_name, *, evidence_ids, cards):
            calls.append(("pg", list(evidence_ids), len(cards)))
            return {"inserted_cards": len(cards), "deleted_card_ids": []}

    class Retriever:
        async def upsert_semantic_documents(self, **kwargs):
            calls.append(("milvus", len(kwargs["documents"])))
            return len(kwargs["documents"])

        async def delete_documents_by_role(self, **_kwargs):
            return 0

    class RelationWriter:
        async def persist_verified_decisions(self, decisions, **kwargs):
            calls.append(("relations", list(decisions), kwargs))
            return {
                "changed_edge_ids": ["kg_card_relation:local"],
                "graph_event_ids": ["event:local"],
            }

    result = await AtomicCognitiveCardStageService(
        repository=Repository(),
        semantic_retriever=Retriever(),
        extractor=AtomicCognitiveCardExtractor(
            llm=_LLM([output, relation_output]),
            model="test",
        ),
        relation_writer=RelationWriter(),
    ).refresh(
        adapter_name="financial",
        target="test",
        kg_version="v1",
        changed_chunks=[chunk],
    )

    assert [item[0] for item in calls] == ["pg", "milvus", "relations"]
    relation_call = calls[2]
    assert len(relation_call[1]) == 1
    assert relation_call[1][0].relation_kind == "causal_influence"
    assert relation_call[2]["pipeline_version"] == "atomic_card_intra_chunk_relation_v1"
    assert result.diagnostics["intra_chunk_relations"] == 1
    assert result.diagnostics["intra_chunk_observed"] == 1
    assert result.diagnostics["intra_chunk_inferred"] == 0
    assert result.diagnostics["intra_chunk_changed_edge_ids"] == [
        "kg_card_relation:local"
    ]


@pytest.mark.asyncio
async def test_stage_zero_card_replacement_deletes_old_pg_and_milvus_cards(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([_extraction_output([], skip_reason="没有可独立验证的事实")])

    class Repository:
        def list_atomic_cognitive_card_ids_for_inactive_evidence(self, _adapter_name):
            return []

        def replace_atomic_cognitive_cards_for_evidence(self, _adapter_name, *, evidence_ids, cards):
            assert evidence_ids == [_chunk().evidence_id]
            assert cards == []
            return {
                "inserted_cards": 0,
                "deleted_cards": 1,
                "deleted_card_ids": ["kg_cognitive_card:old"],
            }

    class Retriever:
        def __init__(self):
            self.deleted = []

        async def upsert_semantic_documents(self, **kwargs):
            assert kwargs["documents"] == []
            return 0

        async def delete_documents_by_role(self, **kwargs):
            self.deleted.extend(kwargs["target_ids"])
            return len(kwargs["target_ids"])

    retriever = Retriever()
    result = await AtomicCognitiveCardStageService(
        repository=Repository(),
        semantic_retriever=retriever,
        extractor=AtomicCognitiveCardExtractor(llm=llm, model="test"),
    ).refresh(
        adapter_name="financial",
        target="test",
        kg_version="v1",
        changed_chunks=[_chunk()],
    )

    assert result.cards == []
    assert result.diagnostics["zero_card_chunks"] == 1
    assert result.diagnostics["zero_card_reasons"][0]["reason"] == "没有可独立验证的事实"
    assert retriever.deleted == ["kg_cognitive_card:old", "kg_cognitive_card:old"]


@pytest.mark.asyncio
async def test_stage_cleans_inactive_evidence_cards_before_deleting_manifest() -> None:
    calls = []

    class Repository:
        def list_atomic_cognitive_card_ids_for_inactive_evidence(self, adapter_name):
            calls.append(("list", adapter_name))
            return ["kg_cognitive_card:inactive"]

        def delete_atomic_cognitive_cards_by_ids(
            self, adapter_name, *, cognitive_card_ids
        ):
            calls.append(("delete_manifest", adapter_name, list(cognitive_card_ids)))
            return len(cognitive_card_ids)

    class Retriever:
        async def delete_documents_by_role(self, **kwargs):
            calls.append(("delete_milvus", kwargs["collection_role"]))
            return len(kwargs["target_ids"])

    class RelationWriter:
        async def invalidate_cards(self, card_ids, **_kwargs):
            calls.append(("invalidate_edges", list(card_ids)))
            return {"changed_edge_ids": ["kg_card_relation:inactive"]}

    result = await AtomicCognitiveCardStageService(
        repository=Repository(),
        semantic_retriever=Retriever(),
        relation_writer=RelationWriter(),
    ).refresh(
        adapter_name="financial",
        target="test",
        kg_version="v1",
        changed_chunks=[],
    )

    assert calls == [
        ("list", "financial"),
        ("delete_milvus", "cognitive_card"),
        ("delete_milvus", "cognitive_card_focus"),
        ("invalidate_edges", ["kg_cognitive_card:inactive"]),
        ("delete_manifest", "financial", ["kg_cognitive_card:inactive"]),
    ]
    assert result.diagnostics["milvus_stale_documents_deleted"] == 2
    assert result.diagnostics["invalidated_relation_edge_ids"] == [
        "kg_card_relation:inactive"
    ]


def test_pg_manifest_schema_does_not_store_readable_card_text() -> None:
    columns = set(KnowledgeCognitiveCard.__table__.columns.keys())

    assert {
        "focus_evidence_refs",
        "focus_span_offsets",
        "relation_probes",
        "generator_version",
    } <= columns
    assert "factual_anchors" not in columns
    assert not {
        "summary",
        "title_candidates",
        "topic_intents",
        "risk_signals",
        "supporting_text",
        "payload",
    }.intersection(columns)

    project_root = Path(__file__).resolve().parents[3]
    for relative_path in ("schema/06_knowledge.sql", "schema/13_cognitive_index.sql"):
        ddl = (project_root / relative_path).read_text(encoding="utf-8")
        table_block = ddl.split("CREATE TABLE IF NOT EXISTS public.kg_cognitive_cards", 1)[1].split(");", 1)[0]
        assert "focus_evidence_refs jsonb" in table_block
        assert "relation_probes jsonb" in table_block
        assert "generator_version character varying(96)" in table_block
        assert "factual_anchors" not in table_block
        assert "summary text" not in table_block
        assert "topic_intents jsonb" not in table_block

    chunk = _chunk()
    card = atomic_card_from_llm_item(
        chunk,
        _card_item(),
        spans=StableSpanSegmenter().segment(chunk.content),
    )
    values = repository_module._atomic_cognitive_card_manifest_values(card)
    assert values["cognitive_card_id"] == card.cognitive_card_id
    assert values["focus_span_offsets"] == card.focus_span_offsets
    assert "summary" not in values
    assert values["relation_probes"] == []


def test_pg_replace_has_no_hidden_assignment_cascade() -> None:
    source = inspect.getsource(repository_module.KnowledgeRepositoryImpl.replace_atomic_cognitive_cards_for_evidence)

    assert "KnowledgeCommunityAssignment" not in source


@pytest.mark.asyncio
async def test_atomic_card_upsert_uses_cognitive_card_collection(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    class Store:
        def __init__(self):
            self.calls = []

        def ensure_ready(self):
            return None

        def upsert_documents_by_role(self, **kwargs):
            self.calls.append(kwargs)
            return sum(len(items) for items in kwargs["documents_by_role"].values())

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    chunk = _chunk()
    card = atomic_card_from_llm_item(
        chunk,
        _card_item(),
        spans=StableSpanSegmenter().segment(chunk.content),
    )
    store = Store()

    count = await MilvusSemanticHybridRetriever(store=store).upsert_semantic_documents(
        adapter_name="financial",
        target="test",
        documents=[
            atomic_card_summary_document(card),
            atomic_card_focus_document(card, chunk_content=chunk.content),
        ],
        kg_version="v1",
    )

    assert count == 2
    summary = store.calls[0]["documents_by_role"]["cognitive_card"][0]
    focus = store.calls[0]["documents_by_role"]["cognitive_card_focus"][0]
    assert summary.target_id == card.cognitive_card_id
    assert summary.text == card.summary
    assert focus.target_id == card.cognitive_card_id
    assert focus.text.replace("\n", "") == chunk.content
