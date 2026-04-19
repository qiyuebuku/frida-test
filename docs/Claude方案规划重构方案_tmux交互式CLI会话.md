# Claude 方案规划重构方案：本地持久化 API 服务 + tmux 交互式 CLI

> 日期：2026-04-19
> 状态：方案设计（v2 — 基于反馈修正）

---

## 1. 背景与问题

### 1.1 现状

当前 `claude-planner` 的工作流程：

```
GLM-5.1 (主会话, 便宜)
  → 收集项目上下文
  → 写入 /tmp/claude_planner_context_xxx.md
  → Agent({model: "sonnet"}) 派发子 agent
  → 子 agent 走 smart-router → Claude 后端 (pincc 已挂)
  → 返回方案文档
```

### 1.2 问题

| 问题 | 原因 |
|------|------|
| pincc 后端不可用 | 第三方 Claude 代理账号失效 |
| 无法代理官方 Claude | Anthropic 2026-02 明确禁止第三方使用订阅 OAuth token，有封号风险 |
| Agent 子 agent 是黑盒 | 执行过程不可见，无法追问，无法中途干预 |
| 依赖 smart-router 的 Claude 路由 | router 是多模型聚合服务，不应绑定特定 Claude 后端的可用性 |

### 1.3 设计目标

1. **自包含**：claude-planner 自己管理 Claude 后端，不依赖 smart-router 的 Claude 路由
2. **灵活切换后端**：通过 `backends.json` 灵活使用官方订阅、第三方 API key、中转站等
3. **可见进度**：实时观察 Claude CLI 的执行过程
4. **可追问**：任务完成后可以继续对话追问，不是一次性黑盒
5. **持久运行**：tmux 中的 Claude CLI 在后台持续运行，GLM-5.1 随时可以发新任务或追问
6. **上下文由 AI 决定**：SKILL.md 指导 AI 如何收集上下文，AI 根据实际情况自主决定收集什么，不写死

---

## 2. 核心思路

**不再用 Agent 工具派发子 agent，改为启动一个本地 API 服务管理持久化的 tmux Claude CLI 会话。**

从 `smart-fund-server` 的 task_orchestrator + API 层提取核心逻辑，构建一个轻量级的本地 planner 服务。

### 架构对比

```
【旧方案 - Agent 子 agent（一次性、黑盒）】
GLM-5.1 → Agent tool → smart-router → Claude 后端 → 返回结果
                         ↑ 依赖 router              ↑ 不可见、不可追问

【新方案 - 本地 API 服务 + tmux Claude CLI（持久、透明）】
GLM-5.1 → curl/Bash → 本地 planner API 服务 → tmux Claude CLI → 直接连 Claude 后端
          ↑ 先检查服务是否启动（健康检查），未启动则先拉起
          ↑ 随时调用      ↑ 持久运行，管理会话      ↑ 进度可见      ↑ env 变量控制后端
          ↑ 可追问        ↑ 任务状态 + 历史消息
```

### 关键区别

| 维度 | 旧方案 (Agent 子 agent) | 新方案 (本地 API 服务) |
|------|----------------------|---------------------|
| 执行方式 | Agent tool 内部子进程 | tmux 持久会话 |
| 进度可见性 | 不可见 | 通过 API 轮询/WebSocket 实时获取 |
| 追问能力 | 无（一次性） | 支持（会话保持活跃） |
| 后端依赖 | smart-router 的 Claude 路由 | Claude CLI 直连（env 变量控制） |
| 生命周期 | 随主会话结束而消失 | 独立持久运行，可跨多轮对话 |
| 上下文收集 | AI + SKILL.md 指引 | AI + SKILL.md 指引（不变） |

---

## 3. 整体架构

```
.claude/skills/claude-planner/
├── SKILL.md                  # 技能定义：指导 AI 收集上下文 + 调用 planner API（现有，更新执行部分）
├── backends.json             # Claude 后端配置（官方/第三方/中转站）
├── server.py                 # 本地 API 服务（FastAPI，从 smart-fund-server 精简提取）
├── tmux_manager.py           # tmux Claude CLI 会话管理（从 task_orchestrator 提取）
├── db.py                     # 轻量 SQLite 任务存储（从 task_db.py 精简）
└── requirements.txt          # 依赖：fastapi, uvicorn

.claude/agents/
└── planner.md                # 架构师 persona + 输出格式（现有，作为系统 prompt 注入 Claude CLI 会话）
```

### 三个角色的职责划分

```
┌─────────────────────────────────────────────────────┐
│  SKILL.md（AI 读取并执行）                            │
│  职责：指导 AI 如何使用这个 skill                      │
│  - 根据用户需求，AI 自主决定收集哪些上下文               │
│  - AI 通过 Bash/curl 调用 planner API                 │
│  - AI 向用户报告进度和结果                             │
├─────────────────────────────────────────────────────┤
│  server.py（本地 API 服务，持久运行）                   │
│  职责：管理 tmux Claude CLI 会话的生命周期              │
│  - 创建/销毁 tmux 会话                                │
│  - 接收 prompt、转发给 tmux 中的 Claude CLI            │
│  - 监控执行进度、解析工具调用                           │
│  - 维护任务状态（SQLite）                              │
│  - 支持追问、停止、查询                                │
├─────────────────────────────────────────────────────┤
│  tmux_manager.py（底层 tmux 操作）                    │
│  职责：与 tmux 交互                                   │
│  - 创建/销毁 tmux session                             │
│  - 发送文本、捕获输出                                  │
│  - 检测提示符、过滤噪音                                │
│  - 环境变量注入（backends.json → env vars）            │
└─────────────────────────────────────────────────────┘
```

---

## 4. backends.json 设计

```json
{
  "active": "official",
  "backends": {
    "official": {
      "description": "Claude 官方（本地 OAuth 订阅，免 key）",
      "env": {},
      "model": "sonnet"
    },
    "api-key": {
      "description": "Anthropic API Key（按量计费）",
      "env": {
        "ANTHROPIC_AUTH_TOKEN": "sk-ant-api03-xxx",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com"
      },
      "model": "claude-sonnet-4-6"
    },
    "third-party": {
      "description": "第三方中转站",
      "env": {
        "ANTHROPIC_AUTH_TOKEN": "第三方 key",
        "ANTHROPIC_BASE_URL": "https://xxx.proxy/api"
      },
      "model": "claude-sonnet-4-6"
    }
  }
}
```

- `official` 后端 env 为空 → Claude CLI 使用本地 OAuth 凭证（Pro 订阅）
- `active` 字段控制当前后端，切换无需改代码
- 后续可在 SKILL.md 中指定 `backend` 参数让 AI 选择不同后端

---

## 5. server.py — 本地 API 服务

### 5.1 API 端点设计

从 `smart-fund-server` 的 task 路由精简提取，保留核心能力：

| 方法 | 端点 | 功能 | 对应 smart-fund-server |
|------|------|------|----------------------|
| `POST` | `/tasks` | 创建任务：启动 tmux Claude CLI 会话 + 发送 prompt | `POST /api/skills/{name}/run` |
| `GET` | `/tasks/{id}` | 查询任务状态/进度/结果 | `GET /api/tasks/{id}` |
| `GET` | `/tasks/{id}/steps` | 获取结构化执行步骤（工具调用序列） | `GET /api/tasks/{id}/steps` |
| `POST` | `/tasks/{id}/message` | 追问：向 tmux Claude 会话发送新消息 | `POST /api/tasks/{id}/message` |
| `POST` | `/tasks/{id}/stop` | 停止任务（发 Escape） | `POST /api/tasks/{id}/stop` |
| `DELETE` | `/tasks/{id}` | 销毁任务：关闭 tmux + 清理数据 | `DELETE /api/tasks/{id}` |
| `GET` | `/tasks` | 列出任务 | `GET /api/tasks` |
| `POST` | `/shutdown` | 关闭 planner 服务自身（任务完成后结束进程） | — |
| `WS` | `/terminal/{id}/ws` | 实时终端镜像（可选，v2） | `WS /terminal/{id}/ws` |
| `GET` | `/health` | 健康检查 | — |
| `GET` | `/backends` | 列出可用后端 + 当前 active | — |
| `PUT` | `/backends/active` | 切换 active 后端 | — |

### 5.2 请求/响应格式

**创建任务 `POST /tasks`**：
```json
// 请求
{
  "prompt": "请基于上下文文件写方案...",
  "context_file": "/tmp/claude_planner_context_1745000000.md",
  "backend": "official",        // 可选，默认用 backends.json 的 active
  "model": "sonnet",            // 可选，默认用后端配置的 model
  "cwd": "/home/yuyang/frida-test",  // 可选，Claude CLI 工作目录
  "timeout": 600                // 可选，默认 600s
}

// 响应
{
  "task_id": 1,
  "status": "processing",
  "backend": "official",
  "model": "sonnet"
}
```

**查询进度 `GET /tasks/1`**：
```json
{
  "task_id": 1,
  "status": "processing",       // pending / processing / completed / failed / stopped
  "progress": 45,               // 0-100
  "progress_msg": "读取上下文文件",
  "partial_result": "...",      // 当前已生成的部分内容
  "tool_calls": [               // 工具调用序列
    {"tool": "Read", "display": "Read: context.md", "at": 2.1},
    {"tool": "Bash", "display": "Bash: ls src/", "at": 5.3}
  ],
  "result": null,               // 完成后才有
  "duration_sec": 30
}
```

**追问 `POST /tasks/1/message`**：
```json
// 请求
{ "message": "方案中的数据库部分能否再详细一些？" }

// 响应
{ "status": "processing", "message": "追问已发送" }
```

### 5.3 服务生命周期

```bash
# 启动（在后台运行）
cd .claude/skills/claude-planner
python server.py --port 8899

# 或用 nohup / systemd 管理
nohup python server.py --port 8899 > planner.log 2>&1 &
```

- 默认端口 `8899`（不与 smart-fund-server 的 8900 冲突）
- 使用 SQLite（`planner.db`），无需外部数据库
- 空闲 tmux 会话 30 分钟后自动清理
- 进程退出时清理所有 tmux 会话

---

## 6. tmux_manager.py — tmux 会话管理

从 `smart-fund-server/src/infrastructure/tmux/session.py` + `task_orchestrator.py` 的核心逻辑提取。

### 6.1 提取来源映射

| 新模块内容 | 提取自 smart-fund-server | 改动 |
|-----------|------------------------|------|
| `_tmux()` | `src/infrastructure/tmux/session.py:21` | 直接复用 |
| `_is_noise()` | `src/infrastructure/tmux/session.py:33` | 直接复用 |
| `_is_at_prompt()` | `src/infrastructure/tmux/session.py:123` | 直接复用 |
| `_extract_content()` | `src/infrastructure/tmux/session.py:96` | 直接复用 |
| `_is_thinking()` | `src/infrastructure/tmux/session.py:106` | 直接复用 |
| `PlannerSession.__init__()` | `ClaudeTmuxSession.__init__()` | **改写**：env 从 backends.json 注入 |
| `PlannerSession.send()` | `ClaudeTmuxSession.send()` | 直接复用 |
| `PlannerSession.get_new_lines()` | `ClaudeTmuxSession.get_new_lines()` | 直接复用 |
| `_run_streaming()` | `task_orchestrator._run_claude_streaming()` | **精简**：去掉 task_db 写入，只更新内存 + SQLite |

### 6.2 关键改动：环境变量注入

原版 `ClaudeTmuxSession` 没有环境变量管理。新版从 `backends.json` 读取并注入：

```python
class PlannerSession:
    def __init__(self, task_id, backend_config, cwd=None, model=None):
        # 从 backends.json 读取 env vars
        env_vars = backend_config.get("env", {})
        env_str = " ".join(f'{k}="{v}"' for k, v in env_vars.items())

        claude_cmd = "claude --dangerously-skip-permissions"
        if model:
            claude_cmd += f" --model {model}"

        # 注入环境变量
        if env_str:
            claude_cmd = f"env {env_str} {claude_cmd}"

        if cwd:
            claude_cmd = f"cd {cwd} && {claude_cmd}"

        # 创建 tmux session
        _tmux("new-session", "-d", "-s", session_name, "-x", "80", "-y", "50", claude_cmd)
```

### 6.3 流式输出监控

从 `_run_claude_streaming()` 精简，核心循环保留：

```python
def run_streaming(self, prompt, timeout=600):
    self.session.send(prompt)

    accumulated = []
    tool_calls = []
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(1)
        new_lines = self.session.get_new_lines()

        if new_lines:
            accumulated.extend(new_lines)
            # 检测工具调用
            for line in new_lines:
                tool_info = _parse_tmux_tool_line(line)
                if tool_info:
                    tool_calls.append(tool_info)
            # 更新 SQLite 进度
            self._update_progress(accumulated, tool_calls)

        # 检测完成：稳定 + 在提示符 + 无 thinking
        if self._is_done(accumulated):
            break

    return {
        "result": _clean_result(accumulated),
        "tool_calls": tool_calls,
        "duration": time.time() - start
    }
```

### 6.4 追问机制

从 `task_orchestrator.queue_message()` + `submit_followup()` 精简：

- tmux 会话活跃时 → 直接 `session.send(message)`
- tmux 会话已关闭但有 session_id → 用 `--resume` 恢复后发送
- 无会话 → 返回错误

---

## 7. db.py — 轻量 SQLite 存储

从 `task_db.py` 精简，去掉 PostgreSQL 依赖，使用 SQLite：

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending/processing/completed/failed/stopped
    progress      INTEGER NOT NULL DEFAULT 0,        -- 0-100
    progress_msg  TEXT,

    prompt        TEXT,             -- 发送给 Claude 的完整 prompt
    context_file  TEXT,             -- 上下文文件路径
    result        TEXT,             -- Claude 最终输出（Markdown）
    partial_result TEXT,            -- 流式部分输出
    tool_calls    TEXT,             -- JSON: [{tool, display, detail, at}]

    backend       TEXT,             -- 使用的后端名称
    model         TEXT,             -- 使用的模型
    session_id    TEXT,             -- Claude CLI session UUID
    cwd           TEXT,             -- 工作目录

    messages      TEXT DEFAULT '[]', -- JSON: [{role, content, created_at}] 对话历史
    error_msg     TEXT,

    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at  TIMESTAMP,
    duration_sec  INTEGER
);
```

- 零外部依赖（Python 自带 sqlite3）
- 文件位置：`.claude/skills/claude-planner/planner.db`

---

## 8. SKILL.md 工作流设计

### 8.1 保留现有上下文收集流程

当前 `SKILL.md` 的上下文收集流程已经很成熟，**完全保留**：

```
阶段 1：收集上下文（当前模型 GLM-5.1，不变）
  - 分析用户需求，确定需要哪些项目上下文
  - 读取相关源码、文档、schema、已有方案
  - 确定方案文档的保存路径
  - 上下文写入 /tmp/claude_planner_context_{timestamp}.md
```

### 8.2 执行层从 Agent tool 改为 planner API

唯一变化的是阶段 2-4 的执行方式。**在调用 API 前，AI 必须先检查服务是否启动**：

```
阶段 1.5：确保 planner 服务可用
  - curl -s http://localhost:8899/health
  - 如果返回 200 → 服务已启动，继续
  - 如果连接失败 → 启动服务：
    nohup python /home/yuyang/frida-test/.claude/skills/claude-planner/server.py \
      --port 8899 > /tmp/planner.log 2>&1 &
    等待 2 秒后再次检查健康状态

阶段 2：委托 planner API（替代原来的 Agent 子 agent）
  - curl POST http://localhost:8899/tasks
  - 传入：prompt + context_file + 保存路径
  - 获得 task_id

阶段 3：监控进度（替代原来的黑盒等待）
  - curl GET http://localhost:8899/tasks/{id}
  - 向用户报告当前步骤（tool_calls）
  - 直到 status 变为 completed/failed

阶段 4：汇报结果（不变）
  - 方案文件路径 + 核心决策摘要
```

### 8.3 planner.md 的角色

`planner.md`（`.claude/agents/planner.md`）定义了架构师 persona 和输出格式。
它作为**系统 prompt** 注入到 tmux Claude CLI 会话中：

```
planner.md 内容：
  - 资深技术架构师角色设定
  - 按复杂度决定方案粒度的原则
  - 每步可独立测试验收的要求
  - 标准输出格式（需求分析→技术方案→实现步骤→验收标准）
  - 上下文补充规则（优先用已有上下文，必要时可自行探索）
```

**集成方式**：planner API 创建任务时，自动将 `planner.md` 内容作为 prompt 前缀，
然后再接上用户的具体需求和上下文文件路径。

### 8.4 SKILL.md 中的 API 调用示例

```markdown
## planner API 使用方式

planner 服务地址：`http://localhost:8899`

### 使用前：确保服务已启动
```bash
# 健康检查
curl -s http://localhost:8899/health
# 如果无响应，启动服务：
nohup python /home/yuyang/frida-test/.claude/skills/claude-planner/server.py --port 8899 > /tmp/planner.log 2>&1 &
sleep 2 && curl -s http://localhost:8899/health
```

### 创建任务
curl -s -X POST http://localhost:8899/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt": "请基于上下文文件写方案...", "context_file": "/tmp/xxx.md", "cwd": "/home/yuyang/frida-test"}'
→ 返回 {"task_id": N, "status": "processing"}

### 查询进度
curl -s http://localhost:8899/tasks/{task_id}
→ 返回 {"status": "processing", "progress": 45, "tool_calls": [...], ...}

### 追问
curl -s -X POST http://localhost:8899/tasks/{task_id}/message \
  -H "Content-Type: application/json" \
  -d '{"message": "补充说明..."}'

### 停止任务
curl -s -X POST http://localhost:8899/tasks/{task_id}/stop
```

---

## 9. 完整请求生命周期

```
用户                    GLM-5.1 (主会话)              planner API 服务             tmux Claude CLI
 |                           |                              |                            |
 |-- "帮我规划 xxx" -------->|                              |                            |
 |                           |-- GET /health -------------->|                            |
 |                           |<-- 200 OK -------------------|                            |
 |                           |  (如无响应则先启动服务)        |                            |
 |                           |                              |                            |
 |                           |-- 收集上下文（AI 自主决定）    |                            |
 |                           |-- Write /tmp/context_xxx.md  |                            |
 |                           |                              |                            |
 |                           |-- POST /tasks -------------->|                            |
 |                           |   {prompt, context_file}     |-- 创建 tmux session ------>|
 |                           |                              |   env vars from backends   |-- claude --dangerously...
 |                           |<-- {"task_id": 1} -----------|                            |-- 等待 ❯ 就绪
 |                           |                              |                            |
 |                           |-- GET /tasks/1 ------------->|                            |
 |                           |   (轮询进度)                 |-- session.get_new_lines() >|-- Claude 执行中...
 |<-- "正在读取上下文..." ----|<-- {progress:30, ...} ------|                            |   (Read 工具调用)
 |                           |                              |                            |
 |                           |-- GET /tasks/1 ------------->|                            |
 |<-- "正在写方案..." -------|<-- {progress:70, ...} ------|   (Write 工具调用)          |-- ...
 |                           |                              |                            |
 |                           |-- GET /tasks/1 ------------->|                            |
 |                           |<-- {status:"completed",     |                            |
 |                           |    result:"方案内容..."} ----|   (检测到完成)              |-- ❯ 回到提示符
 |                           |                              |                            |
 |<-- "方案已生成..." -------|                              |                            |
 |                           |                              |                            |
 |-- "追问: 数据库部分..." ->|                              |                            |
 |                           |-- POST /tasks/1/message ---->|                            |
 |                           |   {message: "数据库..."}     |-- session.send(msg) ------->|-- 收到追问
 |                           |<-- {"status":"processing"} --|                            |-- 开始回答
 |                           |   (再次轮询...)               |                            |
 |                           |<-- {result:"补充..."} -------|                            |-- ❯ 完成
 |<-- "追问已回答..." -------|                              |                            |
```

---

## 10. 实施步骤

### Step 1：创建 `tmux_manager.py`

- 从 `smart-fund-server/src/infrastructure/tmux/session.py` 复制基础函数
- 创建 `PlannerSession` 类，添加 `backends.json` 环境变量注入
- 从 `task_orchestrator.py` 提取 `_run_claude_streaming` 核心循环
- 从 `task_orchestrator.py` 提取追问机制（`queue_message` + `submit_followup` 精简版）

### Step 2：创建 `db.py`

- SQLite 版本的 task 存储
- 参照 `task_db.py` 精简，保留：create / update / get / list / delete

### Step 3：创建 `server.py`

- FastAPI 应用，参照 `smart-fund-server/src/interfaces/api/__init__.py` 精简
- 路由参照 `routes/task.py` + `routes/skill.py` 的任务相关端点
- Lifespan：初始化 DB + 加载 backends.json
- 并发控制：`threading.Semaphore(2)`

### Step 4：创建 `backends.json`

- 默认配置 `official` 后端
- 后续按需添加 API key / 第三方后端

### Step 5：更新 `SKILL.md`

- 更新工作流：收集上下文 → 调 planner API → 轮询进度 → 汇报结果
- 保留上下文收集的灵活性（AI 自主决定）
- 提供完整的 API 调用示例

### Step 6：端到端测试

1. 启动 planner 服务：`python server.py`
2. 在 Claude Code 中触发 claude-planner 技能
3. 验证：上下文收集 → API 创建任务 → 进度轮询 → 结果获取
4. 验证追问能力
5. 验证会话清理

---

## 11. 与 smart-router 的关系

重构后，claude-planner 完全绕过 smart-router 的 Claude 路由：

| smart-router 路由 | 用途 | claude-planner |
|------------------|------|---------------|
| GLM-* → 智谱 | 主会话 GLM-5.1 | 间接使用 |
| plan-opus → Claude | ~~之前用于子 agent~~ | **不再使用** |
| claude-* → Claude | 其他场景 | **不再使用** |

Claude CLI 直接连接 Claude 后端（env 变量控制），smart-router 只负责 GLM 路由。

---

## 12. 风险与注意事项

1. **tmux 依赖**：运行环境必须有 tmux（WSL2 和服务器都已安装）
2. **Claude CLI**：必须有 `claude` 命令且已登录
3. **official 后端的 rate limit**：与正在运行的 Claude Code 共享 Pro 订阅额度
4. **refresh token 冲突**：official 后端与本地 Claude Code 共享 OAuth，一个刷新会使另一个失效（改用 API key 后端可规避）
5. **SQLite 并发**：单文件 DB，足够应对单用户的并发场景（`MAX_CONCURRENT=2`）
6. **端口冲突**：默认 8899，需确保不被占用

---

## 13. 后续扩展

- **WebSocket 终端镜像**：v2 加入 `WS /terminal/{id}/ws`，实时查看 Claude CLI 输出（从 smart-fund-server 的 terminal.py 提取）
- **多后端策略**：不同类型任务自动选择不同后端（方案用官方，代码审查用第三方）
- **成本统计**：解析 tmux 输出中的 token usage
- **远程部署**：将 planner 服务部署到服务器，GLM-5.1 远程调用
- **会话恢复**：利用 Claude CLI `--resume` 跨服务重启复用上下文
