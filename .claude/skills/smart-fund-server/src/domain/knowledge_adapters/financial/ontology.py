"""Financial ontology expressed as a generic adapter spec."""

from src.domain.knowledge.adapter import (
    AdapterSpec,
    ConsumptionRule,
    EntityTypeSpec,
    RelationTypeSpec,
    SourceTypeSpec,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, InputType


CORE_ENTITY_TYPES = {
    "stock",
    "fund",
    "industry",
    "concept",
    "event",
    "policy",
    "institution",
    "macro_indicator",
    "commodity",
    "person",
    "region",
    "supplier",
    "product",
}

CORE_RELATION_TYPES = {
    "belongs_to",
    "holds",
    "mentions",
    "affects",
    "benefits_from",
    "hurt_by",
    "upstream_of",
    "downstream_of",
    "alias_of",
    "related_to",
    "causal_hint",
}


def extend_financial_adapter_spec(
    *,
    extra_entity_types: set[str] | None = None,
    extra_relation_types: set[str] | None = None,
) -> AdapterSpec:
    """Return a financial adapter spec extended by active type registry entries."""

    entity_types = {item.name for item in FINANCIAL_ADAPTER_SPEC.entities}
    relation_types = {item.name for item in FINANCIAL_ADAPTER_SPEC.relations}
    extra_entities = sorted((extra_entity_types or set()) - entity_types)
    extra_relations = sorted((extra_relation_types or set()) - relation_types)
    if not extra_entities and not extra_relations:
        return FINANCIAL_ADAPTER_SPEC

    entities = [
        *FINANCIAL_ADAPTER_SPEC.entities,
        *[
            EntityTypeSpec(
                name=entity_type,
                stable_id_fields=["canonical_name"],
                allow_auto_create=True,
                allow_auto_merge=False,
            )
            for entity_type in extra_entities
        ],
    ]
    all_entity_types = [item.name for item in entities]
    flexible_relations = {
        "mentions",
        "affects",
        "benefits_from",
        "hurt_by",
        "alias_of",
        "related_to",
        "causal_hint",
    }
    relations = []
    for relation in FINANCIAL_ADAPTER_SPEC.relations:
        if relation.name in flexible_relations and extra_entities:
            relations.append(
                relation.model_copy(
                    update={
                        "source_types": _ordered_unique([*relation.source_types, *extra_entities]),
                        "target_types": _ordered_unique([*relation.target_types, *extra_entities]),
                    }
                )
            )
        else:
            relations.append(relation)
    relations.extend(
        RelationTypeSpec(
            name=relation_type,
            source_types=all_entity_types,
            target_types=all_entity_types,
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED, EdgeStatus.ACTIVE],
        )
        for relation_type in extra_relations
    )
    return FINANCIAL_ADAPTER_SPEC.model_copy(update={"entities": entities, "relations": relations})


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result

FINANCIAL_ADAPTER_SPEC = AdapterSpec(
    name="financial",
    version="v1",
    entities=[
        EntityTypeSpec(name="stock", stable_id_fields=["exchange", "code"]),
        EntityTypeSpec(name="fund", stable_id_fields=["fund_code"]),
        EntityTypeSpec(name="industry", stable_id_fields=["taxonomy", "industry_code"]),
        EntityTypeSpec(name="concept", stable_id_fields=["taxonomy", "canonical_name"]),
        EntityTypeSpec(name="event", stable_id_fields=["event_id"]),
        EntityTypeSpec(name="policy", stable_id_fields=["document_id"]),
        EntityTypeSpec(name="institution", stable_id_fields=["external_id", "canonical_name"]),
        EntityTypeSpec(name="macro_indicator", stable_id_fields=["indicator_code"]),
        EntityTypeSpec(name="commodity", stable_id_fields=["commodity_code"]),
        EntityTypeSpec(name="person", stable_id_fields=["external_id", "canonical_name"]),
        EntityTypeSpec(name="region", stable_id_fields=["region_code", "canonical_name"]),
        EntityTypeSpec(name="supplier", stable_id_fields=["canonical_name"]),
        EntityTypeSpec(name="product", stable_id_fields=["canonical_name"]),
    ],
    relations=[
        RelationTypeSpec(
            name="belongs_to",
            source_types=["stock", "concept", "region"],
            target_types=["industry", "concept", "region"],
            allow_inferred=False,
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.HUMAN_VERIFIED],
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="holds",
            source_types=["fund"],
            target_types=["stock"],
            allow_inferred=False,
            has_validity_window=True,
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.HUMAN_VERIFIED],
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="mentions",
            source_types=["event", "policy"],
            target_types=["stock", "fund", "industry", "concept", "event", "institution", "macro_indicator", "policy", "region", "person", "commodity", "supplier", "product"],
            allow_inferred=True,
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.INFERRED],
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="affects",
            source_types=["event", "policy"],
            target_types=["stock", "fund", "industry", "concept", "macro_indicator", "institution", "region", "person", "commodity", "policy", "supplier", "product"],
            allow_inferred=True,
            allowed_confidence_labels=[
                ConfidenceLabel.EXTRACTED,
                ConfidenceLabel.INFERRED,
                ConfidenceLabel.HUMAN_VERIFIED,
            ],
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED],
        ),
        RelationTypeSpec(
            name="benefits_from",
            source_types=["stock", "industry", "concept"],
            target_types=["event", "policy", "concept"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED, EdgeStatus.ACTIVE],
        ),
        RelationTypeSpec(
            name="hurt_by",
            source_types=["stock", "industry", "concept"],
            target_types=["event", "policy", "concept"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED, EdgeStatus.ACTIVE],
        ),
        RelationTypeSpec(
            name="upstream_of",
            source_types=["concept", "industry"],
            target_types=["concept", "industry"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED],
        ),
        RelationTypeSpec(
            name="downstream_of",
            source_types=["concept", "industry"],
            target_types=["concept", "industry"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED],
        ),
        RelationTypeSpec(
            name="alias_of",
            source_types=["stock", "fund", "industry", "concept", "event", "policy", "institution"],
            target_types=["stock", "fund", "industry", "concept", "event", "policy", "institution"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.REVIEW_REQUIRED, EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="related_to",
            source_types=[
                "stock",
                "fund",
                "industry",
                "concept",
                "event",
                "policy",
                "institution",
                "macro_indicator",
                "commodity",
                "person",
                "region",
                "supplier",
                "product",
            ],
            target_types=[
                "stock",
                "fund",
                "industry",
                "concept",
                "event",
                "policy",
                "institution",
                "macro_indicator",
                "commodity",
                "person",
                "region",
                "supplier",
                "product",
            ],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="causal_hint",
            source_types=["event", "policy", "stock", "fund", "industry", "concept"],
            target_types=["stock", "fund", "industry", "concept"],
            allow_inferred=True,
            allowed_statuses=[EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED],
        ),
    ],
    sources=[
        SourceTypeSpec(
            name="stock_basics",
            input_type=InputType.STRUCTURED_RECORD,
            required_fields=["code", "name", "exchange"],
        ),
        SourceTypeSpec(
            name="industry_components",
            input_type=InputType.STRUCTURED_RECORD,
            required_fields=["taxonomy", "component_name", "member_stock_code", "member_stock_exchange"],
        ),
        SourceTypeSpec(
            name="concept_components",
            input_type=InputType.STRUCTURED_RECORD,
            required_fields=["taxonomy", "component_name", "member_stock_code", "member_stock_exchange"],
        ),
        SourceTypeSpec(
            name="fund_holdings",
            input_type=InputType.STRUCTURED_RECORD,
            required_fields=["fund_code", "stock_code", "stock_exchange", "report_date"],
        ),
        SourceTypeSpec(
            name="news_articles",
            input_type=InputType.SEMI_STRUCTURED_RECORD,
            required_fields=["source_id", "published_at"],
        ),
        SourceTypeSpec(
            name="policy_news",
            input_type=InputType.SEMI_STRUCTURED_RECORD,
            required_fields=["source_id", "published_at"],
        ),
        SourceTypeSpec(
            name="l1_events",
            input_type=InputType.EVENT_RECORD,
            required_fields=["event_id", "event_type", "event_time"],
        ),
        SourceTypeSpec(
            name="derived_signal",
            input_type=InputType.DERIVED_SIGNAL,
            required_fields=["target_ref", "signal_type", "observed_at", "value"],
        ),
        SourceTypeSpec(
            name="feedback_records",
            input_type=InputType.FEEDBACK_RECORD,
            required_fields=["target_type", "target_ref", "action", "reason"],
        ),
    ],
    consumption_rules=[
        ConsumptionRule(
            consumer="hard_score",
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.HUMAN_VERIFIED],
            allowed_edge_statuses=[EdgeStatus.ACTIVE],
            requires_human_review=False,
        ),
        ConsumptionRule(
            consumer="retrieval",
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.INFERRED],
            allowed_edge_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
        ),
        ConsumptionRule(
            consumer="explanation",
            allowed_confidence_labels=[
                ConfidenceLabel.EXTRACTED,
                ConfidenceLabel.INFERRED,
                ConfidenceLabel.HUMAN_VERIFIED,
            ],
            allowed_edge_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE, EdgeStatus.REVIEW_REQUIRED],
        ),
    ],
)
