package com.example.screenshotassistant.ui.screens

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.viewinterop.AndroidView
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.ui.components.formatDuration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// ==================== CLI 配色 ====================
private val CliBlue = Color(0xFF5B9CF5)
private val CliGreen = Color(0xFF4EC86C)
private val CliRed = Color(0xFFE05252)
private val CliYellow = Color(0xFFD4A54E)
private val CliDimText = Color(0xFF8B8B8B)
private val CliText = Color(0xFFD4D4D4)
private val CliBg = Color(0xFF1A1A1A)
private val CliSurfaceDim = Color(0xFF232323)

private val STATUS_LABELS = mapOf(
    "processing" to "处理中",
    "pending" to "等待中",
    "completed" to "已完成",
    "failed" to "失败",
    "stopped" to "已停止"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var task by remember { mutableStateOf<TaskItem?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    var inputText by remember { mutableStateOf("") }
    var isSending by remember { mutableStateOf(false) }
    var isStopping by remember { mutableStateOf(false) }
    var isExpanded by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    // 加载任务数据
    LaunchedEffect(taskId) {
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getTask(taskId)?.let { loaded ->
                task = loaded
            }
        }
        isLoading = false
    }

    // 轮询任务状态（处理中每 1.5 秒刷新）
    LaunchedEffect(taskId, task?.isProcessing) {
        if (task?.isProcessing != true) return@LaunchedEffect
        while (true) {
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let { loaded ->
                    withContext(Dispatchers.Main) { task = loaded }
                }
            }
            if (task?.isProcessing != true) break
            delay(1500)
        }
    }

    // 任务结束后，若无展开数据则额外轮询等待后台捕获完成
    LaunchedEffect(task?.isProcessing, task?.hasExpandedLog) {
        val t = task ?: return@LaunchedEffect
        if (t.isProcessing || t.hasExpandedLog) return@LaunchedEffect
        // 任务已结束但还没有展开数据，轮询等待
        repeat(5) {
            delay(2000)
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let { loaded ->
                    withContext(Dispatchers.Main) { task = loaded }
                }
            }
            if (task?.hasExpandedLog == true) return@LaunchedEffect
        }
    }

    Scaffold(
        containerColor = CliBg,
        bottomBar = {
            val t = task
            if (t != null && !isExpanded && t.hasTerminal) {
                CliBottomBar(
                    task = t,
                    inputText = inputText,
                    onInputChange = { inputText = it },
                    isSending = isSending,
                    onSend = {
                        val msg = inputText.trim()
                        if (msg.isNotEmpty()) {
                            inputText = ""
                            isSending = true
                            scope.launch(Dispatchers.IO) {
                                val resp = HttpClient.instance?.sendMessage(taskId, msg)
                                val loaded = HttpClient.instance?.getTask(taskId)
                                withContext(Dispatchers.Main) {
                                    isSending = false
                                    if (resp == null) {
                                        Toast.makeText(context, "发送失败，请重试", Toast.LENGTH_SHORT).show()
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
            CliTopBar(
                task = task,
                onBack = onBack,
                isExpanded = isExpanded,
                onToggleExpand = { isExpanded = !isExpanded },
                isStopping = isStopping,
                onStop = {
                    isStopping = true
                    scope.launch(Dispatchers.IO) {
                        HttpClient.instance?.stopTask(taskId)
                        // 立即刷新 task 数据，确保 status 已更新
                        HttpClient.instance?.getTask(taskId)?.let { loaded ->
                            withContext(Dispatchers.Main) {
                                task = loaded
                                isStopping = false
                            }
                        } ?: withContext(Dispatchers.Main) { isStopping = false }
                    }
                },
                onCopy = {
                    val text = task?.result ?: task?.summary ?: ""
                    if (text.isNotBlank()) {
                        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(ClipData.newPlainText("result", text))
                        Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
                    }
                }
            )
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

        if (task == null) {
            Box(
                modifier = Modifier.fillMaxSize().padding(padding).background(CliBg),
                contentAlignment = Alignment.Center
            ) {
                Text("任务不存在", color = CliDimText)
            }
            return@Scaffold
        }

        val t = task!!
        val serverUrl = HttpClient.instance?.serverUrl ?: ""
        val wsHost = serverUrl
            .removePrefix("http://")
            .removePrefix("https://")
            .trimEnd('/')

        when {
            // 有终端会话的任务 → 显示终端 WebView
            t.hasTerminal -> {
                val terminalPath = if (isExpanded) "/terminal/$taskId/expanded" else "/terminal/$taskId"
                TerminalWebView(
                    url = "http://$wsHost$terminalPath",
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding)
                        .background(CliBg)
                )
            }
            // 3. 纯 OCR 任务（无终端）→ 显示结果文本
            else -> {
                ResultTextView(
                    result = t.result ?: t.summary ?: "处理中...",
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding)
                        .background(CliBg)
                )
            }
        }
    }
}


// ==================== 顶栏 ====================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CliTopBar(
    task: TaskItem?,
    onBack: () -> Unit,
    isExpanded: Boolean,
    onToggleExpand: () -> Unit,
    isStopping: Boolean = false,
    onStop: () -> Unit,
    onCopy: () -> Unit
) {
    Column(modifier = Modifier.background(CliBg)) {
        TopAppBar(
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = CliBg,
                titleContentColor = CliText
            ),
            title = {
                if (task != null) {
                    Column {
                        // 主标题行：标题文字
                        Text(
                            task.typeLabel,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color = CliText
                        )
                        // 副信息行：状态圆点 + 状态文字 + 时间 + 耗时 + 进度%
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(top = 2.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
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
                            Spacer(modifier = Modifier.width(4.dp))
                            Text(
                                buildString {
                                    append(STATUS_LABELS[task.status] ?: task.status)
                                    append("  ")
                                    append(extractTime(task.createdAt))
                                    if (task.durationSec != null) {
                                        append("  ${formatDuration(task.durationSec)}")
                                    }
                                    if (task.isProcessing) {
                                        append("  ${task.progress}%")
                                    }
                                },
                                fontSize = 11.sp,
                                color = CliDimText,
                                maxLines = 1
                            )
                        }
                    }
                } else {
                    Text("任务详情", fontSize = 16.sp, fontWeight = FontWeight.Bold)
                }
            },
            navigationIcon = {
                IconButton(onClick = onBack, modifier = Modifier.size(40.dp)) {
                    Icon(
                        Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "返回",
                        tint = CliDimText,
                        modifier = Modifier.size(20.dp)
                    )
                }
            },
            actions = {
                // 纯 OCR 任务完成后显示复制按钮
                if (task != null && !task.hasTerminal && !task.result.isNullOrBlank()) {
                    IconButton(onClick = onCopy) {
                        Icon(
                            Icons.Default.ContentCopy,
                            contentDescription = "复制",
                            tint = CliDimText,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                }
                if (task != null && task.hasExpandedLog && !task.isProcessing) {
                    IconButton(onClick = onToggleExpand) {
                        Icon(
                            if (isExpanded) Icons.Default.UnfoldLess else Icons.Default.UnfoldMore,
                            contentDescription = if (isExpanded) "折叠" else "展开",
                            tint = if (isExpanded) CliBlue else CliDimText,
                            modifier = Modifier.size(22.dp)
                        )
                    }
                }
                if (task != null && (task.isProcessing || isStopping)) {
                    IconButton(onClick = onStop, enabled = !isStopping) {
                        if (isStopping) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = CliYellow,
                                strokeWidth = 2.dp
                            )
                        } else {
                            Icon(
                                Icons.Default.Stop,
                                contentDescription = "停止",
                                tint = CliRed,
                                modifier = Modifier.size(22.dp)
                            )
                        }
                    }
                }
            }
        )
        // 进度条紧贴标题栏底部
        if (task != null && task.isProcessing) {
            LinearProgressIndicator(
                progress = { task.progress / 100f },
                modifier = Modifier.fillMaxWidth().height(2.dp),
                color = CliBlue,
                trackColor = CliSurfaceDim,
            )
        }
    }
}


// ==================== 底栏 ====================
@Composable
private fun CliBottomBar(
    task: TaskItem,
    inputText: String,
    onInputChange: (String) -> Unit,
    isSending: Boolean,
    onSend: () -> Unit
) {
    Surface(color = CliSurfaceDim, tonalElevation = 0.dp) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            androidx.compose.foundation.text.BasicTextField(
                value = inputText,
                onValueChange = onInputChange,
                modifier = Modifier
                    .weight(1f)
                    .background(Color.Transparent, RoundedCornerShape(8.dp))
                    .border(1.dp, CliDimText.copy(alpha = 0.3f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                maxLines = 3,
                enabled = !isSending,
                textStyle = LocalTextStyle.current.copy(color = CliText, fontSize = 13.sp),
                cursorBrush = androidx.compose.ui.graphics.SolidColor(CliBlue),
                decorationBox = { innerTextField ->
                    Box {
                        if (inputText.isEmpty()) {
                            Text(
                                if (task.isProcessing) "发送消息（将排队等待处理）" else "追问...",
                                color = CliDimText,
                                fontSize = 12.sp
                            )
                        }
                        innerTextField()
                    }
                }
            )
            Spacer(modifier = Modifier.width(4.dp))
            IconButton(
                onClick = onSend,
                enabled = inputText.isNotBlank() && !isSending,
                modifier = Modifier.size(32.dp)
            ) {
                if (isSending) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(16.dp),
                        color = CliBlue,
                        strokeWidth = 2.dp
                    )
                } else {
                    Icon(
                        Icons.AutoMirrored.Filled.Send,
                        contentDescription = "发送",
                        tint = if (inputText.isNotBlank()) CliBlue else CliDimText.copy(alpha = 0.3f),
                        modifier = Modifier.size(16.dp)
                    )
                }
            }
        }
    }
}


// ==================== 工具函数 ====================

private fun extractTime(timestamp: String): String {
    val regex = Regex("""[\sT](\d{2}:\d{2})""")
    return regex.find(timestamp)?.groupValues?.get(1) ?: timestamp.takeLast(8).take(5)
}


// ==================== 结果文本视图（纯 OCR 任务） ====================

@Composable
private fun ResultTextView(
    result: String,
    modifier: Modifier = Modifier
) {
    androidx.compose.foundation.text.selection.SelectionContainer {
        Column(
            modifier = modifier
                .verticalScroll(rememberScrollState())
                .padding(12.dp)
        ) {
            Text(
                text = result,
                color = CliText,
                fontSize = 13.sp,
                fontFamily = FontFamily.Monospace,
                lineHeight = 18.sp,
            )
        }
    }
}


// ==================== 终端 WebView ====================

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TerminalWebView(
    url: String,
    modifier: Modifier = Modifier
) {
    // key(url) 确保 URL 变化时重建 WebView
    key(url) {
        AndroidView(
            factory = { ctx ->
                WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = true
                    settings.allowContentAccess = true
                    settings.mediaPlaybackRequiresUserGesture = false
                    settings.cacheMode = android.webkit.WebSettings.LOAD_NO_CACHE
                    setBackgroundColor(android.graphics.Color.parseColor("#1a1a1a"))

                    webViewClient = object : WebViewClient() {
                        override fun onPageFinished(view: WebView?, url: String?) {
                            super.onPageFinished(view, url)
                            Log.i("TerminalWV", "Page loaded: $url")
                        }
                    }
                    webChromeClient = object : WebChromeClient() {
                        override fun onConsoleMessage(msg: ConsoleMessage?): Boolean {
                            Log.i("TerminalWV", "${msg?.messageLevel()}: ${msg?.message()} [${msg?.sourceId()}:${msg?.lineNumber()}]")
                            return true
                        }
                    }

                    loadUrl(url)
                }
            },
            modifier = modifier
        )
    }
}
