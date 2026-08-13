"""Read-only APIs for the persisted market-data observability dashboard."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from src.application.services.market_observability_service import (
    MarketObservabilityService,
)
from src.application.services.collection_task_observability_service import (
    CollectionTaskObservabilityService,
)
from src.infrastructure.persistence.repositories import NewsRepositoryImpl
from src.infrastructure.persistence.repositories.jettask_schedule_repository import (
    JetTaskScheduleRepository,
)


router = APIRouter(tags=["市场数据观测"])


@router.get(
    "/api/market-observability/gold-news",
    summary="读取已落入 ft_news 的同花顺黄金AI事件",
)
def market_observability_gold_news(
    hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=500),
):
    rows = NewsRepositoryImpl().find_recent(
        source="ths_gold_ai",
        news_kind="news",
        hours=hours,
        limit=limit,
    )
    return {"count": len(rows), "data": rows}


@router.get("/market-dashboard", include_in_schema=False)
def market_dashboard() -> RedirectResponse:
    return RedirectResponse(url="/static/market_observability_dashboard.html")


@router.get(
    "/api/market-observability/dashboard",
    summary="读取行情采集观测看板",
)
def market_observability_dashboard(
    hours: int = Query(24, ge=1, le=168),
):
    try:
        return MarketObservabilityService().dashboard(hours=hours)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取行情采集看板失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/collection-tasks",
    summary="读取业务化采集任务目录与实时状态",
)
def market_observability_collection_tasks():
    try:
        return CollectionTaskObservabilityService().catalogue()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取采集任务状态失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.post(
    "/api/market-observability/collection-tasks/{scheduler_id}/trigger",
    summary="立即触发一个有边界的采集调度任务",
)
async def trigger_market_observability_collection_task(scheduler_id: str):
    schedule = JetTaskScheduleRepository().get(scheduler_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    if not schedule.get("enabled"):
        raise HTTPException(status_code=409, detail="调度任务已停用")
    from jettask import TaskMessage
    from src.interfaces.tasks import app

    task_ids = await app.send([TaskMessage(
        queue=str(schedule["queue_name"]),
        args=list(schedule.get("task_args") or []),
        kwargs=dict(schedule.get("task_kwargs") or {}),
        max_retries=0,
        timeout=500,
        priority=9,
    )])
    return {"scheduler_id": scheduler_id, "task_ids": task_ids, "status": "queued"}


@router.get(
    "/api/market-observability/stock-rankings",
    summary="读取数据库中的个股排行最新投影",
)
def market_observability_stock_rankings(
    sort: str = Query("rise"),
    count: int = Query(20, ge=1, le=50),
):
    try:
        return MarketObservabilityService().stock_ranking(
            sort=sort,
            count=count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取股票排行投影失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/stock-dynamic-groups",
    summary="读取数据库中的同花顺个股动态分组",
)
def market_observability_stock_dynamic_groups(
    count_per_group: int = Query(20, ge=1, le=100),
    scope: str = Query("featured", pattern="^(featured|candidates)$"),
):
    try:
        return MarketObservabilityService().stock_dynamic_groups(
            count_per_group=count_per_group,
            scope=scope,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "读取个股动态分组失败: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


@router.get(
    "/api/market-observability/sectors/overview",
    summary="读取数据库中的同花顺板块市场概览",
)
def market_observability_sector_overview(
    limit_per_group: int = Query(20, ge=1, le=100),
):
    try:
        return MarketObservabilityService().sector_overview(
            limit_per_group=limit_per_group,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取板块市场概览失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/sectors/rankings",
    summary="分页读取同花顺板块排行与信号",
)
def market_observability_sector_rankings(
    data_type: str = Query(..., min_length=1),
    metric: str | None = Query(None),
    sector_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        return MarketObservabilityService().sector_ranking(
            data_type=data_type,
            metric=metric,
            sector_type=sector_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取板块排行失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/sectors/detail",
    summary="读取单个同花顺板块的最新状态与历史",
)
def market_observability_sector_detail(
    provider_sector_code: str = Query(..., min_length=1),
    sector_type: str | None = Query(None),
    history_limit: int = Query(300, ge=1, le=1000),
):
    try:
        return MarketObservabilityService().sector_detail(
            provider_sector_code=provider_sector_code,
            sector_type=sector_type,
            history_limit=history_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取板块详情失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/snapshots",
    summary="查询各对象最新行情快照",
)
def list_market_observation_snapshots(
    data_type: str | None = Query(None),
    subject_type: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    try:
        return MarketObservabilityService().list_snapshots(
            data_type=data_type,
            subject_type=subject_type,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取行情快照失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/history",
    summary="查询单个行情对象的快照历史",
)
def market_observation_history(
    subject_id: str = Query(..., min_length=1),
    data_type: str = Query(..., min_length=1),
    limit: int = Query(500, ge=1, le=5000),
):
    try:
        return MarketObservabilityService().history(
            subject_id=subject_id,
            data_type=data_type,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取行情历史失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/inventory",
    summary="读取全部采集数据资产清单",
)
def collection_inventory():
    try:
        return MarketObservabilityService().inventory()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取采集数据资产失败: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/api/market-observability/records",
    summary="分页读取指定采集数据域",
)
def collection_records(
    domain: str = Query(..., min_length=1),
    group: str | None = Query(None),
    query: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        return MarketObservabilityService().collection_records(
            domain=domain,
            group=group,
            query=query,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"读取采集记录失败: {type(exc).__name__}: {exc}",
        ) from exc
