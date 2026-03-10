package com.example.screenshotassistant.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
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
    var selectedFilter by remember { mutableStateOf<String?>(null) }

    val filterOptions = listOf(
        null to "全部",
        "fund_holdings" to "持仓分析",
        "chat_reply" to "智能回复",
        "ocr" to "文字识别",
        "fund_trade_run" to "交易决策",
        "fund_review" to "持仓审视"
    )

    // 加载和轮询
    LaunchedEffect(selectedFilter) {
        while (true) {
            withContext(Dispatchers.IO) {
                HttpClient.instance?.getTasks(
                    taskType = selectedFilter,
                    limit = 50
                )?.let { (list, count) ->
                    tasks = list
                    total = count
                }
            }
            isLoading = false
            delay(3000)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // 标题栏
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("任务列表", style = MaterialTheme.typography.headlineMedium)

            Box {
                IconButton(onClick = { filterExpanded = true }) {
                    Icon(Icons.Default.FilterList, contentDescription = "筛选")
                }
                DropdownMenu(
                    expanded = filterExpanded,
                    onDismissRequest = { filterExpanded = false }
                ) {
                    filterOptions.forEach { (type, label) ->
                        DropdownMenuItem(
                            text = {
                                Text(
                                    label,
                                    color = if (selectedFilter == type) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.onSurface
                                )
                            },
                            onClick = {
                                selectedFilter = type
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
            // 按日期分组
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
        else -> date.substring(5) // "MM-DD"
    }
}
