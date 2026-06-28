"""ORM 模型 — 与 schema/*.sql 一一对应

所有模型继承 base.Base,通过 import 触发注册到 Base.registry。
"""
from src.infrastructure.persistence.models.base import Base
from src.infrastructure.persistence.models.collection import (
    CollectionState,
    MacroIndicator,
    MarketCache,
    MarketFlow,
    News,
    Sentiment,
    WatchlistData,
)
from src.infrastructure.persistence.models.extraction import (
    Event,
    EventStream,
    RuleThreshold,
)
from src.infrastructure.persistence.models.trading import (
    Decision,
    FundLimit,
    IndexFund,
    IndustryIndex,
    IndustryMapping,
    PendingDecision,
    Position,
    Trade,
)
from src.infrastructure.persistence.models.reflection import (
    Lesson,
    Review,
)
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeAssignmentCandidateOrder,
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeGraphCommunity,
    KnowledgeGraphFinding,
    KnowledgeGraphUnassignedSignal,
    KnowledgeNode,
    KnowledgeNormalizationRule,
    KnowledgeRetrievalEvalMetric,
    KnowledgeRetrievalEvalRun,
    KnowledgeRetrievalLabel,
    KnowledgeRetrievalTraceSnapshot,
    KnowledgeReviewItem,
    KnowledgeVersion,
)

__all__ = [
    "Base",
    # collection
    "News",
    "MarketFlow",
    "MarketCache",
    "Sentiment",
    "MacroIndicator",
    "CollectionState",
    "WatchlistData",
    # extraction
    "Event",
    "EventStream",
    "RuleThreshold",
    # trading
    "PendingDecision",
    "Decision",
    "Trade",
    "Position",
    "IndustryMapping",
    "IndustryIndex",
    "IndexFund",
    "FundLimit",
    # reflection
    "Review",
    "Lesson",
    # knowledge
    "KnowledgeNode",
    "KnowledgeAssignmentCandidateOrder",
    "KnowledgeNormalizationRule",
    "KnowledgeEdge",
    "KnowledgeEvidence",
    "KnowledgeEdgeEvidence",
    "KnowledgeEdgeEvidenceChunk",
    "KnowledgeVersion",
    "KnowledgeReviewItem",
    "KnowledgeCompilationRun",
    "KnowledgeGraphAdjacency",
    "KnowledgeGraphCommunity",
    "KnowledgeGraphFinding",
    "KnowledgeGraphUnassignedSignal",
    "KnowledgeEvidenceChunk",
    "KnowledgeRetrievalTraceSnapshot",
    "KnowledgeRetrievalLabel",
    "KnowledgeRetrievalEvalRun",
    "KnowledgeRetrievalEvalMetric",
]
