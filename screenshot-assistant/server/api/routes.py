import base64
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from handlers.screenshot_handler import ScreenshotHandler
from services.skill_registry import SkillRegistry
from services import db
from services.task_executor import task_executor

router = APIRouter()

screenshot_handler = ScreenshotHandler()

# Skill 注册表 — skills 目录位于 frida-test/.claude/skills/
_skills_dir = str(__import__("pathlib").Path(__file__).resolve().parents[3] / ".claude" / "skills")
skill_registry = SkillRegistry(_skills_dir)


# ── 旧端点 ─────────────────────────────────────────────

@router.post("/screenshot")
async def process_screenshot(request: Request):
    """接收截图并进行 OCR 处理"""
    data = await request.json()
    client_id = request.headers.get("X-Client-Id", "android")
    result = await screenshot_handler.process(data, client_id=client_id)
    return {"success": True, "data": result, "message": "处理完成"}


# ── Skill API ──────────────────────────────────────────

@router.get("/skills")
async def list_skills():
    """获取所有 Skill 列表"""
    skills = skill_registry.list_skills()
    return {"status": "success", "data": [s.to_summary() for s in skills]}


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str):
    """获取单个 Skill 详情（含命令列表）"""
    skill = skill_registry.get_skill(skill_name)
    if not skill:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"Skill '{skill_name}' not found"})
    return {"status": "success", "data": skill.to_detail()}


@router.post("/skills/{skill_name}/run")
async def run_skill_command(skill_name: str, request: Request):
    """触发 Skill 命令执行"""
    skill = skill_registry.get_skill(skill_name)
    if not skill:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"Skill '{skill_name}' not found"})

    data = await request.json()
    command_id = data.get("command_id")
    command = skill.get_command(command_id)
    if not command:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"Command '{command_id}' not found"})

    args = data.get("args")
    input_data = data.get("input_data")
    image_base64 = data.get("image_base64")
    client_id = data.get("client_id", "android")

    # 保存图片
    image_path = None
    if image_base64:
        os.makedirs("images", exist_ok=True)
        image_path = f"images/{int(time.time())}_{skill_name}_{command_id}.jpg"
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(image_base64))

    task_type = f"{skill_name}_{command_id}"
    title = f"{skill.display_name} - {command.name}"

    task_id = db.create_task(
        task_type=task_type,
        skill_name=skill_name,
        command_id=command_id,
        input_type=command.input,
        input_data=input_data,
        image_path=image_path,
        client_id=client_id,
        config={"args": args} if args else None,
        title=title,
    )

    task_executor.submit(task_id, skill, command, args, input_data, image_path)

    return {"status": "success", "task_id": task_id, "message": f"任务已创建：{title}"}


@router.post("/skills/reload")
async def reload_skills():
    """重新扫描 skills 目录"""
    skill_registry.scan()
    return {"status": "success", "message": f"Reloaded {len(skill_registry.skills)} skills"}


# ── Tasks API ──────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    status: str = None,
    task_type: str = None,
    skill_name: str = None,
    limit: int = 20,
    offset: int = 0,
):
    """获取任务列表"""
    tasks, total = db.get_tasks(status, task_type, skill_name, limit, offset)
    # 序列化 datetime 等字段
    for t in tasks:
        for k, v in t.items():
            if hasattr(v, "isoformat"):
                t[k] = v.isoformat()
    return {"status": "success", "data": {"items": tasks, "total": total}}


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: int):
    """获取任务详情"""
    task = db.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Task not found"})
    for k, v in task.items():
        if hasattr(v, "isoformat"):
            task[k] = v.isoformat()
    return {"status": "success", "data": task}


@router.post("/tasks")
async def create_task_legacy(request: Request):
    """兼容旧的任务创建接口"""
    data = await request.json()
    task_type = data.get("task_type") or data.get("action", "unknown")
    client_id = data.get("client_id", "android")
    image_base64 = data.get("imageBase64")

    image_path = None
    if image_base64:
        os.makedirs("images", exist_ok=True)
        image_path = f"images/{int(time.time())}_{task_type}.jpg"
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(image_base64))

    title = data.get("title") or task_type

    task_id = db.create_task(
        task_type=task_type,
        input_type="screenshot" if image_base64 else "none",
        image_path=image_path,
        client_id=client_id,
        title=title,
        config={
            "system_prompt": data.get("system_prompt"),
            "rules": data.get("rules"),
        } if data.get("system_prompt") or data.get("rules") else None,
    )

    # 尝试通过 skill registry 找到匹配的 command
    skill = None
    command = None
    for s in skill_registry.list_skills():
        for c in s.commands:
            if c.id == task_type or f"{s.name}_{c.id}" == task_type:
                skill = s
                command = c
                break
        if skill:
            break

    if skill and command:
        task_executor.submit(task_id, skill, command)
    else:
        # 回退：标记为 pending，等待其他处理方式
        print(f"[routes] no matching skill for task_type={task_type}", flush=True)

    return {"status": "success", "task_id": task_id, "message": f"任务已创建"}
