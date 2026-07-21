"""原子 Cognitive Card 第一阶段测试。"""

from __future__ import annotations

import json
import inspect
import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from src.application.services.atomic_cognitive_card_service import (
    ATOMIC_CARD_SCHEMA,
    ATOMIC_CARD_SYSTEM_PROMPT,
    ATOMIC_CARD_FROM_TOPOLOGY_FOLLOWUP_PROMPT,
    ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT,
    ATOMIC_EVIDENCE_TOPOLOGY_REQUEST,
    ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA,
    ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT,
    ATOMIC_RELATION_PROBE_FROM_TOPOLOGY_FOLLOWUP_PROMPT,
    ATOMIC_RELATION_PROBE_SCHEMA,
    ATOMIC_TOPOLOGY_RELATION_PROBE_SCHEMA,
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
    relation_probes_from_llm_items,
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
) -> dict:
    return {
        "summary": summary,
        "focus_evidence_refs": refs or ["s0001", "s0002"],
    }


def _extraction_output(
    cards: list[dict],
    *,
    relations: list[dict] | None = None,
    skip_reason: str = "",
) -> dict:
    return {
        "cards": [
            {"local_card_id": f"c{index}", **card}
            for index, card in enumerate(cards, start=1)
        ],
        "relations": list(relations or []),
        "skip_reason": skip_reason,
    }


def _probe_output(
    card_count: int,
    probes_by_card: dict[str, list[dict]] | None = None,
) -> dict:
    probes_by_card = probes_by_card or {}
    return {
        "probe_plans": [
            {
                "local_card_id": f"c{index}",
                "relation_probes": list(probes_by_card.get(f"c{index}") or []),
            }
            for index in range(1, card_count + 1)
        ]
    }


def _topology_output() -> dict:
    return {
        "event_groups": [
            {
                "group_id": "g1",
                "evidence_refs": ["s0001", "s0002"],
                "focus": "收购计划与监管条件",
            },
        ],
        "keep_separate": [
            {
                "left_evidence_refs": ["s0001"],
                "right_evidence_refs": ["s0002"],
            }
        ],
        "direct_links": [
            {
                "source_evidence_refs": ["s0002"],
                "target_evidence_refs": ["s0001"],
                "link_evidence_refs": ["s0001", "s0002"],
                "link_statement": "甲公司发布公告，拟收购乙公司。该交易尚需监管机构批准",
                "source_mention": "监管机构批准",
                "relation_cue": "尚需",
                "target_mention": "拟收购乙公司",
                "relation_kind": "constraint",
            }
        ],
        "open_slots": [
            {
                "evidence_refs": ["s0001"],
                "role": "upstream",
                "endpoint_constraint": "甲公司决定收购乙公司的已发生直接原因",
            }
        ],
    }


def test_topology_discards_link_statement_not_found_in_link_evidence() -> None:
    topology = _topology_output()
    topology["direct_links"][0]["link_statement"] = "导致股价上涨"

    validated = AtomicCognitiveCardExtractor._validate_evidence_topology(
        topology,
        evidence_text_by_ref={
            "s0001": "甲公司发布公告，拟收购乙公司。",
            "s0002": "该交易尚需监管机构批准。",
        },
    )

    assert validated["direct_links"] == []


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
    assert ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION == "atomic_card_extractor_v103"
    assert list(ATOMIC_CARD_SCHEMA["properties"]) == [
        "cards",
        "relations",
        "skip_reason",
    ]
    assert ATOMIC_CARD_SCHEMA["required"] == [
        "cards",
        "relations",
        "skip_reason",
    ]
    assert "<title>" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "[sNNNN]" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "一个 Ref 可能包含多个事实" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不是需要逐项分类的清单" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "一张 Card 恰好对应一个事实键" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "同一句、同一公告、同一事件或共同描述一种局面都不是合并理由" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "两个分句能够分别为真或为假时必须拆分" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "原因、决定、执行、回应、结果分别作为端点" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "只合并事实键完全相同的重复表述" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "summary 只陈述一个事实端点、一个核心谓词" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "作者归纳、宽泛评价和没有明确声明者的推演不建卡" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "同时证明两端和连接" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不得使用字母后缀或跳号" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "只有后续 user 明确切换阶段时才执行 Relation Probe" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "其他 Chunk 历史 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "Summary 已承担同义召回" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "候选事件已在当前 Chunk 出现时" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不补造具体主体" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "显著补充解释或可信度" in ATOMIC_CARD_SYSTEM_PROMPT
    assert len(ATOMIC_CARD_SYSTEM_PROMPT) < 2500
    card_schema = ATOMIC_CARD_SCHEMA["properties"]["cards"]["items"]
    assert "relation_probes" not in card_schema["properties"]
    assert "maxItems" not in ATOMIC_CARD_SCHEMA["properties"]["cards"]
    assert "maxItems" not in ATOMIC_CARD_SCHEMA["properties"]["relations"]
    assert ATOMIC_CARD_SCHEMA["properties"]["skip_reason"]["type"] == [
        "string",
        "null",
    ]
    assert len(ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT) < 150
    relation_schema = ATOMIC_CARD_SCHEMA["properties"]["relations"]["items"]
    assert set(relation_schema["properties"]) == {
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "basis",
        "relation_evidence_refs",
    }
    probe_plan_schema = ATOMIC_RELATION_PROBE_SCHEMA["properties"]["probe_plans"]["items"]
    assert probe_plan_schema["properties"]["relation_probes"]["maxItems"] == 2
    assert "same_event" not in probe_plan_schema["properties"]["relation_probes"]["items"][
        "properties"
    ]["role"]["enum"]
    assert set(ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA["properties"]) == {
        "event_groups",
        "keep_separate",
        "direct_links",
        "open_slots",
    }
    topology_unit_schema = ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA["properties"]["event_groups"][
        "items"
    ]
    assert set(topology_unit_schema["properties"]) == {
        "group_id",
        "evidence_refs",
        "focus",
    }
    assert "summary" not in json.dumps(
        ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA,
        ensure_ascii=False,
    )
    assert "禁止摘抄或改写原文" in ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT
    assert "不生成 Card 清单或 Card 文案" in ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT


def test_extractor_can_configure_card_and_probe_thinking_independently() -> None:
    extractor = AtomicCognitiveCardExtractor(
        llm=object(),
        model="test",
        thinking_type="enabled",
        relation_probe_thinking_type="disabled",
    )

    assert extractor._provider_options(extractor._card_thinking_type) == {
        "thinking_type": "enabled",
        "reasoning_effort": "medium",
        "inject_json_schema_instruction": False,
    }
    assert extractor._provider_options(extractor._probe_thinking_type) == {
        "thinking_type": "disabled",
        "inject_json_schema_instruction": False,
    }

    with pytest.raises(ValueError, match="thinking_type"):
        AtomicCognitiveCardExtractor(
            llm=object(),
            model="test",
            thinking_type="invalid",
        )
    with pytest.raises(ValueError, match="relation_probe_thinking_type"):
        AtomicCognitiveCardExtractor(
            llm=object(),
            model="test",
            relation_probe_thinking_type="invalid",
        )


def test_extractor_can_isolate_prompt_experiment_profiles() -> None:
    first = AtomicCognitiveCardExtractor(
        llm=object(),
        model="test",
        system_prompt="system variant one",
        relation_probe_followup_prompt="probe variant one",
        prompt_profile="variant_one",
    )
    second = AtomicCognitiveCardExtractor(
        llm=object(),
        model="test",
        system_prompt="system variant two",
        relation_probe_followup_prompt="probe variant two",
        prompt_profile="variant_two",
    )

    assert first._system_prompt == "system variant one"
    assert first._relation_probe_followup_prompt == "probe variant one"
    assert first._prompt_profile == "variant_one"
    assert first._prompt_fingerprint != second._prompt_fingerprint
    assert first._prefix_warm_scope("test") != second._prefix_warm_scope("test")


def test_extractor_routes_simple_and_complex_inputs_without_llm_classifier(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MODEL", "flash-test")
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_COMPLEX_MODEL", "pro-test")
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MAX_SPANS", 8)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MAX_CHARS", 2500)
    extractor = AtomicCognitiveCardExtractor(llm=object())

    simple = extractor._select_model_route(span_count=8, text_chars=2500)
    complex_by_spans = extractor._select_model_route(span_count=9, text_chars=100)
    complex_by_chars = extractor._select_model_route(span_count=2, text_chars=2501)

    assert (simple.model, simple.tier) == ("flash-test", "simple")
    assert (complex_by_spans.model, complex_by_spans.tier) == (
        "pro-test",
        "complex",
    )
    assert (complex_by_chars.model, complex_by_chars.tier) == (
        "pro-test",
        "complex",
    )


def test_extractor_explicit_model_disables_dynamic_routing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MODEL", "flash-test")
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_COMPLEX_MODEL", "pro-test")
    extractor = AtomicCognitiveCardExtractor(llm=object(), model="manual-test")

    route = extractor._select_model_route(span_count=100, text_chars=10000)

    assert (route.model, route.tier) == ("manual-test", "explicit_override")


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
    def __init__(
        self,
        outputs: list[object],
        *,
        probe_outputs: list[object] | None = None,
        topology_outputs: list[object] | None = None,
    ):
        self.outputs = list(outputs)
        self.probe_outputs = list(probe_outputs or [])
        self.topology_outputs = list(topology_outputs or [])
        self.requests = []
        self.repairs = []

    async def generate(self, request):
        self.requests.append(request)
        if request.metadata.get("task") == "kg_atomic_evidence_topology":
            return self._response(self.topology_outputs.pop(0))
        if request.metadata.get("task") == "kg_relation_probe":
            if self.probe_outputs:
                return self._response(self.probe_outputs.pop(0))
            payload = json.loads(request.messages[-2]["content"])
            return self._response(_probe_output(len(payload["cards"])))
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
        self.started_tasks: list[str] = []

    async def generate(self, request):
        self.started_after_first_done.append(self.first_done.is_set())
        self.started_tasks.append(str(request.metadata.get("task") or ""))
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
        self.locks = {}

    def exists(self, key):
        return int(key in self.values)

    def setex(self, key, _ttl, value):
        self.values[key] = value
        return True

    def lock(self, key, **_kwargs):
        return _RedisLock(self.locks.setdefault(key, threading.Lock()))


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
    assert [len(block.parts) for block in blocks] == [1, 1, 1, 2]
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
    assert prompt_lines[2].startswith("[s0002]财联社7月13日电，据Omdia，")
    assert "随着存储成本持续上涨" in prompt_lines[2]
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


def test_span_segmenter_only_splits_oversized_sentences() -> None:
    clauses = [
        f"第{index}个分句描述同一项长期事实并补充必要上下文"
        for index in range(1, 18)
    ]
    text = "，".join(clauses) + "。"

    blocks = StableSpanSegmenter().segment_blocks(text)
    spans = [part for block in blocks for part in block.parts]

    assert len(blocks) == 1
    assert len(spans) > 1
    assert all(len(span.text) <= StableSpanSegmenter._MAX_SPAN_CHARS for span in spans)
    assert "".join(span.text for span in spans) == text


def test_atomic_card_builds_stable_id_independent_of_ref_gaps() -> None:
    chunk = _chunk("第一句。第二句。第三句。")
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
    item = _card_item()
    probe_items = [
        {
            "role": "upstream",
            "query": "甲公司决定收购乙公司的直接战略或治理动作。",
        },
        {
            "role": "contradiction",
            "query": "甲公司在同一交易范围内否认或取消收购乙公司。",
        },
    ]

    card = replace(
        atomic_card_from_llm_item(
            chunk,
            item,
            spans=StableSpanSegmenter().segment(chunk.content),
        ),
        relation_probes=relation_probes_from_llm_items(probe_items),
    )

    assert [probe.as_dict() for probe in card.relation_probes] == probe_items
    assert [probe.as_dict() for probe in card.manifest().relation_probes] == probe_items


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
    }

    card = atomic_card_from_llm_item(
        chunk,
        item,
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    assert "2026年5月" in card.summary


@pytest.mark.asyncio
async def test_extractor_generates_cards_and_relations_then_probes_as_follow_up(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("甲公司发布公告，拟收购乙公司。丙公司宣布终止丁项目。")
    output = _extraction_output(
        [
            {
                **_card_item(summary="甲公司拟收购乙公司。", refs=["s0001"]),
            },
            {
                "summary": "丙公司宣布终止丁项目。",
                "focus_evidence_refs": ["s0002"],
            },
        ]
    )
    probe_output = _probe_output(
        2,
        {
            "c1": [
                {
                    "role": "upstream",
                    "query": "甲公司决定收购乙公司的直接战略动作。",
                }
            ]
        },
    )
    llm = _LLM([output], probe_outputs=[probe_output])

    cards = await AtomicCognitiveCardExtractor(
        llm=llm,
        model="test",
        provider="aliyun",
        concurrency=1,
    ).extract([chunk])

    assert len(cards) == 2
    assert len(llm.requests) == 2
    assert len(llm.repairs) == 0
    prompt_input = llm.requests[0].messages[1]["content"]
    assert prompt_input == (
        "published_at=2026-07-11T09:00:00+08:00\n"
        "[s0001]甲公司发布公告，拟收购乙公司。\n"
        "[s0002]丙公司宣布终止丁项目。"
    )
    assert chunk.chunk_id not in prompt_input
    assert "chunk_text" not in prompt_input
    assert "source_title" not in prompt_input
    assert llm.requests[0].messages[0]["content"] == ATOMIC_CARD_SYSTEM_PROMPT
    assert "published_at" in llm.requests[0].messages[0]["content"]
    assert "后续 user 明确切换到 Relation Probe 阶段" in llm.requests[0].messages[0]["content"]
    assert "同 Chunk Relation" in llm.requests[0].messages[0]["content"]
    card_schema = llm.requests[0].json_schema["properties"]["cards"]["items"]
    assert "relation_probes" not in card_schema["properties"]
    assert "relations" in llm.requests[0].json_schema["properties"]
    assert "2026-07-11T09:00:00+08:00" not in llm.requests[0].messages[0]["content"]
    assert llm.requests[0].metadata["chunk_id"] == chunk.chunk_id
    assert llm.requests[0].provider_options == {
        "thinking_type": "disabled",
        "inject_json_schema_instruction": False,
    }
    assert llm.requests[0].provider == "aliyun"
    assert llm.requests[0].metadata["_cache_key_metadata"]["generator_version"].startswith(
        "atomic_card_extractor_"
    )
    probe_request = llm.requests[1]
    assert probe_request.metadata["task"] == "kg_relation_probe"
    assert probe_request.model == llm.requests[0].model == "test"
    assert probe_request.provider == "aliyun"
    assert len(probe_request.messages) == 4
    assert probe_request.messages[:2] == llm.requests[0].messages
    assert probe_request.messages[2] == {
        "role": "assistant",
        "content": json.dumps(output, ensure_ascii=False),
    }
    assert probe_request.messages[3] == {
        "role": "user",
        "content": ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT,
    }
    assert '"cards":' not in probe_request.messages[3]["content"]
    assert "甲公司拟收购乙公司" not in probe_request.messages[3]["content"]
    assert "published_at=2026-07-11T09:00:00+08:00" not in probe_request.messages[3]["content"]
    assert probe_request.json_schema == ATOMIC_RELATION_PROBE_SCHEMA
    assert [probe.role for probe in cards[0].relation_probes] == ["upstream"]
    assert cards[1].relation_probes == []


@pytest.mark.asyncio
async def test_extractor_can_preplan_compact_topology_in_same_conversation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk()
    topology = _topology_output()
    card_output = _extraction_output(
        [
            _card_item(summary="甲公司拟收购乙公司。", refs=["s0001"]),
            _card_item(summary="该交易尚需监管机构批准。", refs=["s0002"]),
        ],
        relations=[
            {
                "source_card_id": "c2",
                "target_card_id": "c1",
                "relation_kind": "constraint",
                "basis": "监管批准是甲公司收购乙公司的前置约束。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )
    probe_output = _probe_output(
        2,
        {
            "c1": [
                {
                    "role": "upstream",
                    "query": "甲公司决定收购乙公司的已发生直接原因",
                }
            ]
        },
    )
    probe_output["accepted_relation_pairs"] = [
        {"source_card_id": "c2", "target_card_id": "c1"}
    ]
    llm = _LLM(
        [card_output],
        topology_outputs=[topology],
        probe_outputs=[probe_output],
    )

    result = (
        await AtomicCognitiveCardExtractor(
            llm=llm,
            model="test",
            thinking_type="disabled",
            relation_probe_thinking_type="disabled",
            evidence_topology_preplan=True,
        ).extract_with_diagnostics([chunk])
    )[0]

    assert [request.metadata["task"] for request in llm.requests] == [
        "kg_atomic_evidence_topology",
        "kg_cognitive_card",
        "kg_relation_probe",
    ]
    topology_request, card_request, probe_request = llm.requests
    assert topology_request.messages[0]["content"] == ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT
    assert topology_request.messages[-1]["content"] == ATOMIC_EVIDENCE_TOPOLOGY_REQUEST
    assert topology_request.json_schema == ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA
    assert card_request.messages[:3] == topology_request.messages
    assert json.loads(card_request.messages[3]["content"]) == {
        "keep_separate": topology["keep_separate"],
        "direct_links": topology["direct_links"],
    }
    assert card_request.messages[4]["content"] == ATOMIC_CARD_FROM_TOPOLOGY_FOLLOWUP_PROMPT
    assert probe_request.messages[:2] == topology_request.messages[:2]
    assert len(probe_request.messages) == 3
    assert probe_request.messages[2]["role"] == "user"
    assert probe_request.messages[2]["content"].startswith(
        ATOMIC_RELATION_PROBE_FROM_TOPOLOGY_FOLLOWUP_PROMPT
    )
    review_payload = json.loads(probe_request.messages[2]["content"].splitlines()[-1])
    assert review_payload["cards"] == card_output["cards"]
    assert review_payload["relation_candidates"] == [
        {
            "source_card_id": "c2",
            "target_card_id": "c1",
            "relation_kind": "constraint",
            "relation_evidence_refs": ["s0001", "s0002"],
        }
    ]
    assert all(
        "basis" not in item for item in review_payload["relation_candidates"]
    )
    assert review_payload["direct_links"] == topology["direct_links"]
    assert review_payload["open_slots"] == topology["open_slots"]
    assert probe_request.json_schema == ATOMIC_TOPOLOGY_RELATION_PROBE_SCHEMA
    assert result.evidence_topology == topology
    assert set(result.llm_stage_usage) == {
        "evidence_topology",
        "cards_and_relations",
        "relation_probes",
    }
    assert len(result.cards) == 2
    assert len(result.relations) == 1
    assert [probe.role for probe in result.cards[0].relation_probes] == ["upstream"]


@pytest.mark.asyncio
async def test_preplan_only_passes_validated_topology_to_followup(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk()
    topology = _topology_output()
    topology["direct_links"] = [
        {
            "source_evidence_refs": ["s0001"],
            "target_evidence_refs": ["s0002"],
            "link_evidence_refs": ["s0001"],
            "link_statement": "甲公司拟收购乙公司。",
            "source_mention": "甲公司拟收购乙公司",
            "relation_cue": "导致",
            "target_mention": "监管机构批准",
            "relation_kind": "causal_influence",
        }
    ]
    card_output = _extraction_output(
        [
            _card_item(summary="甲公司拟收购乙公司。", refs=["s0001"]),
            _card_item(summary="该交易尚需监管机构批准。", refs=["s0002"]),
        ]
    )
    probe_output = _probe_output(2)
    probe_output["accepted_relation_pairs"] = []
    llm = _LLM(
        [card_output],
        topology_outputs=[topology],
        probe_outputs=[probe_output],
    )

    result = (
        await AtomicCognitiveCardExtractor(
            llm=llm,
            model="test",
            thinking_type="disabled",
            relation_probe_thinking_type="disabled",
            evidence_topology_preplan=True,
        ).extract_with_diagnostics([chunk])
    )[0]

    card_topology = json.loads(llm.requests[1].messages[3]["content"])
    assert topology["direct_links"]
    assert card_topology["direct_links"] == []
    assert result.evidence_topology["direct_links"] == []


@pytest.mark.asyncio
async def test_dynamic_route_is_selected_once_and_shared_by_card_and_probe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MODEL", "flash-test")
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_COMPLEX_MODEL", "pro-test")
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MAX_SPANS", 8)
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_SIMPLE_MAX_CHARS", 2500)
    simple_chunk = _chunk("简单事实甲。简单事实乙。")
    complex_content = "".join(f"第{index}个完整事实。" for index in range(1, 10))
    complex_chunk = _chunk(complex_content).model_copy(
        update={
            "chunk_id": "kg_chunk:kg_ev:financial:news_articles:test:2:0",
            "evidence_id": "kg_ev:financial:news_articles:test:2",
            "text_hash": "hash-2",
            "payload": {
                **simple_chunk.payload,
                "source_id": "ft_news:2",
            },
        }
    )
    llm = _LLM(
        [
            _extraction_output([_card_item()]),
            _extraction_output([_card_item()]),
        ]
    )
    extractor = AtomicCognitiveCardExtractor(llm=llm, concurrency=1)

    results = await extractor.extract_with_diagnostics([simple_chunk, complex_chunk])

    assert [request.model for request in llm.requests] == [
        "flash-test",
        "flash-test",
        "pro-test",
        "pro-test",
    ]
    assert [(result.selected_model, result.model_route) for result in results] == [
        ("flash-test", "simple"),
        ("pro-test", "complex"),
    ]


def test_prefix_warm_scope_is_isolated_by_model_provider_and_thinking() -> None:
    auto = AtomicCognitiveCardExtractor(
        llm=object(),
        thinking_type="disabled",
    )
    explicit_provider = AtomicCognitiveCardExtractor(
        llm=object(),
        provider="aliyun",
        thinking_type="disabled",
    )
    thinking = AtomicCognitiveCardExtractor(
        llm=object(),
        thinking_type="enabled",
    )

    scopes = {
        auto._prefix_warm_scope("deepseek-v4-flash"),
        auto._prefix_warm_scope("deepseek-v4-pro"),
        explicit_provider._prefix_warm_scope("deepseek-v4-flash"),
        thinking._prefix_warm_scope("deepseek-v4-flash"),
    }

    assert len(scopes) == 4


@pytest.mark.asyncio
async def test_extractor_maps_intra_chunk_relations_to_final_card_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。受强降雨影响，多地停课。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002"]),
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "causal_influence",
                "basis": "原文明确使用“受强降雨影响”连接降雨与停课。",
                "relation_evidence_refs": ["[s0001]", "[s0002]"],
            }
        ],
    )

    llm = _LLM([output])
    results = await AtomicCognitiveCardExtractor(
        llm=llm,
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
    assert relation.target_evidence_refs == ["s0002"]
    assert relation.relation_evidence_refs == [
        {"chunk_id": chunk.chunk_id, "refs": ["s0001", "s0002"]}
    ]
    probe_messages = llm.requests[1].messages
    assert probe_messages[:2] == llm.requests[0].messages
    prior_output = json.loads(probe_messages[2]["content"])
    assert prior_output["cards"][0]["focus_evidence_refs"] == ["s0001"]
    assert prior_output["relations"] == output["relations"]
    assert probe_messages[3]["content"] == ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT


def test_probe_validation_requires_complete_ordered_card_coverage() -> None:
    chunk = _chunk("甲公司发布公告。乙公司发布公告。")
    validated = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        _extraction_output(
            [
                _card_item(summary="甲公司发布公告。", refs=["s0001"]),
                _card_item(summary="乙公司发布公告。", refs=["s0002"]),
            ]
        ),
    )

    with pytest.raises(ValueError, match="完整覆盖 Cards"):
        AtomicCognitiveCardExtractor._validate_probe_response(
            validated.cards_by_local_id,
            _probe_output(1),
        )


def test_probe_review_discards_pairs_not_present_in_relation_candidates() -> None:
    chunk = _chunk("甲公司发布公告。乙公司发布公告。丙公司发布公告。")
    validated_cards = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        _extraction_output(
            [
                _card_item(summary="甲公司发布公告。", refs=["s0001"]),
                _card_item(summary="乙公司发布公告。", refs=["s0002"]),
                _card_item(summary="丙公司发布公告。", refs=["s0003"]),
            ]
        ),
    )
    probe_output = _probe_output(3)
    probe_output["accepted_relation_pairs"] = [
        {"source_card_id": "c1", "target_card_id": "c2"},
        {"source_card_id": "c2", "target_card_id": "c3"},
    ]

    validated_probes = AtomicCognitiveCardExtractor._validate_probe_response(
        validated_cards.cards_by_local_id,
        probe_output,
        review_relations=True,
        allowed_local_relation_pairs=frozenset({("c1", "c2")}),
    )

    assert validated_probes.accepted_relation_pairs == frozenset(
        {
            tuple(
                sorted(
                    (
                        validated_cards.cards_by_local_id["c1"].cognitive_card_id,
                        validated_cards.cards_by_local_id["c2"].cognitive_card_id,
                    )
                )
            )
        }
    )


@pytest.mark.asyncio
async def test_intra_chunk_relation_sync_marks_missing_pairs_as_no_relation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。多地因强降雨停课。甲公司发布年度报告。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002"]),
            _card_item(summary="甲公司发布年度报告。", refs=["s0003"]),
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "causal_influence",
                "basis": "原文明确说明停课由强降雨引起。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )
    results = await AtomicCognitiveCardExtractor(
        llm=_LLM([output]),
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
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "same_event",
                "basis": "属于同一场降雨",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )

    validated = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_output,
    )

    assert validated.relations == []
    assert validated.discarded_relation_count == 1


def test_card_validation_keeps_all_valid_cards_beyond_sixteen() -> None:
    facts = [f"甲公司披露第{index}项独立事实。" for index in range(1, 18)]
    chunk = _chunk("".join(facts))
    spans = StableSpanSegmenter().segment(chunk.content)
    output = _extraction_output(
        [
            _card_item(summary=fact, refs=[f"s{index:04d}"])
            for index, fact in enumerate(facts, start=1)
        ]
    )

    validated = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        spans,
        output,
    )

    assert len(validated.cards) == 17
    assert set(validated.cards_by_local_id) == {f"c{index}" for index in range(1, 18)}
    assert validated.discarded_card_count == 0
    assert validated.issues == ()


def test_card_validation_rejects_non_contiguous_local_ids_without_partial_results() -> None:
    chunk = _chunk("甲公司披露第一项事实。甲公司披露第二项事实。")
    spans = StableSpanSegmenter().segment(chunk.content)
    output = _extraction_output(
        [
            _card_item(summary="甲公司披露第一项事实。", refs=["s0001"]),
            _card_item(summary="甲公司披露第二项事实。", refs=["s0002"]),
        ]
    )
    output["cards"][1]["local_card_id"] = "c2a"

    with pytest.raises(ValueError, match=r"card\[2\].*local_card_id 应为 c2"):
        AtomicCognitiveCardExtractor._validate_card_response(chunk, spans, output)


def test_intra_chunk_fast_path_discards_inferred_contract_without_losing_cards() -> None:
    chunk = _chunk("员工离开甲公司。员工随后加入乙公司。")
    card_output = _extraction_output(
        [
            _card_item(summary="员工离开甲公司。", refs=["s0001"]),
            _card_item(summary="员工加入乙公司。", refs=["s0002"]),
        ],
        relations=[
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

    validated = AtomicCognitiveCardExtractor._validate_card_response(
        chunk,
        StableSpanSegmenter().segment(chunk.content),
        card_output,
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
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "relation_kind": "same_event",
                "basis": "同一场降雨。",
                "relation_evidence_refs": ["s0001", "s0002"],
            }
        ],
    )
    llm = _LLM([output])

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
    card_start_flags = [
        flag
        for flag, task in zip(llm.started_after_first_done, llm.started_tasks, strict=True)
        if task == "kg_cognitive_card"
    ]
    assert card_start_flags == [False, True, True]


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
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_extractor_repairs_invalid_output_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    valid = _extraction_output([_card_item()])
    llm = _LLM([_extraction_output([]), valid])

    results = await AtomicCognitiveCardExtractor(llm=llm, model="test").extract_with_diagnostics([_chunk()])

    assert len(results[0].cards) == 1
    assert results[0].repaired is True
    assert results[0].repair_attempted is True
    assert len(llm.requests) == 2
    assert len(llm.repairs) == 1
    assert json.loads(llm.requests[1].messages[2]["content"]) == valid
    assert llm.requests[1].messages[3]["content"] == ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT


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
        ],
        relations=[
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
            llm=_LLM([output]),
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
