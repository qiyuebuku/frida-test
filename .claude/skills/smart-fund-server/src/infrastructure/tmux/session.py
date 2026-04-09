"""tmux 会话管理 - Claude CLI 交互的底层基础设施

提供 tmux 命令执行、屏幕输出解析、噪音过滤等基础能力，
以及 ClaudeTmuxSession 类管理单个 Claude CLI 交互会话。
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from src.domain.task.models import SESSION_PREFIX, PROMPT_CHAR

logger = logging.getLogger(__name__)


# ==================== tmux 辅助函数 ====================

def _tmux(*args) -> str:
    """执行 tmux 命令，返回 stdout"""
    try:
        result = subprocess.run(
            ["tmux"] + list(args),
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    except Exception:
        return ""


def _is_noise(line: str) -> bool:
    """判断是否为 Claude CLI TUI 噪音行

    过滤规则：
    1. TUI 边框装饰（╭╰╮╯ 等，但保留 │ 因为 Markdown 表格也用）
    2. thinking / spinner 指示器（随机动词…）
    3. 工具调用行（● ToolName(...)）和输出前缀（⎿）
    4. 折叠提示（… +N lines (ctrl+o to expand)）
    5. 状态栏、启动信息、控制提示
    6. 完成标记（✻ Baked for ...）
    """
    stripped = line.strip()
    if not stripped:
        return True
    if stripped in (PROMPT_CHAR, '>', '↑', '?', '? for shortcuts'):
        return True
    # TUI 边框（保留 │ 用于 Markdown 表格）
    if any(c in stripped for c in '╭╰╮╯▐▛▜▘▝'):
        return True
    if stripped.startswith('─' * 5):
        return True
    # thinking / spinner 指示器: "前缀 + 大写动词…"
    if re.match(r'^.\s+[A-Z][a-z]+…', stripped):
        return True
    if re.match(r'^[A-Z][a-z]+…(\s|$)', stripped):
        return True
    # 完成标记: "✻ Baked for 1m 55s"
    if re.match(r'^.\s+(Baked|Cooked|Done) for ', stripped):
        return True
    # 注意：● 前缀行（工具调用、Claude 思考状态）和 ⎿ 前缀行（工具输出）不在此过滤
    # 因为 _parse_tmux_tool_line 需要看到 ● ToolName(...) 才能创建 tool_call 步骤
    # ⎿ 行包含工具执行结果，需要关联到最近的 tool_call
    # 这些行最终由 _clean_result 后处理清理
    # 折叠提示: "… +N lines (ctrl+o to expand)"
    if re.match(r'^…\s*\+\d+\s+lines?\s*\(ctrl\+o', stripped):
        return True
    # prompt 回显: "❯ 执行命令: ..."
    if stripped.startswith(PROMPT_CHAR):
        return True
    # 状态栏 / 启动信息 / TUI 提示
    noise_patterns = [
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
    return any(p in stripped for p in noise_patterns)


def _extract_content(screen: str) -> list[str]:
    """从 tmux 屏幕输出中提取有意义的文本行"""
    lines = []
    for line in screen.split('\n'):
        stripped = line.strip()
        if not _is_noise(stripped) and stripped:
            lines.append(stripped)
    return lines


def _is_thinking(screen: str) -> bool:
    """检查 Claude 是否正在 thinking/执行工具（用于判断循环是否可以结束）"""
    lines = screen.strip().split('\n')
    for line in lines[-10:]:
        stripped = line.strip()
        if '(ctrl+o' in stripped:
            continue
        if stripped.startswith('●') or stripped.startswith('⎿'):
            if 'Running…' in stripped or 'Waiting…' in stripped:
                return True
            continue
        # thinking spinner: "前缀 + 大写动词…" 如 "✻ Synthesizing… (34s"
        if re.search(r'[A-Z][a-z]+…', stripped):
            return True
    return False


def _is_at_prompt(screen: str) -> bool:
    """检查 Claude CLI 是否显示输入提示符 ❯

    只要底部5行有 ❯（排除选择菜单行）就认为可以发送消息。
    即使 Claude 在 thinking，CLI 也接受输入并排队处理。
    """
    lines = screen.strip().split('\n')

    for line in lines[-5:]:
        stripped = line.strip()
        if PROMPT_CHAR in stripped:
            after = stripped.split(PROMPT_CHAR, 1)[1].strip()
            # 排除对话框选择行：❯ 后跟 "数字." 格式
            if re.match(r'^\d+\.', after):
                continue
            return True

    return False


# ==================== tmux 会话管理 ====================

class ClaudeTmuxSession:
    """管理单个 tmux Claude CLI 交互会话"""

    def __init__(self, task_id: int, cwd: str = None, model: str = None,
                 session_id: str = None, resume_session_id: str = None):
        self.task_id = task_id
        self.model = model
        # 生成或使用已有的 Claude session ID（用于 --resume 恢复）
        import uuid
        self.claude_session_id = session_id or str(uuid.uuid4())
        # session name 包含 model 信息，避免不同 model 冲突
        suffix = f"_{model}" if model else ""
        self.session_name = f"{SESSION_PREFIX}{task_id}{suffix}"
        self._known_lines: set[str] = set()
        self.ready = False
        self.has_pending_input = False  # 是否有刚转发的用户消息待处理

        # 清理同名旧 session
        subprocess.run(
            ["tmux", "kill-session", "-t", self.session_name],
            capture_output=True, timeout=5
        )

        # 构建启动命令（跳过权限确认，自动化场景无需交互审批）
        if resume_session_id:
            # 恢复模式：使用 --resume 恢复之前的会话
            claude_cmd = f"claude --dangerously-skip-permissions --resume {resume_session_id}"
            self.claude_session_id = resume_session_id
            logger.info(f"[tmux] 恢复会话 session_id={resume_session_id}")
        else:
            claude_cmd = f"claude --dangerously-skip-permissions --session-id {self.claude_session_id}"
        if model:
            claude_cmd += f" --model {model}"
        if cwd and Path(cwd).exists():
            claude_cmd = f"cd {cwd} && {claude_cmd}"

        # 创建 tmux session，启动 claude 交互模式
        _tmux("new-session", "-d", "-s", self.session_name,
              "-x", "80", "-y", "50", claude_cmd)
        _tmux("set-option", "-t", self.session_name, "history-limit", "10000")

        logger.info(f"[tmux] 启动会话 {self.session_name} (model={model or 'default'}, claude_session={self.claude_session_id[:8]}...)")

        # 等待 Claude CLI 初始化（提示符出现）
        if self._wait_for_prompt(timeout=30):
            screen = self.capture(-500)
            self._known_lines = set(_extract_content(screen))
            self.ready = True
            logger.info(f"[tmux] 会话 {self.session_name} 就绪")
        else:
            logger.warning(f"[tmux] 会话 {self.session_name} 等待提示符超时")

    def capture(self, start_line: int = 0) -> str:
        """捕获 tmux 窗格的纯文本内容"""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", self.session_name,
                 "-p", "-S", str(start_line)],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout
        except Exception:
            return ""

    def _wait_for_prompt(self, timeout: int = 30) -> bool:
        """等待提示符出现，自动处理信任确认等阻塞对话框"""
        start = time.time()
        while time.time() - start < timeout:
            screen = self.capture()
            # 检测信任确认对话框（首次访问工作目录时弹出）
            if "trust this folder" in screen.lower() or "Enter to confirm" in screen:
                logger.info(f"[tmux] 检测到信任确认对话框，自动确认")
                _tmux("send-keys", "-t", self.session_name, "Enter")
                time.sleep(2)
                continue
            if _is_at_prompt(screen):
                return True
            time.sleep(0.5)
        return False

    def mark(self):
        """记录当前屏幕状态（后续 get_new_lines 基于此做 diff）"""
        screen = self.capture(-500)
        self._known_lines = set(_extract_content(screen))

    def send(self, text: str, is_followup: bool = False):
        """发送文本到 Claude（自动按 Enter）

        使用 tmux load-buffer + paste-buffer 粘贴文本（避免 send-keys 丢失长文本），
        然后延迟发送 Enter 确保文本已完整输入。
        """
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(text)
            tmp_path = f.name
        try:
            _tmux("load-buffer", tmp_path)
            _tmux("paste-buffer", "-t", self.session_name)
            time.sleep(0.5)  # 等待粘贴完成
            _tmux("send-keys", "-t", self.session_name, "Enter")
        finally:
            os.unlink(tmp_path)
        if is_followup:
            self.has_pending_input = True

    def send_ctrl_c(self):
        """发送 Ctrl+C 中断"""
        _tmux("send-keys", "-t", self.session_name, "C-c")

    def get_new_lines(self) -> list[str]:
        """获取自上次 mark/check 以来的新内容行"""
        screen = self.capture(-500)
        current = _extract_content(screen)
        new = [l for l in current if l not in self._known_lines]
        self._known_lines.update(current)
        return new

    def is_at_prompt(self) -> bool:
        """Claude 是否在等待输入"""
        screen = self.capture()
        return _is_at_prompt(screen)

    def close(self):
        """关闭 tmux 会话"""
        logger.info(f"[tmux] 关闭会话 {self.session_name}")
        subprocess.run(
            ["tmux", "kill-session", "-t", self.session_name],
            capture_output=True, timeout=5
        )
