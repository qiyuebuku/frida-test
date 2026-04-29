"""Application service entry point for knowledge use cases."""

import logging
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
    KnowledgeQualityScanCommand,
    KnowledgeQualityScanResultDTO,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildIndexesResultDTO,
    KnowledgeRebuildWikiCommand,
    KnowledgeRebuildWikiResultDTO,
    KnowledgeResearchContextCommand,
    KnowledgeResearchContextDTO,
    KnowledgeReviewActionCommand,
    dto_to_dict,
)
from src.application.services.financial_stock_bootstrap import (
    build_stock_basics_records_from_sources,
)
from src.application.services.financial_news_projection import (
    build_news_records_from_sources,
)
from src.application.services.knowledge_adapter_registry import get_adapter, list_adapters
from src.application.services.llm_agentic_retrieval_strategy import LLMAgenticRetrievalStrategy
from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalController,
)
from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge.compiler import KnowledgeCompiler
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
    _inherit_evidence_scores,
)
from src.domain.knowledge.retrieval_plan_executor import RetrievalPlanExecutor
from src.domain.knowledge.retrieval_eval import (
    RetrievalBadCase,
    evaluate_retrieval_bad_case,
)
from src.domain.knowledge.retrieval_trace_replay import replay_retrieval_trace
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.schemas import CompileResult, CompiledEdge, CompiledNode
from src.domain.knowledge.wiki import KnowledgeWikiBuilder, WikiBuildResult
from src.domain.knowledge_adapters.financial.consumption import can_hard_consume
from src.domain.knowledge_adapters.financial.query_planner import FinancialQueryPlanner
from src.infrastructure.config import settings
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
)

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Coordinates knowledge use cases without owning persistence details."""

    def __init__(self, repository: KnowledgeRepository | None = None):
        self.repository = repository
        self.compiler = KnowledgeCompiler(
            repository=repository,
            concurrency=settings.CLAUDE_PROXY_MAX_CONCURRENCY,
        )
        self.wiki_builder = KnowledgeWikiBuilder()
        self.quality_scanner = KnowledgeQualityScanner()

    async def health(self) -> KnowledgeHealthDTO:
        database = "not_configured"
        status = "degraded"
        if self.repository is not None:
            try:
                # A cheap read is enough to verify table and connection availability.
                self.repository.list_nodes("financial")
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
                "rebuild_wiki",
                "rebuild_indexes",
                "research_context",
                "quality_scan",
                "reviews",
            ],
        )

    async def compile_kg(self, command: KnowledgeCompileCommand) -> KnowledgeCompileResultDTO:
        adapter = get_adapter(command.adapter_name)
        compiler = KnowledgeCompiler(
            repository=None if command.dry_run else self.repository,
            concurrency=_compile_concurrency(command),
        )
        result = await compiler.compile(adapter, command.records)
        return KnowledgeCompileResultDTO(
            adapter_name=result.adapter_name,
            run_id=result.run_id,
            nodes=len(result.nodes),
            edges=len(result.edges),
            evidence=len(result.evidence),
            failed_records=len(result.failed_records),
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

    async def rebuild_wiki_for(
        self,
        command: KnowledgeRebuildWikiCommand,
    ) -> KnowledgeRebuildWikiResultDTO:
        _ensure_scope_supported(command.scope)
        get_adapter(command.adapter_name)
        result = await self.rebuild_wiki(command.adapter_name)
        return KnowledgeRebuildWikiResultDTO(
            adapter_name=command.adapter_name,
            run_id=f"kg_run:rebuild_wiki:{uuid4()}",
            pages=len(result.pages),
            issues=len(result.issues),
            warnings=[dto_to_dict(item) for item in result.issues],
        )

    async def rebuild_indexes_for(
        self,
        command: KnowledgeRebuildIndexesCommand,
    ) -> KnowledgeRebuildIndexesResultDTO:
        _ensure_scope_supported(command.scope)
        get_adapter(command.adapter_name)
        repository = self._require_repository()
        allowed = {"graph_adjacency", "evidence_chunks", "hybrid_chunks", "vector_chunks"}
        unknown = sorted(set(command.index_types) - allowed)
        if unknown:
            raise ValueError(f"unsupported index_types: {', '.join(unknown)}")

        result = {"graph_adjacency": 0, "evidence_chunks": 0, "hybrid_chunks": 0}
        warnings: list[str] = []
        if "graph_adjacency" in command.index_types:
            result["graph_adjacency"] = repository.rebuild_graph_adjacency(command.adapter_name)
        if "evidence_chunks" in command.index_types:
            result["evidence_chunks"] = repository.rebuild_evidence_chunks(command.adapter_name)
        if {"hybrid_chunks", "vector_chunks"} & set(command.index_types):
            chunks = repository.list_evidence_chunks(command.adapter_name)
            result["hybrid_chunks"] = await MilvusSemanticHybridRetriever().rebuild_index(
                adapter_name=command.adapter_name,
                target=command.target,
                chunks=chunks,
            )
        return KnowledgeRebuildIndexesResultDTO(
            adapter_name=command.adapter_name,
            run_id=f"kg_run:rebuild_indexes:{uuid4()}",
            graph_adjacency=result["graph_adjacency"],
            evidence_chunks=result["evidence_chunks"],
            hybrid_chunks=result["hybrid_chunks"],
            warnings=warnings,
        )

    async def build_research_context_for(
        self,
        command: KnowledgeResearchContextCommand,
    ) -> KnowledgeResearchContextDTO:
        get_adapter(command.adapter_name)
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
            and command.retrieval_mode == "deterministic_plan"
            else None
        )
        graph_time_window = _graph_time_window_for_plan(retrieval_plan)
        if graph_time_window is not None:
            options = options.model_copy(
                update={
                    "graph_time_start": graph_time_window[0],
                    "graph_time_end": graph_time_window[1],
                }
            )
        context = await self._build_research_answer_context(
            command.query,
            options,
            retrieval_plan=retrieval_plan,
            retrieval_mode=command.retrieval_mode,
        )
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
            agentic_enabled=context.trace.agentic_enabled,
            planner_enabled=retrieval_plan is not None,
            retrieval_plan=dto_to_dict(retrieval_plan) if retrieval_plan is not None else {},
            retrieval_trace=trace,
            warnings=list(context.trace.warnings),
        )

    async def quality_scan_for(
        self,
        command: KnowledgeQualityScanCommand,
    ) -> KnowledgeQualityScanResultDTO:
        get_adapter(command.adapter_name)
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

    async def replay_research_context_bad_cases(
        self,
        command: KnowledgeBadCaseReplayCommand,
    ) -> KnowledgeBadCaseReplayResultDTO:
        get_adapter(command.adapter_name)
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
                ),
                evidence_refs=context.evidence_refs,
                hit_titles=[hit.get("title", "") for hit in context.hits],
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
            registry=RetrievalToolRegistry(runtime, options),
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
            agentic_enabled=context.trace.agentic_enabled,
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
        return await self.compiler.compile(adapter, raw_records)

    async def search(self, *args, **kwargs):
        raise NotImplementedError("Knowledge search is planned for a later step")

    async def rebuild_wiki(self, adapter_name: str) -> WikiBuildResult:
        repository = self._require_repository()
        nodes = repository.list_nodes(adapter_name)
        result = self.wiki_builder.build(
            adapter_name=adapter_name,
            version=_latest_version(nodes),
            nodes=nodes,
            edges=repository.list_edges(adapter_name),
            evidence=repository.list_evidence(adapter_name),
        )
        errors = [issue for issue in result.issues if issue.severity.value == "error"]
        if errors:
            raise ValueError(f"wiki lint failed: {len(errors)} error(s)")
        repository.rebuild_wiki_pages(adapter_name, result.pages)
        return result

    async def rebuild_indexes(self, adapter_name: str) -> dict[str, int]:
        repository = self._require_repository()
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
        if retrieval_mode == "agentic_arag":
            registry = RetrievalToolRegistry(runtime, options)
            agentic = await AgenticRetrievalController(
                registry,
                _agentic_retrieval_strategy(),
                AgenticRetrievalConstraints(max_hits=options.max_hits),
            ).run(query)
            hits = list(agentic.hits)
            trace = agentic.trace
            if agentic.evidence_refs and "chunk_read" not in trace.channels_used:
                chunk_result = await registry.execute(
                    RetrievalToolCall(
                        tool="chunk_read",
                        evidence_ids=agentic.evidence_refs,
                        limit=options.evidence_limit,
                    )
                )
                chunk_hits = _inherit_evidence_scores(chunk_result.hits, hits)
                hits.extend(chunk_hits)
                trace = trace.model_copy(
                    update={
                        "channels_used": _ordered_unique([*trace.channels_used, "chunk_read"]),
                        "steps": [*trace.steps, chunk_result.step],
                    }
                )
            return runtime.build_answer_context_from_hits(
                query=query,
                hits=hits,
                options=options,
                trace=trace,
            )
        if retrieval_plan is None:
            return await runtime.build_answer_context_async(query, options)
        execution = await RetrievalPlanExecutor(
            RetrievalToolRegistry(runtime, options)
        ).execute(query=query, plan=retrieval_plan)
        return runtime.build_answer_context_from_hits(
            query=query,
            hits=execution.hits,
            options=options,
            trace=execution.trace,
        )

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
        return await self.compiler.compile(get_adapter("financial"), [_as_financial_source("l1_events", event_record)])

    async def record_kg_feedback(self, feedback_record: dict[str, Any]) -> CompileResult:
        return await self.compiler.compile(
            get_adapter("financial"),
            [_as_financial_source("feedback_records", feedback_record)],
        )

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
        report = self.quality_scanner.scan(
            adapter_name=adapter_name,
            nodes=repository.list_nodes(adapter_name),
            edges=repository.list_edges(adapter_name),
            evidence=repository.list_evidence(adapter_name),
            wiki_pages=repository.list_wiki_pages(adapter_name),
        )
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

    def _require_repository(self) -> KnowledgeRepository:
        if self.repository is None:
            raise RuntimeError("Knowledge repository is required for this use case")
        return self.repository


Target = Literal["prod", "test"]


def create_knowledge_service(target: Target | None = None) -> KnowledgeService:
    from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
        KnowledgeRepositoryImpl,
    )

    return KnowledgeService(repository=KnowledgeRepositoryImpl(target=target))


def _latest_version(nodes) -> str:
    versions = sorted({node.version for node in nodes})
    return versions[-1] if versions else "v1"


def _ensure_scope_supported(scope: str) -> None:
    if scope != "all":
        raise ValueError("scope 第一版仅支持 all")


def _compile_concurrency(command: KnowledgeCompileCommand) -> int:
    if command.concurrency is not None:
        return max(1, int(command.concurrency))
    return max(1, int(settings.CLAUDE_PROXY_MAX_CONCURRENCY))


def _semantic_hybrid_retriever():
    return MilvusSemanticHybridRetriever()


def _agentic_retrieval_strategy():
    return LLMAgenticRetrievalStrategy()


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


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
