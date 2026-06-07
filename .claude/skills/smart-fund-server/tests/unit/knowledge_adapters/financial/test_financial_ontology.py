"""Financial ontology contract tests."""

from __future__ import annotations

from src.domain.knowledge.adapter import validate_edge_against_adapter
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge.schemas import EdgeDraft, NodeDraft
from src.domain.knowledge_adapters.financial.ontology import (
    CORE_ENTITY_TYPES,
    CORE_RELATION_TYPES,
    FINANCIAL_ADAPTER_SPEC,
    extend_financial_adapter_spec,
)


def test_financial_ontology_declares_core_entities_and_relations() -> None:
    assert CORE_ENTITY_TYPES.issubset({item.name for item in FINANCIAL_ADAPTER_SPEC.entities})
    assert CORE_RELATION_TYPES.issubset({item.name for item in FINANCIAL_ADAPTER_SPEC.relations})


def test_type_registry_can_extend_financial_adapter_spec() -> None:
    spec = extend_financial_adapter_spec(
        extra_entity_types={"infrastructure_theme"},
        extra_relation_types={"constrains"},
    )

    assert "infrastructure_theme" in {item.name for item in spec.entities}
    assert "constrains" in {item.name for item in spec.relations}
    result = validate_edge_against_adapter(
        spec,
        EdgeDraft(
            source_ref="event:news-001",
            target_ref="infrastructure_theme:ai-compute",
            relation_type="mentions",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.8,
            status=EdgeStatus.CANDIDATE,
            evidence_refs=["news:x"],
        ),
        NodeDraft(node_type="event", stable_key="news-001", canonical_name="新闻"),
        NodeDraft(node_type="infrastructure_theme", stable_key="ai-compute", canonical_name="AI算力基础设施"),
    )

    assert result.ok


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


def test_event_can_mention_product_and_supplier_entities() -> None:
    for target_type, target_key, target_name in [
        ("product", "储能电芯", "储能电芯"),
        ("supplier", "电池设备供应商", "电池设备供应商"),
    ]:
        edge = EdgeDraft(
            source_ref="event:news-001",
            target_ref=f"{target_type}:{target_key}",
            relation_type="mentions",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.8,
            status=EdgeStatus.ACTIVE,
            evidence_refs=["news:x"],
        )

        result = validate_edge_against_adapter(
            FINANCIAL_ADAPTER_SPEC,
            edge,
            NodeDraft(node_type="event", stable_key="news-001", canonical_name="新闻"),
            NodeDraft(node_type=target_type, stable_key=target_key, canonical_name=target_name),
        )

        assert result.ok


def test_region_can_have_candidate_related_to_region() -> None:
    edge = EdgeDraft(
        source_ref="region:伊朗",
        target_ref="region:美国",
        relation_type="related_to",
        confidence_label=ConfidenceLabel.INFERRED,
        confidence_score=0.6,
        status=EdgeStatus.CANDIDATE,
        evidence_refs=["news:x"],
    )

    result = validate_edge_against_adapter(
        FINANCIAL_ADAPTER_SPEC,
        edge,
        NodeDraft(node_type="region", stable_key="伊朗", canonical_name="伊朗"),
        NodeDraft(node_type="region", stable_key="美国", canonical_name="美国"),
    )

    assert result.ok


def test_region_can_belong_to_region() -> None:
    edge = EdgeDraft(
        source_ref="region:波兰",
        target_ref="region:欧洲",
        relation_type="belongs_to",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.8,
        status=EdgeStatus.CANDIDATE,
        evidence_refs=["news:x"],
    )

    result = validate_edge_against_adapter(
        FINANCIAL_ADAPTER_SPEC,
        edge,
        NodeDraft(node_type="region", stable_key="波兰", canonical_name="波兰"),
        NodeDraft(node_type="region", stable_key="欧洲", canonical_name="欧洲"),
    )

    assert result.ok
