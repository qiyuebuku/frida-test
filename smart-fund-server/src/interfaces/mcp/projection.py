"""Compact MCP projections for relation-graph Agent tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def project_tool_result(
    tool_name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove storage diagnostics while preserving evidence and graph handles."""

    projected: dict[str, Any] = _select(
        result,
        (
            "operation",
            "query",
            "seed_card_ids",
            "seed_community_ids",
            "truncated",
            "missing_card_ids",
            "missing_edge_ids",
            "missing_community_ids",
            "missing_summary_card_ids",
            "missing_focus_evidence_card_ids",
            "incident_relations_truncated",
            "next_operations",
        ),
    )
    if isinstance(result.get("cards"), list):
        projected["cards"] = [
            _project_card(
                card,
                include_evidence=tool_name == "kg_card_open",
            )
            for card in result["cards"]
            if isinstance(card, Mapping)
        ]
    if isinstance(result.get("edges"), list):
        projected["edges"] = [
            _project_edge(
                edge,
                include_evidence=tool_name == "kg_edge_open",
            )
            for edge in result["edges"]
            if isinstance(edge, Mapping)
        ]
    if (
        tool_name in {"kg_community_expand", "kg_community_open"}
        and isinstance(result.get("communities"), list)
    ):
        projected["communities"] = [
            _project_community(
                community,
                include_members=tool_name == "kg_community_open",
            )
            for community in result["communities"]
            if isinstance(community, Mapping)
        ]
    if isinstance(result.get("community_relations"), list):
        projected["community_relations"] = [
            _select(
                relation,
                (
                    "relation_id",
                    "source_community_id",
                    "target_community_id",
                    "relation_kind",
                    "edge_count",
                    "confidence",
                    "hop",
                ),
            )
            for relation in result["community_relations"]
            if isinstance(relation, Mapping)
        ]
    return projected


def _project_card(
    card: Mapping[str, Any],
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    fields = [
        "card_id",
        "fact_id",
        "summary",
        "source_id",
        "source_published_at",
        "relation_ids",
        "hop",
        "fact_card_count",
    ]
    if include_evidence:
        fields.extend(
            (
                "focus_evidence",
                "evidence_id",
                "primary_chunk_id",
                "community_ids",
            )
        )
    return _select(card, fields)


def _project_edge(
    edge: Mapping[str, Any],
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    fields = [
        "edge_id",
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "relation_type",
        "direction",
        "decision_class",
        "confidence",
    ]
    if include_evidence:
        fields.extend(("basis", "inference_mechanism"))
    projected = _select(edge, fields)
    if include_evidence:
        for endpoint_name in ("source_card", "target_card"):
            endpoint = edge.get(endpoint_name)
            if isinstance(endpoint, Mapping):
                projected[endpoint_name] = _select(
                    endpoint,
                    (
                        "card_id",
                        "fact_id",
                        "summary",
                        "focus_evidence",
                        "source_id",
                        "source_published_at",
                    ),
                )
    return projected


def _project_community(
    community: Mapping[str, Any],
    *,
    include_members: bool,
) -> dict[str, Any]:
    projected = _select(
        community,
        (
            "community_id",
            "title",
            "representative_summary",
            "identity_anchor_card_id",
            "card_count",
            "edge_count",
            "graph_version",
            "graph_changed_at",
            "hop",
            "members_truncated",
            "edges_truncated",
        ),
    )
    if include_members:
        projected["members"] = [
            _select(
                member,
                (
                    "card_id",
                    "summary",
                    "source_id",
                    "source_published_at",
                ),
            )
            for member in community.get("members", [])
            if isinstance(member, Mapping)
        ]
        projected["edges"] = [
            _project_edge(edge, include_evidence=False)
            for edge in community.get("edges", [])
            if isinstance(edge, Mapping)
        ]
    return projected


def _select(
    value: Mapping[str, Any],
    fields: Iterable[str],
) -> dict[str, Any]:
    return {
        key: value[key]
        for key in fields
        if key in value and value[key] not in (None, "", [], {})
    }
