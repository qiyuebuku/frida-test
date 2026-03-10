package com.example.screenshotassistant.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.layout.*
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
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.ui.components.MarkdownViewer
import com.example.screenshotassistant.ui.components.formatDuration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var task by remember { mutableStateOf<TaskItem?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    val context = LocalContext.current

    // 加载任务（处理中的自动轮询）
    LaunchedEffect(taskId) {
        while (true) {
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let {
                    task = it
                }
            }
            isLoading = false
            if (task?.isProcessing != true) break
            delay(3000)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(task?.typeLabel ?: "任务详情") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    if (task?.isCompleted == true && !task?.result.isNullOrBlank()) {
                        IconButton(onClick = {
                            copyToClipboard(context, task?.result ?: "")
                        }) {
                            Icon(Icons.Default.ContentCopy, contentDescription = "复制")
                        }
                    }
                }
            )
        }
    ) { padding ->
        if (isLoading) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        val t = task
        if (t == null) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center
            ) {
                Text("任务不存在")
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // 状态信息
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                colors = CardDefaults.cardColors(
                    containerColor = statusContainerColor(t.status)
                )
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = statusIcon(t.status),
                            contentDescription = null,
                            tint = statusIconColor(t.status),
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = statusLabel(t.status),
                            style = MaterialTheme.typography.titleSmall
                        )
                    }

                    Spacer(modifier = Modifier.height(8.dp))

                    // 时间信息
                    Text(
                        text = buildString {
                            append(t.createdAt.takeLast(8).take(5))
                            if (t.completedAt != null) {
                                append(" -> ")
                                append(t.completedAt.takeLast(8).take(5))
                            }
                            if (t.durationSec != null) {
                                append("  用时 ")
                                append(formatDuration(t.durationSec))
                            }
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    // 进度条（处理中）
                    if (t.isProcessing) {
                        Spacer(modifier = Modifier.height(8.dp))
                        LinearProgressIndicator(
                            progress = { t.progress / 100f },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "${t.progress}% - ${t.progressMsg ?: "处理中..."}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    // 错误信息
                    if (t.isFailed && !t.errorMsg.isNullOrBlank()) {
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = t.errorMsg,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error
                        )
                    }
                }
            }

            // 结果内容（Markdown）
            if (t.isCompleted && !t.result.isNullOrBlank()) {
                MarkdownViewer(
                    markdown = t.result,
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(horizontal = 8.dp)
                )
            } else if (t.isProcessing) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(modifier = Modifier.size(48.dp))
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            t.progressMsg ?: "正在处理...",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun statusContainerColor(status: String) = when (status) {
    "completed" -> MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.3f)
    "failed" -> MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.3f)
    "processing" -> MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.3f)
    else -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f)
}

private fun statusIcon(status: String) = when (status) {
    "completed" -> Icons.Default.CheckCircle
    "failed" -> Icons.Default.Error
    "processing" -> Icons.Default.HourglassTop
    else -> Icons.Default.Schedule
}

@Composable
private fun statusIconColor(status: String) = when (status) {
    "completed" -> MaterialTheme.colorScheme.primary
    "failed" -> MaterialTheme.colorScheme.error
    "processing" -> MaterialTheme.colorScheme.tertiary
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

private fun statusLabel(status: String) = when (status) {
    "completed" -> "处理完成"
    "failed" -> "处理失败"
    "processing" -> "处理中"
    "pending" -> "等待处理"
    else -> status
}

private fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("task_result", text))
    Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
}
