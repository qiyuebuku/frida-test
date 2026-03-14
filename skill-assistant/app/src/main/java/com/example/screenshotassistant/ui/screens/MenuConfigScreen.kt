package com.example.screenshotassistant.ui.screens

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.capture.FloatingAction
import com.example.screenshotassistant.capture.FloatingMenuManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MenuConfigScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    var actions by remember { mutableStateOf(FloatingMenuManager.getActions(context)) }
    var isRefreshing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("悬浮窗功能管理") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    IconButton(
                        onClick = {
                            isRefreshing = true
                            scope.launch {
                                withContext(Dispatchers.IO) {
                                    FloatingMenuManager.refreshFromServer(context)
                                }
                                actions = FloatingMenuManager.getActions(context)
                                isRefreshing = false
                                Toast.makeText(context, "已从服务端刷新", Toast.LENGTH_SHORT).show()
                            }
                        },
                        enabled = !isRefreshing
                    ) {
                        if (isRefreshing) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        } else {
                            Icon(Icons.Default.Refresh, contentDescription = "从服务端刷新")
                        }
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            Text(
                "截图后可用的功能（来源于各 Skill 的命令定义）",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            )

            if (actions.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        "暂无截图类命令\n请确保服务端已启动",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            } else {
                // 按 Skill 分组显示
                val grouped = actions.groupBy { it.skillDisplayName }

                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)
                ) {
                    grouped.forEach { (skillName, skillActions) ->
                        item {
                            Text(
                                skillName,
                                style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp)
                            )
                        }

                        itemsIndexed(
                            skillActions,
                            key = { _, a -> "${a.skillName}:${a.commandId}" }
                        ) { _, action ->
                            Card(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 3.dp)
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        action.icon,
                                        modifier = Modifier.width(32.dp),
                                        style = MaterialTheme.typography.titleMedium
                                    )

                                    Column(modifier = Modifier.weight(1f)) {
                                        Text(action.displayName, style = MaterialTheme.typography.bodyMedium)
                                        Text(
                                            action.captureTypes.joinToString(" / ") { captureTypeLabel(it) },
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }

                                    Switch(
                                        checked = action.enabled,
                                        onCheckedChange = { enabled ->
                                            FloatingMenuManager.setCommandEnabled(
                                                context, action.skillName, action.commandId, enabled
                                            )
                                            actions = FloatingMenuManager.getActions(context)
                                        }
                                    )
                                }
                            }
                        }
                    }

                    item {
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            "功能来源于各 Skill 的命令定义，\n新增功能请在对应 Skill 中添加命令。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 8.dp)
                        )
                    }
                }
            }
        }
    }
}

private fun captureTypeLabel(type: String): String = when (type) {
    "normal" -> "截图"
    "long_scroll" -> "长截图"
    "manual_scroll" -> "手动长截"
    else -> type
}
