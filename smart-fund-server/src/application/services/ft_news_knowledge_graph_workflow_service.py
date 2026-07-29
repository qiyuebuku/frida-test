"""从 ft_news 同步执行到正式 Card Relation Edge 的验证工作流。"""

from __future__ import annotations

from typing import Any

from src.application.dto.knowledge_dto import Target
from src.application.services.knowledge_news_ingestion_service import (
    KnowledgeNewsIngestionService,
)
from src.application.services.relation_discovery_service import RelationDiscoveryService
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    KnowledgeRepositoryImpl,
)


class FtNewsKnowledgeGraphWorkflowService:
    """复用生产服务，同步完成新闻编译和关系发现。"""

    def __init__(
        self,
        *,
        target: Target = "prod",
        ingestion_service: KnowledgeNewsIngestionService | Any | None = None,
        relation_discovery_service: RelationDiscoveryService | Any | None = None,
    ) -> None:
        self._target = target
        self._ingestion_service = ingestion_service or KnowledgeNewsIngestionService(
            target=target
        )
        self._relation_discovery_service = (
            relation_discovery_service
            or RelationDiscoveryService(
                repository=KnowledgeRepositoryImpl(target=target),
            )
        )

    async def run(
        self,
        news_ids: list[int],
        *,
        include_evaluation_details: bool = False,
    ) -> dict[str, Any]:
        unique_news_ids = _ordered_unique_positive_ints(news_ids)
        with langfuse_observation(
            name="kg.ft_news_card_relation_workflow.execute",
            as_type="chain",
            input={
                "news_ids": unique_news_ids,
                "target": self._target,
                "include_evaluation_details": include_evaluation_details,
            },
        ):
            ingestion = await self._ingestion_service.compile_ft_news_ids(
                unique_news_ids
            )
            card_ids = [
                str(item)
                for item in ingestion.get("relation_card_ids") or []
                if str(item).strip()
            ]
            relation_discovery = await self._relation_discovery_service.discover_card_relations(
                card_ids,
                adapter_name="financial",
                target=self._target,
                include_evaluation_details=include_evaluation_details,
                persist_edges=True,
            )
            cross_chunk_persistence = relation_discovery.get("edge_persistence") or {}
            relation_statistics = _relation_statistics(
                ingestion=ingestion,
                relation_discovery=relation_discovery,
            )
            edge_persistence = {
                "changed_edge_ids": _ordered_unique_strings(
                    [
                        *(ingestion.get("intra_chunk_changed_edge_ids") or []),
                        *(cross_chunk_persistence.get("changed_edge_ids") or []),
                    ]
                ),
                "graph_event_ids": _ordered_unique_strings(
                    [
                        *(ingestion.get("intra_chunk_graph_event_ids") or []),
                        *(cross_chunk_persistence.get("graph_event_ids") or []),
                    ]
                ),
                "intra_chunk_changed_edge_ids": list(
                    ingestion.get("intra_chunk_changed_edge_ids") or []
                ),
                "cross_chunk_changed_edge_ids": list(
                    cross_chunk_persistence.get("changed_edge_ids") or []
                ),
            }
            result = {
                "status": "completed",
                "target": self._target,
                "news_ids": unique_news_ids,
                "ingestion": ingestion,
                "relation_discovery": relation_discovery,
                "relation_statistics": relation_statistics,
                "edge_persistence": edge_persistence,
            }
            langfuse_update_span(
                output={
                    "status": result["status"],
                    "news_count": len(unique_news_ids),
                    "card_count": len(card_ids),
                    "observed": relation_statistics["total"]["observed"],
                    "inferred": relation_statistics["total"]["inferred"],
                    "positive_relations": relation_statistics["total"]["positive_relations"],
                    "cross_chunk_no_relation": relation_statistics["cross_chunk"]["no_relation"],
                    "relation_statistics": relation_statistics,
                    "edge_persistence": edge_persistence,
                },
                status_message="completed",
            )
            return result


def _ordered_unique_positive_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _relation_statistics(
    *,
    ingestion: dict[str, Any],
    relation_discovery: dict[str, Any],
) -> dict[str, dict[str, int]]:
    intra_observed = int(ingestion.get("intra_chunk_observed") or 0)
    intra_inferred = int(ingestion.get("intra_chunk_inferred") or 0)
    intra_positive = int(ingestion.get("intra_chunk_relations") or 0)
    cross_observed = int(relation_discovery.get("observed") or 0)
    cross_inferred = int(relation_discovery.get("inferred") or 0)
    cross_no_relation = int(relation_discovery.get("no_relation") or 0)
    return {
        "intra_chunk": {
            "observed": intra_observed,
            "inferred": intra_inferred,
            "positive_relations": intra_positive,
        },
        "cross_chunk": {
            "observed": cross_observed,
            "inferred": cross_inferred,
            "no_relation": cross_no_relation,
            "positive_relations": cross_observed + cross_inferred,
        },
        "total": {
            "observed": intra_observed + cross_observed,
            "inferred": intra_inferred + cross_inferred,
            "positive_relations": intra_positive + cross_observed + cross_inferred,
        },
    }


def _ordered_unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item).strip()))
