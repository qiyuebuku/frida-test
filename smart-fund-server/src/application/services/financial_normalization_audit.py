"""Audit financial normalization rules against persisted KG facts."""

from __future__ import annotations

from typing import Any

from src.domain.knowledge.enums import ValidationSeverity
from src.domain.knowledge.ids import stable_hash
from src.domain.knowledge.quality import QualityIssue
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode

_MIN_WRITE_CONFIDENCE = 0.72


def audit_financial_normalization_rules(
    *,
    adapter_name: str,
    rules: list[dict],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
) -> tuple[list[QualityIssue], dict[str, Any]]:
    """Return quality issues and metrics for normalization-rule usage."""

    active_rules = [rule for rule in rules if rule.get("status") == "active"]
    alias_rules = [rule for rule in active_rules if rule.get("rule_type") == "alias"]
    issues: list[QualityIssue] = []

    for rule in alias_rules:
        raw_value = str(rule.get("raw_value") or "")
        canonical_value = str(rule.get("canonical_value") or "")
        rule_id = str(rule.get("rule_id") or _rule_object_id(rule))
        if not raw_value or not canonical_value:
            issues.append(
                _issue(
                    category="normalization_rule_invalid",
                    object_id=rule_id,
                    message="active alias rule must have raw_value and canonical_value",
                    severity=ValidationSeverity.ERROR,
                    details={"rule": _safe_rule(rule)},
                    review_required=True,
                )
            )
            continue

        affected_nodes = _nodes_affected_by_alias_rule(nodes, raw_value, canonical_value)
        raw_nodes = [node for node in nodes if node.canonical_name == raw_value]
        canonical_nodes = [node for node in nodes if node.canonical_name == canonical_value]

        if not affected_nodes:
            issues.append(
                _issue(
                    category="normalization_rule_unused",
                    object_id=rule_id,
                    message="active alias rule has no matching nodes yet",
                    severity=ValidationSeverity.WARNING,
                    details={"rule": _safe_rule(rule)},
                )
            )

        if raw_nodes and canonical_nodes:
            issues.append(
                _issue(
                    category="normalization_rule_migration_needed",
                    object_id=rule_id,
                    message="raw and canonical nodes both exist; historical data needs migration",
                    severity=ValidationSeverity.WARNING,
                    details={
                        "rule": _safe_rule(rule),
                        "raw_node_ids": [node.node_id for node in raw_nodes],
                        "canonical_node_ids": [node.node_id for node in canonical_nodes],
                    },
                    review_required=True,
                )
            )

        affected_types = sorted({node.node_type for node in affected_nodes})
        if len(affected_types) > 1:
            issues.append(
                _issue(
                    category="normalization_rule_cross_type_usage",
                    object_id=rule_id,
                    message="alias rule affects multiple node types",
                    severity=ValidationSeverity.WARNING,
                    details={"rule": _safe_rule(rule), "node_types": affected_types},
                    review_required=True,
                )
            )

        if str(rule.get("source") or "") == "llm_write_time" and float(rule.get("confidence") or 0.0) < _MIN_WRITE_CONFIDENCE:
            issues.append(
                _issue(
                    category="normalization_rule_low_confidence_active",
                    object_id=rule_id,
                    message="active write-time LLM rule confidence is below threshold",
                    severity=ValidationSeverity.WARNING,
                    details={"rule": _safe_rule(rule), "threshold": _MIN_WRITE_CONFIDENCE},
                    review_required=True,
                )
            )
        if str(rule.get("source") or "") == "llm_write_time" and not (rule.get("payload") or {}).get("audit_status"):
            issues.append(
                _issue(
                    category="normalization_rule_missing_audit_payload",
                    object_id=rule_id,
                    message="write-time LLM rule is missing audit payload",
                    severity=ValidationSeverity.WARNING,
                    details={"rule": _safe_rule(rule)},
                    review_required=True,
                )
            )

    quarantine_evidence = [item for item in evidence if item.payload.get("_normalization_quarantine")]
    merge_mode_counts = _merge_mode_counts(evidence)
    for item in quarantine_evidence:
        issues.append(
            _issue(
                category="normalization_quarantine_observed",
                object_id=item.evidence_id,
                object_type="evidence",
                message="evidence contains entities quarantined by write-time normalization",
                severity=ValidationSeverity.WARNING,
                details={
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "quarantine_count": len(item.payload.get("_normalization_quarantine") or []),
                },
            )
        )

    metrics = {
        "normalization_active_rules": len(active_rules),
        "normalization_alias_rules": len(alias_rules),
        "normalization_rule_issues": len(issues),
        "normalization_migration_needed": len(
            [issue for issue in issues if issue.category == "normalization_rule_migration_needed"]
        ),
        "normalization_quarantine_evidence": len(quarantine_evidence),
        "normalization_rule_edge_scope": _edge_scope_for_normalized_nodes(nodes, edges),
        "normalization_merge_modes": merge_mode_counts,
    }
    return issues, metrics


def plan_financial_normalization_migration(
    *,
    adapter_name: str,
    rules: list[dict],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
) -> dict[str, Any]:
    """Build a deterministic migration plan for historical nodes affected by active rules.

    The plan is intentionally read-only. It gives the operator and future automation a
    stable audit artifact before any historical node/edge rewrite is allowed.
    """

    node_by_id = {node.node_id: node for node in nodes}
    edge_scope_by_node = _edge_scope_by_node(edges)
    active_alias_rules = [
        rule
        for rule in rules
        if rule.get("status") == "active" and rule.get("rule_type") == "alias"
    ]
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for rule in active_alias_rules:
        raw_value = str(rule.get("raw_value") or "")
        canonical_value = str(rule.get("canonical_value") or "")
        rule_id = str(rule.get("rule_id") or _rule_object_id(rule))
        if not raw_value or not canonical_value or raw_value == canonical_value:
            skipped.append({"rule_id": rule_id, "reason": "invalid_or_noop_alias_rule", "rule": _safe_rule(rule)})
            continue

        raw_nodes = [node for node in nodes if node.adapter_name == adapter_name and node.canonical_name == raw_value]
        canonical_nodes = [
            node for node in nodes if node.adapter_name == adapter_name and node.canonical_name == canonical_value
        ]
        if not raw_nodes:
            skipped.append({"rule_id": rule_id, "reason": "raw_node_not_found", "rule": _safe_rule(rule)})
            continue
        if not canonical_nodes:
            skipped.append({"rule_id": rule_id, "reason": "canonical_node_not_found", "rule": _safe_rule(rule)})
            continue

        for raw_node in raw_nodes:
            compatible_canonical_nodes = [
                node
                for node in canonical_nodes
                if node.node_type == raw_node.node_type
            ]
            if not compatible_canonical_nodes:
                skipped.append(
                    {
                        "rule_id": rule_id,
                        "reason": "cross_type_merge_blocked",
                        "raw_node_id": raw_node.node_id,
                        "raw_node_type": raw_node.node_type,
                        "canonical_node_types": sorted({node.node_type for node in canonical_nodes}),
                    }
                )
                continue
            canonical_node = compatible_canonical_nodes[0]
            touched_edge_ids = sorted(edge_scope_by_node.get(raw_node.node_id, set()))
            actions.append(
                {
                    "action": "merge_node_into_canonical",
                    "merge_mode": "hard_merge" if float(rule.get("confidence") or 0.0) >= 0.9 else "soft_merge",
                    "rule_id": rule_id,
                    "raw_node_id": raw_node.node_id,
                    "raw_node_name": raw_node.canonical_name,
                    "canonical_node_id": canonical_node.node_id,
                    "canonical_node_name": canonical_node.canonical_name,
                    "node_type": raw_node.node_type,
                    "touched_edge_ids": touched_edge_ids,
                    "affected_edges": len(touched_edge_ids),
                    "rule": _safe_rule(rule),
                }
            )

    quarantine_evidence_ids = [
        item.evidence_id
        for item in evidence
        if item.payload.get("_normalization_quarantine")
    ]
    return {
        "adapter_name": adapter_name,
        "actions": actions,
        "skipped": skipped,
        "summary": {
            "active_alias_rules": len(active_alias_rules),
            "planned_actions": len(actions),
            "skipped_rules": len(skipped),
            "affected_edges": sum(int(action.get("affected_edges") or 0) for action in actions),
            "quarantine_evidence": len(quarantine_evidence_ids),
        },
        "quarantine_evidence_ids": quarantine_evidence_ids,
    }


def _nodes_affected_by_alias_rule(nodes: list[CompiledNode], raw_value: str, canonical_value: str) -> list[CompiledNode]:
    result: list[CompiledNode] = []
    for node in nodes:
        normalization = _normalization_metadata(node)
        if node.canonical_name == canonical_value and raw_value in node.aliases:
            result.append(node)
        elif normalization.get("raw_name") == raw_value and normalization.get("canonical_name") == canonical_value:
            result.append(node)
        elif node.canonical_name == raw_value:
            result.append(node)
    return result


def _normalization_metadata(node: CompiledNode) -> dict[str, Any]:
    metadata = node.properties.get("normalization")
    return metadata if isinstance(metadata, dict) else {}


def _edge_scope_for_normalized_nodes(nodes: list[CompiledNode], edges: list[CompiledEdge]) -> dict[str, int]:
    normalized_node_ids = {
        node.node_id
        for node in nodes
        if _normalization_metadata(node)
    }
    touched_edges = [
        edge for edge in edges
        if edge.source_node_id in normalized_node_ids or edge.target_node_id in normalized_node_ids
    ]
    return {"normalized_nodes": len(normalized_node_ids), "touched_edges": len(touched_edges)}


def _edge_scope_by_node(edges: list[CompiledEdge]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for edge in edges:
        result.setdefault(edge.source_node_id, set()).add(edge.edge_id)
        result.setdefault(edge.target_node_id, set()).add(edge.edge_id)
    return result


def _merge_mode_counts(evidence: list[CompiledEvidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        for decision in item.payload.get("_normalization_decisions") or []:
            if not isinstance(decision, dict):
                continue
            mode = str(decision.get("merge_mode") or "unknown")
            counts[mode] = counts.get(mode, 0) + 1
        for quarantine in item.payload.get("_normalization_quarantine") or []:
            if isinstance(quarantine, dict):
                counts["block_merge"] = counts.get("block_merge", 0) + 1
    return counts


def _issue(
    *,
    category: str,
    object_id: str,
    message: str,
    severity: ValidationSeverity,
    details: dict[str, Any],
    object_type: str = "normalization_rule",
    review_required: bool = False,
) -> QualityIssue:
    issue_id = stable_hash([category, object_type, object_id, message])
    return QualityIssue(
        issue_id=issue_id,
        severity=severity,
        category=category,
        object_type=object_type,
        object_id=object_id,
        message=message,
        details=details,
        review_required=review_required,
    )


def _rule_object_id(rule: dict) -> str:
    return f"kg_norm_rule:{rule.get('adapter_name') or 'financial'}:{rule.get('rule_type')}:{stable_hash([rule.get('raw_value'), rule.get('status')])}"


def _safe_rule(rule: dict) -> dict[str, Any]:
    return {
        "rule_id": rule.get("rule_id"),
        "rule_type": rule.get("rule_type"),
        "raw_value": rule.get("raw_value"),
        "canonical_value": rule.get("canonical_value"),
        "status": rule.get("status"),
        "confidence": rule.get("confidence"),
        "source": rule.get("source"),
        "version": rule.get("version"),
    }
