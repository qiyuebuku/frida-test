"""任务领域常量和类型定义"""

# 轻量任务类型（走同步 SSE，不经过 TaskExecutor）
SYNC_TASK_TYPES = {"ocr", "table", "search"}

# tmux 会话名前缀
SESSION_PREFIX = "sa_claude_"

# Claude CLI 输入提示符
PROMPT_CHAR = "❯"

# 会话清理超时（秒）
SESSION_IDLE_TIMEOUT = 1800  # 30 分钟
