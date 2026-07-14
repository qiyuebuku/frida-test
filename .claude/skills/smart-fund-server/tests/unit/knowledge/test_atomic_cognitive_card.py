"""原子 Cognitive Card 第一阶段测试。"""

from __future__ import annotations

import json
import inspect
import asyncio
import threading
from pathlib import Path

import pytest

from src.application.services.atomic_cognitive_card_service import (
    ATOMIC_CARD_SYSTEM_PROMPT,
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
        "focus_evidence_refs": refs or ["s0001", "s0002", "s0003"],
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
    assert ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION == "atomic_card_extractor_v38"
    assert "最小但完整" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不等于句子中的语法动词" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "按核心谓词确定 Card 边界" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "可以分别判断真假”不是拆分的充分条件" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不要把一个完整观测拆成“变化事实”和“观测值事实”" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不同核心谓词形成独立事实端点时才拆分" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不能以 relations=[] 结束这种拆分" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "先完整输出 cards，再输出 relations" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不得保留两个 Card 后再用 same_event" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "同一主体、同一来源、同一句话" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不得把原文未写明的诉讼触发" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "原要求”对比最终结果时，应合并成一张变化 Card" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "漏掉一条弱关系好于制造一条伪关系" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "需要模型补充连接机制的 inferred 关系一律省略" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "隐含因果、合理前提、可能影响" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "不在正文中列举 s0001 等 Ref 标签" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "则不要创建该关系" in ATOMIC_CARD_SYSTEM_PROMPT
    assert "若删除其中一个后另一个仍能被独立理解和验证" not in ATOMIC_CARD_SYSTEM_PROMPT


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
    assert [text[span.start_offset : span.end_offset] for span in first] == [span.text for span in first]


def test_atomic_card_builds_stable_id_independent_of_ref_gaps() -> None:
    chunk = _chunk()
    spans = StableSpanSegmenter().segment(chunk.content)
    first = _card_item()
    second = _card_item()
    second["focus_evidence_refs"] = ["s0001", "s0003"]

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


def test_atomic_card_rejects_summary_number_hallucination() -> None:
    chunk = _chunk()
    item = _card_item(summary="甲公司拟以100亿元收购乙公司。")

    with pytest.raises(ValueError, match="未出现的数字"):
        atomic_card_from_llm_item(chunk, item, spans=StableSpanSegmenter().segment(chunk.content))


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
async def test_extractor_generates_multiple_cards_in_one_llm_call(monkeypatch) -> None:
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
            },
        ]
    )
    llm = _LLM([output])

    cards = await AtomicCognitiveCardExtractor(llm=llm, model="test", concurrency=1).extract([chunk])

    assert len(cards) == 2
    assert len(llm.requests) == 1
    assert len(llm.repairs) == 0
    prompt_payload = json.loads(llm.requests[0].prompt)
    assert "chunk_text" not in prompt_payload
    assert "time_grounding" not in prompt_payload
    assert "source_title" not in prompt_payload
    assert prompt_payload["spans"] == [
        {"ref": "s0001", "text": "甲公司发布公告，"},
        {"ref": "s0002", "text": "拟收购乙公司。"},
        {"ref": "s0003", "text": "丙公司宣布终止丁项目。"},
    ]
    assert llm.requests[0].system_prompt == ATOMIC_CARD_SYSTEM_PROMPT
    assert "source_published_at" in llm.requests[0].system_prompt
    assert "Relation Probe" not in llm.requests[0].system_prompt
    assert "候选历史事件" not in llm.requests[0].system_prompt
    card_schema = llm.requests[0].json_schema["properties"]["cards"]["items"]
    assert "relation_probes" not in card_schema["properties"]
    assert prompt_payload["source_published_at"] not in llm.requests[0].system_prompt
    assert llm.requests[0].provider_options == {"reasoning_effort": "medium"}
    assert llm.requests[0].metadata["_cache_key_metadata"]["generator_version"].startswith(
        "atomic_card_extractor_"
    )


@pytest.mark.asyncio
async def test_extractor_maps_intra_chunk_relations_to_final_card_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    chunk = _chunk("辽宁出现强降雨。受强降雨影响，多地停课。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="多地因强降雨停课。", refs=["s0002", "s0003"]),
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "decision_class": "observed",
                "relation_kind": "causal_influence",
                "relation_type": "强降雨直接导致停课",
                "direction": "强降雨事实指向停课事实",
                "basis": "原文明确使用“受强降雨影响”连接降雨与停课。",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0002", "s0003"],
                "inference_mechanism": "",
                "confidence": 0.98,
            }
        ],
    )

    results = await AtomicCognitiveCardExtractor(
        llm=_LLM([output]),
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
                "decision_class": "observed",
                "relation_kind": "causal_influence",
                "relation_type": "强降雨直接导致停课",
                "direction": "强降雨事实指向停课事实",
                "basis": "原文明确说明停课由强降雨引起。",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0002"],
                "inference_mechanism": "",
                "confidence": 0.98,
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


def test_intra_chunk_relation_rejects_same_event_instead_of_hiding_duplicate_cards() -> None:
    chunk = _chunk("辽宁出现强降雨。沈阳出现特大暴雨。")
    output = _extraction_output(
        [
            _card_item(summary="辽宁出现强降雨。", refs=["s0001"]),
            _card_item(summary="沈阳出现特大暴雨。", refs=["s0002"]),
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "decision_class": "observed",
                "relation_kind": "same_event",
                "relation_type": "同一事件",
                "direction": "对称",
                "basis": "属于同一场降雨",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0002"],
                "inference_mechanism": "",
                "confidence": 0.9,
            }
        ],
    )

    with pytest.raises(ValueError, match="relation_kind 非法"):
        AtomicCognitiveCardExtractor._validate_response(
            chunk,
            StableSpanSegmenter().segment(chunk.content),
            output,
        )


def test_intra_chunk_fast_path_rejects_inferred_relations() -> None:
    chunk = _chunk("员工离开甲公司。员工随后加入乙公司。")
    output = _extraction_output(
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
                "relation_type": "离职后入职",
                "direction": "从离开甲公司到加入乙公司",
                "basis": "员工离开甲公司后加入乙公司。",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0002"],
                "inference_mechanism": "根据叙述顺序推断时间先后。",
                "confidence": 0.8,
            }
        ],
    )

    with pytest.raises(ValueError, match="只允许 observed"):
        AtomicCognitiveCardExtractor._validate_response(
            chunk,
            StableSpanSegmenter().segment(chunk.content),
            output,
        )


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
                "relations": [],
                "skip_reason": "",
            },
        ]
    )

    with pytest.raises(RuntimeError, match="修复后仍未通过校验"):
        await AtomicCognitiveCardExtractor(llm=llm, model="test").extract([_chunk()])


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
            _card_item(summary="多地因强降雨停课。", refs=["s0002", "s0003"]),
        ],
        relations=[
            {
                "source_card_id": "c1",
                "target_card_id": "c2",
                "decision_class": "observed",
                "relation_kind": "causal_influence",
                "relation_type": "强降雨直接导致停课",
                "direction": "强降雨事实指向停课事实",
                "basis": "原文明确说明停课受强降雨影响。",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0002", "s0003"],
                "inference_mechanism": "",
                "confidence": 0.98,
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
        extractor=AtomicCognitiveCardExtractor(llm=_LLM([output]), model="test"),
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

    assert {"focus_evidence_refs", "focus_span_offsets", "generator_version"} <= columns
    assert "relation_probes" not in columns
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
        assert "relation_probes jsonb" not in table_block
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
    assert "relation_probes" not in values


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
