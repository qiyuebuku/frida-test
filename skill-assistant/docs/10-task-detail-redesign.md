# 任务详情页重构方案 — 仿 Claude CLI 交互

## 一、现状问题

当前任务详情页存在以下核心问题：

1. **步骤与结果分离**：工具调用步骤和最终结果是两个独立区域，不是统一的对话流
2. **用户消息不在流中**：追问消息没有出现在内容流中，无法形成连贯对话
3. **消息排队不可见**：用户发送追问后看不到排队状态，不知道消息是否被接受
4. **无思考状态指示**：处理中没有类似 `※Coalescing…` 的思考动画
5. **风格不统一**：使用 Material Design 卡片/图标，与 CLI 终端风格不搭

## 二、目标 UI — 仿 Claude CLI

### 2.1 核心设计原则

**一切都是对话流**：用户消息、Claude 文本回复、工具调用、工具输出、思考状态——全部在同一个垂直滚动的流中按时间顺序排列，没有分区、没有卡片。

### 2.2 UI 元素定义（从 Claude CLI 截图提取）

#### 用户消息
```
┌──────────────────────────────────────────┐
│ > 帮我看看今天的新闻消息                    │  ← 深色高亮背景条，全宽
└──────────────────────────────────────────┘
```
- `>` 前缀，白色粗体
- 消息文本，白色
- 背景：比页面背景稍亮的横条（#2A2A2A），全宽贯通
- 上下各有少量 padding

#### Claude 文本回复
```
● 你好！有什么可以帮你的？
```
- `●` 前缀，蓝色
- 文本内容，浅灰色（#D4D4D4）
- 如果是 Markdown 内容（标题、列表、表格等），直接内联渲染，不用 WebView

#### 工具调用
```
● Web Search("今日新闻 2026年3月15日")
  └ Searching: 今日新闻  2026年3月15日
```
- `●` 前缀，蓝色（进行中）/ 绿色（已完成）/ 红色（出错）
- 工具名加粗（如 **Web Search**），参数普通字体
- 下方缩进 `└` 前缀显示输出，灰色文字
- 点击可展开查看完整输出

#### 思考/处理中指示
```
※Coalescing…
```
或
```
· Unfurling… (thinking)
```
- `※` 或 `·` 前缀
- 黄/橙色文字
- 可选地显示耗时统计（如 `32s · ↓ 127 tokens`）

#### 排队消息
```
┌──────────────────────────────────────────┐
│ > 主要关于伊朗的新闻            [排队中]    │  ← 同样的高亮条，右侧显示排队标签
└──────────────────────────────────────────┘
```
- 与普通用户消息样式一致
- 右侧显示灰色 `排队中` 标签
- 消息立即出现在流的最底部，用户可以看到

### 2.3 页面整体布局

```
┌─────────────────────────────────┐
│ ← ● 基金智能交易 - 持仓审视  01:13 │  ← 顶栏：状态点 + 标题 + 时间
├─────────────────────────────────┤
│                                 │
│ > 帮我分析一下当前持仓           │  ← 用户消息（高亮条）
│                                 │
│ ● 开始分析持仓数据               │  ← Claude 文本
│                                 │
│ ● Bash(python client.py snap)   │  ← 工具调用
│   └ 持仓快照: 34只基金, ...      │  ← 工具输出
│                                 │
│ ● Read("review_template.md")    │  ← 工具调用
│   └ # 审视模板...               │  ← 工具输出
│                                 │
│ ● 以下是持仓审视报告：           │  ← Claude 文本回复
│                                 │
│ ## 一、组合概览                  │  ← Markdown 内容内联渲染
│ | 指标 | 数值 |                  │
│ | 持仓数 | 34只 |               │
│ ...                             │
│                                 │
│ > 军工基金需要减仓吗？           │  ← 追问消息（高亮条）
│                                 │
│ ※ thinking…                     │  ← 思考中
│                                 │
│ ● 是的，建议减仓...             │  ← 新一轮回复
│                                 │
├─────────────────────────────────┤
│ ❯ 追问...                   ➤  │  ← 输入栏
└─────────────────────────────────┘
```

## 三、数据模型重构

### 3.1 当前数据模型的问题

当前有两套独立数据：
- `toolCalls: List<ToolCallStep>` — 工具调用步骤
- `result: String` — 最终结果（Markdown）

这两套数据无法表达 **交错的对话流**：用户消息、Claude文本、工具调用应该按时间顺序穿插排列。

### 3.2 新数据模型：统一事件流

引入 `StreamEvent` 统一所有类型的内容：

```kotlin
sealed class StreamEvent {
    data class UserMessage(
        val content: String,
        val isQueued: Boolean = false,   // 是否排队中
        val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    data class AssistantText(
        val content: String,             // Markdown 文本
        val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    data class ToolCall(
        val tool: String,                // 工具名：Bash, Read, Web Search 等
        val display: String,             // 显示文本：Bash(python client.py snap)
        val detail: String = "",         // 详细参数
        val output: String = "",         // └ 输出
        val isRunning: Boolean = false,
        val isError: Boolean = false,
        val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    data class Thinking(
        val label: String = "thinking",  // Coalescing / Unfurling 等
        val elapsed: String? = null,     // 耗时统计
        val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()
}
```

### 3.3 事件流构建逻辑

事件流从以下来源合并构建：

1. **SSE `tool_call` 事件** → `StreamEvent.ToolCall`（isRunning=true）
2. **SSE `tool_result` 事件** → 更新对应 `ToolCall` 的 output，isRunning=false
3. **SSE `text_delta` 事件** → 追加到当前 `StreamEvent.AssistantText`
4. **SSE `status_change(processing)` 事件** → 添加 `StreamEvent.Thinking`
5. **用户发送追问** → 添加 `StreamEvent.UserMessage`
6. **SSE `message_queued` 事件** → 标记最后一个 UserMessage 为 isQueued
7. **SSE `done` 事件** → 移除 Thinking，确保最后的文本已完整
8. **从 DB 加载历史** → 从 `messages[]` + `tool_calls[]` 重建事件流

### 3.4 从 DB 数据重建事件流

任务详情从 `/api/tasks/{id}` 加载时，需要将 DB 数据转换为事件流：

```
messages = [
    {role: "user", content: "帮我分析持仓"},
    {role: "assistant", content: "## 持仓审视报告\n..."}
]
tool_calls = [
    {tool: "Bash", display: "Bash(python client.py snap)", output: "持仓: 34只"},
    {tool: "Read", display: "Read(template.md)", output: "# 模板..."},
    {tool: "_text", display: "开始分析持仓数据", output: ""},
]
```

转换为：
```
[
    UserMessage("帮我分析持仓"),
    AssistantText("开始分析持仓数据"),           // 从 tool_calls 中 _text 类型
    ToolCall(Bash, "Bash(python client.py snap)", output="持仓: 34只"),
    ToolCall(Read, "Read(template.md)", output="# 模板..."),
    AssistantText("## 持仓审视报告\n..."),       // 最终 assistant message
]
```

注意：tool_calls 中 `tool: "_text"` 类型的条目实际是 Claude 的中间文本输出，应转为 `AssistantText`。

## 四、前端交互流程

### 4.1 首次加载任务详情

```
1. GET /api/tasks/{id} → 获取任务数据
2. 从 messages[] + tool_calls[] 重建 StreamEvent 列表
3. 如果 status == "processing"：
   a. 连接 SSE /api/tasks/{id}/stream
   b. SSE 回放已有 tool_calls（与重建的事件去重）
   c. 实时追加新事件
4. 渲染事件流
```

### 4.2 发送追问消息

```
1. 用户在输入栏输入文字，点击发送
2. POST /api/tasks/{id}/message → 发送给后端
3. 如果后端返回 {queued: true}：
   a. 消息进入 queuedMessages 列表（独立于主事件流）
   b. 渲染时排队消息始终显示在所有事件的最底部
   c. 新的 tool_call/text 事件插入到排队消息之前
4. 如果后端返回 {queued: false}：
   a. 消息直接追加到主事件流末尾（作为正常 UserMessage）
   b. 任务状态变为 processing
   c. 连接 SSE 接收新的处理事件
```

### 4.3 排队消息的位置规则（关键）

**核心原则**：排队消息始终固定在事件流最底部，直到被服务端实际处理。

```
正确行为：                          错误行为：
┌─────────────────────┐           ┌─────────────────────┐
│ > 帮我分析持仓       │           │ > 帮我分析持仓       │
│ ● Bash(snapshot)     │           │ ● Bash(snapshot)     │
│ ● Bash(evaluate)     │           │ > 军工要减仓吗 排队中 │ ← 卡在中间！
│ ● Read(template)     │ ← 新步骤 │ ● Read(template)     │
│ ● Bash(detail 008087)│   继续   │ ● Bash(detail 008087)│
│ ● Bash(detail 022365)│   追加   │ ● Bash(detail 022365)│
│                      │          │                      │
│ > 军工要减仓吗 排队中 │ ← 始终  │                      │
│                      │   在最后 │                      │
└─────────────────────┘           └─────────────────────┘
```

**实现方式**：前端维护两个独立状态：
- `events: List<StreamEvent>` — 主事件流（正在处理的对话内容）
- `queuedMessages: List<String>` — 排队中的用户消息

渲染时先渲染 `events`，再渲染 `queuedMessages`。

**状态转换**：
```
用户发送消息（任务处理中）
  → 后端返回 queued=true
  → 消息加入 queuedMessages

当前轮处理完成（SSE done 事件）
  → 后端自动开始处理排队消息
  → SSE 收到 status_change(processing)
  → 将 queuedMessages 中第一条移入 events 作为 UserMessage
  → 新的 tool_call/text 事件正常追加到 events
```

### 4.4 排队消息被处理

```
1. SSE 收到 status_change(processing) → 添加 Thinking 事件
2. 后端开始处理排队消息
3. SSE 收到 tool_call / text_delta → 追加新事件
4. SSE 收到 done → 处理完成
5. 如果还有更多排队消息，重复 1-4
```

### 4.4 处理中发送消息（消息排队）

```
1. 任务 status == "processing" 时，输入栏仍可用
2. 用户发送消息 → POST /api/tasks/{id}/message
3. 后端返回 {queued: true}
4. 消息立即显示在流底部，标签「排队中」
5. Claude 处理完当前工作后自动处理排队消息
```

## 五、SSE 事件与 UI 映射

### 5.1 SSE 事件类型（服务端已支持）

| SSE 事件 | 对应 UI 操作 |
|---------|------------|
| `tool_call` (tool=_text) | 追加 AssistantText |
| `tool_call` (tool=_step) | 追加 AssistantText（步骤说明） |
| `tool_call` (其他) | 追加 ToolCall（isRunning=true） |
| `tool_result` | 更新对应 ToolCall 的 output，isRunning=false |
| `text_delta` | 追加到当前 AssistantText |
| `status_change(processing)` | 添加 Thinking |
| `message_queued` | 标记 UserMessage.isQueued |
| `done` | 移除 Thinking，标记完成 |

### 5.2 需要后端新增/调整的事件

当前后端没有的，需要新增：

1. **`thinking` 事件**：当 Claude 开始思考时发出
   ```json
   {"type": "thinking", "label": "thinking"}
   ```
   目前后端在 `_run_claude_streaming` 中检测到 TUI 噪音中的 thinking 行时可发出此事件。

2. **追问开始处理事件**：当排队消息开始被处理时
   ```json
   {"type": "followup_start", "message": "主要关于伊朗的新闻"}
   ```
   让前端知道哪条排队消息开始被处理（可选，但体验更好）。

## 六、Compose 组件设计

### 6.1 组件树

```
TaskDetailScreen
├── CliTopBar                    // 顶栏：← ● 标题  时间
├── EventStreamColumn            // 事件流主体（LazyColumn 或 Column+verticalScroll）
│   ├── UserMessageItem          // > 用户消息  [排队中]
│   ├── AssistantTextItem        // ● 文本内容（Markdown 内联渲染）
│   ├── ToolCallItem             // ● ToolName(args)  └ output
│   └── ThinkingItem             // ※ thinking…
└── CliInputBar                  // ❯ 输入框  ➤
```

### 6.2 各组件样式规格

#### UserMessageItem
```kotlin
// 全宽高亮条
Box(
    modifier = Modifier
        .fillMaxWidth()
        .background(Color(0xFF2A2A2A))  // 比背景(#1A1A1A)稍亮
        .padding(horizontal = 12.dp, vertical = 10.dp)
) {
    Row {
        Text(">", color = white, fontWeight = Bold, fontFamily = Monospace)
        Spacer(width = 8.dp)
        Text(content, color = white)
        if (isQueued) {
            Spacer(weight = 1f)
            Text("排队中", color = dimGray, fontSize = 11.sp)
        }
    }
}
```

#### AssistantTextItem
```kotlin
// ● 前缀 + Markdown 内联渲染
Row(modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) {
    Text("●", color = CliBlue, fontFamily = Monospace)
    Spacer(width = 8.dp)
    // 短文本直接 Text，长 Markdown 用 MarkdownViewer
    if (isShortText) {
        Text(content, color = CliText)
    } else {
        MarkdownViewer(content)
    }
}
```

#### ToolCallItem
```kotlin
// ● ToolName(args)
Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
    Row {
        Text("●", color = if (isRunning) CliBlue else CliGreen)
        Spacer(width = 8.dp)
        Text(toolName, fontWeight = Bold, color = CliText)  // 加粗工具名
        Text("($args)", color = CliDimText)                // 普通参数
    }
    // └ 输出
    if (output.isNotBlank()) {
        Row(modifier = Modifier.padding(start = 20.dp, top = 2.dp)) {
            Text("└", color = CliDimText, fontFamily = Monospace)
            Spacer(width = 4.dp)
            Text(output, color = CliDimText, maxLines = 3)
        }
    }
}
```

#### ThinkingItem
```kotlin
// ※ thinking…  带脉冲动画
Row(modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) {
    Text("※", color = CliYellow, fontFamily = Monospace)
    Text("thinking…", color = CliYellow, fontFamily = Monospace)
}
```

#### CliInputBar
```kotlin
// 底部输入栏
Surface(color = CliSurfaceDim) {
    Row(padding = 8.dp) {
        Text("❯", color = CliBlue, fontWeight = Bold, fontFamily = Monospace)
        OutlinedTextField(
            placeholder = "追问...",
            // 深色主题配色
        )
        IconButton(send)
    }
}
```

## 七、后端架构调整 — 停止清洗，保留完整数据

### 7.1 现状分析：数据经过了两层处理

当前服务端的数据流：

```
Claude CLI (tmux) → 原始 TUI 输出
  ↓
_run_claude_streaming() → 解析为两套数据：
  ├─ tool_calls[] ← 结构化工具调用（tool, display, detail, output, is_text）
  └─ raw_result   ← 所有文本行拼接
  ↓
_clean_result(raw_result) → 清洗后的 result（去除 ●、⎿、Traceback 等）
  ↓
清理 tool_calls[] → 删掉与 result 不一致的 _text 步骤
  ↓
存入 DB: result + tool_calls + messages
```

**问题**：`_clean_result` 做了大量裁剪（去除 `●` 前缀行、工具名、traceback、`⎿` 输出、
报告前的所有内容），但新 UI 恰好需要这些"噪音"来还原完整的对话流。

### 7.2 结论：清洗应该在前端做，不在后端

**核心原则**：后端只负责**结构化**（把 tmux 原始输出解析为结构化 JSON），不负责**裁剪**。

| 后端应该做的 | 后端不应该做的 |
|------------|-------------|
| 解析 `● Bash(cmd)` → `{tool:"Bash", detail:"cmd"}` | 删除 `●` 前缀行 |
| 解析 `⎿ output` → `{output: "output"}` | 删除 `⎿` 输出行 |
| 检测 Traceback → `{is_error: true}` | 从 result 中移除 Traceback |
| 提取 Claude 中间文本 → `{tool:"_text"}` | 从 result 中截断报告前内容 |

### 7.3 具体改动

#### 改动 1：`_clean_result` 简化为只去 TUI 渲染噪音

当前 `_clean_result` 过度裁剪。改为只去真正的 TUI 渲染噪音（ANSI 码、spinner 字符、
空边框线），保留所有有意义的文本内容。

但实际上，由于新 UI 从 `tool_calls[]` + `messages[]` 重建事件流，`result` 字段仅用于：
1. 任务列表页的预览摘要（summary 字段）
2. 复制全部内容功能

所以 `_clean_result` 仍然有用——它提取的是 **Claude 最终答复的纯文本**，用于摘要和复制。

**结论：`_clean_result` 保留，但不影响 `tool_calls`。**

#### 改动 2：停止清理 tool_calls

当前代码（1055-1071 行）在 `_clean_result` 之后会反过来清理 `tool_calls`：
- 删除 `_text` 步骤中不在 cleaned result 中的条目
- 删除末尾与 result 重复的 `_text` 步骤

**这些清理必须移除**。`tool_calls` 应完整保留，前端需要所有步骤来还原完整流。

```python
# 删除这段逻辑（1055-1071行）：
# if result != raw_result:
#     cleaned_tool_calls = [...]
#     tool_calls = cleaned_tool_calls
# else:
#     while (tool_calls and tool_calls[-1].get("tool") == "_text" ...):
#         tool_calls.pop()
```

#### 改动 3：tool_calls 中的 `_text` 条目保留完整内容

当前 `_flush_text_step` 把 Claude 的中间文本存为 `_text` 类型的 tool_call，display 是
截取的短标题（≤30字符），detail 是完整文本。这个结构已经够用，无需改动。

前端重建事件流时：
- `tool == "_text"` → `StreamEvent.AssistantText(content = detail)`
- `tool == "_step"` → `StreamEvent.AssistantText(content = display)`
- 其他 → `StreamEvent.ToolCall(tool, display, detail, output)`

#### 改动 4：tool_call 的 output 字段

当前 `_parse_tmux_tool_line` 只解析 `● ToolName(args)` 行，工具输出（`⎿` 开头的行）
没有被结构化关联到对应的 tool_call。

**需要增强**：在 `_run_claude_streaming` 的轮询循环中，检测 `⎿` 前缀行并更新最近一个
tool_call 的 output 字段，同时发出 `tool_result` SSE 事件。

```python
# 在 new_lines 循环中增加：
elif stripped.startswith('⎿'):
    output_text = stripped.lstrip('⎿').strip()
    if tool_calls:
        tool_calls[-1]["output"] = (tool_calls[-1].get("output") or "") + output_text + "\n"
        event_bus.emit(task_id, {
            "type": "tool_result",
            "display": tool_calls[-1]["display"],
            "output": output_text,
        })
```

### 7.4 消息转发机制重构 — 直接发给 Claude CLI

#### 问题

当前 `queue_message()` 在任务处理中时把消息存到 Python 内存 `_pending_messages`，
等 `_run_claude_streaming` 整个轮询循环结束后，`_run_followup` 才从队列取出消息
再启动新一轮 `_run_claude_streaming`。

```
当前流程（❌ 与 Claude CLI 行为不一致）：

用户消息 → queue_message() → _pending_messages（Python 内存）
                                    ↓
                        等待 _run_claude_streaming 结束
                                    ↓
                        _run_followup 取出消息，发送新 prompt
```

但 Claude CLI 本身就支持在处理中输入消息——消息直接进入 stdin，Claude 完成当前
工具调用后自动处理。由于后端已经通过 tmux 控制 Claude CLI，我们只需要把用户消息
直接转发给 tmux session 即可。

#### 正确流程

```
新流程（✅ 与 Claude CLI 行为一致）：

用户消息 → queue_message() → 直接 session.send(message) 到 tmux
                                    ↓
                        Claude CLI 收到消息，排入内部队列
                                    ↓
                        Claude 完成当前工作后，自动处理新消息
                                    ↓
                        _run_claude_streaming 轮询循环捕获所有输出
```

#### 实现要点

**1. `queue_message()` 修改**

```python
def queue_message(self, task_id: int, message: str) -> dict:
    task = task_db.get_task(task_id)
    if task["status"] in ("processing", "pending"):
        # 直接转发给 tmux session
        session = self._tmux_sessions.get(task_id)
        if session and session.ready:
            session.send(message)
            # 记录消息到 DB
            messages = task.get("messages") or []
            messages.append({"role": "user", "content": message,
                             "created_at": datetime.now().isoformat()})
            task_db.update_task(task_id, messages=messages)
        else:
            # session 不存在（不应该发生），回退到旧逻辑
            self._pending_messages.setdefault(task_id, []).append(message)
        event_bus.emit(task_id, {"type": "message_queued", "content": message})
        return {"queued": True}
    else:
        self.submit_followup(task_id, message)
        return {"queued": False}
```

**2. `_run_claude_streaming` 轮询循环的结束检测**

关键问题：Claude 完成当前工作后出现 `❯` 提示符，我们的消息刚发过去，Claude 开始
处理新消息。但轮询循环可能已经检测到 `❯` 而 break 了。

解决方案：在 `session.send()` 时设置一个标志，轮询循环检测到此标志时重置 `idle_count`：

```python
# _run_claude_streaming 轮询循环中：
while time.time() - start < timeout:
    time.sleep(1)

    # 检查是否有新消息被转发（重置 idle 检测）
    if session.has_pending_input:
        idle_count = 0
        session.has_pending_input = False

    # ... 其余检测逻辑 ...

    if idle_count >= 2 and session.is_at_prompt():
        # 再次检查是否有新消息
        if not session.has_pending_input:
            break
```

`session.send()` 设置 `has_pending_input = True`，轮询循环看到后重置计数器，
确保不会在新消息被发送后立即 break。

**3. 不再需要 `_pending_messages` 和 `_run_followup` 的消息循环**

消息直接发给 tmux，Claude CLI 自己处理排队，不需要 Python 层面维护消息队列。
`_run_followup` 仅在任务已完成（无活跃 tmux session）时才需要启动新 session。

#### 前端配合

前端不需要改动。`queue_message` 仍然返回 `{queued: true}`，SSE 仍然发出
`message_queued` 事件。唯一区别是消息会更快被 Claude 处理（不需要等整个任务完成）。

### 7.5 API 接口无需改版

现有 API 端点完全够用：

| 端点 | 用途 | 是否需要改动 |
|------|------|------------|
| `GET /api/tasks/{id}` | 获取完整任务数据 | 否（已返回 tool_calls + messages） |
| `GET /api/tasks/{id}/stream` | SSE 实时事件 | 否（已支持 tool_call/tool_result/text_delta/done） |
| `POST /api/tasks/{id}/message` | 发送追问 | 否（已返回 queued: true/false） |
| `GET /api/tasks` | 任务列表 | 否（不含 tool_calls，性能优化合理） |

### 7.5 前端事件去重

SSE 连接时会回放已有 tool_calls（解决连接前步骤丢失），前端需要对从 DB 已加载的事件
和 SSE 回放事件进行去重（按 display+detail 匹配）。

## 八、实施步骤

### Phase 1：数据模型重构（前端）

1. 定义 `StreamEvent` sealed class
2. 实现 `buildEventsFromTask(task: TaskItem): List<StreamEvent>` — 从 DB 数据重建事件流
3. 实现 SSE 事件到 `StreamEvent` 的映射逻辑

### Phase 2：UI 组件开发（前端）

1. 实现 `UserMessageItem` — 高亮条样式
2. 实现 `AssistantTextItem` — ● 前缀 + Markdown 渲染
3. 实现 `ToolCallItem` — ● 工具名 + └ 输出
4. 实现 `ThinkingItem` — ※ 动画
5. 实现 `EventStreamColumn` — 统一渲染事件流

### Phase 3：交互逻辑（前端）

1. 追问发送 → 立即追加 UserMessage 到事件流
2. 排队标记 → queued 返回时标记消息
3. SSE 重连 → 事件去重
4. 自动滚动到底部

### Phase 4：后端微调（可选）

1. 添加 `thinking` SSE 事件
2. 添加 `followup_start` SSE 事件
3. 确保 tool_calls 按时序存储

## 九、配色规格

| 元素 | 颜色 | 十六进制 |
|-----|------|---------|
| 页面背景 | 深黑 | #1A1A1A |
| 用户消息背景 | 稍亮灰 | #2A2A2A |
| 输入栏背景 | 暗灰 | #232323 |
| 普通文本 | 浅灰 | #D4D4D4 |
| 暗淡文本 | 中灰 | #8B8B8B |
| 运行中/链接 | 蓝 | #5B9CF5 |
| 完成/成功 | 绿 | #4EC86C |
| 错误 | 红 | #E05252 |
| 思考/警告 | 黄 | #D4A54E |
| `>` 前缀 | 白 | #FFFFFF |
| `●` 前缀 | 与状态对应 | — |
| `└` 前缀 | 暗淡灰 | #666666 |
| `※` 前缀 | 黄 | #D4A54E |
| `❯` 提示符 | 蓝 | #5B9CF5 |
