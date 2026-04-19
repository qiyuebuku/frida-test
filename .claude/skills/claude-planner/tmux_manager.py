"""tmux Claude CLI 会话管理 — 从 smart-fund-server 精简提取

核心能力：
- 创建 tmux 会话运行 Claude CLI（支持环境变量注入控制后端）
- 发送 prompt 并流式监控输出
- 解析工具调用步骤
- 支持追问（复用或恢复会话）
- 自动清理空闲会话
"""

import atexit
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from db import update_task

log = logging.getLogger("planner.tmux")

SESSION_PREFIX = "planner_"
PROMPT_CHAR = "❯"
IDLE_TIMEOUT = 1800  # 30 分钟空闲自动清理


# ==================== tmux 底层操作 ====================

def _tmux(*args) -> str:
    try:
        r = subprocess.run(["tmux"] + list(args), capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


def _is_noise(line: str) -> bool:
    """判断是否为 Claude CLI TUI 噪音行"""
    s = line.strip()
    if not s:
        return True
    if s in (PROMPT_CHAR, '>', '↑', '?', '? for shortcuts'):
        return True
    if any(c in s for c in '╭╰╮╯▐▛▜▘▝'):
        return True
    if s.startswith('─' * 5):
        return True
    if re.match(r'^.\s+[A-Z][a-z]+…', s):
        return True
    if re.match(r'^[A-Z][a-z]+…(\s|$)', s):
        return True
    if re.match(r'^.\s+(Baked|Cooked|Done) for ', s):
        return True
    if re.match(r'^…\s*\+\d+\s+lines?\s*\(ctrl\+o', s):
        return True
    if s.startswith(PROMPT_CHAR):
        return True
    noise = [
        'medium · /effort', 'Claude Code v', 'Welcome back',
        'Opus 4.6', 'Sonnet', 'glm-', 'API Usage',
        '~/frida-test', '~/…/', '~/smart-fund',
        'Checking for update', 'MCP server',
        "What's new", 'Added ', 'Recent activity',
        'No recent activity', 'Tips for getting',
        'Run /init', '/release-notes', '/resume for more',
        'Opus now defaults', '⧉ In ', 'ctrl+g',
        '? for shortcu', 'Brewed for', 'for shortcuts',
        '◐', '◑', '◒', '◓',
        'esc to interrupt', 'esc to cancel', 'Esc to cancel',
        'Tab to amend', 'Enter to confirm',
        'has switched from npm', 'claude install',
        'Do you want to proceed',
        'Yes, and don\'t ask again',
        'This command requires approval',
        '⏵⏵ bypass permissions',
        'shift+tab to cycle',
    ]
    return any(p in s for p in noise)


def _extract_content(screen: str) -> list[str]:
    lines = []
    for line in screen.split('\n'):
        s = line.strip()
        if not _is_noise(s) and s:
            lines.append(s)
    return lines


def _is_thinking(screen: str) -> bool:
    """检测 Claude CLI 是否正在思考/执行中。

    注意：如果 ❯ 提示符已出现在 thinking 动画之后，说明已完成，返回 False。
    """
    lines = screen.strip().split('\n')
    last_thinking_idx = -1
    last_prompt_idx = -1

    for i, line in enumerate(lines[-15:]):
        s = line.strip()
        if '(ctrl+o' in s:
            continue
        if s.startswith(('●', '⎿')):
            if 'Running…' in s or 'Waiting…' in s:
                last_thinking_idx = i
            continue
        if re.search(r'[A-Z][a-z]+…', s):
            last_thinking_idx = i
        if PROMPT_CHAR in s:
            after = s.split(PROMPT_CHAR, 1)[1].strip()
            if not re.match(r'^\d+\.', after):
                last_prompt_idx = i

    # 如果提示符出现在 thinking 动画之后，说明已完成
    if last_prompt_idx > last_thinking_idx:
        return False
    return last_thinking_idx >= 0


def _is_at_prompt(screen: str) -> bool:
    """检测 Claude CLI 是否真正回到空闲 prompt。

    注意：Claude Code TUI 底部永远有一个 ❯ 输入框，
    但如果同时出现 'esc to interrupt' 说明 Claude 正在执行中，不算真正回到 prompt。
    """
    last_lines = screen.strip().split('\n')[-8:]
    last_text = '\n'.join(last_lines)

    # 如果看到 "esc to interrupt" / "esc to cancel" 说明 Claude 正在执行
    if 'esc to interrupt' in last_text or 'esc to cancel' in last_text:
        return False

    for line in last_lines[-5:]:
        s = line.strip()
        if PROMPT_CHAR in s:
            after = s.split(PROMPT_CHAR, 1)[1].strip()
            if re.match(r'^\d+\.', after):
                continue
            return True
    return False


def _parse_tmux_tool_line(line: str) -> dict | None:
    """检测 Claude 工具调用行

    支持两种格式：
    1. ● ToolName(args...)     — 展开格式
    2. Read 1 file (ctrl+o to expand)  — 折叠格式
    3. Listed 2 directories (ctrl+o to expand)  — 折叠格式
    4. Searched for 1 pattern (ctrl+o to expand)  — 折叠格式
    """
    s = line.strip()

    # 格式 1: ● ToolName(...) / ⏺ ToolName(...)
    for marker in ('⏺', '●', '◆', '▶'):
        if s.startswith(marker):
            rest = s[len(marker):].strip()
            m = re.match(r'(\w+)\((.*)$', rest)
            if m:
                tool_name = m.group(1)
                detail = m.group(2).rstrip(')')
                return {"tool": tool_name, "display": f"{tool_name}: {detail[:60]}", "detail": detail}

    # 格式 2: 折叠格式 "Read N file(s)" / "Listed N directories" / "Searched for N pattern"
    fold_patterns = [
        (r'^Read (\d+) files?\b', "Read"),
        (r'^Listed (\d+) director', "Glob"),
        (r'^Searched for (\d+) pattern', "Grep"),
        (r'^Wrote (\d+) lines? to (.+)', "Write"),
        (r'^Added (\d+) lines?', "Edit"),
        (r'^Removed (\d+) lines?', "Edit"),
    ]
    for pattern, tool_name in fold_patterns:
        m = re.match(pattern, s)
        if m:
            return {"tool": tool_name, "display": f"{tool_name}: {s[:60]}", "detail": s}

    # 格式 3: 混合折叠行 "Read 2 files, listed 2 directories (ctrl+o to expand)"
    if re.match(r'^(Read|Listed|Searched|Wrote|Added|Removed)\s+\d+\s+\w+.*,\s+(read|listed|searched|wrote|added|removed)', s, re.I):
        # 提取第一个动词作为主工具
        first_tool = re.match(r'^(\w+)', s).group(1)
        tool_map = {"Read": "Read", "Listed": "Glob", "Searched": "Grep",
                     "Wrote": "Write", "Added": "Edit", "Removed": "Edit"}
        tool_name = tool_map.get(first_tool, first_tool)
        return {"tool": tool_name, "display": f"{tool_name}: {s[:60]}", "detail": s}

    return None


def _detect_activity(screen: str) -> str | None:
    """从原始屏幕检测 Claude CLI 当前活动（动画行被 _is_noise 过滤，需直接检查屏幕）"""
    for line in screen.strip().split('\n')[-10:]:
        s = line.strip()
        # 检测 "● Exploring…" / "✻ Thinking…" / "◐ Writing…" 等动画行
        m = re.match(r'^[●✻⏺◐◑◒◓]\s*(\w+)…', s)
        if m:
            action = m.group(1)
            if action in ("Exploring", "Writing", "Reading", "Searching",
                          "Thinking", "Planning", "Running", "Waiting"):
                return action
        # 检测 "Exploring…" （无前缀）
        m = re.match(r'^([A-Z][a-z]+)…\s*$', s)
        if m and m.group(1) in ("Exploring", "Writing", "Reading", "Searching",
                                 "Thinking", "Planning"):
            return m.group(1)
    return None


def _clean_result(text: str) -> str:
    """清理 TUI 噪音，提取 Claude 实际回答"""
    if not text:
        return text
    lines = text.split('\n')
    cleaned = []
    in_traceback = False
    for line in lines:
        s = line.strip()
        if not s:
            if cleaned:
                cleaned.append('')
            continue
        if s.startswith('●'):
            # ● ToolName(...) 是工具调用，删除；● 其他文本是 Claude 的回复，保留
            if re.match(r'^●\s*\w+\(', s):
                in_traceback = False
                continue
            # 保留 ● 开头的文本回复，去掉 ● 前缀
            cleaned.append(s[1:].strip())
            continue
        if s.startswith('⎿'):
            continue
        if re.match(r'^(Bash|Read|Grep|Glob|Skill|Write|Edit)\(', s):
            continue
        if s.startswith('Traceback (most recent call last)'):
            in_traceback = True
            continue
        if in_traceback:
            if s.startswith(('File ', '^')) or line.startswith(('  ', '\t')):
                continue
            if re.match(r'^[A-Za-z_]+Error[:(]', s):
                continue
            if re.match(r'^[A-Za-z_]+Exception[:(]', s):
                continue
            in_traceback = False
        if re.match(r'^={5,}$', s):
            continue
        if re.match(r'^(Reading|Writing)\s+\d+\s+file', s):
            continue
        cleaned.append(s)
    return '\n'.join(cleaned).strip()


# ==================== PlannerSession ====================

class PlannerSession:
    """管理一个 tmux 中的 Claude CLI 交互会话"""

    def __init__(self, task_id: int, backend_config: dict, cwd: str = None,
                 model: str = None, resume_session_id: str = None,
                 system_prompt: str = None, env_unset: list[str] = None):
        self.task_id = task_id
        self.model = model or backend_config.get("model", "claude-sonnet-4-6")
        self.claude_session_id = resume_session_id or str(uuid.uuid4())
        self.session_name = f"{SESSION_PREFIX}{task_id}"
        self._known_lines: set[str] = set()
        self.ready = False

        # 清理同名旧 session
        subprocess.run(["tmux", "kill-session", "-t", self.session_name],
                       capture_output=True, timeout=5)

        # ── 构建环境变量：先 unset 所有继承变量，再 set 后端需要的 ──
        # 参照旧方案 accounts.json 的 env_unset + env_set 设计
        env_unset_list = env_unset or []
        env_set_dict = {k: v for k, v in backend_config.get("env", {}).items() if v}

        env_parts = []
        for var in env_unset_list:
            env_parts.append(f'-u {var}')
        for k, v in env_set_dict.items():
            env_parts.append(f'{k}="{v}"')
        env_str = " ".join(env_parts)

        # ── 构建 claude 命令 ──
        if resume_session_id:
            claude_cmd = f"claude --dangerously-skip-permissions --resume {resume_session_id}"
            self.claude_session_id = resume_session_id
        else:
            claude_cmd = f"claude --dangerously-skip-permissions --session-id {self.claude_session_id}"

        claude_cmd += f" --model {self.model}"

        # 所有后端都需要 --setting-sources 空值 防止加载 ~/.claude/settings.json
        # 因为 CC Switch 会把 GLM 路由配置写入 settings.json 的 env 块
        # Claude CLI 会读取并重新注入这些变量，覆盖我们的 env -u
        # 注意：不用单引号 '' 因为后面可能被 bash -c '...' 包裹导致引号冲突
        claude_cmd += ' --setting-sources ""'

        # 通过 --system-prompt 注入角色设定（Claude 不会在回复中回显 system prompt）
        sp_file_path = None
        if system_prompt:
            sp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            sp_file.write(system_prompt)
            sp_file.close()
            sp_file_path = sp_file.name

        if env_str:
            claude_cmd = f"env {env_str} {claude_cmd}"

        if cwd and Path(cwd).exists():
            claude_cmd = f"cd {cwd} && {claude_cmd}"

        # 用 bash -c 包裹以支持 $(cat file) 命令替换
        if sp_file_path:
            claude_cmd = f"bash -c '{claude_cmd} --system-prompt \"$(cat {sp_file_path})\"'"

        # 创建 tmux session
        _tmux("new-session", "-d", "-s", self.session_name,
              "-x", "80", "-y", "50", claude_cmd)
        _tmux("set-option", "-t", self.session_name, "history-limit", "10000")

        # 清理 system prompt 临时文件
        if sp_file_path:
            try:
                os.unlink(sp_file_path)
            except Exception:
                pass

        log.info(f"启动会话 {self.session_name} (model={self.model}, session={self.claude_session_id[:8]}...)")

        # 等待提示符
        if self._wait_for_prompt(timeout=30):
            screen = self.capture(-500)
            self._known_lines = set(_extract_content(screen))
            self.ready = True
            log.info(f"会话 {self.session_name} 就绪")
        else:
            log.warning(f"会话 {self.session_name} 等待提示符超时")

    def capture(self, start_line: int = 0) -> str:
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", self.session_name, "-p", "-S", str(start_line)],
                capture_output=True, text=True, timeout=10
            )
            return r.stdout
        except Exception:
            return ""

    def _wait_for_prompt(self, timeout: int = 30) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            screen = self.capture()
            if "trust this folder" in screen.lower() or "Enter to confirm" in screen:
                log.info("检测到信任确认对话框，自动确认")
                _tmux("send-keys", "-t", self.session_name, "Enter")
                time.sleep(2)
                continue
            if _is_at_prompt(screen):
                return True
            time.sleep(0.5)
        return False

    def mark(self):
        screen = self.capture(-500)
        self._known_lines = set(_extract_content(screen))

    def send(self, text: str):
        """发送文本到 Claude CLI"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(text)
            tmp_path = f.name
        try:
            _tmux("load-buffer", tmp_path)
            _tmux("paste-buffer", "-t", self.session_name)
            time.sleep(0.5)
            _tmux("send-keys", "-t", self.session_name, "Enter")
        finally:
            os.unlink(tmp_path)
        log.info(f"发送消息: {text[:80]}...")

    def send_ctrl_c(self):
        _tmux("send-keys", "-t", self.session_name, "C-c")

    def get_new_lines(self) -> list[str]:
        screen = self.capture(-500)
        current = _extract_content(screen)
        new = [l for l in current if l not in self._known_lines]
        self._known_lines.update(current)
        return new

    def is_at_prompt(self) -> bool:
        return _is_at_prompt(self.capture())

    def close(self):
        log.info(f"关闭会话 {self.session_name}")
        subprocess.run(["tmux", "kill-session", "-t", self.session_name],
                       capture_output=True, timeout=5)


# ==================== SessionManager ====================

class SessionManager:
    """管理所有 PlannerSession，支持并发控制和空闲清理"""

    MAX_CONCURRENT = 2

    def __init__(self):
        self._sessions: dict[int, PlannerSession] = {}
        self._last_active: dict[int, float] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT)

        # 检查 tmux
        try:
            subprocess.run(["tmux", "-V"], capture_output=True, timeout=5, check=True)
        except Exception:
            raise RuntimeError("tmux 未安装或不可用，请先安装: apt install tmux")

        # 启动清理线程
        t = threading.Thread(target=self._cleanup_loop, daemon=True)
        t.start()
        atexit.register(self._cleanup_all)

    def _cleanup_loop(self):
        while True:
            time.sleep(300)
            now = time.time()
            to_remove = []
            with self._lock:
                for tid, last in self._last_active.items():
                    if now - last > IDLE_TIMEOUT:
                        to_remove.append(tid)
            for tid in to_remove:
                with self._lock:
                    session = self._sessions.pop(tid, None)
                    self._last_active.pop(tid, None)
                if session:
                    log.info(f"清理空闲会话 task_id={tid}")
                    session.close()

    def _cleanup_all(self):
        with self._lock:
            for s in self._sessions.values():
                try:
                    s.close()
                except Exception:
                    pass
            self._sessions.clear()
            self._last_active.clear()

    def get_or_create(self, task_id: int, backend_config: dict,
                      cwd: str = None, model: str = None,
                      resume_session_id: str = None,
                      system_prompt: str = None,
                      env_unset: list[str] = None) -> PlannerSession:
        with self._lock:
            session = self._sessions.get(task_id)
            if session and session.ready:
                self._last_active[task_id] = time.time()
                return session
            # 创建新会话
            session = PlannerSession(
                task_id, backend_config, cwd=cwd, model=model,
                resume_session_id=resume_session_id,
                system_prompt=system_prompt,
                env_unset=env_unset,
            )
            self._sessions[task_id] = session
            self._last_active[task_id] = time.time()
            return session

    def get(self, task_id: int) -> PlannerSession | None:
        with self._lock:
            return self._sessions.get(task_id)

    def remove(self, task_id: int):
        with self._lock:
            session = self._sessions.pop(task_id, None)
            self._last_active.pop(task_id, None)
        if session:
            session.close()

    def cleanup_all(self):
        """关闭所有 tmux 会话"""
        with self._lock:
            for task_id, session in list(self._sessions.items()):
                try:
                    session.close()
                except Exception:
                    pass
            self._sessions.clear()
            self._last_active.clear()

    def acquire(self, timeout: float = 300) -> bool:
        return self._semaphore.acquire(timeout=timeout)

    def release(self):
        self._semaphore.release()


# ==================== 流式执行 ====================

def _estimate_progress(tool_calls: list[dict], accumulated: list[str],
                       elapsed: float, timeout: float) -> int:
    """基于阶段的进度估算

    策略：
    - 基础分 5%（已启动）
    - 每个工具调用步骤贡献 5-15%（Read/Grep 较轻 5%，Write 较重 15%，Explore/Agent 10%）
    - Write 工具出现意味着已进入输出阶段（至少 80%）
    - 时间衰减：随时间推移缓慢增长，避免长时间卡在同一进度
    - 上限 95%（100% 留给完成确认）
    """
    base = 5
    tool_score = 0
    has_write = False
    heavy_tools = {"Explore", "Agent", "Bash"}
    light_tools = {"Read", "Grep", "Glob", "LSP"}
    output_tools = {"Write", "Edit"}

    for tc in tool_calls:
        tool_name = tc.get("tool", "")
        if tool_name in output_tools:
            has_write = True
            tool_score += 15
        elif tool_name in heavy_tools:
            tool_score += 10
        elif tool_name in light_tools:
            tool_score += 5
        else:
            tool_score += 7

    # 如果已出现 Write/Edit，说明进入输出阶段
    if has_write:
        base = max(base, 80)

    # 时间衰减：每 10 秒 +2%，让进度不至于完全停滞
    time_bonus = int(elapsed / 10) * 2

    # 文本量补充：有大量输出时额外加分
    text_len = sum(len(l) for l in accumulated)
    text_bonus = min(10, int(text_len / 500))

    pct = base + tool_score + time_bonus + text_bonus
    return min(95, pct)


def run_streaming(
    session: PlannerSession,
    task_id: int,
    prompt: str,
    timeout: int = 600,
) -> dict:
    """发送 prompt 并流式监控输出，返回 {result, tool_calls, duration}

    注意：prompt 应由调用方组装好（含角色设定），本函数只负责发送和监控。
    """
    session.mark()
    # 把 prompt 行加入已知行（避免回显被当作输出）
    for line in prompt.split('\n'):
        s = line.strip()
        if s:
            session._known_lines.add(s)

    session.send(prompt)
    update_task(task_id, status="processing", progress=5, progress_msg="Claude CLI 执行中")
    log.info(f"[task={task_id}] 开始执行，timeout={timeout}s")

    accumulated: list[str] = []
    tool_calls: list[dict] = []
    printed: set[str] = set()
    start = time.time()
    idle_count = 0
    has_real_output = False
    last_content = ""
    last_db_write = 0.0
    last_log_time = 0.0
    last_pct = 5
    poll_count = 0

    while time.time() - start < timeout:
        time.sleep(1)
        poll_count += 1
        new_lines = session.get_new_lines()
        elapsed = time.time() - start

        if new_lines:
            idle_count = 0
            has_real_output = True
            new_count = 0
            for line in new_lines:
                if line in printed:
                    continue
                accumulated.append(line)
                printed.add(line)
                new_count += 1

            if new_count > 0:
                log.debug(f"[task={task_id}] +{new_count} 行新输出 (总计 {len(accumulated)} 行, {elapsed:.0f}s)")

            # 解析工具调用
            for line in new_lines:
                tool_info = _parse_tmux_tool_line(line)
                if tool_info:
                    tool_info["at"] = round(elapsed, 1)
                    tool_calls.append(tool_info)
                    update_task(task_id, progress_msg=tool_info["display"])
                    log.info(f"[task={task_id}] 工具调用: {tool_info['display']} ({elapsed:.0f}s)")

            # 估算进度
            pct = _estimate_progress(tool_calls, accumulated, elapsed, timeout)
            # 进度只能往前，不能倒退
            pct = max(pct, last_pct)
            if pct != last_pct:
                log.info(f"[task={task_id}] 进度: {last_pct}% → {pct}%")
                last_pct = pct
        else:
            idle_count += 1
            # idle 时也检查屏幕活动（动画行不经过 get_new_lines）
            if idle_count % 3 == 0:  # 每 3 秒检查一次
                activity = _detect_activity(session.capture())
                if activity:
                    activity_msg = f"{activity}..."
                    update_task(task_id, progress_msg=activity_msg)
                    if idle_count % 6 == 0:  # 每 6 秒日志一次
                        log.info(f"[task={task_id}] 活动检测: {activity_msg} ({elapsed:.0f}s)")

        # 定期写 DB（每 3 秒）
        now = time.time()
        if now - last_db_write >= 3 and (accumulated or tool_calls):
            update_task(task_id,
                        progress=last_pct,
                        partial_result="\n".join(accumulated[-50:]),  # 只保留最近 50 行避免 DB 膨胀
                        tool_calls=tool_calls)
            last_db_write = now

        # 定期日志（每 15 秒一条状态摘要）
        if now - last_log_time >= 15:
            log.info(f"[task={task_id}] 状态: {last_pct}% | 输出 {len(accumulated)} 行 | "
                     f"工具 {len(tool_calls)} 次 | idle={idle_count} | {elapsed:.0f}s/{timeout}s")
            last_log_time = now

        # 检测完成
        content = "\n".join(accumulated)
        if content == last_content:
            raw_screen = session.capture()
            if (has_real_output and idle_count >= 2
                    and session.is_at_prompt() and not _is_thinking(raw_screen)):
                log.info(f"[task={task_id}] 检测到完成: idle={idle_count}, at_prompt=True, {elapsed:.0f}s")
                break
        else:
            last_content = content

    duration = time.time() - start

    # 超时处理
    if time.time() - start >= timeout:
        log.warning(f"[task={task_id}] 超时 ({timeout}s), 共 {len(accumulated)} 行输出, {len(tool_calls)} 次工具调用")
        session.send_ctrl_c()
        return {"result": None, "tool_calls": tool_calls, "duration": duration, "timeout": True}

    # 提取最终结果
    raw_result = "\n".join(accumulated).strip()
    result = _clean_result(raw_result)

    log.info(f"[task={task_id}] 完成: {duration:.0f}s, 结果 {len(result)} 字符, {len(tool_calls)} 次工具调用")
    update_task(task_id, partial_result=None, tool_calls=tool_calls)

    return {"result": result, "tool_calls": tool_calls, "duration": duration, "timeout": False}


def send_followup(session: PlannerSession, task_id: int, message: str,
                  timeout: int = 600) -> dict:
    """向已有会话发送追问消息（不含 planner_prompt，会话已有上下文）"""
    return run_streaming(session, task_id, message, timeout=timeout)
