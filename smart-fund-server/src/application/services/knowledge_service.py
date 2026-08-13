"""Application service entry point for knowledge use cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from src.application.dto.knowledge_dto import (
    KnowledgeBadCaseReplayCommand,
    KnowledgeBadCaseReplayResultDTO,
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeBootstrapStocksCommand,
    KnowledgeCompileCommand,
    KnowledgeCompileResultDTO,
    KnowledgeHealthDTO,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeIncrementalRefreshResultDTO,
    KnowledgeIncrementalRefreshTaskDTO,
    KnowledgeQualityScanCommand,
    KnowledgeQualityScanResultDTO,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildIndexesResultDTO,
    KnowledgeResearchContextCommand,
    KnowledgeResearchContextDTO,
    KnowledgeReviewActionCommand,
    KnowledgeSourceProjectionCommand,
    KnowledgeSourceProjectionResultDTO,
    dto_to_dict,
)
from src.application.services.financial_stock_bootstrap import (
    build_stock_basics_records_from_sources,
)
from src.application.services.financial_news_projection import (
    build_news_records_from_sources,
)
from src.application.services.financial_normalization_audit import (
    audit_financial_normalization_rules,
    plan_financial_normalization_migration,
)
from src.application.services.knowledge_adapter_registry import get_adapter, list_adapters
from src.application.services.knowledge_source_projection_service import (
    KnowledgeSourceProjectionService,
)
from src.application.services.graph_index_reporter import GraphIndexLLMReporter
from src.application.services.graph_index_profiles import FINANCIAL_GRAPH_PROJECTIONS, GRAPH_INDEX_PUBLIC_LENS_ALIASES
from src.application.services.atomic_cognitive_card_service import AtomicCognitiveCardStageService
from src.application.services.card_relation_write_service import CardRelationWriteService
from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge.chunking import build_chunks_for_compiled_evidence
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.enums import EvidenceStatus
from src.domain.knowledge.graph_index import (
    GraphIndexBuildResult,
    GraphIndexCommunity,
    GraphIndexDirtyRefs,
    GraphIndexRefreshPlan,
    GraphIndexUnassignedSignal,
    GraphIndexVectorDocument,
    expand_community_scope,
    build_graph_index,
    build_graph_index_documents,
    build_rolling_delta_index,
    plan_graph_index_refresh,
    resolve_graph_index_lineage,
)
from src.domain.knowledge.cognitive_index import _community_document as _cognitive_community_document
from src.domain.knowledge.cognitive_index import seed_graph_communities
from src.domain.knowledge.quality import (
    BadCaseReplay,
    KnowledgeQualityScanner,
    QualityReport,
    ReviewAction,
    replay_bad_case,
)
from src.domain.knowledge.retrieval import (
    AnswerContext,
    HybridRetrievalRuntime,
    RetrievalOptions,
    RetrievalTrace,
)
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_router import (
    RetrievalQualityMetrics,
    apply_post_check,
    fast_route,
)
from src.domain.knowledge.retrieval_plan_executor import RetrievalPlanExecutor
from src.domain.knowledge.retrieval_eval import (
    RetrievalBadCase,
    RetrievalTraceSnapshot,
    evaluate_retrieval_bad_case,
)
from src.domain.knowledge.retrieval_trace_replay import replay_retrieval_trace
from src.domain.knowledge.retrieval_tools import RetrievalToolRegistry
from src.domain.knowledge.repositories import (
    KnowledgeRepository,
    KnowledgeSourceProjectionRepository,
)
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COMMUNITY,
    SemanticVectorDocument,
)
from src.domain.knowledge.schemas import (
    CompileResult,
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    EvidenceChunk,
    FailedRecord,
    KnowledgeInput,
)
from src.domain.knowledge_adapters.financial.consumption import can_hard_consume
from src.domain.knowledge_adapters.financial.query_planner import FinancialQueryPlanner
from src.infrastructure.config import settings
from src.infrastructure.clients.reranker import RerankerClient
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
)

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Coordinates knowledge use cases without owning persistence details."""

    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        target: Target | None = None,
        source_projection_repository: KnowledgeSourceProjectionRepository | None = None,
    ):
        self.repository = repository
        self.target = target or "prod"
        self.source_projection_service = (
            KnowledgeSourceProjectionService(source_projection_repository)
            if source_projection_repository is not None
            else None
        )
        self.compiler = KnowledgeCompiler(
            repository=repository,
            concurrency=settings.CLAUDE_PROXY_MAX_CONCURRENCY,
            pre_extraction_chunk_materializer=(
                self._materialize_pre_extraction_chunks if repository is not None else None
            ),
        )
        self.quality_scanner = KnowledgeQualityScanner()

    async def health(self) -> KnowledgeHealthDTO:
        database = "not_configured"
        status = "degraded"
        if self.repository is not None:
            try:
                self.repository.ping()
                database = "ok"
                status = "ok"
            except Exception as exc:
                database = f"error: {exc}"
                status = "degraded"
        return KnowledgeHealthDTO(
            status=status,
            database=database,
            adapters=list_adapters(),
            implemented=[
                "health",
                "compile",
                "rebuild_indexes",
                "research_context",
                "incremental_refresh",
                "source_projection",
                "quality_scan",
                "reviews",
                "relation_graph_agent_tools",
            ],
        )

    async def project_sources(
        self,
        command: KnowledgeSourceProjectionCommand,
    ) -> KnowledgeSourceProjectionResultDTO:
        if self.source_projection_service is None:
            raise RuntimeError("Knowledge source projection repository is required for this use case")
        return self.source_projection_service.project(command)

    async def bootstrap_seed_communities(self, *, adapter_name: str) -> dict[str, Any]:
        repository = self._require_repository()
        return await _ensure_seed_communities(
            repository=repository,
            adapter_name=adapter_name,
            target=self.target,
            kg_version="seed_bootstrap",
        )

    async def compile_kg(self, command: KnowledgeCompileCommand) -> KnowledgeCompileResultDTO:
        metadata = _knowledge_command_metadata(command)
        with langfuse_propagation_context(
            trace_name=f"kg.compile:{command.adapter_name}",
            tags=["kg", "write-path", "compile"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.compile_kg",
                as_type="chain",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await self._compile_kg_impl(command)
                    langfuse_update_span(
                        output={
                            "run_id": result.run_id,
                            "nodes": result.nodes,
                            "edges": result.edges,
                            "evidence": result.evidence,
                            "failed_records": result.failed_records,
                            "dry_run": result.dry_run,
                            "index_refresh": result.index_refresh,
                        },
                        metadata={"status": "completed"},
                        status_message="completed",
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
                finally:
                    langfuse_flush()

    async def _compile_kg_impl(self, command: KnowledgeCompileCommand) -> KnowledgeCompileResultDTO:
        adapter = get_adapter(command.adapter_name, target=command.target)
        inputs, normalize_failures = _normalize_records(adapter, command.records)
        pre_extraction_materializer = None
        if not command.dry_run and self.repository is not None:
            async def pre_extraction_materializer(
                adapter_name: str,
                version: str,
                evidence: list[CompiledEvidence],
            ) -> None:
                await self._materialize_pre_extraction_chunks(
                    adapter_name,
                    version,
                    evidence,
                    target=command.target,
                )

        compiler = KnowledgeCompiler(
            repository=None if command.dry_run else self.repository,
            concurrency=_compile_concurrency(command),
            pre_extraction_chunk_materializer=pre_extraction_materializer,
        )
        result = await compiler.compile(adapter, inputs)
        result.failed_records[:0] = normalize_failures
        index_refresh = (
            await self._refresh_incremental_indexes(
                result,
                command.target,
                workflow_id=command.request_id or "",
            )
            if self.repository is not None and not command.dry_run
            else {}
        )
        return KnowledgeCompileResultDTO(
            adapter_name=result.adapter_name,
            run_id=result.run_id,
            nodes=len(result.nodes),
            edges=len(result.edges),
            evidence=len(result.evidence),
            failed_records=len(result.failed_records),
            node_ids=[node.node_id for node in result.nodes],
            edge_ids=[edge.edge_id for edge in result.edges],
            evidence_ids=[item.evidence_id for item in result.evidence],
            index_refresh=index_refresh,
            warnings=[dto_to_dict(item) for item in result.warnings],
            failures=[dto_to_dict(item) for item in result.failed_records],
            dry_run=command.dry_run,
        )

    async def bootstrap_financial_stock_entities(
        self,
        command: KnowledgeBootstrapStocksCommand,
    ) -> KnowledgeCompileResultDTO:
        records = build_stock_basics_records_from_sources(
            target=command.target,
            codes=command.codes or None,
            limit=command.limit,
        )
        if not records:
            raise ValueError("no stock_basics records found from business source tables")
        return await self.compile_kg(
            KnowledgeCompileCommand(
                adapter_name="financial",
                records=records,
                target=command.target,
                dry_run=command.dry_run,
                request_id=command.request_id,
                concurrency=1,
            )
        )

    async def bootstrap_financial_stock_news(
        self,
        command: KnowledgeBootstrapStockNewsCommand,
    ) -> KnowledgeCompileResultDTO:
        if self.source_projection_service is not None:
            projection = self.source_projection_service.project(
                KnowledgeSourceProjectionCommand(
                    target=command.target,
                    sources=["ft_news"],
                    codes=command.codes,
                    limit=command.limit,
                )
            )
            records = projection.records
        else:
            records = build_news_records_from_sources(
                target=command.target,
                codes=command.codes or None,
                limit=command.limit,
            )
        if not records:
            raise ValueError("no news records found from business source tables")
        return await self.compile_kg(
            KnowledgeCompileCommand(
                adapter_name="financial",
                records=records,
                target=command.target,
                dry_run=command.dry_run,
                request_id=command.request_id,
                concurrency=command.concurrency,
            )
        )

    async def refresh_financial_incremental(
        self,
        command: KnowledgeIncrementalRefreshCommand,
    ) -> KnowledgeIncrementalRefreshResultDTO:
        run_id = f"kg_run:financial_incremental:{uuid4()}"
        steps: list[dict[str, Any]] = []

        stock_result = await self.bootstrap_financial_stock_entities(
            KnowledgeBootstrapStocksCommand(
                target=command.target,
                codes=command.codes,
                limit=command.stock_limit,
                dry_run=command.dry_run,
                request_id=command.request_id,
            )
        )
        steps.append(_incremental_step("bootstrap_stocks", stock_result))

        news_result = await self.bootstrap_financial_stock_news(
            KnowledgeBootstrapStockNewsCommand(
                target=command.target,
                codes=command.codes,
                limit=command.news_limit,
                dry_run=command.dry_run,
                request_id=command.request_id,
                concurrency=command.concurrency,
            )
        )
        steps.append(_incremental_step("bootstrap_stock_news", news_result))

        if command.dry_run:
            steps.append({"name": "rebuild_indexes", "status": "skipped", "reason": "dry_run"})
            return KnowledgeIncrementalRefreshResultDTO(
                adapter_name="financial",
                target=command.target,
                run_id=run_id,
                dry_run=command.dry_run,
                steps=steps,
            )

        refresh_summaries = [
            item
            for item in [stock_result.index_refresh, news_result.index_refresh]
            if item
        ]
        if refresh_summaries:
            steps.append(
                {
                    "name": "incremental_indexes",
                    "status": "ok",
                    "result": _merge_index_refresh_summaries(refresh_summaries),
                }
            )
        else:
            steps.append({"name": "incremental_indexes", "status": "skipped", "reason": "no_changes"})

        if command.rebuild_indexes and not refresh_summaries:
            index_result = await self.rebuild_indexes_for(
                KnowledgeRebuildIndexesCommand(
                    adapter_name="financial",
                    target=command.target,
                    index_types=["graph_adjacency", "evidence_chunks", "hybrid_chunks"],
                )
            )
            steps.append(_incremental_step("rebuild_indexes", index_result))
        else:
            reason = "covered_by_incremental_refresh" if refresh_summaries else "disabled"
            steps.append({"name": "rebuild_indexes", "status": "skipped", "reason": reason})

        return KnowledgeIncrementalRefreshResultDTO(
            adapter_name="financial",
            target=command.target,
            run_id=run_id,
            dry_run=command.dry_run,
            steps=steps,
        )

    async def enqueue_financial_incremental_refresh_task(
        self,
        command: KnowledgeIncrementalRefreshCommand,
        *,
        max_retries: int = 1,
    ) -> KnowledgeIncrementalRefreshTaskDTO:
        repository = self._require_repository()
        run_id = f"kg_task:financial_incremental_refresh:{uuid4()}"
        metadata = _incremental_task_metadata(
            command=command,
            status="pending",
            attempt=0,
            max_retries=max(0, max_retries),
        )
        repository.create_compilation_run(
            {
                "run_id": run_id,
                "adapter_name": "financial",
                "adapter_version": "",
                "source_batch_id": "financial_incremental_refresh_task",
                "status": "pending",
                "input_count": 0,
                "metadata": metadata,
            }
        )
        return _incremental_task_dto(
            {
                "run_id": run_id,
                "adapter_name": "financial",
                "status": "pending",
                "metadata": metadata,
            }
        )

    async def run_financial_incremental_refresh_task(
        self,
        run_id: str,
    ) -> KnowledgeIncrementalRefreshTaskDTO:
        repository = self._require_repository()
        run = repository.get_compilation_run(run_id)
        if run is None:
            raise ValueError(f"incremental refresh task not found: {run_id}")
        metadata = dict(run.get("metadata") or {})
        command = _incremental_command_from_metadata(metadata)
        attempt = int(metadata.get("attempt") or 0) + 1
        max_retries = int(metadata.get("max_retries") or 0)
        running_metadata = {
            **metadata,
            "attempt": attempt,
            "status": "running",
            "error": None,
        }
        repository.create_compilation_run(
            {
                "run_id": run_id,
                "adapter_name": "financial",
                "adapter_version": "",
                "source_batch_id": "financial_incremental_refresh_task",
                "status": "running",
                "started_at": datetime.now(timezone.utc),
                "metadata": running_metadata,
            }
        )
        try:
            result = await self.refresh_financial_incremental(command)
        except Exception as exc:
            failed_metadata = {
                **running_metadata,
                "status": "failed",
                "error": str(exc),
                "retryable": attempt <= max_retries,
            }
            repository.finish_compilation_run(
                run_id,
                {
                    "status": "failed",
                    "failed_count": 1,
                    "metadata": failed_metadata,
                },
            )
            return _incremental_task_dto(
                {
                    **run,
                    "status": "failed",
                    "metadata": failed_metadata,
                }
            )

        result_dict = result.to_dict()
        success_metadata = {
            **running_metadata,
            "status": "success",
            "result": result_dict,
            "error": None,
            "retryable": False,
        }
        repository.finish_compilation_run(
            run_id,
            {
                "status": "success",
                "metadata": success_metadata,
                "node_count": _sum_step_metric(result_dict, "nodes"),
                "edge_count": _sum_step_metric(result_dict, "edges"),
                "evidence_count": _sum_step_metric(result_dict, "evidence"),
                "failed_count": _sum_step_metric(result_dict, "failed_records"),
            },
        )
        return _incremental_task_dto(
            {
                **run,
                "status": "success",
                "metadata": success_metadata,
            }
        )

    async def retry_financial_incremental_refresh_task(
        self,
        run_id: str,
    ) -> KnowledgeIncrementalRefreshTaskDTO:
        run = self._require_repository().get_compilation_run(run_id)
        if run is None:
            raise ValueError(f"incremental refresh task not found: {run_id}")
        if run.get("status") not in {"failed", "pending", "running"}:
            raise ValueError(f"incremental refresh task cannot be retried from status={run.get('status')}")
        return await self.run_financial_incremental_refresh_task(run_id)

    async def get_incremental_refresh_task(self, run_id: str) -> KnowledgeIncrementalRefreshTaskDTO:
        run = self._require_repository().get_compilation_run(run_id)
        if run is None:
            raise ValueError(f"incremental refresh task not found: {run_id}")
        return _incremental_task_dto(run)

    async def rebuild_indexes_for(
        self,
        command: KnowledgeRebuildIndexesCommand,
    ) -> KnowledgeRebuildIndexesResultDTO:
        metadata = _knowledge_command_metadata(command)
        with langfuse_propagation_context(
            trace_name=f"kg.rebuild_indexes:{command.adapter_name}",
            tags=["kg", "index", "rebuild"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.rebuild_indexes",
                as_type="chain",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await self._rebuild_indexes_for_impl(command)
                    langfuse_update_span(
                        output={
                            "run_id": result.run_id,
                            "graph_adjacency": result.graph_adjacency,
                            "evidence_chunks": result.evidence_chunks,
                            "hybrid_chunks": result.hybrid_chunks,
                            "graph_index": result.graph_index,
                            "warnings": result.warnings,
                        },
                        metadata={"status": "completed"},
                        status_message="completed",
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
                finally:
                    langfuse_flush()

    async def _rebuild_indexes_for_impl(
        self,
        command: KnowledgeRebuildIndexesCommand,
    ) -> KnowledgeRebuildIndexesResultDTO:
        _ensure_scope_supported(command.scope)
        get_adapter(command.adapter_name, target=command.target)
        repository = self._require_repository()
        allowed = {"graph_adjacency", "evidence_chunks", "hybrid_chunks", "vector_chunks", "graph_index"}
        unknown = sorted(set(command.index_types) - allowed)
        if unknown:
            raise ValueError(f"unsupported index_types: {', '.join(unknown)}")

        _cleanup_evidence_versions(repository, command.adapter_name)
        result = {"graph_adjacency": 0, "evidence_chunks": 0, "hybrid_chunks": 0}
        graph_index: dict[str, Any] = {}
        warnings: list[str] = []
        if "graph_adjacency" in command.index_types:
            # 旧版 kg_graph_adjacency 投影已随裸 node/edge 主链路一起移除。
            # 保留该索引类型作为运维命令兼容标识，但不再调用不存在的仓储方法；
            # 当前关系图的物化入口是下方独立的 graph_index。
            warnings.append(
                "graph_adjacency is retired; use graph_index for relation graph materialization"
            )
        if "evidence_chunks" in command.index_types:
            result["evidence_chunks"] = repository.rebuild_evidence_chunks(command.adapter_name)
        if {"hybrid_chunks", "vector_chunks"} & set(command.index_types):
            chunks = repository.list_evidence_chunks(command.adapter_name)
            result["hybrid_chunks"] = await _semantic_hybrid_retriever().rebuild_index(
                adapter_name=command.adapter_name,
                target=command.target,
                chunks=chunks,
                # 裸 node/edge 已退出语义检索主链路；关系语义文档由
                # Card Relation worker 独立维护，重建 Evidence 索引时不应读取
                # 已移除的旧仓储接口。
                nodes=[],
                edges=[],
            )
        if "graph_index" in command.index_types:
            try:
                graph_index = await _refresh_graph_index(
                    repository=repository,
                    adapter_name=command.adapter_name,
                    target=command.target,
                    force_rebuild_reason="manual_rebuild_indexes",
                    graph_index_scope=command.scope,
                )
            except Exception:
                repository.mark_graph_index_dirty(command.adapter_name, reason="manual_rebuild_failed")
                raise
        return KnowledgeRebuildIndexesResultDTO(
            adapter_name=command.adapter_name,
            run_id=f"kg_run:rebuild_indexes:{uuid4()}",
            graph_adjacency=result["graph_adjacency"],
            evidence_chunks=result["evidence_chunks"],
            hybrid_chunks=result["hybrid_chunks"],
            graph_index=graph_index,
            warnings=warnings,
        )

    async def cleanup_evidence_versions_for(self, adapter_name: str) -> dict[str, Any]:
        repository = self._require_repository()
        cleanup = _cleanup_evidence_versions(repository, adapter_name)
        cleanup["hybrid_vectors_deleted"] = await _delete_hybrid_evidence_vectors(
            adapter_name=adapter_name,
            target=self.target,
            evidence_ids=cleanup.get("evidence_ids") or [],
        )
        return cleanup

    async def build_research_context_for(
        self,
        command: KnowledgeResearchContextCommand,
    ) -> KnowledgeResearchContextDTO:
        metadata = _knowledge_command_metadata(command)
        with langfuse_propagation_context(
            trace_name=f"kg.research_context:{command.adapter_name}",
            tags=["kg", "retrieval", "research-context"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.build_research_context",
                as_type="chain",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await self._build_research_context_for_impl(command)
                    langfuse_update_span(
                        output={
                            "hits": len(result.hits),
                            "matched_nodes": len(result.matched_nodes),
                            "matched_edges": len(result.matched_edges),
                            "evidence_refs": len(result.evidence_refs),
                            "mode": result.mode,
                            "channels": result.retrieval_channels_enabled,
                        },
                        metadata={"status": "completed"},
                        status_message="completed",
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
                finally:
                    langfuse_flush()

    async def _build_research_context_for_impl(
        self,
        command: KnowledgeResearchContextCommand,
    ) -> KnowledgeResearchContextDTO:
        get_adapter(command.adapter_name, target=command.target)
        repository = self._require_repository()
        anchor = build_guarded_query_anchor(
            command.query,
            known_nodes=repository.list_nodes(command.adapter_name),
        )
        routing = fast_route(command.query, anchor, command.retrieval_mode)
        options = RetrievalOptions(
            adapter_name=command.adapter_name,
            target=command.target,
            graph_depth=max(1, min(command.graph_depth, 3)),
            graph_limit=command.graph_limit,
            wiki_limit=command.wiki_limit,
            evidence_limit=command.evidence_limit,
            keyword_limit=max(command.graph_limit, 1),
            semantic_hybrid_limit=command.evidence_limit,
            max_hits=max(command.graph_limit, command.wiki_limit, command.evidence_limit, 1),
            max_chars=command.max_chars,
        )
        retrieval_plan = (
            FinancialQueryPlanner().plan(command.query)
            if command.adapter_name == "financial"
            and routing.initial_mode == "deterministic_plan"
            else None
        )
        graph_time_window = _graph_time_window_for_plan(retrieval_plan)
        if graph_time_window is not None:
            options = options.model_copy(
                update={
                    "graph_time_start": graph_time_window[0],
                    "graph_time_end": graph_time_window[1],
                    "semantic_time_start": graph_time_window[0],
                    "semantic_time_end": graph_time_window[1],
                }
            )
        context = await self._build_research_answer_context(
            command.query,
            options,
            retrieval_plan=retrieval_plan,
            retrieval_mode=routing.initial_mode,
        )
        if context.trace.retrieval_metrics:
            routing = apply_post_check(
                routing,
                RetrievalQualityMetrics.model_validate(context.trace.retrieval_metrics),
                anchor,
            )
        context.trace.routing_decision.update(routing.model_dump(mode="json"))
        trace = dto_to_dict(context.trace)
        hard_score_edges = [
            edge
            for edge in context.matched_edges
            if command.adapter_name == "financial"
            and can_hard_consume(edge.relation_type, edge.confidence_label, edge.status)
        ]
        hard_edge_ids = {edge.edge_id for edge in hard_score_edges}
        explanation_edges = [
            edge for edge in context.matched_edges if edge.edge_id not in hard_edge_ids
        ]
        evidence_refs = _ordered_unique(
            evidence_id for hit in context.hits for evidence_id in hit.evidence_refs
        )
        _log_research_context_summary(command, context, evidence_refs)
        return KnowledgeResearchContextDTO(
            query=command.query,
            hits=[dto_to_dict(hit) for hit in context.hits],
            matched_nodes=[dto_to_dict(node) for node in context.matched_nodes],
            matched_edges=[dto_to_dict(edge) for edge in context.matched_edges],
            evidence_refs=evidence_refs,
            hard_score_edges=[dto_to_dict(edge) for edge in hard_score_edges],
            explanation_edges=[dto_to_dict(edge) for edge in explanation_edges],
            context_text=_context_text(context),
            budget_usage=dto_to_dict(context.budget_usage),
            mode=context.trace.mode,
            retrieval_channels_enabled=context.trace.channels_enabled,
            retrieval_channels_used=context.trace.channels_used,
            semantic_enabled=context.trace.semantic_enabled,
            milvus_enabled=context.trace.milvus_enabled,
            planner_enabled=context.trace.planner_enabled,
            retrieval_plan=(
                dto_to_dict(retrieval_plan)
                if retrieval_plan is not None and context.trace.planner_enabled
                else {}
            ),
            retrieval_trace=trace,
            query_anchor=trace.get("query_anchor", {}),
            routing_decision=trace.get("routing_decision", {}),
            warnings=list(context.trace.warnings),
        )

    async def quality_scan_for(
        self,
        command: KnowledgeQualityScanCommand,
    ) -> KnowledgeQualityScanResultDTO:
        get_adapter(command.adapter_name, target=getattr(command, "target", self.target))
        report = await self.quality_scan(
            command.adapter_name,
            persist_review=command.persist_review,
        )
        review_items = (
            len(self.quality_scanner.review_entries_for(report))
            if command.persist_review
            else 0
        )
        return KnowledgeQualityScanResultDTO(
            adapter_name=command.adapter_name,
            run_id=f"kg_run:quality_scan:{uuid4()}",
            ok=report.ok,
            metrics=dto_to_dict(report.metrics),
            issues=[dto_to_dict(item) for item in report.issues],
            review_items=review_items,
        )

    async def list_reviews_for(self, status: str | None = "open") -> dict[str, Any]:
        entries = await self.list_review_queue(status=status)
        return {"total": len(entries), "items": [dto_to_dict(entry) for entry in entries]}

    async def plan_normalization_migration_for(self, adapter_name: str) -> dict[str, Any]:
        get_adapter(adapter_name, target=self.target)
        if adapter_name != "financial":
            raise ValueError("normalization migration plan currently supports financial adapter only")
        repository = self._require_repository()
        from src.infrastructure.persistence.repositories.knowledge_normalization_rule_repository import (
            KnowledgeNormalizationRuleRepository,
        )

        rules = KnowledgeNormalizationRuleRepository(target=self.target).list_rules(adapter_name, status="active")
        return plan_financial_normalization_migration(
            adapter_name=adapter_name,
            rules=rules,
            nodes=repository.list_nodes(adapter_name),
            edges=repository.list_edges(adapter_name),
            evidence=repository.list_evidence(adapter_name),
        )

    async def replay_research_context_bad_cases(
        self,
        command: KnowledgeBadCaseReplayCommand,
    ) -> KnowledgeBadCaseReplayResultDTO:
        get_adapter(command.adapter_name, target=command.target)
        results: list[dict[str, Any]] = []
        for case in command.cases:
            if case.replay_trace:
                context, trace_mismatches = await self._replay_research_context_trace(
                    case=case,
                    command=command,
                )
            else:
                context = await self.build_research_context_for(
                    KnowledgeResearchContextCommand(
                        adapter_name=command.adapter_name,
                        target=command.target,
                        query=case.query,
                        retrieval_mode=case.retrieval_mode,
                        graph_depth=command.graph_depth,
                        graph_limit=command.graph_limit,
                        wiki_limit=command.wiki_limit,
                        evidence_limit=command.evidence_limit,
                        max_chars=command.max_chars,
                    )
                )
                trace_mismatches = []
            replay = evaluate_retrieval_bad_case(
                RetrievalBadCase(
                    case_id=case.case_id,
                    query=case.query,
                    expected_evidence_refs=case.expected_evidence_refs,
                    expected_hit_titles=case.expected_hit_titles,
                    expected_top_hit_titles=case.expected_top_hit_titles,
                    top_k=case.top_k,
                    expected_node_names=case.expected_node_names,
                    expected_relation_types=case.expected_relation_types,
                    expected_channels_used=case.expected_channels_used,
                    forbidden_node_names=case.forbidden_node_names,
                    forbidden_evidence_refs=case.forbidden_evidence_refs,
                    forbidden_topics=case.forbidden_topics,
                    min_hits=case.min_hits,
                    min_evidence_refs=case.min_evidence_refs,
                    min_matched_nodes=case.min_matched_nodes,
                    min_matched_edges=case.min_matched_edges,
                    max_forbidden_hits=case.max_forbidden_hits,
                ),
                evidence_refs=context.evidence_refs,
                hit_titles=[hit.get("title", "") for hit in context.hits],
                channels_used=context.retrieval_channels_used,
                matched_nodes=[
                    CompiledNode.model_validate(item) for item in context.matched_nodes
                ],
                matched_edges=[
                    CompiledEdge.model_validate(item) for item in context.matched_edges
                ],
            )
            item = dto_to_dict(replay)
            item["retrieval_mode"] = case.retrieval_mode
            item["channels_used"] = context.retrieval_channels_used
            item["warnings"] = context.warnings
            item["query_anchor"] = context.query_anchor
            item["routing_decision"] = context.routing_decision
            item["retrieval_metrics"] = context.retrieval_trace.get("retrieval_metrics", {})
            item["candidate_judgement_summary"] = _candidate_judgement_summary(
                context.retrieval_trace.get("candidate_judgements", [])
            )
            item["trace_replay"] = case.replay_trace
            item["trace_mismatches"] = trace_mismatches
            results.append(item)
        passed = sum(1 for item in results if item.get("passed"))
        return KnowledgeBadCaseReplayResultDTO(
            adapter_name=command.adapter_name,
            target=command.target,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            metrics=_bad_case_replay_metrics(results),
            results=results,
        )

    async def _replay_research_context_trace(
        self,
        *,
        case: Any,
        command: KnowledgeBadCaseReplayCommand,
    ) -> tuple[KnowledgeResearchContextDTO, list[dict[str, Any]]]:
        if not case.recorded_trace:
            raise ValueError(f"bad case {case.case_id} replay_trace requires recorded_trace")
        options = RetrievalOptions(
            adapter_name=command.adapter_name,
            target=command.target,
            graph_depth=max(1, min(command.graph_depth, 3)),
            graph_limit=command.graph_limit,
            wiki_limit=command.wiki_limit,
            evidence_limit=command.evidence_limit,
            keyword_limit=max(command.graph_limit, 1),
            semantic_hybrid_limit=command.evidence_limit,
            max_hits=max(command.graph_limit, command.wiki_limit, command.evidence_limit, 1),
            max_chars=command.max_chars,
        )
        repository = self._require_repository()
        runtime = HybridRetrievalRuntime(
            repository,
            semantic_retriever=_semantic_hybrid_retriever(),
        )
        replay = await replay_retrieval_trace(
            query=case.query,
            recorded_trace=RetrievalTrace.model_validate(case.recorded_trace),
            registry=_retrieval_tool_registry(runtime, options),
        )
        context = runtime.build_answer_context_from_hits(
            query=case.query,
            hits=replay.hits,
            options=options,
            trace=replay.trace,
        )
        dto = KnowledgeResearchContextDTO(
            query=case.query,
            hits=[dto_to_dict(hit) for hit in context.hits],
            matched_nodes=[dto_to_dict(node) for node in context.matched_nodes],
            matched_edges=[dto_to_dict(edge) for edge in context.matched_edges],
            evidence_refs=_ordered_unique(
                evidence_id for hit in context.hits for evidence_id in hit.evidence_refs
            ),
            context_text=_context_text(context),
            budget_usage=dto_to_dict(context.budget_usage),
            mode=context.trace.mode,
            retrieval_channels_enabled=context.trace.channels_enabled,
            retrieval_channels_used=context.trace.channels_used,
            semantic_enabled=context.trace.semantic_enabled,
            milvus_enabled=context.trace.milvus_enabled,
            planner_enabled=context.trace.planner_enabled,
            retrieval_trace=dto_to_dict(context.trace),
            warnings=list(context.trace.warnings),
        )
        return dto, [dto_to_dict(item) for item in replay.mismatches]

    async def apply_review_action_for(self, command: KnowledgeReviewActionCommand) -> dict[str, Any]:
        await self.apply_review_action(command.review_id, command.action)  # type: ignore[arg-type]
        return {
            "review_id": command.review_id,
            "action": command.action,
            "operator": command.operator,
            "reason": command.reason,
            "updated": True,
        }

    async def compile(self, adapter: DomainAdapter, raw_records: Any) -> CompileResult:
        inputs, normalize_failures = _normalize_records(adapter, raw_records)
        result = await self.compiler.compile(adapter, inputs)
        result.failed_records[:0] = normalize_failures
        return result

    async def search(self, *args, **kwargs):
        raise NotImplementedError("Knowledge search is planned for a later step")

    async def rebuild_indexes(self, adapter_name: str) -> dict[str, int]:
        repository = self._require_repository()
        _cleanup_evidence_versions(repository, adapter_name)
        return {
            "graph_adjacency": repository.rebuild_graph_adjacency(adapter_name),
            "evidence_chunks": repository.rebuild_evidence_chunks(adapter_name),
        }

    async def build_answer_context(
        self,
        query: str,
        options: RetrievalOptions,
    ) -> AnswerContext:
        return await HybridRetrievalRuntime(
            self._require_repository(),
            semantic_retriever=_semantic_hybrid_retriever(),
        ).build_answer_context_async(
            query,
            options,
        )

    async def _build_research_answer_context(
        self,
        query: str,
        options: RetrievalOptions,
        *,
        retrieval_plan,
        retrieval_mode: str = "deterministic_plan",
    ) -> AnswerContext:
        repository = self._require_repository()
        runtime = HybridRetrievalRuntime(
            repository,
            semantic_retriever=_semantic_hybrid_retriever(),
        )
        if retrieval_plan is None:
            context = await runtime.build_answer_context_async(query, options)
            _save_retrieval_trace_snapshot(
                repository,
                query=query,
                options=options,
                context=context,
                strategy_name=context.trace.mode,
                strategy_version="v1",
            )
            return context
        execution = await RetrievalPlanExecutor(
            _retrieval_tool_registry(runtime, options)
        ).execute(query=query, plan=retrieval_plan)
        context = runtime.build_answer_context_from_hits(
            query=query,
            hits=execution.hits,
            options=options,
            trace=execution.trace,
        )
        _save_retrieval_trace_snapshot(
            repository,
            query=query,
            options=options,
            context=context,
            strategy_name=context.trace.mode,
            strategy_version="v1",
        )
        return context

    async def resolve_financial_entities(self, text: str, limit: int = 20) -> dict[str, Any]:
        repository = self._require_repository()
        candidates = _match_financial_nodes(text, repository.list_nodes("financial"))[:limit]
        return {
            "query": text,
            "candidates": candidates,
            "ambiguous": _ambiguous_candidates(candidates),
        }

    async def expand_financial_candidates(
        self,
        mentioned_entities: list[str | dict[str, Any]],
        depth: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        repository = self._require_repository()
        seeds = _resolve_seed_node_ids(mentioned_entities, repository.list_nodes("financial"))
        options = RetrievalOptions(
            adapter_name="financial",
            graph_depth=max(1, min(depth, 3)),
            graph_limit=limit,
            max_hits=limit,
        )
        hits = HybridRetrievalRuntime(repository).graph_search(seeds, options, depth=depth, limit=limit)
        candidate_node_ids = _ordered_unique(node_id for hit in hits for node_id in hit.node_refs)
        return {
            "seed_node_ids": seeds,
            "candidate_node_ids": candidate_node_ids,
            "hits": hits,
        }

    async def write_l1_event_to_kg(self, event_record: dict[str, Any]) -> CompileResult:
        adapter = get_adapter("financial", target=self.target)
        return await self.compiler.compile(adapter, adapter.normalize([_as_financial_source("l1_events", event_record)]))

    async def record_kg_feedback(self, feedback_record: dict[str, Any]) -> CompileResult:
        adapter = get_adapter("financial", target=self.target)
        return await self.compiler.compile(adapter, adapter.normalize([_as_financial_source("feedback_records", feedback_record)]))

    async def find_financial_paths(
        self,
        seed_node_ids: list[str],
        max_depth: int = 3,
        limit: int = 20,
    ) -> dict[str, Any]:
        repository = self._require_repository()
        paths = _find_financial_paths(
            seed_node_ids=seed_node_ids,
            nodes=repository.list_nodes("financial"),
            edges=repository.list_edges("financial"),
            max_depth=max(1, min(max_depth, 4)),
            limit=limit,
        )
        return {"seed_node_ids": seed_node_ids, "paths": paths}

    async def build_research_context(
        self,
        query: str,
        max_chars: int = 5000,
    ) -> dict[str, Any]:
        options = RetrievalOptions(
            adapter_name="financial",
            graph_depth=3,
            graph_limit=20,
            wiki_limit=10,
            evidence_limit=20,
            max_hits=20,
            max_chars=max_chars,
        )
        context = await self.build_answer_context(query, options)
        return {
            "query": query,
            "answer_context": context,
            "evidence_refs": _ordered_unique(
                evidence_id for hit in context.hits for evidence_id in hit.evidence_refs
            ),
            "hard_score_edges": [
                edge
                for edge in context.matched_edges
                if can_hard_consume(edge.relation_type, edge.confidence_label, edge.status)
            ],
            "explanation_edges": [
                edge
                for edge in context.matched_edges
                if not can_hard_consume(edge.relation_type, edge.confidence_label, edge.status)
            ],
        }

    async def quality_scan(self, adapter_name: str, persist_review: bool = True) -> QualityReport:
        repository = self._require_repository()
        nodes = repository.list_nodes(adapter_name)
        edges = repository.list_edges(adapter_name)
        evidence = repository.list_evidence(adapter_name)
        report = self.quality_scanner.scan(
            adapter_name=adapter_name,
            nodes=nodes,
            edges=edges,
            evidence=evidence,
        )
        if adapter_name == "financial":
            from src.infrastructure.persistence.repositories.knowledge_normalization_rule_repository import (
                KnowledgeNormalizationRuleRepository,
            )

            rules = KnowledgeNormalizationRuleRepository(target=self.target).list_rules(adapter_name, status="active")
            rule_issues, rule_metrics = audit_financial_normalization_rules(
                adapter_name=adapter_name,
                rules=rules,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
            )
            report.issues.extend(rule_issues)
            report.metrics["normalization_audit"] = rule_metrics
        if persist_review:
            repository.upsert_review_entries(self.quality_scanner.review_entries_for(report))
        return report

    async def list_review_queue(self, status: str | None = "open"):
        return self._require_repository().list_review_entries(status=status)

    async def apply_review_action(self, review_id: str, action: ReviewAction) -> None:
        self._require_repository().apply_review_action(review_id, action)

    async def replay_bad_case(
        self,
        *,
        case_id: str,
        expected_refs: list[str],
        actual_refs: list[str],
        query: str | None = None,
    ) -> BadCaseReplay:
        return replay_bad_case(
            case_id=case_id,
            query=query,
            expected_refs=expected_refs,
            actual_refs=actual_refs,
        )

    async def compare_compile_results(self, before: CompileResult, after: CompileResult) -> dict[str, Any]:
        return {
            "node_delta": len(after.nodes) - len(before.nodes),
            "edge_delta": len(after.edges) - len(before.edges),
            "evidence_delta": len(after.evidence) - len(before.evidence),
            "failed_delta": len(after.failed_records) - len(before.failed_records),
            "same_node_ids": {node.node_id for node in before.nodes}
            == {node.node_id for node in after.nodes},
            "same_edge_ids": {edge.edge_id for edge in before.edges}
            == {edge.edge_id for edge in after.edges},
        }

    async def _refresh_incremental_indexes(
        self,
        result: CompileResult,
        target: Target,
        *,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        repository = self._require_repository()
        if not result.nodes and not result.edges and not result.evidence:
            return {
                "mode": "incremental",
                "graph_adjacency": 0,
                "evidence_chunks": 0,
                "hybrid_chunks": 0,
                "graph_index": {},
            }

        with profile_span("kg_incremental_index.cleanup_evidence_versions", adapter=result.adapter_name):
            cleanup = _cleanup_evidence_versions(repository, result.adapter_name)
        evidence_ids_to_delete = _ordered_unique(
            [*(cleanup.get("evidence_ids") or []), *[item.evidence_id for item in result.evidence]]
        )
        with profile_span(
            "kg_incremental_index.delete_stale_hybrid_vectors",
            evidence=len(evidence_ids_to_delete),
        ):
            stale_hybrid_vectors = await _delete_hybrid_evidence_vectors(
                adapter_name=result.adapter_name,
                target=target,
                evidence_ids=evidence_ids_to_delete,
            )

        graph_adjacency = 0
        with profile_span("kg_incremental_index.upsert_evidence_chunks", evidence=len(result.evidence)):
            evidence_chunks = repository.upsert_evidence_chunks(result.evidence)
        with profile_span(
            "kg_incremental_index.collect_semantic_materials",
            nodes=len(result.nodes),
            edges=len(result.edges),
            evidence=len(result.evidence),
        ):
            semantic_materials = _semantic_index_materials_for_result(repository, result)
        with profile_span(
            "kg_incremental_index.delete_stale_semantic_documents",
            chunk_ids=len(semantic_materials.stale_chunk_ids),
        ):
            stale_semantic_documents = await _delete_hybrid_documents(
                adapter_name=result.adapter_name,
                target=target,
                chunk_ids=semantic_materials.stale_chunk_ids,
            )
        with profile_span(
            "kg_incremental_index.hybrid_upsert_index",
            chunks=len(semantic_materials.chunks),
            nodes=len(semantic_materials.nodes),
            edges=len(semantic_materials.edges),
        ):
            hybrid_chunks = await _semantic_hybrid_retriever().upsert_index(
                adapter_name=result.adapter_name,
                target=target,
                chunks=semantic_materials.chunks,
                nodes=semantic_materials.nodes,
                edges=semantic_materials.edges,
                kg_version=result.version,
                graph_projections=FINANCIAL_GRAPH_PROJECTIONS if result.adapter_name == "financial" else None,
            )
        with profile_span(
            "kg_incremental_index.refresh_cognitive_index",
            adapter=result.adapter_name,
            changed_chunks=len(semantic_materials.chunks),
        ):
            cognitive_index = await _refresh_cognitive_index(
                repository=repository,
                result=result,
                target=target,
                changed_chunks=semantic_materials.chunks,
                workflow_id=workflow_id,
            )
            graph_index = {
                "status": "pending_relation_graph_phase",
                "reason": "atomic_cards_ready_and_legacy_topic_assignment_disabled",
            }
        summary = {
            "mode": "incremental",
            "graph_adjacency": graph_adjacency,
            "evidence_chunks": evidence_chunks,
            "hybrid_chunks": hybrid_chunks,
            "graph_index": graph_index,
            "cognitive_index": cognitive_index,
            "node_ids": [node.node_id for node in result.nodes],
            "edge_ids": [edge.edge_id for edge in result.edges],
            "evidence_ids": [item.evidence_id for item in result.evidence],
            "stale_evidence_cleanup": cleanup,
            "stale_hybrid_vectors_deleted": stale_hybrid_vectors,
            "stale_semantic_documents_deleted": stale_semantic_documents,
            "semantic_materials": {
                "chunks": len(semantic_materials.chunks),
                "nodes": len(semantic_materials.nodes),
                "edges": len(semantic_materials.edges),
                "stale_chunk_ids": semantic_materials.stale_chunk_ids,
            },
        }
        logger.info(
            "[kg_incremental_index] adapter=%s target=%s graph_adjacency=%d "
            "evidence_chunks=%d hybrid_chunks=%d cognitive_communities=%s nodes=%d edges=%d evidence=%d",
            result.adapter_name,
            target,
            graph_adjacency,
            evidence_chunks,
            hybrid_chunks,
            str((cognitive_index or {}).get("communities") or 0),
            len(result.nodes),
            len(result.edges),
            len(result.evidence),
        )
        return summary

    async def _materialize_pre_extraction_chunks(
        self,
        adapter_name: str,
        version: str,
        evidence: list[CompiledEvidence],
        *,
        target: Target | None = None,
    ) -> None:
        """Persist evidence/chunk readable targets before relation extraction.

        This keeps the write path genuinely chunk-first: the extractor receives
        chunk ids that already have PG manifests and Milvus chunk targets.
        """
        repository = self._require_repository()
        with profile_span("kg_pre_extraction_chunks.upsert_evidence", evidence=len(evidence)):
            repository.upsert_evidence(evidence)
        with profile_span("kg_pre_extraction_chunks.upsert_chunk_manifest", evidence=len(evidence)):
            repository.upsert_evidence_chunks(evidence)
        chunks = [chunk for item in evidence for chunk in build_chunks_for_compiled_evidence(item)]
        if not chunks:
            return
        with profile_span("kg_pre_extraction_chunks.milvus_upsert", chunks=len(chunks)):
            await _semantic_hybrid_retriever().upsert_index(
                adapter_name=adapter_name,
                target=target or self.target,
                chunks=chunks,
                nodes=[],
                edges=[],
                kg_version=version,
                graph_projections=FINANCIAL_GRAPH_PROJECTIONS if adapter_name == "financial" else None,
            )

    def _require_repository(self) -> KnowledgeRepository:
        if self.repository is None:
            raise RuntimeError("Knowledge repository is required for this use case")
        return self.repository

Target = Literal["prod", "test"]


async def _refresh_cognitive_index(
    *,
    repository: KnowledgeRepository,
    result: CompileResult,
    target: Target,
    changed_chunks: list[EvidenceChunk],
    workflow_id: str = "",
) -> dict[str, Any]:
    semantic_retriever = _semantic_hybrid_retriever()
    stage = AtomicCognitiveCardStageService(
        repository=repository,
        semantic_retriever=semantic_retriever,
        relation_writer=CardRelationWriteService(
            knowledge_repository=repository,
            semantic_retriever=semantic_retriever,
            workflow_id=workflow_id,
        ),
    )
    result_stage = await stage.refresh(
        adapter_name=result.adapter_name,
        target=target,
        kg_version=result.version,
        changed_chunks=changed_chunks,
        persist=True,
    )
    return {
        **result_stage.diagnostics,
        "status": result_stage.status,
        "changed_chunks": len(changed_chunks),
        "changed_evidence": len({chunk.evidence_id for chunk in changed_chunks}),
        "card_ids": [card.cognitive_card_id for card in result_stage.cards],
        "assignments": 0,
        "communities": 0,
        "documents_written": 0,
        "cognitive_card_documents_written": result_stage.diagnostics.get(
            "milvus_documents_written",
            0,
        ),
        "graph_persistence": {
            "mode": "intra_chunk_relations",
            "relations": result_stage.diagnostics.get(
                "intra_chunk_relations",
                0,
            ),
            "observed": result_stage.diagnostics.get(
                "intra_chunk_observed",
                0,
            ),
            "inferred": result_stage.diagnostics.get(
                "intra_chunk_inferred",
                0,
            ),
            "changed_edge_ids": result_stage.diagnostics.get(
                "intra_chunk_changed_edge_ids",
                [],
            ),
            "graph_event_ids": result_stage.diagnostics.get(
                "intra_chunk_graph_event_ids",
                [],
            ),
        },
    }


async def _ensure_seed_communities(
    *,
    repository: KnowledgeRepository,
    adapter_name: str,
    target: Target,
    kg_version: str,
) -> dict[str, Any]:
    existing_communities = repository.list_graph_communities(adapter_name)
    seed_communities = seed_graph_communities(
        adapter_name,
        existing_communities=existing_communities,
        community_id_factory=lambda _definition: repository.allocate_graph_community_id(
            adapter_name,
            level=0,
        ),
    )
    if not seed_communities:
        return {"status": "skipped", "reason": "no_seed_definitions", "communities": 0, "documents_written": 0}
    with profile_span("kg_cognitive_index.ensure_seed_pg", communities=len(seed_communities)):
        persistence = repository.replace_graph_index_scope(
            adapter_name,
            remove_community_ids=[],
            communities=seed_communities,
            findings=[],
            deltas=[],
            unassigned_signals=[],
        )
    semantic_documents = [
        _semantic_document_from_graph_index_document(_cognitive_community_document(item))
        for item in seed_communities
    ]
    with profile_span("kg_cognitive_index.ensure_seed_milvus", communities=len(seed_communities)):
        documents_written = await _semantic_hybrid_retriever().upsert_semantic_documents(
            adapter_name=adapter_name,
            target=target,
            documents=semantic_documents,
            kg_version=kg_version,
        )
    return {
        "status": "completed",
        "communities": len(seed_communities),
        "documents_written": documents_written,
        "community_ids": [item.community_id for item in seed_communities],
        "persistence": persistence,
    }


@dataclass(frozen=True)
class _SemanticIndexMaterials:
    chunks: list[EvidenceChunk]
    nodes: list[CompiledNode]
    edges: list[CompiledEdge]
    stale_chunk_ids: list[str]


def create_knowledge_service(target: Target | None = None) -> KnowledgeService:
    from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
        KnowledgeRepositoryImpl,
    )
    from src.infrastructure.persistence.repositories.knowledge_source_projection_repository_impl import (
        KnowledgeSourceProjectionRepositoryImpl,
    )

    return KnowledgeService(
        repository=KnowledgeRepositoryImpl(target=target),
        target=target,
        source_projection_repository=KnowledgeSourceProjectionRepositoryImpl(target=target),
    )


def _ensure_scope_supported(scope: str) -> None:
    _graph_index_scope_projection(scope)


def _graph_index_scope_projection(scope: str) -> str | None:
    normalized = (scope or "all").strip()
    if normalized in {"", "all"}:
        return None
    if normalized.startswith("projection:"):
        normalized = normalized.split(":", 1)[1].strip()
    known = {profile.projection for profile in FINANCIAL_GRAPH_PROJECTIONS}
    if normalized not in known:
        raise ValueError(
            "unsupported scope: "
            f"{scope}. supported scopes: all, "
            + ", ".join(f"projection:{profile.projection}" for profile in FINANCIAL_GRAPH_PROJECTIONS)
        )
    return normalized


def _graph_index_selected_projections(scope: str) -> tuple[Any, ...]:
    projection = _graph_index_scope_projection(scope)
    if projection is None:
        return FINANCIAL_GRAPH_PROJECTIONS
    return tuple(profile for profile in FINANCIAL_GRAPH_PROJECTIONS if profile.projection == projection)


def _compile_concurrency(command: KnowledgeCompileCommand) -> int:
    if command.concurrency is not None:
        return max(1, int(command.concurrency))
    return max(1, int(settings.CLAUDE_PROXY_MAX_CONCURRENCY))


async def _delete_hybrid_evidence_vectors(*, adapter_name: str, target: str, evidence_ids: list[str]) -> int:
    if not evidence_ids:
        return 0
    try:
        return await _semantic_hybrid_retriever().delete_evidence(
            adapter_name=adapter_name,
            target=target,
            evidence_ids=evidence_ids,
        )
    except Exception as exc:
        logger.warning(
            "[kg_cleanup] failed to delete stale hybrid vectors adapter=%s target=%s evidence=%d error=%s",
            adapter_name,
            target,
            len(evidence_ids),
            exc,
        )
        return 0


async def _delete_hybrid_documents(*, adapter_name: str, target: str, chunk_ids: list[str]) -> int:
    if not chunk_ids:
        return 0
    try:
        return await _semantic_hybrid_retriever().delete_documents(
            adapter_name=adapter_name,
            target=target,
            chunk_ids=chunk_ids,
        )
    except Exception as exc:
        logger.warning(
            "[kg_cleanup] failed to delete stale semantic documents adapter=%s target=%s chunk_ids=%d error=%s",
            adapter_name,
            target,
            len(chunk_ids),
            exc,
        )
        return 0


async def _prune_stale_community_documents(
    *,
    adapter_name: str,
    target: str,
    active_target_ids: list[str],
) -> int:
    active = {target_id for target_id in active_target_ids if target_id}
    try:
        existing = await _semantic_hybrid_retriever().list_target_ids_by_role(
            collection_role=SEMANTIC_COLLECTION_COMMUNITY,
            adapter_name=adapter_name,
            target=target,
        )
        stale = sorted(target_id for target_id in existing if target_id not in active)
        if not stale:
            return 0
        return await _semantic_hybrid_retriever().delete_documents_by_role(
            collection_role=SEMANTIC_COLLECTION_COMMUNITY,
            adapter_name=adapter_name,
            target=target,
            target_ids=stale,
        )
    except Exception as exc:
        logger.warning(
            "[kg_cleanup] failed to prune stale community documents adapter=%s target=%s active=%d error=%s",
            adapter_name,
            target,
            len(active),
            exc,
        )
        return 0


async def _prune_stale_cognitive_card_documents(
    *,
    adapter_name: str,
    target: str,
    active_target_ids: list[str],
) -> int:
    active = {target_id for target_id in active_target_ids if target_id}
    try:
        existing = await _semantic_hybrid_retriever().list_target_ids_by_role(
            collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD,
            adapter_name=adapter_name,
            target=target,
        )
        stale = sorted(target_id for target_id in existing if target_id not in active)
        if not stale:
            return 0
        return await _semantic_hybrid_retriever().delete_documents_by_role(
            collection_role=SEMANTIC_COLLECTION_COGNITIVE_CARD,
            adapter_name=adapter_name,
            target=target,
            target_ids=stale,
        )
    except Exception as exc:
        logger.warning(
            "[kg_cleanup] failed to prune stale cognitive card documents adapter=%s target=%s active=%d error=%s",
            adapter_name,
            target,
            len(active),
            exc,
        )
        return 0


async def _refresh_graph_index(
    *,
    repository: KnowledgeRepository,
    adapter_name: str,
    target: str,
    kg_version: str = "",
    changed_node_ids: list[str] | None = None,
    changed_edge_ids: list[str] | None = None,
    changed_evidence_ids: list[str] | None = None,
    changed_chunk_ids: list[str] | None = None,
    force_rebuild_reason: str = "",
    graph_index_scope: str = "all",
) -> dict[str, Any]:
    scope_projection = _graph_index_scope_projection(graph_index_scope)
    existing_communities = repository.list_graph_communities(adapter_name)
    existing_findings = repository.list_graph_findings(adapter_name)
    existing_unassigned_signals = repository.list_graph_unassigned_signals(adapter_name, status="active")
    material_counts = repository.count_graph_index_materials(adapter_name)
    dirty_refs = GraphIndexDirtyRefs(
        node_ids=changed_node_ids or [],
        edge_ids=changed_edge_ids or [],
        evidence_ids=changed_evidence_ids or [],
        chunk_ids=changed_chunk_ids or [],
    )
    related_unassigned_signals = _related_graph_unassigned_signals(existing_unassigned_signals, dirty_refs)
    with langfuse_observation(
        name="graph_index.change_score",
        as_type="span",
        input={
            "changed_node_ids": changed_node_ids or [],
            "changed_edge_ids": changed_edge_ids or [],
            "changed_evidence_ids": changed_evidence_ids or [],
            "changed_chunk_ids": changed_chunk_ids or [],
            "existing_communities": len(existing_communities),
            "existing_unassigned_signals": len(existing_unassigned_signals),
            "related_unassigned_signals": len(related_unassigned_signals),
            "material_counts": material_counts,
        },
        metadata={"adapter_name": adapter_name, "target": target},
    ):
        refresh_plan = plan_graph_index_refresh(
            existing_communities=existing_communities,
            changed_node_ids=changed_node_ids,
            changed_edge_ids=changed_edge_ids,
            changed_evidence_ids=changed_evidence_ids,
            changed_chunk_ids=changed_chunk_ids,
            total_node_count=material_counts.get("nodes", 0),
            total_edge_count=material_counts.get("edges", 0),
            total_chunk_count=material_counts.get("chunks", 0),
        )
        langfuse_update_span(output=refresh_plan.as_dict(), status_message=refresh_plan.action)
    if (
        not force_rebuild_reason
        and related_unassigned_signals
        and refresh_plan.action == "full_rebuild"
        and "changed_refs_not_attached_to_existing_community" in refresh_plan.reasons
    ):
        refresh_plan = GraphIndexRefreshPlan(
            action="local_recompute_required",
            score=min(refresh_plan.score, 0.34),
            affected_community_ids=[],
            affected_projection_counts=refresh_plan.affected_projection_counts,
            changed_counts=refresh_plan.changed_counts,
            metrics={
                **refresh_plan.metrics,
                "related_unassigned_signal_ids": [
                    signal.signal_id for signal in related_unassigned_signals
                ],
            },
            reasons=["related_unassigned_signal_promotion", *refresh_plan.reasons],
        )
    if force_rebuild_reason and refresh_plan.action == "noop":
        refresh_plan = GraphIndexRefreshPlan(
            action="full_rebuild",
            score=1.0,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts=refresh_plan.changed_counts,
            metrics={**refresh_plan.metrics, "existing_communities": len(existing_communities)},
            reasons=[force_rebuild_reason],
        )
    if force_rebuild_reason or refresh_plan.action in {"noop", "full_rebuild"}:
        chunks = repository.list_evidence_chunks(adapter_name)
        nodes = repository.list_nodes(adapter_name)
        edges = repository.list_edges(adapter_name)
    else:
        seed_refs = _graph_index_material_seed(
            existing_communities,
            dirty_refs,
            refresh_plan,
            related_unassigned_signals=related_unassigned_signals,
        )
        scoped_materials = repository.list_graph_index_materials(
            adapter_name,
            node_ids=seed_refs["node_ids"],
            edge_ids=seed_refs["edge_ids"],
            evidence_ids=seed_refs["evidence_ids"],
            chunk_ids=seed_refs["chunk_ids"],
        )
        chunks = list(scoped_materials.get("chunks") or [])
        nodes = list(scoped_materials.get("nodes") or [])
        edges = list(scoped_materials.get("edges") or [])
    with langfuse_observation(
        name="graph_index.dirty_subgraph",
        as_type="span",
        input={"refresh_plan": refresh_plan.as_dict(), "dirty_refs": dirty_refs.__dict__},
        metadata={"adapter_name": adapter_name, "target": target},
    ):
        build_scope = _graph_index_build_scope(
            chunks=chunks,
            nodes=nodes,
            edges=edges,
            existing_communities=existing_communities,
            dirty_refs=dirty_refs,
            related_unassigned_signals=related_unassigned_signals,
            refresh_plan=refresh_plan,
            force_rebuild=bool(force_rebuild_reason),
            graph_index_scope=graph_index_scope,
        )
        langfuse_update_span(output=build_scope["diagnostics"], status_message=build_scope["diagnostics"]["strategy"])

    if refresh_plan.action == "light_refresh_required" and not force_rebuild_reason:
        graph_index = await _light_refresh_graph_index(
            existing_communities=existing_communities,
            existing_findings=existing_findings,
            chunks=chunks,
            nodes=nodes,
            edges=edges,
            dirty_refs=dirty_refs,
            refresh_plan=refresh_plan,
        )
    elif refresh_plan.action == "local_review_required" and not force_rebuild_reason:
        graph_index = await _local_review_graph_index(
            existing_communities=existing_communities,
            chunks=build_scope["chunks"],
            nodes=build_scope["nodes"],
            edges=build_scope["edges"],
            dirty_refs=dirty_refs,
            refresh_plan=refresh_plan,
        )
    else:
        with langfuse_observation(
            name="graph_index.community_detect",
            as_type="span",
            input=build_scope["diagnostics"],
            metadata={"adapter_name": adapter_name, "target": target},
        ):
            graph_index = build_graph_index(
                chunks=build_scope["chunks"],
                nodes=build_scope["nodes"],
                edges=build_scope["edges"],
                projections=build_scope["projections"],
            )
            langfuse_update_span(output=graph_index.diagnostics, status_message="completed")
        with langfuse_observation(
            name="graph_index.report_generate_batch",
            as_type="span",
            input={
                "communities": len(graph_index.communities),
                "chunks": len(build_scope["chunks"]),
                "nodes": len(build_scope["nodes"]),
                "edges": len(build_scope["edges"]),
            },
            metadata={"adapter_name": adapter_name, "target": target},
        ):
            graph_index = await GraphIndexLLMReporter().enrich(
                graph_index=graph_index,
                nodes=build_scope["nodes"],
                edges=build_scope["edges"],
                chunks=build_scope["chunks"],
            )
            langfuse_update_span(output=graph_index.diagnostics, status_message="completed")
        with langfuse_observation(
            name="graph_index.lineage_resolve",
            as_type="span",
            input={
                "new_communities": [community.community_id for community in graph_index.communities],
                "existing_communities": [community.community_id for community in existing_communities],
            },
            metadata={"adapter_name": adapter_name, "target": target},
        ):
            resolved_communities = resolve_graph_index_lineage(
                communities=graph_index.communities,
                existing_communities=existing_communities,
            )
            langfuse_update_span(
                output={
                    "communities": len(resolved_communities),
                    "change_reasons": _count_by([community.change_reason for community in resolved_communities]),
                },
                status_message="completed",
            )
        graph_index = graph_index.__class__(
            communities=resolved_communities,
            findings=graph_index.findings,
            deltas=graph_index.deltas,
            documents=[],
            diagnostics={
                **graph_index.diagnostics,
                "build_scope": build_scope["diagnostics"],
            },
            unassigned_signals=graph_index.unassigned_signals,
        )
        with langfuse_observation(
            name="graph_index.rolling_delta_build",
            as_type="span",
            input={"communities": len(graph_index.communities), "findings": len(graph_index.findings)},
            metadata={"adapter_name": adapter_name, "target": target},
        ):
            deltas = build_rolling_delta_index(
                communities=graph_index.communities,
                findings=graph_index.findings,
                chunks=build_scope["chunks"],
            )
            langfuse_update_span(output={"deltas": len(deltas)}, status_message="completed")
        graph_index = graph_index.__class__(
            communities=graph_index.communities,
            findings=graph_index.findings,
            deltas=deltas,
            documents=build_graph_index_documents(
                communities=graph_index.communities,
                findings=graph_index.findings,
                deltas=deltas,
                nodes=build_scope["nodes"],
            ),
            diagnostics=graph_index.diagnostics,
            unassigned_signals=graph_index.unassigned_signals,
        )
    scope = _graph_index_replacement_scope(
        existing_communities=existing_communities,
        rebuilt_communities=graph_index.communities,
        rebuilt_findings=graph_index.findings,
        rebuilt_deltas=graph_index.deltas,
        dirty_refs=dirty_refs,
        refresh_plan=refresh_plan,
        force_rebuild=bool(force_rebuild_reason),
        scope_projection=scope_projection,
        related_unassigned_signal_ids=[signal.signal_id for signal in related_unassigned_signals],
    )
    promoted_signals = _promoted_graph_unassigned_signals(
        related_unassigned_signals,
        scope["communities"],
    )
    if scope["strategy"] in {"local_recompute_scoped_replace", "local_unassigned_promotion"}:
        selected_community_ids = {community.community_id for community in scope["communities"]}
        selected_finding_ids = {finding.finding_id for finding in scope["findings"]}
        persisted = repository.replace_graph_index_scope(
            adapter_name,
            remove_community_ids=scope["remove_community_ids"],
            communities=scope["communities"],
            findings=scope["findings"],
            deltas=scope["deltas"],
            unassigned_signals=graph_index.unassigned_signals,
            promoted_signals=promoted_signals,
        )
        graph_documents = [
            document
            for document in graph_index.documents
            if document.document_id in selected_community_ids
            or document.document_id in selected_finding_ids
            or document.document_id in {delta.delta_id for delta in scope["deltas"]}
        ]
    else:
        persisted = repository.replace_graph_index(
            adapter_name,
            communities=graph_index.communities,
            findings=graph_index.findings,
            deltas=graph_index.deltas,
            unassigned_signals=graph_index.unassigned_signals,
        )
        graph_documents = graph_index.documents
    stale_target_ids = [str(item) for item in persisted.get("stale_target_ids") or [] if item]
    stale_deleted = await _delete_hybrid_documents(
        adapter_name=adapter_name,
        target=target,
        chunk_ids=stale_target_ids,
    )
    documents = [_semantic_document_from_graph_index_document(document) for document in graph_documents]
    documents_written = await _semantic_hybrid_retriever().upsert_semantic_documents(
        adapter_name=adapter_name,
        target=target,
        documents=documents,
        kg_version=kg_version,
    )
    summary = {
        "communities": len(scope["communities"]),
        "findings": len(scope["findings"]),
        "deltas": len(scope["deltas"]),
        "built_communities": len(graph_index.communities),
        "built_findings": len(graph_index.findings),
        "built_deltas": len(graph_index.deltas),
        "documents": len(documents),
        "built_unassigned_signals": len(graph_index.unassigned_signals),
        "related_unassigned_signals": len(related_unassigned_signals),
        "promoted_unassigned_signals": len(promoted_signals),
        "documents_written": documents_written,
        "stale_documents_deleted": stale_deleted,
        "persisted": {
            "communities": persisted.get("communities", 0),
            "findings": persisted.get("findings", 0),
            "deltas": persisted.get("deltas", 0),
            "unassigned_signals": persisted.get("unassigned_signals", 0),
            "promoted_unassigned_signals": persisted.get("promoted_unassigned_signals", 0),
            "stale_target_ids": len(stale_target_ids),
        },
        "refresh_plan": refresh_plan.as_dict(),
        "actual_refresh_strategy": scope["strategy"],
        "replacement_scope": {
            "remove_community_ids": len(scope["remove_community_ids"]),
            "communities": len(scope["communities"]),
            "findings": len(scope["findings"]),
            "deltas": len(scope["deltas"]),
            "projection": scope_projection or "all",
        },
        "diagnostics": graph_index.diagnostics,
    }
    logger.info(
        "[kg_graph_index] adapter=%s target=%s plan=%s strategy=%s score=%.4f affected=%d "
        "communities=%d/%d findings=%d/%d documents_written=%d stale_deleted=%d",
        adapter_name,
        target,
        refresh_plan.action,
        scope["strategy"],
        refresh_plan.score,
        len(refresh_plan.affected_community_ids),
        len(scope["communities"]),
        len(graph_index.communities),
        len(scope["findings"]),
        len(graph_index.findings),
        documents_written,
        stale_deleted,
    )
    return summary


def _graph_index_replacement_scope(
    *,
    existing_communities: list[Any],
    rebuilt_communities: list[Any],
    rebuilt_findings: list[Any],
    rebuilt_deltas: list[Any],
    dirty_refs: GraphIndexDirtyRefs,
    refresh_plan: GraphIndexRefreshPlan,
    force_rebuild: bool,
    scope_projection: str | None = None,
    related_unassigned_signal_ids: list[str] | None = None,
) -> dict[str, Any]:
    if force_rebuild and scope_projection:
        remove_community_ids = [
            community.community_id for community in existing_communities if community.projection == scope_projection
        ]
        replacement_ids = {community.community_id for community in rebuilt_communities}
        return {
            "strategy": "global_calibration_projection_replace",
            "remove_community_ids": remove_community_ids,
            "communities": rebuilt_communities,
            "findings": [finding for finding in rebuilt_findings if finding.community_id in replacement_ids],
            "deltas": [
                delta for delta in rebuilt_deltas if set(delta.community_ids).intersection(replacement_ids)
            ],
            "replacement_community_ids": replacement_ids,
        }
    if force_rebuild or refresh_plan.action in {"noop", "full_rebuild"}:
        return {
            "strategy": "global_calibration_full_replace" if force_rebuild else "full_replace",
            "remove_community_ids": [],
            "communities": rebuilt_communities,
            "findings": rebuilt_findings,
            "deltas": rebuilt_deltas,
        }
    remove_community_ids = expand_community_scope(existing_communities, refresh_plan.affected_community_ids)
    if not remove_community_ids:
        if related_unassigned_signal_ids:
            replacement_ids = {community.community_id for community in rebuilt_communities}
            return {
                "strategy": "local_unassigned_promotion",
                "remove_community_ids": [],
                "communities": rebuilt_communities,
                "findings": [finding for finding in rebuilt_findings if finding.community_id in replacement_ids],
                "deltas": [
                    delta for delta in rebuilt_deltas if set(delta.community_ids).intersection(replacement_ids)
                ],
                "replacement_community_ids": replacement_ids,
                "related_unassigned_signal_ids": related_unassigned_signal_ids,
            }
        return {
            "strategy": "full_replace",
            "remove_community_ids": [],
            "communities": rebuilt_communities,
            "findings": rebuilt_findings,
            "deltas": rebuilt_deltas,
        }
    replacement_communities = rebuilt_communities
    replacement_ids = {community.community_id for community in replacement_communities}
    replacement_findings = [finding for finding in rebuilt_findings if finding.community_id in replacement_ids]
    replacement_deltas = [
        delta
        for delta in rebuilt_deltas
        if set(delta.community_ids).intersection(replacement_ids)
    ]
    return {
        "strategy": "local_recompute_scoped_replace",
        "remove_community_ids": remove_community_ids,
        "communities": replacement_communities,
        "findings": replacement_findings,
        "deltas": replacement_deltas,
        "replacement_community_ids": replacement_ids,
    }


def _graph_index_material_seed(
    existing_communities: list[Any],
    dirty_refs: GraphIndexDirtyRefs,
    refresh_plan: GraphIndexRefreshPlan,
    *,
    related_unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
) -> dict[str, list[str]]:
    scoped_ids = set(expand_community_scope(existing_communities, refresh_plan.affected_community_ids))
    node_ids = list(dirty_refs.node_ids)
    edge_ids = list(dirty_refs.edge_ids)
    evidence_ids = list(dirty_refs.evidence_ids)
    chunk_ids = list(dirty_refs.chunk_ids)
    for community in existing_communities:
        if community.community_id not in scoped_ids:
            continue
        node_ids.extend(community.member_node_ids)
        edge_ids.extend(community.member_edge_ids)
        evidence_ids.extend(community.evidence_ids)
        chunk_ids.extend(community.chunk_ids)
    for signal in related_unassigned_signals or []:
        node_ids.extend(signal.node_ids)
        edge_ids.extend(signal.edge_ids)
        evidence_ids.extend(signal.evidence_ids)
        chunk_ids.extend(signal.chunk_ids)
    return {
        "node_ids": _ordered_unique(node_ids),
        "edge_ids": _ordered_unique(edge_ids),
        "evidence_ids": _ordered_unique(evidence_ids),
        "chunk_ids": _ordered_unique(chunk_ids),
    }


def _related_graph_unassigned_signals(
    signals: list[GraphIndexUnassignedSignal],
    dirty_refs: GraphIndexDirtyRefs,
) -> list[GraphIndexUnassignedSignal]:
    if not signals:
        return []
    dirty_nodes = set(dirty_refs.node_ids)
    dirty_edges = set(dirty_refs.edge_ids)
    dirty_evidence = set(dirty_refs.evidence_ids)
    dirty_chunks = set(dirty_refs.chunk_ids)
    result = [
        signal
        for signal in signals
        if (
            dirty_nodes.intersection(signal.node_ids)
            or dirty_edges.intersection(signal.edge_ids)
            or dirty_evidence.intersection(signal.evidence_ids)
            or dirty_chunks.intersection(signal.chunk_ids)
        )
    ]
    return sorted(result, key=lambda item: (item.projection, item.signal_id))


def _promoted_graph_unassigned_signals(
    signals: list[GraphIndexUnassignedSignal],
    communities: list[Any],
) -> dict[str, str]:
    if not signals or not communities:
        return {}
    promoted: dict[str, str] = {}
    for signal in signals:
        signal_edges = set(signal.edge_ids)
        signal_chunks = set(signal.chunk_ids)
        signal_evidence = set(signal.evidence_ids)
        signal_nodes = set(signal.node_ids)
        for community in communities:
            community_edges = set(community.member_edge_ids)
            community_chunks = set(community.chunk_ids)
            community_evidence = set(community.evidence_ids)
            community_nodes = set(community.member_node_ids)
            if signal_edges and signal_edges.issubset(community_edges):
                promoted[signal.signal_id] = community.community_id
                break
            if signal_chunks and signal_chunks.issubset(community_chunks) and signal_nodes.issubset(community_nodes):
                promoted[signal.signal_id] = community.community_id
                break
            if (
                signal_evidence
                and signal_evidence.issubset(community_evidence)
                and signal_nodes
                and signal_nodes.issubset(community_nodes)
            ):
                promoted[signal.signal_id] = community.community_id
                break
    return promoted


async def _light_refresh_graph_index(
    *,
    existing_communities: list[Any],
    existing_findings: list[Any],
    chunks: list[Any],
    nodes: list[Any],
    edges: list[Any],
    dirty_refs: GraphIndexDirtyRefs,
    refresh_plan: GraphIndexRefreshPlan,
) -> GraphIndexBuildResult:
    scoped_ids = expand_community_scope(existing_communities, refresh_plan.affected_community_ids)
    scoped_set = set(scoped_ids)
    changed_counts = refresh_plan.changed_counts
    now = datetime.now(timezone.utc)
    updated_communities = []
    for community in existing_communities:
        if community.community_id not in scoped_set:
            continue
        version_id = (
            f"{community.community_id}:v:light:"
            f"{_stable_digest([community.version_id, now.isoformat(), *dirty_refs.node_ids, *dirty_refs.edge_ids, *dirty_refs.chunk_ids])}"
        )
        updated_communities.append(
            replace(
                community,
                version_id=version_id,
                previous_version_id=community.version_id,
                change_reason="light_refresh",
                metrics={
                    **community.metrics,
                    "last_light_refresh": now.isoformat(),
                    "changed_counts": changed_counts,
                    "refresh_plan_score": refresh_plan.score,
                    "refresh_plan_reasons": refresh_plan.reasons,
                },
            )
        )
    version_by_community = {community.community_id: community.version_id for community in updated_communities}
    updated_findings = [
        replace(
            finding,
            version=version_by_community.get(finding.community_id, finding.version),
            payload={
                **(finding.payload or {}),
                "last_light_refresh": now.isoformat(),
                "refresh_plan_score": refresh_plan.score,
            },
        )
        for finding in existing_findings
        if finding.community_id in scoped_set
    ]
    deltas = build_rolling_delta_index(communities=updated_communities, findings=updated_findings, chunks=chunks, now=now)
    seed = GraphIndexBuildResult(
        communities=updated_communities,
        findings=updated_findings,
        deltas=deltas,
        documents=[],
        diagnostics={
            "community_algorithm": "none_light_refresh",
            "community_report_generator": "pending_delta_refresh",
            "build_scope": {
                "strategy": "light_refresh_existing_scope",
                "affected_community_ids": scoped_ids,
                "changed_counts": changed_counts,
            },
            "rolling_delta_count": len(deltas),
        },
    )
    return await GraphIndexLLMReporter().enrich_delta_refresh(
        graph_index=seed,
        nodes=nodes,
        edges=edges,
        chunks=chunks,
    )


async def _local_review_graph_index(
    *,
    existing_communities: list[Any],
    chunks: list[Any],
    nodes: list[Any],
    edges: list[Any],
    dirty_refs: GraphIndexDirtyRefs,
    refresh_plan: GraphIndexRefreshPlan,
) -> GraphIndexBuildResult:
    scoped_ids = expand_community_scope(existing_communities, refresh_plan.affected_community_ids)
    scoped_set = set(scoped_ids)
    now = datetime.now(timezone.utc)
    communities = []
    for community in existing_communities:
        if community.community_id not in scoped_set:
            continue
        version_id = (
            f"{community.community_id}:v:review:"
            f"{_stable_digest([community.version_id, now.isoformat(), *dirty_refs.node_ids, *dirty_refs.edge_ids, *dirty_refs.chunk_ids])}"
        )
        communities.append(
            replace(
                community,
                version_id=version_id,
                previous_version_id=community.version_id,
                change_reason="local_review",
                metrics={
                    **community.metrics,
                    "last_local_review": now.isoformat(),
                    "refresh_plan_score": refresh_plan.score,
                    "refresh_plan_reasons": refresh_plan.reasons,
                },
            )
        )
    seed = GraphIndexBuildResult(
        communities=communities,
        findings=[],
        deltas=[],
        documents=[],
        diagnostics={
            "community_algorithm": "none_local_review",
            "build_scope": {
                "strategy": "local_review_existing_scope",
                "affected_community_ids": scoped_ids,
                "changed_counts": refresh_plan.changed_counts,
            },
        },
    )
    with langfuse_observation(
        name="graph_index.local_review_report_generate",
        as_type="span",
        input={"communities": len(communities), "chunks": len(chunks), "nodes": len(nodes), "edges": len(edges)},
    ):
        reviewed = await GraphIndexLLMReporter().enrich(graph_index=seed, nodes=nodes, edges=edges, chunks=chunks)
        langfuse_update_span(output=reviewed.diagnostics, status_message="completed")
    return reviewed


def _graph_index_build_scope(
    *,
    chunks: list[Any],
    nodes: list[Any],
    edges: list[Any],
    existing_communities: list[Any],
    dirty_refs: GraphIndexDirtyRefs,
    related_unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
    refresh_plan: GraphIndexRefreshPlan,
    force_rebuild: bool,
    graph_index_scope: str = "all",
) -> dict[str, Any]:
    selected_projections = _graph_index_selected_projections(graph_index_scope)
    if force_rebuild or refresh_plan.action in {"noop", "full_rebuild"}:
        return {
            "chunks": chunks,
            "nodes": nodes,
            "edges": edges,
            "projections": selected_projections,
            "diagnostics": {
                "strategy": "full_build",
                "input_nodes": len(nodes),
                "input_edges": len(edges),
                "input_chunks": len(chunks),
                "related_unassigned_signals": len(related_unassigned_signals or []),
                "graph_index_scope": graph_index_scope,
                "projections": [profile.projection for profile in selected_projections],
            },
        }
    remove_community_ids = expand_community_scope(existing_communities, refresh_plan.affected_community_ids)
    if not remove_community_ids:
        return {
            "chunks": chunks,
            "nodes": nodes,
            "edges": edges,
            "projections": selected_projections,
            "diagnostics": {
                "strategy": "full_build_no_dirty_scope",
                "input_nodes": len(nodes),
                "input_edges": len(edges),
                "input_chunks": len(chunks),
                "related_unassigned_signals": len(related_unassigned_signals or []),
                "graph_index_scope": graph_index_scope,
                "projections": [profile.projection for profile in selected_projections],
            },
        }
    scoped_communities = [
        community for community in existing_communities if community.community_id in set(remove_community_ids)
    ]
    seed_node_ids = set(dirty_refs.node_ids)
    seed_edge_ids = set(dirty_refs.edge_ids)
    seed_evidence_ids = set(dirty_refs.evidence_ids)
    seed_chunk_ids = set(dirty_refs.chunk_ids)
    for community in scoped_communities:
        seed_node_ids.update(community.member_node_ids)
        seed_edge_ids.update(community.member_edge_ids)
        seed_evidence_ids.update(community.evidence_ids)
        seed_chunk_ids.update(community.chunk_ids)
    for signal in related_unassigned_signals or []:
        seed_node_ids.update(signal.node_ids)
        seed_edge_ids.update(signal.edge_ids)
        seed_evidence_ids.update(signal.evidence_ids)
        seed_chunk_ids.update(signal.chunk_ids)
    edges_by_id = {edge.edge_id: edge for edge in edges}
    for edge in edges:
        if edge.edge_id in seed_edge_ids or edge.source_node_id in seed_node_ids or edge.target_node_id in seed_node_ids:
            seed_edge_ids.add(edge.edge_id)
            seed_node_ids.add(edge.source_node_id)
            seed_node_ids.add(edge.target_node_id)
            seed_evidence_ids.update(edge.evidence_ids)
    scoped_nodes = [node for node in nodes if node.node_id in seed_node_ids]
    scoped_edges = [
        edge
        for edge in (edges_by_id[edge_id] for edge_id in seed_edge_ids if edge_id in edges_by_id)
        if edge.source_node_id in seed_node_ids and edge.target_node_id in seed_node_ids
    ]
    for edge in scoped_edges:
        seed_evidence_ids.update(edge.evidence_ids)
    scoped_chunks = [
        chunk
        for chunk in chunks
        if chunk.chunk_id in seed_chunk_ids or chunk.evidence_id in seed_evidence_ids
    ]
    affected_projections = set(refresh_plan.affected_projection_counts)
    projections = tuple(
        profile
        for profile in selected_projections
        if not affected_projections or profile.projection in affected_projections
    )
    return {
        "chunks": scoped_chunks,
        "nodes": scoped_nodes,
        "edges": scoped_edges,
        "projections": projections,
        "diagnostics": {
            "strategy": "dirty_subgraph_build",
            "remove_community_ids": len(remove_community_ids),
            "input_nodes": len(nodes),
            "input_edges": len(edges),
            "input_chunks": len(chunks),
            "related_unassigned_signals": len(related_unassigned_signals or []),
            "scoped_nodes": len(scoped_nodes),
            "scoped_edges": len(scoped_edges),
            "scoped_chunks": len(scoped_chunks),
            "projections": [profile.projection for profile in projections],
        },
    }


def _count_by(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _stable_digest(parts: list[str]) -> str:
    import hashlib

    data = "\n".join(str(part) for part in parts)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _semantic_document_from_graph_index_document(document: GraphIndexVectorDocument) -> SemanticVectorDocument:
    metadata = _graph_index_public_lens_metadata(document.metadata)
    text = document.text
    public_tags = metadata.get("public_projection_tags") or metadata.get("public_lens_tags") or []
    if public_tags:
        text = f"{text}\nPublic Lens Tags: {'；'.join(str(item) for item in public_tags)}"
    return SemanticVectorDocument(
        document_id=document.document_id,
        document_type=document.document_type,
        collection_role=document.collection_role,
        source_type=document.source_type,
        source_id=document.source_id,
        evidence_id=document.evidence_id,
        text=text,
        metadata=metadata,
    )


def _graph_index_public_lens_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result = dict(metadata or {})
    projection = str(result.get("projection") or "")
    if projection in GRAPH_INDEX_PUBLIC_LENS_ALIASES:
        result["public_projection"] = GRAPH_INDEX_PUBLIC_LENS_ALIASES[projection]
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        projection_scores = metrics.get("projection_scores")
        if isinstance(projection_scores, dict):
            public_scores = {
                GRAPH_INDEX_PUBLIC_LENS_ALIASES.get(str(key), str(key)): value
                for key, value in projection_scores.items()
            }
            result["public_projection_scores"] = public_scores
        projection_tags = metrics.get("projection_tags")
        if isinstance(projection_tags, list):
            result["public_projection_tags"] = [
                GRAPH_INDEX_PUBLIC_LENS_ALIASES.get(str(item), str(item))
                for item in projection_tags
            ]
    finding_type = str(result.get("finding_type") or "")
    if finding_type in GRAPH_INDEX_PUBLIC_LENS_ALIASES:
        result["public_finding_type"] = GRAPH_INDEX_PUBLIC_LENS_ALIASES[finding_type]
    return result


def _cleanup_evidence_versions(repository: KnowledgeRepository, adapter_name: str) -> dict[str, Any]:
    cleanup = getattr(repository, "cleanup_evidence_versions", None)
    if cleanup is None:
        return {"evidence": 0, "edges": 0, "evidence_ids": [], "edge_ids": []}
    return cleanup(adapter_name)


def _normalize_records(
    adapter: DomainAdapter,
    raw_records: Any,
) -> tuple[list[KnowledgeInput], list[FailedRecord]]:
    records = raw_records if isinstance(raw_records, list) else [raw_records]
    inputs: list[KnowledgeInput] = []
    failures: list[FailedRecord] = []
    for index, record in enumerate(records):
        if isinstance(record, KnowledgeInput):
            inputs.append(record)
            continue
        try:
            inputs.extend(adapter.normalize(record))
        except Exception as exc:
            failures.append(
                FailedRecord(
                    source_type=_raw_source_type(record),
                    source_id=_raw_source_id(record, index),
                    reason=f"normalize failed: {exc}",
                    details={"index": index},
                )
            )
    return inputs, failures


def _raw_source_type(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("source_type") or "raw")
    return "raw"


def _raw_source_id(record: Any, index: int) -> str:
    if isinstance(record, dict):
        payload = record.get("payload")
        if isinstance(payload, dict):
            value = record.get("source_id") or payload.get("source_id") or payload.get("id")
        else:
            value = record.get("source_id") or record.get("id")
        if value is not None and str(value).strip():
            return str(value)
    return f"normalize:{index}"


_SEMANTIC_HYBRID_RETRIEVER: MilvusSemanticHybridRetriever | None = None


def _semantic_hybrid_retriever():
    global _SEMANTIC_HYBRID_RETRIEVER
    if _SEMANTIC_HYBRID_RETRIEVER is None:
        with profile_span("kg_semantic_hybrid_retriever.create"):
            _SEMANTIC_HYBRID_RETRIEVER = MilvusSemanticHybridRetriever()
    return _SEMANTIC_HYBRID_RETRIEVER


def _semantic_index_materials_for_result(
    repository: KnowledgeRepository,
    result: CompileResult,
) -> _SemanticIndexMaterials:
    changed_evidence_ids = {item.evidence_id for item in result.evidence}
    chunks = _evidence_chunks_from_compiled(result.evidence)
    stale_chunk_ids = _semantic_vector_chunk_ids(
        node_ids=set(),
        edge_ids=set(),
        evidence_ids=changed_evidence_ids,
    )
    return _SemanticIndexMaterials(
        chunks=chunks,
        nodes=[],
        edges=[],
        stale_chunk_ids=stale_chunk_ids,
    )


def _semantic_vector_chunk_ids(
    *,
    node_ids: set[str],
    edge_ids: set[str],
    evidence_ids: set[str],
) -> list[str]:
    chunk_ids: list[str] = []
    for evidence_id in sorted(evidence_ids):
        chunk_ids.append(f"kg_chunk:{evidence_id}:0")
    for node_id in sorted(node_ids):
        chunk_ids.append(f"kg_card:node_card:{node_id}")
        chunk_ids.append(f"kg_card:event_card:{node_id}")
    for edge_id in sorted(edge_ids):
        chunk_ids.append(f"kg_card:edge:{edge_id}")
    return _ordered_unique(chunk_ids)


def _evidence_chunks_from_compiled(evidence: list[CompiledEvidence]) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []
    for item in evidence:
        if item.status != EvidenceStatus.ACTIVE:
            continue
        chunks.extend(build_chunks_for_compiled_evidence(item))
    return chunks


def _entity_search_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        for name in ("name", "code", "indicator_code", "taxonomy"):
            if item.get(name):
                terms.append(str(item[name]))
        if item.get("exchange") and item.get("code"):
            terms.append(f"{item['exchange']}:{item['code']}")
    return terms


def _merge_index_refresh_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "mode": "incremental",
        "graph_adjacency": 0,
        "evidence_chunks": 0,
        "hybrid_chunks": 0,
        "node_ids": [],
        "edge_ids": [],
        "evidence_ids": [],
    }
    for summary in summaries:
        for key in ("graph_adjacency", "evidence_chunks", "hybrid_chunks"):
            merged[key] += int(summary.get(key) or 0)
        for key in ("node_ids", "edge_ids", "evidence_ids"):
            merged[key].extend(summary.get(key) or [])
    for key in ("node_ids", "edge_ids", "evidence_ids"):
        merged[key] = _ordered_unique(merged[key])
    return merged


def _incremental_task_metadata(
    *,
    command: KnowledgeIncrementalRefreshCommand,
    status: str,
    attempt: int,
    max_retries: int,
) -> dict[str, Any]:
    return {
        "task_type": "financial_incremental_refresh",
        "status": status,
        "attempt": attempt,
        "max_retries": max_retries,
        "command": dto_to_dict(command),
        "result": {},
        "error": None,
        "retryable": False,
    }


def _incremental_command_from_metadata(metadata: dict[str, Any]) -> KnowledgeIncrementalRefreshCommand:
    command = dict(metadata.get("command") or {})
    return KnowledgeIncrementalRefreshCommand(
        target=command.get("target") or "prod",
        codes=list(command.get("codes") or []),
        stock_limit=int(command.get("stock_limit") or 500),
        news_limit=int(command.get("news_limit") or 20),
        dry_run=bool(command.get("dry_run") or False),
        request_id=command.get("request_id"),
        concurrency=command.get("concurrency"),
        rebuild_indexes=bool(command.get("rebuild_indexes", True)),
    )


def _incremental_task_dto(run: dict[str, Any]) -> KnowledgeIncrementalRefreshTaskDTO:
    metadata = dict(run.get("metadata") or {})
    command = dict(metadata.get("command") or {})
    return KnowledgeIncrementalRefreshTaskDTO(
        run_id=run["run_id"],
        adapter_name=run.get("adapter_name") or "financial",
        target=command.get("target") or "prod",
        task_type=str(metadata.get("task_type") or "financial_incremental_refresh"),
        status=str(run.get("status") or metadata.get("status") or "pending"),
        attempt=int(metadata.get("attempt") or 0),
        max_retries=int(metadata.get("max_retries") or 0),
        command=command,
        result=dict(metadata.get("result") or {}),
        error=metadata.get("error"),
    )


def _sum_step_metric(result: dict[str, Any], metric: str) -> int:
    total = 0
    for step in result.get("steps") or []:
        step_result = step.get("result") if isinstance(step, dict) else None
        if isinstance(step_result, dict):
            total += int(step_result.get(metric) or 0)
    return total


def _graph_time_window_for_plan(
    retrieval_plan,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    if retrieval_plan is None:
        return None
    time_range = retrieval_plan.time_range
    if time_range.start or time_range.end:
        start = _parse_plan_datetime(time_range.start) if time_range.start else None
        end = _parse_plan_datetime(time_range.end) if time_range.end else None
        if start is None and end is None:
            return None
        if start is None:
            start = datetime.min.replace(tzinfo=timezone.utc)
        if end is None:
            end = datetime.max.replace(tzinfo=timezone.utc)
        return start, end
    if not time_range.days:
        return None
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=time_range.days)
    return start, end


def _parse_plan_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _context_text(context: AnswerContext) -> str:
    parts: list[str] = []
    for hit in context.hits:
        snippet = hit.snippet.strip()
        if not snippet:
            continue
        parts.append(f"[{hit.source}:{hit.hit_type}] {hit.title}\n{snippet}")
    return "\n\n".join(parts)


def _save_retrieval_trace_snapshot(
    repository: KnowledgeRepository,
    *,
    query: str,
    options: RetrievalOptions,
    context: AnswerContext,
    strategy_name: str,
    strategy_version: str,
) -> None:
    save_snapshot = getattr(repository, "save_retrieval_trace_snapshot", None)
    if not callable(save_snapshot):
        return
    trace = context.trace
    snapshot = RetrievalTraceSnapshot(
        adapter_name=options.adapter_name,
        target=options.target,
        query=query,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        query_snapshot={
            "query": query,
            "adapter_name": options.adapter_name,
            "target": options.target,
            "mode": trace.mode,
            "query_anchor": trace.query_anchor,
            "routing_decision": trace.routing_decision,
        },
        recall_snapshot={
            "channels_enabled": trace.channels_enabled,
            "channels_used": trace.channels_used,
            "semantic_enabled": trace.semantic_enabled,
            "milvus_enabled": trace.milvus_enabled,
            "steps": [step.model_dump(mode="json") for step in trace.steps],
            "hit_count": len(context.hits),
            "hits": [_retrieval_hit_snapshot(hit) for hit in context.hits],
        },
        package_snapshot={
            "matched_nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "canonical_name": node.canonical_name,
                }
                for node in context.matched_nodes
            ],
            "matched_edges": [
                {
                    "edge_id": edge.edge_id,
                    "relation_type": edge.relation_type,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "evidence_ids": edge.evidence_ids,
                }
                for edge in context.matched_edges
            ],
        },
        ranking_snapshot={
            "controller_decisions": trace.controller_decisions,
            "raw_candidate_counts": [
                item.get("raw_candidate_count", 0)
                for item in trace.controller_decisions
                if isinstance(item, dict)
            ],
            "package_counts": [
                item.get("package_count", 0)
                for item in trace.controller_decisions
                if isinstance(item, dict)
            ],
            "judge_top_k": [
                item.get("judge_top_k", 0)
                for item in trace.controller_decisions
                if isinstance(item, dict)
            ],
        },
        judge_snapshot={
            "candidate_judgements": trace.candidate_judgements,
            "retrieval_metrics": trace.retrieval_metrics,
        },
        context_snapshot={
            "evidence_refs": _ordered_unique(
                evidence_id for hit in context.hits for evidence_id in hit.evidence_refs
            ),
            "budget_usage": context.budget_usage.model_dump(mode="json"),
            "context_text_preview": _clip_text(_context_text(context), 1200),
        },
        stop_snapshot={
            "working_set": trace.working_set,
            "warnings": trace.warnings,
            "stop_reason": (trace.working_set or {}).get("stop_reason") if isinstance(trace.working_set, dict) else None,
        },
    )
    try:
        save_snapshot(snapshot)
    except Exception as exc:  # pragma: no cover - defensive persistence guard
        logger.warning(
            "[kg_retrieval_quality] failed to save trace snapshot query=%r error=%s",
            _clip_text(query, 120),
            exc,
        )


def _retrieval_hit_snapshot(hit) -> dict[str, Any]:
    return {
        "id": hit.hit_id,
        "type": hit.hit_type,
        "title": _clip_text(hit.title, 160),
        "source": hit.source,
        "score": hit.score,
        "node_refs": hit.node_refs,
        "edge_refs": hit.edge_refs,
        "evidence_refs": hit.evidence_refs,
        "matched_terms": hit.matched_terms,
        "matched_fields": hit.matched_fields,
    }


def _incremental_step(name: str, result: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": "ok",
        "result": dto_to_dict(result),
    }


def _log_research_context_summary(
    command: KnowledgeResearchContextCommand,
    context: AnswerContext,
    evidence_refs: list[str],
) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    logger.info(
        "[kg_context] adapter=%s target=%s mode=%s query=%r hits=%d nodes=%s edges=%s "
        "evidence_refs=%s channels=%s warnings=%s",
        command.adapter_name,
        command.target,
        context.trace.mode,
        _clip_text(command.query, 160),
        len(context.hits),
        [
            {
                "id": node.node_id,
                "type": node.node_type,
                "name": _clip_text(node.canonical_name, 80),
            }
            for node in context.matched_nodes[:10]
        ],
        [
            {
                "id": edge.edge_id,
                "relation": edge.relation_type,
                "source": edge.source_node_id,
                "target": edge.target_node_id,
                "evidence": edge.evidence_ids[:3],
            }
            for edge in context.matched_edges[:10]
        ],
        evidence_refs[:10],
        context.trace.channels_used,
        context.trace.warnings,
    )


def _clip_text(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[: max_chars - 3] + "..."


def _bad_case_replay_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    channel_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    total_metrics = {
        "hits": 0,
        "evidence_refs": 0,
        "matched_nodes": 0,
        "matched_edges": 0,
        "forbidden_hits": 0,
    }
    context_precision_total = 0.0
    context_precision_count = 0
    for item in results:
        for channel in item.get("channels_used") or []:
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
        routing = item.get("routing_decision") or {}
        route = str(routing.get("final_mode") or item.get("retrieval_mode") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
        metrics = item.get("metrics") or {}
        for name in total_metrics:
            total_metrics[name] += int(metrics.get(name) or 0)
        retrieval_metrics = item.get("retrieval_metrics") or {}
        if retrieval_metrics.get("context_precision") is not None:
            context_precision_total += float(retrieval_metrics["context_precision"])
            context_precision_count += 1
    return {
        "pass_rate": (passed / total) if total else 0.0,
        "channel_coverage": channel_counts,
        "route_coverage": route_counts,
        "avg_hits": (total_metrics["hits"] / total) if total else 0.0,
        "avg_evidence_refs": (total_metrics["evidence_refs"] / total) if total else 0.0,
        "avg_matched_nodes": (total_metrics["matched_nodes"] / total) if total else 0.0,
        "avg_matched_edges": (total_metrics["matched_edges"] / total) if total else 0.0,
        "avg_forbidden_hits": (total_metrics["forbidden_hits"] / total) if total else 0.0,
        "avg_context_precision": (
            context_precision_total / context_precision_count
            if context_precision_count
            else 0.0
        ),
    }


def _candidate_judgement_summary(judgements: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    sources: dict[str, int] = {}
    for judgement in judgements or []:
        decision = str(judgement.get("decision") or "unknown")
        decisions[decision] = decisions.get(decision, 0) + 1
        source = str(judgement.get("judge_source") or "unknown")
        sources[source] = sources.get(source, 0) + 1
    return {
        "total": len(judgements or []),
        "decisions": decisions,
        "judge_sources": sources,
    }


def _match_financial_nodes(text: str, nodes: list[CompiledNode]) -> list[dict[str, Any]]:
    normalized = text.lower()
    candidates: list[dict[str, Any]] = []
    for node in nodes:
        terms = [node.canonical_name, *node.aliases, *[str(value) for value in node.external_ids.values()]]
        matched_terms = [term for term in terms if term and str(term).lower() in normalized]
        if not matched_terms:
            continue
        candidates.append(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "canonical_name": node.canonical_name,
                "matched_terms": _ordered_unique(str(term) for term in matched_terms),
                "status": node.status.value,
            }
        )
    return sorted(candidates, key=lambda item: (item["node_type"], item["canonical_name"], item["node_id"]))


def _ambiguous_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate["canonical_name"], []).append(candidate)
    return [
        {"canonical_name": name, "candidates": items}
        for name, items in by_name.items()
        if len({item["node_id"] for item in items}) > 1
    ]


def _resolve_seed_node_ids(entities: list[str | dict[str, Any]], nodes: list[CompiledNode]) -> list[str]:
    node_by_id = {node.node_id: node for node in nodes}
    result: list[str] = []
    for entity in entities:
        if isinstance(entity, str):
            if entity in node_by_id:
                result.append(entity)
                continue
            matches = _match_financial_nodes(entity, nodes)
            result.extend(item["node_id"] for item in matches)
            continue
        node_id = entity.get("node_id")
        if node_id:
            result.append(str(node_id))
            continue
        name = entity.get("name") or entity.get("canonical_name") or entity.get("code")
        if name:
            result.extend(item["node_id"] for item in _match_financial_nodes(str(name), nodes))
    return _ordered_unique(result)


def _as_financial_source(source_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("source_type") == source_type and "payload" in payload:
        return payload
    return {
        "source_type": source_type,
        "observed_at": (
            payload.get("observed_at")
            or payload.get("event_time")
            or payload.get("published_at")
            or datetime.now(timezone.utc).isoformat()
        ),
        "payload": payload,
    }


def _retrieval_tool_registry(
    runtime: HybridRetrievalRuntime,
    options: RetrievalOptions,
) -> RetrievalToolRegistry:
    return RetrievalToolRegistry(
        runtime,
        options,
        reranker_client=RerankerClient(
            base_url=settings.RERANKER_URL,
            timeout=settings.RERANKER_TIMEOUT,
            max_documents=settings.RERANKER_MAX_DOCUMENTS,
        ),
        reranker_max_documents=settings.RERANKER_MAX_DOCUMENTS,
        reranker_default_top_n=settings.RERANKER_DEFAULT_TOP_N,
    )


def _find_financial_paths(
    *,
    seed_node_ids: list[str],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    max_depth: int,
    limit: int,
) -> list[dict[str, Any]]:
    node_by_id = {node.node_id: node for node in nodes}
    edges_by_node: dict[str, list[CompiledEdge]] = {}
    for edge in edges:
        edges_by_node.setdefault(edge.source_node_id, []).append(edge)
        edges_by_node.setdefault(edge.target_node_id, []).append(edge)

    paths: list[dict[str, Any]] = []
    queue: list[tuple[str, list[CompiledEdge], list[str]]] = [
        (node_id, [], [node_id]) for node_id in seed_node_ids
    ]
    while queue and len(paths) < limit:
        node_id, path_edges, path_nodes = queue.pop(0)
        if path_edges:
            paths.append(_path_result(path_nodes, path_edges, node_by_id))
        if len(path_edges) >= max_depth:
            continue
        for edge in sorted(edges_by_node.get(node_id, []), key=lambda item: item.edge_id):
            next_node_id = edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
            if next_node_id in path_nodes:
                continue
            queue.append((next_node_id, [*path_edges, edge], [*path_nodes, next_node_id]))
    return paths[:limit]


def _path_result(
    node_ids: list[str],
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
) -> dict[str, Any]:
    hard_edges = [
        edge
        for edge in edges
        if can_hard_consume(edge.relation_type, edge.confidence_label, edge.status)
    ]
    explanation_edges = [edge for edge in edges if edge not in hard_edges]
    confidence = 1.0
    for edge in edges:
        confidence *= edge.confidence_score
    return {
        "path": [
            {
                "node_id": node_id,
                "node_type": node_by_id[node_id].node_type if node_id in node_by_id else "",
                "canonical_name": node_by_id[node_id].canonical_name if node_id in node_by_id else node_id,
            }
            for node_id in node_ids
        ],
        "path_confidence": round(confidence, 6),
        "hard_score_edges": hard_edges,
        "explanation_edges": explanation_edges,
        "evidence_refs": _ordered_unique(evidence_id for edge in edges for evidence_id in edge.evidence_ids),
        "unsupported_assumptions": [
            edge.edge_id for edge in explanation_edges if not edge.evidence_ids or edge.status.value != "active"
        ],
    }


def _knowledge_command_metadata(command: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "command": command.__class__.__name__,
        "adapter_name": getattr(command, "adapter_name", "-"),
        "target": getattr(command, "target", "-"),
        "request_id": getattr(command, "request_id", None),
        "dry_run": getattr(command, "dry_run", None),
    }
    if hasattr(command, "records"):
        records = getattr(command, "records") or []
        metadata["records"] = len(records)
        metadata["source_samples"] = [
            {
                "source_type": getattr(item, "source_type", None),
                "source_id": getattr(item, "source_id", None),
                "title": (getattr(item, "title", "") or "")[:120],
            }
            for item in records[:5]
        ]
    if hasattr(command, "query"):
        metadata["query"] = getattr(command, "query")
    if hasattr(command, "retrieval_mode"):
        metadata["retrieval_mode"] = getattr(command, "retrieval_mode")
    if hasattr(command, "index_types"):
        metadata["index_types"] = list(getattr(command, "index_types") or [])
    if hasattr(command, "scope"):
        metadata["scope"] = getattr(command, "scope")
    return {key: value for key, value in metadata.items() if value is not None}


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
