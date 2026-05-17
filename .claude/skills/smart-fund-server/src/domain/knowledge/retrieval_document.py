"""Retrieval documents derived from knowledge facts."""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, KnowledgeBaseModel
from src.domain.knowledge.wiki import WikiPage

RetrievalSourceFactType = Literal["node", "edge", "evidence", "wiki"]
AnswerCandidateType = Literal["answer", "support", "background", "unknown"]
ImpactDirection = Literal["positive", "negative", "mixed", "neutral", "unknown"]

RETRIEVAL_DOCUMENT_VERSION = "retrieval_doc_v2"

_JSON_FIELD_TOKENS = {
    "aliases",
    "code",
    "company_name",
    "exchange",
    "name",
    "source_id",
    "source_type",
    "status",
    "title",
}

_SCALAR_TERM_KEYS = (
    "title",
    "name",
    "company_name",
    "code",
    "ticker",
    "exchange",
    "taxonomy",
    "indicator_code",
    "target_ref",
    "source_name",
    "source_id",
    "event_name",
    "event_type",
    "signal_type",
    "policy_type",
    "industry",
    "sector",
)

_LIST_TERM_KEYS = (
    "aliases",
    "mentioned_entities",
    "affected_entities",
    "entities",
    "related_entities",
    "industries",
    "concepts",
    "asset_classes",
    "source_type_tags",
)


class RetrievalDocument(KnowledgeBaseModel):
    document_id: str
    adapter_name: str
    target: str = "prod"
    source_fact_type: RetrievalSourceFactType
    source_fact_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    node_refs: list[str] = Field(default_factory=list)
    edge_refs: list[str] = Field(default_factory=list)
    title: str
    search_text: str
    key_phrases: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    event_type: str | None = None
    relation_intents: list[str] = Field(default_factory=list)
    impact_direction: ImpactDirection = "unknown"
    asset_classes: list[str] = Field(default_factory=list)
    time_tags: list[str] = Field(default_factory=list)
    source_type_tags: list[str] = Field(default_factory=list)
    readable_relations: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    answer_candidate_type: AnswerCandidateType = "unknown"
    confidence: float = 0.0
    generated_by: Literal["rule", "llm", "hybrid"] = "rule"
    generation_version: str = RETRIEVAL_DOCUMENT_VERSION
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalDocumentVersion(KnowledgeBaseModel):
    version_id: str = Field(default_factory=lambda: f"kg_rt_doc_version:{uuid4()}")
    adapter_name: str
    target: str = "prod"
    generation_version: str = RETRIEVAL_DOCUMENT_VERSION
    changed_fact_set: dict[str, Any] = Field(default_factory=dict)
    field_coverage: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


def build_retrieval_document_version(
    *,
    adapter_name: str,
    target: str,
    documents: list[RetrievalDocument],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
    wiki_pages: list[WikiPage],
    config: dict[str, Any] | None = None,
) -> RetrievalDocumentVersion:
    return RetrievalDocumentVersion(
        adapter_name=adapter_name,
        target=target,
        generation_version=RETRIEVAL_DOCUMENT_VERSION,
        changed_fact_set={
            "node_ids": [node.node_id for node in nodes],
            "edge_ids": [edge.edge_id for edge in edges],
            "evidence_ids": [item.evidence_id for item in evidence],
            "wiki_page_ids": [page.page_id for page in wiki_pages],
            "document_ids": [document.document_id for document in documents],
        },
        field_coverage=_retrieval_document_field_coverage(documents),
        config=config or {},
    )


def build_retrieval_documents(
    *,
    adapter_name: str,
    target: str,
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
    wiki_pages: list[WikiPage] | None = None,
) -> list[RetrievalDocument]:
    node_by_id = {node.node_id: node for node in nodes}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    readable_relations_by_node: dict[str, list[str]] = {}
    evidence_refs_by_node: dict[str, list[str]] = {}

    for edge in edges:
        relation = _readable_relation(edge, node_by_id)
        for node_id in (edge.source_node_id, edge.target_node_id):
            readable_relations_by_node.setdefault(node_id, []).append(relation)
            evidence_refs_by_node.setdefault(node_id, []).extend(edge.evidence_ids)

    docs: list[RetrievalDocument] = []
    docs.extend(
        _node_document(
            node,
            target=target,
            readable_relations=readable_relations_by_node.get(node.node_id, []),
            evidence_refs=evidence_refs_by_node.get(node.node_id, []),
            evidence_by_id=evidence_by_id,
        )
        for node in nodes
    )
    docs.extend(_edge_document(edge, target=target, node_by_id=node_by_id, evidence_by_id=evidence_by_id) for edge in edges)
    docs.extend(_evidence_document(item, target=target) for item in evidence)
    docs.extend(_wiki_document(page, target=target) for page in wiki_pages or [] if page.adapter_name == adapter_name)
    return _dedupe_documents(docs)


def _retrieval_document_field_coverage(documents: list[RetrievalDocument]) -> dict[str, Any]:
    total = len(documents)
    fields = [
        "search_text",
        "key_phrases",
        "aliases",
        "event_type",
        "relation_intents",
        "impact_direction",
        "asset_classes",
        "time_tags",
        "source_type_tags",
        "readable_relations",
        "evidence_summary",
        "answer_candidate_type",
        "evidence_refs",
        "node_refs",
        "edge_refs",
    ]
    counts = {field: 0 for field in fields}
    for document in documents:
        for field in fields:
            value = getattr(document, field)
            if _has_retrieval_field_value(field, value):
                counts[field] += 1
    ratios = {
        field: round(count / total, 4) if total else 0.0
        for field, count in counts.items()
    }
    return {
        "total_documents": total,
        "filled_counts": counts,
        "filled_ratios": ratios,
    }


def _has_retrieval_field_value(field: str, value: Any) -> bool:
    if field == "impact_direction":
        return bool(value and value != "unknown")
    if field == "answer_candidate_type":
        return bool(value and value != "unknown")
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _node_document(
    node: CompiledNode,
    *,
    target: str,
    readable_relations: list[str],
    evidence_refs: list[str],
    evidence_by_id: dict[str, CompiledEvidence],
) -> RetrievalDocument:
    aliases = _clean_key_phrases([*node.aliases, *_external_id_terms(node.external_ids), *_property_alias_terms(node.properties)])
    key_phrases = _clean_key_phrases(
        [node.canonical_name, node.node_type, *aliases, *_property_terms(node.properties)]
    )
    evidence_summary = _node_evidence_summary(
        node.properties,
        evidence_refs=evidence_refs,
        evidence_by_id=evidence_by_id,
        readable_relations=readable_relations,
    )
    search_text = _join_text([node.canonical_name, node.node_type, *aliases, *key_phrases, *readable_relations, evidence_summary])
    return RetrievalDocument(
        document_id=_document_id("node", node.node_id, target),
        adapter_name=node.adapter_name,
        target=target,
        source_fact_type="node",
        source_fact_id=node.node_id,
        title=node.canonical_name,
        search_text=search_text,
        key_phrases=key_phrases,
        aliases=aliases,
        event_type=_event_type(node.properties, node.node_type),
        relation_intents=_relation_intents_from_text(readable_relations),
        impact_direction=_impact_direction(search_text),
        asset_classes=_asset_classes(search_text),
        time_tags=_time_tags(search_text),
        source_type_tags=[],
        readable_relations=_ordered_unique(readable_relations),
        evidence_summary=evidence_summary,
        answer_candidate_type=_node_answer_candidate_type(node),
        node_refs=[node.node_id],
        evidence_refs=_ordered_unique(evidence_refs),
        confidence=0.8,
        metadata={"node_type": node.node_type, "version": node.version},
    )


def _edge_document(
    edge: CompiledEdge,
    *,
    target: str,
    node_by_id: dict[str, CompiledNode],
    evidence_by_id: dict[str, CompiledEvidence],
) -> RetrievalDocument:
    relation = _readable_relation(edge, node_by_id)
    summaries = [_evidence_summary(evidence_by_id[item]) for item in edge.evidence_ids if item in evidence_by_id]
    title = relation
    search_text = _join_text([relation, edge.relation_type, *summaries])
    return RetrievalDocument(
        document_id=_document_id("edge", edge.edge_id, target),
        adapter_name=edge.adapter_name,
        target=target,
        source_fact_type="edge",
        source_fact_id=edge.edge_id,
        title=title,
        search_text=search_text,
        key_phrases=_clean_key_phrases([edge.relation_type, *_terms_from_text(relation)]),
        aliases=[],
        event_type=None,
        relation_intents=_relation_intents(edge.relation_type),
        impact_direction=_impact_direction(search_text),
        asset_classes=_asset_classes(search_text),
        time_tags=_time_tags(search_text),
        source_type_tags=[],
        readable_relations=[relation],
        evidence_summary=_join_text(summaries),
        answer_candidate_type="support",
        node_refs=[edge.source_node_id, edge.target_node_id],
        edge_refs=[edge.edge_id],
        evidence_refs=edge.evidence_ids,
        confidence=float(edge.confidence_score),
        metadata={"relation_type": edge.relation_type, "version": edge.version},
    )


def _evidence_document(evidence: CompiledEvidence, *, target: str) -> RetrievalDocument:
    summary = _evidence_summary(evidence)
    title = _evidence_title(evidence)
    aliases = _clean_key_phrases([evidence.source_id, *_payload_alias_terms(evidence.payload)])
    key_phrases = _clean_key_phrases(
        [title, evidence.source_type, *aliases, *_payload_terms(evidence.payload), *_terms_from_text(summary)]
    )
    search_text = _join_text([title, evidence.source_type, evidence.source_id, summary, *aliases, *key_phrases])
    node_refs = _node_refs_from_payload(evidence.payload)
    return RetrievalDocument(
        document_id=_document_id("evidence", evidence.evidence_id, target),
        adapter_name=evidence.adapter_name,
        target=target,
        source_fact_type="evidence",
        source_fact_id=evidence.evidence_id,
        title=title,
        search_text=search_text,
        key_phrases=key_phrases,
        aliases=aliases,
        event_type=_event_type(evidence.payload, evidence.source_type),
        relation_intents=_relation_intents_from_text([search_text]),
        impact_direction=_impact_direction(search_text),
        asset_classes=_asset_classes(search_text),
        time_tags=_time_tags(search_text),
        source_type_tags=[evidence.source_type],
        readable_relations=[],
        evidence_summary=summary,
        answer_candidate_type="answer",
        node_refs=node_refs,
        evidence_refs=[evidence.evidence_id],
        confidence=0.75,
        metadata={
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "evidence_type": str(evidence.evidence_type.value if hasattr(evidence.evidence_type, "value") else evidence.evidence_type),
            "version": evidence.version,
        },
    )


def _wiki_document(page: WikiPage, *, target: str) -> RetrievalDocument:
    search_text = _join_text([page.title, page.summary, page.content])
    return RetrievalDocument(
        document_id=_document_id("wiki", page.page_id, target),
        adapter_name=page.adapter_name,
        target=target,
        source_fact_type="wiki",
        source_fact_id=page.page_id,
        title=page.title,
        search_text=search_text,
        key_phrases=_clean_key_phrases([page.title, *_terms_from_text(page.summary)]),
        aliases=[],
        event_type=None,
        relation_intents=_relation_intents_from_text([search_text]),
        impact_direction=_impact_direction(search_text),
        asset_classes=_asset_classes(search_text),
        time_tags=_time_tags(search_text),
        source_type_tags=[page.page_type],
        readable_relations=[],
        evidence_summary=page.summary,
        answer_candidate_type="background" if page.page_type == "index_page" else "support",
        node_refs=page.source_node_ids,
        edge_refs=page.source_edge_ids,
        evidence_refs=page.source_evidence_ids,
        confidence=0.65,
        metadata={"page_type": page.page_type, "version": page.version},
    )


def _document_id(source_fact_type: str, source_fact_id: str, target: str) -> str:
    return f"kg_rdoc:{target}:{source_fact_type}:{source_fact_id}"


def _readable_relation(edge: CompiledEdge, node_by_id: dict[str, CompiledNode]) -> str:
    source = node_by_id.get(edge.source_node_id)
    target = node_by_id.get(edge.target_node_id)
    source_title = source.canonical_name if source else edge.source_node_id
    target_title = target.canonical_name if target else edge.target_node_id
    return f"{source_title} {edge.relation_type} {target_title}"


def _node_answer_candidate_type(node: CompiledNode) -> AnswerCandidateType:
    if node.node_type in {"event", "policy", "industry", "asset", "concept", "commodity", "macro_indicator"}:
        return "answer"
    if node.node_type in {"stock", "company", "person", "region"}:
        return "support"
    return "unknown"


def _evidence_title(evidence: CompiledEvidence) -> str:
    payload = evidence.payload or {}
    for key in ("title", "source_name", "signal_type", "event_name", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{evidence.source_type}:{evidence.source_id}"


def _evidence_summary(evidence: CompiledEvidence) -> str:
    payload = evidence.payload or {}
    for key in ("summary", "abstract", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if evidence.content and evidence.content.strip():
        return evidence.content.strip()
    structured_summary = _structured_payload_summary(payload)
    if structured_summary:
        return structured_summary
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""


def _event_type(payload: dict[str, Any], fallback: str | None = None) -> str | None:
    for key in ("event_type", "signal_type", "policy_type", "source_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _external_id_terms(external_ids: dict[str, str]) -> list[str]:
    return [str(value) for value in external_ids.values() if value]


def _property_alias_terms(properties: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("source_id", "source_ref", "source_key", "code", "ticker"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    terms.extend(_payload_alias_terms(properties))
    return _clean_key_phrases(terms)


def _property_terms(properties: dict[str, Any]) -> list[str]:
    return _payload_terms(properties)


def _payload_terms(payload: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in _SCALAR_TERM_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    for key in _LIST_TERM_KEYS:
        values = payload.get(key)
        terms.extend(_terms_from_payload_value(values))
    return _clean_key_phrases(terms)


def _payload_alias_terms(payload: dict[str, Any]) -> list[str]:
    values = payload.get("aliases")
    if not isinstance(values, list):
        return []
    return _clean_key_phrases(item for item in values if isinstance(item, str))


def _terms_from_payload_value(value: Any) -> list[str]:
    terms: list[str] = []
    if isinstance(value, str):
        terms.append(value)
    elif isinstance(value, list):
        for item in value:
            terms.extend(_terms_from_payload_value(item))
    elif isinstance(value, dict):
        for key in ("name", "canonical_name", "title", "code", "ticker", "company_name", "value"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                terms.append(item.strip())
    return terms


def _structured_payload_summary(payload: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "company_name", "name", "code", "ticker", "exchange", "source_name", "event_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    values.extend(_payload_alias_terms(payload))
    return _join_text(_clean_key_phrases(values))


def _node_evidence_summary(
    properties: dict[str, Any],
    *,
    evidence_refs: list[str],
    evidence_by_id: dict[str, CompiledEvidence],
    readable_relations: list[str],
) -> str:
    explicit = _string_property(properties, ["summary", "description", "content", "raw_text"])
    if explicit:
        return explicit

    summaries: list[str] = []
    for evidence_ref in evidence_refs:
        evidence = evidence_by_id.get(evidence_ref)
        if evidence is None:
            continue
        summary = _evidence_summary(evidence)
        if summary:
            summaries.append(summary)
        if len(summaries) >= 2:
            break
    if summaries:
        return _join_text(summaries)

    return _join_text(readable_relations[:3])


def _node_refs_from_payload(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("node_refs", "source_node_ids", "target_node_ids"):
        values = payload.get(key)
        if isinstance(values, list):
            refs.extend(str(item) for item in values if isinstance(item, str) and item.startswith("kg:"))
    return _ordered_unique(refs)


def _string_property(payload: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _relation_intents(relation_type: str) -> list[str]:
    mapping = {
        "affects": "impact",
        "benefits_from": "beneficiary",
        "causes": "cause",
        "risks": "risk",
        "related_to": "related",
        "mentions": "mention",
        "belongs_to": "taxonomy",
    }
    return [mapping.get(relation_type, relation_type)]


def _relation_intents_from_text(values: list[str]) -> list[str]:
    text = "\n".join(values).lower()
    intents: list[str] = []
    for token, intent in {
        "affects": "impact",
        "impact": "impact",
        "影响": "impact",
        "利好": "impact",
        "利空": "impact",
        "benefits_from": "beneficiary",
        "受益": "beneficiary",
        "risk": "risk",
        "风险": "risk",
        "causes": "cause",
        "导致": "cause",
    }.items():
        if token in text:
            intents.append(intent)
    return _ordered_unique(intents)


def _impact_direction(text: str) -> ImpactDirection:
    positive = any(token in text for token in ("利好", "改善", "增长", "受益", "positive", "benefit"))
    negative = any(token in text for token in ("利空", "下滑", "风险", "处罚", "negative", "risk"))
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "unknown"


def _asset_classes(text: str) -> list[str]:
    classes: list[str] = []
    for token, asset_class in {
        "股票": "stock",
        "股价": "stock",
        "stock": "stock",
        "债券": "bond",
        "债": "bond",
        "bond": "bond",
        "商品": "commodity",
        "原油": "commodity",
        "黄金": "commodity",
        "汇率": "currency",
        "货币": "currency",
        "currency": "currency",
    }.items():
        if token in text:
            classes.append(asset_class)
    return _ordered_unique(classes)


def _time_tags(text: str) -> list[str]:
    return _ordered_unique(re.findall(r"\b20\d{2}(?:[-/年]\d{1,2})?(?:[-/月]\d{1,2})?", text))


def _terms_from_text(text: str) -> list[str]:
    if not text:
        return []
    return _clean_key_phrases(
        item for item in re.split(r"[\s,，。；;:：|/()（）]+", text) if len(item) >= 2
    )[:20]


def _join_text(values: list[str]) -> str:
    return "\n".join(_ordered_unique(str(value).strip() for value in values if str(value).strip()))


def _clean_key_phrases(values) -> list[str]:
    return _ordered_unique(_normalize_key_phrase(value) for value in values)


def _normalize_key_phrase(value: Any) -> str:
    if value is None:
        return ""
    item = str(value).strip()
    if not item:
        return ""
    item = item.strip(" \t\r\n,，。；;:：")
    item = item.strip("'\"")
    item = item.strip()
    if not item:
        return ""
    lowered = item.lower()
    if lowered in _JSON_FIELD_TOKENS:
        return ""
    if item.startswith(("{", "[", "}")) or item.endswith(("}", "]")):
        return ""
    if re.fullmatch(r"[\W_]+", item):
        return ""
    return item


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_documents(documents: list[RetrievalDocument]) -> list[RetrievalDocument]:
    by_id: dict[str, RetrievalDocument] = {}
    for document in documents:
        by_id[document.document_id] = document
    return list(by_id.values())
