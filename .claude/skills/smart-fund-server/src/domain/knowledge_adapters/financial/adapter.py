"""Financial adapter implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.extraction import TextExtractionPipeline
from src.domain.knowledge.schemas import EdgeDraft, EvidenceDraft, KnowledgeInput, NodeDraft
from src.domain.knowledge_adapters.financial.confidence import default_relation_confidence
from src.domain.knowledge_adapters.financial.news_extraction import (
    FinancialNewsExtractionStrategy,
    enrich_financial_text_payload,
)
from src.domain.knowledge_adapters.financial.ontology import FINANCIAL_ADAPTER_SPEC
from src.domain.knowledge_adapters.financial.sources import (
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
    ) -> None:
        self.text_extraction_pipeline = text_extraction_pipeline or TextExtractionPipeline()
        self.news_extraction_strategy = news_extraction_strategy or FinancialNewsExtractionStrategy()
        self.enable_text_extraction = enable_text_extraction

    def normalize(self, raw: Any) -> list[KnowledgeInput]:
        records = raw if isinstance(raw, list) else [raw]
        return [self._normalize_one(record) for record in records]

    async def extract_node_drafts(self, item: KnowledgeInput) -> list[NodeDraft]:
        payload = await self._payload_for_compile(item)
        source_type = item.source_type
        if source_type == "stock_basics":
            return [_stock_node(payload, source_ref=item.source_id)]
        if source_type == "industry_components":
            return _industry_component_nodes(payload, item.source_id)
        if source_type == "concept_components":
            return _concept_component_nodes(payload, item.source_id)
        if source_type == "fund_holdings":
            return _fund_holding_nodes(payload, item.source_id)
        if source_type == "news_articles":
            return _document_nodes(payload, item.source_id, node_type="event")
        if source_type == "policy_news":
            return _document_nodes(payload, item.source_id, node_type="policy")
        if source_type == "l1_events":
            return _document_nodes(payload, item.source_id, node_type="event")
        if source_type == "derived_signal":
            return _derived_signal_nodes(payload, item.source_id)
        return []

    async def extract_edge_drafts(self, item: KnowledgeInput, nodes: list[NodeDraft]) -> list[EdgeDraft]:
        del nodes
        payload = await self._payload_for_compile(item)
        source_type = item.source_type
        evidence_ref = _evidence_ref(item)
        if source_type == "industry_components":
            return [_belongs_to_industry_edge(payload, evidence_ref)]
        if source_type == "concept_components":
            return [_belongs_to_concept_edge(payload, evidence_ref)]
        if source_type == "fund_holdings":
            return [_holds_edge(payload, evidence_ref)]
        if source_type == "news_articles":
            return _document_edges(payload, item.source_id, "event", evidence_ref)
        if source_type == "policy_news":
            return _document_edges(payload, item.source_id, "policy", evidence_ref)
        if source_type == "l1_events":
            return _document_edges(payload, item.source_id, "event", evidence_ref)
        if source_type == "derived_signal":
            return _derived_signal_edges(payload, item.source_id, evidence_ref)
        return []

    def extract_evidence_drafts(self, item: KnowledgeInput) -> list[EvidenceDraft]:
        evidence_type = EvidenceType.RULE_OUTPUT if item.source_type == "derived_signal" else (
            EvidenceType.TEXT_SPAN if item.raw_text else EvidenceType.STRUCTURED_FIELD
        )
        return [
            EvidenceDraft(
                evidence_type=evidence_type,
                source_type=item.source_type,
                source_id=item.source_id,
                content=item.raw_text,
                payload=item.payload,
                metadata={"adapter": self.spec.name},
            )
        ]

    async def _payload_for_compile(self, item: KnowledgeInput) -> dict[str, Any]:
        if not self.enable_text_extraction or item.source_type not in {"news_articles", "policy_news"}:
            return item.payload
        cached = item.metadata.get(_COMPILE_PAYLOAD_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
        enriched = await enrich_financial_text_payload(
            item.payload,
            source_id=item.source_id,
            source_type=item.source_type,
            pipeline=self.text_extraction_pipeline,
            strategy=self.news_extraction_strategy,
        )
        item.metadata[_COMPILE_PAYLOAD_CACHE_KEY] = enriched
        return enriched

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
            payload=payload,
            raw_text=record.get("raw_text") or payload.get("text") or payload.get("summary") or payload.get("title"),
            metadata=dict(record.get("metadata") or {}),
        )


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


def _industry_node(payload: dict[str, Any], *, source_ref: str) -> NodeDraft:
    key = f"{payload['taxonomy']}:{payload.get('component_code') or payload['component_name']}"
    return NodeDraft(
        node_type="industry",
        stable_key=key,
        canonical_name=str(payload["component_name"]),
        external_ids={"taxonomy": str(payload["taxonomy"]), "code": str(payload.get("component_code") or "")},
        properties={"taxonomy": payload["taxonomy"], "code": payload.get("component_code")},
        source_refs=[source_ref],
    )


def _concept_node(payload: dict[str, Any], *, source_ref: str) -> NodeDraft:
    key = f"{payload['taxonomy']}:{payload['component_name']}"
    return NodeDraft(
        node_type="concept",
        stable_key=key,
        canonical_name=str(payload["component_name"]),
        aliases=[str(item) for item in payload.get("aliases", [])],
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


def _industry_component_nodes(payload: dict[str, Any], source_id: str) -> list[NodeDraft]:
    return [
        _industry_node(payload, source_ref=source_id),
        _stock_node(_stock_payload_from_member(payload), source_ref=source_id),
    ]


def _concept_component_nodes(payload: dict[str, Any], source_id: str) -> list[NodeDraft]:
    return [
        _concept_node(payload, source_ref=source_id),
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


def _derived_signal_nodes(payload: dict[str, Any], source_id: str) -> list[NodeDraft]:
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
        nodes.append(_entity_node(target, source_id))
    return _dedupe_nodes(nodes)


def _document_nodes(
    payload: dict[str, Any],
    source_id: str,
    *,
    node_type: str,
) -> list[NodeDraft]:
    nodes = [_event_or_policy_node(payload, source_id, node_type=node_type)]
    for entity in payload.get("mentioned_entities", []) + payload.get("affected_entities", []):
        nodes.append(_entity_node(entity, source_id))
    return _dedupe_nodes(nodes)


def _entity_node(entity: dict[str, Any], source_id: str) -> NodeDraft:
    node_type = entity["type"]
    stable_key = entity_stable_key(entity)
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
            properties={"taxonomy": entity.get("taxonomy", "default"), "code": entity.get("code")},
            source_refs=[source_id],
        )
    if node_type == "concept":
        return NodeDraft(
            node_type="concept",
            stable_key=stable_key,
            canonical_name=str(entity["name"]),
            aliases=[str(item) for item in entity.get("aliases", [])],
            properties={"taxonomy": entity.get("taxonomy", "default")},
            source_refs=[source_id],
        )
    if node_type == "macro_indicator":
        stable_key = entity_stable_key(entity)
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
        source_refs=[source_id],
    )


def _belongs_to_industry_edge(payload: dict[str, Any], evidence_ref: str) -> EdgeDraft:
    relation = default_relation_confidence("belongs_to", source_type="industry_components")
    stock_ref = typed_ref("stock", stock_key(payload["member_stock_exchange"], payload["member_stock_code"]))
    industry_key = f"{payload['taxonomy']}:{payload.get('component_code') or payload['component_name']}"
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


def _belongs_to_concept_edge(payload: dict[str, Any], evidence_ref: str) -> EdgeDraft:
    relation = default_relation_confidence("belongs_to", source_type="concept_components")
    stock_ref = typed_ref("stock", stock_key(payload["member_stock_exchange"], payload["member_stock_code"]))
    concept_key = f"{payload['taxonomy']}:{payload['component_name']}"
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
) -> list[EdgeDraft]:
    source_key = str(payload.get("document_id") or payload.get("event_id") or payload.get("source_id") or source_id)
    source_ref = typed_ref(source_node_type, source_key)
    edges: list[EdgeDraft] = []
    for entity in payload.get("mentioned_entities", []):
        relation = default_relation_confidence("mentions", source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=typed_ref(entity["type"], entity_stable_key(entity)),
                relation_type="mentions",
                confidence_label=relation.label,
                confidence_score=float(entity.get("confidence", relation.score)),
                status=relation.status,
                evidence_refs=[evidence_ref],
            )
        )
    for entity in payload.get("affected_entities", []):
        relation = default_relation_confidence("affects", source_type=source_node_type)
        edges.append(
            EdgeDraft(
                source_ref=source_ref,
                target_ref=typed_ref(entity["type"], entity_stable_key(entity)),
                relation_type="affects",
                confidence_label=relation.label,
                confidence_score=float(entity.get("confidence", relation.score)),
                status=relation.status,
                evidence_refs=[evidence_ref],
                properties={"direction": entity.get("direction"), "reason": entity.get("reason")},
            )
        )
    return edges


def _derived_signal_edges(payload: dict[str, Any], source_id: str, evidence_ref: str) -> list[EdgeDraft]:
    target = _entity_from_target_ref(payload.get("target_ref"))
    if not target:
        return []
    relation = default_relation_confidence("mentions", source_type="event")
    signal_key = str(payload.get("signal_id") or payload.get("source_id") or source_id)
    return [
        EdgeDraft(
            source_ref=typed_ref("event", signal_key),
            target_ref=typed_ref(target["type"], entity_stable_key(target)),
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


def _stock_payload_from_member(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "exchange": payload["member_stock_exchange"],
        "code": payload["member_stock_code"],
        "name": payload.get("member_stock_name") or payload["member_stock_code"],
    }


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
