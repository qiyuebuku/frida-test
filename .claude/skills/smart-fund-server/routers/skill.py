"""Skill API 路由：4 个端点"""

import base64
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from services import task_db
from services.task_executor import executor
import services.skill_registry as sr

router = APIRouter()


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

    image_base64 = data.get("imageBase64", "") or data.get("image_base64", "")
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
