"""应用层 services — 用例 (use case) 入口

每个 use case = 一个公共方法,接收 dto/参数,调 domain services + repositories,
返回 dto。task 层只调这里,不直接 import domain。

当前任务入口只保留数据采集相关 use case:
- CollectionAppService: 5 个数据采集 use case
- KnowledgeNewsIngestionService: 新增新闻入知识图谱 use case
- RelationGraphCommunityService: 关系图 Community 异步刷新 use case
- RelationGraphCommunityCognitionService: Community 报告与预测用例
- RelationGraphExplorerService: Community 关系图只读查询用例
- RelationGraphAgentRetrievalService: Agent 关系图检索工具内核
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "AgentMarketQueryService",
    "CollectionAppService",
    "FtNewsKnowledgeGraphWorkflowService",
    "KnowledgeNewsIngestionService",
    "MarketObservabilityService",
    "MarketObservationService",
    "RelationGraphCommunityService",
    "RelationGraphCommunityCognitionService",
    "RelationGraphExplorerService",
    "RelationGraphAgentRetrievalService",
    "RelationDiscoveryService",
]


def __getattr__(name: str) -> Any:
    """Lazy-load app services to avoid importing unrelated infrastructure deps.

    Importing a submodule such as ``src.application.services.knowledge_service`` must
    not require optional dependencies from collection/trading services.
    """

    if name == "AgentMarketQueryService":
        from src.application.services.agent_market_query_service import (
            AgentMarketQueryService,
        )

        return AgentMarketQueryService
    if name == "CollectionAppService":
        from src.application.services.collection_app_service import CollectionAppService

        return CollectionAppService
    if name == "KnowledgeNewsIngestionService":
        from src.application.services.knowledge_news_ingestion_service import (
            KnowledgeNewsIngestionService,
        )

        return KnowledgeNewsIngestionService
    if name == "MarketObservationService":
        from src.application.services.market_observation_service import (
            MarketObservationService,
        )

        return MarketObservationService
    if name == "MarketObservabilityService":
        from src.application.services.market_observability_service import (
            MarketObservabilityService,
        )

        return MarketObservabilityService
    if name == "FtNewsKnowledgeGraphWorkflowService":
        from src.application.services.ft_news_knowledge_graph_workflow_service import (
            FtNewsKnowledgeGraphWorkflowService,
        )

        return FtNewsKnowledgeGraphWorkflowService
    if name == "RelationGraphCommunityService":
        from src.application.services.relation_graph_community_service import (
            RelationGraphCommunityService,
        )

        return RelationGraphCommunityService
    if name == "RelationGraphCommunityCognitionService":
        from src.application.services.relation_graph_community_cognition_service import (
            RelationGraphCommunityCognitionService,
        )

        return RelationGraphCommunityCognitionService
    if name == "RelationGraphExplorerService":
        from src.application.services.relation_graph_explorer_service import (
            RelationGraphExplorerService,
        )

        return RelationGraphExplorerService
    if name == "RelationGraphAgentRetrievalService":
        from src.application.services.relation_graph_agent_retrieval_service import (
            RelationGraphAgentRetrievalService,
        )

        return RelationGraphAgentRetrievalService
    if name == "RelationDiscoveryService":
        from src.application.services.relation_discovery_service import RelationDiscoveryService

        return RelationDiscoveryService
    raise AttributeError(name)
