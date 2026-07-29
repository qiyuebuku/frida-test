"""后台异步任务执行器 - 全部基于 tmux 控制 Claude CLI 交互模式

核心架构：
- 所有 Claude 调用都通过 tmux 持久会话（支持追问、中途插入消息）
- 不同 model 使用不同 tmux 会话（model 变化时自动切换）
- 每个任务一个 tmux session，完成后保留用于追问，30 分钟无活动自动清理
"""

import atexit
import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from src.infrastructure.db import task_db
from src.infrastructure.db import ocr_db
from src.infrastructure.tools.ocr_service import OCRService
import src.domain.task.handlers as handlers

from src.domain.task.models import (
    SYNC_TASK_TYPES, SESSION_PREFIX, PROMPT_CHAR, SESSION_IDLE_TIMEOUT
)
from src.infrastructure.tmux.session import (
    _tmux, _extract_content, _is_thinking, _is_at_prompt,
    ClaudeTmuxSession,
)
from src.infrastructure.config.settings import SKILL_DIR, OCR_URL

logger = logging.getLogger(__name__)


# ==================== 任务执行器 ====================


class TaskExecutor:
    MAX_CONCURRENT = 2

    def __init__(self):
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT)
        self._ocr = OCRService(OCR_URL)
        # 任务级别的 tool_calls 累积器（跨多次调用）
        self._task_tool_calls: dict[int, list] = {}
        self._task_start_times: dict[int, float] = {}
        # 待处理的追问消息队列
        self._pending_messages: dict[int, list[str]] = {}
        self._message_lock = threading.Lock()
        # tmux 会话管理
        self._tmux_sessions: dict[int, ClaudeTmuxSession] = {}
        self._session_last_active: dict[int, float] = {}
        self._tmux_lock = threading.Lock()
        # tmux 可用性检查（不可用直接报错）
        self._check_tmux()
        # 启动会话清理线程
        cleanup = threading.Thread(target=self._cleanup_sessions_loop, daemon=True)
        cleanup.start()
        # 退出时清理所有会话
        atexit.register(self._cleanup_all_sessions)

    @staticmethod
    def _check_tmux():
        """检查 tmux 是否可用，不可用直接报错"""
        try:
            r = subprocess.run(["tmux", "-V"], capture_output=True, timeout=5)
            if r.returncode == 0:
                logger.info(f"[tmux] 可用: {r.stdout.strip()}")
                return
        except Exception:
            pass
        raise RuntimeError(
            "tmux 未安装或不可用，TaskExecutor 依赖 tmux 控制 Claude CLI。"
            "请先安装: apt install tmux"
        )

    def _cleanup_sessions_loop(self):
        """后台线程：清理超时的 tmux 会话"""
        while True:
            time.sleep(300)  # 每 5 分钟检查一次
            now = time.time()
            to_remove = []
            with self._tmux_lock:
                for task_id, last_active in self._session_last_active.items():
                    if now - last_active > SESSION_IDLE_TIMEOUT:
                        to_remove.append(task_id)
            for task_id in to_remove:
                with self._tmux_lock:
                    session = self._tmux_sessions.pop(task_id, None)
                    self._session_last_active.pop(task_id, None)
                if session:
                    logger.info(f"[tmux] 清理过期会话 task_id={task_id}")
                    session.close()

    def _cleanup_all_sessions(self):
        """退出时清理所有 tmux 会话"""
        with self._tmux_lock:
            for session in self._tmux_sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._tmux_sessions.clear()
            self._session_last_active.clear()

    def _send_to_tmux_directly(self, task_id: int, message: str):
        """当 session 对象不在内存时（如服务重启），直接通过 tmux 命令发送消息"""
        # 尝试所有可能的 session name 格式
        for session_name in [f"{SESSION_PREFIX}{task_id}", f"{SESSION_PREFIX}{task_id}_sonnet",
                             f"{SESSION_PREFIX}{task_id}_haiku", f"{SESSION_PREFIX}{task_id}_opus"]:
            try:
                # 检查 session 是否存在
                r = subprocess.run(["tmux", "has-session", "-t", session_name],
                                   capture_output=True, timeout=5)
                if r.returncode == 0:
                    _tmux("send-keys", "-t", session_name, message, "Enter")
                    logger.info(f"[tmux] 直接转发消息到 {session_name}: {message[:50]}")
                    return
            except Exception as e:
                logger.warning(f"[tmux] 转发失败 {session_name}: {e}")
        logger.warning(f"[tmux] task_id={task_id} 无法找到活跃的 tmux 会话")

    def _get_or_create_session(self, task_id: int, cwd: str = None,
                               model: str = None,
                               resume_session_id: str = None) -> ClaudeTmuxSession:
        """获取或创建任务的 tmux 会话

        如果已有会话且 model 相同 → 复用
        如果 model 不同 → 关闭旧会话，创建新会话
        如果提供 resume_session_id → 使用 --resume 恢复之前的 Claude 会话
        """
        with self._tmux_lock:
            session = self._tmux_sessions.get(task_id)
            if session and session.ready:
                # 未指定 model 或 model 相同 → 复用（追问时不传 model，直接复用）
                if model is None or session.model == model:
                    self._session_last_active[task_id] = time.time()
                    return session
                # model 明确不同，关闭旧会话
                session.close()
            # 创建新会话（可能是 resume 模式）
            session = ClaudeTmuxSession(task_id, cwd=cwd, model=model,
                                        resume_session_id=resume_session_id)
            self._tmux_sessions[task_id] = session
            self._session_last_active[task_id] = time.time()
            # 将 claude_session_id 写入数据库
            task_db.update_task(task_id, session_id=session.claude_session_id)
            return session

    def submit(self, task_id: int):
        thread = threading.Thread(target=self._run, args=(task_id,), daemon=True)
        thread.start()

    def _run(self, task_id: int):
        acquired = self._semaphore.acquire(timeout=300)
        if not acquired:
            task_db.update_task(task_id, status="failed", error_msg="服务器繁忙，请稍后重试")
            return

        try:
            task = task_db.get_task(task_id)
            if not task:
                return

            task_db.update_task(task_id, status="processing", started_at=datetime.now())
            start_time = time.time()
            self._task_tool_calls[task_id] = []
            self._task_start_times[task_id] = start_time

            skill_name = task.get("skill_name") or ""
            command_id = task.get("command_id") or task.get("task_type", "")

            # 路径 1：executor=claude → 通用 claude 执行
            if self._is_claude_command(skill_name, command_id):
                self._execute_claude(task, skill_name, command_id)
            else:
                # 路径 2：查 handler 注册表
                handler = handlers.get_handler(skill_name, command_id)
                if not handler:
                    raise ValueError(
                        f"未注册 handler: ({skill_name}, {command_id})。"
                        f"如果需要 Claude 执行，请在 SKILL.md 中标记 executor: claude"
                    )
                handler(self, task)

            elapsed = int(time.time() - start_time)
            # 检查是否已被标记为 failed（handler 内部可能已标记）
            current = task_db.get_task(task_id)
            if current and current["status"] == "processing":
                task_db.update_task(task_id,
                    status="completed", progress=100, progress_msg=None,
                    partial_result=None,
                    completed_at=datetime.now(), duration_sec=elapsed
                )
                current = task_db.get_task(task_id)

            # 初始化 messages 数组：确保开头有 user/assistant 消息对
            if current and current.get("session_id"):
                existing_messages = current.get("messages") or []
                user_content = (current.get("title") or
                                current.get("command_id") or
                                current.get("task_type") or "任务")
                created_at = current.get("created_at", "")
                if hasattr(created_at, "isoformat"):
                    created_at = created_at.isoformat()
                else:
                    created_at = str(created_at)
                initial_pair = [
                    {"role": "user", "content": user_content,
                     "created_at": created_at},
                    {"role": "assistant", "content": current.get("result") or "",
                     "created_at": datetime.now().isoformat()}
                ]
                # 如果已有消息（如追问），插在初始消息对之后
                if existing_messages:
                    # 过滤掉可能重复的初始消息
                    has_initial = any(m.get("content") == user_content and m.get("role") == "user"
                                     for m in existing_messages)
                    if not has_initial:
                        messages = initial_pair + existing_messages
                    else:
                        messages = existing_messages
                else:
                    messages = initial_pair
                task_db.update_task(task_id, messages=messages)

            # 处理排队的追问消息
            with self._message_lock:
                pending = self._pending_messages.pop(task_id, [])
            logger.info(f"[_run] task_id={task_id} streaming结束后检查pending消息数={len(pending)}")
            if pending:
                for msg in pending:
                    messages = task_db.get_task(task_id).get("messages") or []
                    messages.append({"role": "user", "content": msg,
                                     "created_at": datetime.now().isoformat()})
                    task_db.update_task(task_id, messages=messages)

                    # 追问时不清空 tool_calls，保留历史
                    result = self._run_claude_streaming(
                        task_id, msg, timeout=600,
                        progress_range=(0, 90), estimated_tools=20,
                        emit_done=False, skip_expand=True)
                    if result:
                        messages = task_db.get_task(task_id).get("messages") or []
                        messages.append({"role": "assistant", "content": result,
                                         "created_at": datetime.now().isoformat()})
                        task_db.update_task(task_id, result=result, messages=messages)
                current = task_db.get_task(task_id)

        except Exception as e:
            logger.exception(f"Task {task_id} failed")
            error_msg = str(e)[:500]
            task_db.update_task(task_id, status="failed", error_msg=error_msg)
        finally:
            self._task_tool_calls.pop(task_id, None)
            self._task_start_times.pop(task_id, None)
            # 注意：不清理 tmux 会话，保留用于追问
            self._semaphore.release()

    def destroy_task(self, task_id: int):
        """彻底销毁任务：关闭 tmux 会话 + 删除 Claude CLI 会话文件"""
        # 1. 关闭 tmux 会话
        with self._tmux_lock:
            session = self._tmux_sessions.pop(task_id, None)
            self._session_last_active.pop(task_id, None)
        if session:
            session.close()
            # 2. 删除 Claude CLI 会话文件
            self._delete_claude_session(session.claude_session_id)
        else:
            # 会话可能不在内存中（服务重启后），从 DB 获取 session_id
            task = task_db.get_task(task_id)
            if task and task.get("session_id"):
                self._delete_claude_session(task["session_id"])
            # 尝试 kill 可能存在的 tmux session
            for suffix in ["", "_sonnet", "_haiku"]:
                name = f"{SESSION_PREFIX}{task_id}{suffix}"
                subprocess.run(["tmux", "kill-session", "-t", name],
                               capture_output=True, timeout=5)
        # 3. 清理待发送消息
        with self._message_lock:
            self._pending_messages.pop(task_id, None)
        self._task_tool_calls.pop(task_id, None)
        self._task_start_times.pop(task_id, None)
        logger.info(f"[destroy] task_id={task_id} 任务已销毁")

    @staticmethod
    def _delete_claude_session(session_id: str):
        """删除 Claude CLI 会话文件（~/.claude/projects/*/sessions/）"""
        if not session_id:
            return
        home = Path.home()
        # Claude Code 会话文件存储在 ~/.claude/projects/<project-hash>/sessions/<session-id>.jsonl
        projects_dir = home / ".claude" / "projects"
        if not projects_dir.exists():
            return
        deleted = 0
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            sessions_dir = project_dir / "sessions"
            if not sessions_dir.exists():
                continue
            for ext in [".jsonl", ".json"]:
                session_file = sessions_dir / f"{session_id}{ext}"
                if session_file.exists():
                    session_file.unlink()
                    deleted += 1
                    logger.info(f"[destroy] 删除会话文件: {session_file}")
        if deleted == 0:
            logger.debug(f"[destroy] 未找到会话文件 session_id={session_id[:8]}...")

    def force_stop(self, task_id: int) -> bool:
        """停止正在运行的任务 - 发送 Escape 并立即更新状态，后台异步捕获展开内容"""
        with self._tmux_lock:
            session = self._tmux_sessions.get(task_id)
        if session:
            _tmux("send-keys", "-t", session.session_name, "Escape")
            task_db.update_task(task_id, status="stopped", error_msg="用户手动停止",
                               completed_at=datetime.now())
            # 后台异步：等 Escape 生效后捕获展开内容
            import threading
            def _bg_capture():
                try:
                    # 等待 Escape 生效
                    for _ in range(20):
                        time.sleep(0.2)
                        screen = session.capture(-50)
                        if 'Interrupted' in screen or '>' in screen.split('\n')[-1]:
                            break
                    self._capture_expanded(task_id, session)
                    logger.info(f"[force_stop] task_id={task_id} 展开内容捕获完成")
                except Exception as e:
                    logger.warning(f"[force_stop] task_id={task_id} 捕获展开内容失败: {e}")
            threading.Thread(target=_bg_capture, daemon=True).start()
            return True
        return False

    def queue_message(self, task_id: int, message: str) -> dict:
        """向任务发送追问消息

        - 任务执行中 → 放入 _pending_messages，由 _run_claude_streaming 循环
          在 Claude 回到提示符时自动发送（避免在 TUI 忙碌时发 send-keys 导致 Enter 被吞）
        - 任务已完成 → 启动 submit_followup（用 --resume 恢复会话）
        """
        task = task_db.get_task(task_id)
        if not task:
            raise ValueError("Task not found")

        logger.info(f"[queue_message] task_id={task_id} status={task['status']} message={message[:80]}")

        has_active_session = task_id in self._tmux_sessions
        if task["status"] in ("processing", "pending") and has_active_session:
            # 有活跃会话 → 放入队列，由 _run_claude_streaming 在提示符出现时发送
            with self._message_lock:
                self._pending_messages.setdefault(task_id, []).append(message)
            logger.info(f"[queue_message] task_id={task_id} 消息已加入待发送队列，等待提示符出现后发送")
            messages = task.get("messages") or []
            messages.append({"role": "user", "content": message,
                             "created_at": datetime.now().isoformat()})
            task_db.update_task(task_id, messages=messages)
            return {"queued": True}
        else:
            # 任务已完成/停止，立即启动追问
            logger.info(f"[queue_message] task_id={task_id} 任务已完成，启动 submit_followup")
            self.submit_followup(task_id, message)
            return {"queued": False}

    def submit_followup(self, task_id: int, message: str):
        """提交追问，复用或新建 tmux 会话"""
        task = task_db.get_task(task_id)
        if not task:
            raise ValueError("Task not found")

        # 追加用户消息
        messages = task.get("messages") or []
        messages.append({"role": "user", "content": message,
                         "created_at": datetime.now().isoformat()})
        # 检查是否有活跃 tmux 会话
        has_active_session = task_id in self._tmux_sessions
        # 不清除 result，保留上次结果供 UI 展示
        if has_active_session:
            # 有活跃会话 → 直接发消息，不需要恢复进度
            task_db.update_task(task_id, messages=messages, status="processing",
                               progress=25, partial_result=None)
        else:
            # 无活跃会话 → 需要恢复，显示恢复进度
            task_db.update_task(task_id, messages=messages, status="processing",
                               progress=5, progress_msg="恢复会话中...",
                               partial_result=None)

        thread = threading.Thread(target=self._run_followup, args=(task_id,), daemon=True)
        thread.start()

    def _run_followup(self, task_id: int):
        """追问执行线程 — 直接复用已有 tmux 会话发消息"""
        acquired = self._semaphore.acquire(timeout=300)
        if not acquired:
            task_db.update_task(task_id, status="completed", error_msg="服务器繁忙")
            return
        try:
            # 追问时保留已有 tool_calls 历史，不清空
            if task_id not in self._task_tool_calls:
                self._task_tool_calls[task_id] = []
            self._task_start_times[task_id] = time.time()

            task = task_db.get_task(task_id)
            db_session_id = task.get("session_id")

            # 检查是否有现有 tmux 会话可复用
            with self._tmux_lock:
                has_existing = task_id in self._tmux_sessions

            if has_existing:
                logger.info(f"[followup] task_id={task_id} 复用现有 tmux 会话")
            elif db_session_id:
                logger.info(f"[followup] task_id={task_id} 无 tmux 会话，将 --resume {db_session_id[:8]}...")
            else:
                logger.info(f"[followup] task_id={task_id} 无会话可恢复，创建新会话")

            while True:
                task = task_db.get_task(task_id)
                messages = task.get("messages") or []

                last_user_msg = None
                for m in reversed(messages):
                    if m.get("role") == "user":
                        last_user_msg = m["content"]
                        break
                if not last_user_msg:
                    break

                prompt = last_user_msg

                # 获取 skill 路径（如果有）
                skill_name = task.get("skill_name")
                cwd = None
                if skill_name:
                    import src.infrastructure.tools.skill_registry as sr
                    if sr.skill_registry:
                        skill = sr.skill_registry.get_skill(skill_name)
                        if skill:
                            cwd = skill.path

                # 有现有会话 → 直接发消息（不传 resume）；无会话 → 用 resume 恢复
                resume_id = None
                with self._tmux_lock:
                    has_existing = task_id in self._tmux_sessions
                if not has_existing and db_session_id:
                    resume_id = db_session_id

                result = self._run_claude_streaming(
                    task_id, prompt, timeout=600,
                    progress_range=(25, 90), estimated_tools=20,
                    emit_done=False, cwd=cwd,
                    resume_session_id=resume_id,
                    skip_expand=True)

                if result:
                    messages = task_db.get_task(task_id).get("messages") or []
                    messages.append({"role": "assistant", "content": result,
                                     "created_at": datetime.now().isoformat()})
                    task_db.update_task(task_id, messages=messages, result=result)

                # 检查更多待处理消息
                with self._message_lock:
                    pending = self._pending_messages.pop(task_id, [])
                if not pending:
                    break

                for msg in pending:
                    messages = task_db.get_task(task_id).get("messages") or []
                    messages.append({"role": "user", "content": msg,
                                     "created_at": datetime.now().isoformat()})
                    task_db.update_task(task_id, messages=messages)

            # 追问全部完成后，做一次展开保存完整内容
            with self._tmux_lock:
                session = self._tmux_sessions.get(task_id)
            if session:
                self._capture_expanded(task_id, session)

            # 检查是否已被用户停止，避免覆盖 stopped 状态
            current = task_db.get_task(task_id)
            if not current or current["status"] == "stopped":
                return
            task_db.update_task(task_id, status="completed", progress=100,
                               completed_at=datetime.now())
        except Exception as e:
            logger.exception(f"Follow-up task {task_id} failed")
            task_db.update_task(task_id, status="completed", error_msg=str(e)[:500])
        finally:
            self._task_tool_calls.pop(task_id, None)
            self._task_start_times.pop(task_id, None)
            self._semaphore.release()

    def _progress(self, task_id: int, progress: int, msg: str):
        task_db.update_task(task_id, progress=progress, progress_msg=msg)

    def _emit_step(self, task_id: int, display: str, detail: str = "",
                   progress: int = None, output: str = None):
        """记录一个处理步骤到 tool_calls"""
        start_time = self._task_start_times.get(task_id, time.time())
        tool_calls = self._task_tool_calls.get(task_id, [])
        entry = {
            "tool": "_step",
            "display": display,
            "detail": detail,
            "output": output,
            "at": round(time.time() - start_time, 1),
        }
        tool_calls.append(entry)
        if progress:
            self._progress(task_id, progress, display)
        task_db.update_task(task_id, tool_calls=tool_calls)

    # ==================== OCR 公共步骤 ====================

    def _do_ocr(self, task_id: int, image_path: str, action: str, client_id: str = None):
        """执行 OCR 并保存到 sa_ocr_records，返回 (raw_text, markdown, ocr_id)"""
        self._emit_step(task_id, "OCR 识别中...", f"图片: {image_path}", progress=5)

        loop = asyncio.new_event_loop()
        try:
            ocr_result = loop.run_until_complete(self._ocr.recognize(image_path))
        finally:
            loop.close()

        raw_text = ocr_result.get("raw_ocr_result", "")
        markdown = ocr_result.get("markdown_result", "")

        ocr_id = ocr_db.save_ocr_record(
            action=action, raw_text=raw_text, markdown_text=markdown,
            image_path=image_path, client_id=client_id
        )
        task_db.update_task(task_id, ocr_record_id=ocr_id)
        ocr_output_parts = []
        if markdown:
            ocr_output_parts.append("## 结构化内容\n\n" + markdown)
        if raw_text:
            ocr_output_parts.append("## 纯文本\n\n" + raw_text)
        self._emit_step(task_id, "OCR 识别完成",
                        f"识别 {len(raw_text)} 字符", progress=20,
                        output="\n\n---\n\n".join(ocr_output_parts) or "")

        return raw_text, markdown, ocr_id

    def _do_structurize(self, task_id: int, ocr_id: int, raw_text: str, markdown: str):
        """用 tmux Claude 会话结构化 OCR 文本，写入 sa_ocr_records，返回 structured dict"""
        prompt = f"""请将以下 OCR 识别的文本转换为结构化 JSON。

根据内容自动判断数据类型，提取关键字段。输出严格的 JSON（不要 markdown 代码块包裹）。

常见场景示例：

1. 基金/股票持仓截图：
{{
  "data_type": "fund_holdings",
  "total_assets": 数字或null,
  "total_profit": 数字或null,
  "daily_pnl": 数字或null,
  "holdings": [
    {{
      "fund_name": "基金名称",
      "fund_code": "基金代码6位数字或null",
      "current_value": 数字或null,
      "daily_profit": 数字或null,
      "total_profit": 数字或null,
      "profit_rate": 小数或null
    }}
  ]
}}

2. 其他类型数据：根据内容自行设计合理的 JSON 结构，顶层必须包含 "data_type" 字段标识数据类型（如 "table"、"receipt"、"invoice"、"chat_record" 等），其余字段根据实际内容提取。

OCR 文本：
{markdown or raw_text}"""

        self._emit_step(task_id, "数据结构化", "AI 提取关键字段",
                        progress=22, output=prompt)

        result = self._run_claude_streaming(task_id, prompt,
            timeout=600, progress_range=(22, 35), estimated_tools=2,
            emit_done=False, model="haiku")
        if not result:
            self._emit_step(task_id, "结构化跳过", "Claude 未返回结果", progress=35)
            return None

        structured = self._extract_json(result)
        if not structured:
            ocr_db.update_ocr_structured_data(ocr_id, result, None)
            self._emit_step(task_id, "结构化完成", "未能解析为 JSON", progress=35)
            return None

        structured_str = json.dumps(structured, ensure_ascii=False)
        ocr_db.update_ocr_structured_data(ocr_id, structured_str, structured_str)
        task_db.update_task(task_id, result_data=structured)
        self._emit_step(task_id, "数据结构化完成",
                        f"提取 {len(structured)} 个字段", progress=35,
                        output=structured_str[:500])

        return structured

    # ==================== Skill claude 执行 ====================

    def _is_claude_command(self, skill_name: str, command_id: str) -> bool:
        """检查 SKILL.md 中该命令是否标记为 executor: claude"""
        import src.infrastructure.tools.skill_registry as sr
        if not sr.skill_registry or not skill_name:
            return False
        skill = sr.skill_registry.get_skill(skill_name)
        if not skill:
            return False
        command = skill.get_command(command_id)
        return command is not None and command.executor == "claude"

    @staticmethod
    def _read_skill_body(skill_path: str) -> str:
        """读取 SKILL.md 中 frontmatter 之后的正文部分"""
        md_path = Path(skill_path) / "SKILL.md"
        if not md_path.exists():
            return ""
        text = md_path.read_text(encoding="utf-8")
        # 跳过 frontmatter（--- ... ---）
        m = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.DOTALL)
        body = text[m.end():] if m else text
        return body.strip()

    def _execute_claude(self, task: dict, skill_name: str, command_id: str):
        """通用 claude 执行：在 skill 目录下通过 tmux 交互"""
        import src.infrastructure.tools.skill_registry as sr
        skill = sr.skill_registry.get_skill(skill_name)
        command = skill.get_command(command_id)
        task_id = task["id"]
        config = task.get("config") or {}
        if isinstance(config, str):
            config = json.loads(config)

        # 构建 prompt：注入 SKILL.md 正文，避免 Claude 自己去读
        skill_body = self._read_skill_body(skill.path)
        args_str = ""
        if config.get("args"):
            args_str = "\n".join(f"- {k}: {v}" for k, v in config["args"].items())
        input_str = config.get("input_data", "")

        # 创建任务专属输出目录
        output_dir = Path(skill.path) / "output" / str(task_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        parts = [f"# 任务: {command.name}"]
        parts.append(f"## 输出目录\n所有产出文件必须放到: `{output_dir}`\n命令行中用 `--output {output_dir}` 或手动将结果写入此目录。")
        if args_str:
            parts.append(f"## 参数\n{args_str}")
        if input_str:
            parts.append(f"## 输入\n{input_str}")
        if skill_body:
            parts.append(f"## Skill 知识库（直接参考，不要再读 SKILL.md）\n\n{skill_body}")

        # 用户自定义规则（优先级最高）
        _, user_rules = self._get_custom_config(task)
        req_lines = [
            "- 直接按上述知识库执行，不要先读 SKILL.md 或源码来了解结构",
            "- **必须使用 Skill 知识库中指定的工具/脚本**，禁止自己写脚本替代（如手写 curl+python 转换 HTML）",
            "- 减少解释性文字，专注执行",
            "- 遇到问题时直接修复，不要反复描述问题",
            f"- **所有产出文件必须输出到 `{output_dir}`**，不要输出到其他位置",
        ]
        if user_rules:
            req_lines.insert(0, f"- **用户额外要求（必须遵守）**: {user_rules}")
        parts.append(f"## 执行要求\n" + "\n".join(req_lines))

        prompt = "\n\n".join(parts)
        # system_prompt 仍走 _apply_custom_prompt，但 rules 已在上面处理
        system_prompt, _ = self._get_custom_config(task)
        if system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"

        self._emit_step(task_id, f"执行 {command.name}", command.description,
                        progress=5, output=prompt)

        report = self._run_claude_streaming(task_id, prompt,
            cwd=skill.path, timeout=600, progress_range=(5, 90), estimated_tools=30,
            emit_done=False)

        # 检查是否已被用户停止，避免覆盖 stopped 状态
        current = task_db.get_task(task_id)
        if current and current["status"] == "stopped":
            if report:
                summary = self._extract_summary(report)
                task_db.update_task(task_id, summary=summary, result=report)
            return

        if not report:
            task_db.update_task(task_id, status="failed", error_msg="执行超时或失败")
            return

        summary = self._extract_summary(report)
        task_db.update_task(task_id,
            status="completed", progress=100, summary=summary,
            result=report, completed_at=datetime.now())

    # ==================== 自定义配置 ====================

    def _get_custom_config(self, task: dict) -> tuple[str | None, str | None]:
        """从 task.config 中读取自定义 system_prompt 和 rules"""
        config = task.get("config")
        if not config:
            return None, None
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                return None, None
        return config.get("system_prompt"), config.get("rules")

    def _apply_custom_prompt(self, default_prompt: str, task: dict) -> str:
        """将自定义提示词和规则拼接到默认 prompt"""
        system_prompt, rules = self._get_custom_config(task)
        if system_prompt:
            default_prompt = f"{system_prompt}\n\n{default_prompt}"
        if rules:
            default_prompt = f"{default_prompt}\n\n额外规则：\n{rules}"
        return default_prompt

    # ==================== Claude tmux 调用 ====================

    # 工具命令 → 可读描述的映射
    TOOL_DISPLAY_MAP = {
        "market_overview": "采集市场环境数据",
        "hot_board": "查看热门板块",
        "news_overview": "获取新闻快讯",
        "sector_rank": "查看板块排名",
        "headlines": "获取推荐头条",
        "flash_news": "获取快讯",
        "evaluate": "计算量化信号",
        "snapshot": "获取持仓快照",
        "scan-summary": "扫描基金概况",
    }

    def _text_step_display(self, text: str) -> str | None:
        """从 Claude 文本中提取简短的步骤标题（不超过 30 字符）"""
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            clean = line.lstrip("#").strip().strip("*").strip()
            if len(clean) <= 3:
                continue
            # 截取第一个句号/逗号/分号前的短语作为标题
            for sep in ('。', '，', '；', '、', '：', '，', '. ', ', '):
                idx = clean.find(sep)
                if 3 < idx <= 30:
                    return clean[:idx]
            return clean[:30]
        return None

    def _tool_display(self, tool_name: str, tool_input: dict) -> str:
        """将工具调用转为可读描述"""
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if "client.py" in cmd:
                parts = cmd.split()
                idx = next((i for i, p in enumerate(parts) if p.endswith("client.py")), -1)
                if idx >= 0:
                    args = parts[idx + 1:]
                    for key, desc in self.TOOL_DISPLAY_MAP.items():
                        if key in args:
                            return desc
                    if len(args) >= 2 and args[0].isdigit():
                        action_map = {"detail": "基金详情", "rank": "排名数据",
                                      "holdings": "持仓信息", "news": "相关资讯",
                                      "nav": "历史净值", "rsi": "RSI 指标"}
                        desc = action_map.get(args[1], args[1])
                        return f"查询 {args[0]} {desc}"
                    if args:
                        return f"执行 {' '.join(args[:3])}"
            if "news_overview" in cmd or "news" in cmd:
                return "获取新闻快讯"
            if "curl" in cmd:
                return "请求外部数据"
            return "执行命令"
        elif tool_name == "Read":
            path = tool_input.get("file_path", "")
            fname = path.split("/")[-1] if "/" in path else path
            return f"读取 {fname}"
        elif tool_name in ("Glob", "Grep"):
            return "搜索文件"
        return tool_name

    def _tool_detail(self, tool_name: str, tool_input: dict) -> str:
        """将工具调用的输入转为可读详情文本"""
        if tool_name == "Bash":
            return tool_input.get("command", "")
        elif tool_name == "Read":
            return tool_input.get("file_path", "")
        elif tool_name == "Glob":
            return tool_input.get("pattern", "")
        elif tool_name == "Grep":
            return tool_input.get("pattern", "")
        return json.dumps(tool_input, ensure_ascii=False)[:500] if tool_input else ""

    def _run_claude_streaming(self, task_id: int, prompt: str,
                              timeout: int = 600,
                              progress_range: tuple = (35, 90),
                              estimated_tools: int = 20,
                              cwd: str = None,
                              emit_done: bool = True,
                              model: str = None,
                              resume_session_id: str = None,
                              skip_expand: bool = False) -> str | None:
        """通过 tmux 发送 prompt 到 Claude CLI 交互模式，实时推送新内容

        - 所有 Claude 调用统一走 tmux
        - model 不同时自动切换会话（如 haiku 用独立会话）
        - resume_session_id: 使用 --resume 恢复之前的 Claude 会话
        """
        session = self._get_or_create_session(task_id, cwd=cwd, model=model,
                                               resume_session_id=resume_session_id)
        if not session.ready:
            raise RuntimeError(
                f"tmux 会话 {session.session_name} 未就绪，Claude CLI 启动失败")

        # 会话就绪后，确保 progress >= 25 以触发客户端切换到终端视图
        start_pct, end_pct = progress_range
        ready_pct = max(start_pct, 25)
        self._progress(task_id, ready_pct, "Claude CLI 就绪")
        tool_calls = self._task_tool_calls.get(task_id, [])
        task_start_time = self._task_start_times.get(task_id, time.time())

        # 记录发送前的屏幕状态
        session.mark()
        # 把 prompt 本身也加入已知行（避免把问题回显当作回答）
        for line in prompt.split('\n'):
            stripped = line.strip()
            if stripped:
                session._known_lines.add(stripped)

        # 发送 prompt
        logger.info(f"[tmux] task_id={task_id} 发送消息到 Claude CLI: {prompt[:80]}")
        session.send(prompt)

        # 轮询等待回答
        accumulated_lines: list[str] = []
        printed_set: set[str] = set()  # 已推送过的行（去重）
        start = time.time()
        idle_count = 0
        has_real_output = False  # 是否已收到真正的输出（防止刚启动就误判完成）
        last_content = ""
        last_db_write = time.time()
        last_raw_screen = session.capture(-500)  # 初始化为当前屏幕，避免首次 capture 把历史全当新内容

        while time.time() - start < timeout:
            time.sleep(1)

            # 捕获原始屏幕
            raw_screen = session.capture(-500)

            # 原始屏幕有变化时，打印差异（不做任何过滤）
            if raw_screen != last_raw_screen:
                # 计算新增行
                old_lines = set(last_raw_screen.split('\n'))
                raw_new = [l for l in raw_screen.split('\n') if l not in old_lines]
                if raw_new:
                    for rl in raw_new:
                        stripped_rl = rl.strip()
                        if stripped_rl:  # 跳过纯空行
                            logger.info(f"[tmux-raw] {stripped_rl}")
                last_raw_screen = raw_screen

            # 检测阻塞对话框（权限确认、信任确认）并自动确认
            if "Do you want to proceed" in raw_screen or "requires approval" in raw_screen:
                logger.info(f"[tmux] 检测到权限确认对话框，自动确认")
                _tmux("send-keys", "-t", session.session_name, "Enter")
                time.sleep(1)
                continue
            if "trust this folder" in raw_screen.lower() or "Enter to confirm" in raw_screen:
                logger.info(f"[tmux] 检测到信任确认对话框，自动确认")
                _tmux("send-keys", "-t", session.session_name, "Enter")
                time.sleep(1)
                continue
            # 检测交互式选择菜单（checkbox/select picker），按 Esc 跳过
            if "Enter to select" in raw_screen and "Esc to cancel" in raw_screen:
                logger.info(f"[tmux] 检测到交互式选择菜单，按 Esc 跳过")
                _tmux("send-keys", "-t", session.session_name, "Escape")
                time.sleep(1)
                continue

            new_lines = session.get_new_lines()

            if new_lines:
                idle_count = 0
                has_real_output = True

                for line in new_lines:
                    if line in printed_set:
                        continue
                    accumulated_lines.append(line)
                    printed_set.add(line)

                # 推送 text_delta 事件
                new_text = "\n".join(new_lines)
                total_len = sum(len(l) for l in accumulated_lines)
                pct = min(end_pct - 5,
                          start_pct + int((end_pct - start_pct) * min(1.0, total_len / 2000)))
                # 尝试检测工具调用和有意义的文本步骤
                pending_text = []  # 累积文本行，合并为一个步骤
                for line in new_lines:
                    tool_info = self._parse_tmux_tool_line(line)
                    if tool_info:
                        logger.info(f"[tmux-debug] 检测到工具调用: {tool_info['tool']} → {tool_info['display']}")
                        # 先 flush 累积的文本
                        self._flush_text_step(
                            pending_text, tool_calls, task_id,
                            task_start_time, pct)
                        pending_text = []
                        entry = {
                            "tool": tool_info["tool"],
                            "display": tool_info["display"],
                            "detail": tool_info["detail"],
                            "output": None,
                            "at": round(time.time() - task_start_time, 1),
                        }
                        tool_calls.append(entry)
                        self._progress(task_id, pct, tool_info["display"])
                    else:
                        stripped = line.strip()
                        # ⎿ 前缀行 → 关联到最近一个 tool_call 的 output
                        if stripped.startswith('⎿') and tool_calls:
                            output_text = stripped.lstrip('⎿').strip()
                            if output_text:
                                prev = tool_calls[-1].get("output") or ""
                                tool_calls[-1]["output"] = (prev + output_text + "\n").strip()
                        # 只将 Claude 的自然语言文本（含中文）作为步骤候选
                        elif len(stripped) > 10 and self._is_claude_text(stripped):
                            # 去掉 ● 前缀，保留纯文本
                            text = stripped
                            if text.startswith('●'):
                                text = text[len('●'):].strip()
                            pending_text.append(text)
                # flush 本轮剩余文本
                self._flush_text_step(
                    pending_text, tool_calls, task_id,
                    task_start_time, pct)
            else:
                idle_count += 1

            # 有提示符时，立即检查并发送排队消息（不管 Claude 是否在 thinking）
            if session.is_at_prompt():
                with self._message_lock:
                    pending = self._pending_messages.get(task_id, [])
                if pending:
                    with self._message_lock:
                        pending = self._pending_messages.pop(task_id, [])
                    # 先保存当前回复到 messages
                    raw_result = "\n".join(accumulated_lines).strip()
                    if raw_result:
                        result = self._clean_result(raw_result)
                        messages = task_db.get_task(task_id).get("messages") or []
                        messages.append({"role": "assistant", "content": result,
                                         "created_at": datetime.now().isoformat()})
                        task_db.update_task(task_id, messages=messages, result=result,
                                           tool_calls=tool_calls if tool_calls else None)

                    msg = pending[0]
                    if len(pending) > 1:
                        with self._message_lock:
                            self._pending_messages[task_id] = pending[1:]
                    logger.info(f"[streaming] task_id={task_id} 发送排队消息: {msg[:50]}")
                    session.send(msg)
                    # 重置状态，继续监听新回复（tool_calls 保留历史，不清空）
                    session.mark()
                    accumulated_lines.clear()
                    idle_count = 0
                    has_real_output = False
                    last_content = ""
                    last_db_write = time.time()
                    continue

            # 检测回答结束：屏幕稳定 + 无 thinking 指示器 + 必须已有真正输出
            content = "\n".join(accumulated_lines)
            if content == last_content:
                if has_real_output and idle_count >= 2 and session.is_at_prompt() and not _is_thinking(raw_screen):
                    break
            else:
                last_content = content

            # 定期写 DB
            now = time.time()
            if now - last_db_write >= 3:
                if accumulated_lines or tool_calls:
                    task_db.update_task(task_id,
                        partial_result="\n".join(accumulated_lines) if accumulated_lines else None,
                        tool_calls=tool_calls if tool_calls else None,
                    )
                last_db_write = now

        # 超时检查
        if time.time() - start >= timeout:
            logger.warning(f"[tmux] task_id={task_id} 超时 ({timeout}s)")
            session.send_ctrl_c()
            return None

        # 展开工具输出（Ctrl+O），捕获完整内容存到 terminal_log_expanded
        logger.info(f"[tmux] task_id={task_id} 回答完成, tool_calls={len(tool_calls)}个, session={session.session_name}")
        if not skip_expand:
            self._capture_expanded(task_id, session)
        else:
            logger.info(f"[tmux] task_id={task_id} 跳过展开（追问模式）")

        # 提取最终结果并清理 TUI 噪音
        raw_result = "\n".join(accumulated_lines).strip()
        result = self._clean_result(raw_result)

        task_db.update_task(task_id, partial_result=None, tool_calls=tool_calls)

        # 更新活跃时间
        with self._tmux_lock:
            self._session_last_active[task_id] = time.time()

        return result or None

    @staticmethod
    def _is_claude_text(line: str) -> bool:
        """判断是否为 Claude 的自然语言文本（适合作为步骤标题）

        排除：工具输出数据、表头、错误信息
        ● 前缀的自然语言文本（如 "● 我来执行..."）视为 Claude 文本
        """
        stripped = line.strip()
        # ● 前缀：如果后面跟工具名(... 则不是文本，由 _parse_tmux_tool_line 处理
        if stripped.startswith('●'):
            rest = stripped[len('●'):].strip()
            if re.match(r'\w+\(', rest):
                return False
            # ● 后跟自然语言文本（含中文），视为 Claude 的思考/说明
            stripped = rest
        # 必须包含中文字符
        if not re.search(r'[\u4e00-\u9fff]', stripped):
            return False
        # 排除数据行
        if re.match(r'^\d{6}\s', stripped):  # 基金代码开头
            return False
        if re.match(r'^[\s]*\d+[\s]+\d+', stripped):  # 数字表格行
            return False
        # 排除表头/数据标签行（多个中文词用空格分隔，如 "序号 基金代码 基金名称"）
        if re.match(r'^[\u4e00-\u9fff]+\s{2,}[\u4e00-\u9fff]+\s{2,}', stripped):
            return False
        # 排除键值数据行（如 "基金代码: 022365", "代码 名称 状态"）
        if re.match(r'^[\u4e00-\u9fff]{2,4}[：:]\s*\S', stripped):
            return False
        # 排除错误信息
        if stripped.startswith(('File ', 'Traceback', 'Error')):
            return False
        # 排除日期开头的数据行（如 "2026-03-14 008087 hold ..."）
        if re.match(r'^\d{4}-\d{2}-\d{2}\s', stripped):
            return False
        return True

    def _flush_text_step(self, pending_text: list[str], tool_calls: list,
                         task_id: int, task_start_time: float, pct: int):
        """将累积的文本行合并为一个步骤"""
        if not pending_text:
            return
        # 取第一行作为标题，全部作为详情
        display = self._text_step_display(pending_text[0])
        if not display:
            return
        detail = "\n".join(pending_text)
        text_entry = {
            "tool": "_text",
            "display": display,
            "detail": detail,
            "output": None,
            "at": round(time.time() - task_start_time, 1),
        }
        tool_calls.append(text_entry)
        self._progress(task_id, pct, display)

    def _capture_expanded(self, task_id: int, session: 'ClaudeTmuxSession'):
        """发送 Ctrl+O 展开工具输出，捕获完整内容存到 terminal_log_expanded，再折叠回去"""
        from src.interfaces.api.routes.terminal import _expanding_tasks

        # 检查是否有折叠块
        before_screen = session.capture(-5000)
        collapsed_count = before_screen.count('ctrl+o')
        if collapsed_count == 0:
            logger.info(f"[tmux] task_id={task_id} 无折叠块，跳过展开")
            return

        # 标记展开中，SSE 暂停推送避免 thinking 内容闪现
        _expanding_tasks.add(task_id)
        try:
            logger.info(f"[tmux] task_id={task_id} 检测到 {collapsed_count} 个折叠块，发送 Ctrl+O 展开")
            _tmux("send-keys", "-t", session.session_name, "C-o")

            # 轮询等待展开生效（最多 5 秒）
            expanded_screen = before_screen
            collapsed_after = collapsed_count
            for _ in range(25):
                time.sleep(0.2)
                expanded_screen = session.capture(-5000)
                collapsed_after = expanded_screen.count('ctrl+o')
                if collapsed_after < collapsed_count:
                    break
            logger.info(f"[tmux] task_id={task_id} Ctrl+O 后折叠块: {collapsed_after} (之前 {collapsed_count})")

            if collapsed_after >= collapsed_count:
                # C-o 没生效，尝试 literal 方式
                logger.warning(f"[tmux] task_id={task_id} C-o 未生效，尝试 literal \\x0f")
                _tmux("send-keys", "-t", session.session_name, "-l", "\x0f")
                for _ in range(25):
                    time.sleep(0.2)
                    expanded_screen = session.capture(-5000)
                    collapsed_after = expanded_screen.count('ctrl+o')
                    if collapsed_after < collapsed_count:
                        break
                logger.info(f"[tmux] task_id={task_id} literal 后折叠块: {collapsed_after}")

            # 从 DB 读取已保存的历史（terminal_log 含 saved_prefix + ANSI），
            # 去重后拼接当前展开内容，确保历史消息不丢失且不重复
            try:
                from src.interfaces.api.routes.terminal import _strip_ansi
                existing_log = task_db.get_terminal_log(task_id) or ""
                if existing_log:
                    expanded_lines = expanded_screen.strip().split('\n')
                    existing_lines = existing_log.strip().split('\n')
                    # 找展开内容的第一个非空行在历史中的位置
                    # 注意：existing_lines 含 ANSI 转义码，expanded_lines 是纯文本，需要去 ANSI 后比较
                    first_line = ""
                    for el in expanded_lines[:10]:
                        if el.strip():
                            first_line = el.strip()
                            break
                    cut_at = len(existing_lines)
                    if first_line:
                        for i, hl in enumerate(existing_lines):
                            if _strip_ansi(hl).strip() == first_line:
                                cut_at = i
                                break
                    history_prefix = existing_lines[:cut_at]
                    if history_prefix:
                        full_expanded = '\n'.join(history_prefix) + '\n' + '─' * 60 + '\n' + expanded_screen
                    else:
                        full_expanded = expanded_screen
                else:
                    full_expanded = expanded_screen
                task_db.update_task(task_id, terminal_log_expanded=full_expanded)
                logger.info(f"[tmux] task_id={task_id} 展开内容已保存 ({len(full_expanded)} 字符)")
            except Exception as e:
                logger.warning(f"[tmux] task_id={task_id} 保存展开内容失败: {e}")

            # 立即折叠回去
            if collapsed_after < collapsed_count:
                logger.info(f"[tmux] task_id={task_id} 发送 Ctrl+O 折叠回去")
                _tmux("send-keys", "-t", session.session_name, "C-o")
                time.sleep(0.3)
        finally:
            _expanding_tasks.discard(task_id)

    @staticmethod
    def _parse_tmux_tool_line(line: str) -> dict | None:
        """尝试从 tmux 输出行中检测 Claude 工具调用

        Claude CLI TUI 中工具调用通常显示为：
          ⏺ ToolName(param: "value")
          ● ToolName(...)
        """
        stripped = line.strip()
        for marker in ('⏺', '●', '◆', '▶'):
            if stripped.startswith(marker):
                rest = stripped[len(marker):].strip()
                m = re.match(r'(\w+)\((.*)$', rest)
                if m:
                    tool_name = m.group(1)
                    detail = m.group(2).rstrip(')')
                    # display 展示工具名+简化参数（如 Bash: python client.py snapshot）
                    if tool_name == "Bash":
                        cmd = detail.strip('"').strip("'")
                        if len(cmd) > 80:
                            cmd = cmd[:77] + "..."
                        display = f"Bash: {cmd}" if cmd else "执行命令"
                    elif tool_name == "Read":
                        fname = detail.split("/")[-1].strip('"').strip("'")
                        display = f"Read: {fname}" if fname else "读取文件"
                    elif tool_name == "Grep":
                        display = f"Grep: {detail[:60]}" if detail else "搜索内容"
                    elif tool_name == "Glob":
                        display = f"Glob: {detail[:60]}" if detail else "搜索文件"
                    elif tool_name == "Edit":
                        fname = detail.split("/")[-1].split(",")[0].strip('"').strip("'")
                        display = f"Edit: {fname}" if fname else "编辑文件"
                    elif tool_name == "Write":
                        fname = detail.split("/")[-1].split(",")[0].strip('"').strip("'")
                        display = f"Write: {fname}" if fname else "写入文件"
                    else:
                        display = f"{tool_name}({detail[:50]})" if detail else tool_name
                    return {"tool": tool_name, "display": display, "detail": detail}
        return None

    # ==================== 结果后处理 ====================

    @staticmethod
    def _clean_result(text: str) -> str:
        """后处理：从混合了 TUI 噪音的原始内容中提取 Claude 的实际回答

        策略：
        1. 逐行清理明确的噪音（● 前缀、工具名、traceback、⎿ 输出等）
        2. 如果发现报告起始标记，只保留报告部分
        """
        if not text:
            return text

        lines = text.split('\n')
        cleaned = []
        in_traceback = False

        for line in lines:
            stripped = line.strip()

            if not stripped:
                if cleaned:
                    cleaned.append('')
                continue

            if stripped.startswith('●'):
                in_traceback = False
                continue

            if stripped.startswith('⎿'):
                continue

            if re.match(r'^(Bash|Read|Grep|Glob|Skill|Write|Edit)\(', stripped):
                continue

            if stripped.startswith('Traceback (most recent call last)'):
                in_traceback = True
                continue
            if in_traceback:
                if stripped.startswith(('File ', '^')):
                    continue
                if re.match(r'^[A-Za-z_]+Error[:(]', stripped):
                    continue
                if re.match(r'^[A-Za-z_]+Exception[:(]', stripped):
                    continue
                if re.match(r'^requests\.\w+', stripped):
                    continue
                if line.startswith(('  ', '\t')):
                    continue
                in_traceback = False

            if re.match(r'^={5,}$', stripped):
                continue

            if re.match(r'^(Reading|Writing)\s+\d+\s+file', stripped):
                continue

            cleaned.append(stripped)

        result = '\n'.join(cleaned).strip()

        report_markers = [
            r'📊',
            r'^#\s',
            r'^一[、.]',
        ]
        for marker in report_markers:
            m = re.search(marker, result, re.MULTILINE)
            if m:
                start = m.start()
                line_start = result.rfind('\n', 0, start)
                line_start = line_start + 1 if line_start >= 0 else 0
                report = result[line_start:].strip()
                if len(report) > 100:
                    return report

        return result

    # ==================== 辅助方法 ====================

    def _extract_json(self, text: str) -> dict | None:
        """从文本中提取 JSON"""
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        json_str = m.group(1).strip() if m else text.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def _extract_summary(self, markdown: str, max_len: int = 200) -> str:
        """从 Markdown 报告中提取摘要"""
        if not markdown:
            return ""
        lines = []
        for line in markdown.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-") or line.startswith("*") or line.startswith(">") or len(line) > 5:
                clean = line.lstrip("-*> ").strip()
                if clean:
                    lines.append(clean)
            if len("；".join(lines)) > max_len:
                break
        summary = "；".join(lines)
        return summary[:max_len] if summary else markdown[:max_len]


# 全局单例
executor = TaskExecutor()
