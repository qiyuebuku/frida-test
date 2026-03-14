package com.example.screenshotassistant.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.*
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.example.screenshotassistant.data.StreamEvent
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.data.buildEventsFromTask
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.network.TaskStreamClient
import com.example.screenshotassistant.ui.components.MarkdownViewer
import com.example.screenshotassistant.ui.components.formatDuration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

// ==================== CLI 配色 ====================
private val CliBlue = Color(0xFF5B9CF5)
private val CliGreen = Color(0xFF4EC86C)
private val CliRed = Color(0xFFE05252)
private val CliYellow = Color(0xFFD4A54E)
private val CliDimText = Color(0xFF8B8B8B)
private val CliText = Color(0xFFD4D4D4)
private val CliBg = Color(0xFF1A1A1A)
private val CliUserMsgBg = Color(0xFF2A2A2A)
private val CliSurfaceDim = Color(0xFF232323)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var task by remember { mutableStateOf<TaskItem?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    // 统一事件流
    var events by remember { mutableStateOf(listOf<StreamEvent>()) }
    // 排队中的用户消息（独立于主事件流，始终显示在最底部）
    var queuedMessages by remember { mutableStateOf(listOf<String>()) }
    var progress by remember { mutableStateOf(0) }
    var sseConnected by remember { mutableStateOf(false) }
    var sseFailed by remember { mutableStateOf(false) }

    // 输入状态
    var inputText by remember { mutableStateOf("") }
    var isSending by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    // 自动滚动到底部
    LaunchedEffect(events.size, queuedMessages.size) {
        delay(100)
        scrollState.animateScrollTo(scrollState.maxValue)
    }

    // 加载任务数据 → 重建事件流
    LaunchedEffect(taskId) {
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getTask(taskId)?.let { loaded ->
                task = loaded
                events = buildEventsFromTask(loaded)
                progress = loaded.progress
            }
        }
        isLoading = false
    }

    // SSE 实时流
    LaunchedEffect(taskId, task?.isProcessing) {
        val t = task ?: return@LaunchedEffect
        if (!t.isProcessing) return@LaunchedEffect

        val serverUrl = HttpClient.instance?.serverUrl ?: return@LaunchedEffect
        val streamClient = TaskStreamClient(serverUrl)

        try {
            sseConnected = true
            // 添加 thinking 事件
            events = events + StreamEvent.Thinking()

            streamClient.connect(
                taskId = taskId,
                scope = this,
                onToolCall = { display, detail, prog, isText, output ->
                    // 移除 thinking
                    events = events.filterNot { it is StreamEvent.Thinking }
                    // 去重
                    val isDuplicate = events.any {
                        (it is StreamEvent.ToolCall && it.display == display && it.detail == detail) ||
                        (it is StreamEvent.AssistantText && isText && it.content == (detail.ifBlank { display }))
                    }
                    if (!isDuplicate) {
                        if (isText) {
                            events = events + StreamEvent.AssistantText(detail.ifBlank { display })
                        } else {
                            events = events + StreamEvent.ToolCall(
                                tool = "", display = display, detail = detail,
                                output = output, isRunning = true
                            )
                        }
                    }
                    if (prog > progress) progress = prog
                },
                onToolResult = { display, output, isError ->
                    events = events.toMutableList().also { list ->
                        val idx = list.indexOfLast { it is StreamEvent.ToolCall && (it as StreamEvent.ToolCall).display == display }
                        if (idx >= 0) {
                            val tc = list[idx] as StreamEvent.ToolCall
                            list[idx] = tc.copy(
                                output = if (output.isNotBlank()) output else tc.output,
                                isError = isError,
                                isRunning = false
                            )
                        }
                    }
                },
                onTextDelta = { _, _, prog ->
                    progress = prog
                },
                onMessageQueued = { content ->
                    // 消息已排队（不操作 events，queuedMessages 已在 onSend 中添加）
                    Toast.makeText(context, "消息已排队", Toast.LENGTH_SHORT).show()
                },
                onStatusChange = { status ->
                    if (status == "processing" && queuedMessages.isNotEmpty()) {
                        // 排队消息被拾取：从 queuedMessages 移入主事件流
                        val msg = queuedMessages.first()
                        queuedMessages = queuedMessages.drop(1)
                        events = events + StreamEvent.UserMessage(msg) + StreamEvent.Thinking()
                    }
                    scope.launch(Dispatchers.IO) {
                        HttpClient.instance?.getTask(taskId)?.let { loaded ->
                            withContext(Dispatchers.Main) { task = loaded }
                        }
                    }
                },
                onDone = { status, result, errorMsg ->
                    sseConnected = false
                    // 移除 thinking，标记所有 ToolCall 为完成
                    events = events.filterNot { it is StreamEvent.Thinking }.map {
                        if (it is StreamEvent.ToolCall && it.isRunning) it.copy(isRunning = false)
                        else it
                    }

                    task = task?.copy(
                        status = status,
                        result = result,
                        errorMsg = errorMsg,
                        progress = 100
                    )

                    // 从 DB 重新加载并重建完整事件流
                    scope.launch(Dispatchers.IO) {
                        HttpClient.instance?.getTask(taskId)?.let { loaded ->
                            withContext(Dispatchers.Main) {
                                task = loaded
                                events = buildEventsFromTask(loaded)
                            }
                        }
                    }
                },
                onError = {
                    sseConnected = false
                    sseFailed = true
                    events = events.filterNot { it is StreamEvent.Thinking }
                }
            ).join()
        } catch (_: Exception) {
            sseConnected = false
            sseFailed = true
            events = events.filterNot { it is StreamEvent.Thinking }
        }
    }

    // SSE 失败回退轮询
    LaunchedEffect(sseFailed) {
        if (!sseFailed) return@LaunchedEffect
        while (task?.isProcessing == true) {
            delay(3000)
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let {
                    task = it
                    events = buildEventsFromTask(it)
                }
            }
        }
    }

    Scaffold(
        containerColor = CliBg,
        bottomBar = {
            val t = task
            if (t != null) {
                CliBottomBar(
                    task = t,
                    inputText = inputText,
                    onInputChange = { inputText = it },
                    isSending = isSending,
                    onStop = {
                        scope.launch(Dispatchers.IO) {
                            HttpClient.instance?.stopTask(taskId)
                        }
                    },
                    onSend = {
                        val msg = inputText.trim()
                        if (msg.isNotEmpty()) {
                            inputText = ""
                            val isProcessing = task?.isProcessing == true
                            if (isProcessing) {
                                // 处理中：消息进入排队列表（始终显示在最底部）
                                queuedMessages = queuedMessages + msg
                            } else {
                                // 已完成：消息直接追加到主事件流
                                events = events + StreamEvent.UserMessage(msg)
                            }
                            isSending = true
                            scope.launch(Dispatchers.IO) {
                                val resp = HttpClient.instance?.sendMessage(taskId, msg)
                                val loaded = HttpClient.instance?.getTask(taskId)
                                withContext(Dispatchers.Main) {
                                    isSending = false
                                    if (resp == null) {
                                        Toast.makeText(context, "发送失败，请重试", Toast.LENGTH_SHORT).show()
                                        if (isProcessing) {
                                            queuedMessages = queuedMessages.dropLast(1)
                                        } else {
                                            events = events.dropLast(1)
                                        }
                                    } else if (loaded != null) {
                                        task = loaded
                                    }
                                }
                            }
                        }
                    }
                )
            }
        },
        topBar = {
            CliTopBar(task = task, progress = progress, sseConnected = sseConnected, onBack = onBack)
        }
    ) { padding ->
        if (isLoading) {
            Box(
                modifier = Modifier.fillMaxSize().padding(padding).background(CliBg),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator(color = CliBlue, modifier = Modifier.size(32.dp))
            }
            return@Scaffold
        }

        val t = task
        if (t == null) {
            Box(
                modifier = Modifier.fillMaxSize().padding(padding).background(CliBg),
                contentAlignment = Alignment.Center
            ) {
                Text("任务不存在", color = CliDimText)
            }
            return@Scaffold
        }

        // 主内容：统一事件流
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .background(CliBg)
                .verticalScroll(scrollState)
        ) {
            // 处理中进度条
            if (t.isProcessing) {
                LinearProgressIndicator(
                    progress = { progress / 100f },
                    modifier = Modifier.fillMaxWidth(),
                    color = CliBlue,
                    trackColor = CliSurfaceDim
                )
            }

            // 错误信息
            if (t.isFailed && !t.errorMsg.isNullOrBlank()) {
                CliErrorBlock(t.errorMsg)
            }

            // 渲染事件流
            events.forEach { event ->
                when (event) {
                    is StreamEvent.UserMessage -> CliUserMessage(event)
                    is StreamEvent.AssistantText -> CliAssistantText(event)
                    is StreamEvent.ToolCall -> CliToolCallItem(event)
                    is StreamEvent.Thinking -> CliThinkingItem()
                }
            }

            // 排队中的用户消息（始终在最底部）
            queuedMessages.forEach { msg ->
                CliUserMessage(StreamEvent.UserMessage(content = msg, isQueued = true))
            }

            // 空状态
            if (events.isEmpty() && !t.isProcessing) {
                Box(
                    modifier = Modifier.fillMaxWidth().padding(32.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        when {
                            t.isFailed -> "任务执行失败"
                            t.isStopped -> "任务已停止"
                            else -> "暂无内容"
                        },
                        color = CliDimText, fontSize = 13.sp
                    )
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}


// ==================== 用户消息 ====================
@Composable
private fun CliUserMessage(msg: StreamEvent.UserMessage) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(CliUserMsgBg)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(
            ">",
            color = Color.White,
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.padding(end = 8.dp)
        )
        Text(
            msg.content,
            color = Color.White,
            fontSize = 14.sp,
            modifier = Modifier.weight(1f)
        )
        if (msg.isQueued) {
            Text(
                "排队中",
                color = CliDimText,
                fontSize = 11.sp,
                modifier = Modifier.padding(start = 8.dp)
            )
        }
    }
}


// ==================== Claude 文本回复 ====================
@Composable
private fun CliAssistantText(event: StreamEvent.AssistantText) {
    val content = event.content
    // 短文本直接渲染，长文本或含 Markdown 标记的用 MarkdownViewer
    val isMarkdown = content.length > 200 ||
        content.contains("\n#") || content.contains("\n|") ||
        content.contains("\n- ") || content.contains("```") ||
        content.startsWith("#")

    if (isMarkdown) {
        // Markdown 内容：● 前缀 + MarkdownViewer
        Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            Row(
                modifier = Modifier.padding(horizontal = 12.dp),
                verticalAlignment = Alignment.Top
            ) {
                Text(
                    "●",
                    color = CliBlue,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(end = 8.dp, top = 2.dp)
                )
                // 取第一行作为前导文本（如果有的话）
                val firstLine = content.lines().firstOrNull()?.take(80) ?: ""
                if (firstLine.isNotBlank() && !firstLine.startsWith("#")) {
                    Text(firstLine, color = CliText, fontSize = 13.sp)
                }
            }
            MarkdownViewer(
                markdown = content,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 28.dp)
                    .heightIn(min = 50.dp, max = 2000.dp)
            )
        }
    } else {
        // 短文本：● 前缀 + 直接渲染
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.Top
        ) {
            Text(
                "●",
                color = CliBlue,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(end = 8.dp, top = 2.dp)
            )
            Text(
                content,
                color = CliText,
                fontSize = 13.sp,
                lineHeight = 20.sp
            )
        }
    }
}


// ==================== 工具调用 ====================
@Composable
private fun CliToolCallItem(tc: StreamEvent.ToolCall) {
    var expanded by remember { mutableStateOf(tc.output.isNotBlank()) }
    var detailDialog by remember { mutableStateOf(false) }
    val hasDetail = tc.detail.isNotBlank() || tc.output.isNotBlank()

    if (detailDialog) {
        ToolDetailDialog(tc, onDismiss = { detailDialog = false })
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp)
    ) {
        // ● ToolName(args)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .then(if (hasDetail) Modifier.clickable { expanded = !expanded } else Modifier)
                .padding(horizontal = 12.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (tc.isRunning) {
                val infiniteTransition = rememberInfiniteTransition(label = "pulse")
                val alpha by infiniteTransition.animateFloat(
                    initialValue = 0.3f, targetValue = 1f,
                    animationSpec = infiniteRepeatable(tween(800), RepeatMode.Reverse),
                    label = "alpha"
                )
                Text("●", color = CliBlue.copy(alpha = alpha), fontSize = 12.sp, fontFamily = FontFamily.Monospace)
            } else {
                Text(
                    "●",
                    color = if (tc.isError) CliRed else CliGreen,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
            Spacer(modifier = Modifier.width(8.dp))

            // 工具名加粗
            val displayParts = parseToolDisplay(tc.display)
            Text(
                text = displayParts.first,
                color = CliText,
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace
            )
            if (displayParts.second.isNotBlank()) {
                Text(
                    text = displayParts.second,
                    color = CliDimText,
                    fontSize = 13.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false)
                )
            }
        }

        // └ 输出
        if (tc.output.isNotBlank()) {
            AnimatedVisibility(
                visible = expanded,
                enter = expandVertically(),
                exit = shrinkVertically()
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { detailDialog = true }
                        .padding(start = 32.dp, end = 12.dp, top = 2.dp, bottom = 4.dp)
                ) {
                    val outputLines = tc.output.lines().take(5)
                    outputLines.forEachIndexed { idx, line ->
                        Row {
                            Text(
                                if (idx == 0) "└ " else "  ",
                                color = CliDimText.copy(alpha = 0.5f),
                                fontSize = 12.sp,
                                fontFamily = FontFamily.Monospace
                            )
                            Text(
                                line,
                                color = if (tc.isError) CliRed.copy(alpha = 0.7f) else CliDimText,
                                fontSize = 12.sp,
                                fontFamily = FontFamily.Monospace,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                    if (tc.output.lines().size > 5) {
                        Text(
                            "  … 点击查看完整输出",
                            color = CliBlue.copy(alpha = 0.5f),
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            modifier = Modifier.padding(start = 16.dp, top = 2.dp)
                        )
                    }
                }
            }
        }
    }
}


// ==================== 思考中 ====================
@Composable
private fun CliThinkingItem() {
    val infiniteTransition = rememberInfiniteTransition(label = "think")
    val dotCount by infiniteTransition.animateFloat(
        initialValue = 0f, targetValue = 3f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Restart),
        label = "dots"
    )

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            "※",
            color = CliYellow,
            fontSize = 12.sp,
            fontFamily = FontFamily.Monospace
        )
        Text(
            "thinking" + ".".repeat(dotCount.toInt() + 1),
            color = CliYellow,
            fontSize = 13.sp,
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.padding(start = 4.dp)
        )
    }
}


// ==================== 错误块 ====================
@Composable
private fun CliErrorBlock(error: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(CliRed.copy(alpha = 0.1f))
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text("✗", color = CliRed, fontSize = 13.sp, fontFamily = FontFamily.Monospace)
        Spacer(modifier = Modifier.width(8.dp))
        Text(error, color = CliRed.copy(alpha = 0.9f), fontSize = 12.sp)
    }
}


// ==================== 顶栏 ====================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CliTopBar(
    task: TaskItem?,
    progress: Int,
    sseConnected: Boolean,
    onBack: () -> Unit
) {
    TopAppBar(
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = CliBg,
            titleContentColor = CliText
        ),
        title = {
            if (task != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(
                                when {
                                    task.isProcessing -> CliBlue
                                    task.isCompleted -> CliGreen
                                    task.isFailed -> CliRed
                                    task.isStopped -> CliYellow
                                    else -> CliDimText
                                }
                            )
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        task.typeLabel,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        buildString {
                            append(extractTime(task.createdAt))
                            if (task.durationSec != null) append(" ${formatDuration(task.durationSec)}")
                            if (task.isProcessing) append(" ${progress}%")
                        },
                        fontSize = 11.sp,
                        color = CliDimText
                    )
                }
            } else {
                Text("任务详情", fontSize = 14.sp)
            }
        },
        navigationIcon = {
            IconButton(onClick = onBack, modifier = Modifier.size(36.dp)) {
                Icon(
                    Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = CliDimText,
                    modifier = Modifier.size(18.dp)
                )
            }
        },
        actions = {
            val t = task
            if (t != null && !t.result.isNullOrBlank()) {
                val ctx = LocalContext.current
                IconButton(onClick = { copyToClipboard(ctx, t.result) }) {
                    Icon(
                        Icons.Default.ContentCopy,
                        contentDescription = "复制",
                        tint = CliDimText,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }
    )
}


// ==================== 底栏 ====================
@Composable
private fun CliBottomBar(
    task: TaskItem,
    inputText: String,
    onInputChange: (String) -> Unit,
    isSending: Boolean,
    onStop: () -> Unit,
    onSend: () -> Unit
) {
    Surface(color = CliSurfaceDim, tonalElevation = 0.dp) {
        if (task.isProcessing) {
            // 处理中也可以输入（消息会排队）
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "❯",
                        color = CliBlue,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.padding(end = 8.dp)
                    )
                    OutlinedTextField(
                        value = inputText,
                        onValueChange = onInputChange,
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("发送消息（将排队等待处理）", color = CliDimText, fontSize = 12.sp) },
                        maxLines = 3,
                        enabled = !isSending,
                        textStyle = LocalTextStyle.current.copy(color = CliText, fontSize = 13.sp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = CliBlue.copy(alpha = 0.5f),
                            unfocusedBorderColor = CliDimText.copy(alpha = 0.3f),
                            cursorColor = CliBlue
                        ),
                        shape = RoundedCornerShape(8.dp)
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    IconButton(
                        onClick = onSend,
                        enabled = inputText.isNotBlank() && !isSending,
                        modifier = Modifier.size(36.dp)
                    ) {
                        Icon(
                            Icons.AutoMirrored.Filled.Send,
                            contentDescription = "发送",
                            tint = if (inputText.isNotBlank()) CliBlue else CliDimText.copy(alpha = 0.3f),
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }
                // 停止按钮
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
                    horizontalArrangement = Arrangement.Center
                ) {
                    TextButton(
                        onClick = onStop,
                        colors = ButtonDefaults.textButtonColors(contentColor = CliRed.copy(alpha = 0.7f))
                    ) {
                        Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(14.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("停止", fontSize = 12.sp)
                    }
                }
            }
        } else if (task.sessionId != null) {
            // 已完成：输入框
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    "❯",
                    color = CliBlue,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(end = 8.dp)
                )
                OutlinedTextField(
                    value = inputText,
                    onValueChange = onInputChange,
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("追问...", color = CliDimText, fontSize = 13.sp) },
                    maxLines = 3,
                    enabled = !isSending,
                    textStyle = LocalTextStyle.current.copy(color = CliText, fontSize = 13.sp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = CliBlue.copy(alpha = 0.5f),
                        unfocusedBorderColor = CliDimText.copy(alpha = 0.3f),
                        cursorColor = CliBlue
                    ),
                    shape = RoundedCornerShape(8.dp)
                )
                Spacer(modifier = Modifier.width(4.dp))
                IconButton(
                    onClick = onSend,
                    enabled = inputText.isNotBlank() && !isSending,
                    modifier = Modifier.size(36.dp)
                ) {
                    if (isSending) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            color = CliBlue,
                            strokeWidth = 2.dp
                        )
                    } else {
                        Icon(
                            Icons.AutoMirrored.Filled.Send,
                            contentDescription = "发送",
                            tint = if (inputText.isNotBlank()) CliBlue else CliDimText.copy(alpha = 0.3f),
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }
            }
        }
    }
}


// ==================== 全屏详情对话框 ====================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ToolDetailDialog(tc: StreamEvent.ToolCall, onDismiss: () -> Unit) {
    val context = LocalContext.current

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Scaffold(
            containerColor = CliBg,
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = CliSurfaceDim, titleContentColor = CliText
                    ),
                    title = {
                        Text(tc.display, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    },
                    navigationIcon = {
                        IconButton(onClick = onDismiss) {
                            Icon(Icons.Default.Close, contentDescription = "关闭", tint = CliDimText)
                        }
                    },
                    actions = {
                        IconButton(onClick = {
                            val text = buildString {
                                if (tc.detail.isNotBlank()) append(tc.detail)
                                if (tc.output.isNotBlank()) {
                                    if (isNotEmpty()) append("\n\n")
                                    append(tc.output)
                                }
                            }
                            copyToClipboard(context, text)
                        }) {
                            Icon(Icons.Default.ContentCopy, contentDescription = "复制", tint = CliDimText)
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
                    .padding(16.dp)
            ) {
                if (tc.detail.isNotBlank()) {
                    Text("⎿ 详情", color = CliDimText, fontSize = 12.sp, fontFamily = FontFamily.Monospace,
                         modifier = Modifier.padding(bottom = 8.dp))
                    SelectionContainer {
                        Text(
                            text = formatDetail(tc.detail),
                            color = CliText, fontSize = 12.sp,
                            fontFamily = FontFamily.Monospace, lineHeight = 16.sp
                        )
                    }
                }
                if (tc.output.isNotBlank()) {
                    if (tc.detail.isNotBlank()) {
                        HorizontalDivider(color = CliSurfaceDim, modifier = Modifier.padding(vertical = 16.dp))
                    }
                    Text("⎿ 输出", color = CliDimText, fontSize = 12.sp, fontFamily = FontFamily.Monospace,
                         modifier = Modifier.padding(bottom = 8.dp))
                    SelectionContainer {
                        Text(
                            text = formatDetail(tc.output),
                            color = if (tc.isError) CliRed else CliGreen,
                            fontSize = 12.sp, fontFamily = FontFamily.Monospace, lineHeight = 16.sp
                        )
                    }
                }
            }
        }
    }
}


// ==================== 工具函数 ====================

/** 解析 "Bash(python client.py snap)" → ("Bash", "(python client.py snap)") */
private fun parseToolDisplay(display: String): Pair<String, String> {
    val parenIdx = display.indexOf('(')
    return if (parenIdx > 0) {
        Pair(display.substring(0, parenIdx), display.substring(parenIdx))
    } else {
        Pair(display, "")
    }
}

private fun extractTime(timestamp: String): String {
    val regex = Regex("""[\sT](\d{2}:\d{2})""")
    return regex.find(timestamp)?.groupValues?.get(1) ?: timestamp.takeLast(8).take(5)
}

private fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("task_result", text))
    Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
}

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
