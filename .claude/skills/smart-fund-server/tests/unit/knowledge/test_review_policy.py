"""Unit tests for review queue creation."""

from __future__ import annotations

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.quality import KnowledgeQualityScanner
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode


def test_review_entries_created_for_review_required_issues() -> None:
    nodes = [
        CompiledNode(
            node_id="kg:toy:item:a",
            adapter_name="toy",
            node_type="item",
            canonical_name="Same",
            status=NodeStatus.ACTIVE,
            version="v1",
        ),
        CompiledNode(
            node_id="kg:toy:item:b",
            adapter_name="toy",
            node_type="item",
            canonical_name="Same",
            status=NodeStatus.ACTIVE,
            version="v1",
        ),
    ]
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

    scanner = KnowledgeQualityScanner()
    report = scanner.scan(adapter_name="toy", nodes=nodes, edges=[edge], evidence=[], wiki_pages=[])
    entries = scanner.review_entries_for(report)

    assert entries
    assert all(entry.status == "open" for entry in entries)
    assert {entry.payload["category"] for entry in entries}.issuperset(
        {"active_edge_missing_evidence", "duplicate_canonical_name"}
    )
