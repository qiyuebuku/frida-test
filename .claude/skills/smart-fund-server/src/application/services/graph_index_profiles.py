"""Application-level Graph Index projection profiles."""

from __future__ import annotations

from src.domain.knowledge.graph_index import GraphProjectionProfile


FINANCIAL_GRAPH_PROJECTIONS: tuple[GraphProjectionProfile, ...] = (
    GraphProjectionProfile(
        projection="default_graph_projection",
        description="全量事实图自动发现 community 的基线视角",
    ),
)


GRAPH_INDEX_PUBLIC_LENS_ALIASES: dict[str, str] = {
    "narrative": "market_narrative",
    "chain": "industry_chain",
    "impact": "policy_impact",
    "risk": "risk_event",
}
