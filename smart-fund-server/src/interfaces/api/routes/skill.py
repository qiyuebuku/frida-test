"""Skill API 路由：4 个端点"""

import base64
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from src.infrastructure.db import task_db
from src.application.orchestrators.task_orchestrator import executor
import src.infrastructure.tools.skill_registry as sr

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/guide", summary="API 使用指南", tags=["Skill"])
async def api_guide(request: Request):
    """返回完整的 API 使用协议，供外部 Agent 自主发现和调用本服务的所有能力。

    动态生成：skills 概览从 SkillRegistry 实时读取，新增 Skill 后无需改代码。
    """
    base = str(request.base_url).rstrip("/")

    # 动态生成 skills 概览
    skills_overview = []
    if sr.skill_registry:
        for skill in sr.skill_registry.list_skills():
            skills_overview.append({
                "name": skill.name,
                "display_name": skill.display_name,
                "description": skill.description,
                "commands": [
                    {
                        "id": cmd.id,
                        "name": cmd.name,
                        "description": cmd.description,
                        "args": [
                            {"name": a.name, "description": a.description, "required": a.required}
                            for a in cmd.args
                        ],
                    }
                    for cmd in skill.commands
                ],
            })

    return {
        "status": "success",
        "data": {
            "service": "smart-fund-server",
            "base_url": base,
            "overview": "Skill 任务平台：提交异步任务 → 轮询状态 → 获取结果/文件。所有 Skill 共享同一套任务生命周期。",
            "skills": skills_overview,
            "workflow": [
                {
                    "step": 1,
                    "name": "上传文件（如需）",
                    "description": "当 Skill 命令的参数需要文件路径时，先上传文件获取服务端路径，再将路径传入 args",
                    "endpoint": {"method": "POST", "path": "/api/files/upload"},
                    "body_options": [
                        {"Content-Type": "application/json", "body": {"filename": "my_api.json", "content": "文件内容字符串"}},
                        {"Content-Type": "multipart/form-data", "body": "file=@local_file.json"},
                    ],
                    "response": {"path": "uploads/1234_my_api.json（传入 Skill args）"},
                },
                {
                    "step": 2,
                    "name": "提交任务",
                    "endpoint": {"method": "POST", "path": "/api/skills/{name}/run"},
                    "body": {
                        "command_id": "(必填) 命令 ID，见上方 skills 列表",
                        "args": "(可选) 命令参数，key-value 对象，文件类参数填上一步返回的 path",
                        "rules": "(可选) 用户额外要求，注入执行过程",
                    },
                    "response": {"task_id": "任务 ID，用于后续查询"},
                },
                {
                    "step": 3,
                    "name": "轮询等待",
                    "endpoint": {"method": "GET", "path": "/api/tasks/{task_id}"},
                    "description": "建议间隔 10 秒轮询，直到 status 为 completed 或 failed",
                    "status_values": {
                        "pending": "等待执行",
                        "processing": "执行中",
                        "completed": "已完成（result 含结果）",
                        "failed": "失败（error_msg 含原因）",
                    },
                },
                {
                    "step": 4,
                    "name": "获取结果",
                    "endpoints": [
                        {"method": "GET", "path": "/api/tasks/{task_id}/files", "description": "下载产出文件 zip 包"},
                        {"method": "GET", "path": "/api/files/read?path={file_path}", "description": "读取单个文件"},
                    ],
                },
                {
                    "step": 5,
                    "name": "追问（可选）",
                    "endpoint": {"method": "POST", "path": "/api/tasks/{task_id}/message", "body": {"message": "追问内容"}},
                },
            ],
            "other_endpoints": [
                {"method": "GET", "path": "/api/skills", "description": "Skill 列表"},
                {"method": "GET", "path": "/api/skills/{name}", "description": "Skill 详情"},
                {"method": "GET", "path": "/api/tasks/{task_id}/steps", "description": "执行步骤明细"},
                {"method": "POST", "path": "/api/tasks/{task_id}/stop", "description": "停止任务"},
                {"method": "DELETE", "path": "/api/tasks/{task_id}", "description": "删除任务"},
                {"method": "GET", "path": "/api/tasks?status=all&limit=20", "description": "任务列表"},
                {"method": "GET", "path": "/api/files/read?path={file_path}", "description": "读取文件内容"},
                {"method": "GET", "path": "/api/files/list?path={dir}&pattern=*.md&recursive=true", "description": "列出目录文件"},
                {"method": "POST", "path": "/api/skills/reload", "description": "重新扫描 Skills"},
            ],
            "notes": [
                "所有请求使用 JSON body，Content-Type: application/json",
                "curl 命令建议加 --noproxy '*' 避免代理干扰",
            ],
        },
    }


@router.get("/api/skills", summary="Skill 列表", tags=["Skill"])
async def list_skills():
    """获取所有 Skill 摘要列表（App 渲染项目列表/悬浮球菜单用）"""
    if not sr.skill_registry:
        return {"status": "success", "data": []}
    skills = sr.skill_registry.list_skills()
    return {
        "status": "success",
        "data": [s.to_summary() for s in skills]
    }


@router.get("/api/skills/{name}", summary="Skill 详情", tags=["Skill"])
async def get_skill(name: str):
    """获取单个 Skill 详情（含完整命令列表）"""
    if not sr.skill_registry:
        raise HTTPException(404, "SkillRegistry 未初始化")
    skill = sr.skill_registry.get_skill(name)
    if not skill:
        raise HTTPException(404, f"Skill '{name}' 不存在")
    return {
        "status": "success",
        "data": skill.to_detail()
    }


@router.post("/api/skills/{name}/run", summary="执行 Skill 命令", tags=["Skill"])
async def run_skill(name: str, request: Request):
    """执行 Skill 命令，创建异步任务

    请求体:
    {
        "command_id": "ocr",
        "imageBase64": "...",       // 可选，截图类命令
        "client_id": "android",     // 可选
        "system_prompt": "...",     // 可选
        "rules": "...",             // 可选
        "args": {"key": "value"}    // 可选，命令参数
    }
    """
    if not sr.skill_registry:
        raise HTTPException(500, "SkillRegistry 未初始化")
    skill = sr.skill_registry.get_skill(name)
    if not skill:
        raise HTTPException(404, f"Skill '{name}' 不存在")

    data = await request.json()
    command_id = data.get("command_id", "")
    if not command_id:
        raise HTTPException(400, "缺少 command_id")

    command = skill.get_command(command_id)
    if not command:
        raise HTTPException(404, f"Skill '{name}' 中不存在命令 '{command_id}'")

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

    # 构建 config
    config = {}
    if data.get("system_prompt"):
        config["system_prompt"] = data["system_prompt"]
    if data.get("rules"):
        config["rules"] = data["rules"]
    if data.get("args"):
        config["args"] = data["args"]

    # 创建任务
    task_id = task_db.create_task(
        task_type=command_id,
        input_type="screenshot" if image_base64 else ("none" if command.input == "none" else "command"),
        image_path=image_path,
        client_id=client_id,
        title=f"{skill.display_name} - {command.name}",
        config=config or None,
        skill_name=name,
        command_id=command_id,
    )

    executor.submit(task_id)

    return {
        "status": "success",
        "task_id": task_id,
        "message": f"任务已提交: {command.name}"
    }


@router.post("/api/tasks/{task_id}/stop", summary="停止任务", tags=["任务"])
async def stop_task(task_id: int):
    """强制停止正在执行的任务"""
    task = task_db.get_task(task_id)
    if not task:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    if task["status"] not in ("processing", "pending"):
        raise HTTPException(400, "任务不在执行中")
    stopped = executor.force_stop(task_id)
    return {"status": "success", "stopped": stopped}


@router.delete("/api/tasks/{task_id}", summary="删除任务", tags=["任务"])
async def delete_task(task_id: int):
    """删除任务：清理 DB 记录 + tmux 会话 + Claude CLI 会话文件"""
    task = task_db.get_task(task_id)
    if not task:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    # 先停止正在运行的任务
    if task["status"] in ("processing", "pending"):
        executor.force_stop(task_id)
    # 清理 tmux 会话 + Claude session 文件
    executor.destroy_task(task_id)
    # 删除 DB 记录
    task_db.delete_task(task_id)
    # 删除关联截图文件
    if task.get("image_path"):
        import os
        try:
            os.unlink(task["image_path"])
        except OSError:
            pass
    return {"status": "success", "message": f"任务 {task_id} 已删除"}


@router.post("/api/tasks/{task_id}/message", summary="发送追问", tags=["任务"])
async def send_message(task_id: int, request: Request):
    """向任务发送追问消息，支持会话恢复"""
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    task = task_db.get_task(task_id)
    if not task:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    logger.info(f"[message] task_id={task_id} status={task['status']} session_id={task.get('session_id')} message={message[:80]}")
    if not task.get("session_id") and task["status"] not in ("processing", "pending"):
        logger.warning(f"[message] task_id={task_id} 拒绝追问: 无session且非processing/pending")
        raise HTTPException(400, "该任务不支持追问（无 session）")
    result = executor.queue_message(task_id, message)
    logger.info(f"[message] task_id={task_id} queue_message结果: {result}")
    return {"status": "success", **result}


@router.post("/api/skills/reload", summary="重新扫描 Skills", tags=["Skill"])
async def reload_skills():
    """重新扫描 skills 目录"""
    if not sr.skill_registry:
        raise HTTPException(500, "SkillRegistry 未初始化")
    sr.skill_registry.scan()
    skills = sr.skill_registry.list_skills()
    return {
        "status": "success",
        "message": f"重新加载了 {len(skills)} 个 Skill",
        "data": [s.to_summary() for s in skills]
    }
