package com.example.screenshotassistant.network

import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import com.example.screenshotassistant.data.TaskItem
import org.json.JSONArray
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

class HttpClient(private val serverUrl: String) {

    companion object {
        private const val TAG = "HttpClient"
        var instance: HttpClient? = null
            private set

        fun create(url: String): HttpClient {
            return HttpClient(url).also { instance = it }
        }

        fun destroy() {
            instance = null
        }
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * 发送截图到服务端进行 OCR + AI 结构化处理。
     * 通过 SSE 流式读取进度，onProgress 回调更新状态。
     * 需要在 IO 线程调用。
     */
    fun sendScreenshot(
        bitmap: Bitmap,
        action: String,
        onProgress: ((String) -> Unit)? = null
    ): ServerMessage.Result? {
        return try {
            val base64 = bitmapToBase64(bitmap)
            val json = JSONObject().apply {
                put("type", "screenshot")
                put("imageBase64", base64)
                put("action", action)
                put("timestamp", System.currentTimeMillis())
            }

            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$serverUrl/api/screenshot")
                .post(body)
                .header("X-Client-Id", "android")
                .build()

            Log.d(TAG, "Sending screenshot, action=$action, size=${base64.length} chars")
            val response = client.newCall(request).execute()

            if (!response.isSuccessful) {
                Log.e(TAG, "HTTP error: ${response.code} ${response.message}")
                return ServerMessage.Result(
                    success = false,
                    data = null,
                    message = "HTTP ${response.code}: ${response.message}"
                )
            }

            val contentType = response.header("Content-Type", "")
            if (contentType?.contains("text/event-stream") == true) {
                // SSE 流式读取
                parseSseResponse(response, onProgress)
            } else {
                // 兼容旧的 JSON 响应
                val responseBody = response.body?.string() ?: "{}"
                val responseJson = JSONObject(responseBody)
                ServerMessage.Result(
                    success = responseJson.optBoolean("success", false),
                    data = responseJson.optJSONObject("data"),
                    message = responseJson.optString("message", "")
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "Request failed", e)
            ServerMessage.Result(
                success = false,
                data = null,
                message = "请求失败: ${e.message}"
            )
        }
    }

    private fun parseSseResponse(
        response: okhttp3.Response,
        onProgress: ((String) -> Unit)?
    ): ServerMessage.Result {
        var resultData: JSONObject? = null

        response.body?.byteStream()?.let { stream ->
            val reader = BufferedReader(InputStreamReader(stream, Charsets.UTF_8))
            var currentEvent = ""
            var currentData = ""

            reader.forEachLine { line ->
                when {
                    line.startsWith("event: ") -> {
                        currentEvent = line.removePrefix("event: ").trim()
                    }
                    line.startsWith("data: ") -> {
                        currentData = line.removePrefix("data: ").trim()
                    }
                    line.isBlank() && currentEvent.isNotEmpty() -> {
                        // 空行表示一个事件结束
                        try {
                            val eventJson = JSONObject(currentData)
                            when (currentEvent) {
                                "progress" -> {
                                    val message = eventJson.optString("message", "")
                                    Log.d(TAG, "SSE progress: $message")
                                    onProgress?.invoke(message)
                                }
                                "result" -> {
                                    Log.d(TAG, "SSE result received")
                                    resultData = eventJson
                                }
                            }
                        } catch (e: Exception) {
                            Log.w(TAG, "Failed to parse SSE event: $currentData")
                        }
                        currentEvent = ""
                        currentData = ""
                    }
                }
            }
        }

        return if (resultData != null) {
            ServerMessage.Result(
                success = true,
                data = resultData,
                message = "处理完成"
            )
        } else {
            ServerMessage.Result(
                success = false,
                data = null,
                message = "未收到处理结果"
            )
        }
    }

    /**
     * 创建异步任务（截图发送到服务端异步处理）
     */
    fun createTask(bitmap: Bitmap, action: String): Int? {
        return try {
            val base64 = bitmapToBase64(bitmap)
            val json = JSONObject().apply {
                put("imageBase64", base64)
                put("action", action)
                put("client_id", "android")
            }
            postTask(json)
        } catch (e: Exception) {
            Log.e(TAG, "Create task error", e)
            null
        }
    }

    /**
     * 创建非截屏类异步任务（如每日决策、持仓审视）
     */
    fun createCommandTask(taskType: String): Int? {
        return try {
            val json = JSONObject().apply {
                put("task_type", taskType)
                put("client_id", "android")
            }
            postTask(json)
        } catch (e: Exception) {
            Log.e(TAG, "Create command task error", e)
            null
        }
    }

    private fun postTask(json: JSONObject): Int? {
        val body = json.toString().toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url("$serverUrl/api/tasks")
            .post(body)
            .build()

        Log.d(TAG, "Creating async task: ${json.optString("task_type", json.optString("action"))}")
        val response = client.newCall(request).execute()

        return if (response.isSuccessful) {
            val respJson = JSONObject(response.body?.string() ?: "{}")
            val taskId = respJson.optInt("task_id", -1)
            if (taskId > 0) taskId else null
        } else {
            Log.e(TAG, "Create task failed: ${response.code}")
            null
        }
    }

    /**
     * 获取任务列表
     */
    fun getTasks(
        status: String? = null,
        taskType: String? = null,
        limit: Int = 20,
        offset: Int = 0
    ): Pair<List<TaskItem>, Int>? {
        return try {
            val url = StringBuilder("$serverUrl/api/tasks?limit=$limit&offset=$offset")
            if (status != null) url.append("&status=$status")
            if (taskType != null) url.append("&task_type=$taskType")

            val request = Request.Builder().url(url.toString()).get().build()
            val response = client.newCall(request).execute()

            if (response.isSuccessful) {
                val respJson = JSONObject(response.body?.string() ?: "{}")
                val data = respJson.optJSONObject("data") ?: respJson
                val items = data.optJSONArray("items") ?: JSONArray()
                val total = data.optInt("total", 0)
                val tasks = (0 until items.length()).map { i ->
                    TaskItem.fromJson(items.getJSONObject(i))
                }
                Pair(tasks, total)
            } else null
        } catch (e: Exception) {
            Log.e(TAG, "Get tasks error", e)
            null
        }
    }

    /**
     * 获取任务详情
     */
    fun getTask(taskId: Int): TaskItem? {
        return try {
            val request = Request.Builder()
                .url("$serverUrl/api/tasks/$taskId")
                .get()
                .build()
            val response = client.newCall(request).execute()

            if (response.isSuccessful) {
                val respJson = JSONObject(response.body?.string() ?: "{}")
                val data = respJson.optJSONObject("data") ?: respJson
                TaskItem.fromJson(data)
            } else null
        } catch (e: Exception) {
            Log.e(TAG, "Get task error", e)
            null
        }
    }

    /**
     * 检查服务端是否可用
     */
    fun healthCheck(): Boolean {
        return try {
            val request = Request.Builder()
                .url("$serverUrl/health")
                .get()
                .build()
            val response = client.newCall(request).execute()
            response.isSuccessful
        } catch (e: Exception) {
            false
        }
    }

    private fun bitmapToBase64(bitmap: Bitmap): String {
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, stream)
        return Base64.encodeToString(stream.toByteArray(), Base64.NO_WRAP)
    }
}
