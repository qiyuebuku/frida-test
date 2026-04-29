"""Generic knowledge enums."""

from enum import Enum


class InputType(str, Enum):
    STRUCTURED_RECORD = "structured_record"
    SEMI_STRUCTURED_RECORD = "semi_structured_record"
    DOCUMENT_CHUNK = "document_chunk"
    EVENT_RECORD = "event_record"
    DERIVED_SIGNAL = "derived_signal"
    FEEDBACK_RECORD = "feedback_record"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    AMBIGUOUS = "ambiguous"
    MERGED = "merged"
    DEPRECATED = "deprecated"


class EdgeStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    REVIEW_REQUIRED = "review_required"
    CONFLICT = "conflict"
    DEPRECATED = "deprecated"


class ConfidenceLabel(str, Enum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    REJECTED = "REJECTED"


class EvidenceType(str, Enum):
    STRUCTURED_FIELD = "structured_field"
    TEXT_SPAN = "text_span"
    RULE_OUTPUT = "rule_output"
    LLM_OUTPUT = "llm_output"
    HUMAN_REVIEW = "human_review"
    HISTORICAL_VALIDATION = "historical_validation"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
