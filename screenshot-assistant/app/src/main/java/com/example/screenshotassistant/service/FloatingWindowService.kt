package com.example.screenshotassistant.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.util.TypedValue
import android.view.*
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import com.example.screenshotassistant.MainActivity
import com.example.screenshotassistant.R
import com.example.screenshotassistant.ScreenshotAssistantApp
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.ActionConfigStore
import com.example.screenshotassistant.capture.Actions
import com.example.screenshotassistant.capture.CaptureType
import com.example.screenshotassistant.capture.FloatingAction
import com.example.screenshotassistant.capture.FloatingMenuManager
import com.example.screenshotassistant.capture.ImageStitcher
import com.example.screenshotassistant.network.CommandHandler
import com.example.screenshotassistant.network.HttpClient
import kotlinx.coroutines.*

class FloatingWindowService : Service() {

    companion object {
        var instance: FloatingWindowService? = null
            private set
        private const val TAG = "FloatingWindow"
    }

    var commandHandler: CommandHandler? = null

    private lateinit var windowManager: WindowManager
    private lateinit var floatingView: View
    private var menuBackdrop: View? = null
    private var menuView: View? = null
    private var statusView: TextView? = null
    private var statusDismissJob: Job? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    // 长截图面板相关
    private var capturePanel: View? = null
    private var captureThumbnail: ImageView? = null
    private var captureCountText: TextView? = null
    private var captureScreenshots = mutableListOf<Bitmap>()
    private var captureAction: ActionConfig? = null
    private var captureCallback: ((Bitmap) -> Unit)? = null
    private var isCapturing = false
    private var previewView: View? = null

    private var initialX = 0
    private var initialY = 0
    private var initialTouchX = 0f
    private var initialTouchY = 0f
    private var isMoving = false
    private var ballParams: WindowManager.LayoutParams? = null
    private var pendingShowRunnable: Runnable? = null
    private val showHandler = Handler(Looper.getMainLooper())

    override fun onCreate() {
        super.onCreate()
        instance = this
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        createFloatingBall()
        // 初始隐藏，由无障碍服务根据前台 App 白名单决定是否显示
        floatingView.visibility = View.INVISIBLE
        // 后台加载 Skill 命令到悬浮菜单缓存
        serviceScope.launch(Dispatchers.IO) {
            FloatingMenuManager.refreshFromServer(this@FloatingWindowService)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(1, createNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, createNotification())
        }

        // 支持通过 Intent 触发截图动作（用于自动化测试）
        val actionId = intent?.getStringExtra("trigger_action")
        if (actionId != null) {
            Log.d(TAG, "onStartCommand: trigger_action=$actionId")
            val action = Actions.ALL.find { it.id == actionId }
            if (action != null) {
                Handler(Looper.getMainLooper()).postDelayed({
                    executeAction(action)
                }, 500)
            } else {
                Log.e(TAG, "Unknown action: $actionId")
            }
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        serviceScope.cancel()
        dismissMenu()
        dismissCapturePanel()
        if (::floatingView.isInitialized) {
            windowManager.removeView(floatingView)
        }
    }

    private fun createNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, ScreenshotAssistantApp.CHANNEL_ID_FLOATING)
            .setContentTitle("截屏助手")
            .setContentText("悬浮窗服务运行中")
            .setSmallIcon(R.drawable.ic_screenshot)
            .setContentIntent(pendingIntent)
            .build()
    }

    private fun dp(value: Int): Int {
        return TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics
        ).toInt()
    }

    // region 悬浮球

    private fun createFloatingBall() {
        floatingView = LayoutInflater.from(this).inflate(R.layout.floating_ball, null)

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 0
            y = 200
        }
        ballParams = params

        floatingView.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    initialTouchX = event.rawX
                    initialTouchY = event.rawY
                    isMoving = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - initialTouchX
                    val dy = event.rawY - initialTouchY
                    if (dx * dx + dy * dy > 25) isMoving = true
                    params.x = initialX + dx.toInt()
                    params.y = initialY + dy.toInt()
                    windowManager.updateViewLayout(floatingView, params)
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!isMoving) onFloatingBallClicked()
                    true
                }
                else -> false
            }
        }

        windowManager.addView(floatingView, params)
    }

    fun hideFloatingBall() {
        floatingView.visibility = View.INVISIBLE
    }

    fun showFloatingBall() {
        // 尊重白名单：如果当前前台 App 不在白名单中，不显示
        val a11y = ScreenAssistAccessibilityService.instance
        val currentPkg = a11y?.lastForegroundPackage
        if (currentPkg != null && ActionConfigStore.shouldHideInApp(this, currentPkg)) {
            return
        }
        floatingView.visibility = View.VISIBLE
    }

    /** 前台 App 切换时由无障碍服务调用 */
    fun onForegroundAppChanged(packageName: String) {
        val shouldHide = ActionConfigStore.shouldHideInApp(this, packageName)
        Log.d(TAG, "onForegroundAppChanged: pkg=$packageName, shouldHide=$shouldHide, currentVis=${floatingView.visibility == View.VISIBLE}")

        if (shouldHide) {
            if (floatingView.visibility == View.VISIBLE) {
                // 悬浮球当前可见 → 立即隐藏 + 取消待执行的显示
                pendingShowRunnable?.let { showHandler.removeCallbacks(it) }
                pendingShowRunnable = null
                floatingView.visibility = View.INVISIBLE
            }
            // 悬浮球当前已隐藏 → 不取消待执行的显示（过渡动画中的 launcher 抖动）
        } else {
            // 重置显示延迟（以最后一次非 launcher 事件为准）
            pendingShowRunnable?.let { showHandler.removeCallbacks(it) }
            val runnable = Runnable {
                pendingShowRunnable = null
                floatingView.visibility = View.VISIBLE
            }
            pendingShowRunnable = runnable
            showHandler.postDelayed(runnable, 600)
        }
    }

    // endregion

    // region 菜单

    private fun onFloatingBallClicked() {
        if (menuView != null) {
            dismissMenu()
            return
        }
        showMenu()
    }

    private fun showMenu() {
        showFirstLevelMenu()
    }

    /**
     * 第一级菜单：快捷操作 + 截屏方式选择
     */
    private fun showFirstLevelMenu() {
        val padding = dp(12)

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, padding)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#E0202030"))
                cornerRadius = dp(16).toFloat()
            }
            elevation = dp(8).toFloat()
        }

        // 快捷操作区
        val quickActions = FloatingMenuManager.getQuickActions(this)
        if (quickActions.isNotEmpty()) {
            val quickRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_HORIZONTAL
            }
            for (qa in quickActions) {
                quickRow.addView(createMenuCell(qa.icon, qa.displayName, dp(72)) {
                    dismissMenu()
                    executeQuickAction(qa.skillName, qa.commandId, qa.captureType)
                })
            }
            container.addView(quickRow)

            // 分隔线
            container.addView(View(this).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(1)
                ).apply { setMargins(0, dp(6), 0, dp(6)) }
                setBackgroundColor(Color.parseColor("#30FFFFFF"))
            })
        }

        // 截屏方式选择
        container.addView(TextView(this).apply {
            text = "截屏方式"
            setTextColor(Color.parseColor("#AAAAAA"))
            textSize = 11f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(4))
        })

        val captureRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        captureRow.addView(createMenuCell("📷", "截图", dp(72)) {
            dismissMenu()
            startCaptureAndShowCommands("normal")
        })
        captureRow.addView(createMenuCell("📜", "长截图", dp(72)) {
            dismissMenu()
            startCaptureAndShowCommands("long_scroll")
        })
        captureRow.addView(createMenuCell("✋", "手动长截", dp(72)) {
            dismissMenu()
            startCaptureAndShowCommands("manual_scroll")
        })
        container.addView(captureRow)

        // 关闭按钮
        container.addView(TextView(this).apply {
            text = "✕"
            setTextColor(Color.parseColor("#AAAAAA"))
            textSize = 16f
            gravity = Gravity.CENTER
            setPadding(0, dp(6), 0, dp(2))
            setOnClickListener { dismissMenu() }
        })

        showMenuView(container)
    }

    /**
     * 第二级菜单：截屏完成后选择 Skill 功能
     */
    private fun showSecondLevelMenu(captureType: String, bitmap: Bitmap) {
        val actions = FloatingMenuManager.getActionsForCaptureType(this, captureType)
        val grouped = actions.groupBy { it.skillDisplayName }
        val padding = dp(12)

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, padding)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#E0202030"))
                cornerRadius = dp(16).toFloat()
            }
            elevation = dp(8).toFloat()
        }

        container.addView(TextView(this).apply {
            text = "选择处理方式"
            setTextColor(Color.WHITE)
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(6))
        })

        for ((skillName, skillActions) in grouped) {
            // Skill 组标题
            container.addView(TextView(this).apply {
                text = "── $skillName ──"
                setTextColor(Color.parseColor("#80FFFFFF"))
                textSize = 10f
                gravity = Gravity.CENTER
                setPadding(0, dp(4), 0, dp(2))
            })

            val columns = 3
            val rows = (skillActions.size + columns - 1) / columns
            for (row in 0 until rows) {
                val rowLayout = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_HORIZONTAL
                }
                for (col in 0 until columns) {
                    val idx = row * columns + col
                    if (idx >= skillActions.size) break
                    val action = skillActions[idx]
                    rowLayout.addView(createMenuCell(action.icon, action.displayName, dp(72)) {
                        dismissMenu()
                        FloatingMenuManager.recordUsage(this@FloatingWindowService, action.skillName, action.commandId, captureType)
                        executeSkillAction(action, bitmap)
                    })
                }
                container.addView(rowLayout)
            }
        }

        // 关闭按钮
        container.addView(TextView(this).apply {
            text = "✕ 取消"
            setTextColor(Color.parseColor("#AAAAAA"))
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(0, dp(8), 0, dp(2))
            setOnClickListener { dismissMenu() }
        })

        showMenuView(container)
    }

    private fun createMenuCell(icon: String, label: String, size: Int, onClick: () -> Unit): LinearLayout {
        val gap = dp(8)
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#40FFFFFF"))
                cornerRadius = dp(10).toFloat()
            }
            setPadding(dp(4), dp(8), dp(4), dp(8))
            layoutParams = LinearLayout.LayoutParams(size, size).apply {
                setMargins(gap / 2, gap / 2, gap / 2, gap / 2)
            }
            isClickable = true
            isFocusable = true
            setOnClickListener { onClick() }

            addView(TextView(this@FloatingWindowService).apply {
                text = icon
                textSize = 22f
                gravity = Gravity.CENTER
            })
            addView(TextView(this@FloatingWindowService).apply {
                text = label
                setTextColor(Color.WHITE)
                textSize = 10f
                gravity = Gravity.CENTER
                maxLines = 1
                setPadding(0, dp(2), 0, 0)
            })
        }
    }

    private fun showMenuView(container: View) {
        val screenWidth = resources.displayMetrics.widthPixels
        val ballX = ballParams?.x ?: 0
        val ballY = ballParams?.y ?: 200
        val menuWidth = dp(260)
        val menuX = if (ballX < screenWidth / 2) ballX + dp(60) else ballX - menuWidth

        val menuParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = menuX.coerceAtLeast(dp(8))
            y = (ballY - dp(20)).coerceAtLeast(dp(40))
        }

        val backdrop = View(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            setOnClickListener { dismissMenu() }
        }
        val backdropParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        )
        menuBackdrop = backdrop
        windowManager.addView(backdrop, backdropParams)

        menuView = container
        windowManager.addView(container, menuParams)
    }

    private fun dismissMenu() {
        menuBackdrop?.let { try { windowManager.removeView(it) } catch (_: Exception) {} }
        menuBackdrop = null
        menuView?.let { try { windowManager.removeView(it) } catch (_: Exception) {} }
        menuView = null
    }

    // endregion

    // region 悬浮状态提示

    fun showStatus(text: String, autoDismissMs: Long = 2000) {
        Handler(Looper.getMainLooper()).post {
            statusDismissJob?.cancel()
            statusView?.let {
                it.text = text
                scheduleStatusDismiss(autoDismissMs)
                return@post
            }

            val tv = TextView(this).apply {
                this.text = text
                setTextColor(Color.WHITE)
                textSize = 13f
                gravity = Gravity.CENTER
                setPadding(dp(16), dp(8), dp(16), dp(8))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#CC000000"))
                    cornerRadius = dp(20).toFloat()
                }
            }

            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
                y = dp(80)
            }

            statusView = tv
            windowManager.addView(tv, params)
            scheduleStatusDismiss(autoDismissMs)
        }
    }

    private fun scheduleStatusDismiss(delayMs: Long) {
        statusDismissJob = serviceScope.launch {
            delay(delayMs)
            dismissStatus()
        }
    }

    private fun dismissStatus() {
        Handler(Looper.getMainLooper()).post {
            statusView?.let { try { windowManager.removeView(it) } catch (_: Exception) {} }
            statusView = null
        }
    }

    // endregion

    // region Skill 化执行

    /**
     * 快捷操作：一键截屏 + 执行绑定的 Skill 命令
     */
    private fun executeQuickAction(skillName: String, commandId: String, captureType: String) {
        val cType = when (captureType) {
            "long_scroll" -> CaptureType.LONG_SCROLL
            "manual_scroll" -> CaptureType.MANUAL_SCROLL
            else -> CaptureType.NORMAL
        }

        // 构造一个兼容的 ActionConfig 用于截屏流程
        val action = ActionConfig(
            id = commandId, name = commandId, icon = "",
            captureType = cType, description = "",
        )

        // 记录使用
        FloatingMenuManager.recordUsage(this, skillName, commandId, captureType)

        // 设置截屏后的回调
        pendingSkillAction = Pair(skillName, commandId)

        when (cType) {
            CaptureType.NORMAL -> {
                hideFloatingBall()
                dismissStatus()
                serviceScope.launch {
                    delay(150)
                    val bitmap = ScreenAssistAccessibilityService.instance?.takeScreenshot()
                    showFloatingBall()
                    if (bitmap != null) {
                        executeSkillWithBitmap(skillName, commandId, bitmap)
                    } else {
                        showStatus("✗ 截屏失败", 3000)
                    }
                }
            }
            else -> startScrollCapture(action)
        }
    }

    /**
     * 第一级菜单选择截屏方式后：执行截屏，完成后弹出第二级菜单
     */
    private fun startCaptureAndShowCommands(captureType: String) {
        pendingCaptureType = captureType

        when (captureType) {
            "normal" -> {
                hideFloatingBall()
                dismissStatus()
                serviceScope.launch {
                    delay(150)
                    val bitmap = ScreenAssistAccessibilityService.instance?.takeScreenshot()
                    showFloatingBall()
                    if (bitmap != null) {
                        pendingBitmap = bitmap
                        showSecondLevelMenu(captureType, bitmap)
                    } else {
                        showStatus("✗ 截屏失败", 3000)
                    }
                }
            }
            "long_scroll" -> {
                // 自动滚动截屏，截完后弹出命令选择
                val action = ActionConfig(
                    id = "_capture", name = "截屏", icon = "",
                    captureType = CaptureType.LONG_SCROLL, description = "",
                )
                pendingSkillAction = null  // 截完后弹菜单而非直接执行
                startScrollCapture(action)
            }
            "manual_scroll" -> {
                val action = ActionConfig(
                    id = "_capture", name = "截屏", icon = "",
                    captureType = CaptureType.MANUAL_SCROLL, description = "",
                )
                pendingSkillAction = null
                startScrollCapture(action)
            }
        }
    }

    /**
     * 通过 Skill API 执行命令（截图类）
     */
    private fun executeSkillAction(action: FloatingAction, bitmap: Bitmap) {
        executeSkillWithBitmap(action.skillName, action.commandId, bitmap)
    }

    private fun executeSkillWithBitmap(skillName: String, commandId: String, bitmap: Bitmap) {
        val httpClient = HttpClient.instance
        if (httpClient == null) {
            showStatus("✗ 服务未连接", 3000)
            return
        }

        showStatus("正在提交任务...", 10000)
        serviceScope.launch(Dispatchers.IO) {
            val base64 = bitmapToBase64(bitmap)
            val taskId = httpClient.runSkillCommand(
                skillName = skillName,
                commandId = commandId,
                imageBase64 = base64,
            )
            withContext(Dispatchers.Main) {
                if (taskId != null) {
                    showStatus("✓ 任务已提交 #$taskId", 3000)
                } else {
                    showStatus("✗ 任务提交失败", 3000)
                }
            }
        }
    }

    // 待执行的 skill action（快捷操作或长截图完成后）
    private var pendingSkillAction: Pair<String, String>? = null
    // 待处理的截屏类型（两级菜单流程）
    private var pendingCaptureType: String? = null
    // 待处理的截图（普通截图后弹二级菜单时暂存）
    private var pendingBitmap: Bitmap? = null

    private fun bitmapToBase64(bitmap: Bitmap): String {
        val stream = java.io.ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, stream)
        return android.util.Base64.encodeToString(stream.toByteArray(), android.util.Base64.NO_WRAP)
    }

    // endregion

    // region 截图面板（缩略图 + 张数 + 完成按钮）

    private fun showCapturePanel() {
        val panelWidth = dp(120)
        val thumbHeight = dp(80)

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(8), dp(8), dp(8))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#DD1A1A2E"))
                cornerRadius = dp(12).toFloat()
                setStroke(dp(1), Color.parseColor("#40FFFFFF"))
            }
            elevation = dp(8).toFloat()
        }

        val thumb = ImageView(this).apply {
            layoutParams = LinearLayout.LayoutParams(panelWidth - dp(16), thumbHeight)
            scaleType = ImageView.ScaleType.CENTER_CROP
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#20FFFFFF"))
                cornerRadius = dp(6).toFloat()
            }
            setOnClickListener { showPreview() }
        }
        container.addView(thumb)
        captureThumbnail = thumb

        val countText = TextView(this).apply {
            text = "已截取 0 张"
            setTextColor(Color.WHITE)
            textSize = 11f
            gravity = Gravity.CENTER
            setPadding(0, dp(6), 0, dp(4))
        }
        container.addView(countText)
        captureCountText = countText

        // "下一页"按钮：滚动一段距离 + 截屏
        val nextBtn = TextView(this).apply {
            text = "▼ 下一页"
            setTextColor(Color.WHITE)
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(dp(8), dp(6), dp(8), dp(6))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#2196F3"))
                cornerRadius = dp(8).toFloat()
            }
            setOnClickListener { onManualNextPage() }
        }
        container.addView(nextBtn, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(4) })

        val doneBtn = TextView(this).apply {
            text = "✓ 完成"
            setTextColor(Color.WHITE)
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(dp(8), dp(6), dp(8), dp(6))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#4CAF50"))
                cornerRadius = dp(8).toFloat()
            }
            setOnClickListener { finishCapture() }
        }
        container.addView(doneBtn, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(4) })

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.END
            x = dp(8)
            y = dp(100)
        }

        capturePanel = container
        windowManager.addView(container, params)
    }


    private fun updateCapturePanel(latestBitmap: Bitmap, count: Int) {
        // 已在主线程，直接更新 UI
        captureCountText?.text = "已截取 $count 张"
        val thumbW = dp(104)
        val thumbH = dp(80)
        val scale = minOf(thumbW.toFloat() / latestBitmap.width, thumbH.toFloat() / latestBitmap.height)
        val w = (latestBitmap.width * scale).toInt().coerceAtLeast(1)
        val h = (latestBitmap.height * scale).toInt().coerceAtLeast(1)
        val thumbnail = Bitmap.createScaledBitmap(latestBitmap, w, h, true)
        captureThumbnail?.setImageBitmap(thumbnail)
    }

    private fun showPreview() {
        if (captureScreenshots.isEmpty()) return
        if (previewView != null) return

        // 在后台拼接当前所有截图，然后显示
        showStatus("正在生成预览...", 5000)
        serviceScope.launch(Dispatchers.Default) {
            val stitched = if (captureScreenshots.size == 1) {
                captureScreenshots[0]
            } else {
                com.example.screenshotassistant.capture.ImageStitcher.stitch(captureScreenshots.toList())
            }
            withContext(Dispatchers.Main) {
                dismissStatus()
                showPreviewBitmap(stitched)
            }
        }
    }

    private fun showPreviewBitmap(bitmap: Bitmap) {
        if (previewView != null) return

        val container = FrameLayout(this).apply {
            setBackgroundColor(Color.parseColor("#E0000000"))
            setOnClickListener { dismissPreview() }
        }

        // 用 ScrollView 包裹，长图可以滚动查看
        val scrollView = android.widget.ScrollView(this).apply {
            isVerticalScrollBarEnabled = true
            setOnClickListener { dismissPreview() }
        }

        val imageView = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            setImageBitmap(bitmap)
            adjustViewBounds = true
            setPadding(dp(8), dp(40), dp(8), dp(60))
            setOnClickListener { dismissPreview() }
        }
        scrollView.addView(imageView, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT
        ))
        container.addView(scrollView, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        ))

        // 顶部张数提示
        val info = TextView(this).apply {
            text = "预览 (${captureScreenshots.size} 张拼接)"
            setTextColor(Color.WHITE)
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(0, dp(12), 0, 0)
        }
        container.addView(info, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            Gravity.TOP
        ))

        // 底部提示
        val hint = TextView(this).apply {
            text = "点击任意位置关闭"
            setTextColor(Color.parseColor("#80FFFFFF"))
            textSize = 12f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(12))
        }
        container.addView(hint, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM
        ))

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        )

        previewView = container
        windowManager.addView(container, params)
    }

    private fun dismissPreview() {
        previewView?.let { try { windowManager.removeView(it) } catch (_: Exception) {} }
        previewView = null
    }

    private fun dismissCapturePanel() {
        dismissPreview()
        capturePanel?.let { try { windowManager.removeView(it) } catch (_: Exception) {} }
        capturePanel = null
        captureThumbnail = null
        captureCountText = null
    }

    // endregion

    // region 执行截图

    fun executeActionFromBroadcast(action: ActionConfig) {
        Log.d(TAG, "executeActionFromBroadcast: ${action.id}")
        executeAction(action)
    }

    private fun executeAction(action: ActionConfig) {
        Log.d(TAG, "executeAction: ${action.id}, captureType=${action.captureType}")

        when (action.captureType) {
            CaptureType.NORMAL -> {
                hideFloatingBall()
                dismissStatus()
                serviceScope.launch {
                    delay(150)
                    val bitmap = ScreenAssistAccessibilityService.instance?.takeScreenshot()
                    showFloatingBall()
                    if (bitmap != null) {
                        sendOrSave(bitmap, action)
                    } else {
                        showStatus("✗ 截屏失败，请确保无障碍服务已开启", 3000)
                    }
                }
            }
            CaptureType.LONG_SCROLL, CaptureType.MANUAL_SCROLL -> {
                startScrollCapture(action)
            }
        }
    }

    private fun sendOrSave(bitmap: Bitmap, action: ActionConfig) {
        val httpClient = HttpClient.instance
        if (httpClient == null) {
            saveBitmapLocally(bitmap, action)
            showStatus("✓ 截图已保存到本地（服务未连接）", 2000)
            return
        }

        // 获取用户自定义配置
        val config = ActionConfigStore.get(this, action.id) ?: action

        // 重型任务使用异步任务 API（提交即返回）
        val isHeavyTask = config.processingMode == "async_task" ||
                action.captureType == CaptureType.LONG_SCROLL ||
                action.id == "fund_holdings" || action.id == "full_page"

        if (isHeavyTask) {
            showStatus("正在提交任务...", 10000)
            serviceScope.launch(Dispatchers.IO) {
                val taskId = httpClient.createTask(bitmap, action.id,
                    systemPrompt = config.systemPrompt, rules = config.rules)
                withContext(Dispatchers.Main) {
                    if (taskId != null) {
                        showStatus("✓ 任务已提交 #$taskId，可在任务页查看进度", 3000)
                    } else {
                        showStatus("✗ 任务提交失败", 3000)
                    }
                }
            }
            return
        }

        // 轻量任务保持 SSE 同步模式
        showStatus("截图已发送，处理中...", 120000)

        serviceScope.launch(Dispatchers.IO) {
            val result = httpClient.sendScreenshot(bitmap, action.id) { progressMessage ->
                serviceScope.launch(Dispatchers.Main) {
                    showStatus(progressMessage, 120000)
                }
            }
            withContext(Dispatchers.Main) {
                if (result != null) {
                    commandHandler?.handleResult(result)
                } else {
                    showStatus("✗ 请求失败", 3000)
                }
            }
        }
    }

    private fun saveBitmapLocally(bitmap: Bitmap, action: ActionConfig) {
        try {
            val timestamp = System.currentTimeMillis()
            val filename = "screenshot_${action.id}_$timestamp.jpg"
            val dir = getExternalFilesDir("screenshots")
            dir?.mkdirs()
            val file = java.io.File(dir, filename)
            java.io.FileOutputStream(file).use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
            }
        } catch (e: Exception) {
            showStatus("✗ 保存失败: ${e.message}", 3000)
        }
    }

    private fun saveToGallery(bitmap: Bitmap, name: String) {
        try {
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, "$name.png")
                put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/png")
                put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/ScreenshotAssistant")
            }
            val uri = contentResolver.insert(
                android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values
            )
            uri?.let {
                contentResolver.openOutputStream(it)?.use { out ->
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
                }
            }
            Log.d(TAG, "saveToGallery: saved $name, ${bitmap.width}x${bitmap.height}")
        } catch (e: Exception) {
            Log.e(TAG, "saveToGallery failed: ${e.message}")
        }
    }

    // endregion

    // region 长截图（自动+手动共用）


    private fun startScrollCapture(action: ActionConfig) {
        if (ScreenAssistAccessibilityService.instance == null) {
            showStatus("✗ 无障碍服务未启用，请先开启", 3000)
            return
        }

        captureScreenshots.clear()
        captureAction = action
        isCapturing = true

        hideFloatingBall()

        if (action.captureType == CaptureType.LONG_SCROLL) {
            // 自动滚动模式：不显示截图面板（避免面板覆盖屏幕干扰截图）
            serviceScope.launch {
                delay(300) // 等待悬浮球消失
                doOneCapture()
                performAutoScroll()
            }
        } else {
            // 手动模式：显示面板，用户点"下一页"按钮控制滚动+截屏
            showCapturePanel()
            serviceScope.launch {
                delay(200)
                capturePanel?.visibility = View.INVISIBLE
                delay(100)
                doOneCapture()
                capturePanel?.visibility = View.VISIBLE
                showStatus("点击「下一页」滚动截屏，「完成」结束", 3000)
            }
        }
    }


    private fun onManualNextPage() {
        if (!isCapturing) return
        val a11y = ScreenAssistAccessibilityService.instance ?: run {
            showStatus("✗ 无障碍服务未连接", 2000)
            return
        }
        serviceScope.launch {
            // 隐藏面板 → 滚动 → 截屏 → 显示面板
            capturePanel?.visibility = View.INVISIBLE
            delay(100)
            a11y.performScrollDown()
            delay(1500)
            val result = doOneCapture()
            capturePanel?.visibility = View.VISIBLE
            if (result == null) {
                showStatus("已到底部", 2000)
            }
        }
    }

    /**
     * 比较两张截图的中间区域相似度（忽略实时数据微小变化）。
     * 返回 0.0~1.0，1.0 表示完全相同。
     */
    private fun bitmapSimilarity(a: Bitmap, b: Bitmap): Float {
        if (a.width != b.width || a.height != b.height) return 0f
        val w = a.width
        val h = a.height
        // 只比较中间 60% 的区域（排除状态栏、导航栏）
        val yStart = h * 20 / 100
        val yEnd = h * 80 / 100
        val rowA = IntArray(w)
        val rowB = IntArray(w)
        var same = 0
        var total = 0
        val yStep = maxOf(1, (yEnd - yStart) / 80) // 采样 80 行
        for (y in yStart until yEnd step yStep) {
            a.getPixels(rowA, 0, w, 0, y, w, 1)
            b.getPixels(rowB, 0, w, 0, y, w, 1)
            for (x in 0 until w step 6) {
                total++
                val pa = rowA[x]; val pb = rowB[x]
                // 允许每通道 ±30 的差异（实时数据颜色微变）
                val dr = kotlin.math.abs(((pa shr 16) and 0xFF) - ((pb shr 16) and 0xFF))
                val dg = kotlin.math.abs(((pa shr 8) and 0xFF) - ((pb shr 8) and 0xFF))
                val db = kotlin.math.abs((pa and 0xFF) - (pb and 0xFF))
                if (dr <= 30 && dg <= 30 && db <= 30) same++
            }
        }
        return if (total > 0) same.toFloat() / total else 0f
    }

    private suspend fun doOneCapture(): Bitmap? {
        val bitmap = ScreenAssistAccessibilityService.instance?.takeScreenshot()
        if (bitmap == null) {
            Log.e(TAG, "doOneCapture: bitmap is null!")
            return null
        }

        // 和上一帧比较相似度
        val lastBitmap = captureScreenshots.lastOrNull()
        if (lastBitmap != null) {
            val similarity = bitmapSimilarity(lastBitmap, bitmap)
            Log.d(TAG, "doOneCapture: similarity=${(similarity * 100).toInt()}%")
            if (similarity > 0.95f) {
                Log.d(TAG, "doOneCapture: too similar, skipping")
                bitmap.recycle()
                return null
            }
        }

        captureScreenshots.add(bitmap)
        updateCapturePanel(bitmap, captureScreenshots.size)
        Log.d(TAG, "captured #${captureScreenshots.size}, size=${bitmap.width}x${bitmap.height}")
        saveToGallery(bitmap, "frame_${captureScreenshots.size}_${System.currentTimeMillis()}")
        return bitmap
    }

    private fun performAutoScroll() {
        serviceScope.launch {
            val a11y = ScreenAssistAccessibilityService.instance
            Log.d(TAG, "performAutoScroll: a11y instance=${a11y != null}")
            if (a11y == null) {
                Log.e(TAG, "performAutoScroll: accessibility service not connected!")
                showStatus("✗ 无障碍服务未启用（自动滚动需要）", 3000)
                finishCapture()
                return@launch
            }

            val maxScrolls = 40

            for (i in 0 until maxScrolls) {
                if (!isCapturing) break

                Log.d(TAG, "performAutoScroll: scroll #${i+1}")
                a11y.performScrollDown()
                delay(1500) // 等待滚动动画完成

                val result = doOneCapture()
                if (result == null) {
                    // 相似度太高（没滚动），直接停止
                    Log.d(TAG, "performAutoScroll: end of scroll at #${i+1}")
                    break
                }
            }

            finishCapture()
        }
    }

    private fun finishCapture() {
        isCapturing = false

        val screenshots = captureScreenshots.toList()
        val action = captureAction
        val skillAction = pendingSkillAction
        val captType = pendingCaptureType

        dismissCapturePanel()
        showFloatingBall()

        pendingSkillAction = null
        pendingCaptureType = null

        if (screenshots.isEmpty() || action == null) {
            showStatus("✗ 没有截取到内容", 2000)
            return
        }

        val processResult: (Bitmap) -> Unit = { bitmap ->
            when {
                // 快捷操作：直接执行绑定的 Skill 命令
                skillAction != null -> {
                    executeSkillWithBitmap(skillAction.first, skillAction.second, bitmap)
                }
                // 两级菜单流程：截屏后弹出命令选择
                captType != null -> {
                    pendingBitmap = bitmap
                    showSecondLevelMenu(captType, bitmap)
                }
                // 兼容旧流程
                else -> {
                    sendOrSave(bitmap, action)
                }
            }
        }

        if (screenshots.size == 1) {
            processResult(screenshots[0])
            return
        }

        showStatus("正在拼接 ${screenshots.size} 张截图...", 10000)
        serviceScope.launch(Dispatchers.Default) {
            val result = com.example.screenshotassistant.capture.ImageStitcher.stitch(screenshots)
            saveToGallery(result, "long_screenshot_${System.currentTimeMillis()}")
            withContext(Dispatchers.Main) {
                processResult(result)
            }
        }
    }

    // endregion

    // region 兼容旧接口

    fun executeCapture(action: ActionConfig, callback: (Bitmap) -> Unit) {
        hideFloatingBall()
        serviceScope.launch {
            delay(150)
            when (action.captureType) {
                CaptureType.NORMAL -> {
                    val bitmap = ScreenAssistAccessibilityService.instance?.takeScreenshot()
                    showFloatingBall()
                    if (bitmap != null) callback(bitmap)
                }
                else -> {
                    captureCallback = callback
                    startScrollCapture(action)
                }
            }
        }
    }

    // endregion

}
