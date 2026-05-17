"""Relation type normalization for financial candidate facts."""

from __future__ import annotations

from typing import Any

from src.domain.knowledge_adapters.financial.ontology import CORE_RELATION_TYPES


_RELATION_ALIASES = {
    "impact": "affects",
    "impacts": "affects",
    "affect": "affects",
    "influences": "affects",
    "influence": "affects",
    "drives": "affects",
    "boosts": "affects",
    "benefits": "affects",
    "benefits_from": "benefits_from",
    "supports": "affects",
    "promotes": "affects",
    "pressures": "affects",
    "hurts": "affects",
    "weighs_on": "affects",
    "causes": "causal_hint",
    "leads_to": "causal_hint",
    "triggers": "causal_hint",
    "mentions": "mentions",
    "mentioned": "mentions",
    "references": "mentions",
    "related": "related_to",
    "related_to": "related_to",
    "associated_with": "related_to",
    "conflicts_with": "related_to",
    "opposes": "related_to",
    "competes_with": "related_to",
    "located_in": "related_to",
    "based_in": "related_to",
    "part_of": "related_to",
}


_NEGATIVE_RELATIONS = {"pressures", "hurts", "weighs_on", "conflicts_with", "opposes"}
_POSITIVE_RELATIONS = {"supports", "promotes", "boosts", "benefits"}


def normalize_candidate_relation_type(
    relation_type: Any,
    *,
    direction: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Map LLM-friendly relation labels to ontology relation types.

    Unknown labels are deliberately downgraded to ``related_to`` instead of
    failing compilation. The original label is preserved in edge properties so
    quality scans can audit whether a new ontology relation is needed.
    """

    original = str(relation_type or "").strip()
    normalized_key = _normalize_key(original)
    mapped = _RELATION_ALIASES.get(normalized_key)
    relation = mapped if mapped in CORE_RELATION_TYPES else None
    if relation is None:
        relation = normalized_key if normalized_key in CORE_RELATION_TYPES else "related_to"

    metadata: dict[str, Any] = {}
    if original and relation != original:
        metadata["original_relation_type"] = original
        metadata["relation_type_normalized"] = True
    if normalized_key in _NEGATIVE_RELATIONS and not direction:
        metadata["direction"] = "negative"
    elif normalized_key in _POSITIVE_RELATIONS and not direction:
        metadata["direction"] = "positive"
    if mapped is None and normalized_key not in CORE_RELATION_TYPES:
        metadata["relation_type_fallback"] = "unknown_to_related_to"
    return relation, metadata


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")
