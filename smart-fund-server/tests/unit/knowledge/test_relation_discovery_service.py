"""Relation Probe 双视图候选发现与原文核验测试。"""

from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from src.application.services.relation_discovery_service import (
    RELATION_DISCOVERY_PIPELINE_VERSION,
    RelationDiscoveryService,
    _parse_same_fact_gate,
    _parse_verified_decision,
    _same_fact_gate_schema,
    _verification_schema,
)
from src.domain.knowledge.atomic_cognitive_card import CognitiveCardManifest
from src.domain.knowledge.relation_discovery import (
    MergedRelationCandidate,
    PairEvidencePackage,
    RelationProbe,
    RelationRecallHit,
    VerifiedRelationDecision,
)
from src.infrastructure.clients.reranker import RerankResponse, RerankResult
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.types import LLMProxyResponse
from src.infrastructure.vector_store.relation_candidate_store import RelationCardText


def _manifest(
    card_id: str,
    chunk_id: str,
    *,
    probes: list[RelationProbe] | None = None,
    fact_id: str = "",
) -> CognitiveCardManifest:
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
        schema_version="atomic_cognitive_card_v7",
        generator_version="atomic_card_extractor_v62",
        relation_probes=list(probes or []),
        status="active",
        fact_id=fact_id,
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
        return [
            self.manifests[item]
            for item in cognitive_card_ids
            if item in self.manifests
        ]


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
                {
                    "source_published_at": "2026-07-10",
                    "schema_version": "atomic_cognitive_card_v7",
                    "status": "active",
                },
            ),
            "card:2": RelationCardText(
                "card:2",
                "下游制造企业原材料库存下降。",
                {
                    "source_published_at": "2026-07-11",
                    "schema_version": "atomic_cognitive_card_v5",
                    "status": "active",
                },
            ),
            "card:3": RelationCardText(
                "card:3",
                "另一行业公司发布季度业绩。",
                {
                    "source_published_at": "2026-07-11",
                    "schema_version": "atomic_cognitive_card_v7",
                    "status": "active",
                },
            ),
        }
        self.chunks = {
            "chunk:1": RelationCardText(
                "chunk:1", "监管部门限制关键原材料出口。相关措施立即执行。", {}
            ),
            "chunk:2": RelationCardText(
                "chunk:2", "下游制造企业库存下降并开始减产。供应商交付减少。", {}
            ),
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
        return {
            item: self.summaries[item] for item in card_ids if item in self.summaries
        }

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
        self.calls.append(
            {"query": query, "documents": list(documents), "top_n": top_n}
        )
        return RerankResponse(
            model="test",
            results=[
                RerankResult(
                    index=index, relevance_score=1.0 - index * 0.1, document=document
                )
                for index, document in enumerate(documents[:top_n])
            ],
            latency_ms=1,
            total_documents=len(documents),
        )


class _NegativeReranker:
    def __init__(self) -> None:
        self.calls = []

    async def rerank(self, *, query, documents, top_n=None):
        self.calls.append(
            {"query": query, "documents": list(documents), "top_n": top_n}
        )
        return RerankResponse(
            model="test",
            results=[
                RerankResult(
                    index=index, relevance_score=-0.01 - index * 0.1, document=document
                )
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
        source_card_summary="监管部门限制关键原材料出口。",
        candidate_card_id="card:2",
        candidate_evidence_context=[
            {"text": "下游库存下降。", "evidence_ref": "s0001"}
        ],
        candidate_focus_refs=["s0001"],
        candidate_published_at="2026-07-11",
        candidate_card_summary="下游制造企业原材料库存下降。",
    )


def test_no_relation_contract_uses_empty_relations_array() -> None:
    package = _pair_package()
    schema = _verification_schema(package)

    assert schema["required"] == ["relations"]
    assert schema["properties"]["relations"]["maxItems"] == 1
    relation_schema = schema["properties"]["relations"]["items"]
    assert relation_schema["properties"]["decision_class"]["enum"] == [
        "observed",
        "inferred",
    ]

    decision = _parse_verified_decision({"relations": []}, package)
    assert decision.as_dict() == {"decision_class": "no_relation"}

    with pytest.raises(ValueError, match="最多包含一项"):
        _parse_verified_decision({"relations": [{}, {}]}, package)


def test_existing_fact_candidates_only_consume_one_screening_slot() -> None:
    repository = _Repository(
        [
            _manifest(
                "card:1",
                "chunk:1",
                fact_id="kg_fact:shared",
            ),
            _manifest(
                "card:2",
                "chunk:2",
                fact_id="kg_fact:shared",
            ),
            _manifest(
                "card:3",
                "chunk:3",
                fact_id="kg_fact:other",
            ),
        ]
    )
    service = RelationDiscoveryService(
        repository=repository,
        vector_store=object(),
        reranker=object(),
        llm=object(),
        relation_writer=object(),
    )
    candidates = [
        MergedRelationCandidate("card:1", "第一条报道", "", rrf_score=0.9),
        MergedRelationCandidate("card:2", "同一事实的重复报道", "", rrf_score=0.8),
        MergedRelationCandidate("card:3", "另一个事件", "", rrf_score=0.7),
    ]

    grouped, collapsed = service._group_candidates_by_existing_fact(
        candidates,
        adapter_name="financial",
    )

    assert [item.candidate_card_id for item in grouped] == [
        "card:1",
        "card:3",
    ]
    assert collapsed == ["card:2"]


def test_market_co_movement_is_a_supported_inferred_relation() -> None:
    package = PairEvidencePackage(
        source_card_id="card:silver",
        source_evidence_context=[
            {"text": "现货白银日内跌幅扩大至3%。", "evidence_ref": "s0001"}
        ],
        source_focus_refs=["s0001"],
        source_published_at="2026-07-13T03:12:22+00:00",
        candidate_card_id="card:equity",
        candidate_evidence_context=[
            {"text": "A股贵金属方向跌幅居前。", "evidence_ref": "s0001"}
        ],
        candidate_focus_refs=["s0001"],
        candidate_published_at="2026-07-13T01:41:01+00:00",
    )

    assert (
        "market_co_movement"
        in (
            _verification_schema(package)["properties"]["relations"]["items"][
                "properties"
            ]["relation_kind"]["enum"]
        )
    )
    decision = _parse_verified_decision(
        {
            "relations": [
                {
                    "source_card_id": "card:silver",
                    "target_card_id": "card:equity",
                    "decision_class": "inferred",
                    "relation_kind": "market_co_movement",
                    "relation_type": "贵金属现货与权益板块同步走弱",
                    "direction": "symmetric",
                    "basis": "现货白银与A股贵金属板块在相近时间内均走弱。",
                    "inference_mechanism": "白银现货和贵金属权益板块提供同一贵金属市场的跨载体弱势信号。",
                    "confidence": 0.68,
                }
            ],
        },
        package,
    )

    assert decision.relation_kind == "market_co_movement"
    assert decision.decision_class == "inferred"
    assert decision.source_evidence_refs == ["s0001"]
    assert decision.target_evidence_refs == ["s0001"]


def test_same_event_requires_observed_decision() -> None:
    package = _pair_package()
    with pytest.raises(ValueError, match="same_event 只能裁决为 observed"):
        _parse_verified_decision(
            {
                "relations": [
                    {
                        "source_card_id": "card:1",
                        "target_card_id": "card:2",
                        "decision_class": "inferred",
                        "relation_kind": "same_event",
                        "relation_type": "同一事件",
                        "direction": "symmetric",
                        "basis": "双方描述同一事件。",
                        "inference_mechanism": "通过主体与时间对齐。",
                        "confidence": 0.8,
                    }
                ]
            },
            package,
        )


def test_same_fact_is_supported_and_requires_observed_decision() -> None:
    package = _pair_package()
    relation_kinds = _verification_schema(package)["properties"]["relations"]["items"][
        "properties"
    ]["relation_kind"]["enum"]
    assert "same_fact" in relation_kinds

    with pytest.raises(ValueError, match="same_fact 只能裁决为 observed"):
        _parse_verified_decision(
            {
                "relations": [
                    {
                        "source_card_id": "card:1",
                        "target_card_id": "card:2",
                        "decision_class": "inferred",
                        "relation_kind": "same_fact",
                        "relation_type": "同一原子事实",
                        "direction": "symmetric",
                        "basis": "双方陈述相同事实。",
                        "inference_mechanism": "通过语义推断。",
                        "confidence": 0.8,
                    }
                ]
            },
            package,
        )


def test_same_fact_gate_rejects_internally_inconsistent_equivalence() -> None:
    schema = _same_fact_gate_schema()
    assert schema["properties"]["equivalent"]["type"] == "boolean"

    assert (
        _parse_same_fact_gate(
            {
                "source_claims": ["事实一。"],
                "target_claims": ["事实一。", "事实二。"],
                "equivalent": False,
                "same_event": True,
                "basis": "目标摘要包含额外独立事实。",
            }
        )["same_event"]
        is True
    )

    with pytest.raises(ValueError, match="主张数量"):
        _parse_same_fact_gate(
            {
                "source_claims": ["事实一。"],
                "target_claims": ["事实一。", "事实二。"],
                "equivalent": True,
                "same_event": True,
                "basis": "错误裁决。",
            }
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
                "relations": [
                    {
                        "source_card_id": "card:1",
                        "target_card_id": "card:2",
                        "decision_class": "observed",
                        "relation_kind": "causal_influence",
                        "relation_type": "supply_constraint_transmission",
                        "direction": "card:1_to_card:2",
                        "basis": "出口限制后，下游企业库存下降并减产。",
                        "inference_mechanism": "",
                        "confidence": 0.9,
                    }
                ],
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


def _verified_decision(
    source_card_id: str,
    target_card_id: str,
) -> VerifiedRelationDecision:
    return VerifiedRelationDecision(
        source_card_id=source_card_id,
        target_card_id=target_card_id,
        decision_class="observed",
        relation_kind="same_event",
        relation_type="同一事件的不同事实侧面",
        direction="symmetric",
        basis="双方证据对齐同一可识别事件。",
        source_evidence_refs=["s0001"],
        target_evidence_refs=["s0001"],
        confidence=0.9,
    )


@dataclass
class _SameFactLLM:
    requests: list
    equivalent: bool
    same_event: bool

    async def generate(self, request):
        self.requests.append(request)
        if request.metadata["task"] == "kg_same_fact_gate":
            output = {
                "source_claims": ["事实一。"],
                "target_claims": (
                    ["事实一。"] if self.equivalent else ["事实一。", "事实二。"]
                ),
                "equivalent": self.equivalent,
                "same_event": self.same_event,
                "basis": "双方属于同一事件，但目标摘要包含额外独立事实。",
            }
        else:
            output = {
                "relations": [
                    {
                        "source_card_id": "card:1",
                        "target_card_id": "card:2",
                        "decision_class": "observed",
                        "relation_kind": "same_fact",
                        "relation_type": "同一原子事实",
                        "direction": "对称关系",
                        "basis": "双方共享相同核心事实。",
                        "inference_mechanism": "",
                        "confidence": 0.98,
                    }
                ],
            }
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


@pytest.mark.asyncio
async def test_same_fact_gate_downgrades_subset_relation_to_same_event() -> None:
    llm = _SameFactLLM([], equivalent=False, same_event=True)
    service = RelationDiscoveryService(
        repository=_Repository([]),
        vector_store=_VectorStore(),
        reranker=_Reranker(),
        llm=llm,
        relation_writer=_RelationWriter(),
    )

    decisions = await service._verify_packages([_pair_package()])

    assert len(decisions) == 1
    assert decisions[0].relation_kind == "same_event"
    assert decisions[0].decision_class == "observed"
    assert [request.metadata["task"] for request in llm.requests] == [
        "kg_relation_evidence_verify",
        "kg_same_fact_gate",
    ]
    gate_request = llm.requests[1]
    assert gate_request.provider_options == {}
    assert json.loads(gate_request.prompt) == {
        "source_summary": "监管部门限制关键原材料出口。",
        "target_summary": "下游制造企业原材料库存下降。",
    }


@pytest.mark.asyncio
async def test_dual_view_recall_uses_summary_for_rerank_and_full_text_only_for_verification(
    monkeypatch,
) -> None:
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

    result = await service.discover_card_relations(
        ["card:1"],
        target="test",
        workflow_id="workflow:test",
    )

    assert result["observed"] == 1
    assert result["edge_persistence"]["changed_edge_ids"] == ["kg_card_relation:test"]
    assert result["workflow_id"] == "workflow:test"
    assert len(relation_writer.calls) == 1
    assert relation_writer.calls[0][1]["workflow_id"] == "workflow:test"
    assert "evaluation_details" not in result["card_diagnostics"][0]
    assert result["decisions"][0]["target_card_id"] == "card:2"
    assert len(vector_store.routes) == 1
    assert vector_store.routes[0].query == "监管部门限制关键原材料出口。"
    assert all(
        item.metadata["task"] != "kg_relation_probe_planning" for item in llm.requests
    )
    assert reranker.calls
    for call in reranker.calls:
        assert call["documents"] == [
            "下游制造企业原材料库存下降。",
            "另一行业公司发布季度业绩。",
        ]
        assert all("减产" not in document for document in call["documents"])

    screen_request = next(
        item
        for item in llm.requests
        if item.metadata["task"] == "kg_relation_candidate_screen"
    )
    verify_request = next(
        item
        for item in llm.requests
        if item.metadata["task"] == "kg_relation_evidence_verify"
    )
    assert "开始减产" not in screen_request.prompt
    assert "开始减产" not in verify_request.prompt
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
    assert "如果双方分别提供不同属性、不同阶段" in verify_request.system_prompt
    assert "必须先检查是否满足严格的 same_fact" in verify_request.system_prompt
    assert "同一次现实事件" in verify_request.system_prompt
    assert "仅处于同一交易日、同一市场" in verify_request.system_prompt
    assert "个股上涨与市场指数上涨" in verify_request.system_prompt
    assert (
        "分别描述不同目标期间、预测区间或并列指标，不构成时间进展"
        in verify_request.system_prompt
    )
    assert "observed 关系不需要推理链" in verify_request.system_prompt
    assert "不得用“通常会”" in verify_request.system_prompt
    assert "X→A 且 X→B 只能证明共同驱动" in verify_request.system_prompt
    assert "card_summary 定义当前 Card 要表达的原子事实" in verify_request.system_prompt
    assert "原文中不属于 card_summary 的并列内容" in verify_request.system_prompt
    assert (
        "即使双方 evidence 来自同一段原文或内容完全相同" in verify_request.system_prompt
    )
    assert "same_fact 必须满足双向完整等价" in verify_request.system_prompt
    assert "market_co_movement" in verify_request.system_prompt
    assert screen_request.metadata["_cache_key_metadata"]["pipeline_version"] == (
        "relation_discovery_v2_edge_persistence"
    )
    assert (
        verify_request.metadata["_cache_key_metadata"]["pipeline_version"]
        == RELATION_DISCOVERY_PIPELINE_VERSION
    )
    verify_payload = json.loads(verify_request.prompt)
    assert set(verify_payload) == {"source_card", "candidate_card"}
    assert verify_payload["source_card"] == {
        "card_id": "card:1",
        "source_published_at": "2026-07-10",
        "card_summary": "监管部门限制关键原材料出口。",
        "chunk_summary": "",
        "evidence": [
            {"text": "监管部门限制关键"},
        ],
    }
    assert verify_payload["candidate_card"] == {
        "card_id": "card:2",
        "source_published_at": "2026-07-11",
        "card_summary": "下游制造企业原材料库存下降。",
        "chunk_summary": "",
        "evidence": [
            {"text": "下游制造企业库存"},
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
async def test_batch_discovery_persists_each_card_before_processing_the_next(
    monkeypatch,
) -> None:
    manifests = [
        _manifest("card:1", "chunk:1"),
        _manifest("card:2", "chunk:2"),
    ]
    writer = _RelationWriter()
    service = RelationDiscoveryService(
        repository=_Repository(manifests),
        vector_store=_VectorStore(),
        reranker=_Reranker(),
        llm=_LLM([]),
        relation_writer=writer,
    )

    async def discover_one(*, manifest, **_kwargs):
        if manifest.cognitive_card_id == "card:2":
            raise RuntimeError("second card failed")
        return (
            [_verified_decision("card:1", "card:2")],
            {"card_id": manifest.cognitive_card_id},
        )

    monkeypatch.setattr(service, "_discover_one", discover_one)

    with pytest.raises(RuntimeError, match="second card failed"):
        await service.discover_card_relations(
            ["card:1", "card:2"],
            target="test",
            workflow_id="workflow:checkpoint",
        )

    assert len(writer.calls) == 1
    decisions, kwargs = writer.calls[0]
    assert decisions == [_verified_decision("card:1", "card:2")]
    assert kwargs["workflow_id"] == "workflow:checkpoint"


@pytest.mark.asyncio
async def test_batch_discovery_merges_per_card_persistence_results(
    monkeypatch,
) -> None:
    manifests = [
        _manifest("card:1", "chunk:1"),
        _manifest("card:2", "chunk:2"),
        _manifest("card:3", "chunk:3"),
    ]
    writer = _RelationWriter()
    service = RelationDiscoveryService(
        repository=_Repository(manifests),
        vector_store=_VectorStore(),
        reranker=_Reranker(),
        llm=_LLM([]),
        relation_writer=writer,
    )

    async def discover_one(*, manifest, **_kwargs):
        card_id = manifest.cognitive_card_id
        return (
            [_verified_decision(card_id, "card:3")],
            {"card_id": card_id},
        )

    monkeypatch.setattr(service, "_discover_one", discover_one)

    result = await service.discover_card_relations(
        ["card:1", "card:2"],
        target="test",
        workflow_id="workflow:checkpoint",
    )

    assert len(writer.calls) == 2
    assert result["observed"] == 2
    assert result["edge_persistence"]["checkpoint_count"] == 2
    assert result["edge_persistence"]["checkpointed_card_ids"] == [
        "card:1",
        "card:2",
    ]
    assert result["edge_persistence"]["changed_edge_ids"] == [
        "kg_card_relation:test"
    ]
    assert result["edge_persistence"]["graph_event_ids"] == ["event:test"]
    assert result["edge_persistence"]["workflow_id"] == "workflow:checkpoint"


@pytest.mark.asyncio
async def test_historical_pair_reverification_bypasses_recall_screen_and_rerank(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_VERIFY_CONCURRENCY", 2)
    source = _manifest("card:1", "chunk:1")
    candidate = _manifest("card:2", "chunk:2")
    vector_store = _VectorStore()
    reranker = _Reranker()
    llm = _LLM([])
    relation_writer = _RelationWriter()

    result = await RelationDiscoveryService(
        repository=_Repository([source, candidate]),
        vector_store=vector_store,
        reranker=reranker,
        llm=llm,
        relation_writer=relation_writer,
    ).reverify_card_pairs(
        [("card:2", "card:1"), ("card:1", "card:2")],
        target="test",
        workflow_id="workflow:historical",
    )

    assert result["pairs_requested"] == 1
    assert result["other_relation"] == 1
    assert result["workflow_id"] == "workflow:historical"
    assert vector_store.routes == []
    assert reranker.calls == []
    assert [request.metadata["task"] for request in llm.requests] == [
        "kg_relation_evidence_verify"
    ]
    assert len(relation_writer.calls) == 1
    decisions, kwargs = relation_writer.calls[0]
    assert len(decisions) == 1
    assert kwargs["workflow_id"] == "workflow:historical"


@pytest.mark.asyncio
async def test_relation_probes_create_independent_recall_and_rerank_routes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "KG_RELATION_RERANK_TOP_N", 12)
    source = _manifest(
        "card:1",
        "chunk:1",
        probes=[
            RelationProbe(
                role="upstream",
                query="监管部门出台关键原材料出口限制的前置政策或供应原因。",
            ),
            RelationProbe(
                role="downstream",
                query="关键原材料供应减少后下游制造企业库存下降或减产。",
            ),
        ],
    )
    candidate = _manifest("card:2", "chunk:2")
    unrelated = _manifest("card:3", "chunk:3")
    vector_store = _VectorStore()
    reranker = _Reranker()

    result = await RelationDiscoveryService(
        repository=_Repository([source, candidate, unrelated]),
        vector_store=vector_store,
        reranker=reranker,
        llm=_LLM([]),
        relation_writer=_RelationWriter(),
    ).discover_card_relations(
        ["card:1"],
        target="test",
        include_evaluation_details=True,
    )

    assert [
        (route.route_type, route.role, route.query) for route in vector_store.routes
    ] == [
        ("summary", "baseline", "监管部门限制关键原材料出口。"),
        (
            "probe",
            "upstream",
            "监管部门出台关键原材料出口限制的前置政策或供应原因。",
        ),
        (
            "probe",
            "downstream",
            "关键原材料供应减少后下游制造企业库存下降或减产。",
        ),
    ]
    assert [call["query"] for call in reranker.calls] == [
        route.query for route in vector_store.routes
    ]
    details = result["card_diagnostics"][0]["evaluation_details"]
    assert details["route_count"] == 3
    assert [item["role"] for item in details["routes"]] == [
        "baseline",
        "upstream",
        "downstream",
    ]


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
async def test_negative_rerank_scores_are_filtered_before_summary_llm(
    monkeypatch,
) -> None:
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
async def test_same_primary_chunk_candidates_are_excluded_before_rerank_and_llm() -> (
    None
):
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
async def test_missing_pair_chunk_is_reported_without_fabricating_relation(
    monkeypatch,
) -> None:
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
    assert (
        result["card_diagnostics"][0]["pair_data_errors"][0]["candidate_card_id"]
        == "card:2"
    )
    assert all(
        request.metadata["task"] != "kg_relation_evidence_verify"
        for request in llm.requests
    )
