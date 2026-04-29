"""Financial ontology expressed as a generic adapter spec."""

from src.domain.knowledge.adapter import (
    AdapterSpec,
    ConsumptionRule,
    EntityTypeSpec,
    RelationTypeSpec,
    SourceTypeSpec,
    WikiPageSpec,
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
            source_types=["stock", "concept"],
            target_types=["industry", "concept"],
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
            target_types=["stock", "fund", "industry", "concept", "institution", "macro_indicator", "policy", "region", "person", "commodity"],
            allow_inferred=True,
            allowed_confidence_labels=[ConfidenceLabel.EXTRACTED, ConfidenceLabel.INFERRED],
            allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
        ),
        RelationTypeSpec(
            name="affects",
            source_types=["event", "policy"],
            target_types=["stock", "fund", "industry", "concept", "macro_indicator", "institution", "region", "person", "commodity", "policy"],
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
            source_types=["stock", "fund", "industry", "concept", "event", "policy", "institution"],
            target_types=["stock", "fund", "industry", "concept", "event", "policy", "institution"],
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
    wiki_pages=[
        WikiPageSpec(page_type="financial_entity_page", subject_types=["stock", "fund", "industry", "concept"]),
        WikiPageSpec(page_type="financial_event_page", subject_types=["event", "policy"]),
    ],
)
