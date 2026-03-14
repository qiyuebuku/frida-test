package com.example.screenshotassistant.capture

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import com.example.screenshotassistant.data.SkillCommand
import com.example.screenshotassistant.data.SkillDetail
import com.example.screenshotassistant.network.HttpClient
import org.json.JSONArray
import org.json.JSONObject

/**
 * 悬浮球菜单项：一个 Skill 命令 + 截屏类型的绑定
 */
data class FloatingAction(
    val skillName: String,
    val skillDisplayName: String,
    val commandId: String,
    val displayName: String,
    val icon: String,
    val captureTypes: List<String>,
    val enabled: Boolean = true,
    val sortOrder: Int = 0,
) {
    /** 是否支持指定的截屏类型 */
    fun supportsCaptureType(type: String): Boolean = type in captureTypes

    val captureType: CaptureType
        get() = when {
            "manual_scroll" in captureTypes -> CaptureType.MANUAL_SCROLL
            "long_scroll" in captureTypes -> CaptureType.LONG_SCROLL
            else -> CaptureType.NORMAL
        }
}

/**
 * 快捷操作：截屏方式 + Skill 命令的绑定组合
 */
data class QuickAction(
    val skillName: String,
    val commandId: String,
    val captureType: String,
    val displayName: String,
    val icon: String,
    val useCount: Int = 0,
    val lastUsedAt: Long = 0,
)

/**
 * 管理悬浮球菜单数据：从 API 加载 Skill 命令，缓存到本地
 */
object FloatingMenuManager {

    private const val TAG = "FloatingMenuManager"
    private const val PREFS_NAME = "floating_menu"
    private const val KEY_ACTIONS = "cached_actions"
    private const val KEY_DISABLED = "disabled_commands"  // skillName:commandId
    private const val KEY_QUICK_ACTIONS = "quick_actions"

    private var cachedActions: List<FloatingAction> = emptyList()

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /**
     * 从服务端加载所有 screenshot 类 Skill 命令（需在 IO 线程）
     */
    fun refreshFromServer(context: Context): Boolean {
        val client = HttpClient.instance
        if (client == null) {
            Log.w(TAG, "refreshFromServer: HttpClient.instance is null")
            return false
        }
        val skills = client.getSkills()
        if (skills == null) {
            Log.w(TAG, "refreshFromServer: getSkills() returned null")
            return false
        }
        Log.i(TAG, "refreshFromServer: got ${skills.size} skills")

        val actions = mutableListOf<FloatingAction>()
        var order = 0

        for (skill in skills) {
            val detail = client.getSkillDetail(skill.name) ?: continue
            for (cmd in detail.commands) {
                if (cmd.input != "screenshot") continue
                val disabled = getDisabledCommands(context)
                val key = "${skill.name}:${cmd.id}"
                actions.add(
                    FloatingAction(
                        skillName = skill.name,
                        skillDisplayName = skill.displayName,
                        commandId = cmd.id,
                        displayName = cmd.name,
                        icon = getIconForCommand(cmd.id),
                        captureTypes = cmd.captureTypes,
                        enabled = key !in disabled,
                        sortOrder = order++,
                    )
                )
            }
        }

        cachedActions = actions
        saveToCache(context, actions)
        pruneQuickActions(context, actions)
        Log.d(TAG, "Refreshed ${actions.size} floating actions from server")
        return true
    }

    /**
     * 获取所有悬浮菜单项（优先用内存缓存，否则读本地缓存）
     */
    fun getActions(context: Context): List<FloatingAction> {
        if (cachedActions.isNotEmpty()) return cachedActions
        cachedActions = loadFromCache(context)
        if (cachedActions.isEmpty()) {
            // 回退到 BUILTIN_ACTIONS 兼容
            cachedActions = builtinFallback()
        }
        return cachedActions
    }

    /**
     * 获取启用的菜单项
     */
    fun getEnabledActions(context: Context): List<FloatingAction> =
        getActions(context).filter { it.enabled }

    /**
     * 按截屏类型过滤命令
     */
    fun getActionsForCaptureType(context: Context, captureType: String): List<FloatingAction> =
        getEnabledActions(context).filter { it.supportsCaptureType(captureType) }

    /**
     * 获取快捷操作（最多 3 个，按使用频率排序）
     */
    fun getQuickActions(context: Context): List<QuickAction> {
        val json = prefs(context).getString(KEY_QUICK_ACTIONS, null) ?: return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                QuickAction(
                    skillName = obj.getString("skillName"),
                    commandId = obj.getString("commandId"),
                    captureType = obj.getString("captureType"),
                    displayName = obj.getString("displayName"),
                    icon = obj.optString("icon", "📱"),
                    useCount = obj.optInt("useCount", 0),
                    lastUsedAt = obj.optLong("lastUsedAt", 0),
                )
            }.sortedByDescending { it.useCount * 100 + (it.lastUsedAt / 60000) }
                .take(3)
        } catch (_: Exception) { emptyList() }
    }

    /**
     * 记录一次使用（自动学习快捷操作）
     */
    fun recordUsage(context: Context, skillName: String, commandId: String, captureType: String) {
        val quickActions = getQuickActions(context).toMutableList()
        val existing = quickActions.find { it.skillName == skillName && it.commandId == commandId && it.captureType == captureType }

        val action = getActions(context).find { it.skillName == skillName && it.commandId == commandId }
        val displayName = action?.displayName ?: commandId
        val icon = action?.icon ?: "📱"

        if (existing != null) {
            quickActions.remove(existing)
            quickActions.add(existing.copy(useCount = existing.useCount + 1, lastUsedAt = System.currentTimeMillis()))
        } else {
            quickActions.add(QuickAction(skillName, commandId, captureType, displayName, icon, 1, System.currentTimeMillis()))
        }

        val arr = JSONArray()
        quickActions.sortedByDescending { it.useCount }.take(10).forEach { qa ->
            arr.put(JSONObject().apply {
                put("skillName", qa.skillName)
                put("commandId", qa.commandId)
                put("captureType", qa.captureType)
                put("displayName", qa.displayName)
                put("icon", qa.icon)
                put("useCount", qa.useCount)
                put("lastUsedAt", qa.lastUsedAt)
            })
        }
        prefs(context).edit().putString(KEY_QUICK_ACTIONS, arr.toString()).apply()
    }

    /**
     * 禁用/启用命令
     */
    fun setCommandEnabled(context: Context, skillName: String, commandId: String, enabled: Boolean) {
        val disabled = getDisabledCommands(context).toMutableSet()
        val key = "$skillName:$commandId"
        if (enabled) disabled.remove(key) else disabled.add(key)
        prefs(context).edit().putString(KEY_DISABLED, disabled.joinToString(",")).apply()
        // 更新缓存
        cachedActions = cachedActions.map {
            if (it.skillName == skillName && it.commandId == commandId) it.copy(enabled = enabled) else it
        }
    }

    private fun getDisabledCommands(context: Context): Set<String> {
        val raw = prefs(context).getString(KEY_DISABLED, null) ?: return emptySet()
        return raw.split(",").filter { it.isNotBlank() }.toSet()
    }

    private fun saveToCache(context: Context, actions: List<FloatingAction>) {
        val arr = JSONArray()
        actions.forEach { a ->
            arr.put(JSONObject().apply {
                put("skillName", a.skillName)
                put("skillDisplayName", a.skillDisplayName)
                put("commandId", a.commandId)
                put("displayName", a.displayName)
                put("icon", a.icon)
                put("captureTypes", JSONArray(a.captureTypes))
                put("sortOrder", a.sortOrder)
            })
        }
        prefs(context).edit().putString(KEY_ACTIONS, arr.toString()).apply()
    }

    private fun loadFromCache(context: Context): List<FloatingAction> {
        val json = prefs(context).getString(KEY_ACTIONS, null) ?: return emptyList()
        val disabled = getDisabledCommands(context)
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                val captureArr = obj.getJSONArray("captureTypes")
                val captureTypes = (0 until captureArr.length()).map { captureArr.getString(it) }
                val key = "${obj.getString("skillName")}:${obj.getString("commandId")}"
                FloatingAction(
                    skillName = obj.getString("skillName"),
                    skillDisplayName = obj.getString("skillDisplayName"),
                    commandId = obj.getString("commandId"),
                    displayName = obj.getString("displayName"),
                    icon = obj.optString("icon", "📱"),
                    captureTypes = captureTypes,
                    enabled = key !in disabled,
                    sortOrder = obj.optInt("sortOrder", 0),
                )
            }
        } catch (_: Exception) { emptyList() }
    }

    /** 清理无效的快捷操作（服务端已不存在的命令） */
    private fun pruneQuickActions(context: Context, validActions: List<FloatingAction>) {
        val validKeys = validActions.map { "${it.skillName}:${it.commandId}" }.toSet()
        val json = prefs(context).getString(KEY_QUICK_ACTIONS, null) ?: return
        try {
            val arr = JSONArray(json)
            val kept = JSONArray()
            var pruned = 0
            for (i in 0 until arr.length()) {
                val obj = arr.getJSONObject(i)
                val key = "${obj.getString("skillName")}:${obj.getString("commandId")}"
                if (key in validKeys) {
                    kept.put(obj)
                } else {
                    pruned++
                    Log.i(TAG, "Pruned stale quick action: $key")
                }
            }
            if (pruned > 0) {
                prefs(context).edit().putString(KEY_QUICK_ACTIONS, kept.toString()).apply()
                Log.i(TAG, "Pruned $pruned stale quick actions, kept ${kept.length()}")
            }
        } catch (_: Exception) {}
    }

    /** 兼容回退：从 BUILTIN_ACTIONS 生成 FloatingAction */
    private fun builtinFallback(): List<FloatingAction> {
        return BUILTIN_ACTIONS.map { action ->
            val captureTypes = when (action.captureType) {
                CaptureType.NORMAL -> listOf("normal")
                CaptureType.LONG_SCROLL -> listOf("normal", "long_scroll")
                CaptureType.MANUAL_SCROLL -> listOf("manual_scroll")
            }
            FloatingAction(
                skillName = "screenshot",
                skillDisplayName = "截屏工具箱",
                commandId = action.id,
                displayName = action.name,
                icon = getIconForCommand(action.id),
                captureTypes = captureTypes,
                enabled = action.enabled,
                sortOrder = action.sortOrder,
            )
        }
    }

    private fun getIconForCommand(id: String): String = when (id) {
        "ocr" -> "🔤"
        "chat_reply" -> "💬"
        "table" -> "📊"
        "search" -> "🔍"
        "fund_holdings", "ocr-analyze" -> "📈"
        "full_page" -> "📄"
        "manual_scroll" -> "✋"
        else -> "📱"
    }
}
