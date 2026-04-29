"""Knowledge graph API routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.dto.knowledge_dto import (
    KnowledgeCompileCommand,
    KnowledgeQualityScanCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildWikiCommand,
    KnowledgeResearchContextCommand,
    KnowledgeReviewActionCommand,
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


class KGRebuildWikiRequest(BaseModel):
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    scope: str = Field("all", description="第一版仅支持 all")


class KGRebuildIndexesRequest(BaseModel):
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    index_types: list[str] = Field(
        default_factory=lambda: ["graph_adjacency", "evidence_chunks"],
        description="索引类型：graph_adjacency / evidence_chunks / hybrid_chunks",
    )
    scope: str = Field("all", description="第一版仅支持 all")


class KGResearchContextRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询文本")
    adapter_name: str = Field("financial", description="adapter 名称")
    target: Target = Field("prod", description="数据库目标")
    retrieval_mode: Literal["deterministic_plan", "agentic_arag"] = Field(
        "agentic_arag",
        description="检索模式，默认 Agentic A-RAG",
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


@router.post("/rebuild-wiki", summary="重建知识 Wiki")
async def rebuild_wiki(req: KGRebuildWikiRequest):
    service = create_knowledge_service(target=req.target)
    return await _call(
        service.rebuild_wiki_for(
            KnowledgeRebuildWikiCommand(
                adapter_name=req.adapter_name,
                target=req.target,
                scope=req.scope,
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
