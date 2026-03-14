# 08 - Skill 架构集成方案（修正版）

> 修正 `07-skill-based-refactoring.md` 的架构问题：
> 1. 服务端代码错误地放在了 `screenshot-assistant/server/`，实际唯一服务端是 `smart-fund-server`
> 2. 引入了不必要的 `pipeline` executor — 固定工作流已有 Python 代码，不需要 Skill 参与决策
> 3. 两个入口（skill API + task_type 硬编码路由）导致维护分裂 — 需要统一入口 + handler 注册机制

---

## 一、核心设计：统一入口 + 两条执行路径

### 调度逻辑

```
POST /api/skills/{name}/run
    │
    ▼
查 SkillRegistry → 拿到 command 定义
    │
    ▼
创建 sa_tasks（skill_name + command_id）
    │
    ▼
TaskExecutor._run()
    │
    ├─ command.executor == "claude"?
    │     │
    │     YES → 通用 claude 执行（构建 prompt + _run_claude_streaming(cwd=skill.path)）
    │           无需注册 handler，无需写代码
    │           新增 Skill 命令 = 只改 SKILL.md
    │
    └─ NO → 查 handler 注册表
              │
              ├─ 找到 → 执行注册的 Python handler
              │
              └─ 找不到 → 报错（明确告知未注册）
```

### 两条路径的边界

| | Handler（注册制） | Claude（自动） |
|---|------------------|---------------|
| **SKILL.md** | 无 `executor` 字段 | `executor: claude` |
| **新增方式** | 写 handler + @register + SKILL.md | 只改 SKILL.md |
| **执行方式** | Python 函数，确定性 | `claude -p` 在 skill 目录 |
| **适用场景** | 固定工作流（OCR/回复/持仓分析） | 需要 Claude 编排的复杂任务 |
| **未注册** | **直接报错** | 不需要注册 |

### 为什么不兜底？

未注册 handler 的非 claude 命令应该直接报错，而不是默默走 claude：
- 明确告知开发者"你忘注册 handler 了"
- 避免不可预期的行为
- 如果真的想走 claude，在 SKILL.md 里标 `executor: claude` 即可

---

## 二、Handler 注册机制

### 2.1 注册表

```python
# services/handlers/__init__.py

_registry: dict[tuple[str, str], Callable] = {}


def register(skill_name: str, command_id: str):
    """注册 handler，支持一个函数注册多个命令"""
    def decorator(fn):
        _registry[(skill_name, command_id)] = fn
        return fn
    return decorator


def get_handler(skill_name: str, command_id: str):
    """精确匹配，找不到返回 None"""
    return _registry.get((skill_name, command_id))
```

### 2.2 Handler 文件组织

```
services/handlers/
├── __init__.py            # 注册表
├── ocr.py                 # OCR 类（5 个命令复用同一个函数）
├── chat_reply.py          # 智能回复
└── fund_holdings.py       # 持仓分析
```

没有 `claude_skill.py` — claude 执行路径内置在 `TaskExecutor._run()` 中，不经过 handler 注册表。

### 2.3 Handler 示例

```python
# services/handlers/ocr.py
from services.handlers import register

@register("screenshot", "ocr")
@register("screenshot", "table")
@register("screenshot", "search")
@register("screenshot", "full_page")
@register("screenshot", "manual_scroll")
def handle_ocr(executor, task: dict):
    """纯 OCR：识别 → 返回文本"""
    task_id = task["id"]
    raw, md, ocr_id = executor._do_ocr(task_id, task["image_path"], ...)
    result = md or raw
    task_db.update_task(task_id, status="completed", result=result, ...)
```

```python
# services/handlers/chat_reply.py
from services.handlers import register

@register("screenshot", "chat_reply")
def handle_chat_reply(executor, task: dict):
    """OCR → LLM 回复建议"""
    task_id = task["id"]
    raw, md, ocr_id = executor._do_ocr(...)
    prompt = f"分析聊天内容，给出回复建议...\n{md or raw}"
    report = executor._run_claude_streaming(task_id, prompt, ...)
    task_db.update_task(task_id, status="completed", result=report, ...)
```

### 2.4 TaskExecutor._run() 调度

```python
def _run(self, task_id: int):
    task = task_db.get_task(task_id)
    task_db.update_task(task_id, status="processing", started_at=datetime.now())
    start_time = time.time()

    try:
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

        # 完成（handler 内部未标记完成的情况）
        ...
    except Exception as e:
        task_db.update_task(task_id, status="failed", error_msg=str(e)[:500])
        ...


def _is_claude_command(self, skill_name: str, command_id: str) -> bool:
    """检查 SKILL.md 中该命令是否标记为 executor: claude"""
    import services.skill_registry as sr
    if not sr.skill_registry or not skill_name:
        return False
    skill = sr.skill_registry.get_skill(skill_name)
    if not skill:
        return False
    command = skill.get_command(command_id)
    return command is not None and command.executor == "claude"


def _execute_claude(self, task: dict, skill_name: str, command_id: str):
    """通用 claude 执行：在 skill 目录下 claude -p"""
    import services.skill_registry as sr
    skill = sr.skill_registry.get_skill(skill_name)
    command = skill.get_command(command_id)
    task_id = task["id"]
    config = task.get("config") or {}

    prompt = f"执行命令: {command.name}\n{command.description}"
    if config.get("args"):
        prompt += f"\n参数: {' '.join(f'{k} {v}' for k, v in config['args'].items())}"
    if config.get("input_data"):
        prompt += f"\n输入: {config['input_data']}"
    prompt = self._apply_custom_prompt(prompt, task)

    report = self._run_claude_streaming(task_id, prompt,
        cwd=skill.path, timeout=600, progress_range=(5, 90), estimated_tools=30)

    if not report:
        task_db.update_task(task_id, status="failed", error_msg="执行超时或失败")
        return

    summary = self._extract_summary(report)
    task_db.update_task(task_id,
        status="completed", progress=100, summary=summary,
        result=report, completed_at=datetime.now())
```

### 2.5 旧任务兼容

旧的 `POST /api/tasks` 创建的任务没有 `skill_name`，需要兼容注册：

```python
# handlers/ocr.py
@register("", "ocr")            # 旧任务兼容
@register("", "table")
@register("", "full_page")
@register("screenshot", "ocr")  # 新 Skill 路径
@register("screenshot", "table")
@register("screenshot", "full_page")
def handle_ocr(executor, task):
    ...
```

### 2.6 新增功能流程对比

| 场景 | 需要做什么 | 改几个文件 |
|------|----------|----------|
| 新增 Skill 命令（如 fund-trade 的 market） | SKILL.md 加 command（`executor: claude`） | **1 个** |
| 新增固定工作流（如 简历分析） | 写 handler + @register + SKILL.md 加 command | **2 个** |

两种情况都**不需要改** task_executor.py / routers/ / main.py / App。

---

## 三、SKILL.md 规范

### 固定工作流命令（无 executor 字段）

```yaml
# screenshot/SKILL.md
---
name: screenshot
display_name: 截屏工具箱
icon: screenshot
description: 截屏识别、智能回复、表格提取等
category: tools
commands:
  - id: ocr
    name: 识别文字
    input: screenshot
    capture_types: [normal, long_scroll, manual_scroll]
    estimated_time: 10
    # 无 executor → 必须有注册的 handler

  - id: chat_reply
    name: 智能回复
    input: screenshot
    capture_types: [normal]
    estimated_time: 15

  - id: fund_holdings
    name: 持仓分析
    input: screenshot
    capture_types: [normal, long_scroll]
    estimated_time: 120
---
```

### Claude 命令（标记 executor: claude）

```yaml
# fund-trade/SKILL.md
---
name: fund-trade
display_name: 基金智能交易
icon: trending_up
description: LLM 决策引擎 + 量化信号 + 风控硬约束
category: finance
commands:
  - id: run
    name: 每日交易决策
    executor: claude           # ← 走通用 claude 路径
    input: none
    estimated_time: 300

  - id: review
    name: 持仓绩效审视
    executor: claude
    input: none
    estimated_time: 180

  - id: market                 # ← 新增命令，只改这里，零代码
    name: 市场分析
    executor: claude
    input: none
    estimated_time: 60
---
```

---

## 四、整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                   screenshot-assistant（纯 App）               │
│  GET /api/skills → 渲染项目列表 / 悬浮球菜单                    │
│  POST /api/skills/{name}/run → 提交任务                        │
│  GET /api/tasks/{id}/stream → SSE 进度                         │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│                smart-fund-server                              │
│                                                               │
│  routers/skill.py ─→ SkillRegistry ─→ create_task ─→ submit  │
│                                                               │
│  TaskExecutor._run()                                          │
│    │                                                          │
│    ├─ executor: claude? ──→ _execute_claude(cwd=skill.path)   │
│    │                                                          │
│    └─ 查 handler 注册表                                        │
│         ├─ 找到 → handler(self, task)                          │
│         └─ 找不到 → raise ValueError("未注册 handler")          │
│                                                               │
│  services/handlers/                                           │
│    ├── ocr.py               5 个命令复用                       │
│    ├── chat_reply.py        OCR → LLM 回复                    │
│    └── fund_holdings.py     OCR → 结构化 → LLM 分析            │
│                                                               │
│  _run_claude_streaming()    共享基础设施                        │
│  _do_ocr()                  （handler 和 claude 路径都可用）     │
│  event_bus + SSE                                              │
└───────────────────────────────────────────────────────────────┘
```

---

## 五、路由拆分

将 `routers/__init__.py`（2192 行）拆为模块：

```
routers/
├── __init__.py          # 汇总（~30 行）
├── _utils.py            # safe_call 等
├── _models.py           # Pydantic Models
├── fund_query.py        # 基金查询（~43 端点）
├── market.py            # 市场行情 + 热榜（~29 端点）
├── trade.py             # 交易 + 认证（~20 端点）
├── strategy.py          # 决策/风控/持仓（~29 端点）
├── task.py              # 任务 + OCR（~8 端点）
└── skill.py             # Skill API（4 端点）
```

---

## 六、数据模型变更

```sql
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS skill_name VARCHAR(64);
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS command_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_sa_tasks_skill ON sa_tasks(skill_name);
```

- `create_task()` 新增 `skill_name`, `command_id` 参数
- `list_tasks()` 新增 `skill_name` 过滤 + SELECT 返回

---

## 七、实施步骤

### Phase 1：路由拆分

拆 `routers/__init__.py` 为模块。纯重构，不改功能。

### Phase 2：Handler 注册机制

1. 创建 `services/handlers/` + 注册表
2. 现有 handler 搬出 task_executor.py → 各 handler 文件
3. `_run()` 改为：判断 claude → 查注册表 → 报错
4. `_run_claude_streaming()` 加 `cwd` 参数
5. 旧 task_type 兼容注册

### Phase 3：Skill 集成

1. 简化 `skill_registry.py`（移除 pipeline）
2. 修改 `task_db.py`（加字段）
3. 新增 `routers/skill.py`
4. 修改 `main.py`（初始化 SkillRegistry）
5. 简化 SKILL.md 文件

### Phase 4：清理

1. 删除 `screenshot-assistant/server/`
2. 标记 `POST /api/tasks` 为 deprecated
3. 部署 + 验证

---

## 八、文件改动清单

### 新增

| 文件 | 说明 |
|------|------|
| `services/handlers/__init__.py` | 注册表 |
| `services/handlers/ocr.py` | OCR handler |
| `services/handlers/chat_reply.py` | 智能回复 handler |
| `services/handlers/fund_holdings.py` | 持仓分析 handler |
| `services/skill_registry.py` | Skill 注册表 |
| `routers/skill.py` | 4 个 Skill API |
| `routers/_utils.py` `_models.py` | 共享代码 |
| `routers/fund_query.py` `market.py` `trade.py` `strategy.py` `task.py` | 拆分路由 |

### 修改

| 文件 | 说明 |
|------|------|
| `task_executor.py` | `_run()` → 查表分发 + `_execute_claude()` + `_is_claude_command()` |
| `task_db.py` | +skill_name/command_id |
| `main.py` | +SkillRegistry 初始化 |
| `routers/__init__.py` | 重写为汇总 |
| `screenshot/SKILL.md` | 去掉 executor/pipeline |

### 删除

| 文件 | 说明 |
|------|------|
| `screenshot-assistant/server/` | 错误代码 |
