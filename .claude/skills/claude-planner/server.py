"""Claude Planner 本地 API 服务 — 轻量 FastAPI 应用

从 smart-fund-server 精简提取，提供任务创建/查询/追问/停止等端点。
默认端口 8899，使用 SQLite 存储。
"""

import argparse
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import init_tables, create_task, update_task, get_task, list_tasks, delete_task
from tmux_manager import SessionManager, PlannerSession, run_streaming, run_chat, run_pipe, send_followup

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志同时输出到文件和 stderr
_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件 handler：按天滚动
from logging.handlers import TimedRotatingFileHandler
_file_handler = TimedRotatingFileHandler(
    LOG_DIR / "planner.log", when="D", backupCount=7, encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
_file_handler.setLevel(logging.DEBUG)

# stderr handler
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_log_formatter)
_stderr_handler.setLevel(logging.INFO)

logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _stderr_handler])
log = logging.getLogger("planner")

# ==================== 全局状态 ====================

SKILL_DIR = Path(__file__).parent
BACKENDS_PATH = SKILL_DIR / "backends.json"
PLANNER_PROMPT_PATH = SKILL_DIR.parent.parent / "agents" / "planner.md"

_session_manager: SessionManager | None = None
_backends_cache: dict = {"data": None, "mtime": 0}


def load_backends() -> dict:
    """加载 backends.json（支持热更新）"""
    try:
        st = BACKENDS_PATH.stat()
        if st.st_mtime != _backends_cache["mtime"]:
            _backends_cache["data"] = json.loads(BACKENDS_PATH.read_text())
            _backends_cache["mtime"] = st.st_mtime
    except Exception:
        if _backends_cache["data"] is None:
            raise
    return _backends_cache["data"]


def get_active_backend() -> dict:
    cfg = load_backends()
    active_name = cfg.get("active", "official")
    return cfg["backends"][active_name]


def get_backend_priority(initial: str | None = None) -> list[str]:
    """返回后端尝试顺序，initial 排最前"""
    cfg = load_backends()
    priority = cfg.get("priority")
    if priority:
        order = [b for b in priority if b in cfg["backends"]]
    else:
        order = list(cfg["backends"].keys())
    if initial and initial in order:
        order.remove(initial)
        order.insert(0, initial)
    return order


def load_planner_prompt() -> str:
    """加载架构师 persona（planner.md）"""
    if PLANNER_PROMPT_PATH.exists():
        return PLANNER_PROMPT_PATH.read_text(encoding="utf-8")
    return ""


# ==================== 请求模型 ====================

class CreateTaskRequest(BaseModel):
    prompt: str
    context_file: str | None = None
    backend: str | None = None
    model: str | None = None
    cwd: str | None = None
    timeout: int = 600

class MessageRequest(BaseModel):
    message: str

class RespondDialogRequest(BaseModel):
    action: str  # "enter" | "escape" | "text"
    text: str | None = None  # 当 action="text" 时，发送指定文本

class SetBackendRequest(BaseModel):
    name: str

class ChatRequest(BaseModel):
    prompt: str
    system_prompt: str | None = None
    model: str | None = None
    backend: str | None = None
    cwd: str | None = None
    timeout: int = 180


# ==================== 任务执行线程 ====================

def _run_task_once(task_id: int, prompt: str, backend_name: str,
                   backend_config: dict, cwd: str | None,
                   model: str | None, timeout: int) -> dict | None:
    """单次尝试：创建 tmux 会话 → 执行 → 返回结果。

    返回 result dict（含 rate_limited 等标记），或 None 表示启动失败。
    调用方负责 acquire/release semaphore。
    """
    update_task(task_id, status="processing", progress=0,
                progress_msg=f"启动 Claude CLI（后端: {backend_name}）")

    planner_prompt = load_planner_prompt()
    backends_cfg = load_backends()
    env_unset = backends_cfg.get("env_unset", [])
    model_map = backends_cfg.get("model_map", {})

    session = _session_manager.get_or_create(
        task_id, backend_config, cwd=cwd, model=model,
        system_prompt=planner_prompt or None,
        env_unset=env_unset,
        model_map=model_map,
    )
    if not session.ready:
        return None

    update_task(task_id, session_id=session.claude_session_id,
                session_name=session.session_name)

    return run_streaming(session, task_id, prompt, timeout=timeout)


def _run_task(task_id: int, prompt: str, initial_backend: str,
              cwd: str | None, model: str | None, timeout: int):
    """后台线程：按优先级尝试各后端，rate limit 时自动切换"""
    priority = get_backend_priority(initial_backend)
    cfg = load_backends()
    last_error = "无可用后端"

    for idx, backend_name in enumerate(priority):
        backend_config = cfg["backends"][backend_name]
        model_to_use = model or backend_config.get("model", "sonnet")

        if not _session_manager.acquire(timeout=300):
            last_error = "服务器繁忙，请稍后重试"
            continue

        try:
            if idx > 0:
                log.info(f"[task={task_id}] 切换到后端: {backend_name}（第 {idx+1}/{len(priority)} 个）")
                update_task(task_id, progress=0,
                            progress_msg=f"切换后端: {backend_name}（{idx+1}/{len(priority)}）")

            result = _run_task_once(task_id, prompt, backend_name,
                                    backend_config, cwd, model_to_use, timeout)

            if result is None:
                last_error = f"后端 {backend_name} Claude CLI 启动失败"
                _session_manager.remove(task_id)
                continue

            usage = result.get("usage", {})
            dur = int(result["duration"])

            if result.get("rate_limited"):
                last_error = f"后端 {backend_name} rate limited"
                log.warning(f"[task={task_id}] {last_error}，尝试下一个后端")
                _session_manager.remove(task_id)
                continue

            if result.get("timeout"):
                has_partial = bool(result.get("result"))
                update_task(task_id,
                            status="completed" if has_partial else "failed",
                            progress=95 if has_partial else 0,
                            error_msg=f"执行超时 ({timeout}s)" + ("，但文件可能已写入" if has_partial else ""),
                            result=result.get("result"), tool_calls=result.get("tool_calls"),
                            completed_at=datetime.now().isoformat(),
                            duration_sec=dur, usage=usage)
            elif result.get("result"):
                update_task(task_id, status="completed", progress=100,
                            result=result["result"], tool_calls=result.get("tool_calls"),
                            completed_at=datetime.now().isoformat(),
                            duration_sec=dur, usage=usage)
            else:
                update_task(task_id, status="failed", error_msg="未获得有效输出",
                            duration_sec=dur, usage=usage)
            return

        except Exception as e:
            last_error = f"后端 {backend_name} 异常: {str(e)[:200]}"
            log.exception(f"[task={task_id}] {last_error}")
            _session_manager.remove(task_id)
            continue
        finally:
            _session_manager.release()

    update_task(task_id, status="failed",
                error_msg=f"所有后端均失败（尝试 {len(priority)} 个）: {last_error}",
                completed_at=datetime.now().isoformat())


def _run_followup(task_id: int, message: str):
    """后台线程：追问"""
    if not _session_manager.acquire(timeout=300):
        update_task(task_id, status="failed", error_msg="服务器繁忙")
        return

    try:
        task = get_task(task_id)
        if not task:
            return

        session = _session_manager.get(task_id)
        if not session:
            # 尝试用 --resume 恢复
            backend = get_active_backend()
            session = _session_manager.get_or_create(
                task_id, backend, cwd=task.get("cwd"),
                resume_session_id=task.get("session_id"),
            )

        if not session or not session.ready:
            update_task(task_id, status="failed", error_msg="无法恢复 Claude CLI 会话")
            return

        update_task(task_id, status="processing", progress=0,
                    progress_msg="追问处理中", partial_result=None)

        result = send_followup(session, task_id, message, timeout=600)

        if result.get("result"):
            # 追加到 messages
            task = get_task(task_id)
            messages = task.get("messages") or []
            messages.append({"role": "user", "content": message,
                             "created_at": datetime.now().isoformat()})
            messages.append({"role": "assistant", "content": result["result"],
                             "created_at": datetime.now().isoformat()})

            update_task(task_id, status="completed", progress=100,
                        result=result["result"], messages=messages,
                        tool_calls=result.get("tool_calls"),
                        completed_at=datetime.now().isoformat(),
                        duration_sec=int(result["duration"]))
        else:
            update_task(task_id, status="failed", error_msg="追问未获得有效输出")

    except Exception as e:
        log.exception(f"task_id={task_id} 追问失败")
        update_task(task_id, status="failed", error_msg=str(e)[:500])
    finally:
        _session_manager.release()


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session_manager
    init_tables()
    _session_manager = SessionManager()
    backends = load_backends()
    active = backends.get("active", "official")
    log.info(f"Planner API 服务就绪 | active backend: {active}")
    yield
    # shutdown: _session_manager 的 atexit 会处理清理


app = FastAPI(title="Claude Planner API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok", "backend": load_backends().get("active", "unknown")}


@app.get("/backends")
def list_backends():
    cfg = load_backends()
    return {"active": cfg.get("active"), "backends": list(cfg["backends"].keys())}


@app.put("/backends/active")
def set_active_backend(req: SetBackendRequest):
    cfg = load_backends()
    if req.name not in cfg["backends"]:
        raise HTTPException(400, f"未知后端: {req.name}")
    cfg["active"] = req.name
    BACKENDS_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    _backends_cache["mtime"] = 0  # 强制重新加载
    return {"active": req.name}


@app.post("/tasks")
def create_new_task(req: CreateTaskRequest):
    backend_name = req.backend or load_backends().get("active", "official")
    cfg = load_backends()
    if backend_name not in cfg["backends"]:
        raise HTTPException(400, f"未知后端: {backend_name}")
    backend_config = cfg["backends"][backend_name]
    model = req.model or backend_config.get("model", "sonnet")

    task_id = create_task(
        prompt=req.prompt,
        context_file=req.context_file,
        status="pending",
        backend=backend_name,
        model=model,
        cwd=req.cwd,
        messages=[],
    )

    t = threading.Thread(
        target=_run_task,
        args=(task_id, req.prompt, backend_name, req.cwd, model, req.timeout),
        daemon=True,
    )
    t.start()

    return {"task_id": task_id, "status": "processing", "backend": backend_name, "model": model}


@app.get("/tasks/{task_id}")
def get_task_status(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/tasks")
def list_all_tasks(status: str = None, limit: int = 50):
    return {"items": list_tasks(status=status, limit=limit)}


@app.post("/tasks/{task_id}/message")
def send_message(task_id: int, req: MessageRequest):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # 追加用户消息
    messages = task.get("messages") or []
    messages.append({"role": "user", "content": req.message,
                     "created_at": datetime.now().isoformat()})
    update_task(task_id, messages=messages)

    t = threading.Thread(target=_run_followup, args=(task_id, req.message), daemon=True)
    t.start()

    return {"status": "processing", "message": "追问已发送"}


@app.post("/tasks/{task_id}/respond")
def respond_dialog(task_id: int, req: RespondDialogRequest):
    """响应 Claude CLI 的交互对话框

    客户端通过轮询 GET /tasks/{id} 发现 pending_dialog 不为空时调用此接口。
    action: "enter" 确认 / "escape" 取消 / "text" 发送自定义文本
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    session = _session_manager.get(task_id)
    if not session:
        raise HTTPException(400, "无活跃的 tmux 会话")

    from tmux_manager import _tmux

    if req.action == "enter":
        _tmux("send-keys", "-t", session.session_name, "Enter")
    elif req.action == "escape":
        _tmux("send-keys", "-t", session.session_name, "Escape")
    elif req.action == "text" and req.text:
        session.send(req.text)
    else:
        raise HTTPException(400, f"无效的 action: {req.action}")

    # 标记对话框已响应
    update_task(task_id, pending_dialog={"resolved": True, "action": req.action})
    log.info(f"task_id={task_id} 对话框已响应: action={req.action}")

    return {"status": "responded", "action": req.action}


@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task["status"] not in ("pending", "processing"):
        raise HTTPException(400, f"任务状态为 {task['status']}，无法停止")

    session = _session_manager.get(task_id)
    if session:
        session.send_ctrl_c()

    update_task(task_id, status="stopped", error_msg="用户手动停止",
                completed_at=datetime.now().isoformat())
    return {"status": "stopped"}


@app.delete("/tasks/{task_id}")
def destroy_task(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    _session_manager.remove(task_id)
    delete_task(task_id)
    return {"status": "deleted"}


@app.post("/chat")
def chat(req: ChatRequest):
    """一次性 LLM 调用（不写 tasks DB，不复用 session）。

    创建临时 tmux + Claude CLI → 发送 prompt → 等待完成 → 从 JSONL 读取结果 → 关闭 session。
    共享 SessionManager 的 semaphore，与 /tasks 互斥。
    """
    cfg = load_backends()
    priority = get_backend_priority(req.backend or cfg.get("active"))
    env_unset = cfg.get("env_unset", [])
    model_map = cfg.get("model_map", {})
    last_error = "无可用后端"

    if not _session_manager.acquire(timeout=300):
        raise HTTPException(503, "服务器繁忙，所有槽位被占用")

    session = None
    try:
        for backend_name in priority:
            backend_config = cfg["backends"][backend_name]
            model = req.model or backend_config.get("model", "sonnet")

            import random
            fake_id = random.randint(100000, 999999)
            session = PlannerSession(
                task_id=fake_id,
                backend_config=backend_config,
                cwd=req.cwd,
                model=model,
                system_prompt=req.system_prompt,
                env_unset=env_unset,
                model_map=model_map,
            )
            if not session.ready:
                last_error = f"后端 {backend_name} 启动失败"
                try:
                    session.close()
                except Exception:
                    pass
                session = None
                continue

            try:
                result = run_chat(session, req.prompt, timeout=req.timeout)
                saved_uuid = session.claude_session_id
            except RuntimeError as e:
                raise HTTPException(500, str(e))
            finally:
                try:
                    session.close()
                except Exception:
                    pass
                session = None

            if result.get("rate_limited"):
                last_error = f"后端 {backend_name} rate limited"
                log.warning(f"[chat] {last_error}，切换下一个后端")
                continue

            if result.get("timeout"):
                raise HTTPException(504, f"执行超时 ({req.timeout}s)")

            if not result.get("result"):
                last_error = f"后端 {backend_name} 未获得有效输出"
                continue

            return {
                "result": result["result"],
                "session_uuid": saved_uuid,
                "duration_sec": int(result["duration"]),
                "usage": result.get("usage", {}),
                "tool_calls": result.get("tool_calls", []),
            }

        raise HTTPException(429, f"所有后端均失败: {last_error}")
    finally:
        if session:
            try:
                session.close()
            except Exception:
                pass
        _session_manager.release()


@app.post("/shutdown")
def shutdown():
    """关闭 planner 服务自身（清理所有 tmux 会话后退出）"""
    import os
    import signal

    def _do_shutdown():
        time.sleep(0.5)  # 等响应返回
        if _session_manager:
            _session_manager.cleanup_all()
        log.info("Planner API 服务已关闭")
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return {"status": "shutting_down", "message": "服务正在关闭，所有 tmux 会话将被清理"}


# ==================== 启动 ====================

def main():
    parser = argparse.ArgumentParser(description="Claude Planner API 服务")
    parser.add_argument("--port", type=int, default=8899, help="监听端口（默认 8899）")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    args = parser.parse_args()

    import uvicorn
    print(f"\n  Claude Planner API · http://{args.host}:{args.port}")
    print(f"  后端配置: {BACKENDS_PATH}")
    print(f"  数据库:   {SKILL_DIR / 'planner.db'}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
