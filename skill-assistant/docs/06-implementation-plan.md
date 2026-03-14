# 06 - 分阶段实施计划

## v1 状态

v1 已于 2026-03-10 全部完成（4 个阶段）。以下是 v2 改造计划。

---

## v2 阶段总览

```
Phase A (服务端流式)     Phase B (App UI 重构)      Phase C (配置系统)
───────────────────    ──────────────────────    ──────────────────────
 event_bus.py            删除 HomeScreen           任务配置页 (提示词/规则)
 stream-json 解析        2 tab 导航 (任务/设置)     悬浮窗菜单配置
 SSE 端点                SSE 客户端接入             自定义操作
 DB 新增字段             实时渲染 partial_result    ActionConfig 动态化
                        工具调用日志展示            配置持久化 DataStore
```

---

## Phase A：服务端实时流式输出

### A.1 数据库变更
- [ ] `sa_tasks` 新增 `partial_result` TEXT 字段
- [ ] `sa_tasks` 新增 `tool_calls` JSONB 字段
- [ ] `sa_tasks` 新增 `config` JSONB 字段（存储任务的自定义提示词/规则）
- [ ] `task_db.py` 更新 CRUD 支持新字段

### A.2 内存事件总线
- [ ] 新建 `services/event_bus.py`
- [ ] `TaskEventBus` 类：subscribe / unsubscribe / emit
- [ ] 线程安全（threading.Lock + queue.Queue）
- [ ] 全局单例 `event_bus`

### A.3 流式 Claude 调用
- [ ] `task_executor.py` 新增 `_run_claude_streaming()` 方法
- [ ] `claude -p --output-format stream-json --verbose` 启动子进程
- [ ] 逐行读取 JSONL，解析 `assistant` 事件中的 `text` 和 `tool_use`
- [ ] 工具调用 → emit `tool_call` 事件 + 更新 progress_msg
- [ ] 文本输出 → emit `text_delta` 事件 + 追加 partial_result
- [ ] 最终结果 → emit `done` 事件
- [ ] 每 3 秒批量写 DB（partial_result + tool_calls）
- [ ] `_handle_fund_holdings` / `_handle_skill_command` 改用 `_run_claude_streaming`

### A.4 SSE 端点
- [ ] `GET /api/tasks/{id}/stream` — SSE 端点
- [ ] 订阅 event_bus，异步推送事件到客户端
- [ ] 心跳保活（0.3 秒无事件发送 `: heartbeat`）
- [ ] 任务已完成时直接返回 `done` 事件
- [ ] `GET /api/tasks/{id}` 响应新增 partial_result / tool_calls（降级用）

### A.5 自定义提示词支持（服务端）
- [ ] `POST /api/tasks` 接收 `system_prompt` 和 `rules` 参数
- [ ] 保存到 `sa_tasks.config` JSONB
- [ ] `task_executor.py` 读取 config 中的提示词，拼接到 claude -p prompt
- [ ] 新增 `GET /api/task-configs` — 获取服务端默认配置（各任务类型的默认提示词）
- [ ] 新增 `POST /api/task-configs` — 保存用户自定义配置

### A.6 验证
- [ ] 远端 `claude -p --output-format stream-json --verbose` 正常输出
- [ ] SSE 端点能持续推送 tool_call / text_delta / done 事件
- [ ] 断线后 `GET /api/tasks/{id}` 能返回 partial_result

---

## Phase B：App UI 重构

### B.1 导航结构改造
- [ ] `MainActivity.kt`：Screen 枚举从 3 个减为 2 个（TASKS / SETTINGS）
- [ ] 删除 `HomeScreen.kt`
- [ ] 底部导航栏改为 2 tab

### B.2 任务页改造（原 TaskListScreen）
- [ ] 顶部：App 名称 + StatusDot（服务状态）
- [ ] 无障碍服务未开启时显示警告卡片
- [ ] AI 分析按钮条：每日决策 + 持仓审视（一行紧凑横排）
- [ ] 任务列表：按天分组 + 筛选器 + TaskCard
- [ ] 移除 3 秒轮询（列表页不需要实时更新，进入详情页时才用 SSE）

### B.3 SSE 客户端
- [ ] 新建 `network/TaskStreamClient.kt`
- [ ] `connect(taskId, onToolCall, onTextDelta, onDone, onError)` — SSE 连接
- [ ] 解析 `data:` 行中的 JSON 事件
- [ ] 分发到对应回调
- [ ] 断线重连：指数退避，重连时先 GET 一次完整状态

### B.4 任务详情页 SSE 实时渲染
- [ ] 打开详情页时连接 `GET /api/tasks/{id}/stream`
- [ ] `onToolCall` → 追加到工具调用日志列表，更新进度条
- [ ] `onTextDelta` → 增量追加文本，用 MarkdownViewer 实时渲染
- [ ] `onDone` → 切换到完成/失败状态
- [ ] `onError` → 降级为轮询 `GET /api/tasks/{id}`

**关键：App 端只显示 Markdown 渲染结果，不显示原始 JSON。**
- `tool_calls` 展示为可读的步骤日志（如 "采集市场数据..."），不是 JSON
- `partial_result`（text_delta 累积）直接喂给 MarkdownViewer 渲染
- 最终 `result` 也是 Markdown，同样用 MarkdownViewer 展示

### B.5 工具调用日志展示
- [ ] 新建 `ui/components/ToolCallLog.kt`
- [ ] 显示为竖排步骤列表（非 JSON）：
  ```
  ✅ 采集市场环境数据          3s
  ✅ 查看热门板块              2s
  ✅ 获取新闻快讯              1s
  🔄 分析 012414 基金详情...   进行中
  ○  分析 519191 基金详情      待执行
  ```
- [ ] 工具命令映射为可读描述：
  | 命令 | 显示 |
  |------|------|
  | `python client.py market_overview` | 采集市场环境数据 |
  | `python client.py hot_board` | 查看热门板块 |
  | `python client.py 012414 detail` | 分析 012414 基金详情 |
  | `python client.py 012414 rank` | 查看 012414 排名数据 |
  | `curl .../news_overview` | 获取新闻快讯 |
  | 其他 Bash 命令 | 执行命令... |

### B.6 MarkdownViewer 增量更新
- [ ] 支持内容动态追加（不是每次重新创建 WebView）
- [ ] `updateContent(newMarkdown)` — 通过 JS 更新 HTML 内容
- [ ] 自动滚动到底部（新内容追加时）

---

## Phase C：配置系统

### C.1 设置页重写
- [ ] 重写 `SettingsScreen.kt`
- [ ] 分区：任务配置 + 悬浮窗菜单 + 系统设置

### C.2 任务配置
- [ ] 任务类型列表（ocr / chat_reply / table / fund_holdings 等）
- [ ] 点击进入配置详情页 `TaskConfigScreen.kt`
- [ ] 系统提示词（多行文本框）
- [ ] 输出规则（多行文本框）
- [ ] 处理模式选择（同步 SSE / 异步任务）
- [ ] 超时时间设置
- [ ] 恢复默认按钮

### C.3 悬浮窗菜单配置
- [ ] 新建 `MenuConfigScreen.kt`
- [ ] 拖拽排序（ReorderableList）
- [ ] 开关控制显隐
- [ ] 添加自定义操作（名称 + 截图模式 + 提示词）

### C.4 ActionConfig 动态化
- [ ] `CaptureModels.kt`：ActionConfig 扩展字段（enabled / sortOrder / systemPrompt / rules / processingMode / timeoutSec）
- [ ] `Actions.getAll()` 从 DataStore 读取合并内置 + 自定义
- [ ] `FloatingWindowService.kt` 菜单从硬编码改为动态读取
- [ ] 配置持久化到 DataStore

### C.5 配置同步
- [ ] `HttpClient.kt`：发送任务时附带 system_prompt 和 rules
- [ ] 服务端读取 config 拼接到 claude -p prompt

---

## 文件变更清单

### 服务端新增（2 个）

| 文件 | 说明 |
|------|------|
| `services/event_bus.py` | 内存事件总线（subscribe / unsubscribe / emit） |
| DDL: sa_tasks 新字段 | partial_result TEXT + tool_calls JSONB + config JSONB |

### 服务端修改（3 个）

| 文件 | 变更 |
|------|------|
| `services/task_executor.py` | 新增 `_run_claude_streaming()`，替换 `_run_claude_with_progress()` |
| `services/task_db.py` | CRUD 支持 partial_result / tool_calls / config |
| `routers/__init__.py` | 新增 SSE 端点 + task-configs API + POST /api/tasks 接收 config |

### App 端新增（4 个）

| 文件 | 说明 |
|------|------|
| `network/TaskStreamClient.kt` | SSE 客户端 |
| `ui/components/ToolCallLog.kt` | 工具调用日志组件（步骤列表，非 JSON） |
| `ui/screens/TaskConfigScreen.kt` | 任务配置详情页 |
| `ui/screens/MenuConfigScreen.kt` | 悬浮窗菜单配置页 |

### App 端修改（7 个）

| 文件 | 变更 |
|------|------|
| `MainActivity.kt` | 2 tab 导航（去掉 HOME） |
| `ui/screens/TaskListScreen.kt` | 顶部加 StatusDot + AI 分析按钮，成为主页面 |
| `ui/screens/TaskDetailScreen.kt` | SSE 接入 + 实时渲染 partial_result + 工具调用日志 |
| `ui/screens/SettingsScreen.kt` | 重写：任务配置 + 悬浮窗菜单 + 系统设置 |
| `ui/components/MarkdownViewer.kt` | 支持增量更新 + 自动滚动 |
| `data/TaskItem.kt` | 新增 partialResult / toolCalls 字段 |
| `capture/CaptureModels.kt` | ActionConfig 扩展 + Actions.getAll() 动态化 |
| `network/HttpClient.kt` | getTask 解析新字段 + createTask 附带 config |
| `service/FloatingWindowService.kt` | 菜单从硬编码改为 DataStore 动态读取 |

### App 端删除（1 个）

| 文件 | 说明 |
|------|------|
| `ui/screens/HomeScreen.kt` | 整个文件移除 |
