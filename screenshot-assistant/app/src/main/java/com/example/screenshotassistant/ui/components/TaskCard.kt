package com.example.screenshotassistant.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.TaskItem

@Composable
fun TaskCard(task: TaskItem, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
        )
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.Top
        ) {
            // 状态图标
            Icon(
                imageVector = statusIcon(task.status),
                contentDescription = task.status,
                tint = statusColor(task.status),
                modifier = Modifier
                    .size(24.dp)
                    .padding(top = 2.dp)
            )

            Spacer(modifier = Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                // 标题行
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = task.title ?: task.typeLabel,
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.weight(1f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    if (task.durationSec != null) {
                        Text(
                            text = formatDuration(task.durationSec),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }

                Spacer(modifier = Modifier.height(4.dp))

                // 进度条（处理中）
                if (task.isProcessing) {
                    LinearProgressIndicator(
                        progress = { task.progress / 100f },
                        modifier = Modifier.fillMaxWidth(),
                        trackColor = MaterialTheme.colorScheme.surfaceVariant,
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = task.progressMsg ?: "等待处理...",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                // 摘要（已完成）
                if (task.isCompleted && !task.summary.isNullOrBlank()) {
                    Text(
                        text = renderMarkdown(task.summary),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                // 错误信息（失败）
                if (task.isFailed && !task.errorMsg.isNullOrBlank()) {
                    Text(
                        text = task.errorMsg,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

@Composable
private fun statusIcon(status: String): ImageVector {
    return when (status) {
        "completed" -> Icons.Default.CheckCircle
        "failed" -> Icons.Default.Error
        "processing" -> Icons.Default.HourglassTop
        else -> Icons.Default.Schedule
    }
}

@Composable
private fun statusColor(status: String): androidx.compose.ui.graphics.Color {
    return when (status) {
        "completed" -> MaterialTheme.colorScheme.primary
        "failed" -> MaterialTheme.colorScheme.error
        "processing" -> MaterialTheme.colorScheme.tertiary
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

private fun renderMarkdown(text: String): AnnotatedString {
    // 预处理：去掉 header 标记、表格管道符、链接语法，合并空白
    val cleaned = text
        .replace(Regex("#+\\s*"), "")                 // ## headers
        .replace(Regex("\\[(.+?)\\]\\(.+?\\)"), "$1") // [link](url)
        .replace("|", " ")                             // table pipes
        .replace(Regex("\\s{2,}"), " ")               // collapse whitespace
        .trim()

    return buildAnnotatedString {
        var i = 0
        while (i < cleaned.length) {
            when {
                // **bold**
                cleaned.startsWith("**", i) -> {
                    val end = cleaned.indexOf("**", i + 2)
                    if (end > i + 2) {
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                            append(cleaned.substring(i + 2, end))
                        }
                        i = end + 2
                    } else {
                        append(cleaned[i])
                        i++
                    }
                }
                // *italic*
                cleaned[i] == '*' && (i == 0 || cleaned[i - 1] != '*') -> {
                    val end = cleaned.indexOf('*', i + 1)
                    if (end > i + 1 && !cleaned.startsWith("**", end)) {
                        withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                            append(cleaned.substring(i + 1, end))
                        }
                        i = end + 1
                    } else {
                        append(cleaned[i])
                        i++
                    }
                }
                // `code`
                cleaned[i] == '`' -> {
                    val end = cleaned.indexOf('`', i + 1)
                    if (end > i + 1) {
                        withStyle(SpanStyle(fontWeight = FontWeight.Medium)) {
                            append(cleaned.substring(i + 1, end))
                        }
                        i = end + 1
                    } else {
                        append(cleaned[i])
                        i++
                    }
                }
                else -> {
                    append(cleaned[i])
                    i++
                }
            }
        }
    }
}

fun formatDuration(seconds: Int): String {
    return when {
        seconds < 60 -> "${seconds}s"
        seconds < 3600 -> "${seconds / 60}m${seconds % 60}s"
        else -> "${seconds / 3600}h${(seconds % 3600) / 60}m"
    }
}
