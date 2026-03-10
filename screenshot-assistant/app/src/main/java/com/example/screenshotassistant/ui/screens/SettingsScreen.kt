package com.example.screenshotassistant.ui.screens

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen() {
    var serverConnected by remember { mutableStateOf(false) }
    var a11yConnected by remember { mutableStateOf(ScreenAssistAccessibilityService.instance != null) }

    val context = LocalContext.current

    LaunchedEffect(Unit) {
        while (true) {
            a11yConnected = ScreenAssistAccessibilityService.instance != null
            serverConnected = withContext(Dispatchers.IO) {
                HttpClient.instance?.healthCheck() == true
            }
            delay(5000)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text("设置", style = MaterialTheme.typography.headlineMedium)

        // 服务器地址
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("服务器地址", style = MaterialTheme.typography.titleSmall)
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    "http://119.23.227.187:8900",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // 服务状态
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("服务状态", style = MaterialTheme.typography.titleSmall)
                Spacer(modifier = Modifier.height(12.dp))

                StatusRow("服务端连接", serverConnected)
                Spacer(modifier = Modifier.height(8.dp))
                StatusRow("无障碍服务", a11yConnected)

                if (!a11yConnected) {
                    Spacer(modifier = Modifier.height(8.dp))
                    FilledTonalButton(
                        onClick = {
                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("开启无障碍服务")
                    }
                }
            }
        }

        // 使用说明
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("使用说明", style = MaterialTheme.typography.titleSmall)
                Spacer(modifier = Modifier.height(8.dp))
                Text("1. 开启无障碍服务（截屏和滚动需要）", style = MaterialTheme.typography.bodySmall)
                Text("2. 打开 App 后悬浮球自动出现", style = MaterialTheme.typography.bodySmall)
                Text("3. 点击悬浮球选择操作类型", style = MaterialTheme.typography.bodySmall)
                Text("4. 在任务页查看处理进度和结果", style = MaterialTheme.typography.bodySmall)
            }
        }

        // 版本
        Text(
            "版本 2.0.0",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp)
        )
    }
}

@Composable
private fun StatusRow(label: String, connected: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Surface(
                modifier = Modifier.size(8.dp),
                shape = MaterialTheme.shapes.extraSmall,
                color = if (connected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.error
            ) {}
            Spacer(modifier = Modifier.width(6.dp))
            Text(
                if (connected) "已连接" else "未连接",
                style = MaterialTheme.typography.bodySmall,
                color = if (connected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.error
            )
        }
    }
}
