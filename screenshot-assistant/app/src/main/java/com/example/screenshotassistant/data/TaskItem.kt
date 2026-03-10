package com.example.screenshotassistant.data

import org.json.JSONObject

data class TaskItem(
    val id: Int,
    val taskType: String,
    val status: String,
    val progress: Int,
    val progressMsg: String?,
    val title: String?,
    val summary: String?,
    val result: String?,
    val imagePath: String?,
    val errorMsg: String?,
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

        fun fromJson(json: JSONObject): TaskItem {
            return TaskItem(
                id = json.optInt("id"),
                taskType = json.optString("task_type", ""),
                status = json.optString("status", "pending"),
                progress = json.optInt("progress", 0),
                progressMsg = json.optNullableString("progress_msg"),
                title = json.optNullableString("title"),
                summary = json.optNullableString("summary"),
                result = json.optNullableString("result"),
                imagePath = json.optNullableString("image_path"),
                errorMsg = json.optNullableString("error_msg"),
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
