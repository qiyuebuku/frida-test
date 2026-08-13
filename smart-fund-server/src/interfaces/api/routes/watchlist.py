"""自选标的管理 API"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.application.services.market_tracking_service import (
    MarketTrackingService,
)
from src.application.services.watchlist_service import WatchlistService
from src.infrastructure.tasks.jettask_dispatcher import (
    send_watchlist_instrument_collection,
)

router = APIRouter(prefix="/api/watchlist", tags=["自选管理"])

_svc = WatchlistService()
_tracking = MarketTrackingService(watchlist_service=_svc)


# ==================== 请求模型 ====================


class AddWatchlistRequest(BaseModel):
    code: str                       # 如 "sh600036"
    name: str = ""                  # 如 "招商银行"
    type: Literal["auto", "stock", "fund", "etf", "index"] = "auto"
    source: Literal["manual", "position", "event", "agent"] = "manual"
    reason: str = ""
    target_days: int | None = Field(default=None, ge=1)
    priority: Literal["critical", "standard", "low"] = "standard"
    priority_reason: str = ""
    realtime_interval_seconds: int | None = Field(default=None, ge=30)


class BatchAddRequest(BaseModel):
    items: list[AddWatchlistRequest]


class UpdateWatchlistRequest(BaseModel):
    code: str
    enabled: bool | None = None
    name: str | None = None
    type: Literal["stock", "fund", "etf", "index"] | None = None
    priority: Literal["critical", "standard", "low"] | None = None
    priority_reason: str | None = None
    realtime_interval_seconds: int | None = Field(default=None, ge=30)
    target_days: int | None = Field(default=None, ge=1)
    source: Literal["manual", "position", "event", "agent"] | None = None
    reason: str | None = None


class BatchUpdateRequest(BaseModel):
    items: list[UpdateWatchlistRequest]


class BatchDeleteRequest(BaseModel):
    codes: list[str]


# ==================== 路由 ====================


@router.get("", summary="列出自选")
async def list_watchlist(enabled_only: bool = False):
    items = _svc.list_all(enabled_only=enabled_only)
    return {"total": len(items), "items": [i.to_dict() for i in items]}


@router.get("/{code}", summary="查看单个自选")
async def get_watchlist(code: str):
    item = _svc.get(code)
    if not item:
        raise HTTPException(404, f"自选 {code} 不存在")
    return item.to_dict()


@router.post("/batch", summary="批量添加或重新启用自选")
async def batch_add_watchlist(req: BatchAddRequest):
    try:
        result = await _tracking.add_instruments(
            [item.model_dump() for item in req.items]
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "total": len(result["items"]),
        "items": result["items"],
        "collection_event_ids": result["collection_event_ids"],
    }


@router.put("/batch", summary="批量更新或停用自选")
async def batch_update_watchlist(req: BatchUpdateRequest):
    payload = [
        {
            key: value
            for key, value in item.model_dump().items()
            if value is not None
        }
        for item in req.items
    ]
    try:
        mutations = _svc.update_batch(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    collect_codes = [
        mutation.code
        for mutation in mutations
        if mutation.should_collect_now
    ]
    event_ids = await send_watchlist_instrument_collection(collect_codes)
    return {
        "total": len(mutations),
        "items": [mutation.to_dict() for mutation in mutations],
        "collection_event_ids": event_ids,
    }


@router.delete("/batch", summary="批量删除自选配置")
async def batch_delete_watchlist(req: BatchDeleteRequest):
    deleted_codes = _svc.remove_batch(req.codes)
    return {
        "deleted": len(deleted_codes),
        "codes": deleted_codes,
    }


@router.post("/sync-positions", summary="从持仓同步到自选")
async def sync_positions():
    mutations = _svc.sync_from_positions_batch()
    collect_codes = [
        mutation.code
        for mutation in mutations
        if mutation.should_collect_now
    ]
    event_ids = await send_watchlist_instrument_collection(collect_codes)
    return {
        "total": len(mutations),
        "items": [mutation.to_dict() for mutation in mutations],
        "collection_event_ids": event_ids,
    }
