package com.example.screenshotassistant.capture

enum class CaptureType {
    NORMAL,
    LONG_SCROLL,
    MANUAL_SCROLL
}

data class ActionConfig(
    val id: String,
    val name: String,
    val icon: String, // Material icon name
    val captureType: CaptureType,
    val description: String
)

object Actions {
    val ALL = listOf(
        ActionConfig("ocr", "识别文字", "text_fields", CaptureType.NORMAL, "提取屏幕文字"),
        ActionConfig("chat_reply", "智能回复", "chat", CaptureType.NORMAL, "分析聊天并推荐回复"),
        ActionConfig("table", "表格识别", "table_chart", CaptureType.NORMAL, "识别表格数据"),
        ActionConfig("search", "搜索内容", "search", CaptureType.NORMAL, "识别后搜索"),
        ActionConfig("fund_holdings", "持仓分析", "account_balance", CaptureType.LONG_SCROLL, "自动滚动采集完整持仓"),
        ActionConfig("full_page", "完整页面", "article", CaptureType.LONG_SCROLL, "自动滚动截取完整页面"),
        ActionConfig("manual_scroll", "手动长截", "pan_tool", CaptureType.MANUAL_SCROLL, "手动滑动，自动采集")
    )
}
