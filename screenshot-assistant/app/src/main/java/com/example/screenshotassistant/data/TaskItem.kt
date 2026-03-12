package com.example.screenshotassistant.data

import org.json.JSONArray
import org.json.JSONObject

data class TaskItem(
    val id: Int,
    val taskType: String,
    val skillName: String?,
    val commandId: String?,
    val status: String,
    val progress: Int,
    val progressMsg: String?,
    val title: String?,
    val summary: String?,
    val result: String?,
    val imagePath: String?,
    val errorMsg: String?,
    val partialResult: String?,
    val toolCallDisplays: List<String>,
    val clientId: String?,
    val createdAt: String,
    val startedAt: String?,
    val completedAt: String?,
    val durationSec: Int?
) {
    val typeLabel: String
        get() = TYPE_LABELS[taskType] ?: taskType

    val isProcessing: Boolean get() = status == "processing" || status == "pending"
    val isCompleted: Boolean get() = status == "completed"
    val isFailed: Boolean get() = status == "failed"

    companion object {
        val TYPE_LABELS = mapOf(
            "fund_holdings" to "持仓分析",
            "chat_reply" to "智能回复",
            "ocr" to "文字识别",
            "table" to "表格识别",
            "search" to "搜索内容",
            "full_page" to "完整页面",
            "fund_trade_run" to "每日交易决策",
            "fund_review" to "持仓审视"
        )

        private fun JSONObject.optNullableString(key: String): String? {
            return if (has(key) && !isNull(key)) optString(key) else null
        }

        private fun parseToolCalls(json: JSONObject): List<String> {
            if (!json.has("tool_calls") || json.isNull("tool_calls")) return emptyList()
            return try {
                val arr = json.optJSONArray("tool_calls") ?: return emptyList()
                (0 until arr.length()).mapNotNull { i ->
                    val obj = arr.optJSONObject(i) ?: return@mapNotNull null
                    val display = obj.optString("display").takeIf { it.isNotBlank() } ?: return@mapNotNull null
                    val detail = obj.optString("detail", "")
                    val output = obj.optString("output", "")
                    val isText = obj.optString("tool", "") == "_text"
                    val isError = obj.optBoolean("is_error", false)
                    // 格式：display\n<SEP_DETAIL>detail\n<SEP_OUTPUT>output\n<SEP_IS_TEXT>\n<SEP_IS_ERROR>
                    val parts = mutableListOf(display)
                    if (detail.isNotBlank()) parts.add("<SEP_DETAIL>$detail")
                    if (output.isNotBlank()) parts.add("<SEP_OUTPUT>$output")
                    if (isText) parts.add("<SEP_IS_TEXT>")
                    if (isError) parts.add("<SEP_IS_ERROR>")
                    parts.joinToString("\n")
                }
            } catch (_: Exception) { emptyList() }
        }

        fun fromJson(json: JSONObject): TaskItem {
            return TaskItem(
                id = json.optInt("id"),
                taskType = json.optString("task_type", ""),
                skillName = json.optNullableString("skill_name"),
                commandId = json.optNullableString("command_id"),
                status = json.optString("status", "pending"),
                progress = json.optInt("progress", 0),
                progressMsg = json.optNullableString("progress_msg"),
                title = json.optNullableString("title"),
                summary = json.optNullableString("summary"),
                result = json.optNullableString("result"),
                imagePath = json.optNullableString("image_path"),
                errorMsg = json.optNullableString("error_msg"),
                partialResult = json.optNullableString("partial_result"),
                toolCallDisplays = parseToolCalls(json),
                clientId = json.optNullableString("client_id"),
                createdAt = json.optString("created_at", ""),
                startedAt = json.optNullableString("started_at"),
                completedAt = json.optNullableString("completed_at"),
                durationSec = if (json.has("duration_sec") && !json.isNull("duration_sec"))
                    json.optInt("duration_sec") else null
            )
        }
    }
}
