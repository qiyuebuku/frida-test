package com.example.screenshotassistant.ui.screens

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.ActionConfigStore
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen(
    onNavigateToTaskConfig: ((String) -> Unit)? = null,
    onNavigateToMenuConfig: (() -> Unit)? = null
) {
    var serverConnected by remember { mutableStateOf(false) }
    var a11yConnected by remember { mutableStateOf(ScreenAssistAccessibilityService.instance != null) }

    val context = LocalContext.current

    // 加载操作列表
    val actions = remember { ActionConfigStore.getAll(context) }

    LaunchedEffect(Unit) {
        while (true) {
            a11yConnected = ScreenAssistAccessibilityService.instance != null
            serverConnected = withContext(Dispatchers.IO) {
                HttpClient.instance?.healthCheck() == true
            }
            delay(5000)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text("设置", style = MaterialTheme.typography.headlineMedium)

        // 任务配置
        Text(
            "任务配置",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        actions.filter { it.id != "manual_scroll" }.forEach { action ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onNavigateToTaskConfig?.invoke(action.id) }
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = getActionEmoji(action.id),
                        modifier = Modifier.width(32.dp)
                    )
                    Column(modifier = Modifier.weight(1f)) {
                        Text(action.name, style = MaterialTheme.typography.titleSmall)
                        Text(
                            text = if (action.systemPrompt.isNullOrBlank()) "系统提示词: 默认"
                                   else "系统提示词: ${action.systemPrompt.take(30)}...",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                    Icon(
                        Icons.Default.ChevronRight,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        // 悬浮窗菜单配置
        Text(
            "悬浮窗菜单",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onNavigateToMenuConfig?.invoke() }
        ) {
            Row(
                modifier = Modifier.padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    Icons.Default.Widgets,
                    contentDescription = null,
                    modifier = Modifier.size(24.dp),
                    tint = MaterialTheme.colorScheme.primary
                )
                Spacer(modifier = Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text("菜单项管理", style = MaterialTheme.typography.titleSmall)
                    val enabledCount = actions.count { it.enabled }
                    Text(
                        "${enabledCount}/${actions.size} 项已启用",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Icon(
                    Icons.Default.ChevronRight,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // 悬浮球显示控制
        Text(
            "悬浮球显示",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                // 在桌面显示
                var showOnLauncher by remember { mutableStateOf(ActionConfigStore.isShowOnLauncher(context)) }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("在桌面显示悬浮球", style = MaterialTheme.typography.titleSmall)
                        Text(
                            "返回桌面时是否显示悬浮球",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    Switch(
                        checked = showOnLauncher,
                        onCheckedChange = {
                            showOnLauncher = it
                            ActionConfigStore.setShowOnLauncher(context, it)
                        }
                    )
                }

                HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

                // 白名单 App 管理
                var visibleApps by remember { mutableStateOf(ActionConfigStore.getVisibleApps(context)) }
                var showAddDialog by remember { mutableStateOf(false) }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("App 白名单", style = MaterialTheme.typography.titleSmall)
                        Text(
                            if (visibleApps.isEmpty()) "未配置，悬浮球在所有 App 中隐藏"
                            else "仅在以下 ${visibleApps.size} 个 App 中显示悬浮球",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    IconButton(onClick = { showAddDialog = true }) {
                        Icon(Icons.Default.Add, contentDescription = "添加")
                    }
                }

                if (visibleApps.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    visibleApps.forEach { pkg ->
                        val appName = try {
                            val ai = context.packageManager.getApplicationInfo(pkg, 0)
                            context.packageManager.getApplicationLabel(ai).toString()
                        } catch (_: Exception) { pkg }

                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                appName,
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.weight(1f)
                            )
                            IconButton(
                                onClick = {
                                    ActionConfigStore.removeVisibleApp(context, pkg)
                                    visibleApps = ActionConfigStore.getVisibleApps(context)
                                },
                                modifier = Modifier.size(32.dp)
                            ) {
                                Icon(
                                    Icons.Default.Close,
                                    contentDescription = "移除",
                                    modifier = Modifier.size(16.dp),
                                    tint = MaterialTheme.colorScheme.error
                                )
                            }
                        }
                    }
                }

                if (showAddDialog) {
                    AddHiddenAppDialog(
                        onDismiss = { showAddDialog = false },
                        onAdd = { pkg ->
                            ActionConfigStore.addVisibleApp(context, pkg)
                            visibleApps = ActionConfigStore.getVisibleApps(context)
                            showAddDialog = false
                        }
                    )
                }
            }
        }

        // 系统
        Text(
            "系统",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        // 服务器地址
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("服务器地址", style = MaterialTheme.typography.titleSmall)
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    HttpClient.instance?.serverUrl ?: "未配置",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // 服务状态
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("服务状态", style = MaterialTheme.typography.titleSmall)
                Spacer(modifier = Modifier.height(12.dp))

                StatusRow("服务端连接", serverConnected)
                Spacer(modifier = Modifier.height(8.dp))
                StatusRow("无障碍服务", a11yConnected)

                if (!a11yConnected) {
                    Spacer(modifier = Modifier.height(8.dp))
                    FilledTonalButton(
                        onClick = {
                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("开启无障碍服务")
                    }
                }
            }
        }

        // 版本
        Text(
            "版本 2.1.0",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp)
        )
    }
}

@Composable
private fun StatusRow(label: String, connected: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Surface(
                modifier = Modifier.size(8.dp),
                shape = MaterialTheme.shapes.extraSmall,
                color = if (connected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.error
            ) {}
            Spacer(modifier = Modifier.width(6.dp))
            Text(
                if (connected) "已连接" else "未连接",
                style = MaterialTheme.typography.bodySmall,
                color = if (connected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.error
            )
        }
    }
}

@Composable
private fun AddHiddenAppDialog(
    onDismiss: () -> Unit,
    onAdd: (String) -> Unit
) {
    val context = LocalContext.current
    // 获取已安装的第三方 App 列表
    val installedApps = remember {
        context.packageManager.getInstalledApplications(0)
            .filter { it.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM == 0 }
            .filter { it.packageName != context.packageName }
            .sortedBy { context.packageManager.getApplicationLabel(it).toString() }
    }
    var searchText by remember { mutableStateOf("") }
    val filtered = installedApps.filter {
        val label = context.packageManager.getApplicationLabel(it).toString()
        label.contains(searchText, ignoreCase = true) ||
                it.packageName.contains(searchText, ignoreCase = true)
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("选择要隐藏悬浮球的 App") },
        text = {
            Column {
                OutlinedTextField(
                    value = searchText,
                    onValueChange = { searchText = it },
                    placeholder = { Text("搜索 App 名称") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 400.dp)
                        .verticalScroll(rememberScrollState())
                ) {
                    filtered.take(50).forEach { appInfo ->
                        val label = context.packageManager.getApplicationLabel(appInfo).toString()
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onAdd(appInfo.packageName) }
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(label, style = MaterialTheme.typography.bodyMedium)
                                Text(
                                    appInfo.packageName,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

private fun getActionEmoji(id: String): String = when (id) {
    "ocr" -> "🔤"
    "chat_reply" -> "💬"
    "table" -> "📊"
    "search" -> "🔍"
    "fund_holdings" -> "📈"
    "full_page" -> "📄"
    "manual_scroll" -> "✋"
    else -> "📱"
}
