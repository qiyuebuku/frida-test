package com.example.screenshotassistant.network

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.Actions
import com.example.screenshotassistant.capture.CaptureType
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.service.ScreenCaptureService
import kotlinx.coroutines.*
import org.json.JSONObject

class CommandHandler(private val context: Context) {

    companion object {
        private const val TAG = "CommandHandler"
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val mainHandler = Handler(Looper.getMainLooper())

    fun handleCommand(command: ServerMessage.Command) {
        Log.d(TAG, "Handling command: ${command.action}")

        when (command.action) {
            "capture" -> handleCapture(command.params)
            "long_capture" -> handleLongCapture(command.params)
            "open_app" -> handleOpenApp(command.params)
            "scroll" -> handleScroll(command.params)
            "click" -> handleClick(command.params)
            "click_text" -> handleClickText(command.params)
            "wait" -> handleWait(command.params)
            "automation_sequence" -> handleAutomationSequence(command.params)
            else -> Log.w(TAG, "Unknown command: ${command.action}")
        }
    }

    fun handleResult(result: ServerMessage.Result) {
        Log.d(TAG, "Handling result: success=${result.success}, message=${result.message}")

        if (!result.success) {
            FloatingWindowService.instance?.showStatus("处理失败: ${result.message}", 5000)
            return
        }

        val data = result.data
        if (data is JSONObject) {
            val autoCopy = data.optBoolean("auto_copy", false)
            val text = data.optString("text", "")
            val reply = data.optString("reply", "")

            val contentToCopy = reply.ifEmpty { text }

            if (contentToCopy.isNotEmpty()) {
                if (autoCopy) {
                    copyToClipboard(contentToCopy)
                }
                // 在悬浮窗显示结果摘要
                val preview = if (contentToCopy.length > 80) contentToCopy.take(80) + "..." else contentToCopy
                val statusText = if (autoCopy) "已复制: $preview" else "结果: $preview"
                FloatingWindowService.instance?.showStatus(statusText, 8000)
            } else {
                FloatingWindowService.instance?.showStatus("处理完成（无文本内容）", 3000)
            }
        } else {
            FloatingWindowService.instance?.showStatus(result.message.ifEmpty { "处理完成" }, 3000)
        }
    }

    private fun handleCapture(params: JSONObject?) {
        val action = ActionConfig("remote_capture", "远程截屏", "camera", CaptureType.NORMAL, "远程触发截屏")
        FloatingWindowService.instance?.executeCapture(action) { bitmap ->
            val callbackAction = params?.optString("callback_action", "ocr") ?: "ocr"
            WebSocketClient.instance?.sendScreenshot(bitmap, callbackAction)
            showToast("截图已发送")
        }
    }

    private fun handleLongCapture(params: JSONObject?) {
        val action = ActionConfig("remote_long_capture", "远程长截屏", "camera", CaptureType.LONG_SCROLL, "远程触发长截屏")
        FloatingWindowService.instance?.executeCapture(action) { bitmap ->
            val callbackAction = params?.optString("callback_action", "full_page") ?: "full_page"
            WebSocketClient.instance?.sendScreenshot(bitmap, callbackAction)
            showToast("长截图已发送")
        }
    }

    private fun handleOpenApp(params: JSONObject?) {
        val packageName = params?.optString("package", "") ?: return
        if (packageName.isNotEmpty()) {
            ScreenAssistAccessibilityService.instance?.launchApp(packageName)
        }
    }

    private fun handleScroll(params: JSONObject?) {
        val direction = params?.optString("direction", "down") ?: "down"
        when (direction) {
            "down" -> ScreenAssistAccessibilityService.instance?.performScrollDown()
            "up" -> ScreenAssistAccessibilityService.instance?.performScrollUp()
        }
    }

    private fun handleClick(params: JSONObject?) {
        val x = params?.optDouble("x", 0.0)?.toFloat() ?: return
        val y = params?.optDouble("y", 0.0)?.toFloat() ?: return
        ScreenAssistAccessibilityService.instance?.clickAt(x, y)
    }

    private fun handleClickText(params: JSONObject?) {
        val text = params?.optString("text", "") ?: return
        if (text.isNotEmpty()) {
            ScreenAssistAccessibilityService.instance?.clickByText(text)
        }
    }

    private fun handleWait(params: JSONObject?) {
        // Wait is handled in automation sequence
    }

    private fun handleAutomationSequence(params: JSONObject?) {
        val sequenceArray = params?.optJSONArray("sequence") ?: return
        val callbackAction = params.optString("callback_action", "ocr")

        scope.launch {
            for (i in 0 until sequenceArray.length()) {
                val step = sequenceArray.getJSONObject(i)
                val action = step.getString("action")
                Log.d(TAG, "Executing step $i: $action")

                when (action) {
                    "open_app" -> {
                        val pkg = step.getString("package")
                        ScreenAssistAccessibilityService.instance?.launchApp(pkg)
                    }
                    "wait" -> {
                        val duration = step.optLong("duration", 1000)
                        delay(duration)
                    }
                    "click_text" -> {
                        val text = step.getString("text")
                        ScreenAssistAccessibilityService.instance?.clickByText(text)
                    }
                    "click" -> {
                        val x = step.getDouble("x").toFloat()
                        val y = step.getDouble("y").toFloat()
                        ScreenAssistAccessibilityService.instance?.clickAt(x, y)
                    }
                    "scroll" -> {
                        val direction = step.optString("direction", "down")
                        when (direction) {
                            "down" -> ScreenAssistAccessibilityService.instance?.performScrollDown()
                            "up" -> ScreenAssistAccessibilityService.instance?.performScrollUp()
                        }
                    }
                    "capture" -> {
                        val captureAction = ActionConfig("auto_capture", "自动截屏", "camera", CaptureType.NORMAL, "")
                        FloatingWindowService.instance?.executeCapture(captureAction) { bitmap ->
                            WebSocketClient.instance?.sendScreenshot(bitmap, callbackAction)
                        }
                    }
                    "long_capture" -> {
                        val captureAction = ActionConfig("auto_long_capture", "自动长截屏", "camera", CaptureType.LONG_SCROLL, "")
                        FloatingWindowService.instance?.executeCapture(captureAction) { bitmap ->
                            WebSocketClient.instance?.sendScreenshot(bitmap, callbackAction)
                        }
                        // 长截屏需要更多时间
                        delay(step.optLong("max_scrolls", 10) * 600L)
                    }
                }

                // 每个步骤之间的默认间隔
                if (action != "wait") {
                    delay(300)
                }
            }

            showToast("自动化序列执行完成")
            WebSocketClient.instance?.sendStatus("completed", "Automation sequence completed")
        }
    }

    private fun copyToClipboard(text: String) {
        mainHandler.post {
            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = ClipData.newPlainText("技能助手", text)
            clipboard.setPrimaryClip(clip)
        }
    }

    private fun showToast(message: String) {
        mainHandler.post {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }
}
