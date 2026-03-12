package com.example.screenshotassistant.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.network.TaskStreamClient
import com.example.screenshotassistant.ui.components.MarkdownViewer
import com.example.screenshotassistant.ui.components.formatDuration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import kotlinx.coroutines.withContext

data class ToolCallStep(
    val display: String,
    val detail: String = "",
    val output: String = "",
    val progress: Int = 0,
    val timestamp: Long = System.currentTimeMillis(),
    val isText: Boolean = false,
    val isError: Boolean = false
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var task by remember { mutableStateOf<TaskItem?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    // SSE 实时状态
    var toolCalls by remember { mutableStateOf(listOf<ToolCallStep>()) }
    var partialText by remember { mutableStateOf("") }
    var progress by remember { mutableStateOf(0) }
    var currentStep by remember { mutableStateOf<String?>(null) }
    var sseConnected by remember { mutableStateOf(false) }
    var sseFailed by remember { mutableStateOf(false) }

    val context = LocalContext.current

    // 先加载任务基础信息，并用已有数据初始化 SSE 状态
    LaunchedEffect(taskId) {
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getTask(taskId)?.let { loaded ->
                task = loaded
                // 从服务端已保存的数据恢复状态（解决返回后重进丢失的问题）
                if (!loaded.partialResult.isNullOrBlank()) {
                    partialText = loaded.partialResult
                }
                if (loaded.toolCallDisplays.isNotEmpty()) {
                    toolCalls = loaded.toolCallDisplays.map { raw ->
                        var display = raw
                        var detail = ""
                        var output = ""
                        var isText = false
                        var isError = false
                        // 解析带分隔符的格式
                        val lines = raw.split("\n")
                        val displayLines = mutableListOf<String>()
                        for (line in lines) {
                            when {
                                line.startsWith("<SEP_DETAIL>") -> detail = line.removePrefix("<SEP_DETAIL>")
                                line.startsWith("<SEP_OUTPUT>") -> output = line.removePrefix("<SEP_OUTPUT>")
                                line == "<SEP_IS_TEXT>" -> isText = true
                                line == "<SEP_IS_ERROR>" -> isError = true
                                else -> displayLines.add(line)
                            }
                        }
                        display = displayLines.firstOrNull() ?: raw
                        // 兼容旧格式：第二行没有前缀时作为 detail
                        if (detail.isEmpty() && displayLines.size > 1) {
                            detail = displayLines.drop(1).joinToString("\n")
                        }
                        ToolCallStep(display = display, detail = detail, output = output, isText = isText, isError = isError)
                    }
                }
                progress = loaded.progress
                if (!loaded.progressMsg.isNullOrBlank()) {
                    currentStep = loaded.progressMsg
                }
            }
        }
        isLoading = false
    }

    // SSE 实时流连接（处理中的任务）
    LaunchedEffect(taskId, task?.isProcessing) {
        val t = task ?: return@LaunchedEffect
        if (!t.isProcessing) return@LaunchedEffect

        val serverUrl = HttpClient.instance?.serverUrl ?: return@LaunchedEffect
        val streamClient = TaskStreamClient(serverUrl)

        try {
            sseConnected = true
            streamClient.connect(
                taskId = taskId,
                scope = this,
                onToolCall = { display, detail, prog, isText ->
                    // 避免重复：如果最后一条已经是同名步骤，不再追加
                    if (toolCalls.lastOrNull()?.display != display) {
                        toolCalls = toolCalls + ToolCallStep(display = display, detail = detail, progress = prog, isText = isText)
                    }
                    currentStep = display
                    progress = prog
                },
                onToolResult = { display, output, isError ->
                    // 将 output 和 isError 附加到最后一个匹配 display 的 tool_call
                    val idx = toolCalls.indexOfLast { it.display == display }
                    if (idx >= 0 && (output.isNotBlank() || isError)) {
                        toolCalls = toolCalls.toMutableList().also {
                            it[idx] = it[idx].copy(
                                output = if (output.isNotBlank()) output else it[idx].output,
                                isError = isError
                            )
                        }
                    }
                },
                onTextDelta = { text, _, prog ->
                    partialText += text
                    progress = prog
                },
                onDone = { status, result, errorMsg ->
                    sseConnected = false
                    // 刷新完整任务数据
                    task = task?.copy(
                        status = status,
                        result = result ?: partialText.ifEmpty { null },
                        errorMsg = errorMsg,
                        progress = 100
                    )
                },
                onError = {
                    sseConnected = false
                    sseFailed = true
                }
            ).join()
        } catch (_: Exception) {
            sseConnected = false
            sseFailed = true
        }
    }

    // SSE 失败时回退到轮询
    LaunchedEffect(sseFailed) {
        if (!sseFailed) return@LaunchedEffect
        while (task?.isProcessing == true) {
            delay(3000)
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let { task = it }
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    val t = task
                    if (t != null) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            // 状态图标
                            Icon(
                                imageVector = statusIcon(t.status),
                                contentDescription = null,
                                tint = statusIconColor(t.status),
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                            // 标题
                            Text(
                                t.typeLabel,
                                style = MaterialTheme.typography.titleSmall,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            // 时间 + 耗时
                            Text(
                                buildString {
                                    append(extractTime(t.createdAt))
                                    if (t.durationSec != null) {
                                        append(" ${formatDuration(t.durationSec)}")
                                    }
                                },
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            // 处理中显示进度
                            if (t.isProcessing) {
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    "${if (t.isProcessing) progress else t.progress}%",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.tertiary
                                )
                                if (sseConnected) {
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Box(
                                        modifier = Modifier
                                            .size(6.dp)
                                            .clip(CircleShape)
                                            .background(MaterialTheme.colorScheme.primary)
                                    )
                                }
                            }
                        }
                    } else {
                        Text("任务详情", style = MaterialTheme.typography.titleSmall)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
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
            // 处理中显示进度条
            if (t.isProcessing) {
                LinearProgressIndicator(
                    progress = { progress / 100f },
                    modifier = Modifier.fillMaxWidth(),
                )
                val step = currentStep
                if (step != null) {
                    Text(
                        text = step,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp)
                    )
                }
            }

            // 错误信息
            if (t.isFailed && !t.errorMsg.isNullOrBlank()) {
                Text(
                    text = t.errorMsg,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)
                )
            }

            // 内容区域
            when {
                // 已完成 - 显示步骤 + 最终结果 + 复制按钮
                t.isCompleted && !t.result.isNullOrBlank() -> {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(horizontal = 8.dp)
                    ) {
                        if (toolCalls.isNotEmpty()) {
                            CompletedToolCallSteps(
                                steps = toolCalls,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 4.dp)
                            )
                        }
                        MarkdownViewer(
                            markdown = t.result,
                            modifier = Modifier.weight(1f).fillMaxWidth()
                        )
                        // 底部复制按钮
                        FilledTonalButton(
                            onClick = { copyToClipboard(context, t.result) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 8.dp, vertical = 8.dp)
                        ) {
                            Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(6.dp))
                            Text("复制全部内容")
                        }
                    }
                }
                // 处理中 - 实时显示步骤
                t.isProcessing -> {
                    ProcessingContent(
                        toolCalls = toolCalls,
                        partialText = partialText,
                        currentStep = currentStep,
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(horizontal = 8.dp)
                    )
                }
                // 失败
                t.isFailed -> {
                    if (!t.partialResult.isNullOrBlank()) {
                        Text(
                            "部分结果：",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                        )
                        MarkdownViewer(
                            markdown = t.partialResult,
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(horizontal = 8.dp)
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ProcessingContent(
    toolCalls: List<ToolCallStep>,
    partialText: String,
    currentStep: String?,
    modifier: Modifier = Modifier
) {
    if (toolCalls.isEmpty() && partialText.isBlank()) {
        Box(
            modifier = modifier,
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                CircularProgressIndicator(modifier = Modifier.size(48.dp))
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    "等待 AI 开始处理...",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        return
    }

    // 处理中只显示步骤列表，不显示 partial text（避免展示 AI 内部推理文本）
    if (toolCalls.isNotEmpty()) {
        ToolCallSteps(
            steps = toolCalls,
            isRunning = currentStep != null,
            modifier = modifier
                .padding(vertical = 4.dp)
        )
    }
}

@Composable
private fun ToolCallSteps(
    steps: List<ToolCallStep>,
    isRunning: Boolean,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    // 自动滚动到底部
    LaunchedEffect(steps.size) {
        scrollState.animateScrollTo(scrollState.maxValue)
    }

    var allExpanded by remember { mutableStateOf(false) }
    // 跟踪每个步骤的展开状态
    val expandedStates = remember { mutableStateMapOf<Int, Boolean>() }
    val hasAnyDetail = steps.any { it.detail.isNotBlank() || it.output.isNotBlank() }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(scrollState)
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
            ),
            shape = RoundedCornerShape(8.dp)
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "处理步骤 (${steps.size})",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.weight(1f)
                    )
                    // 只有前面的步骤（非最后3个）有可折叠的才显示按钮
                    val hasCollapsible = steps.size > 3 && steps.dropLast(3).any { it.detail.isNotBlank() || it.output.isNotBlank() }
                    if (hasCollapsible) {
                        TextButton(
                            onClick = {
                                allExpanded = !allExpanded
                                steps.forEachIndexed { i, s ->
                                    if (i < steps.size - 3 && (s.detail.isNotBlank() || s.output.isNotBlank())) {
                                        expandedStates[i] = allExpanded
                                    }
                                }
                            },
                            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                            modifier = Modifier.height(28.dp)
                        ) {
                            Text(
                                if (allExpanded) "全部收起" else "全部展开",
                                style = MaterialTheme.typography.labelSmall
                            )
                        }
                    }
                }
                Spacer(modifier = Modifier.height(6.dp))
                steps.forEachIndexed { index, step ->
                    val isLast = index == steps.lastIndex
                    val hasDetail = step.detail.isNotBlank() || step.output.isNotBlank()
                    // 最后 3 个步骤始终展开，之前的按手动状态
                    val isRecentStep = index >= steps.size - 3
                    val expanded = if (isRecentStep) true else expandedStates[index] == true
                    val canToggle = hasDetail && !isRecentStep

                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .then(
                                if (canToggle) Modifier.clickable {
                                    expandedStates[index] = !expanded
                                } else Modifier
                            )
                            .padding(vertical = 3.dp)
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (isLast && isRunning) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(14.dp),
                                    strokeWidth = 2.dp
                                )
                            } else {
                                Icon(
                                    when {
                                        step.isError -> Icons.Default.Cancel
                                        step.isText -> Icons.Default.Chat
                                        else -> Icons.Default.CheckCircle
                                    },
                                    contentDescription = null,
                                    modifier = Modifier.size(14.dp),
                                    tint = when {
                                        step.isError -> MaterialTheme.colorScheme.error
                                        step.isText -> MaterialTheme.colorScheme.tertiary
                                        else -> MaterialTheme.colorScheme.primary
                                    }
                                )
                            }
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = step.display,
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = if (step.isText) 2 else 1,
                                overflow = TextOverflow.Ellipsis,
                                color = if (isLast && isRunning)
                                    MaterialTheme.colorScheme.onSurface
                                else
                                    MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.weight(1f)
                            )
                            // 最后 3 个步骤不显示展开图标
                            if (canToggle) {
                                Icon(
                                    if (expanded) Icons.Default.ExpandLess
                                    else Icons.Default.ExpandMore,
                                    contentDescription = null,
                                    modifier = Modifier.size(16.dp),
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
                                )
                            }
                        }

                        if (hasDetail && expanded) {
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(start = 22.dp, top = 4.dp, bottom = 2.dp)
                                    .background(
                                        MaterialTheme.colorScheme.surface.copy(alpha = 0.5f),
                                        RoundedCornerShape(4.dp)
                                    )
                                    .padding(8.dp)
                            ) {
                                if (step.detail.isNotBlank()) {
                                    Text(
                                        text = formatDetail(step.detail),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f),
                                        softWrap = true
                                    )
                                }
                                if (step.output.isNotBlank()) {
                                    if (step.detail.isNotBlank()) {
                                        Spacer(modifier = Modifier.height(6.dp))
                                    }
                                    Text(
                                        text = "→ ${step.output}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.8f),
                                        softWrap = true,
                                        maxLines = 10,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** 已完成任务的步骤列表 - 默认折叠，点击展开 */
@Composable
private fun CompletedToolCallSteps(
    steps: List<ToolCallStep>,
    modifier: Modifier = Modifier
) {
    var stepsExpanded by remember { mutableStateOf(false) }
    var allDetailExpanded by remember { mutableStateOf(false) }
    val detailStates = remember { mutableStateMapOf<Int, Boolean>() }
    val hasAnyDetail = steps.any { it.detail.isNotBlank() || it.output.isNotBlank() }

    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f)
        ),
        shape = RoundedCornerShape(8.dp)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.clickable { stepsExpanded = !stepsExpanded }
            ) {
                Icon(
                    Icons.Default.CheckCircle,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = MaterialTheme.colorScheme.primary
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    "处理步骤 (${steps.size})",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f)
                )
                Icon(
                    if (stepsExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            AnimatedVisibility(
                visible = stepsExpanded,
                enter = expandVertically(),
                exit = shrinkVertically()
            ) {
                val innerScrollState = rememberScrollState()
                Column(
                    modifier = Modifier
                        .padding(top = 8.dp)
                        .heightIn(max = 400.dp)
                        .verticalScroll(innerScrollState)
                ) {
                    // 只有前面的步骤（非最后3个）有可折叠的才显示按钮
                    val hasCollapsible = steps.size > 3 && steps.dropLast(3).any { it.detail.isNotBlank() || it.output.isNotBlank() }
                    if (hasCollapsible) {
                        TextButton(
                            onClick = {
                                allDetailExpanded = !allDetailExpanded
                                steps.forEachIndexed { i, s ->
                                    if (i < steps.size - 3 && (s.detail.isNotBlank() || s.output.isNotBlank())) {
                                        detailStates[i] = allDetailExpanded
                                    }
                                }
                            },
                            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                            modifier = Modifier.height(28.dp)
                        ) {
                            Text(
                                if (allDetailExpanded) "全部收起详情" else "全部展开详情",
                                style = MaterialTheme.typography.labelSmall
                            )
                        }
                    }

                    steps.forEachIndexed { index, step ->
                        val hasDetail = step.detail.isNotBlank() || step.output.isNotBlank()
                        // 最后 3 个步骤始终展开，之前的按手动状态
                        val isRecentStep = index >= steps.size - 3
                        val detailExpanded = if (isRecentStep) true else detailStates[index] == true
                        val canToggle = hasDetail && !isRecentStep

                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .then(
                                    if (canToggle) Modifier.clickable {
                                        detailStates[index] = !(detailStates[index] == true)
                                    } else Modifier
                                )
                                .padding(vertical = 2.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    when {
                                        step.isError -> Icons.Default.Cancel
                                        step.isText -> Icons.Default.Chat
                                        else -> Icons.Default.CheckCircle
                                    },
                                    contentDescription = null,
                                    modifier = Modifier.size(12.dp),
                                    tint = when {
                                        step.isError -> MaterialTheme.colorScheme.error.copy(alpha = 0.7f)
                                        step.isText -> MaterialTheme.colorScheme.tertiary.copy(alpha = 0.7f)
                                        else -> MaterialTheme.colorScheme.primary.copy(alpha = 0.7f)
                                    }
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = step.display,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = if (step.isText) 2 else 1,
                                    overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.weight(1f)
                                )
                                // 最后 3 个步骤不显示展开图标
                                if (canToggle) {
                                    Icon(
                                        if (detailExpanded) Icons.Default.ExpandLess
                                        else Icons.Default.ExpandMore,
                                        contentDescription = null,
                                        modifier = Modifier.size(14.dp),
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                                    )
                                }
                            }
                            if (hasDetail && detailExpanded) {
                                Column(
                                    modifier = Modifier
                                        .padding(start = 18.dp, top = 2.dp)
                                        .background(
                                            MaterialTheme.colorScheme.surface.copy(alpha = 0.4f),
                                            RoundedCornerShape(4.dp)
                                        )
                                        .padding(6.dp)
                                ) {
                                    if (step.detail.isNotBlank()) {
                                        Text(
                                            text = step.detail,
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                                            softWrap = true
                                        )
                                    }
                                    if (step.output.isNotBlank()) {
                                        if (step.detail.isNotBlank()) {
                                            Spacer(modifier = Modifier.height(4.dp))
                                        }
                                        Text(
                                            text = "→ ${step.output}",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.primary.copy(alpha = 0.7f),
                                            softWrap = true,
                                            maxLines = 10,
                                            overflow = TextOverflow.Ellipsis
                                        )
                                    }
                                }
                            }
                        }
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

/** 从 "2026-03-10 19:02:05.436" 或 "2026-03-10T19:02:05" 中提取 "19:02" */
private fun extractTime(timestamp: String): String {
    // 找 HH:MM 部分：匹配 "空格或T" 后面的 \d{2}:\d{2}
    val regex = Regex("""[\sT](\d{2}:\d{2})""")
    return regex.find(timestamp)?.groupValues?.get(1) ?: timestamp.takeLast(8).take(5)
}

private fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("task_result", text))
    Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
}

/** 尝试格式化 JSON，失败则原样返回 */
private fun formatDetail(text: String): String {
    val trimmed = text.trim()
    return try {
        when {
            trimmed.startsWith("{") -> JSONObject(trimmed).toString(2)
            trimmed.startsWith("[") -> JSONArray(trimmed).toString(2)
            else -> text
        }
    } catch (_: Exception) {
        text
    }
}
