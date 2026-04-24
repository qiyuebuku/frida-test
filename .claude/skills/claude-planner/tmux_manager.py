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

from db import update_task, get_task

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


def _detect_rate_limit(screen: str) -> bool:
    """检测 Claude CLI 是否触发了 rate limit"""
    text = screen.lower()
    return ("hit your limit" in text
            or "rate-limit-options" in text
            or "resets " in text and "stop and wait" in text)


def _detect_interactive_dialog(screen: str) -> dict | None:
    """检测 Claude CLI 是否在等待用户交互（任何类型的对话框）

    返回: None 或 {
        "type": "confirmation" | "rate_limit" | "permission" | "selection" | "unknown",
        "title": str,         # 对话框标题/问题
        "options": list[str], # 可选项
        "hint": str,          # 操作提示（如 "Enter to confirm"）
        "raw": str,           # 原始屏幕文本（最后 15 行）
        "auto_action": str | None,  # 建议的自动操作: "enter" / "escape" / None（需客户端决策）
    }
    """
    lines = screen.strip().split('\n')
    last_lines = lines[-15:]
    last_text = '\n'.join(last_lines)

    # rate limit（特殊处理，建议 escape）
    if _detect_rate_limit(screen):
        return {
            "type": "rate_limit",
            "title": "Claude Pro 额度用完",
            "options": ["Stop and wait for limit to reset", "Upgrade your plan"],
            "hint": "rate limit reached",
            "raw": last_text,
            "auto_action": "escape",
        }

    # 检测通用交互标记
    has_enter_confirm = 'Enter to confirm' in last_text
    has_esc_cancel = 'Esc to cancel' in last_text
    has_tab_amend = 'Tab to amend' in last_text

    if not (has_enter_confirm or has_esc_cancel or has_tab_amend):
        return None

    # 提取选项列表（❯ 1. xxx / 2. xxx 格式）
    options = []
    for line in last_lines:
        s = line.strip()
        m = re.match(r'^[❯\s]*(\d+)\.\s+(.+)', s)
        if m:
            options.append(m.group(2).strip())

    # 提取标题（"Do you want to..." 或其他问题）
    title = ""
    for line in last_lines:
        s = line.strip()
        if s.startswith('Do you want to'):
            title = s
            break
        if s.endswith('?') and len(s) > 10:
            title = s
            break
        if 'trust this' in s.lower() or 'This command requires' in s:
            title = s
            break

    # 分类
    dialog_type = "unknown"
    auto_action = None

    if 'Do you want to create' in last_text or 'Do you want to overwrite' in last_text:
        dialog_type = "confirmation"
        auto_action = "enter"  # 文件创建/覆盖 → 自动确认
    elif 'trust this folder' in last_text.lower():
        dialog_type = "confirmation"
        auto_action = "enter"  # 信任文件夹 → 自动确认
    elif 'This command requires approval' in last_text:
        dialog_type = "permission"
        auto_action = "enter"  # 权限确认 → 自动确认（已有 --dangerously-skip-permissions）
    elif 'Do you want to proceed' in last_text:
        dialog_type = "confirmation"
        auto_action = "enter"
    elif options:
        dialog_type = "selection"
        auto_action = None  # 有选项的对话框需要客户端决策

    hint_parts = []
    if has_enter_confirm:
        hint_parts.append("Enter to confirm")
    if has_esc_cancel:
        hint_parts.append("Esc to cancel")
    if has_tab_amend:
        hint_parts.append("Tab to amend")

    return {
        "type": dialog_type,
        "title": title or "(未识别的对话框)",
        "options": options,
        "hint": " · ".join(hint_parts),
        "raw": last_text,
        "auto_action": auto_action,
    }


def _is_at_prompt(screen: str) -> bool:
    """检测 Claude CLI 是否真正回到空闲 prompt。

    注意：
    - Claude Code TUI 底部永远有一个 ❯ 输入框，'esc to interrupt' 说明正在执行
    - rate limit 选择界面也有 ❯，但后面跟的是 '1.' 选项，不算 prompt
    - 'Enter to confirm' 说明在等用户选择（如 rate limit / trust folder），不算 prompt
    """
    last_lines = screen.strip().split('\n')[-8:]
    last_text = '\n'.join(last_lines)

    # 正在执行中
    if 'esc to interrupt' in last_text or 'esc to cancel' in last_text:
        return False

    # 等待用户选择（rate limit / trust folder 等）
    if 'Enter to confirm' in last_text:
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
                 system_prompt: str = None, env_unset: list[str] = None,
                 model_map: dict = None):
        self.task_id = task_id
        self.model = model or backend_config.get("model", "sonnet")
        self.claude_session_id = resume_session_id or str(uuid.uuid4())
        self.session_name = f"{SESSION_PREFIX}{task_id}"
        self._known_lines: set[str] = set()
        self.ready = False

        # 清理同名旧 session
        subprocess.run(["tmux", "kill-session", "-t", self.session_name],
                       capture_output=True, timeout=5)

        # ── 构建环境变量：先 unset 所有继承变量，再 set 模型映射 + 后端变量 ──
        env_unset_list = env_unset or []
        env_set_dict = {k: v for k, v in backend_config.get("env", {}).items() if v}

        # 注入模型映射（如 ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-7）
        # 先 unset 清除父进程的映射，然后用 model_map 重新设置
        model_map = model_map or {}
        for k, v in model_map.items():
            if v:
                env_set_dict[k] = v

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
              "-x", "220", "-y", "50", claude_cmd)
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
                      env_unset: list[str] = None,
                      model_map: dict = None) -> PlannerSession:
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
                model_map=model_map,
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

    # 等待粘贴回显渲染完毕，然后 mark 屏幕状态
    # 这样回显的换行/折行版本也被标记为"已知"，不会被误认为输出
    time.sleep(2)
    screen = session.capture(-500)
    for line in _extract_content(screen):
        session._known_lines.add(line)

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

        # 检测交互对话框（每 2 秒 idle 检查一次）
        if idle_count >= 2 and idle_count % 2 == 0:
            raw_screen = session.capture()
            dialog = _detect_interactive_dialog(raw_screen)
            if dialog:
                log.info(f"[task={task_id}] 检测到对话框: type={dialog['type']} "
                         f"title={dialog['title'][:50]} auto={dialog['auto_action']} ({elapsed:.0f}s)")

                if dialog["type"] == "rate_limit":
                    # rate limit 直接终止
                    log.warning(f"[task={task_id}] rate limit! 终止任务")
                    session.send_ctrl_c()
                    duration = time.time() - start
                    raw_result = "\n".join(accumulated).strip()
                    result = _clean_result(raw_result)
                    usage = parse_session_usage(session.claude_session_id)
                    return {"result": result if result else None,
                            "tool_calls": tool_calls, "duration": duration,
                            "timeout": False, "rate_limited": True, "usage": usage}

                if dialog["auto_action"]:
                    # 可自动处理的对话框
                    key = {"enter": "Enter", "escape": "Escape"}.get(dialog["auto_action"], "Enter")
                    log.info(f"[task={task_id}] 自动响应: {key}")
                    _tmux("send-keys", "-t", session.session_name, key)
                    idle_count = 0
                    continue

                # 需要客户端决策的对话框 → 写入 DB，等待客户端响应
                log.info(f"[task={task_id}] 等待客户端决策: {dialog['title'][:60]}")
                update_task(task_id, progress_msg=f"⏸️ 等待确认: {dialog['title'][:40]}",
                            pending_dialog=dialog)

                # 等待客户端通过 /tasks/{id}/respond 发送响应（最多等 300 秒）
                wait_start = time.time()
                while time.time() - wait_start < 300:
                    time.sleep(2)
                    task = get_task(task_id)
                    pd = task.get("pending_dialog")
                    if not pd or (isinstance(pd, dict) and pd.get("resolved")):
                        log.info(f"[task={task_id}] 客户端已响应对话框")
                        idle_count = 0
                        break
                else:
                    # 超时未响应，默认 Enter
                    log.warning(f"[task={task_id}] 等待客户端响应超时(300s)，默认 Enter")
                    _tmux("send-keys", "-t", session.session_name, "Enter")
                    update_task(task_id, pending_dialog=None)
                    idle_count = 0
                continue

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
        raw_result = "\n".join(accumulated).strip()
        result = _clean_result(raw_result)
        usage = parse_session_usage(session.claude_session_id)
        return {"result": result if result else None,
                "tool_calls": tool_calls, "duration": duration,
                "timeout": True, "usage": usage}

    # 提取最终结果
    raw_result = "\n".join(accumulated).strip()
    result = _clean_result(raw_result)

    # 从 JSONL 会话文件解析 token 用量
    usage = parse_session_usage(session.claude_session_id)

    log.info(f"[task={task_id}] 完成: {duration:.0f}s, 结果 {len(result)} 字符, "
             f"{len(tool_calls)} 次工具调用, "
             f"tokens: in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')} "
             f"cost=${usage.get('total_cost_usd', '?')}")
    update_task(task_id, partial_result=None, tool_calls=tool_calls)

    return {"result": result, "tool_calls": tool_calls, "duration": duration,
            "timeout": False, "usage": usage}


def _parse_session_result(session_id: str) -> tuple[str, list[dict]]:
    """从 JSONL 会话文件中读取完整的 assistant 输出和工具调用。

    返回 (text_result, tool_calls)。
    比 tmux capture-pane 抓屏可靠得多——TUI 渲染会破坏长文本结构，
    JSONL 文件则保留了完整的原始内容。
    """
    f = _find_session_file(session_id)
    if not f:
        return "", []

    texts: list[str] = []
    tools: list[dict] = []

    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    text = block.get("text", "").strip()
                    if text:
                        texts.append(text)
                elif bt == "tool_use":
                    tools.append({
                        "tool": block.get("name", ""),
                        "display": f"{block.get('name', '')}: {json.dumps(block.get('input', {}), ensure_ascii=False)[:80]}",
                        "input": block.get("input", {}),
                    })
    except Exception as e:
        log.warning(f"[session_result] 解析失败: {e}")

    return "\n\n".join(texts), tools


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


def parse_session_usage(session_id: str) -> dict:
    """从 Claude CLI 会话 JSONL 文件解析 token 用量

    返回: {
        "input_tokens": int,
        "output_tokens": int,
        "cache_creation_tokens": int,
        "cache_read_tokens": int,
        "total_cost_usd": float (估算),
        "model": str,
        "turns": int (assistant 响应次数),
    }
    """
    f = _find_session_file(session_id)
    if not f:
        log.debug(f"未找到会话文件 session_id={session_id[:8]}...")
        return {}

    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    model = ""
    turns = 0

    try:
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message", {})
            usage = msg.get("usage", {})
            if not usage:
                continue

            turns += 1
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            total_cache_create += usage.get("cache_creation_input_tokens", 0)
            total_cache_read += usage.get("cache_read_input_tokens", 0)
            if msg.get("model"):
                model = msg["model"]
    except Exception as e:
        log.warning(f"解析会话文件失败: {e}")
        return {}

    # 估算费用（基于 Anthropic 官方定价，单位 USD per million tokens）
    # Sonnet: input $3, output $15; Opus: input $15, output $75
    # Cache: create 同 input 价格的 25%，read 同 input 价格的 10%
    if "opus" in model:
        input_rate, output_rate = 15.0, 75.0
    else:
        input_rate, output_rate = 3.0, 15.0
    cache_create_rate = input_rate * 0.25
    cache_read_rate = input_rate * 0.10

    cost = (
        total_input * input_rate / 1_000_000
        + total_output * output_rate / 1_000_000
        + total_cache_create * cache_create_rate / 1_000_000
        + total_cache_read * cache_read_rate / 1_000_000
    )

    result = {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_creation_tokens": total_cache_create,
        "cache_read_tokens": total_cache_read,
        "total_cost_usd": round(cost, 4),
        "model": model,
        "turns": turns,
    }
    log.info(f"Token 用量: input={total_input} output={total_output} "
             f"cache_create={total_cache_create} cache_read={total_cache_read} "
             f"cost=${cost:.4f} model={model} turns={turns}")
    return result


def send_followup(session: PlannerSession, task_id: int, message: str,
                  timeout: int = 600) -> dict:
    """向已有会话发送追问消息（不含 planner_prompt，会话已有上下文）"""
    return run_streaming(session, task_id, message, timeout=timeout)


def run_chat(session: PlannerSession, prompt: str, timeout: int = 180) -> dict:
    """一次性 chat 执行（不写 DB，不跟踪进度）。

    完成检测仍用 tmux 屏幕状态判断（idle + at_prompt），
    但最终结果从 JSONL 会话文件读取——避免 TUI 渲染破坏长文本。
    """
    session.send(prompt)
    log.info(f"[chat] 开始执行, session={session.session_name}, timeout={timeout}s")

    start = time.time()
    idle_count = 0

    while time.time() - start < timeout:
        time.sleep(1)
        raw_screen = session.capture()

        # 对话框处理
        dialog = _detect_interactive_dialog(raw_screen)
        if dialog:
            if dialog["type"] == "rate_limit":
                session.send_ctrl_c()
                usage = parse_session_usage(session.claude_session_id)
                return {"result": None, "tool_calls": [], "duration": time.time() - start,
                        "timeout": False, "rate_limited": True, "usage": usage}
            if dialog["auto_action"]:
                key = {"enter": "Enter", "escape": "Escape"}.get(dialog["auto_action"], "Enter")
                _tmux("send-keys", "-t", session.session_name, key)
                idle_count = 0
                continue
            session.send_ctrl_c()
            raise RuntimeError(f"[chat] 未预期的交互对话框: {dialog['title'][:80]}")

        # 完成检测：at_prompt + 非 thinking
        if _is_at_prompt(raw_screen) and not _is_thinking(raw_screen):
            idle_count += 1
            if idle_count >= 3:
                break
        else:
            idle_count = 0

    duration = time.time() - start

    if time.time() - start >= timeout:
        session.send_ctrl_c()
        usage = parse_session_usage(session.claude_session_id)
        return {"result": None, "tool_calls": [], "duration": duration,
                "timeout": True, "usage": usage}

    # 从 JSONL 文件读取完整结果（比 tmux 抓屏可靠）
    result, tool_calls = _parse_session_result(session.claude_session_id)
    usage = parse_session_usage(session.claude_session_id)

    log.info(f"[chat] 完成: {duration:.0f}s, 结果 {len(result)} 字符, "
             f"{len(tool_calls)} 次工具调用, "
             f"tokens: in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}")
    return {"result": result or None, "tool_calls": tool_calls, "duration": duration,
            "timeout": False, "rate_limited": False, "usage": usage}


def run_pipe(prompt: str, backend_config: dict, model: str = None,
             system_prompt: str = None, cwd: str = None,
             env_unset: list[str] = None, model_map: dict = None,
             timeout: int = 180) -> dict:
    """一次性 LLM 调用，使用 `claude -p` 子进程。

    不创建 tmux 会话，不需要 SessionManager semaphore。
    `claude -p --output-format json` 直接返回结构化 JSON（含 usage）。
    """
    model = model or backend_config.get("model", "sonnet")

    # 写 prompt 到临时文件（避免 shell 转义问题）
    prompt_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    prompt_file.write(prompt)
    prompt_file.close()

    # 写 system_prompt 到临时文件
    sp_file = None
    if system_prompt:
        sp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        sp_file.write(system_prompt)
        sp_file.close()

    try:
        # 构建环境变量
        env = os.environ.copy()
        for var in (env_unset or []):
            env.pop(var, None)
        for k, v in backend_config.get("env", {}).items():
            if v:
                env[k] = v
        for k, v in (model_map or {}).items():
            if v:
                env[k] = v

        # 构建命令
        cmd = ["claude", "-p", f"$(cat {prompt_file.name})",
               "--model", model, "--output-format", "json"]
        if sp_file:
            cmd += ["--system-prompt", f"$(cat {sp_file.name})"]

        # 用 bash -c 展开 $(cat ...)
        bash_cmd = " ".join(cmd)
        log.info(f"[pipe] 启动: model={model}, timeout={timeout}s")

        start = time.time()
        r = subprocess.run(
            ["bash", "-c", bash_cmd],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd, env=env,
        )
        duration = time.time() - start

        # 解析 JSON 输出
        stdout = r.stdout.strip()
        if not stdout:
            log.warning(f"[pipe] 无输出, stderr={r.stderr[:200]}")
            return {"result": None, "tool_calls": [], "duration": duration,
                    "timeout": False, "rate_limited": False, "usage": {}}

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # 非 JSON 输出，直接作为文本返回
            log.info(f"[pipe] 非 JSON 输出 ({len(stdout)} chars), {duration:.0f}s")
            return {"result": stdout, "tool_calls": [], "duration": duration,
                    "timeout": False, "rate_limited": False, "usage": {}}

        # claude -p --output-format json 的输出结构
        result_text = data.get("result", "")
        is_error = data.get("is_error", False)
        session_id = data.get("session_id", "")
        cost_usd = data.get("total_cost_usd", data.get("cost_usd", 0))

        # 提取 usage
        raw_usage = data.get("usage", {})
        usage = {
            "input_tokens": raw_usage.get("input_tokens", 0),
            "output_tokens": raw_usage.get("output_tokens", 0),
            "cache_creation_tokens": raw_usage.get("cache_creation_input_tokens", 0),
            "cache_read_tokens": raw_usage.get("cache_read_input_tokens", 0),
            "total_cost_usd": cost_usd,
            "model": model,
            "turns": data.get("num_turns", 1),
        }

        # rate limit 检测
        if "hit your limit" in result_text.lower() or "rate" in result_text.lower() and "limit" in result_text.lower():
            log.warning(f"[pipe] rate limited, {duration:.0f}s")
            return {"result": None, "tool_calls": [], "duration": duration,
                    "timeout": False, "rate_limited": True, "usage": usage}

        if is_error:
            log.warning(f"[pipe] 错误: {result_text[:200]}")
            return {"result": None, "tool_calls": [], "duration": duration,
                    "timeout": False, "rate_limited": False, "usage": usage}

        log.info(f"[pipe] 完成: {duration:.0f}s, 结果 {len(result_text)} 字符, "
                 f"tokens: in={usage['input_tokens']} out={usage['output_tokens']}, "
                 f"cost=${cost_usd:.4f}")

        return {
            "result": result_text or None,
            "tool_calls": [],
            "duration": duration,
            "timeout": False,
            "rate_limited": False,
            "usage": usage,
            "session_uuid": session_id,
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        log.warning(f"[pipe] 超时 ({timeout}s)")
        return {"result": None, "tool_calls": [], "duration": duration,
                "timeout": True, "rate_limited": False, "usage": {}}
    finally:
        os.unlink(prompt_file.name)
        if sp_file:
            os.unlink(sp_file.name)
