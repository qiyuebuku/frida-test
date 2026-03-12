"""任务路由：异步任务 + 截图处理 + OCR"""

import asyncio
import base64
import json
import time
import queue as _queue
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import StreamingResponse

from services import task_db as _task_db
from services.event_bus import event_bus as _event_bus

router = APIRouter()


# ==================== 异步任务 ====================

@router.post("/api/tasks", summary="创建异步任务", tags=["任务"])
async def create_task(request: Request):
    """接收截图或命令，创建后台异步任务，立即返回 task_id"""
    from services.task_executor import executor as _executor

    data = await request.json()
    task_type = data.get("task_type", data.get("action", "ocr"))
    image_base64 = data.get("imageBase64", "")
    client_id = data.get("client_id", request.headers.get("X-Client-Id", "android"))

    # 保存截图
    image_path = None
    if image_base64:
        images_dir = Path(__file__).parent.parent / "images"
        images_dir.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        filepath = images_dir / f"screenshot_{ts}.jpg"
        filepath.write_bytes(base64.b64decode(image_base64))
        image_path = str(filepath)

    # 自定义配置（提示词/规则）
    config = None
    system_prompt = data.get("system_prompt")
    rules = data.get("rules")
    if system_prompt or rules:
        config = {}
        if system_prompt:
            config["system_prompt"] = system_prompt
        if rules:
            config["rules"] = rules

    task_id = _task_db.create_task(
        task_type=task_type,
        input_type="screenshot" if image_base64 else "command",
        image_path=image_path,
        client_id=client_id,
        config=config,
    )

    _executor.submit(task_id)

    return {"status": "success", "task_id": task_id, "message": "任务已提交，正在处理中"}


@router.get("/api/tasks", summary="查询任务列表", tags=["任务"])
async def list_tasks(
    status: str = Query(None, description="按状态筛选: pending/processing/completed/failed/all"),
    task_type: str = Query(None, description="按类型筛选: fund_holdings/chat_reply/ocr/..."),
    limit: int = Query(20, description="每页数量", ge=1, le=100),
    offset: int = Query(0, description="偏移量", ge=0),
):
    """查询任务列表，支持状态和类型筛选"""
    items, total = _task_db.list_tasks(status=status, task_type=task_type, limit=limit, offset=offset)
    return {"status": "success", "data": {"total": total, "items": items}}


@router.get("/api/tasks/{task_id}", summary="查询任务详情", tags=["任务"])
async def get_task(task_id: int):
    """查询单个任务的完整详情（含 result Markdown）"""
    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return {"status": "success", "data": task}


@router.get("/api/tasks/{task_id}/stream", summary="任务实时事件流(SSE)", tags=["任务"])
async def stream_task(task_id: int):
    """SSE 端点，实时推送任务执行事件（tool_call / text_delta / done）"""
    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    # 任务已完成/失败，直接返回终态
    if task["status"] in ("completed", "failed"):
        async def done_stream():
            event = {
                "type": "done",
                "status": task["status"],
                "result": task.get("result"),
                "error_msg": task.get("error_msg"),
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(done_stream(), media_type="text/event-stream")

    q = _event_bus.subscribe(task_id)

    async def event_stream():
        try:
            while True:
                try:
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=1)
                    )
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") == "done":
                        break
                except _queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            _event_bus.unsubscribe(task_id, q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ==================== 截图处理 ====================

@router.get("/api/ocr/records", summary="查询OCR记录", tags=["截图"])
async def get_ocr_records(
    action: str = Query(None, description="按action筛选，如ocr/fund_holdings"),
    limit: int = Query(20, description="返回条数"),
):
    """查询OCR识别记录"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=limit)
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    return {"status": "success", "data": records, "total": len(records)}


@router.get("/api/ocr/records/{record_id}", summary="查询单条OCR记录", tags=["截图"])
async def get_ocr_record_by_id(record_id: int):
    """根据ID查询单条OCR记录"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(limit=1000)
    for r in records:
        if r["id"] == record_id:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
            return {"status": "success", "data": r}
    raise HTTPException(status_code=404, detail=f"OCR记录 {record_id} 不存在")


@router.get("/api/ocr/latest", summary="最新OCR记录", tags=["截图"])
async def get_latest_ocr(
    action: str = Query(None, description="按action筛选"),
    count: int = Query(1, description="返回条数"),
):
    """获取最新的OCR识别记录（支付宝决策用）"""
    from services import db as ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=count)
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    if count == 1 and records:
        return {"status": "success", "data": records[0]}
    return {"status": "success", "data": records}


@router.post("/api/screenshot", summary="截图处理（SSE流式）", tags=["截图"])
async def process_screenshot(request: Request):
    """接收截图并进行 OCR + AI 结构化处理，通过 SSE 推送进度"""
    from handlers.screenshot_handler import ScreenshotHandler

    data = await request.json()
    client_id = request.headers.get("X-Client-Id", "android")
    handler = ScreenshotHandler()

    async def event_stream():
        async for event in handler.process_stream(data, client_id=client_id):
            event_type = event.get("event", "message")
            event_data = json.dumps(event.get("data", {}), ensure_ascii=False)
            yield f"event: {event_type}\ndata: {event_data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
