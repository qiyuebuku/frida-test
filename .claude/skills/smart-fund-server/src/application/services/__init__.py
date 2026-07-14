"""应用层 services — 用例 (use case) 入口

每个 use case = 一个公共方法,接收 dto/参数,调 domain services + repositories,
返回 dto。task 层只调这里,不直接 import domain。

当前任务入口只保留数据采集相关 use case:
- CollectionAppService: 5 个数据采集 use case
- KnowledgeNewsIngestionService: 新增新闻入知识图谱 use case
- CommunityInsightService: Community Insight 异步刷新 use case
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "CollectionAppService",
    "CommunityInsightService",
    "FtNewsKnowledgeGraphWorkflowService",
    "KnowledgeNewsIngestionService",
    "RelationDiscoveryService",
]


def __getattr__(name: str) -> Any:
    """Lazy-load app services to avoid importing unrelated infrastructure deps.

    Importing a submodule such as ``src.application.services.knowledge_service`` must
    not require optional dependencies from collection/trading services.
    """

    if name == "CollectionAppService":
        from src.application.services.collection_app_service import CollectionAppService

        return CollectionAppService
    if name == "KnowledgeNewsIngestionService":
        from src.application.services.knowledge_news_ingestion_service import (
            KnowledgeNewsIngestionService,
        )

        return KnowledgeNewsIngestionService
    if name == "FtNewsKnowledgeGraphWorkflowService":
        from src.application.services.ft_news_knowledge_graph_workflow_service import (
            FtNewsKnowledgeGraphWorkflowService,
        )

        return FtNewsKnowledgeGraphWorkflowService
    if name == "CommunityInsightService":
        from src.application.services.community_insight_service import CommunityInsightService

        return CommunityInsightService
    if name == "RelationDiscoveryService":
        from src.application.services.relation_discovery_service import RelationDiscoveryService

        return RelationDiscoveryService
    raise AttributeError(name)
