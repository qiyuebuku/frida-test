"""正式 Card Relation Edge 的持久化、语义投影和图变化发布。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Awaitable, Callable

from src.domain.knowledge.card_relation import CardRelationEdge, build_card_relation_edge
from src.domain.knowledge.relation_discovery import VerifiedRelationDecision
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
    SEMANTIC_COLLECTION_CARD_RELATION,
    SemanticVectorDocument,
)
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.card_relation_repository import (
    CardRelationRepository,
    CardRelationSyncResult,
)
from src.infrastructure.persistence.repositories.card_fact_repository import (
    CardFactProjectionResult,
    CardFactRepository,
)
from src.infrastructure.tasks.jettask_dispatcher import send_kg_graph_changed
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
)


GraphEventPublisher = Callable[..., Awaitable[list[str]]]


class CardRelationWriteService:
    """把核验决定推进到 PG Edge、Milvus 和图变化事件。"""

    def __init__(
        self,
        *,
        knowledge_repository: KnowledgeRepository,
        relation_repository: CardRelationRepository | Any | None = None,
        card_fact_repository: CardFactRepository | Any | None = None,
        semantic_retriever: MilvusSemanticHybridRetriever | Any | None = None,
        relation_candidate_store: MilvusRelationCandidateStore | Any | None = None,
        graph_event_publisher: GraphEventPublisher = send_kg_graph_changed,
        workflow_id: str = "",
    ) -> None:
        self._knowledge_repository = knowledge_repository
        self._relation_repository = relation_repository
        self._card_fact_repository = card_fact_repository
        if semantic_retriever is None and relation_candidate_store is None:
            self._semantic_retriever = MilvusSemanticHybridRetriever()
            self._relation_candidate_store = MilvusRelationCandidateStore(
                store=self._semantic_retriever.store
            )
        elif semantic_retriever is None:
            self._relation_candidate_store = relation_candidate_store
            shared_store = getattr(relation_candidate_store, "store", None)
            self._semantic_retriever = (
                MilvusSemanticHybridRetriever(store=shared_store)
                if shared_store is not None
                else MilvusSemanticHybridRetriever()
            )
        elif relation_candidate_store is None:
            self._semantic_retriever = semantic_retriever
            shared_store = getattr(semantic_retriever, "store", None)
            self._relation_candidate_store = (
                MilvusRelationCandidateStore(store=shared_store)
                if shared_store is not None
                else MilvusRelationCandidateStore()
            )
        else:
            self._semantic_retriever = semantic_retriever
            self._relation_candidate_store = relation_candidate_store
        self._graph_event_publisher = graph_event_publisher
        self._workflow_id = str(workflow_id or "").strip()

    async def persist_verified_decisions(
        self,
        decisions: list[VerifiedRelationDecision],
        *,
        adapter_name: str,
        target: str,
        pipeline_version: str,
        model_name: str,
        prompt_version: str,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        positives = [
            decision
            for decision in decisions
            if decision.decision_class in {"observed", "inferred"}
        ]
        rejected_pairs = [
            (decision.source_card_id, decision.target_card_id)
            for decision in decisions
            if decision.decision_class == "no_relation"
        ]
        await self._validate_positive_decisions(positives, adapter_name=adapter_name)
        edges = [
            build_card_relation_edge(
                decision,
                pipeline_version=pipeline_version,
                model_name=model_name,
                prompt_version=prompt_version,
            )
            for decision in positives
        ]
        repository = self._repository(target)
        with langfuse_observation(
            name="kg.relation.edge.pg_upsert",
            as_type="span",
            input={"positive_decisions": len(edges), "rejected_pairs": len(rejected_pairs)},
        ):
            sync = await asyncio.to_thread(
                repository.synchronize_batch,
                accepted_edges=edges,
                rejected_pairs=rejected_pairs,
            )
            langfuse_update_span(
                output={
                    "touched_edge_ids": sync.touched_edge_ids,
                    "changed_edge_ids": sync.changed_edge_ids,
                    "active_to_publish": len(sync.active_edges_to_publish),
                    "inactive_to_delete": len(sync.inactive_edge_ids_to_delete),
                },
                status_message="completed",
            )
        return await self._reconcile(
            sync,
            repository=repository,
            adapter_name=adapter_name,
            target=target,
            workflow_id=str(workflow_id or "").strip() or self._workflow_id,
        )

    async def invalidate_cards(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
        target: str,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        repository = self._repository(target)
        sync = await asyncio.to_thread(repository.invalidate_cards, card_ids)
        return await self._reconcile(
            sync,
            repository=repository,
            adapter_name=adapter_name,
            target=target,
            workflow_id=str(workflow_id or "").strip() or self._workflow_id,
        )

    async def refresh_fact_projection(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
        target: str,
    ) -> CardFactProjectionResult:
        """Rebuild fact identities and synchronize both Card Milvus views."""

        projection = await asyncio.to_thread(
            self._fact_repository(target).refresh_affected,
            adapter_name=adapter_name,
            seed_card_ids=card_ids,
        )
        if projection.affected_card_ids:
            await self._sync_fact_card_documents(
                projection,
                adapter_name=adapter_name,
                target=target,
            )
        return projection

    async def _validate_positive_decisions(
        self,
        decisions: list[VerifiedRelationDecision],
        *,
        adapter_name: str,
    ) -> None:
        card_ids = sorted(
            {
                card_id
                for decision in decisions
                for card_id in (decision.source_card_id, decision.target_card_id)
            }
        )
        if not card_ids:
            return
        manifests = self._knowledge_repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=card_ids,
            status="active",
        )
        manifest_by_id = {item.cognitive_card_id: item for item in manifests}
        missing = sorted(set(card_ids) - set(manifest_by_id))
        if missing:
            raise ValueError(f"正式 Edge 端点 Card 不存在或已失效: {missing}")
        for decision in decisions:
            source_refs = set(manifest_by_id[decision.source_card_id].focus_evidence_refs)
            target_refs = set(manifest_by_id[decision.target_card_id].focus_evidence_refs)
            if not set(decision.source_evidence_refs).issubset(source_refs):
                raise ValueError(f"Edge source refs 非法: {decision.source_card_id}")
            if not set(decision.target_evidence_refs).issubset(target_refs):
                raise ValueError(f"Edge target refs 非法: {decision.target_card_id}")

    async def _reconcile(
        self,
        sync: CardRelationSyncResult,
        *,
        repository: CardRelationRepository | Any,
        adapter_name: str,
        target: str,
        workflow_id: str,
    ) -> dict[str, Any]:
        fact_projection = (
            await asyncio.to_thread(
                self._fact_repository(target).refresh_affected,
                adapter_name=adapter_name,
                seed_card_ids=sync.affected_card_ids,
            )
            if sync.affected_card_ids
            else CardFactProjectionResult((), (), (), {})
        )
        if fact_projection.affected_card_ids:
            await self._sync_fact_card_documents(
                fact_projection,
                adapter_name=adapter_name,
                target=target,
            )
        published_ids: list[str] = []
        deleted_ids = list(sync.inactive_edge_ids_to_delete)
        with langfuse_observation(
            name="kg.relation.edge.milvus_sync",
            as_type="span",
            input={
                "active_edge_ids": [edge.id for edge in sync.active_edges_to_publish],
                "inactive_edge_ids": deleted_ids,
            },
        ):
            if sync.active_edges_to_publish:
                with langfuse_observation(
                    name="kg.relation.edge.materialize",
                    as_type="span",
                    input={"edge_ids": [edge.id for edge in sync.active_edges_to_publish]},
                ):
                    documents = await self._semantic_documents(
                        sync.active_edges_to_publish,
                        adapter_name=adapter_name,
                        target=target,
                    )
                    langfuse_update_span(
                        output={"documents": len(documents)},
                        status_message="completed",
                    )
                with langfuse_observation(
                    name="kg.relation.edge.milvus_upsert",
                    as_type="span",
                    input={"edge_ids": [edge.id for edge in sync.active_edges_to_publish]},
                ):
                    await self._semantic_retriever.upsert_semantic_documents(
                        adapter_name=adapter_name,
                        target=target,
                        documents=documents,
                        kg_version=sync.active_edges_to_publish[0].pipeline_version,
                    )
                    langfuse_update_span(
                        output={"documents": len(documents)},
                        status_message="completed",
                    )
                published_ids = [edge.id for edge in sync.active_edges_to_publish]
            if deleted_ids:
                await self._semantic_retriever.delete_documents_by_role(
                    collection_role=SEMANTIC_COLLECTION_CARD_RELATION,
                    adapter_name=adapter_name,
                    target=target,
                    target_ids=deleted_ids,
                )
            semantic_ids = sorted(set(published_ids) | set(deleted_ids))
            if semantic_ids:
                await asyncio.to_thread(repository.mark_semantic_synced, semantic_ids)
            langfuse_update_span(
                output={"published_edge_ids": published_ids, "deleted_edge_ids": deleted_ids},
                status_message="completed",
            )

        pending = await asyncio.to_thread(
            repository.list_pending_graph_events,
            sync.touched_edge_ids,
        )
        event_ids: list[str] = []
        if pending or fact_projection.changed_card_ids:
            changed_edge_ids = sorted(
                row.id for row in pending
            ) or sorted(sync.touched_edge_ids)
            affected_card_ids = sorted(
                {
                    card_id
                    for row in pending
                    for card_id in (row.source_card_id, row.target_card_id)
                }
                | set(fact_projection.affected_card_ids)
            )
            changes = {
                "upserted_edge_ids": sorted(row.id for row in pending if row.status == "active"),
                "invalidated_edge_ids": sorted(row.id for row in pending if row.status == "inactive"),
                "fact_id_changed_card_ids": list(
                    fact_projection.changed_card_ids
                ),
            }
            event_identity = _graph_event_identity(
                pending,
                fact_projection=fact_projection,
            )
            with langfuse_observation(
                name="kg.relation.graph_change.publish",
                as_type="span",
                input={
                    "event_identity": event_identity,
                    "changed_edge_ids": changed_edge_ids,
                    "affected_card_ids": affected_card_ids,
                    "changes": changes,
                },
            ):
                publish_kwargs = {
                    "adapter_name": adapter_name,
                    "changed_edge_ids": changed_edge_ids,
                    "affected_card_ids": affected_card_ids,
                    "changes": changes,
                    "event_identity": event_identity,
                }
                if workflow_id:
                    publish_kwargs["workflow_id"] = workflow_id
                event_ids = await self._graph_event_publisher(
                    **publish_kwargs,
                )
                if pending:
                    await asyncio.to_thread(
                        repository.mark_graph_events_published,
                        [row.id for row in pending],
                    )
                langfuse_update_span(
                    output={"jettask_event_ids": event_ids},
                    status_message="completed",
                )
        return {
            "touched_edge_ids": sync.touched_edge_ids,
            "changed_edge_ids": sync.changed_edge_ids,
            "milvus_upserted_edge_ids": published_ids,
            "milvus_deleted_edge_ids": deleted_ids,
            "graph_event_ids": event_ids,
            "affected_card_ids": sorted(
                set(sync.affected_card_ids)
                | set(fact_projection.affected_card_ids)
            ),
            "card_fact_ids": list(fact_projection.fact_ids),
            "fact_id_changed_card_ids": list(
                fact_projection.changed_card_ids
            ),
            "workflow_id": workflow_id,
        }

    def _fact_repository(self, target: str) -> CardFactRepository | Any:
        if self._card_fact_repository is not None:
            return self._card_fact_repository
        return CardFactRepository(target=target)

    async def _semantic_documents(
        self,
        edges: list[CardRelationEdge],
        *,
        adapter_name: str,
        target: str,
    ) -> list[SemanticVectorDocument]:
        card_ids = sorted(
            {
                card_id
                for edge in edges
                for card_id in (edge.source_card_id, edge.target_card_id)
            }
        )
        summaries = await self._relation_candidate_store.get_summaries(
            card_ids,
            adapter_name=adapter_name,
            target=target,
        )
        manifests = self._knowledge_repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=card_ids,
            status="active",
        )
        manifest_by_id = {item.cognitive_card_id: item for item in manifests}
        missing = sorted(set(card_ids) - set(summaries))
        if missing:
            raise RuntimeError(f"Edge Milvus 投影缺少 Card Summary: {missing}")
        documents: list[SemanticVectorDocument] = []
        for edge in edges:
            source_summary = summaries[edge.source_card_id].text
            target_summary = summaries[edge.target_card_id].text
            source_manifest = manifest_by_id[edge.source_card_id]
            target_manifest = manifest_by_id[edge.target_card_id]
            evidence_ids = list(
                dict.fromkeys((source_manifest.evidence_id, target_manifest.evidence_id))
            )
            chunk_ids = list(
                dict.fromkeys(
                    [*source_manifest.chunk_ids, *target_manifest.chunk_ids]
                )
            )
            text = _edge_semantic_text(
                edge,
                source_summary=source_summary,
                target_summary=target_summary,
            )
            documents.append(
                SemanticVectorDocument(
                    document_id=edge.id,
                    document_type="card_relation_edge",
                    collection_role=SEMANTIC_COLLECTION_CARD_RELATION,
                    source_type="kg_card_relation",
                    source_id=edge.id,
                    evidence_id=evidence_ids[0] if evidence_ids else "",
                    text=text,
                    metadata={
                        "target_type": "card_relation_edge",
                        "edge_id": edge.id,
                        "source_card_id": edge.source_card_id,
                        "target_card_id": edge.target_card_id,
                        "relation_kind": edge.relation_kind,
                        "relation_type": edge.relation_type,
                        "decision_class": edge.decision_class,
                        "confidence": edge.confidence,
                        "evidence_ids": evidence_ids,
                        "chunk_ids": chunk_ids,
                        "cited_evidence_ids": evidence_ids,
                        "cited_chunk_ids": chunk_ids,
                        "edge_ids": [edge.id],
                        "content_version": edge.content_version,
                    },
                )
            )
        return documents

    async def _sync_fact_card_documents(
        self,
        projection: CardFactProjectionResult,
        *,
        adapter_name: str,
        target: str,
    ) -> None:
        affected_card_ids = list(projection.affected_card_ids)
        summaries, focus_evidence = await asyncio.gather(
            self._relation_candidate_store.get_summaries(
                affected_card_ids,
                adapter_name=adapter_name,
                target=target,
            ),
            self._relation_candidate_store.get_focus_evidence(
                affected_card_ids,
                adapter_name=adapter_name,
                target=target,
            ),
        )
        card_ids = [
            card_id
            for card_id in affected_card_ids
            if card_id in projection.fact_by_card_id
            and (
                card_id not in summaries
                or card_id not in focus_evidence
                or "event_id" in summaries[card_id].metadata
                or "event_id" in focus_evidence[card_id].metadata
                or str(
                    summaries[card_id].metadata.get("fact_id") or ""
                )
                != projection.fact_by_card_id[card_id]
                or str(
                    focus_evidence[card_id].metadata.get("fact_id") or ""
                )
                != projection.fact_by_card_id[card_id]
            )
        ]
        if not card_ids:
            return
        manifests = self._knowledge_repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=card_ids,
            status="active",
        )
        manifest_by_id = {
            item.cognitive_card_id: item
            for item in manifests
        }
        missing = sorted(
            card_id
            for card_id in card_ids
            if card_id not in summaries
            or card_id not in focus_evidence
            or card_id not in manifest_by_id
        )
        if missing:
            raise RuntimeError(
                f"fact_id 同步缺少 Card 双视图或 manifest: {missing}"
            )

        documents: list[SemanticVectorDocument] = []
        for card_id in card_ids:
            manifest = manifest_by_id[card_id]
            fact_id = projection.fact_by_card_id[card_id]
            summary = summaries[card_id]
            focus = focus_evidence[card_id]
            summary_metadata = {
                key: value
                for key, value in summary.metadata.items()
                if key != "event_id"
            }
            focus_metadata = {
                key: value
                for key, value in focus.metadata.items()
                if key != "event_id"
            }
            common_metadata = {
                **summary_metadata,
                "target_id": card_id,
                "cognitive_card_id": card_id,
                "fact_id": fact_id,
                "evidence_id": manifest.evidence_id,
                "primary_chunk_id": manifest.primary_chunk_id,
                "cited_chunk_ids": list(manifest.chunk_ids),
                "cited_evidence_ids": [manifest.evidence_id],
                "status": manifest.status,
            }
            documents.extend(
                [
                    SemanticVectorDocument(
                        document_id=card_id,
                        document_type="atomic_cognitive_card_summary",
                        collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD,
                        source_type="kg_cognitive_card",
                        source_id=card_id,
                        evidence_id=manifest.evidence_id,
                        text=summary.text,
                        metadata={
                            **common_metadata,
                            "target_type": "atomic_cognitive_card_summary",
                        },
                    ),
                    SemanticVectorDocument(
                        document_id=card_id,
                        document_type="atomic_cognitive_card_focus_evidence",
                        collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
                        source_type="kg_cognitive_card_focus_evidence",
                        source_id=card_id,
                        evidence_id=manifest.evidence_id,
                        text=focus.text,
                        metadata={
                            **focus_metadata,
                            **common_metadata,
                            "target_type": "atomic_cognitive_card_focus_evidence",
                        },
                    ),
                ]
            )
        with langfuse_observation(
            name="kg.card_fact.milvus_sync",
            as_type="span",
            input={
                "changed_card_ids": card_ids,
                "fact_ids": list(projection.fact_ids),
            },
        ):
            await self._semantic_retriever.upsert_semantic_documents(
                adapter_name=adapter_name,
                target=target,
                documents=documents,
                kg_version="card_fact_projection_v1",
            )
            langfuse_update_span(
                output={"documents": len(documents)},
                status_message="completed",
            )

    def _repository(self, target: str) -> CardRelationRepository | Any:
        if self._relation_repository is not None:
            return self._relation_repository
        return CardRelationRepository(target=target)  # type: ignore[arg-type]


def _edge_semantic_text(
    edge: CardRelationEdge,
    *,
    source_summary: str,
    target_summary: str,
) -> str:
    parts = [
        "关系 Edge",
        f"起点事实：{source_summary}",
        f"终点事实：{target_summary}",
        f"关系类别：{edge.relation_kind}",
        f"关系说明：{edge.relation_type}",
        f"方向：{edge.direction}",
        f"证据等级：{edge.decision_class}",
        f"成立依据：{edge.basis}",
    ]
    if edge.inference_mechanism:
        parts.append(f"推导机制：{edge.inference_mechanism}")
    return "\n".join(parts)


def _graph_event_identity(
    rows: list[Any],
    *,
    fact_projection: CardFactProjectionResult | None = None,
) -> str:
    payload = {
        "edges": sorted(
            (str(row.id), str(row.content_version))
            for row in rows
        ),
        "fact_projection": sorted(
            (card_id, fact_id)
            for card_id, fact_id in (
                (fact_projection.fact_by_card_id if fact_projection else {})
            ).items()
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "kg_graph_change:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
