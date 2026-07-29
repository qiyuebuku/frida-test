"""Derived cognition contracts for relationship-first Graph Communities."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


FACT_REPORT_GENERATOR_VERSION = "relation_graph_fact_report_v9_fact_identity"
PROJECTION_GENERATOR_VERSION = "relation_graph_projection_v7"


@dataclass(frozen=True)
class CommunityCardMaterial:
    alias: str
    card_id: str
    summary: str
    source_alias: str = ""
    source_published_at: str = ""
    fact_card_count: int = 1

    def prompt_dict(self) -> dict[str, Any]:
        payload = {
            "alias": self.alias,
            "summary": self.summary,
        }
        if self.fact_card_count > 1:
            payload["fact_card_count"] = self.fact_card_count
        return payload


@dataclass(frozen=True)
class CommunityEdgeMaterial:
    alias: str
    edge_id: str
    source_card_alias: str
    target_card_alias: str
    relation_kind: str
    relation_type: str
    direction: str
    decision_class: str
    basis: str
    inference_mechanism: str = ""

    def prompt_dict(self) -> dict[str, Any]:
        payload = {
            "alias": self.alias,
            "source_card_alias": self.source_card_alias,
            "target_card_alias": self.target_card_alias,
            "relation_kind": self.relation_kind,
            "relation_type": self.relation_type,
            "decision_class": self.decision_class,
            "basis": self.basis,
        }
        if (
            self.inference_mechanism
            and _normalized_text(self.inference_mechanism)
            != _normalized_text(self.basis)
        ):
            payload["inference_mechanism"] = self.inference_mechanism
        return payload


@dataclass(frozen=True)
class CommunityCognitionMaterial:
    community_id: str
    adapter_name: str
    graph_fingerprint: str
    graph_version: int
    cards: tuple[CommunityCardMaterial, ...]
    edges: tuple[CommunityEdgeMaterial, ...]

    @property
    def card_alias_to_id(self) -> dict[str, str]:
        return {item.alias: item.card_id for item in self.cards}

    @property
    def edge_alias_to_id(self) -> dict[str, str]:
        return {item.alias: item.edge_id for item in self.edges}

    def fact_payload(self) -> dict[str, Any]:
        observed_relations = []
        inferred_relations = []
        for item in self.edges:
            payload = item.prompt_dict()
            payload.pop("decision_class", None)
            if item.decision_class == "observed":
                observed_relations.append(payload)
            else:
                inferred_relations.append(payload)
        return {
            "source_context": self.source_context(),
            "cards": [item.prompt_dict() for item in self.cards],
            "relations": {
                "observed": observed_relations,
                "inferred": inferred_relations,
            },
        }

    def source_context(self) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for card in self.cards:
            group = groups.setdefault(
                card.source_alias,
                {
                    "source_alias": card.source_alias,
                    "card_aliases": [],
                    "published_at_values": [],
                },
            )
            group["card_aliases"].append(card.alias)
            if card.source_published_at:
                group["published_at_values"].append(
                    card.source_published_at
                )

        sources: list[dict[str, Any]] = []
        for group in groups.values():
            published = sorted(set(group.pop("published_at_values")))
            source = {
                "source_alias": group["source_alias"],
                "card_aliases": group["card_aliases"],
            }
            if len(published) == 1:
                source["source_published_at"] = published[0]
            elif published:
                source["source_published_at_start"] = published[0]
                source["source_published_at_end"] = published[-1]
            sources.append(source)
        return {
            "source_record_count": len(sources),
            "sources": sources,
        }

    def projection_payload(
        self,
        *,
        title: str,
        fact_report: str,
    ) -> dict[str, Any]:
        return {
            "fact_report": {
                "title": title,
                "report_text": fact_report,
            },
            "cards": [
                {
                    "alias": item.alias,
                    "summary": item.summary,
                    "source_published_at": item.source_published_at,
                    **(
                        {"fact_card_count": item.fact_card_count}
                        if item.fact_card_count > 1
                        else {}
                    ),
                }
                for item in self.cards
            ],
            "edges": [
                {
                    "alias": item.alias,
                    "source_card_alias": item.source_card_alias,
                    "target_card_alias": item.target_card_alias,
                    "relation_kind": item.relation_kind,
                    "decision_class": item.decision_class,
                    "basis": item.basis,
                    **(
                        {"inference_mechanism": item.inference_mechanism}
                        if item.inference_mechanism
                        else {}
                    ),
                }
                for item in self.edges
            ],
        }


@dataclass(frozen=True)
class CommunityFactReport:
    title: str
    report_text: str
    referenced_card_ids: tuple[str, ...]
    referenced_edge_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConditionalProjection:
    conditional_judgement: str
    conditions: tuple[str, ...]
    possible_result: str
    observation_indicators: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    time_horizon: str
    supporting_card_ids: tuple[str, ...]
    supporting_edge_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "conditional_judgement": self.conditional_judgement,
            "conditions": list(self.conditions),
            "possible_result": self.possible_result,
            "observation_indicators": list(self.observation_indicators),
            "invalidation_conditions": list(self.invalidation_conditions),
            "time_horizon": self.time_horizon,
            "supporting_card_ids": list(self.supporting_card_ids),
            "supporting_edge_ids": list(self.supporting_edge_ids),
        }


FACT_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "report_text",
        "title",
        "referenced_card_aliases",
        "referenced_edge_aliases",
    ],
    "properties": {
        "report_text": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "referenced_card_aliases": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "referenced_edge_aliases": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    },
}


PROJECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projections"],
    "properties": {
        "projections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "conditional_judgement",
                    "conditions",
                    "possible_result",
                    "observation_indicators",
                    "invalidation_conditions",
                    "time_horizon",
                    "supporting_card_aliases",
                    "supporting_edge_aliases",
                ],
                "properties": {
                    "conditional_judgement": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "possible_result": {"type": "string", "minLength": 1},
                    "observation_indicators": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "invalidation_conditions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "time_horizon": {"type": "string", "minLength": 1},
                    "supporting_card_aliases": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "supporting_edge_aliases": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        }
    },
}


def parse_fact_report(
    value: dict[str, Any],
    material: CommunityCognitionMaterial,
) -> CommunityFactReport:
    title = _required_text(value.get("title"), "title")
    report_text = _required_text(value.get("report_text"), "report_text")
    card_aliases = _unique_texts(
        value.get("referenced_card_aliases"),
        "referenced_card_aliases",
    )
    edge_aliases = _unique_texts(
        value.get("referenced_edge_aliases"),
        "referenced_edge_aliases",
    )
    card_alias_to_id = material.card_alias_to_id
    edge_alias_to_id = material.edge_alias_to_id
    _validate_aliases(card_aliases, set(card_alias_to_id), "Card")
    _validate_aliases(edge_aliases, set(edge_alias_to_id), "Edge")

    # A Community report represents the complete current component. Omitting an
    # input member would make the published report inconsistent with its graph.
    missing_cards = sorted(set(card_alias_to_id) - set(card_aliases))
    missing_edges = sorted(set(edge_alias_to_id) - set(edge_aliases))
    if missing_cards or missing_edges:
        raise ValueError(
            "事实报告未覆盖完整关系子图: "
            f"missing_cards={missing_cards} missing_edges={missing_edges}"
        )
    _reject_alias_mentions(
        [title, report_text],
        aliases={*card_alias_to_id, *edge_alias_to_id},
        field="事实报告",
    )
    return CommunityFactReport(
        title=title,
        report_text=report_text,
        referenced_card_ids=tuple(card_alias_to_id[item] for item in card_aliases),
        referenced_edge_ids=tuple(edge_alias_to_id[item] for item in edge_aliases),
    )


def parse_conditional_projections(
    value: dict[str, Any],
    material: CommunityCognitionMaterial,
) -> tuple[ConditionalProjection, ...]:
    raw_items = value.get("projections")
    if not isinstance(raw_items, list):
        raise ValueError("projections 必须是数组")
    card_alias_to_id = material.card_alias_to_id
    edge_alias_to_id = material.edge_alias_to_id
    result: list[ConditionalProjection] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"projections[{index}] 必须是对象")
        card_aliases = _unique_texts(
            raw.get("supporting_card_aliases"),
            f"projections[{index}].supporting_card_aliases",
        )
        edge_aliases = _unique_texts(
            raw.get("supporting_edge_aliases"),
            f"projections[{index}].supporting_edge_aliases",
        )
        _validate_aliases(card_aliases, set(card_alias_to_id), "Card")
        _validate_aliases(edge_aliases, set(edge_alias_to_id), "Edge")
        projection = ConditionalProjection(
            conditional_judgement=_required_text(
                raw.get("conditional_judgement"),
                f"projections[{index}].conditional_judgement",
            ),
            conditions=tuple(
                _nonempty_list(
                    raw.get("conditions"),
                    f"projections[{index}].conditions",
                )
            ),
            possible_result=_required_text(
                raw.get("possible_result"),
                f"projections[{index}].possible_result",
            ),
            observation_indicators=tuple(
                _nonempty_list(
                    raw.get("observation_indicators"),
                    f"projections[{index}].observation_indicators",
                )
            ),
            invalidation_conditions=tuple(
                _nonempty_list(
                    raw.get("invalidation_conditions"),
                    f"projections[{index}].invalidation_conditions",
                )
            ),
            time_horizon=_required_text(
                raw.get("time_horizon"),
                f"projections[{index}].time_horizon",
            ),
            supporting_card_ids=tuple(
                card_alias_to_id[item] for item in card_aliases
            ),
            supporting_edge_ids=tuple(
                edge_alias_to_id[item] for item in edge_aliases
            ),
        )
        _reject_alias_mentions(
            [
                projection.conditional_judgement,
                *projection.conditions,
                projection.possible_result,
                *projection.observation_indicators,
                *projection.invalidation_conditions,
                projection.time_horizon,
            ],
            aliases={*card_alias_to_id, *edge_alias_to_id},
            field=f"projections[{index}]",
        )
        result.append(projection)
    return tuple(result)


def render_projection_text(
    title: str,
    projections: tuple[ConditionalProjection, ...],
) -> str:
    sections = [f"# {title}：条件性推演"]
    for index, item in enumerate(projections, start=1):
        sections.extend(
            [
                "",
                f"## 情景 {index}",
                item.conditional_judgement,
                f"成立条件：{'；'.join(item.conditions)}",
                f"可能结果：{item.possible_result}",
                f"观察指标：{'；'.join(item.observation_indicators)}",
                f"失效条件：{'；'.join(item.invalidation_conditions)}",
                f"时间范围：{item.time_horizon}",
            ]
        )
    return "\n".join(sections).strip()


def projection_target_id(community_id: str) -> str:
    return f"{community_id}:projection"


def fact_semantic_version(*, graph_fingerprint: str, report_version: int) -> str:
    return _semantic_version_digest(
        graph_fingerprint,
        "fact",
        str(report_version),
    )


def projection_semantic_version(
    *,
    graph_fingerprint: str,
    fact_report_version: int,
    projection_version: int,
) -> str:
    return _semantic_version_digest(
        graph_fingerprint,
        "fact",
        str(fact_report_version),
        "projection",
        str(projection_version),
    )


def _semantic_version_digest(*parts: str) -> str:
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _reject_alias_mentions(
    values: list[str],
    *,
    aliases: set[str],
    field: str,
) -> None:
    leaked = sorted(
        alias
        for alias in aliases
        if any(alias in value for value in values)
    )
    if leaked:
        raise ValueError(f"{field} 的自然语言字段泄漏内部 alias: {leaked}")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 不能为空")
    return text


def _unique_texts(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是数组")
    result = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if len(result) != len(value):
        raise ValueError(f"{field} 包含空值或重复值")
    return result


def _nonempty_list(value: Any, field: str) -> list[str]:
    result = _unique_texts(value, field)
    if not result:
        raise ValueError(f"{field} 不能为空")
    return result


def _validate_aliases(values: list[str], allowed: set[str], kind: str) -> None:
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(f"{kind} alias 不属于当前 Community: {invalid}")
