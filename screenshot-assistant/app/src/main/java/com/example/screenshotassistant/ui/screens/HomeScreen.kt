package com.example.screenshotassistant.ui.screens

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Article
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.automirrored.filled.TrendingUp
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.Actions
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.ui.components.TaskCard
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    onTaskClick: (Int) -> Unit,
    onNavigateToTasks: () -> Unit
) {
    var serverConnected by remember { mutableStateOf(false) }
    var a11yConnected by remember { mutableStateOf(ScreenAssistAccessibilityService.instance != null) }
    var recentTasks by remember { mutableStateOf<List<TaskItem>>(emptyList()) }

    val context = LocalContext.current

    // 定期刷新状态和最近任务
    LaunchedEffect(Unit) {
        while (true) {
            a11yConnected = ScreenAssistAccessibilityService.instance != null
            withContext(Dispatchers.IO) {
                serverConnected = HttpClient.instance?.healthCheck() == true
                if (serverConnected) {
                    HttpClient.instance?.getTasks(limit = 3)?.let { (tasks, _) ->
                        recentTasks = tasks
                    }
                }
            }
            delay(3000)
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        contentPadding = PaddingValues(vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // 连接状态
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("截屏助手", style = MaterialTheme.typography.headlineMedium)
                StatusDot(connected = serverConnected, label = if (serverConnected) "已连接" else "未连接")
            }
        }

        // 无障碍服务提示
        if (!a11yConnected) {
            item {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer
                    ),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            Icons.Default.Warning,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            "无障碍服务未开启",
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f)
                        )
                        TextButton(onClick = {
                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        }) {
                            Text("去开启")
                        }
                    }
                }
            }
        }

        // 快捷操作
        item {
            Text(
                "快捷操作",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 4.dp)
            )
        }

        item {
            QuickActionsGrid()
        }

        // 服务端任务（非截屏类）
        item {
            Text(
                "AI 分析",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 4.dp)
            )
        }

        item {
            ServerTaskButtons(
                serverConnected = serverConnected,
                onTaskCreated = { taskId -> onTaskClick(taskId) }
            )
        }

        // 最近任务
        if (recentTasks.isNotEmpty()) {
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("最近任务", style = MaterialTheme.typography.titleMedium)
                    TextButton(onClick = onNavigateToTasks) {
                        Text("查看全部")
                    }
                }
            }

            items(recentTasks, key = { it.id }) { task ->
                TaskCard(task = task, onClick = { onTaskClick(task.id) })
            }
        }
    }
}

@Composable
private fun QuickActionsGrid() {
    // 显示前 4 个常用动作（2x2 网格）
    val quickActions = Actions.ALL.take(4)

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (row in quickActions.chunked(2)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                for (action in row) {
                    QuickActionCard(
                        action = action,
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        }
    }
}

@Composable
private fun QuickActionCard(action: ActionConfig, modifier: Modifier = Modifier) {
    val icon = actionToIcon(action.id)

    Card(
        onClick = {
            FloatingWindowService.instance?.executeActionFromBroadcast(action)
        },
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.5f)
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                imageVector = icon,
                contentDescription = action.name,
                modifier = Modifier.size(28.dp),
                tint = MaterialTheme.colorScheme.primary
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = action.name,
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}

@Composable
private fun StatusDot(connected: Boolean, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Surface(
            modifier = Modifier.size(8.dp),
            shape = MaterialTheme.shapes.extraSmall,
            color = if (connected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error
        ) {}
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = if (connected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error
        )
    }
}

private fun actionToIcon(actionId: String): ImageVector {
    return when (actionId) {
        "ocr" -> Icons.Default.TextFields
        "chat_reply" -> Icons.AutoMirrored.Filled.Chat
        "table" -> Icons.Default.TableChart
        "search" -> Icons.Default.Search
        "fund_holdings" -> Icons.Default.AccountBalance
        "full_page" -> Icons.AutoMirrored.Filled.Article
        "manual_scroll" -> Icons.Default.PanTool
        else -> Icons.Default.TouchApp
    }
}

data class ServerTask(
    val taskType: String,
    val name: String,
    val icon: ImageVector,
    val description: String
)

private val SERVER_TASKS = listOf(
    ServerTask("fund_trade_run", "每日决策", Icons.AutoMirrored.Filled.TrendingUp, "采集市场数据，AI 生成交易决策"),
    ServerTask("fund_review", "持仓审视", Icons.Default.Assessment, "分析持仓绩效，生成调仓建议"),
)

@Composable
private fun ServerTaskButtons(
    serverConnected: Boolean,
    onTaskCreated: (Int) -> Unit
) {
    val scope = rememberCoroutineScope()

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        for (task in SERVER_TASKS) {
            var isLoading by remember { mutableStateOf(false) }

            Card(
                onClick = {
                    if (!serverConnected || isLoading) return@Card
                    isLoading = true
                    scope.launch {
                        val taskId = withContext(Dispatchers.IO) {
                            HttpClient.instance?.createCommandTask(task.taskType)
                        }
                        isLoading = false
                        if (taskId != null) {
                            onTaskCreated(taskId)
                        }
                    }
                },
                modifier = Modifier.weight(1f),
                colors = CardDefaults.cardColors(
                    containerColor = if (serverConnected)
                        MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.5f)
                    else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f)
                )
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    if (isLoading) {
                        CircularProgressIndicator(modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
                    } else {
                        Icon(
                            imageVector = task.icon,
                            contentDescription = task.name,
                            modifier = Modifier.size(28.dp),
                            tint = if (serverConnected) MaterialTheme.colorScheme.tertiary
                            else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f)
                        )
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = task.name,
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (serverConnected) MaterialTheme.colorScheme.onSurface
                        else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f)
                    )
                    Text(
                        text = task.description,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1
                    )
                }
            }
        }
    }
}
