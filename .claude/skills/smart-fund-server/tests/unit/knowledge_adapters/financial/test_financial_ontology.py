"""Financial ontology contract tests."""

from __future__ import annotations

from src.domain.knowledge.adapter import validate_edge_against_adapter
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge.schemas import EdgeDraft, NodeDraft
from src.domain.knowledge_adapters.financial.ontology import (
    CORE_ENTITY_TYPES,
    CORE_RELATION_TYPES,
    FINANCIAL_ADAPTER_SPEC,
)


def test_financial_ontology_declares_core_entities_and_relations() -> None:
    assert CORE_ENTITY_TYPES.issubset({item.name for item in FINANCIAL_ADAPTER_SPEC.entities})
    assert CORE_RELATION_TYPES.issubset({item.name for item in FINANCIAL_ADAPTER_SPEC.relations})


def test_holds_fund_to_stock_is_valid() -> None:
    edge = EdgeDraft(
        source_ref="fund:000001",
        target_ref="stock:SZ:300750",
        relation_type="holds",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=["fund_holdings:x"],
    )

    result = validate_edge_against_adapter(
        FINANCIAL_ADAPTER_SPEC,
        edge,
        NodeDraft(node_type="fund", stable_key="000001", canonical_name="F"),
        NodeDraft(node_type="stock", stable_key="SZ:300750", canonical_name="S"),
    )

    assert result.ok


def test_holds_stock_to_fund_is_invalid() -> None:
    edge = EdgeDraft(
        source_ref="stock:SZ:300750",
        target_ref="fund:000001",
        relation_type="holds",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=["fund_holdings:x"],
    )

    result = validate_edge_against_adapter(
        FINANCIAL_ADAPTER_SPEC,
        edge,
        NodeDraft(node_type="stock", stable_key="SZ:300750", canonical_name="S"),
        NodeDraft(node_type="fund", stable_key="000001", canonical_name="F"),
    )

    assert not result.ok
    assert result.issues[0].message == "invalid source node type"
