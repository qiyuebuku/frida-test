"""Tests for first-version KG incremental refresh orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeBootstrapStocksCommand,
    KnowledgeCompileResultDTO,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeIncrementalRefreshResultDTO,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildIndexesResultDTO,
)
from src.application.services import knowledge_service as service_module
from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.chunking import build_chunks_for_compiled_evidence
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.graph_index import (
    GraphIndexCommunity,
    GraphIndexDirtyRefs,
    GraphIndexFinding,
    GraphIndexRefreshPlan,
    GraphIndexUnassignedSignal,
)
from src.domain.knowledge.schemas import CompileResult, CompiledEdge, CompiledNode, EvidenceChunk
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"


class _Service(KnowledgeService):
    def __init__(self):
        super().__init__(repository=None)
        self.calls: list[str] = []

    async def bootstrap_financial_stock_entities(self, command: KnowledgeBootstrapStocksCommand):
        self.calls.append(f"stocks:{command.target}:{','.join(command.codes)}")
        return KnowledgeCompileResultDTO(
            adapter_name="financial",
            run_id="kg_run:stocks",
            nodes=1,
            edges=0,
            evidence=1,
            failed_records=0,
            dry_run=command.dry_run,
        )

    async def bootstrap_financial_stock_news(self, command: KnowledgeBootstrapStockNewsCommand):
        self.calls.append(f"news:{command.limit}:{command.concurrency}")
        return KnowledgeCompileResultDTO(
            adapter_name="financial",
            run_id="kg_run:news",
            nodes=2,
            edges=1,
            evidence=1,
            failed_records=0,
            dry_run=command.dry_run,
        )

    async def rebuild_indexes_for(self, command: KnowledgeRebuildIndexesCommand):
        self.calls.append("indexes:" + ",".join(command.index_types))
        return KnowledgeRebuildIndexesResultDTO(
            adapter_name=command.adapter_name,
            run_id="kg_run:indexes",
            graph_adjacency=1,
            evidence_chunks=1,
            hybrid_chunks=1,
        )


@pytest.mark.asyncio
async def test_incremental_refresh_runs_compile_and_indexes() -> None:
    service = _Service()

    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target="test",
            codes=["300750"],
            news_limit=7,
            concurrency=2,
        )
    )

    assert result.adapter_name == "financial"
    assert result.target == "test"
    assert result.dry_run is False
    assert [step["name"] for step in result.steps] == [
        "bootstrap_stocks",
        "bootstrap_stock_news",
        "incremental_indexes",
        "rebuild_indexes",
    ]
    assert service.calls == [
        "stocks:test:300750",
        "news:7:2",
        "indexes:graph_adjacency,evidence_chunks,hybrid_chunks",
    ]


@pytest.mark.asyncio
async def test_incremental_refresh_dry_run_skips_rebuild_steps() -> None:
    service = _Service()

    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target="test",
            codes=["300750"],
            dry_run=True,
        )
    )

    assert [step["status"] for step in result.steps] == ["ok", "ok", "skipped"]
    assert service.calls == ["stocks:test:300750", "news:20:1"]


@pytest.mark.asyncio
async def test_compile_kg_refreshes_changed_indexes_incrementally(monkeypatch) -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    repository = _CompileRefreshRepository()
    hybrid_calls: list[dict] = []

    class FakeRetriever:
        async def delete_evidence(self, **_kwargs):
            return 0

        async def delete_documents(self, **_kwargs):
            return 0

        async def upsert_index(self, **kwargs):
            hybrid_calls.append(kwargs)
            return len(kwargs["chunks"]) + len(kwargs["nodes"]) + len(kwargs["edges"])

        async def upsert_semantic_documents(self, **kwargs):
            hybrid_calls.append(kwargs)
            return len(kwargs["documents"])

    monkeypatch.setattr(service_module, "get_adapter", lambda _name, **_kwargs: ToyProjectAdapter())
    monkeypatch.setattr(service_module, "MilvusSemanticHybridRetriever", lambda: FakeRetriever())
    monkeypatch.setattr(service_module, "GraphIndexLLMReporter", lambda: _FakeGraphIndexReporter())

    class FakeAtomicCardStage:
        def __init__(self, **_kwargs):
            pass

        async def refresh(self, *, changed_chunks, **_kwargs):
            cards = [
                SimpleNamespace(cognitive_card_id=f"card:{chunk.chunk_id}")
                for chunk in changed_chunks
            ]
            return SimpleNamespace(
                status="cards_ready",
                cards=cards,
                diagnostics={
                    "status": "cards_ready",
                    "cards": len(cards),
                    "assignment_executed": False,
                    "milvus_documents_written": len(cards),
                },
            )

    monkeypatch.setattr(service_module, "AtomicCognitiveCardStageService", FakeAtomicCardStage)

    result = await KnowledgeService(repository=repository).compile_kg(
        service_module.KnowledgeCompileCommand(
            adapter_name="toy",
            records=records,
            target="test",
        )
    )

    assert result.index_refresh["mode"] == "incremental"
    assert result.index_refresh["graph_adjacency"] == 0
    assert result.index_refresh["evidence_chunks"] == 1
    refresh_call = [call for call in hybrid_calls if "chunks" in call][-1]
    assert result.index_refresh["hybrid_chunks"] == len(refresh_call["chunks"])
    assert refresh_call["nodes"] == []
    assert refresh_call["edges"] == []
    assert "graph_index" in result.index_refresh
    assert result.index_refresh["graph_index"]["status"] == "pending_relation_graph_phase"
    assert result.index_refresh["cognitive_index"]["status"] == "cards_ready"
    assert result.index_refresh["cognitive_index"]["cards"] == 1
    assert result.index_refresh["cognitive_index"]["assignment_executed"] is False
    assert hybrid_calls[0]["target"] == "test"
    assert refresh_call["target"] == "test"
    assert hybrid_calls[0]["chunks"]
    assert hybrid_calls[0]["nodes"] == []
    assert hybrid_calls[0]["edges"] == []
    assert repository.calls[:2] == ["upsert_evidence:1", "upsert_evidence_chunks:1"]
    assert "create_run" in repository.calls
    assert not any(call.startswith("upsert_nodes") for call in repository.calls)
    assert not any(call.startswith("upsert_edges") for call in repository.calls)
    assert not any(call.startswith("upsert_graph_adjacency") for call in repository.calls)
    assert "upsert_evidence_chunks:1" in repository.calls
    assert "mark_graph_index_dirty:compile_changed_refs" not in repository.calls
    assert not any(call.startswith("replace_graph_index") for call in repository.calls)


@pytest.mark.asyncio
async def test_prune_stale_community_documents_deletes_legacy_documents_regardless_source_type(monkeypatch) -> None:
    class FakeRetriever:
        def __init__(self):
            self.deleted: list[str] = []
            self.list_calls: list[dict] = []

        async def list_target_ids_by_role(self, **kwargs):
            self.list_calls.append(kwargs)
            if kwargs.get("source_type"):
                return ["kgc:financial:l0:1"]
            return [
                "kgc:financial:l0:1",
                "kg_community:risk_event:l0:legacy",
                "kg_finding:legacy",
            ]

        async def delete_documents_by_role(self, **kwargs):
            self.deleted.extend(kwargs["target_ids"])
            return len(kwargs["target_ids"])

    retriever = FakeRetriever()
    monkeypatch.setattr(service_module, "_semantic_hybrid_retriever", lambda: retriever)

    deleted = await service_module._prune_stale_community_documents(
        adapter_name="financial",
        target="prod",
        active_target_ids=["kgc:financial:l0:1"],
    )

    assert deleted == 2
    assert retriever.list_calls == [
        {
            "collection_role": service_module.SEMANTIC_COLLECTION_COMMUNITY,
            "adapter_name": "financial",
            "target": "prod",
        }
    ]
    assert retriever.deleted == [
        "kg_community:risk_event:l0:legacy",
        "kg_finding:legacy",
    ]


def test_semantic_index_materials_include_edges_related_to_changed_node() -> None:
    repository = _CompileRefreshRepository()
    changed_node = CompiledNode(
        node_id="kg:toy:node:changed",
        adapter_name="toy",
        node_type="company",
        canonical_name="Changed",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    neighbor = CompiledNode(
        node_id="kg:toy:node:neighbor",
        adapter_name="toy",
        node_type="industry",
        canonical_name="Neighbor",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    existing_edge = CompiledEdge(
        edge_id="kg_edge:toy:related_to:1",
        adapter_name="toy",
        source_node_id=changed_node.node_id,
        target_node_id=neighbor.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:toy:1"],
        version="v1",
    )
    repository.nodes = [changed_node, neighbor]
    repository.edges = [existing_edge]

    materials = service_module._semantic_index_materials_for_result(
        repository,
        CompileResult(
            run_id="kg_run:test",
            adapter_name="toy",
            adapter_version="v1",
            version="v1",
            nodes=[changed_node],
            edges=[],
            evidence=[],
        ),
    )

    assert materials.nodes == []
    assert materials.edges == []
    assert f"kg_card:node_card:{changed_node.node_id}" not in materials.stale_chunk_ids
    assert f"kg_card:edge:{existing_edge.edge_id}" not in materials.stale_chunk_ids


def test_semantic_index_materials_delete_stale_cards_for_deprecated_edge() -> None:
    repository = _CompileRefreshRepository()
    source = CompiledNode(
        node_id="kg:toy:node:source",
        adapter_name="toy",
        node_type="company",
        canonical_name="Source",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    target = CompiledNode(
        node_id="kg:toy:node:target",
        adapter_name="toy",
        node_type="industry",
        canonical_name="Target",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    deprecated_edge = CompiledEdge(
        edge_id="kg_edge:toy:related_to:deprecated",
        adapter_name="toy",
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.DEPRECATED,
        evidence_ids=["kg_ev:toy:1"],
        version="v2",
    )
    repository.nodes = [source, target]
    repository.edges = [deprecated_edge]

    materials = service_module._semantic_index_materials_for_result(
        repository,
        CompileResult(
            run_id="kg_run:test",
            adapter_name="toy",
            adapter_version="v1",
            version="v2",
            nodes=[],
            edges=[deprecated_edge],
            evidence=[],
        ),
    )

    assert materials.edges == []
    assert f"kg_card:edge:{deprecated_edge.edge_id}" not in materials.stale_chunk_ids


def test_graph_index_build_scope_uses_dirty_subgraph_not_full_graph() -> None:
    dirty = CompiledNode(
        node_id="kg:toy:node:dirty",
        adapter_name="toy",
        node_type="company",
        canonical_name="Dirty",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    neighbor = CompiledNode(
        node_id="kg:toy:node:neighbor",
        adapter_name="toy",
        node_type="company",
        canonical_name="Neighbor",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    unrelated = CompiledNode(
        node_id="kg:toy:node:unrelated",
        adapter_name="toy",
        node_type="company",
        canonical_name="Unrelated",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    unrelated_peer = CompiledNode(
        node_id="kg:toy:node:unrelated_peer",
        adapter_name="toy",
        node_type="company",
        canonical_name="Unrelated Peer",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    dirty_edge = CompiledEdge(
        edge_id="kg_edge:toy:related_to:dirty",
        adapter_name="toy",
        source_node_id=dirty.node_id,
        target_node_id=neighbor.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["ev-dirty"],
        version="v1",
    )
    unrelated_edge = CompiledEdge(
        edge_id="kg_edge:toy:related_to:unrelated",
        adapter_name="toy",
        source_node_id=unrelated.node_id,
        target_node_id=unrelated_peer.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["ev-unrelated"],
        version="v1",
    )
    existing = _graph_community_for_scope(
        nodes=[dirty.node_id, neighbor.node_id],
        edges=[dirty_edge.edge_id],
        evidence=["ev-dirty"],
        chunks=["chunk-dirty"],
    )

    scope = service_module._graph_index_build_scope(
        chunks=[
            EvidenceChunk(chunk_id="chunk-dirty", adapter_name="toy", evidence_id="ev-dirty", content="dirty"),
            EvidenceChunk(chunk_id="chunk-unrelated", adapter_name="toy", evidence_id="ev-unrelated", content="unrelated"),
        ],
        nodes=[dirty, neighbor, unrelated, unrelated_peer],
        edges=[dirty_edge, unrelated_edge],
        existing_communities=[existing],
        dirty_refs=GraphIndexDirtyRefs(edge_ids=[dirty_edge.edge_id]),
        refresh_plan=GraphIndexRefreshPlan(
            action="local_recompute_required",
            score=0.1,
            affected_community_ids=[existing.community_id],
            affected_projection_counts={existing.projection: 1},
            changed_counts={"nodes": 0, "edges": 1, "evidence": 0, "chunks": 0},
            metrics={},
            reasons=["localized_dirty_subgraph"],
        ),
        force_rebuild=False,
    )

    assert scope["diagnostics"]["strategy"] == "dirty_subgraph_build"
    assert {node.node_id for node in scope["nodes"]} == {dirty.node_id, neighbor.node_id}
    assert {edge.edge_id for edge in scope["edges"]} == {dirty_edge.edge_id}
    assert [chunk.chunk_id for chunk in scope["chunks"]] == ["chunk-dirty"]


def test_graph_index_projection_scope_replaces_only_selected_projection() -> None:
    default = _graph_community_for_scope(
        nodes=["n-default"],
        edges=["e-default"],
        evidence=["ev-default"],
        chunks=["chunk-default"],
    )
    policy = GraphIndexCommunity(
        community_id="kg_community:policy_impact:l0:policy",
        version_id="kg_community:policy_impact:l0:policy:v1",
        adapter_name="financial",
        projection="policy_impact",
        level=0,
        parent_community_id="",
        title="policy",
        summary="",
        member_node_ids=["n-policy"],
        member_edge_ids=["e-policy"],
        evidence_ids=["ev-policy"],
        chunk_ids=["chunk-policy"],
        metrics={},
    )
    rebuilt_policy = GraphIndexCommunity(
        community_id="kg_community:policy_impact:l0:policy-new",
        version_id="kg_community:policy_impact:l0:policy-new:v1",
        adapter_name="financial",
        projection="policy_impact",
        level=0,
        parent_community_id="",
        title="policy new",
        summary="",
        member_node_ids=["n-policy"],
        member_edge_ids=["e-policy"],
        evidence_ids=["ev-policy"],
        chunk_ids=["chunk-policy"],
        metrics={},
    )

    scope = service_module._graph_index_replacement_scope(
        existing_communities=[default, policy],
        rebuilt_communities=[rebuilt_policy],
        rebuilt_findings=[],
        rebuilt_deltas=[],
        dirty_refs=GraphIndexDirtyRefs(),
        refresh_plan=GraphIndexRefreshPlan(
            action="full_rebuild",
            score=1.0,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts={},
            metrics={},
            reasons=["manual_rebuild_indexes"],
        ),
        force_rebuild=True,
        scope_projection="policy_impact",
    )

    assert scope["strategy"] == "global_calibration_projection_replace"
    assert scope["remove_community_ids"] == [policy.community_id]
    assert scope["communities"] == [rebuilt_policy]


def test_graph_index_replacement_scope_uses_local_unassigned_promotion() -> None:
    rebuilt = _graph_community_for_scope(
        nodes=["n-policy", "n-energy", "n-industry"],
        edges=["e-policy-energy", "e-policy-industry"],
        evidence=["ev-new", "ev-old"],
        chunks=["chunk-new", "chunk-old"],
    )
    scope = service_module._graph_index_replacement_scope(
        existing_communities=[],
        rebuilt_communities=[rebuilt],
        rebuilt_findings=[],
        rebuilt_deltas=[],
        dirty_refs=GraphIndexDirtyRefs(edge_ids=["e-policy-industry"]),
        refresh_plan=GraphIndexRefreshPlan(
            action="local_recompute_required",
            score=0.34,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts={"nodes": 1, "edges": 1, "evidence": 1, "chunks": 1},
            metrics={"related_unassigned_signal_ids": ["signal-1"]},
            reasons=["related_unassigned_signal_promotion"],
        ),
        force_rebuild=False,
        related_unassigned_signal_ids=["signal-1"],
    )

    assert scope["strategy"] == "local_unassigned_promotion"
    assert scope["remove_community_ids"] == []
    assert scope["communities"] == [rebuilt]


def test_graph_index_material_seed_includes_related_unassigned_signal_refs() -> None:
    signal = GraphIndexUnassignedSignal(
        signal_id="signal-1",
        adapter_name="financial",
        projection="default_graph_projection",
        title="新能源弱信号",
        reason="insufficient_root_structure",
        node_ids=["n-old"],
        edge_ids=["e-old"],
        evidence_ids=["ev-old"],
        chunk_ids=["chunk-old"],
        topic_tags=["十五五规划"],
        impact_tags=["政策利好"],
        event_type_tags=["政策规划"],
        relation_types=["affects"],
        support_score=0.4,
        metrics={},
    )

    seed = service_module._graph_index_material_seed(
        [],
        GraphIndexDirtyRefs(node_ids=["n-new"], edge_ids=["e-new"], evidence_ids=["ev-new"], chunk_ids=["chunk-new"]),
        GraphIndexRefreshPlan(
            action="local_recompute_required",
            score=0.34,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts={},
            metrics={},
            reasons=["related_unassigned_signal_promotion"],
        ),
        related_unassigned_signals=[signal],
    )

    assert seed["node_ids"] == ["n-new", "n-old"]
    assert seed["edge_ids"] == ["e-new", "e-old"]
    assert seed["evidence_ids"] == ["ev-new", "ev-old"]
    assert seed["chunk_ids"] == ["chunk-new", "chunk-old"]


@pytest.mark.asyncio
async def test_graph_index_light_refresh_uses_delta_reporter(monkeypatch) -> None:
    community = GraphIndexCommunity(
        community_id="kg_community:market:l0:semi",
        version_id="kg_community:market:l0:semi:v1",
        adapter_name="financial",
        projection="market_narrative",
        level=0,
        parent_community_id="",
        title="半导体",
        summary="半导体主题",
        member_node_ids=["kg:financial:industry:semi"],
        member_edge_ids=["kg_edge:financial:mentions:semi"],
        evidence_ids=["kg_ev:financial:news:1"],
        chunk_ids=["kg_chunk:kg_ev:financial:news:1:0"],
        metrics={},
    )
    finding = GraphIndexFinding(
        finding_id="kg_finding:semi",
        community_id=community.community_id,
        adapter_name="financial",
        projection=community.projection,
        finding_type="market_narrative",
        title="半导体叙事增强",
        statement="半导体相关叙事增强。",
        cited_chunk_ids=community.chunk_ids,
        cited_evidence_ids=community.evidence_ids,
        supporting_edge_ids=community.member_edge_ids,
        node_ids=community.member_node_ids,
        confidence=0.8,
        version=community.version_id,
        payload={"source": "llm_community_report"},
    )
    node = CompiledNode(
        node_id="kg:financial:industry:semi",
        adapter_name="financial",
        node_type="industry",
        canonical_name="半导体",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    chunk = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:1",
        content="半导体相关叙事增强。",
        payload={"published_at": "2026-07-01T00:00:00+00:00"},
    )
    plan = GraphIndexRefreshPlan(
        action="light_refresh_required",
        score=0.01,
        affected_community_ids=[community.community_id],
        affected_projection_counts={"market_narrative": 1},
        changed_counts={"nodes": 0, "edges": 1, "evidence": 0, "chunks": 0},
        metrics={},
        reasons=["localized_dirty_subgraph"],
    )

    fake_reporter = _FakeDeltaRefreshReporter()
    monkeypatch.setattr(service_module, "GraphIndexLLMReporter", lambda: fake_reporter)

    result = await service_module._light_refresh_graph_index(
        existing_communities=[community],
        existing_findings=[finding],
        chunks=[chunk],
        nodes=[node],
        edges=[],
        dirty_refs=GraphIndexDirtyRefs(edge_ids=community.member_edge_ids),
        refresh_plan=plan,
    )

    assert fake_reporter.calls == ["enrich_delta_refresh"]
    assert result.diagnostics["community_algorithm"] == "none_light_refresh"
    assert result.communities[0].change_reason == "light_refresh"
    assert result.findings[0].payload["source"] == "llm_delta_finding"
    assert result.deltas


def _graph_community_for_scope(*, nodes: list[str], edges: list[str], evidence: list[str], chunks: list[str]):
    return GraphIndexCommunity(
        community_id="kg_community:test:l0:dirty",
        version_id="kg_community:test:l0:dirty:v1",
        adapter_name="toy",
        projection="default_graph_projection",
        level=0,
        parent_community_id="",
        title="dirty",
        summary="",
        member_node_ids=nodes,
        member_edge_ids=edges,
        evidence_ids=evidence,
        chunk_ids=chunks,
        metrics={},
    )
    assert f"kg_card:node_card:{source.node_id}" in materials.stale_chunk_ids


@pytest.mark.asyncio
async def test_incremental_refresh_task_can_run_and_persist_status() -> None:
    repository = _TaskRepository()
    service = _TaskService(repository=repository)

    task = await service.enqueue_financial_incremental_refresh_task(
        KnowledgeIncrementalRefreshCommand(target="test", codes=["300750"]),
        max_retries=2,
    )
    result = await service.run_financial_incremental_refresh_task(task.run_id)

    assert task.status == "pending"
    assert result.status == "success"
    assert result.attempt == 1
    assert result.result["adapter_name"] == "financial"
    assert repository.runs[task.run_id]["status"] == "success"


@pytest.mark.asyncio
async def test_incremental_refresh_task_records_failure_and_retry() -> None:
    repository = _TaskRepository()
    service = _TaskService(repository=repository, fail_once=True)

    task = await service.enqueue_financial_incremental_refresh_task(
        KnowledgeIncrementalRefreshCommand(target="test", codes=["300750"]),
        max_retries=2,
    )
    failed = await service.run_financial_incremental_refresh_task(task.run_id)
    retried = await service.retry_financial_incremental_refresh_task(task.run_id)

    assert failed.status == "failed"
    assert failed.attempt == 1
    assert failed.error == "boom"
    assert retried.status == "success"
    assert retried.attempt == 2


class _CompileRefreshRepository:
    def __init__(self):
        self.calls: list[str] = []
        self.nodes = []
        self.edges = []
        self.evidence = []
        self.chunks = []
        self.cognitive_cards = []

    def create_compilation_run(self, _run):
        self.calls.append("create_run")
        return "kg_run:test"

    def finish_compilation_run(self, _run_id, _result):
        self.calls.append("finish_run")

    def upsert_nodes(self, nodes):
        self.calls.append(f"upsert_nodes:{len(nodes)}")
        self.nodes = nodes
        return len(nodes)

    def upsert_edges(self, edges):
        self.calls.append(f"upsert_edges:{len(edges)}")
        self.edges = edges
        return len(edges)

    def upsert_evidence(self, evidence):
        self.calls.append(f"upsert_evidence:{len(evidence)}")
        self.evidence = evidence
        return len(evidence)

    def upsert_graph_adjacency(self, edges):
        self.calls.append(f"upsert_graph_adjacency:{len(edges)}")
        return len(edges)

    def upsert_evidence_chunks(self, evidence):
        self.calls.append(f"upsert_evidence_chunks:{len(evidence)}")
        self.chunks = [chunk for item in evidence for chunk in build_chunks_for_compiled_evidence(item)]
        return len(evidence)

    def get_node(self, node_id):
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def list_nodes(self, _adapter_name):
        return self.nodes

    def list_edges(self, _adapter_name):
        return self.edges

    def list_evidence(self, _adapter_name):
        return self.evidence

    def list_evidence_chunks(self, _adapter_name):
        return self.chunks

    def replace_cognitive_cards_for_evidence(self, _adapter_name, *, evidence_ids, cards):
        self.calls.append(f"replace_cognitive_cards_for_evidence:{len(evidence_ids)}:{len(cards)}")
        self.cognitive_cards = list(cards)
        return {"deleted_cards": 0, "inserted_cards": len(cards), "evidence_ids": evidence_ids}

    def list_cognitive_cards(self, _adapter_name, *, status="active"):
        del status
        return self.cognitive_cards

    def replace_community_assignments_for_cards(self, _adapter_name, *, cognitive_card_ids, assignments):
        self.calls.append(f"replace_community_assignments_for_cards:{len(cognitive_card_ids)}:{len(assignments)}")
        return len(assignments)

    def count_graph_index_materials(self, _adapter_name):
        return {"nodes": len(self.nodes), "edges": len(self.edges), "chunks": len(self.chunks)}

    def list_graph_index_materials(self, _adapter_name, *, node_ids, edge_ids, evidence_ids, chunk_ids):
        node_set = set(node_ids)
        edge_set = set(edge_ids)
        evidence_set = set(evidence_ids)
        chunk_set = set(chunk_ids)
        edges = [
            edge
            for edge in self.edges
            if edge.edge_id in edge_set or edge.source_node_id in node_set or edge.target_node_id in node_set
        ]
        for edge in edges:
            node_set.add(edge.source_node_id)
            node_set.add(edge.target_node_id)
            evidence_set.update(edge.evidence_ids)
        return {
            "nodes": [node for node in self.nodes if node.node_id in node_set],
            "edges": edges,
            "chunks": [
                chunk
                for chunk in self.chunks
                if chunk.chunk_id in chunk_set or chunk.evidence_id in evidence_set
            ],
        }

    def list_graph_communities(self, _adapter_name):
        return []

    def allocate_graph_community_id(self, adapter_name, *, level=0):
        return f"kgc:{adapter_name}:l{level}:{len(self.calls) + 1}"

    def list_graph_findings(self, _adapter_name):
        return []

    def list_graph_deltas(self, _adapter_name):
        return []

    def list_graph_unassigned_signals(self, _adapter_name, *, status="active"):
        return []

    def mark_graph_index_dirty(self, _adapter_name, *, reason):
        self.calls.append(f"mark_graph_index_dirty:{reason}")
        return 0

    def replace_graph_index(self, _adapter_name, *, communities, findings, deltas=None, unassigned_signals=None):
        self.calls.append(
            f"replace_graph_index:{len(communities)}:{len(findings)}:{len(deltas or [])}:{len(unassigned_signals or [])}"
        )
        return {
            "communities": len(communities),
            "findings": len(findings),
            "deltas": len(deltas or []),
            "unassigned_signals": len(unassigned_signals or []),
            "stale_target_ids": [],
        }

    def replace_graph_index_scope(
        self,
        _adapter_name,
        *,
        remove_community_ids,
        communities,
        findings,
        deltas=None,
        unassigned_signals=None,
        promoted_signals=None,
    ):
        self.calls.append(
            "replace_graph_index_scope:"
            f"{len(remove_community_ids)}:{len(communities)}:{len(findings)}:"
            f"{len(deltas or [])}:{len(unassigned_signals or [])}:{len(promoted_signals or {})}"
        )
        return {
            "communities": len(communities),
            "findings": len(findings),
            "deltas": len(deltas or []),
            "unassigned_signals": len(unassigned_signals or []),
            "promoted_unassigned_signals": len(promoted_signals or {}),
            "stale_target_ids": [],
            "removed_community_ids": len(remove_community_ids),
            "removed_finding_ids": 0,
            "removed_delta_ids": 0,
        }

    def attach_edge_evidence(self, *_args):
        return 0


class _FakeGraphIndexReporter:
    async def enrich(self, *, graph_index, nodes, edges, chunks):
        return graph_index


class _FakeDeltaRefreshReporter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enrich_delta_refresh(self, *, graph_index, nodes, edges, chunks):
        self.calls.append("enrich_delta_refresh")
        community = graph_index.communities[0]
        chunk = chunks[0]
        finding = GraphIndexFinding(
            finding_id="kg_finding:delta:test",
            community_id=community.community_id,
            adapter_name=community.adapter_name,
            projection=community.projection,
            finding_type="recent_change",
            title="近期变化",
            statement="delta reporter 基于新增 chunk 生成近期变化。",
            cited_chunk_ids=[chunk.chunk_id],
            cited_evidence_ids=[chunk.evidence_id],
            supporting_edge_ids=community.member_edge_ids,
            node_ids=community.member_node_ids,
            confidence=0.8,
            version=community.version_id,
            payload={"source": "llm_delta_finding"},
        )
        return graph_index.__class__(
            communities=graph_index.communities,
            findings=[finding],
            deltas=graph_index.deltas,
            documents=[],
            diagnostics={
                **graph_index.diagnostics,
                "community_report_generator": "llm_delta_refresh",
            },
        )


class _TaskRepository:
    def __init__(self):
        self.runs: dict[str, dict] = {}

    def create_compilation_run(self, run):
        run_id = run["run_id"]
        current = self.runs.get(run_id, {})
        metadata = run.get("metadata", current.get("metadata", {}))
        self.runs[run_id] = {
            **current,
            **run,
            "metadata": metadata,
        }
        return run_id

    def finish_compilation_run(self, run_id, result):
        current = self.runs[run_id]
        current["status"] = result.get("status", current.get("status"))
        current["metadata"] = result.get("metadata", current.get("metadata", {}))

    def get_compilation_run(self, run_id):
        return self.runs.get(run_id)

    def list_compilation_runs(self, **_kwargs):
        return list(self.runs.values())


class _TaskService(KnowledgeService):
    def __init__(self, repository, fail_once: bool = False):
        super().__init__(repository=repository)
        self.fail_once = fail_once
        self.calls = 0

    async def refresh_financial_incremental(self, command: KnowledgeIncrementalRefreshCommand):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("boom")
        return KnowledgeIncrementalRefreshResultDTO(
            adapter_name="financial",
            target=command.target,
            run_id="kg_run:incremental:test",
            dry_run=command.dry_run,
            steps=[
                {
                    "name": "bootstrap_stocks",
                    "status": "ok",
                    "result": {
                        "nodes": 1,
                        "edges": 0,
                        "evidence": 1,
                        "failed_records": 0,
                    },
                }
            ],
        )
