package com.example.screenshotassistant.data

import org.json.JSONObject

data class StepItem(
    val type: String,          // "text" | "tool_use"
    val tool: String?,         // tool name (for tool_use)
    val title: String?,        // display title (for tool_use)
    val content: String?,      // text content (for text)
    val input: JSONObject?,    // tool input params
    val output: String?,       // tool output
    val isError: Boolean,
    val timestamp: String?
) {
    val isText: Boolean get() = type == "text"
    val isToolUse: Boolean get() = type == "tool_use"

    /** 工具图标 hint */
    val iconName: String get() = when (tool) {
        "Read" -> "description"
        "Write", "Edit" -> "edit_note"
        "Bash" -> "terminal"
        "Glob", "Grep" -> "search"
        "WebFetch", "WebSearch" -> "language"
        "Agent" -> "account_tree"
        else -> "build"
    }

    companion object {
        fun fromJson(json: JSONObject): StepItem {
            return StepItem(
                type = json.optString("type", "text"),
                tool = json.optString("tool", "").ifBlank { null },
                title = json.optString("title", "").ifBlank { null },
                content = json.optString("content", "").ifBlank { null },
                input = if (json.has("input") && !json.isNull("input")) json.optJSONObject("input") else null,
                output = json.optString("output", "").ifBlank { null },
                isError = json.optBoolean("is_error", false),
                timestamp = json.optString("timestamp", "").ifBlank { null }
            )
        }
    }
}
