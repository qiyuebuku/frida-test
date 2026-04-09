package com.example.screenshotassistant.ui.screens

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.zIndex
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.example.screenshotassistant.data.StepItem
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.ui.components.StepsView
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

    // 步骤模式 vs 终端模式（步骤模式默认）
    var isTerminalMode by remember { mutableStateOf(false) }
    var steps by remember { mutableStateOf<List<StepItem>>(emptyList()) }
    var stepsProgress by remember { mutableStateOf(0) }
    var stepsProgressMsg by remember { mutableStateOf<String?>(null) }

    // 步骤区域是否收起
    var stepsCollapsed by remember { mutableStateOf(false) }

    // 任务完成/已完成时自动收起步骤
    LaunchedEffect(task?.isProcessing) {
        val current = task?.isProcessing
        if (current == false) {
            stepsCollapsed = true
        }
    }

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

    // 轮询步骤数据（步骤模式时）
    LaunchedEffect(taskId, isTerminalMode) {
        if (isTerminalMode) return@LaunchedEffect
        var doneRetries = 0
        while (true) {
            withContext(Dispatchers.IO) {
                val result = HttpClient.instance?.getTaskSteps(taskId)
                Log.d("TaskDetail", "[steps-poll] taskId=$taskId serverSteps=${result?.steps?.size} localSteps=${steps.size} isProcessing=${task?.isProcessing} doneRetries=$doneRetries")
                if (result != null) {
                    withContext(Dispatchers.Main) {
                        // 已有步骤数据时不允许被空数据覆盖
                        if (result.steps.isNotEmpty() || steps.isEmpty()) {
                            steps = result.steps
                        }
                        stepsProgress = result.progress
                        stepsProgressMsg = result.progressMsg
                    }
                }
            }
            // 任务完成后最多再轮询 3 次
            if (task?.isProcessing != true) {
                doneRetries++
                if (doneRetries >= 3) break
            }
            delay(2000)
        }
        Log.d("TaskDetail", "[steps-poll] stopped. finalSteps=${steps.size}")
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
            if (t != null && !isExpanded && t.hasTerminal && isTerminalMode) {
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
                isTerminalMode = isTerminalMode,
                hasTerminal = task?.hasTerminal == true,
                onToggleMode = { isTerminalMode = !isTerminalMode },
                isStopping = isStopping,
                onStop = {
                    isStopping = true
                    scope.launch(Dispatchers.IO) {
                        HttpClient.instance?.stopTask(taskId)
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
            // 终端模式（仅有终端的任务可切换）
            isTerminalMode && t.hasTerminal -> {
                val terminalPath = if (isExpanded) "/terminal/$taskId/expanded" else "/terminal/$taskId"
                TerminalWebView(
                    url = "http://$wsHost$terminalPath",
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding)
                        .background(CliBg)
                )
            }
            // 默认：步骤 + 结果视图（所有任务都显示）
            else -> {
                val resultText = t.result ?: t.summary
                val hasResult = !resultText.isNullOrBlank() && !t.isProcessing
                val hasSteps = steps.isNotEmpty() || t.isProcessing

                Log.d("TaskDetail", "hasSteps=$hasSteps hasResult=$hasResult stepsSize=${steps.size} stepsCollapsed=$stepsCollapsed isProcessing=${t.isProcessing} resultLen=${resultText?.length}")

                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding)
                        .background(CliBg)
                ) {
                    // 步骤区域（zIndex 确保不被 WebView 覆盖）
                    if (hasSteps) {
                        // 步骤标题栏（可收起）
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .zIndex(1f)
                                .clickable { stepsCollapsed = !stepsCollapsed }
                                .background(CliSurfaceDim)
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Default.ListAlt, null, tint = CliBlue, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(6.dp))
                            Text("执行步骤 (${steps.size})", color = CliText, fontSize = 13.sp,
                                fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                            Icon(
                                if (stepsCollapsed) Icons.Default.ExpandMore else Icons.Default.ExpandLess,
                                null, tint = CliDimText, modifier = Modifier.size(18.dp)
                            )
                        }
                        // 展开时才渲染，且必须有内容（步骤或进度）
                        if (!stepsCollapsed && (steps.isNotEmpty() || t.isProcessing)) {
                            val stepsModifier = if (hasResult) {
                                Modifier.fillMaxWidth().zIndex(1f).weight(9f)
                            } else {
                                Modifier.fillMaxWidth().weight(1f)
                            }
                            StepsView(
                                steps = steps, progress = stepsProgress,
                                progressMsg = stepsProgressMsg, isProcessing = t.isProcessing,
                                modifier = stepsModifier
                            )
                        }
                    }

                    // 结果区域
                    if (hasResult) {
                        if (hasSteps) {
                            HorizontalDivider(color = CliSurfaceDim, thickness = 1.dp)
                        }
                        ResultHeader(t.isFailed)
                        // 步骤展开时结果占 10%，收起时占满
                        val resultWeight = if (hasSteps && !stepsCollapsed) 1f else 1f
                        MarkdownResultView(
                            markdown = resultText!!,
                            modifier = Modifier.fillMaxWidth().weight(resultWeight).clipToBounds()
                        )
                    }

                    // 无步骤也无结果
                    if (!hasSteps && !hasResult) {
                        Box(
                            modifier = Modifier.fillMaxWidth().weight(1f),
                            contentAlignment = Alignment.Center
                        ) {
                            if (t.isProcessing) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    CircularProgressIndicator(color = CliBlue, modifier = Modifier.size(32.dp))
                                    Spacer(modifier = Modifier.height(8.dp))
                                    Text("等待处理...", color = CliDimText, fontSize = 13.sp)
                                }
                            } else {
                                Text("暂无数据", color = CliDimText, fontSize = 13.sp)
                            }
                        }
                    }
                }
            }
        }
    }
}


// ==================== 结果标题栏 ====================
@Composable
private fun ResultHeader(isFailed: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = if (isFailed) Icons.Default.ErrorOutline else Icons.Default.Description,
            contentDescription = null,
            tint = if (isFailed) CliRed else CliGreen,
            modifier = Modifier.size(16.dp)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            if (isFailed) "错误信息" else "执行结果",
            color = CliText, fontSize = 13.sp, fontWeight = FontWeight.Medium
        )
    }
}

// ==================== Markdown 结果视图 ====================

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun MarkdownResultView(
    markdown: String,
    modifier: Modifier = Modifier
) {
    // Base64 编码避免 JS 字符串注入问题（HTML 标签中的 </ 会破坏 script）
    val b64 = Base64.encodeToString(markdown.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

    val html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body {
        background: #1a1a1a;
        color: #d4d4d4;
        font-family: -apple-system, system-ui, sans-serif;
        font-size: 13px;
        line-height: 1.6;
        padding: 0 12px 16px 12px;
        word-wrap: break-word;
        -webkit-text-size-adjust: none;
      }
      h1, h2, h3, h4 {
        color: #e0e0e0;
        margin: 12px 0 6px 0;
        font-weight: 600;
      }
      h1 { font-size: 18px; border-bottom: 1px solid #333; padding-bottom: 4px; }
      h2 { font-size: 16px; border-bottom: 1px solid #2a2a2a; padding-bottom: 3px; }
      h3 { font-size: 14px; }
      p { margin: 4px 0; }
      ul, ol { padding-left: 20px; margin: 4px 0; }
      li { margin: 2px 0; }
      code {
        background: #2a2a2a;
        color: #e06c75;
        padding: 1px 4px;
        border-radius: 3px;
        font-family: monospace;
        font-size: 12px;
      }
      pre {
        background: #232323;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 8px;
        margin: 6px 0;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }
      pre code { background: none; color: #d4d4d4; padding: 0; }
      blockquote {
        border-left: 3px solid #5b9cf5;
        padding-left: 10px;
        color: #8b8b8b;
        margin: 6px 0;
      }
      table {
        border-collapse: collapse;
        width: 100%;
        margin: 6px 0;
        font-size: 12px;
      }
      th, td {
        border: 1px solid #333;
        padding: 4px 8px;
        text-align: left;
      }
      th { background: #232323; color: #e0e0e0; font-weight: 600; }
      tr:nth-child(even) { background: #1e1e1e; }
      a { color: #5b9cf5; text-decoration: none; }
      strong { color: #e0e0e0; }
      em { color: #b0b0b0; }
      hr { border: none; border-top: 1px solid #333; margin: 8px 0; }
      img { max-width: 100%; border-radius: 4px; }
    </style>
    </head>
    <body>
    <div id="content"></div>
    <script>
    function decodeBase64(s) {
      return decodeURIComponent(escape(atob(s)));
    }
    function renderMarkdown(md) {
      // 如果内容已经含有 HTML 标签（如 <table>），直接作为 HTML 渲染
      if (md.indexOf('<table') !== -1 || md.indexOf('<div') !== -1) {
        // 混合模式：处理 Markdown 标记但保留 HTML
        var html = md;
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
          return '<pre><code>' + code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').trim() + '</code></pre>';
        });
        html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        html = html.replace(/^---+$/gm, '<hr>');
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\n{2,}/g, '<br><br>');
        html = html.replace(/\n/g, '<br>');
        return html;
      }
      var html = md;
      html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
        return '<pre><code>' + code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').trim() + '</code></pre>';
      });
      html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
      html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
      html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
      html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
      html = html.replace(/^---+$/gm, '<hr>');
      html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
      html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
      html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
      html = html.replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)*)/gm, function(m, header, sep, body) {
        var cols = header.split('|').filter(function(c){return c.trim();});
        var rows = body.trim().split('\n');
        var t = '<table><thead><tr>';
        cols.forEach(function(c){t += '<th>' + c.trim() + '</th>';});
        t += '</tr></thead><tbody>';
        rows.forEach(function(r){
          var cells = r.split('|').filter(function(c){return c.trim()!=='';});
          t += '<tr>';
          cells.forEach(function(c){t += '<td>' + c.trim() + '</td>';});
          t += '</tr>';
        });
        t += '</tbody></table>';
        return t;
      });
      html = html.replace(/^[\*\-] (.+)$/gm, '<li>$1</li>');
      html = html.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, '<ul>$&</ul>');
      html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
      html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
      html = html.replace(/^(?!<[hupboltd]|<\/|<li|<hr|<blockquote|<table|<thead|<tbody|<tr|<ul|<ol)(.+)$/gm, '<p>$1</p>');
      html = html.replace(/\n{2,}/g, '\n');
      return html;
    }
    var md = decodeBase64('""" + b64 + """');
    document.getElementById('content').innerHTML = renderMarkdown(md);
    </script>
    </body>
    </html>
    """.trimIndent()

    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                setBackgroundColor(android.graphics.Color.parseColor("#1a1a1a"))
                webViewClient = WebViewClient()
                loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
            }
        },
        update = { webView ->
            webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        },
        modifier = modifier
    )
}


// ==================== 顶栏 ====================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CliTopBar(
    task: TaskItem?,
    onBack: () -> Unit,
    isExpanded: Boolean,
    onToggleExpand: () -> Unit,
    isTerminalMode: Boolean,
    hasTerminal: Boolean,
    onToggleMode: () -> Unit,
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
                        Text(
                            task.typeLabel,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color = CliText
                        )
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
                // 停止按钮（处理中显示）
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
                // 查看全量数据（终端模式 + 有展开日志时显示）
                if (task != null && task.hasExpandedLog && !task.isProcessing && isTerminalMode) {
                    IconButton(onClick = onToggleExpand) {
                        Icon(
                            if (isExpanded) Icons.Default.UnfoldLess else Icons.Default.UnfoldMore,
                            contentDescription = if (isExpanded) "折叠" else "展开",
                            tint = if (isExpanded) CliBlue else CliDimText,
                            modifier = Modifier.size(22.dp)
                        )
                    }
                }
                // 复制结果按钮（任务完成时显示）
                if (task != null && !task.result.isNullOrBlank()) {
                    IconButton(onClick = onCopy) {
                        Icon(
                            Icons.Default.ContentCopy,
                            contentDescription = "复制",
                            tint = CliDimText,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                }
                // 步骤/终端 模式切换（最右侧，位置固定）
                if (hasTerminal) {
                    IconButton(onClick = onToggleMode) {
                        Icon(
                            if (isTerminalMode) Icons.Default.ViewList else Icons.Default.Terminal,
                            contentDescription = if (isTerminalMode) "步骤模式" else "终端模式",
                            tint = if (isTerminalMode) CliBlue else CliDimText,
                            modifier = Modifier.size(20.dp)
                        )
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


// ==================== 终端 WebView ====================

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TerminalWebView(
    url: String,
    modifier: Modifier = Modifier
) {
    var webViewRef by remember { mutableStateOf<WebView?>(null) }

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
                    webViewRef = this
                }
            },
            modifier = modifier
        )
    }

    DisposableEffect(url) {
        onDispose {
            webViewRef?.loadUrl("about:blank")
            webViewRef?.destroy()
            webViewRef = null
            Log.i("TerminalWV", "WebView destroyed, WS closed")
        }
    }
}
