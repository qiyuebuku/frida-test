"""Financial normalization rule audit tests."""

from __future__ import annotations

from src.application.services.financial_normalization_audit import (
    audit_financial_normalization_rules,
    plan_financial_normalization_migration,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode


def test_normalization_audit_flags_migration_needed_for_raw_and_canonical_nodes() -> None:
    rules = [
        {
            "rule_id": "rule:红利资产",
            "adapter_name": "financial",
            "rule_type": "alias",
            "raw_value": "红利资产",
            "canonical_value": "高股息",
            "status": "active",
            "confidence": 0.95,
            "source": "llm_write_time",
            "version": "v1",
        }
    ]
    nodes = [
        _node("kg:financial:concept:raw", "concept", "红利资产"),
        _node("kg:financial:concept:canonical", "concept", "高股息", aliases=["红利资产"]),
    ]

    issues, metrics = audit_financial_normalization_rules(
        adapter_name="financial",
        rules=rules,
        nodes=nodes,
        edges=[],
        evidence=[],
    )

    assert any(issue.category == "normalization_rule_migration_needed" for issue in issues)
    assert metrics["normalization_migration_needed"] == 1


def test_normalization_audit_flags_low_confidence_active_llm_rule() -> None:
    rules = [
        {
            "rule_id": "rule:低置信",
            "adapter_name": "financial",
            "rule_type": "alias",
            "raw_value": "红利策略",
            "canonical_value": "高股息",
            "status": "active",
            "confidence": 0.4,
            "source": "llm_write_time",
            "version": "v1",
        }
    ]

    issues, _metrics = audit_financial_normalization_rules(
        adapter_name="financial",
        rules=rules,
        nodes=[_node("kg:financial:concept:canonical", "concept", "高股息", aliases=["红利策略"])],
        edges=[],
        evidence=[],
    )

    assert any(issue.category == "normalization_rule_low_confidence_active" for issue in issues)


def test_normalization_audit_reports_quarantine_evidence() -> None:
    evidence = [
        CompiledEvidence(
            evidence_id="kg_ev:financial:news_articles:1:abc",
            adapter_name="financial",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="news_articles",
            source_id="news:1",
            payload={"_normalization_quarantine": [{"entity": {"name": "风险资产"}}]},
            version="v1",
        )
    ]

    issues, metrics = audit_financial_normalization_rules(
        adapter_name="financial",
        rules=[],
        nodes=[],
        edges=[],
        evidence=evidence,
    )

    assert any(issue.category == "normalization_quarantine_observed" for issue in issues)
    assert metrics["normalization_quarantine_evidence"] == 1
    assert metrics["normalization_merge_modes"]["block_merge"] == 1


def test_normalization_audit_counts_edges_touching_normalized_nodes() -> None:
    node = _node(
        "kg:financial:concept:canonical",
        "concept",
        "高股息",
        properties={"normalization": {"decision": "create_new_alias_rule"}},
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:1",
        adapter_name="financial",
        source_node_id="kg:financial:event:1",
        target_node_id=node.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news_articles:1:abc"],
        version="v1",
    )

    _issues, metrics = audit_financial_normalization_rules(
        adapter_name="financial",
        rules=[],
        nodes=[node],
        edges=[edge],
        evidence=[],
    )

    assert metrics["normalization_rule_edge_scope"] == {"normalized_nodes": 1, "touched_edges": 1}


def test_normalization_migration_plan_builds_read_only_actions() -> None:
    rule = {
        "rule_id": "rule:红利资产",
        "adapter_name": "financial",
        "rule_type": "alias",
        "raw_value": "红利资产",
        "canonical_value": "高股息",
        "status": "active",
        "confidence": 0.95,
        "source": "llm_write_time",
        "version": "v1",
    }
    raw = _node("kg:financial:concept:raw", "concept", "红利资产")
    canonical = _node("kg:financial:concept:canonical", "concept", "高股息")
    edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:1",
        adapter_name="financial",
        source_node_id="kg:financial:event:1",
        target_node_id=raw.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news_articles:1:abc"],
        version="v1",
    )

    plan = plan_financial_normalization_migration(
        adapter_name="financial",
        rules=[rule],
        nodes=[raw, canonical],
        edges=[edge],
        evidence=[],
    )

    assert plan["summary"]["planned_actions"] == 1
    assert plan["summary"]["affected_edges"] == 1
    assert plan["actions"][0]["merge_mode"] == "hard_merge"
    assert plan["actions"][0]["raw_node_id"] == raw.node_id
    assert plan["actions"][0]["canonical_node_id"] == canonical.node_id


def test_normalization_migration_plan_blocks_cross_type_merge() -> None:
    plan = plan_financial_normalization_migration(
        adapter_name="financial",
        rules=[
            {
                "rule_id": "rule:新能源车产业链",
                "adapter_name": "financial",
                "rule_type": "alias",
                "raw_value": "新能源车产业链",
                "canonical_value": "新能源车",
                "status": "active",
                "confidence": 0.8,
                "source": "llm_write_time",
                "version": "v1",
            }
        ],
        nodes=[
            _node("kg:financial:concept:raw", "concept", "新能源车产业链"),
            _node("kg:financial:industry:canonical", "industry", "新能源车"),
        ],
        edges=[],
        evidence=[],
    )

    assert plan["summary"]["planned_actions"] == 0
    assert plan["skipped"][0]["reason"] == "cross_type_merge_blocked"


def _node(
    node_id: str,
    node_type: str,
    canonical_name: str,
    *,
    aliases: list[str] | None = None,
    properties: dict | None = None,
) -> CompiledNode:
    return CompiledNode(
        node_id=node_id,
        adapter_name="financial",
        node_type=node_type,
        canonical_name=canonical_name,
        aliases=aliases or [],
        properties=properties or {},
        status=NodeStatus.ACTIVE,
        version="v1",
    )
