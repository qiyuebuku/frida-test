"""自选标的管理 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.application.services.watchlist_service import WatchlistService

router = APIRouter(prefix="/api/watchlist", tags=["自选管理"])

_svc = WatchlistService()


# ==================== 请求模型 ====================


class AddWatchlistRequest(BaseModel):
    code: str                       # 如 "sh600036"
    name: str = ""                  # 如 "招商银行"
    type: str = "stock"             # stock / fund / index
    source: str = "manual"
    target_days: int | None = None  # 回填天数，None 用默认值
    interval: int | None = None     # 采集间隔秒，None 用默认值


class BatchAddRequest(BaseModel):
    items: list[AddWatchlistRequest]


class UpdateWatchlistRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    type: str | None = None
    interval: int | None = None
    target_days: int | None = None


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


@router.post("", summary="添加自选")
async def add_watchlist(req: AddWatchlistRequest):
    created = _svc.add(
        code=req.code,
        name=req.name,
        type=req.type,
        source=req.source,
        target_days=req.target_days,
        interval=req.interval,
    )
    if not created:
        return {"created": False, "message": f"{req.code} 已存在"}
    return {"created": True, "code": req.code}


@router.post("/batch", summary="批量添加自选")
async def batch_add_watchlist(req: BatchAddRequest):
    items = [i.model_dump() for i in req.items]
    count = _svc.add_batch(items)
    return {"created": count, "total": len(items)}


@router.put("/{code}", summary="更新自选配置")
async def update_watchlist(code: str, req: UpdateWatchlistRequest):
    kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(400, "没有要更新的字段")
    ok = _svc.update(code, **kwargs)
    if not ok:
        raise HTTPException(404, f"自选 {code} 不存在")
    return {"updated": True, "code": code}


@router.delete("/{code}", summary="删除自选")
async def delete_watchlist(code: str):
    ok = _svc.remove(code)
    if not ok:
        raise HTTPException(404, f"自选 {code} 不存在")
    return {"deleted": True, "code": code}


@router.post("/sync-positions", summary="从持仓同步到自选")
async def sync_positions():
    count = _svc.sync_from_positions()
    return {"synced": count}
