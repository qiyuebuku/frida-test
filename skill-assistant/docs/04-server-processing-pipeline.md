# 04 - 服务端处理流水线（v2 实时输出）

## v1 的问题

```
v1 流程：
claude -p (Popen, stdout=PIPE)
    │
    │  进程运行中... 3-10 分钟
    │  用户看到的：
    │    35% 正在采集市场数据...    ← 假的，按时间插值
    │    45% 正在分析基金详情...    ← 假的
    │    60% 正在分析持仓配置...    ← 假的
    │    85% 正在整理分析报告...    ← 假的
    │
    ▼
process.stdout.read()  ← 进程结束后才一次性读取全部输出
    │
    ▼
写入 DB → 100% 完成
```

**问题**：用户等待 5-10 分钟，只看到假进度条，不知道 AI 在干什么。直到完全结束才能看到任何内容。

## v2 方案：实时流式输出

### 核心改动

`claude -p` 支持 `--output-format stream-json --verbose`，输出 JSONL 事件流：

```jsonl
{"type":"system","subtype":"init","tools":[...],...}
{"type":"assistant","message":{"content":[{"type":"text","text":"让我先..."}],...}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"python client.py market_overview"}}],...}}
{"type":"assistant","message":{"content":[{"type":"tool_result","output":"大盘指数 3245..."}],...}}
{"type":"assistant","message":{"content":[{"type":"text","text":"# 持仓分析报告\n\n## 市场环境\n..."}],...}}
{"type":"result","subtype":"success","result":"# 持仓分析报告\n..."}
```

我们可以**逐行读取事件**，实时提取：
1. **工具调用** → 更新 `progress_msg`（如 "正在执行: python client.py market_overview"）
2. **文本输出** → 追加到 `partial_result`，用户可以看到报告逐步生成
3. **最终结果** → 写入 `result`，标记完成

### 用户体验变化

```
v2 流程：
claude -p (Popen, --output-format stream-json --verbose)
    │
    │  逐行读取 JSONL 事件
    │  用户看到的（实时！每 3 秒随轮询更新）：
    │
    │  10% 🔧 python client.py market_overview
    │  15% 🔧 python client.py hot_board
    │  20% 🔧 curl http://127.0.0.1:8900/api/news_overview
    │  30% 🔧 python client.py 012414 detail
    │  35% 🔧 python client.py 012414 rank
    │  40% 🔧 python client.py 519191 detail
    │  ...
    │  70% 📝 正在生成报告...
    │       "# 持仓分析报告
    │        ## 市场环境
    │        今日大盘..."          ← partial_result 不断增长
    │  85% 📝 报告持续输出中...
    │       "...
    │        ## 操作建议
    │        1. 减仓纳指..."
    │
    ▼
100% 完成 → result = 完整报告
```

---

## 一、数据库变更

### `sa_tasks` 新增字段

```sql
ALTER TABLE sa_tasks ADD COLUMN partial_result TEXT;      -- 实时中间输出（不断追加）
ALTER TABLE sa_tasks ADD COLUMN tool_calls    JSONB;      -- 工具调用记录列表
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `partial_result` | TEXT | AI 生成中的文本，每几秒写入一次，App 轮询时展示 |
| `tool_calls` | JSONB | 工具调用日志，如 `[{"tool":"Bash","input":"python client.py market_overview","at":1710000}]` |

### 字段使用时机

| 阶段 | partial_result | tool_calls | progress_msg |
|------|---------------|------------|-------------|
| 工具调用中 | 可能为空 | 持续追加 | "🔧 python client.py ..." |
| 报告生成中 | 持续追加 | 不变 | "📝 正在生成报告..." |
| 完成 | 清空（移入 result） | 保留 | null |

---

## 二、整体流水线

```
客户端提交截图
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Stage 1: 接收 & 保存（不变）                          │
│  进度: 0% → 5%                                       │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Stage 2: OCR 识别（不变）                             │
│  进度: 5% → 20%                                      │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Stage 3: 数据结构化（不变）                           │
│  进度: 20% → 35%                                     │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Stage 4: 智能分析（★ 改造重点）                       │
│  - claude -p --output-format stream-json --verbose    │
│  - 逐行读取 JSONL，实时解析 tool_use 和 text 事件      │
│  - 工具调用 → 更新 progress_msg + tool_calls          │
│  - 文本输出 → 追加 partial_result                     │
│  - 每 N 秒（或每 N 个事件）批量写入 DB                 │
│  进度: 35% → 90%（基于实际事件，非时间估算）            │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Stage 5: 结果写入（不变）                             │
│  - partial_result 清空，result 写入完整报告            │
│  进度: 90% → 100%                                    │
└──────────────────────────────────────────────────────┘
```

---

## 三、Stage 4 核心改造

### 3.1 stream-json 事件解析器

```python
def _run_claude_streaming(self, task_id: int, prompt: str,
                          timeout: int = 600,
                          progress_range: tuple = (35, 90)) -> str | None:
    """启动 claude -p 并实时解析 stream-json 事件"""
    env = os.environ.copy()
    env["FUND_API_BASE"] = "http://127.0.0.1:8900"

    start_pct, end_pct = progress_range

    process = subprocess.Popen(
        ["claude", "-p", prompt,
         "--allowedTools", "Bash,Read,Glob,Grep",
         "--output-format", "stream-json", "--verbose"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,  # 行缓冲
        cwd=SKILL_DIR, env=env
    )

    tool_calls = []        # 工具调用记录
    partial_text = []      # 累积的文本输出
    tool_count = 0         # 已执行的工具数量
    last_db_write = 0      # 上次写 DB 的时间

    start_time = time.time()

    for line in process.stdout:
        line = line.strip()
        if not line:
            continue

        # 超时检查
        if time.time() - start_time > timeout:
            process.kill()
            return None

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")

        # === 处理 assistant 消息（含 text 和 tool_use） ===
        if event_type == "assistant":
            content_list = event.get("message", {}).get("content", [])
            for block in content_list:
                block_type = block.get("type")

                if block_type == "text":
                    # AI 输出文本（可能是思考过程或最终报告）
                    text = block.get("text", "")
                    partial_text.append(text)

                elif block_type == "tool_use":
                    # AI 调用工具
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    tool_count += 1

                    # 提取可读的命令描述
                    if tool_name == "Bash":
                        cmd = tool_input.get("command", "")
                        display = cmd[:100]
                    elif tool_name == "Read":
                        display = tool_input.get("file_path", "")
                    else:
                        display = f"{tool_name}"

                    tool_calls.append({
                        "tool": tool_name,
                        "display": display,
                        "at": time.time() - start_time,
                    })

                    # 基于工具调用次数估算进度
                    # 典型持仓分析约 10-20 次工具调用
                    estimated_pct = min(end_pct,
                        start_pct + int((end_pct - start_pct) * tool_count / 20))
                    self._progress(task_id, estimated_pct, f"🔧 {display}")

        # === 处理最终结果 ===
        elif event_type == "result":
            final_result = event.get("result", "")
            # 清空 partial_result
            task_db.update_task(task_id, partial_result=None)
            return final_result

        # === 定期写入 partial_result 到 DB（节流，避免写太频繁） ===
        now = time.time()
        if now - last_db_write >= 3 and partial_text:
            accumulated = "".join(partial_text)
            task_db.update_task(task_id,
                partial_result=accumulated,
                tool_calls=json.dumps(tool_calls, ensure_ascii=False)
            )
            last_db_write = now

    # 进程结束但没收到 result 事件
    process.wait()
    if process.returncode != 0:
        stderr = process.stderr.read()
        logger.warning(f"claude -p failed: {stderr[:300]}")
        return None

    # 兜底：用 partial_text 作为结果
    return "".join(partial_text).strip() or None
```

### 3.2 进度估算策略变更

**v1**：按时间线性插值（纯猜测）
**v2**：基于实际工具调用次数

```python
# 不同任务类型的预估工具调用次数
ESTIMATED_TOOL_CALLS = {
    "fund_holdings": 20,    # market_overview + hot_board + news + N个基金detail/rank
    "fund_trade_run": 30,   # 完整 run 流程，工具调用更多
    "fund_review": 15,      # review 流程
    "chat_reply": 2,        # 基本不调工具
}

# 进度 = start_pct + (end_pct - start_pct) * (tool_count / estimated_total)
```

当文本开始大量生成（连续收到 text 事件，无 tool_use）时，说明已进入报告生成阶段，进度跳到 80%+。

### 3.3 progress_msg 的变化

| v1（假进度） | v2（真实进度） |
|-------------|--------------|
| "正在采集市场数据..." | "🔧 python client.py market_overview" |
| "正在分析基金详情..." | "🔧 python client.py 012414 detail" |
| "正在分析持仓配置..." | "🔧 python client.py 519191 rank" |
| "正在生成操作建议..." | "📝 正在生成报告..." |
| "正在整理分析报告..." | "📝 报告生成中（2,345 字）" |

---

## 四、实时推送方案

### 4.1 为什么不用轮询

v1 的 3 秒轮询有两个问题：
1. **延迟**：最差情况下用户要等 3 秒才看到更新
2. **浪费**：任务空闲时也在不断请求

用 **SSE（Server-Sent Events）** 替代：服务端主动推送，App 实时接收。我们已有 SSE 经验（`POST /api/screenshot` 就是 SSE 流式返回）。

### 4.2 架构：内存事件总线 + SSE 端点

```
TaskExecutor (线程)                    SSE 端点 (async)                    App
    │                                      │                               │
    │  解析 stream-json 事件                │                               │
    │  ─── tool_use ──►  event_bus.emit()  │                               │
    │                         │            │                               │
    │                         └──►  queue ──► SSE push ─────────────────► onEvent()
    │                                      │                               │
    │  ─── text ──────►  event_bus.emit()  │                               │
    │                         │            │                               │
    │                         └──►  queue ──► SSE push ─────────────────► onEvent()
    │                                      │     │                         │
    │                                      │     │ 同时写 DB              │
    │                                      │     └──► sa_tasks            │
    │  ─── result ────►  event_bus.emit()  │                               │
    │                         │            │                               │
    │                         └──►  queue ──► SSE push (done) ──────────► 渲染完整报告
```

### 4.3 服务端：事件总线

```python
# services/event_bus.py

import threading
import queue
from collections import defaultdict

class TaskEventBus:
    """线程安全的任务事件总线，支持多个订阅者"""

    def __init__(self):
        self._subscribers: dict[int, list[queue.Queue]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, task_id: int) -> queue.Queue:
        """订阅某个任务的事件流，返回一个 Queue"""
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers[task_id].append(q)
        return q

    def unsubscribe(self, task_id: int, q: queue.Queue):
        """取消订阅"""
        with self._lock:
            subs = self._subscribers.get(task_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subscribers.pop(task_id, None)

    def emit(self, task_id: int, event: dict):
        """发布事件到所有订阅者"""
        with self._lock:
            for q in self._subscribers.get(task_id, []):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass  # 丢弃，避免阻塞

event_bus = TaskEventBus()
```

### 4.4 服务端：SSE 端点

```python
# routers/__init__.py

@router.get("/api/tasks/{task_id}/stream", summary="任务实时事件流", tags=["任务"])
async def stream_task(task_id: int):
    """SSE 端点，实时推送任务执行事件"""
    from starlette.responses import StreamingResponse
    from services.event_bus import event_bus
    import asyncio

    # 先检查任务是否存在
    task = task_db.get_task(task_id)
    if not task:
        return {"status": "error", "message": "任务不存在"}

    # 如果任务已完成/失败，直接返回最终状态
    if task["status"] in ("completed", "failed"):
        async def done_stream():
            event = {
                "type": "done",
                "status": task["status"],
                "result": task.get("result"),
                "error_msg": task.get("error_msg"),
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(done_stream(), media_type="text/event-stream")

    # 订阅事件
    q = event_bus.subscribe(task_id)

    async def event_stream():
        try:
            while True:
                try:
                    # 非阻塞地从 Queue 读取（每 0.3 秒检查一次）
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=0.3)
                    )
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    # 收到终止事件则关闭连接
                    if event.get("type") == "done":
                        break
                except queue.Empty:
                    # 发送心跳保持连接
                    yield f": heartbeat\n\n"
        finally:
            event_bus.unsubscribe(task_id, q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 4.5 TaskExecutor 发布事件

在 `_run_claude_streaming` 中，每解析到一个事件就同时发布到 event_bus：

```python
# 替换原来直接写 DB 的逻辑

# 工具调用事件
event_bus.emit(task_id, {
    "type": "tool_call",
    "tool": tool_name,
    "display": display,
    "progress": estimated_pct,
})

# 文本追加事件
event_bus.emit(task_id, {
    "type": "text_delta",
    "text": text_chunk,         # 增量文本
    "total_len": len(accumulated),
    "progress": current_pct,
})

# 完成事件
event_bus.emit(task_id, {
    "type": "done",
    "status": "completed",
    "result": final_report,      # 完整结果
    "summary": summary,
})

# 失败事件
event_bus.emit(task_id, {
    "type": "done",
    "status": "failed",
    "error_msg": "...",
})
```

同时仍然定期写 DB（作为持久化备份，防止 App 断开后重连能通过 `GET /api/tasks/{id}` 获取中间状态）。

### 4.6 SSE 事件格式汇总

| 事件类型 | 触发时机 | 字段 | App 处理方式 |
|---------|---------|------|-------------|
| `tool_call` | AI 调用工具时 | `tool`, `display`, `progress` | 显示工具调用日志，更新进度条 |
| `text_delta` | AI 输出文本时 | `text`(增量), `total_len`, `progress` | 追加到 Markdown 渲染区域 |
| `progress` | 阶段切换时 | `progress`, `message` | 更新进度条和步骤描述 |
| `done` | 任务完成/失败 | `status`, `result`/`error_msg` | 切换到完成/失败状态 |

### 4.7 App 端 SSE 客户端

```kotlin
// network/TaskStreamClient.kt

class TaskStreamClient(private val baseUrl: String) {

    /**
     * 连接任务事件流，通过回调实时通知 UI
     */
    fun connect(
        taskId: Int,
        onToolCall: (tool: String, display: String, progress: Int) -> Unit,
        onTextDelta: (text: String, totalLen: Int, progress: Int) -> Unit,
        onDone: (status: String, result: String?, errorMsg: String?) -> Unit,
        onError: (Exception) -> Unit,
    ): Job {
        return CoroutineScope(Dispatchers.IO).launch {
            try {
                val url = URL("$baseUrl/api/tasks/$taskId/stream")
                val conn = url.openConnection() as HttpURLConnection
                conn.setRequestProperty("Accept", "text/event-stream")
                conn.connectTimeout = 5000
                conn.readTimeout = 0  // 无超时，持续读取

                val reader = BufferedReader(InputStreamReader(conn.inputStream))
                var line: String?

                while (isActive) {
                    line = reader.readLine() ?: break

                    if (!line.startsWith("data: ")) continue
                    val json = JSONObject(line.removePrefix("data: "))

                    withContext(Dispatchers.Main) {
                        when (json.getString("type")) {
                            "tool_call" -> onToolCall(
                                json.getString("tool"),
                                json.getString("display"),
                                json.getInt("progress")
                            )
                            "text_delta" -> onTextDelta(
                                json.getString("text"),
                                json.getInt("total_len"),
                                json.getInt("progress")
                            )
                            "done" -> {
                                onDone(
                                    json.getString("status"),
                                    json.optString("result", null),
                                    json.optString("error_msg", null)
                                )
                                cancel()  // 关闭连接
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                if (isActive) withContext(Dispatchers.Main) { onError(e) }
            }
        }
    }
}
```

### 4.8 App 端 TaskDetailScreen 改造

```kotlin
// ui/screens/TaskDetailScreen.kt

@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var status by remember { mutableStateOf("loading") }
    var progress by remember { mutableIntStateOf(0) }
    var progressMsg by remember { mutableStateOf("") }
    var toolCalls by remember { mutableStateOf<List<ToolCallItem>>(emptyList()) }
    var partialText by remember { mutableStateOf("") }       // 实时累积的文本
    var finalResult by remember { mutableStateOf<String?>(null) }
    var errorMsg by remember { mutableStateOf<String?>(null) }

    val streamClient = remember { TaskStreamClient(HttpClient.BASE_URL) }

    // 连接 SSE 事件流
    LaunchedEffect(taskId) {
        // 先获取当前状态
        val task = withContext(Dispatchers.IO) { HttpClient.instance?.getTask(taskId) }
        if (task != null) {
            status = task.status
            if (task.isCompleted) { finalResult = task.result; return@LaunchedEffect }
            if (task.isFailed) { errorMsg = task.errorMsg; return@LaunchedEffect }
        }

        // 处理中 → 连接 SSE
        streamClient.connect(
            taskId = taskId,
            onToolCall = { tool, display, pct ->
                toolCalls = toolCalls + ToolCallItem(tool, display)
                progress = pct
                progressMsg = "🔧 $display"
            },
            onTextDelta = { text, totalLen, pct ->
                partialText += text
                progress = pct
                progressMsg = "📝 生成报告中（${totalLen} 字）"
            },
            onDone = { s, result, error ->
                status = s
                finalResult = result
                errorMsg = error
            },
            onError = { e ->
                // SSE 断开，降级为轮询
                // fallbackToPolling(taskId)
            }
        )
    }

    // === UI 渲染 ===
    Scaffold(topBar = { ... }) {
        when {
            status == "completed" && finalResult != null ->
                MarkdownViewer(finalResult!!)

            status == "failed" ->
                ErrorCard(errorMsg)

            status == "processing" -> Column {
                // 进度条 + 步骤
                LinearProgressIndicator(progress / 100f)
                Text(progressMsg)

                if (partialText.isNotEmpty()) {
                    // ★ 实时渲染正在生成的报告
                    MarkdownViewer(partialText)
                } else if (toolCalls.isNotEmpty()) {
                    // 工具调用日志
                    ToolCallLog(toolCalls)
                } else {
                    CircularProgressIndicator()
                }
            }
        }
    }
}
```

### 4.9 断线重连 & 降级策略

| 场景 | 处理方式 |
|------|---------|
| SSE 连接成功 | 实时接收事件，不轮询 |
| SSE 连接失败（服务端不支持） | 降级为 3 秒轮询 `GET /api/tasks/{id}` |
| SSE 中途断开 | 自动重连（指数退避），重连后通过 `GET /api/tasks/{id}` 获取当前完整状态，然后继续 SSE |
| App 切后台再回来 | 重新发起 SSE 连接，先获取一次完整状态作为基线 |
| 任务在 App 打开前就完成了 | SSE 端点直接返回 `done` 事件 |

**关键**：DB 中的 `partial_result` 和 `tool_calls` 仍然定期写入，作为 SSE 断线后的恢复数据源。`GET /api/tasks/{id}` 仍然返回这些字段，用于降级和重连恢复。

---

## 五、不同任务类型的处理差异（更新）

| 任务类型 | Stage 2 | Stage 3 | Stage 4 | 流式输出 | 预计耗时 |
|----------|---------|---------|---------|---------|----------|
| `ocr` | GLM-OCR | - | - | 不需要 | 5-10s |
| `table` | GLM-OCR | - | - | 不需要 | 5-10s |
| `chat_reply` | GLM-OCR | - | claude -p | 可选（短任务） | 15-30s |
| `fund_holdings` | GLM-OCR | claude -p | claude -p streaming | **必须** | 2-5min |
| `fund_trade_run` | - | - | claude -p streaming | **必须** | 5-10min |
| `fund_review` | - | - | claude -p streaming | **必须** | 3-5min |

**规则**：预计耗时 > 1 分钟的任务用 `_run_claude_streaming`，短任务继续用 `_run_claude`。

---

## 六、并发控制 & 错误处理

与 v1 相同：
- `Semaphore(MAX_CONCURRENT=2)` — 最多 2 个并发
- 超时自动 kill 子进程
- 异常写入 error_msg

---

## 七、改动清单

### 服务端

| 文件 | 改动 |
|------|------|
| 新增 `services/event_bus.py` | 线程安全的内存事件总线（subscribe / unsubscribe / emit） |
| `services/task_db.py` | sa_tasks 新增 `partial_result` TEXT 和 `tool_calls` JSONB 字段 |
| `services/task_executor.py` | 新增 `_run_claude_streaming()` 方法，替换 `_run_claude_with_progress()` |
| `services/task_executor.py` | 解析 stream-json 事件时同时 emit 到 event_bus + 定期写 DB |
| `routers/__init__.py` | 新增 `GET /api/tasks/{id}/stream` SSE 端点 |
| `routers/__init__.py` | `GET /api/tasks/{id}` 响应包含 partial_result 和 tool_calls（降级用） |

### App 端

| 文件 | 改动 |
|------|------|
| 新增 `network/TaskStreamClient.kt` | SSE 客户端，连接 `/api/tasks/{id}/stream`，解析事件回调 |
| `data/TaskItem.kt` | 新增 `partialResult` 和 `toolCalls` 字段 |
| `ui/screens/TaskDetailScreen.kt` | 处理中用 SSE 实时接收事件；显示工具调用日志 + 增量渲染报告 |
| `network/HttpClient.kt` | getTask 解析新字段（降级和重连恢复用） |
