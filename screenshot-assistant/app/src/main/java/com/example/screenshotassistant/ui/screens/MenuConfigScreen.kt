package com.example.screenshotassistant.ui.screens

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.ActionConfigStore
import com.example.screenshotassistant.capture.CaptureType

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MenuConfigScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    var actions by remember { mutableStateOf(ActionConfigStore.getAll(context)) }
    var showAddDialog by remember { mutableStateOf(false) }
    var hasChanges by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("悬浮窗菜单") },
                navigationIcon = {
                    IconButton(onClick = {
                        if (hasChanges) {
                            ActionConfigStore.saveOrder(context, actions)
                            Toast.makeText(context, "菜单配置已保存", Toast.LENGTH_SHORT).show()
                        }
                        onBack()
                    }) {
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
        ) {
            Text(
                "开关控制是否在悬浮窗中显示，上下箭头调整顺序",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            )

            LazyColumn(
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(horizontal = 16.dp)
            ) {
                itemsIndexed(actions, key = { _, a -> a.id }) { index, action ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp)
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            // 排序按钮
                            Column {
                                IconButton(
                                    onClick = {
                                        if (index > 0) {
                                            val list = actions.toMutableList()
                                            val tmp = list[index]
                                            list[index] = list[index - 1]
                                            list[index - 1] = tmp
                                            actions = list
                                            hasChanges = true
                                        }
                                    },
                                    modifier = Modifier.size(24.dp),
                                    enabled = index > 0
                                ) {
                                    Icon(Icons.Default.KeyboardArrowUp, contentDescription = "上移",
                                        modifier = Modifier.size(18.dp))
                                }
                                IconButton(
                                    onClick = {
                                        if (index < actions.lastIndex) {
                                            val list = actions.toMutableList()
                                            val tmp = list[index]
                                            list[index] = list[index + 1]
                                            list[index + 1] = tmp
                                            actions = list
                                            hasChanges = true
                                        }
                                    },
                                    modifier = Modifier.size(24.dp),
                                    enabled = index < actions.lastIndex
                                ) {
                                    Icon(Icons.Default.KeyboardArrowDown, contentDescription = "下移",
                                        modifier = Modifier.size(18.dp))
                                }
                            }

                            Spacer(modifier = Modifier.width(8.dp))

                            // 名称
                            Column(modifier = Modifier.weight(1f)) {
                                Text(action.name, style = MaterialTheme.typography.bodyMedium)
                                Text(
                                    action.description,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }

                            // 删除按钮（仅自定义操作）
                            if (!action.isBuiltin) {
                                IconButton(
                                    onClick = {
                                        ActionConfigStore.deleteCustomAction(context, action.id)
                                        actions = actions.filter { it.id != action.id }
                                        Toast.makeText(context, "已删除 ${action.name}", Toast.LENGTH_SHORT).show()
                                    },
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Delete, contentDescription = "删除",
                                        tint = MaterialTheme.colorScheme.error,
                                        modifier = Modifier.size(18.dp))
                                }
                            }

                            // 启用开关
                            Switch(
                                checked = action.enabled,
                                onCheckedChange = { enabled ->
                                    actions = actions.map {
                                        if (it.id == action.id) it.copy(enabled = enabled) else it
                                    }
                                    hasChanges = true
                                }
                            )
                        }
                    }
                }
            }

            // 添加自定义操作按钮
            Button(
                onClick = { showAddDialog = true },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                Icon(Icons.Default.Add, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text("添加自定义操作")
            }
        }
    }

    if (showAddDialog) {
        AddCustomActionDialog(
            onDismiss = { showAddDialog = false },
            onSave = { newAction ->
                ActionConfigStore.saveCustomAction(context, newAction)
                actions = ActionConfigStore.getAll(context)
                showAddDialog = false
                Toast.makeText(context, "已添加 ${newAction.name}", Toast.LENGTH_SHORT).show()
            }
        )
    }
}

@Composable
private fun AddCustomActionDialog(
    onDismiss: () -> Unit,
    onSave: (ActionConfig) -> Unit
) {
    var name by remember { mutableStateOf("") }
    var systemPrompt by remember { mutableStateOf("") }
    var rules by remember { mutableStateOf("") }
    var captureType by remember { mutableStateOf(CaptureType.NORMAL) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("新建操作") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("操作名称") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                Text("截图模式", style = MaterialTheme.typography.labelMedium)
                CaptureType.entries.forEach { type ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = captureType == type,
                            onClick = { captureType = type }
                        )
                        Text(
                            when (type) {
                                CaptureType.NORMAL -> "普通截图（当前屏幕）"
                                CaptureType.LONG_SCROLL -> "自动滚动（长页面采集）"
                                CaptureType.MANUAL_SCROLL -> "手动滚动（手动控制采集）"
                            },
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }

                OutlinedTextField(
                    value = systemPrompt,
                    onValueChange = { systemPrompt = it },
                    label = { Text("系统提示词") },
                    minLines = 3,
                    maxLines = 5,
                    modifier = Modifier.fillMaxWidth()
                )

                OutlinedTextField(
                    value = rules,
                    onValueChange = { rules = it },
                    label = { Text("输出规则（可选）") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (name.isNotBlank()) {
                        val id = "custom_${System.currentTimeMillis()}"
                        onSave(ActionConfig(
                            id = id,
                            name = name,
                            icon = "apps",
                            captureType = captureType,
                            description = systemPrompt.take(30),
                            isBuiltin = false,
                            enabled = true,
                            sortOrder = 99,
                            systemPrompt = systemPrompt.ifBlank { null },
                            rules = rules.ifBlank { null },
                            processingMode = if (captureType == CaptureType.NORMAL) "sync_sse" else "async_task"
                        ))
                    }
                },
                enabled = name.isNotBlank()
            ) {
                Text("保存")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        }
    )
}
