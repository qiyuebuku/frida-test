"""Query anchor extraction and validation for KG retrieval."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.schemas import CompiledNode, KnowledgeBaseModel

AnchorHintType = Literal["entity", "event", "topic", "time", "source", "relation"]
AnchorHintStrength = Literal["strong", "weak", "inferred"]
AnchorHintSource = Literal["guard", "llm", "validator"]
GuardConstraintType = Literal[
    "source_id",
    "evidence_id",
    "instrument_code",
    "date",
    "exact_entity",
    "query_phrase",
]


class AnchorHint(KnowledgeBaseModel):
    text: str
    hint_type: AnchorHintType
    strength: AnchorHintStrength = "weak"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: AnchorHintSource = "guard"


class GuardConstraint(KnowledgeBaseModel):
    value: str
    constraint_type: GuardConstraintType
    must_preserve: bool = True


class QueryAnchor(KnowledgeBaseModel):
    query: str
    core_event: str | None = None
    core_entities: list[AnchorHint] = Field(default_factory=list)
    core_topics: list[AnchorHint] = Field(default_factory=list)
    relation_intents: list[str] = Field(default_factory=list)
    time_hints: list[AnchorHint] = Field(default_factory=list)
    source_hints: list[AnchorHint] = Field(default_factory=list)
    negative_boundaries: list[str] = Field(default_factory=list)
    inferred_hints: list[AnchorHint] = Field(default_factory=list)
    guard_constraints: list[GuardConstraint] = Field(default_factory=list)
    confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


def build_guarded_query_anchor(
    query: str,
    *,
    known_nodes: list[CompiledNode] | None = None,
) -> QueryAnchor:
    """Build a deterministic anchor baseline.

    This is the guard layer, not the final semantic parser. It only extracts
    stable constraints and obvious phrases that are safe to give to an LLM
    parser or a deterministic candidate judge.
    """

    constraints = extract_guard_constraints(query, known_nodes=known_nodes)
    core_entities: list[AnchorHint] = []
    time_hints: list[AnchorHint] = []
    source_hints: list[AnchorHint] = []
    topics: list[AnchorHint] = []

    for constraint in constraints:
        if constraint.constraint_type in {"source_id", "evidence_id"}:
            source_hints.append(
                AnchorHint(
                    text=constraint.value,
                    hint_type="source",
                    strength="strong",
                    confidence=0.98,
                )
            )
        elif constraint.constraint_type in {"instrument_code", "exact_entity"}:
            core_entities.append(
                AnchorHint(
                    text=constraint.value,
                    hint_type="entity",
                    strength="strong",
                    confidence=0.95,
                )
            )
        elif constraint.constraint_type == "date":
            time_hints.append(
                AnchorHint(
                    text=constraint.value,
                    hint_type="time",
                    strength="strong",
                    confidence=0.9,
                )
            )
        elif constraint.constraint_type == "query_phrase":
            topics.append(
                AnchorHint(
                    text=constraint.value,
                    hint_type="topic",
                    strength="weak",
                    confidence=0.65,
                )
            )

    core_event = _longest_query_phrase(constraints)
    relation_intents = _relation_intents(query)
    confidence = 0.8 if core_entities or source_hints or core_event else 0.55
    if relation_intents:
        confidence = max(confidence, 0.7)
    return QueryAnchor(
        query=query,
        core_event=core_event,
        core_entities=_dedupe_hints(core_entities),
        core_topics=_dedupe_hints(topics),
        relation_intents=relation_intents,
        time_hints=_dedupe_hints(time_hints),
        source_hints=_dedupe_hints(source_hints),
        guard_constraints=constraints,
        confidence=confidence,
    )


def extract_guard_constraints(
    query: str,
    *,
    known_nodes: list[CompiledNode] | None = None,
) -> list[GuardConstraint]:
    constraints: list[GuardConstraint] = []
    for value in _ordered_unique(_SOURCE_ID_RE.findall(query)):
        constraint_type: GuardConstraintType = (
            "evidence_id" if value.startswith("kg_ev:") else "source_id"
        )
        constraints.append(GuardConstraint(value=value, constraint_type=constraint_type))
    for value in _ordered_unique(_INSTRUMENT_CODE_RE.findall(query)):
        constraints.append(GuardConstraint(value=value[-6:], constraint_type="instrument_code"))
    for value in _ordered_unique(_DATE_RE.findall(query)):
        constraints.append(GuardConstraint(value=value, constraint_type="date"))
    for node in known_nodes or []:
        names = [node.canonical_name, *node.aliases]
        for name in names:
            name = str(name).strip()
            if len(name) >= 2 and name in query:
                constraints.append(GuardConstraint(value=name, constraint_type="exact_entity"))
    for phrase in _query_phrases(query):
        constraints.append(
            GuardConstraint(
                value=phrase,
                constraint_type="query_phrase",
                must_preserve=False,
            )
        )
    return _dedupe_constraints(constraints)


def validate_query_anchor(anchor: QueryAnchor, llm_payload: dict[str, Any] | None = None) -> QueryAnchor:
    """Validate an LLM anchor payload and preserve guard constraints.

    The current implementation accepts optional LLM payloads but remains
    deterministic if no parser is wired. It never lets the LLM drop hard guard
    constraints.
    """

    if not llm_payload:
        return anchor
    warnings = list(anchor.warnings)
    merged = anchor.model_copy(deep=True)
    confidence = llm_payload.get("confidence")
    if isinstance(confidence, int | float):
        merged.confidence = max(0.0, min(float(confidence), 1.0))
    core_event = llm_payload.get("core_event")
    if isinstance(core_event, str) and core_event.strip():
        merged.core_event = core_event.strip()
    intents = llm_payload.get("relation_intents")
    if isinstance(intents, list):
        merged.relation_intents = _ordered_unique(
            str(item).strip() for item in intents if str(item).strip()
        )
    for constraint in anchor.guard_constraints:
        if constraint.must_preserve and constraint.value not in _anchor_text(merged):
            warnings.append(f"anchor guard preserved: {constraint.constraint_type}:{constraint.value}")
            if constraint.constraint_type in {"source_id", "evidence_id"}:
                merged.source_hints.append(
                    AnchorHint(text=constraint.value, hint_type="source", strength="strong", confidence=0.98)
                )
            elif constraint.constraint_type in {"instrument_code", "exact_entity"}:
                merged.core_entities.append(
                    AnchorHint(text=constraint.value, hint_type="entity", strength="strong", confidence=0.95)
                )
    merged.core_entities = _dedupe_hints(merged.core_entities)
    merged.source_hints = _dedupe_hints(merged.source_hints)
    merged.warnings = _ordered_unique(warnings)
    return merged


def anchor_terms(anchor: QueryAnchor) -> list[str]:
    entity_or_source_values = [
        *(hint.text for hint in anchor.core_entities if hint.strength == "strong"),
        *(hint.text for hint in anchor.source_hints if hint.strength == "strong"),
        *(constraint.value for constraint in anchor.guard_constraints if constraint.must_preserve),
    ]
    strong_values = [
        *(entity_or_source_values or [anchor.core_event or ""]),
        *(hint.text for hint in anchor.time_hints if hint.strength == "strong"),
    ]
    weak_values = [hint.text for hint in anchor.core_topics]
    values = strong_values or weak_values
    terms: list[str] = []
    for value in values:
        terms.extend(_tokenize(value))
    return _ordered_unique(term for term in terms if len(term) >= 2)


def _anchor_text(anchor: QueryAnchor) -> str:
    return "\n".join(
        [
            anchor.core_event or "",
            *(hint.text for hint in anchor.core_entities),
            *(hint.text for hint in anchor.core_topics),
            *(hint.text for hint in anchor.source_hints),
            *(hint.text for hint in anchor.time_hints),
        ]
    )


def _longest_query_phrase(constraints: list[GuardConstraint]) -> str | None:
    phrases = [
        constraint.value
        for constraint in constraints
        if constraint.constraint_type == "query_phrase" and len(constraint.value) >= 8
    ]
    return max(phrases, key=len) if phrases else None


def _query_phrases(query: str) -> list[str]:
    pieces = re.split(r"[\s，。！？；;,.!?、（）()【】\\[\\]\"'“”]+", query)
    phrases = [piece.strip() for piece in pieces if len(piece.strip()) >= 4]
    return _ordered_unique(phrases)[:8]


def _relation_intents(query: str) -> list[str]:
    mapping = {
        "影响": "impact",
        "利好": "beneficiary",
        "受益": "beneficiary",
        "受损": "risk",
        "风险": "risk",
        "传导": "transmission",
        "为什么": "reasoning",
        "原因": "reasoning",
        "归因": "attribution",
        "对比": "comparison",
        "复盘": "review",
    }
    return _ordered_unique(intent for word, intent in mapping.items() if word in query)


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_:.\\-]+|[\u4e00-\u9fff]+", text.lower())
    terms: list[str] = []
    for item in raw:
        if _is_cjk(item) and len(item) > 4:
            terms.append(item)
            terms.extend(item[index : index + 4] for index in range(len(item) - 3))
        else:
            terms.append(item)
    return terms


def _is_cjk(value: str) -> bool:
    return bool(value) and all("\u4e00" <= char <= "\u9fff" for char in value)


def _dedupe_constraints(values: list[GuardConstraint]) -> list[GuardConstraint]:
    result: list[GuardConstraint] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (item.constraint_type, item.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_hints(values: list[AnchorHint]) -> list[AnchorHint]:
    result: list[AnchorHint] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (item.hint_type, item.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _ordered_unique(values) -> list:
    result: list = []
    seen: set = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


_SOURCE_ID_RE = re.compile(r"\b(?:kg_ev:[A-Za-z0-9_.:-]+|[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9_.:-]+)")
_INSTRUMENT_CODE_RE = re.compile(r"\b(?:[A-Z]{1,4}[:\\-]?)?\d{6}\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?\b")
