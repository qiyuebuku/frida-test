package com.example.screenshotassistant.capture

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

enum class CaptureType {
    NORMAL,
    LONG_SCROLL,
    MANUAL_SCROLL
}

data class ActionConfig(
    val id: String,
    val name: String,
    val icon: String,
    val captureType: CaptureType,
    val description: String,
    val isBuiltin: Boolean = true,
    val enabled: Boolean = true,
    val sortOrder: Int = 0,
    val systemPrompt: String? = null,
    val rules: String? = null,
    val processingMode: String = "auto",  // auto / sync_sse / async_task
    val timeoutSec: Int = 120
)

val BUILTIN_ACTIONS = listOf(
    ActionConfig("ocr", "识别文字", "text_fields", CaptureType.NORMAL, "提取屏幕文字",
        sortOrder = 0, processingMode = "sync_sse", timeoutSec = 30),
    ActionConfig("chat_reply", "智能回复", "chat", CaptureType.LONG_SCROLL, "分析聊天并推荐回复",
        sortOrder = 1, processingMode = "sync_sse", timeoutSec = 60),
    ActionConfig("table", "表格识别", "table_chart", CaptureType.NORMAL, "识别表格数据",
        sortOrder = 2, processingMode = "sync_sse", timeoutSec = 30),
    ActionConfig("search", "搜索内容", "search", CaptureType.NORMAL, "识别后搜索",
        sortOrder = 3, processingMode = "sync_sse", timeoutSec = 30),
    ActionConfig("fund_holdings", "持仓分析", "account_balance", CaptureType.LONG_SCROLL, "自动滚动采集完整持仓",
        sortOrder = 4, processingMode = "async_task", timeoutSec = 600),
    ActionConfig("full_page", "完整页面", "article", CaptureType.LONG_SCROLL, "自动滚动截取完整页面",
        sortOrder = 5, processingMode = "async_task", timeoutSec = 120),
    ActionConfig("manual_scroll", "手动长截", "pan_tool", CaptureType.MANUAL_SCROLL, "手动滑动，自动采集",
        sortOrder = 6, processingMode = "async_task", timeoutSec = 120)
)

/**
 * ActionConfig 持久化存储（SharedPreferences）
 */
object ActionConfigStore {

    private const val PREFS_NAME = "action_configs"
    private const val KEY_OVERRIDES = "overrides"      // 内置操作的覆盖配置
    private const val KEY_CUSTOM = "custom_actions"     // 用户自定义操作
    private const val KEY_VISIBLE_APPS = "visible_apps" // 白名单：悬浮球只在这些 App 中显示
    private const val KEY_HIDE_IN_SELF = "hide_in_self" // 进入自身 App 时隐藏
    private const val KEY_SHOW_ON_LAUNCHER = "show_on_launcher" // 在桌面显示悬浮球

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /**
     * 获取所有可用操作（内置 + 自定义，合并用户覆盖配置）
     */
    fun getAll(context: Context): List<ActionConfig> {
        val overrides = loadOverrides(context)
        val custom = loadCustomActions(context)
        val merged = BUILTIN_ACTIONS.map { builtin ->
            overrides[builtin.id]?.let { mergeOverride(builtin, it) } ?: builtin
        }
        return (merged + custom).sortedBy { it.sortOrder }
    }

    /**
     * 获取启用的操作（用于悬浮窗菜单）
     */
    fun getEnabled(context: Context): List<ActionConfig> =
        getAll(context).filter { it.enabled }

    /**
     * 获取指定操作的配置
     */
    fun get(context: Context, actionId: String): ActionConfig? =
        getAll(context).find { it.id == actionId }

    /**
     * 保存操作配置覆盖
     */
    fun saveOverride(context: Context, action: ActionConfig) {
        val overrides = loadOverrides(context).toMutableMap()
        overrides[action.id] = actionToJson(action)
        prefs(context).edit()
            .putString(KEY_OVERRIDES, JSONObject(overrides.mapValues { it.value.toString() }).toString())
            .apply()
    }

    /**
     * 保存自定义操作
     */
    fun saveCustomAction(context: Context, action: ActionConfig) {
        val customs = loadCustomActions(context).toMutableList()
        val idx = customs.indexOfFirst { it.id == action.id }
        if (idx >= 0) customs[idx] = action else customs.add(action)
        val arr = JSONArray()
        customs.forEach { arr.put(actionToJson(it)) }
        prefs(context).edit().putString(KEY_CUSTOM, arr.toString()).apply()
    }

    /**
     * 删除自定义操作
     */
    fun deleteCustomAction(context: Context, actionId: String) {
        val customs = loadCustomActions(context).filter { it.id != actionId }
        val arr = JSONArray()
        customs.forEach { arr.put(actionToJson(it)) }
        prefs(context).edit().putString(KEY_CUSTOM, arr.toString()).apply()
    }

    /**
     * 重置内置操作为默认值
     */
    fun resetOverride(context: Context, actionId: String) {
        val overrides = loadOverrides(context).toMutableMap()
        overrides.remove(actionId)
        prefs(context).edit()
            .putString(KEY_OVERRIDES, JSONObject(overrides.mapValues { it.value.toString() }).toString())
            .apply()
    }

    /**
     * 批量保存排序和启用状态
     */
    fun saveOrder(context: Context, actions: List<ActionConfig>) {
        val overrides = loadOverrides(context).toMutableMap()
        val customs = loadCustomActions(context).toMutableList()

        actions.forEachIndexed { index, action ->
            if (action.isBuiltin) {
                val existing = overrides[action.id] ?: JSONObject()
                existing.put("enabled", action.enabled)
                existing.put("sortOrder", index)
                overrides[action.id] = existing
            } else {
                val idx = customs.indexOfFirst { it.id == action.id }
                if (idx >= 0) {
                    customs[idx] = action.copy(sortOrder = index, enabled = action.enabled)
                }
            }
        }

        val editor = prefs(context).edit()
        editor.putString(KEY_OVERRIDES, JSONObject(overrides.mapValues { it.value.toString() }).toString())
        val arr = JSONArray()
        customs.forEach { arr.put(actionToJson(it)) }
        editor.putString(KEY_CUSTOM, arr.toString())
        editor.apply()
    }

    private fun loadOverrides(context: Context): Map<String, JSONObject> {
        val json = prefs(context).getString(KEY_OVERRIDES, null) ?: return emptyMap()
        return try {
            val obj = JSONObject(json)
            obj.keys().asSequence().associateWith { JSONObject(obj.getString(it)) }
        } catch (_: Exception) { emptyMap() }
    }

    private fun loadCustomActions(context: Context): List<ActionConfig> {
        val json = prefs(context).getString(KEY_CUSTOM, null) ?: return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { jsonToAction(arr.getJSONObject(it)) }
        } catch (_: Exception) { emptyList() }
    }

    private fun mergeOverride(builtin: ActionConfig, override: JSONObject): ActionConfig {
        return builtin.copy(
            enabled = override.optBoolean("enabled", builtin.enabled),
            sortOrder = override.optInt("sortOrder", builtin.sortOrder),
            systemPrompt = override.optNullStr("systemPrompt") ?: builtin.systemPrompt,
            rules = override.optNullStr("rules") ?: builtin.rules,
            processingMode = override.optString("processingMode", builtin.processingMode),
            timeoutSec = override.optInt("timeoutSec", builtin.timeoutSec)
        )
    }

    private fun actionToJson(action: ActionConfig): JSONObject {
        return JSONObject().apply {
            put("id", action.id)
            put("name", action.name)
            put("icon", action.icon)
            put("captureType", action.captureType.name)
            put("description", action.description)
            put("isBuiltin", action.isBuiltin)
            put("enabled", action.enabled)
            put("sortOrder", action.sortOrder)
            put("systemPrompt", action.systemPrompt ?: JSONObject.NULL)
            put("rules", action.rules ?: JSONObject.NULL)
            put("processingMode", action.processingMode)
            put("timeoutSec", action.timeoutSec)
        }
    }

    private fun jsonToAction(json: JSONObject): ActionConfig {
        return ActionConfig(
            id = json.getString("id"),
            name = json.getString("name"),
            icon = json.optString("icon", "apps"),
            captureType = try { CaptureType.valueOf(json.optString("captureType", "NORMAL")) }
                          catch (_: Exception) { CaptureType.NORMAL },
            description = json.optString("description", ""),
            isBuiltin = json.optBoolean("isBuiltin", false),
            enabled = json.optBoolean("enabled", true),
            sortOrder = json.optInt("sortOrder", 99),
            systemPrompt = json.optNullStr("systemPrompt"),
            rules = json.optNullStr("rules"),
            processingMode = json.optString("processingMode", "auto"),
            timeoutSec = json.optInt("timeoutSec", 120)
        )
    }

    private fun JSONObject.optNullStr(key: String): String? =
        if (has(key) && !isNull(key)) optString(key).ifEmpty { null } else null

    // region 悬浮球白名单

    /** 获取白名单 App 列表（悬浮球只在这些 App 中显示） */
    fun getVisibleApps(context: Context): Set<String> {
        val raw = prefs(context).getString(KEY_VISIBLE_APPS, null) ?: return emptySet()
        return raw.split(",").filter { it.isNotBlank() }.toSet()
    }

    fun setVisibleApps(context: Context, apps: Set<String>) {
        prefs(context).edit().putString(KEY_VISIBLE_APPS, apps.joinToString(",")).apply()
    }

    fun addVisibleApp(context: Context, packageName: String) {
        val apps = getVisibleApps(context).toMutableSet()
        apps.add(packageName)
        setVisibleApps(context, apps)
    }

    fun removeVisibleApp(context: Context, packageName: String) {
        val apps = getVisibleApps(context).toMutableSet()
        apps.remove(packageName)
        setVisibleApps(context, apps)
    }

    fun isHideInSelf(context: Context): Boolean =
        prefs(context).getBoolean(KEY_HIDE_IN_SELF, true)

    fun setHideInSelf(context: Context, hide: Boolean) {
        prefs(context).edit().putBoolean(KEY_HIDE_IN_SELF, hide).apply()
    }

    fun isShowOnLauncher(context: Context): Boolean =
        prefs(context).getBoolean(KEY_SHOW_ON_LAUNCHER, false)

    fun setShowOnLauncher(context: Context, show: Boolean) {
        prefs(context).edit().putBoolean(KEY_SHOW_ON_LAUNCHER, show).apply()
    }

    /** 判断是否应该在指定包名的 App 中隐藏悬浮球 */
    fun shouldHideInApp(context: Context, packageName: String): Boolean {
        // 桌面单独控制
        if (isLauncherPackage(packageName)) return !isShowOnLauncher(context)
        // 白名单模式：只在白名单内的 App 中显示，未配置则全部隐藏
        val whitelist = getVisibleApps(context)
        if (whitelist.isEmpty()) return true // 未配置白名单 = 全部隐藏
        return packageName !in whitelist
    }

    private fun isLauncherPackage(packageName: String): Boolean {
        return packageName.contains("launcher", ignoreCase = true) ||
               packageName.contains("home", ignoreCase = true)
    }

    // endregion
}

/**
 * 兼容旧代码：Actions.ALL 现在从 BUILTIN_ACTIONS 读取
 */
object Actions {
    val ALL: List<ActionConfig> get() = BUILTIN_ACTIONS
}
