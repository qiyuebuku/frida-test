"""Relation Probe 双视图候选发现与原文核验测试。"""

from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from src.application.services.relation_discovery_service import (
    RelationDiscoveryService,
    _parse_verified_decision,
    _verification_schema,
)
from src.domain.knowledge.atomic_cognitive_card import CognitiveCardManifest
from src.domain.knowledge.relation_discovery import PairEvidencePackage, RelationRecallHit
from src.infrastructure.clients.reranker import RerankResponse, RerankResult
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.types import LLMProxyResponse
from src.infrastructure.vector_store.relation_candidate_store import RelationCardText


def _manifest(card_id: str, chunk_id: str) -> CognitiveCardManifest:
    return CognitiveCardManifest(
        cognitive_card_id=card_id,
        adapter_name="financial",
        source_type="news_articles",
        source_id=f"ft_news:{card_id[-1]}",
        evidence_id=f"ev:{card_id}",
        primary_chunk_id=chunk_id,
        chunk_ids=[chunk_id],
        chunk_index=0,
        focus_evidence_refs=["s0001"],
        focus_span_offsets=[{"ref": "s0001", "start_offset": 0, "end_offset": 8}],
        schema_version="atomic_cognitive_card_v5",
        generator_version="atomic_card_extractor_v38",
        status="active",
    )


class _Repository:
    def __init__(self, manifests: list[CognitiveCardManifest]) -> None:
        self.manifests = {item.cognitive_card_id: item for item in manifests}

    def list_atomic_cognitive_card_manifests_by_ids(
        self,
        _adapter_name,
        *,
        cognitive_card_ids,
        status="active",
    ):
        return [self.manifests[item] for item in cognitive_card_ids if item in self.manifests]


class _VectorStore:
    def __init__(self) -> None:
        self.manifests_to_chunk = {
            "card:1": "chunk:1",
            "card:2": "chunk:2",
            "card:3": "chunk:3",
        }
        self.summaries = {
            "card:1": RelationCardText(
                "card:1",
                "监管部门限制关键原材料出口。",
                {"source_published_at": "2026-07-10", "schema_version": "atomic_cognitive_card_v5", "status": "active"},
            ),
            "card:2": RelationCardText(
                "card:2",
                "下游制造企业原材料库存下降。",
                {"source_published_at": "2026-07-11", "schema_version": "atomic_cognitive_card_v5", "status": "active"},
            ),
            "card:3": RelationCardText(
                "card:3",
                "另一行业公司发布季度业绩。",
                {"source_published_at": "2026-07-11", "schema_version": "atomic_cognitive_card_v5", "status": "active"},
            ),
        }
        self.chunks = {
            "chunk:1": RelationCardText("chunk:1", "监管部门限制关键原材料出口。相关措施立即执行。", {}),
            "chunk:2": RelationCardText("chunk:2", "下游制造企业库存下降并开始减产。供应商交付减少。", {}),
        }
        self.routes = []

    async def recall_routes(self, routes, **_kwargs):
        self.routes = routes
        return {
            route.route_id: [
                RelationRecallHit("card:2", "summary", 1, 0.8),
                RelationRecallHit("card:2", "focus_evidence", 1, 0.9),
                RelationRecallHit("card:3", "focus_evidence", 2, 0.6),
            ]
            for route in routes
        }

    async def get_summaries(self, card_ids, **_kwargs):
        return {item: self.summaries[item] for item in card_ids if item in self.summaries}

    async def get_focus_evidence(self, card_ids, **_kwargs):
        return {
            item: RelationCardText(
                item,
                self.chunks[self.manifests_to_chunk[item]].text,
                self.summaries[item].metadata,
            )
            for item in card_ids
            if item in self.manifests_to_chunk
        }

    async def get_chunks(self, chunk_ids, **_kwargs):
        return {item: self.chunks[item] for item in chunk_ids if item in self.chunks}


class _Reranker:
    def __init__(self) -> None:
        self.calls = []

    async def rerank(self, *, query, documents, top_n=None):
        self.calls.append({"query": query, "documents": list(documents), "top_n": top_n})
        return RerankResponse(
            model="test",
            results=[
                RerankResult(index=index, relevance_score=1.0 - index * 0.1, document=document)
                for index, document in enumerate(documents[:top_n])
            ],
            latency_ms=1,
            total_documents=len(documents),
        )


class _NegativeReranker:
    def __init__(self) -> None:
        self.calls = []

    async def rerank(self, *, query, documents, top_n=None):
        self.calls.append({"query": query, "documents": list(documents), "top_n": top_n})
        return RerankResponse(
            model="test",
            results=[
                RerankResult(index=index, relevance_score=-0.01 - index * 0.1, document=document)
                for index, document in enumerate(documents[:top_n])
            ],
            latency_ms=1,
            total_documents=len(documents),
        )


def _pair_package() -> PairEvidencePackage:
    return PairEvidencePackage(
        source_card_id="card:1",
        source_evidence_context=[{"text": "上游限制出口。", "evidence_ref": "s0001"}],
        source_focus_refs=["s0001"],
        source_published_at="2026-07-10",
        candidate_card_id="card:2",
        candidate_evidence_context=[{"text": "下游库存下降。", "evidence_ref": "s0001"}],
        candidate_focus_refs=["s0001"],
        candidate_published_at="2026-07-11",
    )


def test_no_relation_contract_only_outputs_decision_class() -> None:
    package = _pair_package()
    schema = _verification_schema(package)

    no_relation_branch = schema["oneOf"][0]
    assert no_relation_branch["required"] == ["decision_class"]
    assert set(no_relation_branch["properties"]) == {"decision_class"}

    decision = _parse_verified_decision({"decision_class": "no_relation"}, package)
    assert decision.as_dict() == {"decision_class": "no_relation"}

    with pytest.raises(ValueError, match="只能输出 decision_class"):
        _parse_verified_decision(
            {
                "decision_class": "no_relation",
                "source_evidence_refs": ["s0001"],
            },
            package,
        )


@dataclass
class _LLM:
    requests: list

    async def generate(self, request):
        self.requests.append(request)
        task = request.metadata["task"]
        if task == "kg_relation_candidate_screen":
            output = {
                "related_candidate_ids": ["card:2"],
            }
        else:
            output = {
                "source_card_id": "card:1",
                "target_card_id": "card:2",
                "decision_class": "observed",
                "relation_kind": "causal_influence",
                "relation_type": "supply_constraint_transmission",
                "direction": "card:1_to_card:2",
                "basis": "出口限制后，下游企业库存下降并减产。",
                "source_evidence_refs": ["s0001"],
                "target_evidence_refs": ["s0001"],
                "inference_mechanism": "",
                "confidence": 0.9,
            }
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


class _RelationWriter:
    def __init__(self):
        self.calls = []

    async def persist_verified_decisions(self, decisions, **kwargs):
        self.calls.append((list(decisions), kwargs))
        return {
            "changed_edge_ids": ["kg_card_relation:test"] if decisions else [],
            "graph_event_ids": ["event:test"] if decisions else [],
        }


@pytest.mark.asyncio
async def test_dual_view_recall_uses_summary_for_rerank_and_full_text_only_for_verification(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_RECALL_PER_VIEW", 50)
    monkeypatch.setattr(settings, "KG_RELATION_RERANK_TOP_N", 12)
    monkeypatch.setattr(settings, "KG_RELATION_MERGED_CANDIDATE_LIMIT", 20)
    monkeypatch.setattr(settings, "KG_RELATION_SCREEN_BATCH_SIZE", 20)
    monkeypatch.setattr(settings, "KG_RELATION_VERIFY_CONCURRENCY", 2)
    source = _manifest("card:1", "chunk:1")
    candidate = _manifest("card:2", "chunk:2")
    unrelated = _manifest("card:3", "chunk:3")
    vector_store = _VectorStore()
    reranker = _Reranker()
    llm = _LLM([])
    relation_writer = _RelationWriter()
    service = RelationDiscoveryService(
        repository=_Repository([source, candidate, unrelated]),
        vector_store=vector_store,
        reranker=reranker,
        llm=llm,
        relation_writer=relation_writer,
    )

    result = await service.discover_card_relations(["card:1"], target="test")

    assert result["observed"] == 1
    assert result["edge_persistence"]["changed_edge_ids"] == ["kg_card_relation:test"]
    assert len(relation_writer.calls) == 1
    assert "evaluation_details" not in result["card_diagnostics"][0]
    assert result["decisions"][0]["target_card_id"] == "card:2"
    assert len(vector_store.routes) == 1
    assert vector_store.routes[0].query == "监管部门限制关键原材料出口。"
    assert all(
        item.metadata["task"] != "kg_relation_probe_planning"
        for item in llm.requests
    )
    assert reranker.calls
    for call in reranker.calls:
        assert call["documents"] == [
            "下游制造企业原材料库存下降。",
            "另一行业公司发布季度业绩。",
        ]
        assert all("减产" not in document for document in call["documents"])

    screen_request = next(item for item in llm.requests if item.metadata["task"] == "kg_relation_candidate_screen")
    verify_request = next(item for item in llm.requests if item.metadata["task"] == "kg_relation_evidence_verify")
    assert "开始减产" not in screen_request.prompt
    assert "开始减产" in verify_request.prompt
    screen_payload = json.loads(screen_request.prompt)
    assert screen_payload["candidates"][1]["candidate_id"] == "card:3"
    candidate_payload = screen_payload["candidates"][0]
    assert candidate_payload == {
        "candidate_id": "card:2",
        "summary": "下游制造企业原材料库存下降。",
        "source_published_at": "2026-07-11",
    }
    for noisy_key in (
        "rerank_rank",
        "rerank_score",
        "recall_score",
        "recall_rank",
        "recall_views",
        "route_provenance",
        "relation_hypotheses",
    ):
        assert noisy_key not in screen_request.prompt
        assert noisy_key not in verify_request.prompt
    assert "共同具体驱动" in screen_request.system_prompt
    assert "不要因为内容重复就拒绝" in screen_request.system_prompt
    assert "不同互补属性" in screen_request.system_prompt
    assert "不同目标期间、不同预测区间或并列指标" in screen_request.system_prompt
    assert "跨市场或跨来源印证" in verify_request.system_prompt
    assert "双方可以分别提供方向、幅度、绝对水平" in verify_request.system_prompt
    assert "优先使用 same_event" in verify_request.system_prompt
    assert "分别描述不同目标期间、预测区间或并列指标，不构成时间进展" in verify_request.system_prompt
    assert "observed 关系不需要推理链" in verify_request.system_prompt
    assert "不得用“通常会”" in verify_request.system_prompt
    assert "X→A 且 X→B 只能证明共同驱动" in verify_request.system_prompt
    assert "只出现在某一侧未标记上下文中的信息不能参与建立关系" in verify_request.system_prompt
    assert "最小充分 ref 集合" in verify_request.system_prompt
    verify_payload = json.loads(verify_request.prompt)
    assert set(verify_payload) == {"source_card", "candidate_card"}
    assert verify_payload["source_card"] == {
        "card_id": "card:1",
        "source_published_at": "2026-07-10",
        "evidence_context": [
            {"text": "监管部门限制关键", "evidence_ref": "s0001"},
            {"text": "原材料出口。相关措施立即执行。", "evidence_ref": None},
        ],
    }
    assert verify_payload["candidate_card"] == {
        "card_id": "card:2",
        "source_published_at": "2026-07-11",
        "evidence_context": [
            {"text": "下游制造企业库存", "evidence_ref": "s0001"},
            {"text": "下降并开始减产。供应商交付减少。", "evidence_ref": None},
        ],
    }
    for removed_key in (
        "source_summary",
        "candidate_summary",
        "source_chunk_id",
        "candidate_chunk_id",
        "source_focus_refs",
        "candidate_focus_refs",
        "preliminary_role",
        "screening_basis",
    ):
        assert removed_key not in verify_request.prompt


@pytest.mark.asyncio
async def test_evaluation_mode_returns_stage_candidate_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_RERANK_TOP_N", 12)
    source = _manifest("card:1", "chunk:1")
    candidate = _manifest("card:2", "chunk:2")
    unrelated = _manifest("card:3", "chunk:3")
    result = await RelationDiscoveryService(
        repository=_Repository([source, candidate, unrelated]),
        vector_store=_VectorStore(),
        reranker=_Reranker(),
        llm=_LLM([]),
        relation_writer=_RelationWriter(),
    ).discover_card_relations(
        ["card:1"],
        target="test",
        include_evaluation_details=True,
    )

    details = result["card_diagnostics"][0]["evaluation_details"]
    assert details["merged_candidate_ids"] == ["card:2", "card:3"]
    assert details["selected_candidate_ids"] == ["card:2", "card:3"]
    assert details["screened_related_candidate_ids"] == ["card:2"]
    assert details["verified_candidate_ids"] == ["card:2"]
    assert all("card:2" in item["reranked_ids"] for item in details["routes"])


@pytest.mark.asyncio
async def test_no_recall_candidates_do_not_call_llm() -> None:
    source = _manifest("card:1", "chunk:1")
    vector_store = _VectorStore()

    async def no_hits(routes, **_kwargs):
        return {route.route_id: [] for route in routes}

    vector_store.recall_routes = no_hits
    llm = _LLM([])
    result = await RelationDiscoveryService(
        repository=_Repository([source]),
        vector_store=vector_store,
        reranker=_Reranker(),
        llm=llm,
        relation_writer=_RelationWriter(),
    ).discover_card_relations(["card:1"], target="test")

    assert result["decisions"] == []
    assert llm.requests == []


@pytest.mark.asyncio
async def test_negative_rerank_scores_are_filtered_before_summary_llm(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_RERANK_MIN_SCORE", 0.0)
    source = _manifest("card:1", "chunk:1")
    candidate = _manifest("card:2", "chunk:2")
    unrelated = _manifest("card:3", "chunk:3")
    reranker = _NegativeReranker()
    llm = _LLM([])

    result = await RelationDiscoveryService(
        repository=_Repository([source, candidate, unrelated]),
        vector_store=_VectorStore(),
        reranker=reranker,
        llm=llm,
        relation_writer=_RelationWriter(),
    ).discover_card_relations(["card:1"], target="test")

    assert reranker.calls
    assert result["decisions"] == []
    assert result["card_diagnostics"][0]["route_reranked_hits"] == 0
    assert result["card_diagnostics"][0]["screened_candidates"] == 0
    assert llm.requests == []


@pytest.mark.asyncio
async def test_same_primary_chunk_candidates_are_excluded_before_rerank_and_llm() -> None:
    source = _manifest("card:1", "chunk:shared")
    sibling = _manifest("card:2", "chunk:shared")
    vector_store = _VectorStore()

    async def sibling_only(routes, **_kwargs):
        return {
            route.route_id: [RelationRecallHit("card:2", "summary", 1, 0.9)]
            for route in routes
        }

    vector_store.recall_routes = sibling_only
    reranker = _Reranker()
    llm = _LLM([])

    result = await RelationDiscoveryService(
        repository=_Repository([source, sibling]),
        vector_store=vector_store,
        reranker=reranker,
        llm=llm,
        relation_writer=_RelationWriter(),
    ).discover_card_relations(["card:1"], target="test")

    assert result["decisions"] == []
    assert reranker.calls == []
    assert llm.requests == []
    diagnostics = result["card_diagnostics"][0]
    assert diagnostics["same_chunk_excluded_candidate_ids"] == ["card:2"]


@pytest.mark.asyncio
async def test_missing_pair_chunk_is_reported_without_fabricating_relation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_RERANK_TOP_N", 12)
    source = _manifest("card:1", "chunk:1")
    candidate = _manifest("card:2", "chunk:missing")
    vector_store = _VectorStore()
    llm = _LLM([])

    result = await RelationDiscoveryService(
        repository=_Repository([source, candidate]),
        vector_store=vector_store,
        reranker=_Reranker(),
        llm=llm,
        relation_writer=_RelationWriter(),
    ).discover_card_relations(["card:1"], target="test")

    assert result["decisions"] == []
    assert result["card_diagnostics"][0]["pair_data_errors"][0]["candidate_card_id"] == "card:2"
    assert all(request.metadata["task"] != "kg_relation_evidence_verify" for request in llm.requests)
