package com.example.screenshotassistant.data

/**
 * 统一事件流模型 — 所有对话内容（用户消息、Claude文本、工具调用、思考状态）
 * 按时间顺序排列在同一个列表中，前端按顺序渲染即可还原 Claude CLI 风格的交互界面。
 */
sealed class StreamEvent {
    abstract val timestamp: Long

    /** 用户消息：> xxx */
    data class UserMessage(
        val content: String,
        val isQueued: Boolean = false,
        override val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    /** Claude 文本回复：● xxx */
    data class AssistantText(
        val content: String,
        override val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    /** 工具调用：● ToolName(args) + └ output */
    data class ToolCall(
        val tool: String,
        val display: String,
        val detail: String = "",
        val output: String = "",
        val isRunning: Boolean = false,
        val isError: Boolean = false,
        override val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()

    /** 思考中：※ thinking… */
    data class Thinking(
        val label: String = "thinking",
        override val timestamp: Long = System.currentTimeMillis()
    ) : StreamEvent()
}

/**
 * 从任务数据重建事件流
 *
 * 数据来源：
 * - messages[]: 用户和助手的对话轮次
 * - toolCallDisplays[]: 工具调用步骤（已编码为 SEP 格式字符串）
 *
 * 重建逻辑：
 * 1. 第一条 user message → UserMessage
 * 2. tool_calls 按顺序 → ToolCall 或 AssistantText（_text 类型）
 * 3. 最后一条 assistant message → AssistantText（最终结果）
 * 4. 后续的 user/assistant 消息对 → 追问轮次
 */
fun buildEventsFromTask(task: TaskItem): List<StreamEvent> {
    val events = mutableListOf<StreamEvent>()
    val messages = task.messages
    val toolDisplays = task.toolCallDisplays

    if (messages.isEmpty() && toolDisplays.isEmpty() && task.result.isNullOrBlank()) {
        return events
    }

    // 解析 tool_calls（从 SEP 格式字符串）
    val parsedTools = toolDisplays.map { raw ->
        var display = raw
        var detail = ""
        var output = ""
        var isText = false
        var isError = false
        val lines = raw.split("\n")
        val displayLines = mutableListOf<String>()
        for (line in lines) {
            when {
                line.startsWith("<SEP_DETAIL>") -> detail = line.removePrefix("<SEP_DETAIL>")
                line.startsWith("<SEP_OUTPUT>") -> output = line.removePrefix("<SEP_OUTPUT>")
                line == "<SEP_IS_TEXT>" -> isText = true
                line == "<SEP_IS_ERROR>" -> isError = true
                else -> displayLines.add(line)
            }
        }
        display = displayLines.firstOrNull() ?: raw
        if (detail.isEmpty() && displayLines.size > 1) {
            detail = displayLines.drop(1).joinToString("\n")
        }
        ParsedTool(display, detail, output, isText, isError)
    }

    if (messages.size <= 2) {
        // 简单场景：初始问答（0-2 条消息）
        // 第一条 user message
        val firstUser = messages.firstOrNull { it.role == "user" }
        if (firstUser != null) {
            events.add(StreamEvent.UserMessage(firstUser.content))
        }

        // 工具调用步骤
        for (tool in parsedTools) {
            if (tool.isText) {
                events.add(StreamEvent.AssistantText(tool.detail.ifBlank { tool.display }))
            } else {
                events.add(StreamEvent.ToolCall(
                    tool = "", display = tool.display, detail = tool.detail,
                    output = tool.output, isError = tool.isError
                ))
            }
        }

        // 最终结果（如果不是已经作为 _text 步骤显示的话）
        if (!task.result.isNullOrBlank()) {
            events.add(StreamEvent.AssistantText(task.result))
        }
    } else {
        // 多轮对话：messages 中有追问
        for (msg in messages) {
            if (msg.role == "user") {
                events.add(StreamEvent.UserMessage(msg.content))
            } else if (msg.role == "assistant" && msg.content.isNotBlank()) {
                events.add(StreamEvent.AssistantText(msg.content))
            }
        }

        // 工具调用步骤插在最后一轮对话之前（近似还原顺序）
        // 注意：多轮时 tool_calls 只包含最近一轮的步骤
        if (parsedTools.isNotEmpty()) {
            // 找到最后一条 assistant 消息的位置，在它之前插入工具步骤
            val lastAssistantIdx = events.indexOfLast { it is StreamEvent.AssistantText }
            if (lastAssistantIdx > 0) {
                val toolEvents = parsedTools.map { tool ->
                    if (tool.isText) {
                        StreamEvent.AssistantText(tool.detail.ifBlank { tool.display })
                    } else {
                        StreamEvent.ToolCall(
                            tool = "", display = tool.display, detail = tool.detail,
                            output = tool.output, isError = tool.isError
                        )
                    }
                }
                events.addAll(lastAssistantIdx, toolEvents)
            }
        }
    }

    return events
}

private data class ParsedTool(
    val display: String,
    val detail: String,
    val output: String,
    val isText: Boolean,
    val isError: Boolean
)
