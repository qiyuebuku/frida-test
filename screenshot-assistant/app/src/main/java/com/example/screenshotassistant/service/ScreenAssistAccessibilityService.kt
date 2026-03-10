package com.example.screenshotassistant.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

class ScreenAssistAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "ScreenAssistA11y"
        var instance: ScreenAssistAccessibilityService? = null
            private set
    }

    var onScrollDetected: (() -> Unit)? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.d(TAG, "onServiceConnected: instance set")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
            Log.d(TAG, "TYPE_VIEW_SCROLLED detected, onScrollDetected=${onScrollDetected != null}")
            onScrollDetected?.invoke()
        }
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        super.onDestroy()
        instance = null
    }

    fun performScrollDown() {
        val displayMetrics = resources.displayMetrics
        val centerX = displayMetrics.widthPixels / 2f
        // 适中滚动：从 75% 到 25%（50%屏幕高度），避免过度滚动弹回
        val startY = displayMetrics.heightPixels * 0.75f
        val endY = displayMetrics.heightPixels * 0.25f
        Log.d(TAG, "performScrollDown: gesture ($centerX, $startY) -> ($centerX, $endY), screen=${displayMetrics.widthPixels}x${displayMetrics.heightPixels}")

        val result = performSwipe(centerX, startY, centerX, endY, 800)
        Log.d(TAG, "performScrollDown: dispatchGesture result=$result")
    }

    fun performScrollUp() {
        val displayMetrics = resources.displayMetrics
        val centerX = displayMetrics.widthPixels / 2f
        val startY = displayMetrics.heightPixels * 0.3f
        val endY = displayMetrics.heightPixels * 0.7f
        performSwipe(centerX, startY, centerX, endY)
    }

    fun clickAt(x: Float, y: Float) {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 50))
            .build()
        dispatchGesture(gesture, null, null)
    }

    fun clickByText(text: String): Boolean {
        val rootNode = rootInActiveWindow ?: return false
        val nodes = rootNode.findAccessibilityNodeInfosByText(text)
        nodes.firstOrNull()?.let { node ->
            node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK)
            return true
        }
        return false
    }

    suspend fun takeScreenshot(): Bitmap? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            Log.e(TAG, "takeScreenshot requires Android 11+")
            return null
        }
        return try {
            suspendCoroutine { cont ->
                takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor,
                    object : TakeScreenshotCallback {
                        override fun onSuccess(result: ScreenshotResult) {
                            val hardwareBitmap = Bitmap.wrapHardwareBuffer(
                                result.hardwareBuffer, result.colorSpace
                            )
                            result.hardwareBuffer.close()
                            val bitmap = hardwareBitmap?.copy(Bitmap.Config.ARGB_8888, false)
                            hardwareBitmap?.recycle()
                            Log.d(TAG, "takeScreenshot success: ${bitmap?.width}x${bitmap?.height}")
                            cont.resume(bitmap)
                        }
                        override fun onFailure(errorCode: Int) {
                            Log.e(TAG, "takeScreenshot failed: errorCode=$errorCode")
                            cont.resume(null)
                        }
                    })
            }
        } catch (e: SecurityException) {
            Log.e(TAG, "takeScreenshot SecurityException: ${e.message}")
            null
        } catch (e: Exception) {
            Log.e(TAG, "takeScreenshot exception: ${e.message}")
            null
        }
    }

    fun launchApp(packageName: String) {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        intent?.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }

    private fun performSwipe(startX: Float, startY: Float, endX: Float, endY: Float, duration: Long = 300): Boolean {
        val path = Path().apply {
            moveTo(startX, startY)
            lineTo(endX, endY)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, duration))
            .build()
        return dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                Log.d(TAG, "gesture completed")
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                Log.w(TAG, "gesture cancelled")
            }
        }, null)
    }
}
