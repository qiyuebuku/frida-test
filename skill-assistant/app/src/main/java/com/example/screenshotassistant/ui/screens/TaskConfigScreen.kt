package com.example.screenshotassistant.ui.screens

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.ActionConfigStore
import com.example.screenshotassistant.capture.BUILTIN_ACTIONS

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskConfigScreen(actionId: String, onBack: () -> Unit) {
    val context = LocalContext.current
    val action = remember { ActionConfigStore.get(context, actionId) }

    if (action == null) {
        onBack()
        return
    }

    val defaultAction = BUILTIN_ACTIONS.find { it.id == actionId }

    var systemPrompt by remember { mutableStateOf(action.systemPrompt ?: "") }
    var rules by remember { mutableStateOf(action.rules ?: "") }
    var processingMode by remember { mutableStateOf(action.processingMode) }
    var timeoutSec by remember { mutableStateOf(action.timeoutSec.toString()) }
    var modeExpanded by remember { mutableStateOf(false) }

    val modeOptions = listOf(
        "auto" to "自动",
        "sync_sse" to "同步 SSE（轻量任务）",
        "async_task" to "异步任务（重型任务）"
    )

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("${action.name} 配置") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // 系统提示词
            Text("系统提示词", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = systemPrompt,
                onValueChange = { systemPrompt = it },
                modifier = Modifier.fillMaxWidth(),
                minLines = 4,
                maxLines = 8,
                placeholder = { Text("留空使用默认提示词") }
            )

            // 输出规则
            Text("输出规则", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = rules,
                onValueChange = { rules = it },
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
                maxLines = 6,
                placeholder = { Text("附加约束条件，拼接在提示词之后") }
            )

            // 高级选项
            Text("高级选项", style = MaterialTheme.typography.titleSmall)

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    // 处理模式
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("处理模式", style = MaterialTheme.typography.bodyMedium)
                        Box {
                            TextButton(onClick = { modeExpanded = true }) {
                                Text(modeOptions.find { it.first == processingMode }?.second ?: processingMode)
                            }
                            DropdownMenu(
                                expanded = modeExpanded,
                                onDismissRequest = { modeExpanded = false }
                            ) {
                                modeOptions.forEach { (mode, label) ->
                                    DropdownMenuItem(
                                        text = { Text(label) },
                                        onClick = {
                                            processingMode = mode
                                            modeExpanded = false
                                        }
                                    )
                                }
                            }
                        }
                    }

                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

                    // 超时时间
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("超时时间（秒）", style = MaterialTheme.typography.bodyMedium)
                        OutlinedTextField(
                            value = timeoutSec,
                            onValueChange = { timeoutSec = it.filter { c -> c.isDigit() } },
                            modifier = Modifier.width(100.dp),
                            singleLine = true
                        )
                    }
                }
            }

            // 按钮
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                // 恢复默认
                if (action.isBuiltin) {
                    OutlinedButton(
                        onClick = {
                            ActionConfigStore.resetOverride(context, actionId)
                            val def = defaultAction ?: action
                            systemPrompt = ""
                            rules = ""
                            processingMode = def.processingMode
                            timeoutSec = def.timeoutSec.toString()
                            Toast.makeText(context, "已恢复默认", Toast.LENGTH_SHORT).show()
                        },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("恢复默认")
                    }
                }

                // 保存
                Button(
                    onClick = {
                        val timeout = timeoutSec.toIntOrNull() ?: action.timeoutSec
                        val updated = action.copy(
                            systemPrompt = systemPrompt.ifBlank { null },
                            rules = rules.ifBlank { null },
                            processingMode = processingMode,
                            timeoutSec = timeout
                        )
                        if (action.isBuiltin) {
                            ActionConfigStore.saveOverride(context, updated)
                        } else {
                            ActionConfigStore.saveCustomAction(context, updated)
                        }
                        Toast.makeText(context, "配置已保存", Toast.LENGTH_SHORT).show()
                        onBack()
                    },
                    modifier = Modifier.weight(1f)
                ) {
                    Text("保存")
                }
            }
        }
    }
}
