package com.example.screenshotassistant

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import com.example.screenshotassistant.network.CommandHandler
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.ui.screens.*
import com.example.screenshotassistant.ui.theme.ScreenshotAssistantTheme
import kotlinx.coroutines.*

class MainActivity : ComponentActivity() {

    private var commandHandler: CommandHandler? = null
    private val serverUrl = "http://119.23.227.187:8900"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        autoStart()

        setContent {
            ScreenshotAssistantTheme {
                AppNavigation()
            }
        }
    }

    private fun autoStart() {
        if (!Settings.canDrawOverlays(this)) {
            startActivity(Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            ))
            Toast.makeText(this, "请授予悬浮窗权限后重试", Toast.LENGTH_LONG).show()
            return
        }

        if (FloatingWindowService.instance == null) {
            startForegroundService(Intent(this, FloatingWindowService::class.java))
        }

        HttpClient.create(serverUrl)

        if (commandHandler == null) {
            commandHandler = CommandHandler(applicationContext)
        }
        CoroutineScope(Dispatchers.Main).launch {
            repeat(20) {
                if (FloatingWindowService.instance != null) {
                    FloatingWindowService.instance?.commandHandler = commandHandler
                    return@launch
                }
                delay(100)
            }
        }

        if (ScreenAssistAccessibilityService.instance == null) {
            Toast.makeText(this, "请开启无障碍服务以启用截屏功能", Toast.LENGTH_LONG).show()
        }
    }
}

private enum class Screen { TASKS, SETTINGS }
private enum class SubScreen { NONE, TASK_DETAIL, TASK_CONFIG, MENU_CONFIG }

@Composable
private fun AppNavigation() {
    var currentScreen by remember { mutableStateOf(Screen.TASKS) }
    var subScreen by remember { mutableStateOf(SubScreen.NONE) }
    var detailTaskId by remember { mutableStateOf<Int?>(null) }
    var configActionId by remember { mutableStateOf<String?>(null) }

    // 拦截系统返回键，子页面时返回上一层而不是退出 App
    BackHandler(enabled = subScreen != SubScreen.NONE) {
        subScreen = SubScreen.NONE
        detailTaskId = null
        configActionId = null
    }

    // 子页面（覆盖在 tab 之上）
    when (subScreen) {
        SubScreen.TASK_DETAIL -> {
            if (detailTaskId != null) {
                TaskDetailScreen(
                    taskId = detailTaskId!!,
                    onBack = { subScreen = SubScreen.NONE; detailTaskId = null }
                )
                return
            }
        }
        SubScreen.TASK_CONFIG -> {
            if (configActionId != null) {
                TaskConfigScreen(
                    actionId = configActionId!!,
                    onBack = { subScreen = SubScreen.NONE; configActionId = null }
                )
                return
            }
        }
        SubScreen.MENU_CONFIG -> {
            MenuConfigScreen(
                onBack = { subScreen = SubScreen.NONE }
            )
            return
        }
        SubScreen.NONE -> { /* fall through to tabs */ }
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    icon = { Icon(Icons.AutoMirrored.Filled.ListAlt, contentDescription = "任务") },
                    label = { Text("任务") },
                    selected = currentScreen == Screen.TASKS,
                    onClick = { currentScreen = Screen.TASKS }
                )
                NavigationBarItem(
                    icon = { Icon(Icons.Default.Settings, contentDescription = "设置") },
                    label = { Text("设置") },
                    selected = currentScreen == Screen.SETTINGS,
                    onClick = { currentScreen = Screen.SETTINGS }
                )
            }
        }
    ) { padding ->
        Box(modifier = Modifier.padding(padding)) {
            when (currentScreen) {
                Screen.TASKS -> TaskListScreen(
                    onTaskClick = {
                        detailTaskId = it
                        subScreen = SubScreen.TASK_DETAIL
                    }
                )
                Screen.SETTINGS -> SettingsScreen(
                    onNavigateToTaskConfig = {
                        configActionId = it
                        subScreen = SubScreen.TASK_CONFIG
                    },
                    onNavigateToMenuConfig = {
                        subScreen = SubScreen.MENU_CONFIG
                    }
                )
            }
        }
    }
}
