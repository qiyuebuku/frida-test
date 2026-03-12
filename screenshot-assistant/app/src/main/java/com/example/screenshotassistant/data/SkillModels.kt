package com.example.screenshotassistant.data

import org.json.JSONObject

data class SkillProject(
    val name: String,
    val displayName: String,
    val icon: String,
    val description: String,
    val category: String,
    val commandCount: Int,
) {
    companion object {
        fun fromJson(json: JSONObject) = SkillProject(
            name = json.optString("name"),
            displayName = json.optString("display_name"),
            icon = json.optString("icon", "star"),
            description = json.optString("description"),
            category = json.optString("category", "other"),
            commandCount = json.optInt("command_count", 0),
        )
    }
}

data class CommandArg(
    val name: String,
    val description: String,
    val required: Boolean,
) {
    companion object {
        fun fromJson(json: JSONObject) = CommandArg(
            name = json.optString("name"),
            description = json.optString("description"),
            required = json.optBoolean("required", false),
        )
    }
}

data class SkillCommand(
    val id: String,
    val name: String,
    val description: String,
    val input: String,
    val captureTypes: List<String>,
    val executor: String,
    val estimatedTime: Int,
    val floatable: Boolean,
    val args: List<CommandArg>,
) {
    val inputLabel: String
        get() = when (input) {
            "none" -> "无需输入"
            "screenshot" -> "需要截图"
            "text" -> "需要文本输入"
            "file" -> "需要文件"
            else -> input
        }

    val estimatedTimeLabel: String
        get() = when {
            estimatedTime >= 60 -> "~${estimatedTime / 60}min"
            else -> "~${estimatedTime}s"
        }

    companion object {
        fun fromJson(json: JSONObject): SkillCommand {
            val argsArr = json.optJSONArray("args")
            val args = if (argsArr != null) {
                (0 until argsArr.length()).map { CommandArg.fromJson(argsArr.getJSONObject(it)) }
            } else emptyList()

            val captureArr = json.optJSONArray("capture_types")
            val captureTypes = if (captureArr != null) {
                (0 until captureArr.length()).map { captureArr.getString(it) }
            } else emptyList()

            return SkillCommand(
                id = json.optString("id"),
                name = json.optString("name"),
                description = json.optString("description"),
                input = json.optString("input", "none"),
                captureTypes = captureTypes,
                executor = json.optString("executor", "claude"),
                estimatedTime = json.optInt("estimated_time", 60),
                floatable = json.optBoolean("floatable", false),
                args = args,
            )
        }
    }
}

data class SkillDetail(
    val project: SkillProject,
    val commands: List<SkillCommand>,
) {
    companion object {
        fun fromJson(json: JSONObject): SkillDetail {
            val commandsArr = json.optJSONArray("commands")
            val commands = if (commandsArr != null) {
                (0 until commandsArr.length()).map { SkillCommand.fromJson(commandsArr.getJSONObject(it)) }
            } else emptyList()

            return SkillDetail(
                project = SkillProject.fromJson(json),
                commands = commands,
            )
        }
    }
}
