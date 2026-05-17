"""Tests for retrieval document generation."""

from __future__ import annotations

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval_document import build_retrieval_document_version, build_retrieval_documents
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode


def test_build_retrieval_documents_adds_readable_relations_and_evidence_summary() -> None:
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        external_ids={"code": "300750"},
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    event = CompiledNode(
        node_id="kg:financial:event:overseas",
        adapter_name="financial",
        node_type="event",
        canonical_name="宁德时代海外产能扩张",
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:1",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=stock.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="notebook:catl",
        content="宁德时代推进欧洲和东南亚海外产能扩张，改善供应链交付能力。",
        payload={"title": "海外产能扩张"},
        version="v1",
    )

    documents = build_retrieval_documents(
        adapter_name="financial",
        target="prod",
        nodes=[stock, event],
        edges=[edge],
        evidence=[evidence],
    )

    event_doc = next(item for item in documents if item.source_fact_id == event.node_id)
    edge_doc = next(item for item in documents if item.source_fact_id == edge.edge_id)
    evidence_doc = next(item for item in documents if item.source_fact_id == evidence.evidence_id)

    assert event_doc.answer_candidate_type == "answer"
    assert "宁德时代海外产能扩张 affects 宁德时代" in event_doc.readable_relations
    assert "改善供应链交付能力" in event_doc.evidence_summary
    assert edge_doc.answer_candidate_type == "support"
    assert edge_doc.relation_intents == ["impact"]
    assert evidence_doc.answer_candidate_type == "answer"
    assert "改善供应链交付能力" in evidence_doc.search_text

    version = build_retrieval_document_version(
        adapter_name="financial",
        target="prod",
        documents=documents,
        nodes=[stock, event],
        edges=[edge],
        evidence=[evidence],
        wiki_pages=[],
        config={"compile_run_id": "run-1"},
    )

    assert version.changed_fact_set["node_ids"] == [stock.node_id, event.node_id]
    assert version.changed_fact_set["edge_ids"] == [edge.edge_id]
    assert version.changed_fact_set["evidence_ids"] == [evidence.evidence_id]
    assert version.field_coverage["total_documents"] == len(documents)
    assert version.field_coverage["filled_counts"]["search_text"] == len(documents)


def test_evidence_retrieval_document_uses_structured_aliases_without_json_noise() -> None:
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:stock:300750",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="stock_basics",
        source_id="notebook_baseline:stock:300750",
        content="",
        payload={
            "aliases": ["CATL", "300750", "300750.SZ"],
            "code": "300750",
            "company_name": "宁德时代新能源科技股份有限公司",
            "exchange": "SZ",
            "name": "宁德时代",
            "status": "active",
        },
        version="v1",
    )

    document = next(
        item
        for item in build_retrieval_documents(
            adapter_name="financial",
            target="prod",
            nodes=[],
            edges=[],
            evidence=[evidence],
        )
        if item.source_fact_id == evidence.evidence_id
    )

    assert document.aliases == ["notebook_baseline:stock:300750", "CATL", "300750", "300750.SZ"]
    assert "宁德时代新能源科技股份有限公司" in document.evidence_summary
    assert "CATL" in document.search_text
    assert "company_name" not in document.key_phrases
    assert all(not phrase.startswith("{") for phrase in document.key_phrases)
