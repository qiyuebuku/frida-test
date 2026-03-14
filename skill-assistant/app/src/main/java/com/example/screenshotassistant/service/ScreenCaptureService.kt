package com.example.screenshotassistant.service

import android.app.Activity
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import com.example.screenshotassistant.MainActivity
import com.example.screenshotassistant.R
import com.example.screenshotassistant.ScreenshotAssistantApp

class ScreenCaptureService : Service() {

    companion object {
        private const val TAG = "ScreenCaptureService"
        var instance: ScreenCaptureService? = null
            private set
    }

    val isReady: Boolean get() = virtualDisplay != null

    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var screenWidth = 0
    private var screenHeight = 0
    private var screenDensity = 0

    private var imageHandlerThread: HandlerThread? = null
    private var imageHandler: Handler? = null

    // 持续更新的最新帧
    @Volatile private var latestBitmap: Bitmap? = null
    @Volatile private var frameSeq = 0L
    private val frameLock = Object()

    override fun onCreate() {
        super.onCreate()
        instance = this

        val metrics = resources.displayMetrics
        screenWidth = metrics.widthPixels
        screenHeight = metrics.heightPixels
        screenDensity = metrics.densityDpi

        imageHandlerThread = HandlerThread("ImageReaderThread").also { it.start() }
        imageHandler = Handler(imageHandlerThread!!.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(2, createNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(2, createNotification())
        }

        intent?.let {
            val resultCode = it.getIntExtra("resultCode", Activity.RESULT_CANCELED)
            @Suppress("DEPRECATION")
            val data = it.getParcelableExtra<Intent>("data")
            if (resultCode != Activity.RESULT_CANCELED && data != null) {
                setupMediaProjection(resultCode, data)
            }
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        releaseCapture()
        mediaProjection?.stop()
        imageHandlerThread?.quitSafely()
    }

    private fun createNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, ScreenshotAssistantApp.CHANNEL_ID_CAPTURE)
            .setContentTitle("截屏服务")
            .setContentText("截屏服务运行中")
            .setSmallIcon(R.drawable.ic_skill_notify)
            .setContentIntent(pendingIntent)
            .build()
    }

    private fun setupMediaProjection(resultCode: Int, data: Intent) {
        val mpManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = mpManager.getMediaProjection(resultCode, data)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            mediaProjection?.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    Log.d(TAG, "MediaProjection stopped by system")
                    releaseCapture()
                }
            }, Handler(Looper.getMainLooper()))
        }

        createCapture()
        Log.d(TAG, "MediaProjection setup complete: ${screenWidth}x${screenHeight}")
    }

    /**
     * 创建 VirtualDisplay + ImageReader，持续接收帧。
     */
    private fun createCapture() {
        val mp = mediaProjection ?: return

        val reader = ImageReader.newInstance(
            screenWidth, screenHeight,
            PixelFormat.RGBA_8888, 4
        )

        reader.setOnImageAvailableListener({ r ->
            val image = r.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val planes = image.planes
                val buffer = planes[0].buffer
                val pixelStride = planes[0].pixelStride
                val rowStride = planes[0].rowStride
                val rowPadding = rowStride - pixelStride * screenWidth

                val bmpWidth = screenWidth + rowPadding / pixelStride
                val bitmap = Bitmap.createBitmap(bmpWidth, screenHeight, Bitmap.Config.ARGB_8888)
                bitmap.copyPixelsFromBuffer(buffer)

                val finalBitmap = if (rowPadding > 0) {
                    val cropped = Bitmap.createBitmap(bitmap, 0, 0, screenWidth, screenHeight)
                    bitmap.recycle()
                    cropped
                } else {
                    bitmap
                }

                synchronized(frameLock) {
                    latestBitmap?.recycle()
                    latestBitmap = finalBitmap
                    frameSeq++
                    frameLock.notifyAll()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error processing frame", e)
            } finally {
                image.close()
            }
        }, imageHandler)

        val vd = mp.createVirtualDisplay(
            "ScreenCapture",
            screenWidth, screenHeight, screenDensity,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface, null, null
        )

        imageReader = reader
        virtualDisplay = vd
        Log.d(TAG, "VirtualDisplay created")
    }

    private fun releaseCapture() {
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        synchronized(frameLock) {
            latestBitmap?.recycle()
            latestBitmap = null
            frameSeq = 0
        }
    }

    fun captureScreen(callback: (Bitmap) -> Unit) {
        Thread {
            val bitmap = captureFrame()
            if (bitmap != null) {
                Handler(Looper.getMainLooper()).post { callback(bitmap) }
            } else {
                Log.e(TAG, "captureScreen failed")
            }
        }.start()
    }

    /**
     * 截取当前屏幕：通过重建 ImageReader 强制获取最新帧。
     */
    suspend fun captureScreenSync(): Bitmap? {
        return kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            captureFrame()
        }
    }

    private fun captureFrame(): Bitmap? {
        val vd = virtualDisplay ?: return null

        // 方案：重建 ImageReader 并切换 surface，强制获取最新屏幕内容
        val newReader = ImageReader.newInstance(
            screenWidth, screenHeight,
            PixelFormat.RGBA_8888, 2
        )

        var result: Bitmap? = null
        var frameCount = 0
        val lock = Object()

        newReader.setOnImageAvailableListener({ r ->
            val image = r.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                frameCount++
                // 跳过第一帧（可能是 VirtualDisplay 缓存的旧帧），取第二帧
                // 如果等不到第二帧，也用第一帧
                val planes = image.planes
                val buffer = planes[0].buffer
                val pixelStride = planes[0].pixelStride
                val rowStride = planes[0].rowStride
                val rowPadding = rowStride - pixelStride * screenWidth

                val bmpWidth = screenWidth + rowPadding / pixelStride
                val bitmap = Bitmap.createBitmap(bmpWidth, screenHeight, Bitmap.Config.ARGB_8888)
                bitmap.copyPixelsFromBuffer(buffer)

                val finalBitmap = if (rowPadding > 0) {
                    val cropped = Bitmap.createBitmap(bitmap, 0, 0, screenWidth, screenHeight)
                    bitmap.recycle()
                    cropped
                } else {
                    bitmap
                }

                synchronized(lock) {
                    result?.recycle()
                    result = finalBitmap
                    lock.notifyAll()
                }
            } catch (e: Exception) {
                Log.e(TAG, "captureFrame error", e)
            } finally {
                image.close()
            }
        }, imageHandler)

        // 切换 surface
        vd.surface = newReader.surface

        // 等待帧（最多 2 秒）
        val deadline = System.currentTimeMillis() + 2000
        synchronized(lock) {
            while (result == null && System.currentTimeMillis() < deadline) {
                lock.wait(100)
            }
            // 如果拿到了第一帧，再等一小会看有没有第二帧
            if (result != null && frameCount == 1) {
                lock.wait(200) // 等 200ms 看有没有第二帧
            }
        }

        // 恢复原始 ImageReader 的 surface（保持持续帧接收）
        imageReader?.let { vd.surface = it.surface }
        newReader.close()

        if (result != null) {
            Log.d(TAG, "captureFrame: ${result!!.width}x${result!!.height} (frames=$frameCount)")
        } else {
            Log.w(TAG, "captureFrame: timeout")
        }

        return result
    }
}
