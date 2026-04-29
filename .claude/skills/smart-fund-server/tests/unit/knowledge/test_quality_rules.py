"""Unit tests for generic quality rules."""

from __future__ import annotations

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.quality import KnowledgeQualityScanner
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.domain.knowledge.wiki import WikiPage


def test_quality_scan_flags_active_edge_without_evidence() -> None:
    nodes = _nodes()
    edge = CompiledEdge.model_construct(
        edge_id="kg_edge:toy:links:a",
        adapter_name="toy",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="links",
        properties={},
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[],
        valid_from=None,
        valid_to=None,
        version="v1",
    )

    report = KnowledgeQualityScanner().scan(
        adapter_name="toy",
        nodes=nodes,
        edges=[edge],
        evidence=[],
        wiki_pages=[],
    )

    assert any(issue.category == "active_edge_missing_evidence" for issue in report.issues)
    assert report.metrics["active_edge_evidence_coverage"] == 0.0


def test_quality_scan_flags_stale_wiki() -> None:
    nodes = _nodes(version="v2")
    page = WikiPage(
        page_id="kg_wiki:toy:entity:a",
        adapter_name="toy",
        page_type="entity_page",
        subject_type="item",
        subject_id=nodes[0].node_id,
        title="A",
        summary="A",
        content="A",
        source_node_ids=[nodes[0].node_id],
        version="v1",
    )

    report = KnowledgeQualityScanner().scan(
        adapter_name="toy",
        nodes=nodes,
        edges=[],
        evidence=[],
        wiki_pages=[page],
    )

    assert any(issue.category == "stale_wiki_page" for issue in report.issues)


def test_quality_scan_flags_missing_edge_evidence_reference() -> None:
    nodes = _nodes()
    edge = CompiledEdge(
        edge_id="kg_edge:toy:links:b",
        adapter_name="toy",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="links",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:toy:note:missing"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:toy:note:n1",
        adapter_name="toy",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="A links B.",
        version="v1",
    )

    report = KnowledgeQualityScanner().scan(
        adapter_name="toy",
        nodes=nodes,
        edges=[edge],
        evidence=[evidence],
        wiki_pages=[],
    )

    assert any(issue.category == "edge_evidence_missing" for issue in report.issues)


def _nodes(version: str = "v1") -> list[CompiledNode]:
    return [
        CompiledNode(
            node_id="kg:toy:item:a",
            adapter_name="toy",
            node_type="item",
            canonical_name="A",
            status=NodeStatus.ACTIVE,
            version=version,
        ),
        CompiledNode(
            node_id="kg:toy:item:b",
            adapter_name="toy",
            node_type="item",
            canonical_name="B",
            status=NodeStatus.ACTIVE,
            version=version,
        ),
    ]
