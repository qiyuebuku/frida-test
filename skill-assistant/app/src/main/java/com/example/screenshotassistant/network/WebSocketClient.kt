package com.example.screenshotassistant.network

import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.*
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

class WebSocketClient(private val serverUrl: String) {

    companion object {
        private const val TAG = "WebSocketClient"
        var instance: WebSocketClient? = null
            private set

        fun create(url: String): WebSocketClient {
            return WebSocketClient(url).also { instance = it }
        }
    }

    private var webSocket: WebSocket? = null
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState = _connectionState.asStateFlow()

    private val _messages = MutableSharedFlow<ServerMessage>()
    val messages = _messages.asSharedFlow()

    private var reconnectJob: Job? = null
    private var reconnectAttempts = 0
    private var manualDisconnect = false

    enum class ConnectionState {
        DISCONNECTED, CONNECTING, CONNECTED
    }

    fun connect() {
        if (_connectionState.value == ConnectionState.CONNECTING) return
        manualDisconnect = false
        _connectionState.value = ConnectionState.CONNECTING

        val request = Request.Builder()
            .url(serverUrl)
            .build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.d(TAG, "WebSocket connected")
                _connectionState.value = ConnectionState.CONNECTED
                reconnectAttempts = 0
                sendStatus("ready", "Client connected")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                scope.launch {
                    try {
                        val json = JSONObject(text)
                        val message = parseMessage(json)
                        if (message != null) {
                            _messages.emit(message)
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Failed to parse message: $text", e)
                    }
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.d(TAG, "WebSocket closing: $reason")
                webSocket.close(1000, null)
                _connectionState.value = ConnectionState.DISCONNECTED
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket failure", t)
                _connectionState.value = ConnectionState.DISCONNECTED
                scheduleReconnect()
            }
        })
    }

    fun disconnect() {
        manualDisconnect = true
        reconnectJob?.cancel()
        webSocket?.close(1000, "Client disconnect")
        _connectionState.value = ConnectionState.DISCONNECTED
    }

    fun sendScreenshot(bitmap: Bitmap, action: String) {
        val base64 = bitmapToBase64(bitmap)
        val json = JSONObject().apply {
            put("type", "screenshot")
            put("imageBase64", base64)
            put("action", action)
            put("timestamp", System.currentTimeMillis())
        }
        webSocket?.send(json.toString())
    }

    fun sendStatus(status: String, message: String) {
        val json = JSONObject().apply {
            put("type", "status")
            put("status", status)
            put("message", message)
        }
        webSocket?.send(json.toString())
    }

    private fun parseMessage(json: JSONObject): ServerMessage? {
        return when (json.optString("type")) {
            "command" -> ServerMessage.Command(
                action = json.getString("action"),
                params = json.optJSONObject("params")
            )
            "result" -> ServerMessage.Result(
                success = json.getBoolean("success"),
                data = json.opt("data"),
                message = json.optString("message", "")
            )
            else -> null
        }
    }

    private fun scheduleReconnect() {
        if (manualDisconnect) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val delayMs = minOf(3000L * (1 shl minOf(reconnectAttempts, 4)), 30000L)
            reconnectAttempts++
            Log.d(TAG, "Reconnecting in ${delayMs}ms (attempt $reconnectAttempts)...")
            delay(delayMs)
            connect()
        }
    }

    private fun bitmapToBase64(bitmap: Bitmap): String {
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, stream)
        return Base64.encodeToString(stream.toByteArray(), Base64.NO_WRAP)
    }
}

sealed class ServerMessage {
    data class Command(
        val action: String,
        val params: JSONObject?
    ) : ServerMessage()

    data class Result(
        val success: Boolean,
        val data: Any?,
        val message: String
    ) : ServerMessage()
}
