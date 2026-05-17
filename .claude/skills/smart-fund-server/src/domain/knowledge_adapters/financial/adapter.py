"""Financial adapter implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.domain.knowledge.enums import EvidenceType, RecordKind
from src.domain.knowledge.extraction import TextExtractionPipeline
from src.domain.knowledge.schemas import EdgeDraft, EvidenceDraft, KnowledgeInput, NodeDraft
from src.domain.knowledge.source_record import resolve_record_kind
from src.domain.knowledge_adapters.financial.confidence import default_relation_confidence
from src.domain.knowledge_adapters.financial.news_extraction import (
    FinancialNewsExtractionStrategy,
    enrich_financial_text_payload,
)
from src.domain.knowledge_adapters.financial.normalization import (
    EMPTY_NORMALIZATION_RULES,
    NormalizationRules,
    normalize_entity_with_rules,
    normalize_term_name_with_rules,
)
from src.domain.knowledge_adapters.financial.normalization_decision import (
    FinancialPayloadNormalizationStrategy,
)
from src.domain.knowledge_adapters.financial.ontology import FINANCIAL_ADAPTER_SPEC
from src.domain.knowledge_adapters.financial.relation_normalization import (
    normalize_candidate_relation_type,
)
from src.domain.knowledge_adapters.financial.semantic_certainty import assess_semantic_certainty
from src.domain.knowledge_adapters.financial.sources import (
    SOURCE_RECORD_KINDS,
    SOURCE_INPUT_TYPES,
    entity_stable_key,
    parse_datetime,
    stock_key,
    typed_ref,
    validate_source_payload,
)

_COMPILE_PAYLOAD_CACHE_KEY = "_financial_compile_payload"


class FinancialKGAdapter:
    spec = FINANCIAL_ADAPTER_SPEC

    def __init__(
        self,
        *,
        text_extraction_pipeline: TextExtractionPipeline | None = None,
        news_extraction_strategy: FinancialNewsExtractionStrategy | None = None,
        enable_text_extraction: bool = True,
        normalization_rules: NormalizationRules = EMPTY_NORMALIZATION_RULES,
        normalization_decision_strategy: FinancialPayloadNormalizationStrategy | None = None,
    ) -> None:
        self.text_extraction_pipeline = text_extraction_pipeline or TextExtractionPipeline()
        self.news_extraction_strategy = news_extraction_strategy or FinancialNewsExtractionStrategy()
        self.enable_text_extraction = enable_text_extraction
        self.normalization_rules = normalization_rules
        self.normalization_decision_strategy = normalization_decision_strategy

    def normalize(self, raw: Any) -> list[KnowledgeInput]:
        records = raw if isinstance(raw, list) else [raw]
        return [self._normalize_one(record) for record in records]

    async def extract_node_drafts(self, item: KnowledgeInput) -> list[NodeDraft]:
        payload = await self._payload_for_compile(item)
        record_kind = item.record_kind
        if record_kind == RecordKind.ENTITY_SNAPSHOT:
            return _entity_snapshot_nodes(item.source_type, payload, item.source_id)
        if record_kind == RecordKind.RELATION_ASSERTION:
            return _relation_assertion_nodes(item.source_type, payload, item.source_id, self.normalization_rules)
        if record_kind == RecordKind.TEXT_DOCUMENT:
            return _text_document_nodes(item.source_type, payload, item.source_id, self.normalization_rules)
        if record_kind == RecordKind.EVENT_ASSERTION:
            return _event_assertion_nodes(item.source_type, payload, item.source_id, self.normalization_rules)
        if record_kind == RecordKind.STRUCTURED_SIGNAL:
            return _structured_signal_nodes(item.source_type, payload, item.source_id, self.normalization_rules)
        return []

    async def extract_edge_drafts(self, item: KnowledgeInput, nodes: list[NodeDraft]) -> list[EdgeDraft]:
        del nodes
        payload = await self._payload_for_compile(item)
        evidence_ref = _evidence_ref(item)
        record_kind = item.record_kind
        if record_kind == RecordKind.RELATION_ASSERTION:
            return _relation_assertion_edges(item.source_type, payload, evidence_ref, self.normalization_rules)
        if record_kind == RecordKind.TEXT_DOCUMENT:
            return _text_document_edges(item.source_type, payload, item.source_id, evidence_ref, self.normalization_rules)
        if record_kind == RecordKind.EVENT_ASSERTION:
            return _event_assertion_edges(item.source_type, payload, item.source_id, evidence_ref, self.normalization_rules)
        if record_kind == RecordKind.STRUCTURED_SIGNAL:
            return _structured_signal_edges(item.source_type, payload, item.source_id, evidence_ref, self.normalization_rules)
        return []

    def extract_evidence_drafts(self, item: KnowledgeInput) -> list[EvidenceDraft]:
        evidence_type = EvidenceType.RULE_OUTPUT if item.record_kind == RecordKind.STRUCTURED_SIGNAL else (
            EvidenceType.TEXT_SPAN if item.raw_text else EvidenceType.STRUCTURED_FIELD
        )
        return [
            EvidenceDraft(
                evidence_type=evidence_type,
                source_type=item.source_type,
                source_id=item.source_id,
                content=item.raw_text,
                payload=item.payload,
                metadata={**dict(item.metadata or {}), "adapter": self.spec.name},
            )
        ]

    async def _payload_for_compile(self, item: KnowledgeInput) -> dict[str, Any]:
        cached = item.metadata.get(_COMPILE_PAYLOAD_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
        payload = item.payload
        weak_hints = item.metadata.get("weak_entity_hints")
        if weak_hints and "weak_entity_hints" not in payload:
            payload = {**payload, "weak_entity_hints": weak_hints}
        assessment = assess_semantic_certainty(item)
        item.metadata["_semantic_certainty"] = assessment.model_dump()
        if self.enable_text_extraction and item.record_kind == RecordKind.TEXT_DOCUMENT:
            payload = await enrich_financial_text_payload(
                payload,
                source_id=item.source_id,
                source_type=item.source_type,
                pipeline=self.text_extraction_pipeline,
                strategy=self.news_extraction_strategy,
                semantic_assessment=assessment,
            )
        if self.normalization_decision_strategy is not None and item.source_type in {"news_articles", "policy_news", "l1_events"}:
            payload = await self.normalization_decision_strategy.normalize_payload(
                payload,
                source_id=item.source_id,
                source_type=item.source_type,
            )
        item.metadata[_COMPILE_PAYLOAD_CACHE_KEY] = payload
        return payload

    def _normalize_one(self, record: dict[str, Any]) -> KnowledgeInput:
        source_type = record.get("source_type")
        if not source_type:
            raise ValueError("source_type is required")
        payload = dict(record.get("payload") or {})
        validate_source_payload(source_type, payload)
        source_id = str(record.get("source_id") or payload.get("source_id") or _source_id(source_type, payload))
        observed_at = _observed_at(record, payload)
        return KnowledgeInput(
            input_type=SOURCE_INPUT_TYPES[source_type],
            source_type=source_type,
            source_id=source_id,
            observed_at=observed_at,
            adapter_name=self.spec.name,
            adapter_version=self.spec.version,
            record_kind=resolve_record_kind(
                source_type=source_type,
                input_type=SOURCE_INPUT_TYPES[source_type],
                explicit=record.get("record_kind") or payload.get("record_kind"),
                source_type_hints=SOURCE_RECORD_KINDS,
            ),
            payload=payload,
            raw_text=record.get("raw_text") or payload.get("text") or payload.get("summary") or payload.get("title"),
            metadata=dict(record.get("metadata") or {}),
        )


def _entity_snapshot_nodes(source_type: str, payload: dict[str, Any], source_id: str) -> list[NodeDraft]:
    if source_type == "stock_basics":
        return [_stock_node(payload, source_ref=source_id)]
    return []


def _relation_assertion_nodes(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    if source_type == "industry_components":
        return _industry_component_nodes(payload, source_id, normalization_rules)
    if source_type == "concept_components":
        return _concept_component_nodes(payload, source_id, normalization_rules)
    if source_type == "fund_holdings":
        return _fund_holding_nodes(payload, source_id)
    return []


def _text_document_nodes(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    if source_type == "news_articles":
        return _document_nodes(payload, source_id, node_type="event", normalization_rules=normalization_rules)
    if source_type == "policy_news":
        return _document_nodes(payload, source_id, node_type="policy", normalization_rules=normalization_rules)
    return []


def _event_assertion_nodes(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    if source_type == "l1_events":
        return _document_nodes(payload, source_id, node_type="event", normalization_rules=normalization_rules)
    return []


def _structured_signal_nodes(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    if source_type == "derived_signal":
        return _derived_signal_nodes(payload, source_id, normalization_rules)
    return []


def _relation_assertion_edges(
    source_type: str,
    payload: dict[str, Any],
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    if source_type == "industry_components":
        return [_belongs_to_industry_edge(payload, evidence_ref, normalization_rules)]
    if source_type == "concept_components":
        return [_belongs_to_concept_edge(payload, evidence_ref, normalization_rules)]
    if source_type == "fund_holdings":
        return [_holds_edge(payload, evidence_ref)]
    return []


def _text_document_edges(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    if source_type == "news_articles":
        return _document_edges(payload, source_id, "event", evidence_ref, normalization_rules)
    if source_type == "policy_news":
        return _document_edges(payload, source_id, "policy", evidence_ref, normalization_rules)
    return []


def _event_assertion_edges(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    if source_type == "l1_events":
        return _document_edges(payload, source_id, "event", evidence_ref, normalization_rules)
    return []


def _structured_signal_edges(
    source_type: str,
    payload: dict[str, Any],
    source_id: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    if source_type == "derived_signal":
        return _derived_signal_edges(payload, source_id, evidence_ref, normalization_rules)
    return []


def _stock_node(payload: dict[str, Any], *, source_ref: str) -> NodeDraft:
    key = stock_key(payload["exchange"], payload["code"])
    aliases = [str(item) for item in payload.get("aliases", [])]
    if payload.get("company_name"):
        aliases.append(str(payload["company_name"]))
    return NodeDraft(
        node_type="stock",
        stable_key=key,
        canonical_name=str(payload["name"]),
        aliases=_unique(aliases),
        external_ids={"exchange": str(payload["exchange"]), "code": str(payload["code"])},
        properties={
            "exchange": payload["exchange"],
            "code": payload["code"],
            "list_date": payload.get("list_date"),
            "status": payload.get("status", "active"),
        },
        source_refs=[source_ref],
    )


def _fund_node(payload: dict[str, Any], *, source_ref: str) -> NodeDraft:
    return NodeDraft(
        node_type="fund",
        stable_key=str(payload["fund_code"]),
        canonical_name=str(payload.get("fund_name") or payload["fund_code"]),
        external_ids={"fund_code": str(payload["fund_code"])},
        properties={"fund_code": payload["fund_code"]},
        source_refs=[source_ref],
    )


def _industry_node(
    payload: dict[str, Any],
    *,
    source_ref: str,
    normalization_rules: NormalizationRules,
) -> NodeDraft:
    canonical_name = normalize_term_name_with_rules(payload["component_name"], normalization_rules)
    key = f"{payload['taxonomy']}:{payload.get('component_code') or canonical_name}"
    return NodeDraft(
        node_type="industry",
        stable_key=key,
        canonical_name=canonical_name,
        external_ids={"taxonomy": str(payload["taxonomy"]), "code": str(payload.get("component_code") or "")},
        properties={"taxonomy": payload["taxonomy"], "code": payload.get("component_code")},
        source_refs=[source_ref],
    )


def _concept_node(
    payload: dict[str, Any],
    *,
    source_ref: str,
    normalization_rules: NormalizationRules,
) -> NodeDraft:
    canonical_name = normalize_term_name_with_rules(payload["component_name"], normalization_rules)
    key = f"{payload['taxonomy']}:{canonical_name}"
    aliases = [str(item) for item in payload.get("aliases", [])]
    raw_name = str(payload["component_name"])
    if raw_name != canonical_name:
        aliases.append(raw_name)
    return NodeDraft(
        node_type="concept",
        stable_key=key,
        canonical_name=canonical_name,
        aliases=_unique(aliases),
        external_ids={"taxonomy": str(payload["taxonomy"])},
        properties={"taxonomy": payload["taxonomy"]},
        source_refs=[source_ref],
    )


def _event_or_policy_node(
    payload: dict[str, Any],
    source_id: str,
    *,
    node_type: str,
) -> NodeDraft:
    stable_key = str(payload.get("document_id") or payload.get("event_id") or payload.get("source_id") or source_id)
    title = payload.get("title") or payload.get("event_type") or stable_key
    return NodeDraft(
        node_type=node_type,
        stable_key=stable_key,
        canonical_name=str(title),
        external_ids={node_type: stable_key},
        properties={
            "published_at": payload.get("published_at"),
            "event_time": payload.get("event_time"),
            "source_name": payload.get("source_name"),
            "event_type": payload.get("event_type"),
        },
        source_refs=[source_id],
    )


def _industry_component_nodes(
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    return [
        _industry_node(payload, source_ref=source_id, normalization_rules=normalization_rules),
        _stock_node(_stock_payload_from_member(payload), source_ref=source_id),
    ]


def _concept_component_nodes(
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    return [
        _concept_node(payload, source_ref=source_id, normalization_rules=normalization_rules),
        _stock_node(_stock_payload_from_member(payload), source_ref=source_id),
    ]


def _fund_holding_nodes(payload: dict[str, Any], source_id: str) -> list[NodeDraft]:
    return [
        _fund_node(payload, source_ref=source_id),
        _stock_node(
            {
                "exchange": payload["stock_exchange"],
                "code": payload["stock_code"],
                "name": payload.get("stock_name") or payload["stock_code"],
            },
            source_ref=source_id,
        ),
    ]


def _derived_signal_nodes(
    payload: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    target = _entity_from_target_ref(payload.get("target_ref"))
    title = payload.get("title") or _derived_signal_title(payload, target)
    signal_node = NodeDraft(
        node_type="event",
        stable_key=str(payload.get("signal_id") or payload.get("source_id") or source_id),
        canonical_name=str(title),
        external_ids={"event": str(payload.get("signal_id") or payload.get("source_id") or source_id)},
        properties={
            "event_type": "derived_signal",
            "signal_type": payload.get("signal_type"),
            "observed_at": payload.get("observed_at"),
            "value": payload.get("value"),
            "unit": payload.get("unit"),
            "window": payload.get("window"),
        },
        source_refs=[source_id],
    )
    nodes = [signal_node]
    if target:
        nodes.append(_entity_node(target, source_id, normalization_rules))
    return _dedupe_nodes(nodes)


def _document_nodes(
    payload: dict[str, Any],
    source_id: str,
    *,
    node_type: str,
    normalization_rules: NormalizationRules,
) -> list[NodeDraft]:
    nodes = [_event_or_policy_node(payload, source_id, node_type=node_type)]
    candidate_entities = _candidate_package_entities(payload)
    candidate_events = _candidate_package_events(payload, source_id, node_type=node_type)
    for entity in payload.get("mentioned_entities", []) + payload.get("affected_entities", []) + candidate_entities:
        node = _safe_entity_node(entity, source_id, normalization_rules)
        if node is not None:
            nodes.append(node)
    for event in candidate_events:
        nodes.append(_candidate_event_node(event, source_id, node_type=node_type))
    return _dedupe_nodes(nodes)


def _safe_entity_node(
    entity: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> NodeDraft | None:
    prepared = _prepare_entity_for_ref(entity, normalization_rules)
    if prepared is None:
        return None
    return _entity_node(prepared, source_id, normalization_rules)


def _entity_node(
    entity: dict[str, Any],
    source_id: str,
    normalization_rules: NormalizationRules,
) -> NodeDraft:
    entity = normalize_entity_with_rules(entity, normalization_rules)
    node_type = entity["type"]
    if node_type == "stock":
        entity = _complete_stock_entity(entity)
    stable_key = entity_stable_key(entity, normalization_rules=normalization_rules)
    if node_type == "stock":
        return _stock_node(
            {
                "exchange": entity["exchange"],
                "code": entity["code"],
                "name": entity.get("name") or entity["code"],
                "aliases": entity.get("aliases", []),
            },
            source_ref=source_id,
        )
    if node_type == "fund":
        return _fund_node({"fund_code": entity["fund_code"], "fund_name": entity.get("name")}, source_ref=source_id)
    if node_type == "industry":
        return NodeDraft(
            node_type="industry",
            stable_key=stable_key,
            canonical_name=str(entity["name"]),
            aliases=[str(item) for item in entity.get("aliases", [])],
            properties=_with_normalization_metadata(
                {"taxonomy": entity.get("taxonomy", "default"), "code": entity.get("code")},
                entity,
            ),
            source_refs=[source_id],
        )
    if node_type == "concept":
        return NodeDraft(
            node_type="concept",
            stable_key=stable_key,
            canonical_name=str(entity["name"]),
            aliases=[str(item) for item in entity.get("aliases", [])],
            properties=_with_normalization_metadata({"taxonomy": entity.get("taxonomy", "default")}, entity),
            source_refs=[source_id],
        )
    if node_type == "macro_indicator":
        stable_key = entity_stable_key(entity, normalization_rules=normalization_rules)
        return NodeDraft(
            node_type="macro_indicator",
            stable_key=stable_key,
            canonical_name=str(entity.get("name") or stable_key),
            external_ids={"indicator_code": str(entity.get("indicator_code") or entity.get("code") or stable_key)},
            properties={"indicator_code": str(entity.get("indicator_code") or entity.get("code") or stable_key)},
            source_refs=[source_id],
        )
    return NodeDraft(
        node_type=node_type,
        stable_key=stable_key,
        canonical_name=str(entity.get("name") or stable_key),
        aliases=[str(item) for item in entity.get("aliases", [])],
        properties=_with_normalization_metadata({}, entity),
        source_refs=[source_id],
    )


def _belongs_to_industry_edge(
    payload: dict[str, Any],
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> EdgeDraft:
    relation = default_relation_confidence("belongs_to", source_type="industry_components")
    stock_ref = typed_ref("stock", stock_key(payload["member_stock_exchange"], payload["member_stock_code"]))
    canonical_name = normalize_term_name_with_rules(payload["component_name"], normalization_rules)
    industry_key = f"{payload['taxonomy']}:{payload.get('component_code') or canonical_name}"
    return EdgeDraft(
        source_ref=stock_ref,
        target_ref=typed_ref("industry", industry_key),
        relation_type="belongs_to",
        confidence_label=relation.label,
        confidence_score=relation.score,
        status=relation.status,
        evidence_refs=[evidence_ref],
        properties={"taxonomy": payload["taxonomy"], "weight": payload.get("weight")},
    )


def _belongs_to_concept_edge(
    payload: dict[str, Any],
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> EdgeDraft:
    relation = default_relation_confidence("belongs_to", source_type="concept_components")
    stock_ref = typed_ref("stock", stock_key(payload["member_stock_exchange"], payload["member_stock_code"]))
    concept_key = f"{payload['taxonomy']}:{normalize_term_name_with_rules(payload['component_name'], normalization_rules)}"
    return EdgeDraft(
        source_ref=stock_ref,
        target_ref=typed_ref("concept", concept_key),
        relation_type="belongs_to",
        confidence_label=relation.label,
        confidence_score=relation.score,
        status=relation.status,
        evidence_refs=[evidence_ref],
        properties={"taxonomy": payload["taxonomy"], "weight": payload.get("weight")},
    )


def _holds_edge(payload: dict[str, Any], evidence_ref: str) -> EdgeDraft:
    relation = default_relation_confidence("holds", source_type="fund_holdings")
    return EdgeDraft(
        source_ref=typed_ref("fund", str(payload["fund_code"])),
        target_ref=typed_ref("stock", stock_key(payload["stock_exchange"], payload["stock_code"])),
        relation_type="holds",
        confidence_label=relation.label,
        confidence_score=relation.score,
        status=relation.status,
        evidence_refs=[evidence_ref],
        valid_from=parse_datetime(payload["report_date"]),
        properties={
            "report_date": payload["report_date"],
            "holding_ratio": payload.get("holding_ratio"),
            "rank": payload.get("rank"),
        },
    )


def _document_edges(
    payload: dict[str, Any],
    source_id: str,
    source_node_type: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    source_key = str(payload.get("document_id") or payload.get("event_id") or payload.get("source_id") or source_id)
    source_ref = typed_ref(source_node_type, source_key)
    edges: list[EdgeDraft] = []
    for entity in payload.get("mentioned_entities", []):
        entity = normalize_entity_with_rules(entity, normalization_rules)
        target_ref = _entity_typed_ref(entity, normalization_rules)
        if target_ref is None:
            continue
        relation = default_relation_confidence("mentions", source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=target_ref,
                relation_type="mentions",
                confidence_label=relation.label,
                confidence_score=float(entity.get("confidence", relation.score)),
                status=relation.status,
                evidence_refs=[evidence_ref],
            )
        )
    candidate_entities = _candidate_package_entities(payload)
    for entity in candidate_entities:
        entity = normalize_entity_with_rules(entity, normalization_rules)
        target_ref = _entity_typed_ref(entity, normalization_rules)
        if target_ref is None:
            continue
        relation = default_relation_confidence("mentions", source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=target_ref,
                relation_type="mentions",
                confidence_label=relation.label,
                confidence_score=float(entity.get("confidence", relation.score)),
                status=relation.status,
                evidence_refs=[evidence_ref],
            )
        )
    for entity in payload.get("affected_entities", []):
        entity = normalize_entity_with_rules(entity, normalization_rules)
        target_ref = _entity_typed_ref(entity, normalization_rules)
        if target_ref is None:
            continue
        relation = default_relation_confidence("affects", source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=target_ref,
                relation_type="affects",
                confidence_label=relation.label,
                confidence_score=float(entity.get("confidence", relation.score)),
                status=relation.status,
                evidence_refs=[evidence_ref],
                properties={"direction": entity.get("direction"), "reason": entity.get("reason")},
            )
        )
    edges.extend(
        _candidate_package_relation_edges(
            payload,
            source_id,
            source_node_type,
            source_ref,
            evidence_ref,
            normalization_rules,
        )
    )
    return edges


def _candidate_package_relation_edges(
    payload: dict[str, Any],
    source_id: str,
    source_node_type: str,
    document_source_ref: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    package = _candidate_package(payload)
    if not package:
        return []
    entity_map = _candidate_endpoint_map(payload, source_id, source_node_type, normalization_rules)
    edges: list[EdgeDraft] = []
    for relation_payload in package.get("relations", []):
        if not isinstance(relation_payload, dict):
            continue
        relation_payload_properties = (
            relation_payload.get("properties")
            if isinstance(relation_payload.get("properties"), dict)
            else {}
        )
        relation_type, relation_metadata = normalize_candidate_relation_type(
            relation_payload.get("relation_type"),
            direction=relation_payload.get("direction"),
        )
        target_name = str(relation_payload.get("target") or "").strip()
        if not relation_type or not target_name:
            continue
        source_name = str(relation_payload.get("source") or "").strip()
        source_ref = entity_map.get(source_name) or document_source_ref
        target_ref = entity_map.get(target_name)
        if target_ref is None:
            fallback_target = {
                "type": "concept",
                "name": target_name,
                "confidence": relation_payload.get("confidence", 0.6),
            }
            fallback_target = normalize_entity_with_rules(fallback_target, normalization_rules)
            target_ref = typed_ref(
                fallback_target["type"],
                entity_stable_key(fallback_target, normalization_rules=normalization_rules),
            )
        direction = relation_payload.get("direction") or relation_metadata.get("direction")
        relation_type, endpoint_metadata = _normalize_relation_for_endpoints(
            relation_type,
            source_ref=source_ref,
            target_ref=target_ref,
        )
        relation_metadata = {**relation_metadata, **endpoint_metadata}
        relation = default_relation_confidence(relation_type, source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=target_ref,
                relation_type=relation_type,
                confidence_label=relation.label,
                confidence_score=float(relation_payload.get("confidence") or relation.score),
                status=relation.status,
                evidence_refs=[evidence_ref],
                properties={
                    name: value
                    for name, value in {
                        "direction": direction,
                        "reason": relation_payload.get("reason"),
                        "candidate_fact_package": True,
                        "original_relation_type": relation_payload.get("original_relation_type")
                        or relation_payload_properties.get("original_relation_type"),
                        "relation_type_normalized": relation_payload.get("relation_type_normalized")
                        or relation_payload_properties.get("relation_type_normalized"),
                        "relation_type_fallback": relation_payload.get("relation_type_fallback")
                        or relation_payload_properties.get("relation_type_fallback"),
                        "original_source_type": relation_payload.get("original_source_type")
                        or relation_payload_properties.get("original_source_type"),
                        "original_target_type": relation_payload.get("original_target_type")
                        or relation_payload_properties.get("original_target_type"),
                        **{
                            key: value
                            for key, value in relation_metadata.items()
                            if key != "direction"
                        },
                    }.items()
                    if value is not None
                },
            )
        )
    return edges


def _normalize_relation_for_endpoints(
    relation_type: str,
    *,
    source_ref: str,
    target_ref: str,
) -> tuple[str, dict[str, Any]]:
    source_type = _typed_ref_type(source_ref)
    target_type = _typed_ref_type(target_ref)
    if _relation_endpoint_allowed(relation_type, source_type, target_type):
        return relation_type, {}
    if relation_type == "mentions" and source_type not in {"event", "policy"}:
        return "related_to", {
            "original_relation_type": relation_type,
            "relation_type_normalized": True,
            "relation_type_fallback": "invalid_endpoint_to_related_to",
            "original_source_type": source_type,
            "original_target_type": target_type,
        }
    if relation_type == "affects" and source_type not in {"event", "policy"}:
        return "related_to", {
            "original_relation_type": relation_type,
            "relation_type_normalized": True,
            "relation_type_fallback": "invalid_endpoint_to_related_to",
            "original_source_type": source_type,
            "original_target_type": target_type,
        }
    fallback_relation = _fallback_relation_for_invalid_endpoints(relation_type, source_type, target_type)
    return fallback_relation, {
        "original_relation_type": relation_type,
        "relation_type_normalized": True,
        "relation_type_fallback": f"invalid_endpoint_to_{fallback_relation}",
        "original_source_type": source_type,
        "original_target_type": target_type,
    }


def _fallback_relation_for_invalid_endpoints(
    relation_type: str,
    source_type: str,
    target_type: str,
) -> str:
    if relation_type == "belongs_to" and _relation_endpoint_allowed("mentions", source_type, target_type):
        return "mentions"
    if relation_type == "benefits_from" and _relation_endpoint_allowed("affects", source_type, target_type):
        return "affects"
    if relation_type == "hurt_by" and _relation_endpoint_allowed("affects", source_type, target_type):
        return "affects"
    if relation_type == "holds" and _relation_endpoint_allowed("related_to", source_type, target_type):
        return "related_to"
    if _relation_endpoint_allowed("related_to", source_type, target_type):
        return "related_to"
    return "related_to"


def _relation_endpoint_allowed(relation_type: str, source_type: str, target_type: str) -> bool:
    for relation in FINANCIAL_ADAPTER_SPEC.relations:
        if relation.name == relation_type:
            return source_type in relation.source_types and target_type in relation.target_types
    return False


def _typed_ref_type(ref: str) -> str:
    if ":" not in ref:
        return ""
    return ref.split(":", 1)[0].strip()


def _candidate_endpoint_map(
    payload: dict[str, Any],
    source_id: str,
    source_node_type: str,
    normalization_rules: NormalizationRules,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    document_title = str(payload.get("title") or payload.get("event_type") or "").strip()
    document_key = str(payload.get("document_id") or payload.get("event_id") or payload.get("source_id") or source_id)
    if document_title:
        mapping[document_title] = typed_ref(source_node_type, document_key)
    for entity in _candidate_package_entities(payload):
        normalized = normalize_entity_with_rules(entity, normalization_rules)
        name = str(normalized.get("name") or "").strip()
        entity_ref = _entity_typed_ref(normalized, normalization_rules)
        if name and entity_ref is not None:
            mapping[name] = entity_ref
    for index, event in enumerate(_candidate_package_events(payload, source_id, node_type=source_node_type)):
        title = str(event.get("title") or "").strip()
        if title:
            mapping[title] = typed_ref(source_node_type, _candidate_event_stable_key(event, source_id, index=index))
    return mapping


def _derived_signal_edges(
    payload: dict[str, Any],
    source_id: str,
    evidence_ref: str,
    normalization_rules: NormalizationRules,
) -> list[EdgeDraft]:
    target = _entity_from_target_ref(payload.get("target_ref"))
    if not target:
        return []
    relation = default_relation_confidence("mentions", source_type="event")
    signal_key = str(payload.get("signal_id") or payload.get("source_id") or source_id)
    return [
        EdgeDraft(
            source_ref=typed_ref("event", signal_key),
            target_ref=typed_ref(target["type"], entity_stable_key(target, normalization_rules=normalization_rules)),
            relation_type="mentions",
            confidence_label=relation.label,
            confidence_score=float(payload.get("confidence") or relation.score),
            status=relation.status,
            evidence_refs=[evidence_ref],
            properties={
                "signal_type": payload.get("signal_type"),
                "value": payload.get("value"),
                "unit": payload.get("unit"),
                "window": payload.get("window"),
            },
        )
    ]


def _candidate_package(payload: dict[str, Any]) -> dict[str, Any]:
    package = payload.get("candidate_fact_package")
    return package if isinstance(package, dict) else {}


def _candidate_package_entities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    package = _candidate_package(payload)
    entities = package.get("entities")
    if not isinstance(entities, list):
        entities = []
    result: list[dict[str, Any]] = []
    for item in entities:
        entity = _candidate_entity_payload(item)
        if entity:
            result.append(entity)
    result.extend(_candidate_relation_endpoint_entities(package, result, payload))
    return result


def _candidate_relation_endpoint_entities(
    package: dict[str, Any],
    existing_entities: list[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    relations = package.get("relations")
    if not isinstance(relations, list):
        return []
    existing_names = {
        str(entity.get("name") or entity.get("canonical_name") or "").strip()
        for entity in existing_entities
        if str(entity.get("name") or entity.get("canonical_name") or "").strip()
    }
    document_title = str(payload.get("title") or payload.get("event_type") or "").strip()
    if document_title:
        existing_names.add(document_title)
    events = package.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and str(event.get("title") or "").strip():
                existing_names.add(str(event.get("title") or "").strip())
    fallback_entities: list[dict[str, Any]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        evidence_spans = relation.get("evidence_spans")
        if not evidence_spans:
            continue
        for side in ("source", "target"):
            name = str(relation.get(side) or "").strip()
            if not name or name in existing_names:
                continue
            existing_names.add(name)
            fallback_entities.append(
                {
                    "type": "concept",
                    "name": name,
                    "confidence": relation.get("confidence", 0.6),
                    "evidence_spans": evidence_spans,
                    "properties": {
                        "candidate_relation_endpoint": True,
                        "endpoint_side": side,
                    },
                }
            )
    return fallback_entities


def _candidate_package_events(
    payload: dict[str, Any],
    source_id: str,
    *,
    node_type: str,
) -> list[dict[str, Any]]:
    package = _candidate_package(payload)
    events = package.get("events")
    if not isinstance(events, list):
        return []
    document_title = str(payload.get("title") or payload.get("event_type") or "").strip()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(events):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or title == document_title:
            continue
        result.append(
            {
                "type": node_type,
                "title": title,
                "summary": item.get("summary"),
                "confidence": item.get("confidence"),
                "source_id": f"{source_id}:candidate_event:{index}",
                "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
            }
        )
    return result


def _candidate_entity_payload(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    entity_type = item.get("entity_type") or item.get("type")
    name = item.get("canonical_name") or item.get("name")
    if not entity_type or not name:
        return None
    result: dict[str, Any] = {
        "type": entity_type,
        "name": name,
        "confidence": item.get("confidence", 0.7),
    }
    identifiers = item.get("identifiers")
    if isinstance(identifiers, dict):
        result.update({str(key): value for key, value in identifiers.items() if value is not None})
    properties = item.get("properties")
    if isinstance(properties, dict):
        for key in ("direction", "reason", "taxonomy", "indicator_code", "code", "exchange", "fund_code"):
            if key in properties and key not in result:
                result[key] = properties[key]
    if item.get("aliases"):
        result["aliases"] = [str(alias) for alias in item.get("aliases", [])]
    if item.get("evidence_spans"):
        result["evidence_spans"] = item["evidence_spans"]
    return result


def _candidate_event_node(
    event: dict[str, Any],
    source_id: str,
    *,
    node_type: str,
) -> NodeDraft:
    index = _candidate_event_index(event)
    stable_key = _candidate_event_stable_key(event, source_id, index=index)
    return NodeDraft(
        node_type=node_type,
        stable_key=stable_key,
        canonical_name=str(event["title"]),
        external_ids={node_type: stable_key},
        properties={
            "summary": event.get("summary"),
            "confidence": event.get("confidence"),
            **(event.get("properties") if isinstance(event.get("properties"), dict) else {}),
        },
        source_refs=[source_id],
    )


def _candidate_event_stable_key(event: dict[str, Any], source_id: str, *, index: int) -> str:
    return str(event.get("source_id") or f"{source_id}:candidate_event:{index}")


def _candidate_event_index(event: dict[str, Any]) -> int:
    value = str(event.get("source_id") or "")
    if ":candidate_event:" not in value:
        return 0
    try:
        return int(value.rsplit(":candidate_event:", 1)[1])
    except ValueError:
        return 0


def _stock_payload_from_member(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "exchange": payload["member_stock_exchange"],
        "code": payload["member_stock_code"],
        "name": payload.get("member_stock_name") or payload["member_stock_code"],
    }


def _with_normalization_metadata(properties: dict[str, Any], entity: dict[str, Any]) -> dict[str, Any]:
    metadata = entity.get("_normalization")
    if isinstance(metadata, dict) and metadata:
        result = dict(properties)
        result["normalization"] = metadata
        return result
    return properties


def _source_id(source_type: str, payload: dict[str, Any]) -> str:
    if source_type == "stock_basics":
        return f"stock:{payload['exchange']}:{payload['code']}"
    if source_type in {"industry_components", "concept_components"}:
        return f"{source_type}:{payload['taxonomy']}:{payload['component_name']}:{payload['member_stock_code']}"
    if source_type == "fund_holdings":
        return f"fund_holding:{payload['fund_code']}:{payload['stock_code']}:{payload['report_date']}"
    if source_type == "l1_events":
        return str(payload["event_id"])
    if source_type in {"news_articles", "policy_news"}:
        return str(payload["source_id"])
    if source_type == "derived_signal":
        if payload.get("source_table") and payload.get("source_pk") is not None:
            return f"{payload['source_table']}:{payload['source_pk']}"
        target_ref = payload["target_ref"]
        if isinstance(target_ref, dict):
            target_ref = ":".join(
                str(target_ref.get(name) or "")
                for name in ["type", "exchange", "code", "taxonomy", "name", "indicator_code"]
                if target_ref.get(name)
            )
        return f"derived_signal:{target_ref}:{payload['signal_type']}:{payload['observed_at']}:{payload.get('window') or ''}"
    return f"{source_type}:{payload.get('target_ref', 'unknown')}"


def _observed_at(record: dict[str, Any], payload: dict[str, Any]) -> datetime:
    value = (
        record.get("observed_at")
        or payload.get("observed_at")
        or payload.get("published_at")
        or payload.get("event_time")
        or payload.get("report_date")
    )
    return parse_datetime(value)


def _evidence_ref(item: KnowledgeInput) -> str:
    return f"{item.source_type}:{item.source_id}"


def _entity_from_target_ref(target_ref: Any) -> dict[str, Any] | None:
    if isinstance(target_ref, dict):
        node_type = target_ref.get("type")
        if not node_type:
            return None
        entity = dict(target_ref)
        if node_type == "stock":
            entity.setdefault("exchange", "CN")
            entity.setdefault("name", entity.get("code"))
        if node_type in {"industry", "concept"}:
            entity.setdefault("taxonomy", "business")
        if node_type == "macro_indicator":
            entity.setdefault("name", entity.get("indicator_code") or entity.get("code"))
        return entity
    if not isinstance(target_ref, str) or not target_ref:
        return None
    parts = target_ref.split(":")
    if parts[0] == "stock" and len(parts) >= 3:
        return {"type": "stock", "exchange": parts[1], "code": parts[2], "name": parts[3] if len(parts) > 3 else parts[2]}
    if parts[0] in {"industry", "concept"} and len(parts) >= 3:
        return {"type": parts[0], "taxonomy": parts[1], "name": parts[2], "code": parts[3] if len(parts) > 3 else None}
    if parts[0] == "macro_indicator" and len(parts) >= 2:
        return {"type": "macro_indicator", "indicator_code": parts[1], "name": parts[2] if len(parts) > 2 else parts[1]}
    return {"type": "macro_indicator", "indicator_code": target_ref, "name": target_ref}


def _complete_stock_entity(entity: dict[str, Any]) -> dict[str, Any]:
    result = dict(entity)
    code = str(result.get("code") or "").strip()
    if not code:
        raise ValueError(f"stock entity missing code: {entity}")
    result.setdefault("name", code)
    if not result.get("exchange"):
        result["exchange"] = _infer_stock_exchange(code)
    return result


def _prepare_entity_for_ref(
    entity: dict[str, Any],
    normalization_rules: NormalizationRules,
) -> dict[str, Any] | None:
    if not isinstance(entity, dict):
        return None
    normalized = normalize_entity_with_rules(entity, normalization_rules)
    if normalized.get("type") == "stock":
        code = str(normalized.get("code") or "").strip()
        if not code:
            return None
        normalized = _complete_stock_entity(normalized)
    return normalized


def _entity_typed_ref(
    entity: dict[str, Any],
    normalization_rules: NormalizationRules,
) -> str | None:
    prepared = _prepare_entity_for_ref(entity, normalization_rules)
    if prepared is None:
        return None
    return typed_ref(
        prepared["type"],
        entity_stable_key(prepared, normalization_rules=normalization_rules),
    )


def _infer_stock_exchange(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if normalized.isdigit() and len(normalized) == 6:
        if normalized.startswith("6"):
            return "SH"
        if normalized.startswith(("0", "3")):
            return "SZ"
        if normalized.startswith(("4", "8")):
            return "BJ"
    if normalized.endswith(".HK") or (normalized.isdigit() and len(normalized) <= 5):
        return "HK"
    return "US"


def _derived_signal_title(payload: dict[str, Any], target: dict[str, Any] | None) -> str:
    target_name = (target or {}).get("name") or (target or {}).get("code") or payload.get("target_ref")
    unit = payload.get("unit") or ""
    return f"{payload.get('signal_type')} {target_name} {payload.get('value')}{unit}"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _dedupe_nodes(nodes: list[NodeDraft]) -> list[NodeDraft]:
    seen: set[tuple[str, str]] = set()
    result: list[NodeDraft] = []
    for node in nodes:
        key = (node.node_type, node.stable_key)
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result
