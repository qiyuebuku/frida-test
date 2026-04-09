# Step 3：task_executor 拆分

**目标**：将 600+ 行的 `task_executor.py` 拆分为三个职责清晰的模块。
**性质**：逻辑重构 — 拆文件、分离职责。
**风险**：中 — 需要仔细处理状态管理和 tmux 交互。
**前置**：Step 2 完成（代码已在 `src/` 下）
**参考**：[系统架构设计 § 7. task_executor 拆分方案](../系统架构设计.md)

---

## 拆分目标

| 当前 | 拆分后 | 层 | 职责 |
|------|--------|---|------|
| `task_executor.py` 全部 | `src/infrastructure/tmux/session.py` | 基础设施 | tmux 会话操作 |
| | `src/domain/task/models.py` | 领域 | 常量 + 任务模型 |
| | `src/application/orchestrators/task_orchestrator.py` | 应用 | 生命周期管理 |

---

## 1. 提取 tmux 适配器

从 task_executor 中提取所有 subprocess 调用 tmux 的代码：

```python
# src/infrastructure/tmux/session.py
class TmuxSession:
    """tmux 会话 — 纯技术操作，不含业务逻辑"""

    def __init__(self, name: str):
        self.name = name

    def create(self, working_dir: str, command: str):
        """创建 tmux session 并执行初始命令"""

    def send_keys(self, text: str):
        """向 session 发送按键"""

    def capture_output(self, max_lines: int = 500) -> str:
        """捕获当前 pane 输出"""

    def kill(self):
        """销毁 session"""

    def is_alive(self) -> bool:
        """检查 session 是否存在"""
```

**提取哪些代码**：搜索 task_executor.py 中所有 `subprocess.run(["tmux"` 相关的函数/方法。

---

## 2. 提取任务模型

```python
# src/domain/task/models.py

# 轻量任务类型（走同步 SSE，不经过 TaskOrchestrator）
SYNC_TASK_TYPES = {"ocr", "table", "search"}

# tmux 会话配置
SESSION_PREFIX = "sa_claude_"
PROMPT_CHAR = "❯"
SESSION_IDLE_TIMEOUT = 1800  # 30 分钟
```

---

## 3. 重构 orchestrator

剩余的生命周期管理代码重构为 `TaskOrchestrator`：

```python
# src/application/orchestrators/task_orchestrator.py
from src.infrastructure.tmux.session import TmuxSession
from src.domain.task.models import SESSION_PREFIX, SESSION_IDLE_TIMEOUT

class TaskOrchestrator:
    """任务生命周期管理（有状态，伴随进程）"""

    def __init__(self):
        self._active_tasks: dict = {}        # task_id → TmuxSession
        self._session_models: dict = {}      # session_name → model

    async def execute(self, task_id: str, prompt: str, model: str):
        """创建 tmux 会话，启动 Claude CLI 执行任务"""

    async def poll(self, task_id: str) -> dict:
        """轮询任务输出"""

    async def follow_up(self, task_id: str, message: str):
        """向运行中的任务追问"""

    async def cancel(self, task_id: str):
        """取消任务，销毁 tmux 会话"""

    def cleanup_idle_sessions(self):
        """清理超时空闲会话"""
```

---

## 4. 删除旧文件 + 验证

```bash
rm src/application/orchestrators/task_executor.py

# 所有引用改为：
# from src.application.orchestrators.task_orchestrator import TaskOrchestrator
# from src.domain.task.models import SYNC_TASK_TYPES

# 验证
python main.py
# 测试：创建任务 → 执行 → 轮询 → 追问 → 完成，全流程

git add -A && git commit -m "refactor: 拆分 task_executor 为 orchestrator + tmux + models"
```
