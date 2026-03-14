package com.example.screenshotassistant.ui.screens

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.ActionConfig
import com.example.screenshotassistant.capture.Actions
import com.example.screenshotassistant.network.WebSocketClient
import com.example.screenshotassistant.service.FloatingWindowService
import com.example.screenshotassistant.ui.theme.ScreenshotAssistantTheme

class ActionPickerActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ScreenshotAssistantTheme {
                ActionPickerScreen(
                    actions = Actions.ALL,
                    onActionSelected = { action -> executeAction(action) },
                    onCancel = { finish() }
                )
            }
        }
    }

    private fun executeAction(action: ActionConfig) {
        finish()

        FloatingWindowService.instance?.executeCapture(action) { bitmap ->
            // 发送到服务端
            WebSocketClient.instance?.sendScreenshot(bitmap, action.id)
                ?: run {
                    // WebSocket 未连接，本地保存截图
                    saveBitmapLocally(bitmap, action)
                }

            Handler(Looper.getMainLooper()).post {
                Toast.makeText(this, "截图已发送: ${action.name}", Toast.LENGTH_SHORT).show()
            }
        } ?: run {
            Toast.makeText(this, "服务未启动", Toast.LENGTH_SHORT).show()
        }
    }

    private fun saveBitmapLocally(bitmap: android.graphics.Bitmap, action: ActionConfig) {
        try {
            val timestamp = System.currentTimeMillis()
            val filename = "screenshot_${action.id}_$timestamp.jpg"
            val dir = getExternalFilesDir("screenshots")
            dir?.mkdirs()
            val file = java.io.File(dir, filename)
            java.io.FileOutputStream(file).use { out ->
                bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
            }
            Handler(Looper.getMainLooper()).post {
                Toast.makeText(this, "截图已保存: $filename", Toast.LENGTH_SHORT).show()
            }
        } catch (e: Exception) {
            Handler(Looper.getMainLooper()).post {
                Toast.makeText(this, "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }
}

@Composable
fun ActionPickerScreen(
    actions: List<ActionConfig>,
    onActionSelected: (ActionConfig) -> Unit,
    onCancel: () -> Unit
) {
    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background.copy(alpha = 0.95f)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = "选择操作",
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.padding(bottom = 24.dp)
            )

            LazyVerticalGrid(
                columns = GridCells.Fixed(3),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.weight(1f, fill = false)
            ) {
                items(actions) { action ->
                    ActionCard(action = action, onClick = { onActionSelected(action) })
                }
            }

            Spacer(modifier = Modifier.height(24.dp))

            OutlinedButton(
                onClick = onCancel,
                modifier = Modifier.fillMaxWidth(0.5f)
            ) {
                Text("取消")
            }
        }
    }
}

@Composable
fun ActionCard(
    action: ActionConfig,
    onClick: () -> Unit
) {
    val icon = when (action.id) {
        "ocr" -> Icons.Default.TextFields
        "chat_reply" -> Icons.Default.Chat
        "table" -> Icons.Default.TableChart
        "search" -> Icons.Default.Search
        "fund_holdings" -> Icons.Default.AccountBalance
        "full_page" -> Icons.Default.Article
        else -> Icons.Default.Apps
    }

    Card(
        modifier = Modifier
            .aspectRatio(1f)
            .clickable { onClick() },
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                imageVector = icon,
                contentDescription = action.name,
                modifier = Modifier.size(32.dp),
                tint = MaterialTheme.colorScheme.primary
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = action.name,
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}
