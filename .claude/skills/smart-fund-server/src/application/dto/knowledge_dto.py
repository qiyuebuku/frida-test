"""Knowledge application DTOs shared by API and CLI use cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

Target = Literal["prod", "test"]


class KnowledgeDTO:
    """Small serialization helper for application DTOs."""

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class KnowledgeHealthDTO(KnowledgeDTO):
    status: str
    database: str
    adapters: list[str] = field(default_factory=list)
    implemented: list[str] = field(default_factory=list)


@dataclass
class KnowledgeCompileCommand:
    adapter_name: str = "financial"
    records: list[dict[str, Any]] = field(default_factory=list)
    target: Target = "prod"
    dry_run: bool = False
    request_id: str | None = None
    concurrency: int | None = None


@dataclass
class KnowledgeCompileResultDTO(KnowledgeDTO):
    adapter_name: str
    run_id: str
    nodes: int
    edges: int
    evidence: int
    failed_records: int
    warnings: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class KnowledgeBootstrapStocksCommand:
    target: Target = "prod"
    codes: list[str] = field(default_factory=list)
    limit: int = 500
    dry_run: bool = False
    request_id: str | None = None


@dataclass
class KnowledgeBootstrapStockNewsCommand:
    target: Target = "prod"
    codes: list[str] = field(default_factory=list)
    limit: int = 20
    dry_run: bool = False
    request_id: str | None = None
    concurrency: int | None = 1


@dataclass
class KnowledgeIncrementalRefreshCommand:
    target: Target = "prod"
    codes: list[str] = field(default_factory=list)
    stock_limit: int = 500
    news_limit: int = 20
    dry_run: bool = False
    request_id: str | None = None
    concurrency: int | None = 1
    rebuild_indexes: bool = True


@dataclass
class KnowledgeIncrementalRefreshResultDTO(KnowledgeDTO):
    adapter_name: str
    target: Target
    run_id: str
    dry_run: bool
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class KnowledgeRebuildWikiCommand:
    adapter_name: str = "financial"
    target: Target = "prod"
    scope: str = "all"


@dataclass
class KnowledgeRebuildWikiResultDTO(KnowledgeDTO):
    adapter_name: str
    run_id: str | None
    pages: int
    issues: int
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class KnowledgeRebuildIndexesCommand:
    adapter_name: str = "financial"
    target: Target = "prod"
    index_types: list[str] = field(default_factory=lambda: ["graph_adjacency", "evidence_chunks"])
    scope: str = "all"


@dataclass
class KnowledgeRebuildIndexesResultDTO(KnowledgeDTO):
    adapter_name: str
    run_id: str | None
    graph_adjacency: int = 0
    evidence_chunks: int = 0
    hybrid_chunks: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class KnowledgeResearchContextCommand:
    query: str
    adapter_name: str = "financial"
    target: Target = "prod"
    retrieval_mode: Literal["deterministic_plan", "agentic_arag"] = "agentic_arag"
    graph_depth: int = 3
    graph_limit: int = 20
    wiki_limit: int = 10
    evidence_limit: int = 20
    max_chars: int = 5000


@dataclass
class KnowledgeResearchContextDTO(KnowledgeDTO):
    query: str
    hits: list[dict[str, Any]] = field(default_factory=list)
    matched_nodes: list[dict[str, Any]] = field(default_factory=list)
    matched_edges: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    hard_score_edges: list[dict[str, Any]] = field(default_factory=list)
    explanation_edges: list[dict[str, Any]] = field(default_factory=list)
    context_text: str = ""
    budget_usage: dict[str, Any] = field(default_factory=dict)
    mode: str = "deterministic_plan"
    retrieval_channels_enabled: list[str] = field(default_factory=list)
    retrieval_channels_used: list[str] = field(default_factory=list)
    semantic_enabled: bool = False
    milvus_enabled: bool = False
    agentic_enabled: bool = False
    planner_enabled: bool = False
    retrieval_plan: dict[str, Any] = field(default_factory=dict)
    retrieval_trace: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class KnowledgeResearchContextBadCase:
    case_id: str
    query: str
    expected_evidence_refs: list[str] = field(default_factory=list)
    expected_hit_titles: list[str] = field(default_factory=list)
    expected_top_hit_titles: list[str] = field(default_factory=list)
    top_k: int = 5
    expected_node_names: list[str] = field(default_factory=list)
    expected_relation_types: list[str] = field(default_factory=list)
    expected_channels_used: list[str] = field(default_factory=list)
    min_hits: int = 0
    min_evidence_refs: int = 0
    min_matched_nodes: int = 0
    min_matched_edges: int = 0
    retrieval_mode: Literal["deterministic_plan", "agentic_arag"] = "deterministic_plan"
    replay_trace: bool = False
    recorded_trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeBadCaseReplayCommand:
    adapter_name: str = "financial"
    target: Target = "prod"
    cases: list[KnowledgeResearchContextBadCase] = field(default_factory=list)
    graph_depth: int = 3
    graph_limit: int = 20
    wiki_limit: int = 10
    evidence_limit: int = 20
    max_chars: int = 5000


@dataclass
class KnowledgeBadCaseReplayResultDTO(KnowledgeDTO):
    adapter_name: str
    target: Target
    total: int
    passed: int
    failed: int
    metrics: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class KnowledgeQualityScanCommand:
    adapter_name: str = "financial"
    target: Target = "prod"
    persist_review: bool = True


@dataclass
class KnowledgeQualityScanResultDTO(KnowledgeDTO):
    adapter_name: str
    run_id: str | None
    ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    review_items: int = 0


@dataclass
class KnowledgeReviewActionCommand:
    review_id: str
    action: str
    target: Target = "prod"
    operator: str | None = None
    reason: str | None = None


def dto_to_dict(value: Any) -> Any:
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value
