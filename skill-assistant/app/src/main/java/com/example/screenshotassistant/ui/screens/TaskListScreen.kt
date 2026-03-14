package com.example.screenshotassistant.ui.screens

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.SkillProject
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.ui.components.TaskCard
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskListScreen(onTaskClick: (Int) -> Unit) {
    var tasks by remember { mutableStateOf<List<TaskItem>>(emptyList()) }
    var total by remember { mutableStateOf(0) }
    var isLoading by remember { mutableStateOf(true) }
    var filterExpanded by remember { mutableStateOf(false) }
    var selectedSkillFilter by remember { mutableStateOf<String?>(null) }
    var serverConnected by remember { mutableStateOf(false) }
    var a11yConnected by remember { mutableStateOf(ScreenAssistAccessibilityService.instance != null) }
    var skills by remember { mutableStateOf<List<SkillProject>>(emptyList()) }

    val context = LocalContext.current

    // 加载和轮询
    LaunchedEffect(selectedSkillFilter) {
        while (true) {
            a11yConnected = ScreenAssistAccessibilityService.instance != null
            withContext(Dispatchers.IO) {
                val healthy = HttpClient.instance?.healthCheck() == true
                serverConnected = healthy
                if (skills.isEmpty()) {
                    HttpClient.instance?.getSkills()?.let { skills = it }
                }
                HttpClient.instance?.getTasks(
                    skillName = selectedSkillFilter,
                    limit = 50
                )?.let { (list, count) ->
                    tasks = list
                    total = count
                }
            }
            isLoading = false
            delay(5000)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // 标题栏 + 服务状态
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("技能助手", style = MaterialTheme.typography.headlineMedium)

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(
                            if (serverConnected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.error
                        )
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    if (serverConnected) "已连接" else "未连接",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (serverConnected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.error
                )
            }
        }

        // 无障碍服务警告
        if (!a11yConnected) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.5f)
                )
            ) {
                Row(
                    modifier = Modifier.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(Icons.Default.Warning, contentDescription = null,
                        tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(20.dp))
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("无障碍服务未开启", style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f))
                    TextButton(onClick = {
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }) {
                        Text("去开启")
                    }
                }
            }
        }

        // 筛选行
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "任务列表",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Box {
                IconButton(onClick = { filterExpanded = true }) {
                    Icon(Icons.Default.FilterList, contentDescription = "筛选")
                }
                DropdownMenu(
                    expanded = filterExpanded,
                    onDismissRequest = { filterExpanded = false }
                ) {
                    DropdownMenuItem(
                        text = {
                            Text(
                                "全部",
                                color = if (selectedSkillFilter == null) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.onSurface
                            )
                        },
                        onClick = {
                            selectedSkillFilter = null
                            filterExpanded = false
                            isLoading = true
                        }
                    )
                    skills.forEach { skill ->
                        DropdownMenuItem(
                            text = {
                                Text(
                                    skill.displayName,
                                    color = if (selectedSkillFilter == skill.name) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.onSurface
                                )
                            },
                            onClick = {
                                selectedSkillFilter = skill.name
                                filterExpanded = false
                                isLoading = true
                            }
                        )
                    }
                }
            }
        }

        if (isLoading && tasks.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator()
            }
        } else if (tasks.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    "暂无任务",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            val grouped = tasks.groupBy { it.createdAt.take(10) }

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                grouped.forEach { (date, dateTasks) ->
                    item {
                        Text(
                            text = formatDateGroup(date),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 4.dp)
                        )
                    }

                    items(dateTasks, key = { it.id }) { task ->
                        TaskCard(task = task, onClick = { onTaskClick(task.id) })
                    }
                }
            }
        }
    }
}

private fun formatDateGroup(date: String): String {
    if (date.length < 10) return date
    val today = java.time.LocalDate.now().toString()
    val yesterday = java.time.LocalDate.now().minusDays(1).toString()
    return when (date) {
        today -> "今天"
        yesterday -> "昨天"
        else -> date.substring(5)
    }
}
