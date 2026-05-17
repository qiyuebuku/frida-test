"""Financial entity normalization rules before KG compilation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizationRules:
    aliases: dict[str, str]
    weak_suffixes: tuple[str, ...]
    preserved_suffixes: tuple[str, ...]
    generic_policy_suffixes: tuple[str, ...]
    concrete_policy_hints: tuple[str, ...]
    concept_taxonomy_default: str
    concept_taxonomy_industry_chain: str
    concept_taxonomy_policy_theme: str


EMPTY_NORMALIZATION_RULES = NormalizationRules(
    aliases={},
    weak_suffixes=(),
    preserved_suffixes=(),
    generic_policy_suffixes=(),
    concrete_policy_hints=(),
    concept_taxonomy_default="business",
    concept_taxonomy_industry_chain="industry_chain",
    concept_taxonomy_policy_theme="policy_theme",
)

ENTITY_TYPE_ALIASES = {
    "country": "region",
    "nation": "region",
    "province": "region",
    "city": "region",
    "location": "region",
    "area": "region",
}


def normalize_term_name(value: Any) -> str:
    """Normalize a weak-ID financial term into a canonical display name."""

    return normalize_term_name_with_rules(value, EMPTY_NORMALIZATION_RULES)


def normalize_term_name_with_rules(value: Any, rules: NormalizationRules) -> str:
    """Normalize a weak-ID financial term using database-backed rules."""

    text = _clean_text(value)
    if not text:
        return ""
    text = rules.aliases.get(text, text)
    text = _strip_weak_suffix(text, rules)
    return rules.aliases.get(text, text)


def normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Return an entity dict with canonical type/name/taxonomy/aliases."""

    return normalize_entity_with_rules(entity, EMPTY_NORMALIZATION_RULES)


def normalize_entity_type(value: Any) -> str:
    """Normalize common LLM/provider entity type aliases into financial ontology types."""

    node_type = str(value or "").strip().lower()
    return ENTITY_TYPE_ALIASES.get(node_type, node_type)


def normalize_entity_with_rules(entity: dict[str, Any], rules: NormalizationRules) -> dict[str, Any]:
    """Return an entity dict with canonical type/name/taxonomy/aliases."""

    normalized = dict(entity)
    node_type = normalize_entity_type(normalized.get("type") or normalized.get("entity_type"))
    original_type = node_type
    raw_name = _entity_name(normalized)
    canonical_name = normalize_term_name_with_rules(raw_name, rules) or raw_name or node_type

    if node_type in {"industry", "concept", "policy"}:
        node_type = _normalize_weak_entity_type(node_type, canonical_name, normalized, rules)
        if original_type == "policy" and node_type == "concept":
            canonical_name = _strip_generic_policy_suffix(canonical_name, rules)
        normalized["type"] = node_type
        normalized["name"] = canonical_name
        normalized["aliases"] = _merge_aliases(normalized.get("aliases", []), raw_name, canonical_name)
        if node_type == "concept":
            normalized["taxonomy"] = _normalize_concept_taxonomy(normalized, canonical_name, original_type, rules)
        elif node_type == "industry":
            normalized.setdefault("taxonomy", "default")
    elif node_type:
        normalized["type"] = node_type
        if raw_name:
            normalized["name"] = canonical_name
            normalized["aliases"] = _merge_aliases(normalized.get("aliases", []), raw_name, canonical_name)
    return normalized


def is_industry_chain_name(name: str) -> bool:
    return is_industry_chain_name_with_rules(name, EMPTY_NORMALIZATION_RULES)


def is_industry_chain_name_with_rules(name: str, rules: NormalizationRules) -> bool:
    return any(str(name).endswith(suffix) for suffix in rules.preserved_suffixes)


def _normalize_weak_entity_type(
    node_type: str,
    canonical_name: str,
    entity: dict[str, Any],
    rules: NormalizationRules,
) -> str:
    if node_type == "industry" and is_industry_chain_name_with_rules(canonical_name, rules):
        return "concept"
    if node_type == "policy" and not _is_concrete_policy(entity, canonical_name, rules):
        return "concept"
    return node_type


def _normalize_concept_taxonomy(
    entity: dict[str, Any],
    canonical_name: str,
    original_type: str,
    rules: NormalizationRules,
) -> str:
    if is_industry_chain_name_with_rules(canonical_name, rules):
        return rules.concept_taxonomy_industry_chain
    if original_type == "policy":
        return rules.concept_taxonomy_policy_theme
    return str(entity.get("taxonomy") or rules.concept_taxonomy_default)


def _is_concrete_policy(entity: dict[str, Any], canonical_name: str, rules: NormalizationRules) -> bool:
    if entity.get("document_id") or entity.get("policy_id"):
        return True
    if entity.get("source_name") and any(hint in canonical_name for hint in rules.concrete_policy_hints):
        return True
    concrete_policy_hints_without_generic = tuple(
        hint for hint in rules.concrete_policy_hints if hint not in rules.generic_policy_suffixes
    )
    return (
        any(hint in canonical_name for hint in concrete_policy_hints_without_generic)
        and len(canonical_name) >= 6
    )


def _entity_name(entity: dict[str, Any]) -> str:
    return _clean_text(
        entity.get("name")
        or entity.get("canonical_name")
        or entity.get("code")
        or entity.get("fund_code")
        or entity.get("indicator_code")
        or entity.get("id")
        or ""
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    return text.strip()


def _strip_weak_suffix(text: str, rules: NormalizationRules) -> str:
    if is_industry_chain_name_with_rules(text, rules):
        return text
    for suffix in rules.weak_suffixes:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            return text[: -len(suffix)]
    return text


def _strip_generic_policy_suffix(text: str, rules: NormalizationRules) -> str:
    for suffix in rules.generic_policy_suffixes:
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            return text[: -len(suffix)]
    return text


def _merge_aliases(values: Any, raw_name: str, canonical_name: str) -> list[str]:
    aliases: list[str] = []
    if isinstance(values, list):
        aliases.extend(_clean_text(item) for item in values)
    if raw_name and raw_name != canonical_name:
        aliases.append(raw_name)
    return _unique(alias for alias in aliases if alias and alias != canonical_name)


def _unique(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _tuple_text(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_clean_text(item) for item in value if _clean_text(item))
