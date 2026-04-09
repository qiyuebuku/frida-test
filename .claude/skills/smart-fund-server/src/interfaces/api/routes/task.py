"""任务路由：异步任务 + 截图处理 + OCR + 步骤视图"""

import base64
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from src.infrastructure.db import task_db as _task_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== 异步任务 ====================

@router.post("/api/tasks", summary="创建异步任务", tags=["任务"])
async def create_task(request: Request):
    """接收截图或命令，创建后台异步任务，立即返回 task_id"""
    from src.application.orchestrators.task_executor import executor as _executor

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
    skill_name: str = Query(None, description="按 Skill 筛选"),
    limit: int = Query(20, description="每页数量", ge=1, le=100),
    next_token: str = Query(None, description="翻页游标"),
):
    """查询任务列表，支持 NextToken 游标翻页"""
    items, total, new_next_token = _task_db.list_tasks(
        status=status, task_type=task_type, skill_name=skill_name,
        limit=limit, next_token=next_token
    )
    data = {"total": total, "items": items}
    if new_next_token:
        data["next_token"] = new_next_token
    return {"status": "success", "data": data}


@router.get("/api/tasks/{task_id}", summary="查询任务详情", tags=["任务"])
async def get_task(task_id: int):
    """查询单个任务的完整详情（含 result Markdown）"""
    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return {"status": "success", "data": task}


@router.get("/api/tasks/{task_id}/files", summary="下载任务产出文件", tags=["任务"])
async def download_task_files(task_id: int):
    """将任务产出的所有文件打包为 zip 返回。

    每个任务的产出文件存放在 {skill_path}/output/{task_id}/ 目录下。
    """
    import io
    import zipfile
    from fastapi.responses import StreamingResponse
    import src.infrastructure.tools.skill_registry as sr

    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    if task["status"] not in ("completed", "stopped"):
        raise HTTPException(400, f"任务尚未完成 (status={task['status']})")

    # 确定 skill 目录
    skill_name = task.get("skill_name")
    if not skill_name or not sr.skill_registry:
        raise HTTPException(400, "无法确定任务对应的 Skill 目录")
    skill = sr.skill_registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(404, f"Skill '{skill_name}' 不存在")

    output_dir = Path(skill.path) / "output" / str(task_id)
    if not output_dir.is_dir():
        raise HTTPException(404, f"任务 {task_id} 无产出文件目录")

    # 收集文件
    output_files = [f for f in output_dir.rglob("*") if f.is_file()]
    if not output_files:
        raise HTTPException(404, f"任务 {task_id} 产出目录为空")

    # 打包 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_files:
            zf.write(f, str(f.relative_to(output_dir)))
    buf.seek(0)

    # 文件名
    config = task.get("config")
    site_name = ""
    if config:
        if isinstance(config, str):
            config = json.loads(config)
        site_name = (config.get("args") or {}).get("site_name", "")
    zip_name = f"task_{task_id}_{site_name or skill_name}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.get("/api/ocr/records", summary="查询OCR记录", tags=["截图"])
async def get_ocr_records(
    action: str = Query(None, description="按action筛选，如ocr/fund_holdings"),
    limit: int = Query(20, description="返回条数"),
):
    """查询OCR识别记录"""
    from src.infrastructure.db import ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=limit)
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    return {"status": "success", "data": records, "total": len(records)}


@router.get("/api/ocr/records/{record_id}", summary="查询单条OCR记录", tags=["截图"])
async def get_ocr_record_by_id(record_id: int):
    """根据ID查询单条OCR记录"""
    from src.infrastructure.db import ocr_db
    records = ocr_db.get_ocr_records(limit=1000)
    for r in records:
        if r["id"] == record_id:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
            return {"status": "success", "data": r}
    raise HTTPException(status_code=404, detail=f"OCR记录 {record_id} 不存在")


# ==================== 步骤视图 ====================

# tool 名称 → 中文显示 + 图标 hint
_TOOL_LABELS = {
    "Read": "读取文件",
    "Write": "写入文件",
    "Edit": "编辑文件",
    "Bash": "执行命令",
    "Glob": "搜索文件",
    "Grep": "搜索内容",
    "WebFetch": "抓取网页",
    "WebSearch": "搜索网页",
    "Agent": "子任务",
    "TodoWrite": "更新待办",
    "Skill": "执行技能",
}


def _find_session_file(session_id: str) -> Path | None:
    """查找 Claude CLI 会话 JSONL 文件"""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        f = project_dir / f"{session_id}.jsonl"
        if f.exists():
            return f
    return None


def _tool_display(tool_name: str, tool_input: dict) -> str:
    """生成 tool_use 的可读摘要"""
    label = _TOOL_LABELS.get(tool_name, tool_name)
    if tool_name == "Read":
        p = tool_input.get("file_path", "")
        return f"{label}: {Path(p).name}" if p else label
    elif tool_name == "Write":
        p = tool_input.get("file_path", "")
        return f"{label}: {Path(p).name}" if p else label
    elif tool_name == "Edit":
        p = tool_input.get("file_path", "")
        return f"{label}: {Path(p).name}" if p else label
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        return f"{label}: {desc}" if desc else f"{label}: {cmd[:80]}"
    elif tool_name == "Glob":
        return f"{label}: {tool_input.get('pattern', '')}"
    elif tool_name == "Grep":
        return f"{label}: {tool_input.get('pattern', '')}"
    elif tool_name == "Agent":
        return f"{label}: {tool_input.get('description', tool_input.get('prompt', '')[:60])}"
    return label


def _extract_tool_result_text(content) -> str:
    """从 tool_result 的 content 中提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content) if content else ""


def _parse_session_steps(session_id: str) -> list[dict]:
    """解析 Claude CLI 会话 JSONL 文件，返回结构化步骤列表"""
    f = _find_session_file(session_id)
    if not f:
        return []

    steps = []
    # tool_use_id → step index（用于关联 tool_result）
    pending_tools: dict[str, int] = {}

    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = obj.get("type", "")
            msg = obj.get("message", {})
            ts = obj.get("timestamp", "")

            if rec_type == "assistant":
                content = msg.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type", "")
                    if bt == "text":
                        text = block.get("text", "").strip()
                        if text:
                            steps.append({
                                "type": "text",
                                "content": text,
                                "timestamp": ts,
                            })
                    elif bt == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        tool_id = block.get("id", "")
                        step = {
                            "type": "tool_use",
                            "tool": tool_name,
                            "title": _tool_display(tool_name, tool_input),
                            "input": tool_input,
                            "output": None,
                            "is_error": False,
                            "timestamp": ts,
                        }
                        steps.append(step)
                        if tool_id:
                            pending_tools[tool_id] = len(steps) - 1

            elif rec_type == "user":
                # tool_result 嵌在 user 消息的 content 数组里
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            tool_id = block.get("tool_use_id", "")
                            result_content = block.get("content", "")
                            is_error = block.get("is_error", False)
                            output = _extract_tool_result_text(result_content)
                            if tool_id and tool_id in pending_tools:
                                idx = pending_tools.pop(tool_id)
                                steps[idx]["output"] = output[:2000]
                                steps[idx]["is_error"] = is_error

    except Exception as e:
        logger.warning(f"[steps] 解析 session JSONL 失败: {e}")

    return steps


@router.get("/api/tasks/{task_id}/steps", summary="查询任务步骤", tags=["任务"])
async def get_task_steps(task_id: int):
    """获取任务的结构化步骤列表

    来源优先级:
    1. Claude CLI 会话 JSONL 文件（AI 任务）
    2. DB tool_calls 字段（所有任务）
    """
    task = _task_db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    steps = []

    # 优先从 Claude session JSONL 解析
    session_id = task.get("session_id")
    if session_id:
        steps = _parse_session_steps(session_id)

    # 如果没有 session 步骤，从 DB tool_calls 回退
    if not steps and task.get("tool_calls"):
        tool_calls = task["tool_calls"]
        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except json.JSONDecodeError:
                tool_calls = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            steps.append({
                "type": "tool_use",
                "tool": tc.get("tool", ""),
                "title": tc.get("display", ""),
                "input": {},
                "output": tc.get("output", ""),
                "is_error": tc.get("is_error", False),
                "timestamp": "",
            })

    return {
        "status": "success",
        "data": {
            "steps": steps,
            "progress": task.get("progress", 0),
            "progress_msg": task.get("progress_msg"),
            "status": task.get("status", "pending"),
        }
    }


@router.get("/api/ocr/latest", summary="最新OCR记录", tags=["截图"])
async def get_latest_ocr(
    action: str = Query(None, description="按action筛选"),
    count: int = Query(1, description="返回条数"),
):
    """获取最新的OCR识别记录（支付宝决策用）"""
    from src.infrastructure.db import ocr_db
    records = ocr_db.get_ocr_records(action=action, limit=count)
    for r in records:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    if count == 1 and records:
        return {"status": "success", "data": records[0]}
    return {"status": "success", "data": records}
