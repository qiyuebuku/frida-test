"""正式 Card Relation Edge 写入链路测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.services.card_relation_write_service import CardRelationWriteService
from src.domain.knowledge.atomic_cognitive_card import CognitiveCardManifest
from src.domain.knowledge.card_relation import build_card_relation_edge
from src.domain.knowledge.relation_discovery import VerifiedRelationDecision
from src.infrastructure.persistence.repositories.card_relation_repository import (
    CardRelationSyncResult,
)
from src.infrastructure.persistence.repositories.card_fact_repository import (
    CardFactProjectionResult,
)
from src.infrastructure.vector_store.relation_candidate_store import RelationCardText


def _manifest(card_id: str, evidence_id: str, chunk_id: str) -> CognitiveCardManifest:
    return CognitiveCardManifest(
        cognitive_card_id=card_id,
        adapter_name="financial",
        source_type="news_articles",
        source_id=f"ft_news:{card_id[-1]}",
        evidence_id=evidence_id,
        primary_chunk_id=chunk_id,
        chunk_ids=[chunk_id],
        chunk_index=0,
        focus_evidence_refs=["s0001"],
        focus_span_offsets=[{"ref": "s0001", "start_offset": 0, "end_offset": 8}],
        schema_version="atomic_v1",
        generator_version="generator_v1",
        status="active",
    )


def _decision(
    *,
    source_id: str = "card:1",
    target_id: str = "card:2",
    decision_class: str = "inferred",
    relation_kind: str = "common_driver",
) -> VerifiedRelationDecision:
    return VerifiedRelationDecision(
        source_card_id=source_id,
        target_card_id=target_id,
        decision_class=decision_class,  # type: ignore[arg-type]
        relation_kind=relation_kind,
        relation_type="共同受到 AI 投资增长驱动",
        direction="AI 投资增长分别作用于双方",
        basis="双方原文均明确提到 AI 投资增长。",
        source_evidence_refs=["s0001"],
        target_evidence_refs=["s0001"],
        inference_mechanism="同一具体驱动分别连接两个端点事实。",
        confidence=0.88,
        relation_evidence_refs=[
            {"chunk_id": "chunk:1", "refs": ["s0001"]},
            {"chunk_id": "chunk:2", "refs": ["s0001"]},
        ],
    )


class _KnowledgeRepository:
    def __init__(self):
        self.manifests = {
            "card:1": _manifest("card:1", "evidence:1", "chunk:1"),
            "card:2": _manifest("card:2", "evidence:2", "chunk:2"),
        }

    def list_atomic_cognitive_card_manifests_by_ids(
        self, _adapter_name, *, cognitive_card_ids, status="active"
    ):
        return [self.manifests[item] for item in cognitive_card_ids if item in self.manifests]


class _CandidateStore:
    async def get_summaries(self, card_ids, **_kwargs):
        values = {
            "card:1": RelationCardText(
                "card:1",
                "存储器价格预期上修。",
                {"fact_id": "kg_fact:1"},
            ),
            "card:2": RelationCardText(
                "card:2",
                "半导体设备销售预期上调。",
                {"fact_id": "kg_fact:2"},
            ),
        }
        return {item: values[item] for item in card_ids}

    async def get_focus_evidence(self, card_ids, **_kwargs):
        values = {
            "card:1": RelationCardText(
                "card:1",
                "存储器价格预期上修原文。",
                {"fact_id": "kg_fact:1"},
            ),
            "card:2": RelationCardText(
                "card:2",
                "半导体设备销售预期上调原文。",
                {"fact_id": "kg_fact:2"},
            ),
        }
        return {item: values[item] for item in card_ids}


class _LegacyEventCandidateStore(_CandidateStore):
    async def get_summaries(self, card_ids, **kwargs):
        values = await super().get_summaries(card_ids, **kwargs)
        for value in values.values():
            value.metadata["event_id"] = "kg_event:legacy"
        return values

    async def get_focus_evidence(self, card_ids, **kwargs):
        values = await super().get_focus_evidence(card_ids, **kwargs)
        for value in values.values():
            value.metadata["event_id"] = "kg_event:legacy"
        return values


class _RelationRepository:
    def __init__(self):
        self.accepted = []
        self.rejected = []
        self.semantic_synced = []
        self.events_published = []
        self.pending = []

    def synchronize_batch(self, *, accepted_edges, rejected_pairs):
        self.accepted = list(accepted_edges)
        self.rejected = list(rejected_pairs)
        if not accepted_edges:
            return CardRelationSyncResult([], [], [], [], [])
        edge = accepted_edges[0]
        self.pending = [
            SimpleNamespace(
                id=edge.id,
                content_version=edge.content_version,
                source_card_id=edge.source_card_id,
                target_card_id=edge.target_card_id,
                status="active",
            )
        ]
        return CardRelationSyncResult(
            touched_edge_ids=[edge.id],
            changed_edge_ids=[edge.id],
            active_edges_to_publish=[edge],
            inactive_edge_ids_to_delete=[],
            affected_card_ids=[edge.source_card_id, edge.target_card_id],
        )

    def mark_semantic_synced(self, edge_ids):
        self.semantic_synced.extend(edge_ids)
        return len(edge_ids)

    def list_pending_graph_events(self, edge_ids):
        return [item for item in self.pending if item.id in edge_ids]

    def mark_graph_events_published(self, edge_ids):
        self.events_published.extend(edge_ids)
        return len(edge_ids)


class _SemanticRetriever:
    def __init__(self):
        self.documents = []
        self.deleted = []

    async def upsert_semantic_documents(self, **kwargs):
        self.documents.extend(kwargs["documents"])
        return len(kwargs["documents"])

    async def delete_documents_by_role(self, **kwargs):
        self.deleted.extend(kwargs["target_ids"])
        return len(kwargs["target_ids"])


class _CardFactRepository:
    def __init__(self):
        self.seed_card_ids = []

    def refresh_affected(self, *, adapter_name, seed_card_ids):
        self.seed_card_ids = list(seed_card_ids)
        return CardFactProjectionResult(
            affected_card_ids=tuple(sorted(seed_card_ids)),
            changed_card_ids=(),
            fact_ids=("kg_fact:1", "kg_fact:2"),
            fact_by_card_id={
                "card:1": "kg_fact:1",
                "card:2": "kg_fact:2",
            },
        )


class _ChangedCardFactRepository:
    def refresh_affected(self, *, adapter_name, seed_card_ids):
        return CardFactProjectionResult(
            affected_card_ids=("card:1", "card:2"),
            changed_card_ids=("card:2",),
            fact_ids=("kg_fact:shared",),
            fact_by_card_id={
                "card:1": "kg_fact:shared",
                "card:2": "kg_fact:shared",
            },
        )


def test_writer_reuses_semantic_retriever_store_for_card_reads() -> None:
    class SharedStore:
        def __init__(self):
            self.ready_calls = 0

        def ensure_ready(self):
            self.ready_calls += 1

    shared_store = SharedStore()
    semantic_retriever = SimpleNamespace(store=shared_store)

    service = CardRelationWriteService(
        knowledge_repository=_KnowledgeRepository(),
        semantic_retriever=semantic_retriever,
    )

    assert service._semantic_retriever is semantic_retriever
    assert service._relation_candidate_store.store is shared_store
    assert shared_store.ready_calls == 1


def test_symmetric_relation_identity_is_stable_when_endpoints_swap() -> None:
    left = build_card_relation_edge(
        _decision(source_id="card:1", target_id="card:2"),
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
    )
    right = build_card_relation_edge(
        _decision(source_id="card:2", target_id="card:1"),
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
    )

    assert left.id == right.id
    assert left.source_card_id == right.source_card_id == "card:1"
    assert left.target_card_id == right.target_card_id == "card:2"
    assert left.relation_evidence_refs == right.relation_evidence_refs


def test_market_co_movement_normalizes_as_symmetric_edge() -> None:
    edge = build_card_relation_edge(
        _decision(
            source_id="card:2",
            target_id="card:1",
            relation_kind="market_co_movement",
        ),
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
    )

    assert edge.source_card_id == "card:1"
    assert edge.target_card_id == "card:2"
    assert edge.relation_kind == "market_co_movement"


def test_inferred_same_fact_cannot_create_fact_identity_edge() -> None:
    with pytest.raises(ValueError, match="same_fact 只能由 observed"):
        build_card_relation_edge(
            _decision(
                decision_class="inferred",
                relation_kind="same_fact",
            ),
            pipeline_version="pipeline-v1",
            model_name="model-v1",
            prompt_version="prompt-v1",
        )


def test_inferred_same_event_cannot_create_event_relation_edge() -> None:
    with pytest.raises(ValueError, match="same_event 只能由 observed"):
        build_card_relation_edge(
            _decision(
                decision_class="inferred",
                relation_kind="same_event",
            ),
            pipeline_version="pipeline-v1",
            model_name="model-v1",
            prompt_version="prompt-v1",
        )


@pytest.mark.asyncio
async def test_positive_relation_publishes_readable_milvus_edge_then_graph_event() -> None:
    repository = _RelationRepository()
    semantic = _SemanticRetriever()
    published_events = []

    async def publish_event(**kwargs):
        published_events.append(kwargs)
        return ["jettask-event-1"]

    service = CardRelationWriteService(
        knowledge_repository=_KnowledgeRepository(),
        relation_repository=repository,
        card_fact_repository=_CardFactRepository(),
        semantic_retriever=semantic,
        relation_candidate_store=_CandidateStore(),
        graph_event_publisher=publish_event,
    )
    result = await service.persist_verified_decisions(
        [_decision()],
        adapter_name="financial",
        target="test",
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
        workflow_id="workflow:test",
    )

    assert len(repository.accepted) == 1
    edge = repository.accepted[0]
    assert semantic.documents[0].document_id == edge.id
    assert semantic.documents[0].collection_role == "card_relation"
    assert "存储器价格预期上修" in semantic.documents[0].text
    assert "半导体设备销售预期上调" in semantic.documents[0].text
    assert semantic.documents[0].metadata["edge_id"] == edge.id
    assert repository.semantic_synced == [edge.id]
    assert repository.events_published == [edge.id]
    assert published_events[0]["changed_edge_ids"] == [edge.id]
    assert published_events[0]["workflow_id"] == "workflow:test"
    assert result["graph_event_ids"] == ["jettask-event-1"]
    assert result["workflow_id"] == "workflow:test"


@pytest.mark.asyncio
async def test_fact_projection_repairs_both_card_views_before_graph_event() -> None:
    repository = _RelationRepository()
    semantic = _SemanticRetriever()
    published_events = []

    async def publish_event(**kwargs):
        published_events.append(kwargs)
        return ["jettask-event-1"]

    service = CardRelationWriteService(
        knowledge_repository=_KnowledgeRepository(),
        relation_repository=repository,
        card_fact_repository=_ChangedCardFactRepository(),
        semantic_retriever=semantic,
        relation_candidate_store=_LegacyEventCandidateStore(),
        graph_event_publisher=publish_event,
    )

    result = await service.persist_verified_decisions(
        [_decision()],
        adapter_name="financial",
        target="test",
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
    )

    card_documents = [
        document
        for document in semantic.documents
        if document.collection_role
        in {"cognitive_card", "cognitive_card_focus"}
    ]
    assert len(card_documents) == 4
    assert {
        document.metadata["fact_id"] for document in card_documents
    } == {"kg_fact:shared"}
    assert all(
        "event_id" not in document.metadata
        for document in card_documents
    )
    assert result["fact_id_changed_card_ids"] == ["card:2"]
    assert published_events[0]["changes"]["fact_id_changed_card_ids"] == [
        "card:2"
    ]


@pytest.mark.asyncio
async def test_no_relation_only_synchronizes_rejected_pair_without_edge_document() -> None:
    repository = _RelationRepository()
    semantic = _SemanticRetriever()
    service = CardRelationWriteService(
        knowledge_repository=_KnowledgeRepository(),
        relation_repository=repository,
        card_fact_repository=_CardFactRepository(),
        semantic_retriever=semantic,
        relation_candidate_store=_CandidateStore(),
        graph_event_publisher=lambda **_kwargs: None,  # 不会被调用
    )
    result = await service.persist_verified_decisions(
        [_decision(decision_class="no_relation", relation_kind="")],
        adapter_name="financial",
        target="test",
        pipeline_version="pipeline-v1",
        model_name="model-v1",
        prompt_version="prompt-v1",
    )

    assert repository.accepted == []
    assert repository.rejected == [("card:1", "card:2")]
    assert semantic.documents == []
    assert result["changed_edge_ids"] == []


@pytest.mark.asyncio
async def test_graph_event_failure_does_not_advance_published_version() -> None:
    repository = _RelationRepository()

    async def fail_event(**_kwargs):
        raise RuntimeError("jettask unavailable")

    service = CardRelationWriteService(
        knowledge_repository=_KnowledgeRepository(),
        relation_repository=repository,
        card_fact_repository=_CardFactRepository(),
        semantic_retriever=_SemanticRetriever(),
        relation_candidate_store=_CandidateStore(),
        graph_event_publisher=fail_event,
    )

    with pytest.raises(RuntimeError, match="jettask unavailable"):
        await service.persist_verified_decisions(
            [_decision()],
            adapter_name="financial",
            target="test",
            pipeline_version="pipeline-v1",
            model_name="model-v1",
            prompt_version="prompt-v1",
        )

    assert repository.semantic_synced
    assert repository.events_published == []
