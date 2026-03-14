package com.example.screenshotassistant.ui.components

import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

@Composable
fun MarkdownViewer(markdown: String, modifier: Modifier = Modifier) {
    val isDark = isSystemInDarkTheme()
    val currentMarkdown = rememberUpdatedState(markdown)

    // 初始 HTML 模板（含 JS 增量更新函数）
    val initialHtml = remember(isDark) { buildShellHtml(isDark) }

    AndroidView(
        factory = { context ->
            WebView(context).apply {
                // 标记页面是否已加载完成
                tag = false // pageReady flag
                webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView?, url: String?) {
                        super.onPageFinished(view, url)
                        view?.tag = true
                        // 页面加载完成，立即推送当前内容
                        val md = currentMarkdown.value
                        if (md.isNotBlank()) {
                            val html = markdownToHtml(md)
                            val escaped = escapeForJs(html)
                            view?.evaluateJavascript("updateContent('$escaped')", null)
                        }
                    }
                }
                settings.javaScriptEnabled = true
                settings.defaultTextEncodingName = "utf-8"
                isHorizontalScrollBarEnabled = false
                setBackgroundColor(0)
                loadDataWithBaseURL(null, initialHtml, "text/html", "utf-8", null)
            }
        },
        update = { webView ->
            // 只在页面已加载完成后才用 JS 更新
            if (webView.tag == true) {
                val md = currentMarkdown.value
                val html = markdownToHtml(md)
                val escaped = escapeForJs(html)
                webView.evaluateJavascript("updateContent('$escaped')", null)
            }
        },
        modifier = modifier
    )
}

private fun escapeForJs(html: String): String {
    return html
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "")
}

private fun buildShellHtml(isDark: Boolean): String {
    val textColor = if (isDark) "#e0e0e0" else "#1a1a1a"
    val bgColor = if (isDark) "#1a1a1a" else "#ffffff"
    val codeBg = if (isDark) "#2d2d2d" else "#f5f5f5"
    val borderColor = if (isDark) "#444" else "#ddd"
    val linkColor = if (isDark) "#82b1ff" else "#1976d2"
    val blockquoteBorder = if (isDark) "#555" else "#ccc"
    val blockquoteBg = if (isDark) "#252525" else "#f9f9f9"

    return """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body {
            font-family: -apple-system, system-ui, sans-serif;
            font-size: 14px;
            line-height: 1.6;
            color: $textColor;
            background: $bgColor;
            padding: 16px;
            word-wrap: break-word;
            overflow-wrap: break-word;
            word-break: break-word;
            max-width: 100vw;
            overflow-x: hidden;
        }
        h1 { font-size: 20px; margin: 16px 0 8px; }
        h2 { font-size: 17px; margin: 14px 0 6px; }
        h3 { font-size: 15px; margin: 12px 0 4px; }
        p { margin: 6px 0; }
        ul, ol { padding-left: 20px; margin: 6px 0; }
        li { margin: 3px 0; }
        code {
            background: $codeBg;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 13px;
            font-family: monospace;
        }
        pre {
            background: $codeBg;
            padding: 12px;
            border-radius: 6px;
            overflow-x: hidden;
            white-space: pre-wrap;
            word-wrap: break-word;
            word-break: break-all;
            margin: 8px 0;
        }
        pre code { padding: 0; background: none; white-space: pre-wrap; word-break: break-all; }
        .table-wrap {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            margin: 8px 0;
        }
        table {
            border-collapse: collapse;
            font-size: 13px;
            white-space: nowrap;
        }
        th, td {
            border: 1px solid $borderColor;
            padding: 6px 10px;
            text-align: left;
            white-space: nowrap;
        }
        th { background: $codeBg; font-weight: 600; }
        blockquote {
            border-left: 3px solid $blockquoteBorder;
            background: $blockquoteBg;
            padding: 8px 12px;
            margin: 8px 0;
        }
        a { color: $linkColor; text-decoration: none; }
        hr { border: none; border-top: 1px solid $borderColor; margin: 12px 0; }
        strong { font-weight: 600; }
        em { font-style: italic; }
    </style>
    </head>
    <body>
    <div id="content"></div>
    <script>
    function updateContent(html) {
        document.getElementById('content').innerHTML = html;
        window.scrollTo(0, document.body.scrollHeight);
    }
    </script>
    </body>
    </html>
    """.trimIndent()
}

internal fun markdownToHtml(md: String): String {
    val lines = md.lines()
    val sb = StringBuilder()
    var inCodeBlock = false
    var inList = false
    var inOrderedList = false
    var inTable = false

    for (line in lines) {
        // Code block
        if (line.trimStart().startsWith("```")) {
            if (inCodeBlock) {
                sb.append("</code></pre>")
                inCodeBlock = false
            } else {
                closeList(sb, inList, inOrderedList, inTable).also {
                    inList = it.first; inOrderedList = it.second; inTable = it.third
                }
                sb.append("<pre><code>")
                inCodeBlock = true
            }
            continue
        }
        if (inCodeBlock) {
            sb.append(escapeHtml(line)).append("\n")
            continue
        }

        val trimmed = line.trim()

        // Empty line
        if (trimmed.isEmpty()) {
            closeList(sb, inList, inOrderedList, inTable).also {
                inList = it.first; inOrderedList = it.second; inTable = it.third
            }
            continue
        }

        // Table
        if (trimmed.contains("|") && trimmed.startsWith("|")) {
            val cells = trimmed.trim('|').split("|").map { it.trim() }
            if (cells.all { it.matches(Regex("[-:]+")) }) continue // separator row
            if (!inTable) {
                sb.append("<div class=\"table-wrap\"><table>")
                inTable = true
                sb.append("<tr>")
                cells.forEach { sb.append("<th>").append(inlineMarkdown(it)).append("</th>") }
                sb.append("</tr>")
            } else {
                sb.append("<tr>")
                cells.forEach { sb.append("<td>").append(inlineMarkdown(it)).append("</td>") }
                sb.append("</tr>")
            }
            continue
        }
        if (inTable) { sb.append("</table></div>"); inTable = false }

        // Headers
        when {
            trimmed.startsWith("### ") -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<h3>").append(inlineMarkdown(trimmed.removePrefix("### "))).append("</h3>")
            }
            trimmed.startsWith("## ") -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<h2>").append(inlineMarkdown(trimmed.removePrefix("## "))).append("</h2>")
            }
            trimmed.startsWith("# ") -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<h1>").append(inlineMarkdown(trimmed.removePrefix("# "))).append("</h1>")
            }
            trimmed.startsWith("---") || trimmed.startsWith("***") -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<hr>")
            }
            trimmed.startsWith("> ") -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<blockquote><p>").append(inlineMarkdown(trimmed.removePrefix("> "))).append("</p></blockquote>")
            }
            trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                if (!inList) { sb.append("<ul>"); inList = true }
                sb.append("<li>").append(inlineMarkdown(trimmed.drop(2))).append("</li>")
            }
            trimmed.matches(Regex("^\\d+\\.\\s.*")) -> {
                if (!inOrderedList) { sb.append("<ol>"); inOrderedList = true }
                val content = trimmed.replaceFirst(Regex("^\\d+\\.\\s"), "")
                sb.append("<li>").append(inlineMarkdown(content)).append("</li>")
            }
            else -> {
                closeList(sb, inList, inOrderedList, false).also { inList = it.first; inOrderedList = it.second }
                sb.append("<p>").append(inlineMarkdown(trimmed)).append("</p>")
            }
        }
    }

    if (inCodeBlock) sb.append("</code></pre>")
    closeList(sb, inList, inOrderedList, inTable)
    return sb.toString()
}

private fun closeList(
    sb: StringBuilder,
    inList: Boolean,
    inOrderedList: Boolean,
    inTable: Boolean
): Triple<Boolean, Boolean, Boolean> {
    if (inList) sb.append("</ul>")
    if (inOrderedList) sb.append("</ol>")
    if (inTable) sb.append("</table></div>")
    return Triple(false, false, false)
}

private fun inlineMarkdown(text: String): String {
    var result = escapeHtml(text)
    // Bold
    result = result.replace(Regex("\\*\\*(.+?)\\*\\*"), "<strong>$1</strong>")
    // Italic
    result = result.replace(Regex("\\*(.+?)\\*"), "<em>$1</em>")
    // Inline code
    result = result.replace(Regex("`(.+?)`"), "<code>$1</code>")
    // Links
    result = result.replace(Regex("\\[(.+?)\\]\\((.+?)\\)"), "<a href=\"$2\">$1</a>")
    return result
}

private fun escapeHtml(text: String): String {
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
}
