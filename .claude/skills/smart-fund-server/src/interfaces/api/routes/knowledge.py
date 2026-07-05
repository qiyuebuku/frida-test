"""Knowledge graph API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.dto.knowledge_dto import (
    KnowledgeAgentExpandCommand,
    KnowledgeAgentOpenCommand,
    KnowledgeAgentRefineCommand,
    KnowledgeAgentSearchCommand,
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
    retrieval_mode: Literal["auto", "deterministic_plan", "agentic_arag", "openai_agents_arag"] = Field(
        "auto",
        description="检索模式；auto 会自动路由到 deterministic_plan 或 agentic_arag；openai_agents_arag 为 Agent SDK 方案灰度入口",
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


class KGAgentSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Agent 的自然语言检索意图")
    adapter_name: str = Field("financial")
    target: Target = "prod"
    session_id: str = Field(..., min_length=1, description="Agent 检索会话 ID；同一任务的 search/open/expand/refine 必须复用同一个值")
    limit: int = Field(8, ge=1, le=50, description="最终返回给 Agent 的 evidence package 数量")
    candidate_limit: int | None = Field(None, ge=1, le=300, description="内部候选池规模")
    sort: Literal["relevance", "freshness", "evidence_strength", "diversity"] = "relevance"
    time_range: KGAgentTimeRangeRequest | None = None
    max_chars: int = Field(8000, ge=1000, le=40000)
    focus_aspects: list[str] = Field(default_factory=list, description="Agent 显式关注的检索侧面")


class KGAgentOpenRequest(BaseModel):
    target_ids: list[str] = Field(..., min_length=1)
    query: str | None = Field(None, description="可选原始查询；提供后用于对邻接上下文做 query-aware 排序")
    adapter_name: str = Field("financial")
    target: Target = "prod"
    session_id: str = Field(..., min_length=1, description="Agent 检索会话 ID；用于串联上下文和过滤重复结果")
    include_neighbors: bool = True
    limit: int = Field(12, ge=1, le=100)
    max_chars: int = Field(12000, ge=1000, le=60000)


class KGAgentExpandRequest(BaseModel):
    target_id: str = Field(..., min_length=1)
    query: str | None = Field(None, description="可选原始查询；提供后用于对展开结果做 query-aware 排序")
    adapter_name: str = Field("financial")
    target: Target = "prod"
    session_id: str = Field(..., min_length=1, description="Agent 检索会话 ID；用于串联上下文和过滤重复结果")
    direction: Literal["supporting_cards", "supporting_chunks", "neighbors", "auto"] = "auto"
    limit: int = Field(20, ge=1, le=150)
    max_chars: int = Field(12000, ge=1000, le=60000)


class KGAgentRefineRequest(KGAgentSearchRequest):
    previous_context: dict[str, Any] = Field(default_factory=dict)
    refinement: str = ""


@router.get("/health", summary="知识图谱健康检查")
async def knowledge_health(target: Target = Query("prod")):
    service = create_knowledge_service(target=target)
    return (await service.health()).to_dict()


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


@router.post("/agent/search", summary="Agent 知识检索")
async def agent_search(req: KGAgentSearchRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.agent_search(
            KnowledgeAgentSearchCommand(
                query=req.query,
                adapter_name=req.adapter_name,
                target=req.target,
                session_id=req.session_id,
                limit=req.limit,
                candidate_limit=req.candidate_limit,
                sort=req.sort,
                time_start=_parse_api_datetime(req.time_range.start) if req.time_range else None,
                time_end=_parse_api_datetime(req.time_range.end) if req.time_range else None,
                max_chars=req.max_chars,
                focus_aspects=req.focus_aspects,
            )
        )
    )


@router.post("/agent/open", summary="Agent 打开检索命中上下文")
async def agent_open(req: KGAgentOpenRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.agent_open(
            KnowledgeAgentOpenCommand(
                target_ids=req.target_ids,
                query=req.query,
                adapter_name=req.adapter_name,
                target=req.target,
                session_id=req.session_id,
                include_neighbors=req.include_neighbors,
                limit=req.limit,
                max_chars=req.max_chars,
            )
        )
    )


@router.post("/agent/expand", summary="Agent 展开 community/card/chunk")
async def agent_expand(req: KGAgentExpandRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.agent_expand(
            KnowledgeAgentExpandCommand(
                target_id=req.target_id,
                query=req.query,
                adapter_name=req.adapter_name,
                target=req.target,
                session_id=req.session_id,
                direction=req.direction,
                limit=req.limit,
                max_chars=req.max_chars,
            )
        )
    )


@router.post("/agent/refine", summary="Agent 基于上一轮检索继续 refine")
async def agent_refine(req: KGAgentRefineRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.agent_refine(
            KnowledgeAgentRefineCommand(
                query=req.query,
                adapter_name=req.adapter_name,
                target=req.target,
                session_id=req.session_id,
                limit=req.limit,
                candidate_limit=req.candidate_limit,
                sort=req.sort,
                time_start=_parse_api_datetime(req.time_range.start) if req.time_range else None,
                time_end=_parse_api_datetime(req.time_range.end) if req.time_range else None,
                max_chars=req.max_chars,
                focus_aspects=req.focus_aspects,
                previous_context=req.previous_context,
                refinement=req.refinement,
            )
        )
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


async def _run_incremental_refresh_task(run_id: str, target: Target) -> None:
    await create_knowledge_service(target=target).run_financial_incremental_refresh_task(run_id)


async def _retry_incremental_refresh_task(run_id: str, target: Target) -> None:
    await create_knowledge_service(target=target).retry_financial_incremental_refresh_task(run_id)
