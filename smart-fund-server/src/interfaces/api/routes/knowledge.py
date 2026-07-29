"""Knowledge graph API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.application.dto.knowledge_dto import (
    KnowledgeCompileCommand,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeQualityScanCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeResearchContextCommand,
    KnowledgeReviewActionCommand,
    KnowledgeSourceProjectionCommand,
    dto_to_dict,
)
from src.application.services.knowledge_adapter_registry import AdapterNotFoundError
from src.application.services.knowledge_service import create_knowledge_service
from src.application.services.relation_graph_explorer_service import (
    create_relation_graph_explorer_service,
)
from src.application.services.relation_graph_agent_retrieval_service import (
    create_relation_graph_agent_retrieval_service,
)
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_flush,
    langfuse_propagation_context,
)

router = APIRouter(prefix="/api/kg", tags=["知识图谱"])

Target = Literal["prod", "test"]


class KGCompileRequest(BaseModel):
    adapter_name: str = Field("financial", description="adapter 名称")
    records: list[dict[str, Any]] = Field(..., min_length=1, description="领域原始记录")
    target: Target = Field("prod", description="数据库目标")
    dry_run: bool = Field(False, description="只编译不写入")
    request_id: str | None = Field(None, description="调用方幂等 ID")
    concurrency: int | None = Field(None, ge=1, le=20, description="编译并发数，默认跟随 LLM proxy 配置")


class KGSourceProjectionRequest(BaseModel):
    target: Target = Field("prod", description="数据库目标")
    sources: list[str] | None = Field(None, description="为空时读取五张核心表")
    codes: list[str] = Field(default_factory=list, description="可选股票代码过滤，当前仅作用于 ft_news")
    limit: int = Field(100, ge=1, le=5000, description="每个来源读取数量上限")
    include_skipped: bool = Field(True, description="是否返回跳过行明细")


class KGRebuildIndexesRequest(BaseModel):
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    index_types: list[str] = Field(
        default_factory=lambda: ["graph_adjacency", "evidence_chunks"],
        description="索引类型：graph_adjacency / evidence_chunks / hybrid_chunks",
    )
    scope: str = Field("all", description="索引重建范围：all 或 projection:<projection_name>")


class KGResearchContextRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询文本")
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    retrieval_mode: Literal["auto", "deterministic_plan"] = Field(
        "auto",
        description="检索模式；auto 和 deterministic_plan 均使用确定性投研上下文检索",
    )
    graph_depth: int = Field(3, ge=1, le=4)
    graph_limit: int = Field(20, ge=1, le=100)
    wiki_limit: int = Field(10, ge=1, le=100)
    evidence_limit: int = Field(20, ge=1, le=100)
    max_chars: int = Field(5000, ge=500, le=20000)


class KGQualityScanRequest(BaseModel):
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    persist_review: bool = Field(True, description="是否写入复核队列")


class KGResolveFinancialEntitiesRequest(BaseModel):
    text: str = Field(..., min_length=1)
    target: Target = "prod"
    limit: int = Field(20, ge=1, le=100)


class KGFinancialIncrementalRefreshRequest(BaseModel):
    target: Target = "prod"
    codes: list[str] = Field(default_factory=list)
    stock_limit: int = Field(500, ge=1, le=5000)
    news_limit: int = Field(20, ge=1, le=5000)
    dry_run: bool = False
    request_id: str | None = None
    concurrency: int | None = Field(1, ge=1, le=20)
    rebuild_indexes: bool = True


class KGFinancialIncrementalRefreshTaskRequest(KGFinancialIncrementalRefreshRequest):
    max_retries: int = Field(1, ge=0, le=5)


class KGFinancialPathsRequest(BaseModel):
    seed_node_ids: list[str] = Field(..., min_length=1)
    target: Target = "prod"
    max_depth: int = Field(3, ge=1, le=4)
    limit: int = Field(20, ge=1, le=100)


class KGReviewActionRequest(BaseModel):
    action: str = Field(..., min_length=1)
    target: Target = "prod"
    operator: str | None = None
    reason: str | None = None


class KGAgentTimeRangeRequest(BaseModel):
    start: str | None = None
    end: str | None = None


class KGRelationGraphSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = Field(
        None,
        description="可选 Agent 会话 ID，用于串联 Langfuse Trace",
    )
    seed_limit: int = Field(8, ge=1, le=30)
    candidate_limit: int = Field(32, ge=1, le=100)
    time_range: KGAgentTimeRangeRequest | None = None


class KGCardExpandRequest(BaseModel):
    card_ids: list[str] = Field(..., min_length=1, max_length=30)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = None
    hop_limit: int = Field(1, ge=1, le=2)
    node_limit: int = Field(40, ge=1, le=100)
    edge_limit: int = Field(80, ge=1, le=200)
    relation_kinds: list[str] = Field(default_factory=list)
    decision_classes: list[Literal["observed", "inferred"]] = Field(
        default_factory=lambda: ["observed", "inferred"]
    )
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)


class KGCommunityExpandRequest(BaseModel):
    community_ids: list[str] = Field(..., min_length=1, max_length=20)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = None
    hop_limit: int = Field(1, ge=1, le=2)
    community_limit: int = Field(30, ge=1, le=100)
    relation_limit: int = Field(60, ge=1, le=200)
    relation_kinds: list[str] = Field(default_factory=list)


class KGCardOpenRequest(BaseModel):
    card_ids: list[str] = Field(..., min_length=1, max_length=30)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = None
    incident_edge_limit: int = Field(40, ge=1, le=200)


class KGEdgeOpenRequest(BaseModel):
    edge_ids: list[str] = Field(..., min_length=1, max_length=50)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = None


class KGCommunityOpenRequest(BaseModel):
    community_ids: list[str] = Field(..., min_length=1, max_length=20)
    adapter_name: str = Field("financial", min_length=1)
    target: Target = "prod"
    session_id: str | None = None
    member_limit: int = Field(40, ge=1, le=100)
    edge_limit: int = Field(80, ge=1, le=200)


@router.get("/health", summary="知识图谱健康检查")
async def knowledge_health(target: Target = Query("prod")):
    service = create_knowledge_service(target=target)
    return (await service.health()).to_dict()


@router.get("/graph-viewer", include_in_schema=False)
async def graph_viewer(request: Request):
    query = request.url.query
    suffix = f"?{query}" if query else ""
    return RedirectResponse(url=f"/static/kg_graph_explorer.html{suffix}")


@router.get("/graph-communities", summary="查询关系图 Community")
async def list_graph_communities(
    adapter_name: str = Query("financial", min_length=1),
    graph_status: str = Query("active", min_length=1),
    query: str = Query(""),
    sort_by: Literal[
        "edge_count",
        "card_count",
        "relation_count",
        "updated_at",
    ] = Query(
        "updated_at"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    target: Target = Query("prod"),
):
    service = create_relation_graph_explorer_service(target=target)
    return await _call(
        service.list_communities(
            adapter_name=adapter_name,
            graph_status=graph_status,
            query=query,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/graph-community-overview",
    summary="查询平行 Graph Community 关系概览",
)
async def get_graph_community_overview(
    adapter_name: str = Query("financial", min_length=1),
    graph_status: str = Query("active", min_length=1),
    query: str = Query(""),
    relation_kind: str = Query(""),
    sort_by: Literal[
        "edge_count",
        "card_count",
        "relation_count",
        "updated_at",
    ] = Query("relation_count"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(
        0,
        ge=0,
        le=5000,
        description="0 表示返回全部 Community；正数表示分页上限",
    ),
    offset: int = Query(0, ge=0),
    target: Target = Query("prod"),
):
    service = create_relation_graph_explorer_service(target=target)
    return await _call(
        service.get_overview(
            adapter_name=adapter_name,
            graph_status=graph_status,
            query=query,
            relation_kind=relation_kind,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/graph-communities/{community_id}",
    summary="查询关系图 Community 详情",
)
async def get_graph_community(
    community_id: str,
    target: Target = Query("prod"),
):
    service = create_relation_graph_explorer_service(target=target)
    result = await _call(service.get_community(community_id=community_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Graph Community 不存在")
    return result


@router.get(
    "/graph-community-relations/{relation_id}",
    summary="查询跨 Community 关系及底层 Card Edge",
)
async def get_graph_community_relation(
    relation_id: str,
    adapter_name: str = Query("financial", min_length=1),
    target: Target = Query("prod"),
):
    service = create_relation_graph_explorer_service(target=target)
    result = await _call(
        service.get_community_relation(
            relation_id=relation_id,
            adapter_name=adapter_name,
        )
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Community Relation 不存在")
    return result


@router.post("/compile", summary="编译知识图谱")
async def compile_kg(req: KGCompileRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.compile_kg(
            KnowledgeCompileCommand(
                adapter_name=req.adapter_name,
                records=req.records,
                target=req.target,
                dry_run=req.dry_run,
                request_id=req.request_id,
                concurrency=req.concurrency,
            )
        )
    )


@router.post("/project-sources", summary="投影业务表为 Source Record")
async def project_sources(req: KGSourceProjectionRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.project_sources(
            KnowledgeSourceProjectionCommand(
                target=req.target,
                sources=req.sources,
                codes=req.codes,
                limit=req.limit,
                include_skipped=req.include_skipped,
            )
        )
    )


@router.post("/rebuild-indexes", summary="重建知识图谱索引")
async def rebuild_indexes(req: KGRebuildIndexesRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.rebuild_indexes_for(
            KnowledgeRebuildIndexesCommand(
                adapter_name=req.adapter_name,
                target=req.target,
                index_types=req.index_types,
                scope=req.scope,
            )
        )
    )


@router.post("/research-context", summary="构建投研上下文")
async def research_context(req: KGResearchContextRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.build_research_context_for(
            KnowledgeResearchContextCommand(
                adapter_name=req.adapter_name,
                target=req.target,
                query=req.query,
                retrieval_mode=req.retrieval_mode,
                graph_depth=req.graph_depth,
                graph_limit=req.graph_limit,
                wiki_limit=req.wiki_limit,
                evidence_limit=req.evidence_limit,
                max_chars=req.max_chars,
            )
        )
    )


@router.post(
    "/agent/relation-graph/search",
    summary="Agent 语义检索关系图 Card",
)
async def relation_graph_search(req: KGRelationGraphSearchRequest):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "search",
        service.search(
            query=req.query,
            adapter_name=req.adapter_name,
            seed_limit=req.seed_limit,
            candidate_limit=req.candidate_limit,
            time_start=(
                _parse_api_datetime(req.time_range.start)
                if req.time_range
                else None
            ),
            time_end=(
                _parse_api_datetime(req.time_range.end)
                if req.time_range
                else None
            ),
        ),
    )


@router.post(
    "/agent/relation-graph/cards/expand",
    summary="Agent 沿 Card Edge 展开关系图",
)
async def relation_graph_card_expand(req: KGCardExpandRequest):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "card_expand",
        service.expand_cards(
            card_ids=req.card_ids,
            adapter_name=req.adapter_name,
            hop_limit=req.hop_limit,
            node_limit=req.node_limit,
            edge_limit=req.edge_limit,
            relation_kinds=req.relation_kinds,
            decision_classes=list(req.decision_classes),
            min_confidence=req.min_confidence,
        ),
    )


@router.post(
    "/agent/relation-graph/communities/expand",
    summary="Agent 沿跨 Community 关系展开",
)
async def relation_graph_community_expand(
    req: KGCommunityExpandRequest,
):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "community_expand",
        service.expand_communities(
            community_ids=req.community_ids,
            adapter_name=req.adapter_name,
            hop_limit=req.hop_limit,
            community_limit=req.community_limit,
            relation_limit=req.relation_limit,
            relation_kinds=req.relation_kinds,
        ),
    )


@router.post(
    "/agent/relation-graph/cards/open",
    summary="Agent 精确打开 Card 和焦点原文",
)
async def relation_graph_card_open(req: KGCardOpenRequest):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "card_open",
        service.open_cards(
            card_ids=req.card_ids,
            adapter_name=req.adapter_name,
            incident_edge_limit=req.incident_edge_limit,
        ),
    )


@router.post(
    "/agent/relation-graph/edges/open",
    summary="Agent 精确打开 Card Edge 和关系证据",
)
async def relation_graph_edge_open(req: KGEdgeOpenRequest):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "edge_open",
        service.open_edges(
            edge_ids=req.edge_ids,
            adapter_name=req.adapter_name,
        ),
    )


@router.post(
    "/agent/relation-graph/communities/open",
    summary="Agent 精确打开 Community 内部结构",
)
async def relation_graph_community_open(req: KGCommunityOpenRequest):
    service = create_relation_graph_agent_retrieval_service(
        target=req.target
    )
    return await _relation_graph_call(
        req,
        "community_open",
        service.open_communities(
            community_ids=req.community_ids,
            adapter_name=req.adapter_name,
            member_limit=req.member_limit,
            edge_limit=req.edge_limit,
        ),
    )


@router.post("/quality-scan", summary="知识图谱质量扫描")
async def quality_scan(req: KGQualityScanRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.quality_scan_for(
            KnowledgeQualityScanCommand(
                adapter_name=req.adapter_name,
                target=req.target,
                persist_review=req.persist_review,
            )
        )
    )


@router.post("/financial/resolve-entities", summary="金融实体解析")
async def resolve_financial_entities(req: KGResolveFinancialEntitiesRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(service.resolve_financial_entities(req.text, limit=req.limit))


@router.post("/financial/incremental-refresh", summary="金融知识图谱增量刷新")
async def financial_incremental_refresh(req: KGFinancialIncrementalRefreshRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.refresh_financial_incremental(
            KnowledgeIncrementalRefreshCommand(
                target=req.target,
                codes=req.codes,
                stock_limit=req.stock_limit,
                news_limit=req.news_limit,
                dry_run=req.dry_run,
                request_id=req.request_id,
                concurrency=req.concurrency,
                rebuild_indexes=req.rebuild_indexes,
            )
        )
    )


@router.post("/financial/incremental-refresh/tasks", summary="提交金融知识图谱增量刷新后台任务")
async def enqueue_financial_incremental_refresh_task(
    req: KGFinancialIncrementalRefreshTaskRequest,
    background_tasks: BackgroundTasks,
):
    service = create_knowledge_service(target=req.target)
    task = await _call(
        service.enqueue_financial_incremental_refresh_task(
            KnowledgeIncrementalRefreshCommand(
                target=req.target,
                codes=req.codes,
                stock_limit=req.stock_limit,
                news_limit=req.news_limit,
                dry_run=req.dry_run,
                request_id=req.request_id,
                concurrency=req.concurrency,
                rebuild_indexes=req.rebuild_indexes,
            ),
            max_retries=req.max_retries,
        )
    )
    background_tasks.add_task(_run_incremental_refresh_task, task["run_id"], req.target)
    return task


@router.get("/financial/incremental-refresh/tasks/{run_id}", summary="查询金融知识图谱增量刷新后台任务")
async def get_financial_incremental_refresh_task(run_id: str, target: Target = Query("prod")):
    service = create_knowledge_service(target=target)
    return await _call(service.get_incremental_refresh_task(run_id))


@router.post("/financial/incremental-refresh/tasks/{run_id}/retry", summary="重试金融知识图谱增量刷新后台任务")
async def retry_financial_incremental_refresh_task(
    run_id: str,
    background_tasks: BackgroundTasks,
    target: Target = Query("prod"),
):
    service = create_knowledge_service(target=target)
    task = await _call(service.get_incremental_refresh_task(run_id))
    background_tasks.add_task(_retry_incremental_refresh_task, run_id, target)
    return {**task, "status": "retry_submitted"}


@router.post("/financial/paths", summary="金融影响路径查询")
async def financial_paths(req: KGFinancialPathsRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.find_financial_paths(
            seed_node_ids=req.seed_node_ids,
            max_depth=req.max_depth,
            limit=req.limit,
        )
    )


@router.get("/reviews", summary="查看知识图谱复核队列")
async def list_reviews(
    status: str | None = Query("open"),
    target: Target = Query("prod"),
):
    service = create_knowledge_service(target=target)
    return await _call(service.list_reviews_for(status=status))


@router.post("/reviews/{review_id}/actions", summary="执行知识图谱复核动作")
async def apply_review_action(review_id: str, req: KGReviewActionRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.apply_review_action_for(
            KnowledgeReviewActionCommand(
                review_id=review_id,
                action=req.action,
                target=req.target,
                operator=req.operator,
                reason=req.reason,
            )
        )
    )


def _parse_api_datetime(value: str | None) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"time_range 时间格式无效: {value}") from exc


async def _call(coro):
    try:
        result = await coro
        return result.to_dict() if hasattr(result, "to_dict") else dto_to_dict(result)
    except AdapterNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"知识图谱服务异常: {exc}") from exc


async def _relation_graph_call(req, operation: str, coro):
    metadata = {
        "operation": operation,
        "adapter_name": req.adapter_name,
        "target": req.target,
    }
    try:
        with langfuse_propagation_context(
            trace_name=f"kg.relation_graph_agent.{operation}",
            session_id=req.session_id,
            tags=["kg", "agent-tool", "relation-graph", operation],
            metadata=metadata,
        ):
            return await _call(coro)
    finally:
        langfuse_flush()


async def _run_incremental_refresh_task(run_id: str, target: Target) -> None:
    await create_knowledge_service(target=target).run_financial_incremental_refresh_task(run_id)


async def _retry_incremental_refresh_task(run_id: str, target: Target) -> None:
    await create_knowledge_service(target=target).retry_financial_incremental_refresh_task(run_id)
