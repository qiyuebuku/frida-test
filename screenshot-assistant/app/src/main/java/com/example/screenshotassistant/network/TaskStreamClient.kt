package com.example.screenshotassistant.network

import android.util.Log
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * SSE 客户端 - 连接 /api/tasks/{id}/stream 实时接收任务事件
 */
class TaskStreamClient(private val baseUrl: String) {

    companion object {
        private const val TAG = "TaskStreamClient"
    }

    fun connect(
        taskId: Int,
        scope: CoroutineScope,
        onToolCall: (display: String, detail: String, progress: Int, isText: Boolean) -> Unit,
        onToolResult: (display: String, output: String, isError: Boolean) -> Unit = { _, _, _ -> },
        onTextDelta: (text: String, totalLen: Int, progress: Int) -> Unit,
        onDone: (status: String, result: String?, errorMsg: String?) -> Unit,
        onError: (Exception) -> Unit,
    ): Job {
        return scope.launch(Dispatchers.IO) {
            try {
                val url = URL("$baseUrl/api/tasks/$taskId/stream")
                val conn = url.openConnection() as HttpURLConnection
                conn.setRequestProperty("Accept", "text/event-stream")
                conn.connectTimeout = 10000
                conn.readTimeout = 0  // 无超时，持续读取

                if (conn.responseCode != 200) {
                    withContext(Dispatchers.Main) {
                        onError(Exception("SSE 连接失败: HTTP ${conn.responseCode}"))
                    }
                    return@launch
                }

                val reader = BufferedReader(InputStreamReader(conn.inputStream))
                var line: String?

                while (isActive) {
                    line = reader.readLine() ?: break
                    if (!line.startsWith("data: ")) continue

                    try {
                        val json = JSONObject(line.removePrefix("data: "))
                        val type = json.getString("type")

                        withContext(Dispatchers.Main) {
                            when (type) {
                                "tool_call" -> onToolCall(
                                    json.getString("display"),
                                    json.optString("detail", ""),
                                    json.getInt("progress"),
                                    json.optBoolean("is_text", false)
                                )
                                "tool_result" -> onToolResult(
                                    json.optString("display", ""),
                                    json.optString("output", ""),
                                    json.optBoolean("is_error", false)
                                )
                                "text_delta" -> onTextDelta(
                                    json.getString("text"),
                                    json.getInt("total_len"),
                                    json.getInt("progress")
                                )
                                "done" -> {
                                    val result = if (json.has("result") && !json.isNull("result"))
                                        json.getString("result") else null
                                    val errorMsg = if (json.has("error_msg") && !json.isNull("error_msg"))
                                        json.getString("error_msg") else null
                                    onDone(
                                        json.getString("status"),
                                        result,
                                        errorMsg
                                    )
                                    cancel()
                                }
                            }
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "Parse SSE event failed: $line", e)
                    }
                }

                reader.close()
                conn.disconnect()
            } catch (e: CancellationException) {
                // 正常取消
            } catch (e: Exception) {
                if (isActive) {
                    Log.e(TAG, "SSE error", e)
                    withContext(Dispatchers.Main) { onError(e) }
                }
            }
        }
    }
}
