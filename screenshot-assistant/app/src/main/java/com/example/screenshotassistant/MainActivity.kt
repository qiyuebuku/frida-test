package com.example.screenshotassistant

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import com.example.screenshotassistant.network.CommandHandler
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.ui.screens.HomeScreen
import com.example.screenshotassistant.ui.screens.SettingsScreen
import com.example.screenshotassistant.ui.screens.TaskDetailScreen
import com.example.screenshotassistant.ui.screens.TaskListScreen
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

private enum class Screen { HOME, TASKS, SETTINGS }

@Composable
private fun AppNavigation() {
    var currentScreen by remember { mutableStateOf(Screen.HOME) }
    var detailTaskId by remember { mutableStateOf<Int?>(null) }

    // 如果在详情页，显示详情
    if (detailTaskId != null) {
        TaskDetailScreen(
            taskId = detailTaskId!!,
            onBack = { detailTaskId = null }
        )
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    icon = { Icon(Icons.Default.Home, contentDescription = "首页") },
                    label = { Text("首页") },
                    selected = currentScreen == Screen.HOME,
                    onClick = { currentScreen = Screen.HOME }
                )
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
                Screen.HOME -> HomeScreen(
                    onTaskClick = { detailTaskId = it },
                    onNavigateToTasks = { currentScreen = Screen.TASKS }
                )
                Screen.TASKS -> TaskListScreen(
                    onTaskClick = { detailTaskId = it }
                )
                Screen.SETTINGS -> SettingsScreen()
            }
        }
    }
}
