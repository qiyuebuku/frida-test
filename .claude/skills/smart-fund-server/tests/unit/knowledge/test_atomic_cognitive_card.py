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
)
from src.domain.knowledge.atomic_cognitive_card import (
    StableSpanSegmenter,
    atomic_card_document,
    atomic_card_from_llm_item,
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
        "factual_anchors": {
            "actors": ["甲公司"],
            "action": "拟收购",
            "objects": ["乙公司"],
            "event_time": "",
            "explicit_causes": [],
            "explicit_effects": ["尚需监管机构批准"],
        },
        "relation_probes": [
            {"role": "same_event", "query": "甲公司收购乙公司的后续审批或交易进展"},
            {"role": "contradiction", "query": "甲公司终止、取消或未获批准收购乙公司的材料"},
        ],
    }


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


def test_span_segmenter_is_stable_and_preserves_offsets() -> None:
    text = "第一句。\n第二句！第三句没有句号"
    segmenter = StableSpanSegmenter()

    first = segmenter.segment(text)
    second = segmenter.segment(text)

    assert first == second
    assert [span.ref for span in first] == ["s0001", "s0002", "s0003"]
    assert [text[span.start_offset : span.end_offset] for span in first] == [span.text for span in first]


def test_atomic_card_builds_stable_id_independent_of_anchor_array_order() -> None:
    chunk = _chunk()
    spans = StableSpanSegmenter().segment(chunk.content)
    first = _card_item()
    second = _card_item()
    second["factual_anchors"]["action"] = "收购"
    second["focus_evidence_refs"] = ["s0001", "s0003"]

    card_a = atomic_card_from_llm_item(chunk, first, spans=spans)
    card_b = atomic_card_from_llm_item(chunk, second, spans=spans)

    assert card_a.cognitive_card_id == card_b.cognitive_card_id
    assert card_a.summary
    assert card_a.focus_span_offsets[0]["ref"] == "s0001"


def test_atomic_card_rejects_unknown_span_ref() -> None:
    chunk = _chunk()
    item = _card_item(refs=["s9999"])

    with pytest.raises(ValueError, match="不存在的 Span Ref"):
        atomic_card_from_llm_item(chunk, item, spans=StableSpanSegmenter().segment(chunk.content))


def test_atomic_card_rejects_ungrounded_anchor_number() -> None:
    chunk = _chunk()
    item = _card_item()
    item["factual_anchors"]["explicit_causes"] = ["行业投资增长100%"]

    with pytest.raises(ValueError, match="无法支持的数字"):
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
        "factual_anchors": {
            "actors": ["甲公司"],
            "action": "增长",
            "objects": ["收入"],
            "event_time": "",
            "explicit_causes": [],
            "explicit_effects": [],
        },
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
        "factual_anchors": {
            "actors": ["甲公司"],
            "action": "发布",
            "objects": ["新产品"],
            "event_time": "今年5月",
            "explicit_causes": [],
            "explicit_effects": [],
        },
        "relation_probes": [],
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
    output = {
        "cards": [
            {
                **_card_item(summary="甲公司拟收购乙公司。", refs=["s0001", "s0002"]),
                "factual_anchors": {
                    "actors": ["甲公司"],
                    "action": "拟收购",
                    "objects": ["乙公司"],
                    "event_time": "",
                    "explicit_causes": [],
                    "explicit_effects": [],
                },
            },
            {
                "summary": "丙公司宣布终止丁项目。",
                "focus_evidence_refs": ["s0003"],
                "factual_anchors": {
                    "actors": ["丙公司"],
                    "action": "宣布终止",
                    "objects": ["丁项目"],
                    "event_time": "",
                    "explicit_causes": [],
                    "explicit_effects": [],
                },
                "relation_probes": [],
            },
        ],
        "skip_reason": "",
    }
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
    assert prompt_payload["source_published_at"] not in llm.requests[0].system_prompt
    assert llm.requests[0].provider_options == {"reasoning_effort": "high"}
    assert llm.requests[0].metadata["_cache_key_metadata"]["generator_version"].startswith(
        "atomic_card_extractor_"
    )


@pytest.mark.asyncio
async def test_extractor_allows_zero_cards_with_reason(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([{"cards": [], "skip_reason": "只有网页导航信息"}])

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
    llm = _DelayedLLM([{"cards": [_card_item()], "skip_reason": ""} for _ in chunks])
    extractor = AtomicCognitiveCardExtractor(llm=llm, model="test", concurrency=3)
    extractor._redis = _Redis()

    cards = await extractor.extract(chunks)

    assert len(cards) == 3
    assert llm.started_after_first_done[0] is False
    assert llm.started_after_first_done[1:] == [True, True]


@pytest.mark.asyncio
async def test_extractor_repairs_invalid_output_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    valid = {"cards": [_card_item()], "skip_reason": ""}
    llm = _LLM([{"cards": [], "skip_reason": ""}, valid])

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
            {"cards": [], "skip_reason": ""},
            {"cards": [{"summary": "仍然缺少必要字段"}], "skip_reason": ""},
        ]
    )

    with pytest.raises(RuntimeError, match="修复后仍未通过校验"):
        await AtomicCognitiveCardExtractor(llm=llm, model="test").extract([_chunk()])


def test_milvus_document_contains_readable_card_and_complete_payload() -> None:
    chunk = _chunk()
    card = atomic_card_from_llm_item(
        chunk,
        _card_item(),
        spans=StableSpanSegmenter().segment(chunk.content),
    )

    document = atomic_card_document(card)

    assert document.document_id == card.cognitive_card_id
    assert document.collection_role == "cognitive_card"
    assert card.summary in document.text
    assert document.metadata["summary"] == card.summary
    assert document.metadata["relation_probes"][0]["role"] == "same_event"
    assert document.metadata["focus_span_offsets"][0]["start_offset"] == 0


@pytest.mark.asyncio
async def test_stage_stops_at_cards_ready_and_cleans_stale_targets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([{"cards": [_card_item()], "skip_reason": ""}])

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
    service = AtomicCognitiveCardStageService(
        repository=repository,
        semantic_retriever=retriever,
        extractor=AtomicCognitiveCardExtractor(llm=llm, model="test"),
    )

    result = await service.refresh(
        adapter_name="financial",
        target="test",
        kg_version="v1",
        changed_chunks=[_chunk()],
    )

    assert result.status == "cards_ready"
    assert result.diagnostics["assignment_executed"] is False
    assert result.diagnostics["milvus_documents_written"] == 1
    assert retriever.deletes[0]["target_ids"] == ["kg_cognitive_card:stale"]


@pytest.mark.asyncio
async def test_stage_zero_card_replacement_deletes_old_pg_and_milvus_cards(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS", 0)
    llm = _LLM([{"cards": [], "skip_reason": "没有可独立验证的事实"}])

    class Repository:
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
    assert retriever.deleted == ["kg_cognitive_card:old"]


def test_pg_manifest_schema_does_not_store_readable_card_text() -> None:
    columns = set(KnowledgeCognitiveCard.__table__.columns.keys())

    assert {"focus_evidence_refs", "focus_span_offsets", "factual_anchors", "generator_version"} <= columns
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
        assert "generator_version character varying(96)" in table_block
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
        documents=[atomic_card_document(card)],
        kg_version="v1",
    )

    assert count == 1
    document = store.calls[0]["documents_by_role"]["cognitive_card"][0]
    assert document.target_id == card.cognitive_card_id
    assert document.metadata["summary"] == card.summary
    assert document.metadata["relation_probes"] == [probe.as_dict() for probe in card.relation_probes]
