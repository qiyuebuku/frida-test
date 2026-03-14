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
import androidx.compose.foundation.layout.*
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailScreen(taskId: Int, onBack: () -> Unit) {
    var task by remember { mutableStateOf<TaskItem?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    var inputText by remember { mutableStateOf("") }
    var isSending by remember { mutableStateOf(false) }

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

    // 轮询任务状态（处理中时每 3 秒刷新）
    LaunchedEffect(taskId, task?.isProcessing) {
        if (task?.isProcessing != true) return@LaunchedEffect
        while (true) {
            delay(3000)
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTask(taskId)?.let { loaded ->
                    withContext(Dispatchers.Main) { task = loaded }
                }
            }
            if (task?.isProcessing != true) break
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
            CliTopBar(task = task, onBack = onBack)
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

        // 终端 WebView
        val serverUrl = HttpClient.instance?.serverUrl ?: ""
        val wsHost = serverUrl
            .removePrefix("http://")
            .removePrefix("https://")
            .trimEnd('/')

        TerminalWebView(
            taskId = taskId,
            wsHost = wsHost,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .background(CliBg)
        )
    }
}


// ==================== 顶栏 ====================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CliTopBar(
    task: TaskItem?,
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
                            if (task.isProcessing) append(" ${task.progress}%")
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
                    placeholder = {
                        Text(
                            if (task.isProcessing) "发送消息（将排队等待处理）" else "追问...",
                            color = CliDimText,
                            fontSize = 12.sp
                        )
                    },
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
            // 停止按钮（处理中显示，完成后占位保持高度一致）
            if (task.isProcessing) {
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
            } else {
                Spacer(modifier = Modifier.height(36.dp))
            }
        }
    }
}


// ==================== 工具函数 ====================

private fun extractTime(timestamp: String): String {
    val regex = Regex("""[\sT](\d{2}:\d{2})""")
    return regex.find(timestamp)?.groupValues?.get(1) ?: timestamp.takeLast(8).take(5)
}

private fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("task_result", text))
    Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show()
}


// ==================== 终端 WebView ====================

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TerminalWebView(
    taskId: Int,
    wsHost: String,
    modifier: Modifier = Modifier
) {
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

                val url = "http://$wsHost/terminal/$taskId"
                loadUrl(url)
            }
        },
        modifier = modifier
    )
}
