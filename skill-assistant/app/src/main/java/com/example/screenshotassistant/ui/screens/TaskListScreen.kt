package com.example.screenshotassistant.ui.screens

import android.content.Intent
import android.provider.Settings
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.SkillProject
import com.example.screenshotassistant.data.TaskItem
import com.example.screenshotassistant.network.HttpClient
import com.example.screenshotassistant.service.ScreenAssistAccessibilityService
import com.example.screenshotassistant.ui.components.TaskCard
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskListScreen(onTaskClick: (Int) -> Unit) {
    var tasks by remember { mutableStateOf<List<TaskItem>>(emptyList()) }
    var total by remember { mutableStateOf(0) }
    var nextToken by remember { mutableStateOf<String?>(null) }
    var isLoading by remember { mutableStateOf(true) }
    var isLoadingMore by remember { mutableStateOf(false) }
    var filterExpanded by remember { mutableStateOf(false) }
    var selectedSkillFilter by remember { mutableStateOf<String?>(null) }
    var serverConnected by remember { mutableStateOf(false) }
    var a11yConnected by remember { mutableStateOf(ScreenAssistAccessibilityService.instance != null) }
    var skills by remember { mutableStateOf<List<SkillProject>>(emptyList()) }

    // 多选模式
    var selectionMode by remember { mutableStateOf(false) }
    var selectedIds by remember { mutableStateOf(setOf<Int>()) }
    var isDeleting by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    // 退出多选模式
    fun exitSelectionMode() {
        selectionMode = false
        selectedIds = emptySet()
    }

    // 首次加载和轮询
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
                    limit = 20
                )?.let { result ->
                    tasks = result.items
                    total = result.total
                    nextToken = result.nextToken
                }
            }
            isLoading = false
            delay(5000)
        }
    }

    // 加载更多
    LaunchedEffect(isLoadingMore) {
        if (!isLoadingMore) return@LaunchedEffect
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getTasks(
                skillName = selectedSkillFilter,
                limit = 20,
                nextToken = nextToken
            )?.let { result ->
                tasks = tasks + result.items
                total = result.total
                nextToken = result.nextToken
            }
        }
        isLoadingMore = false
    }

    // 单个删除
    fun deleteTask(taskId: Int) {
        tasks = tasks.filter { it.id != taskId }
        total = (total - 1).coerceAtLeast(0)
        GlobalScope.launch(Dispatchers.IO) {
            HttpClient.instance?.deleteTask(taskId)
        }
    }

    // 批量删除
    fun batchDelete() {
        if (selectedIds.isEmpty()) return
        isDeleting = true
        val ids = selectedIds.toSet()
        tasks = tasks.filter { it.id !in ids }
        total = (total - ids.size).coerceAtLeast(0)
        exitSelectionMode()
        GlobalScope.launch(Dispatchers.IO) {
            ids.forEach { id ->
                HttpClient.instance?.deleteTask(id)
            }
            isDeleting = false
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize()) {
            // 标题栏
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                if (selectionMode) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { exitSelectionMode() }) {
                            Icon(Icons.Default.Close, contentDescription = "取消")
                        }
                        Text(
                            "已选 ${selectedIds.size} 项",
                            style = MaterialTheme.typography.titleMedium
                        )
                    }
                    Row {
                        // 全选/取消全选
                        TextButton(onClick = {
                            selectedIds = if (selectedIds.size == tasks.size) {
                                emptySet()
                            } else {
                                tasks.map { it.id }.toSet()
                            }
                        }) {
                            Text(
                                if (selectedIds.size == tasks.size) "取消全选" else "全选"
                            )
                        }
                    }
                } else {
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
            }

            // 无障碍服务警告
            if (!a11yConnected && !selectionMode) {
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
            if (!selectionMode) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "任务列表 ($total)",
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
                // 底部留出批量删除栏空间
                val bottomPadding = if (selectionMode && selectedIds.isNotEmpty()) 72.dp else 8.dp

                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(
                        start = 16.dp, end = 16.dp,
                        top = 8.dp, bottom = bottomPadding
                    ),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    grouped.forEach { (date, dateTasks) ->
                        item(key = "header_$date") {
                            Text(
                                text = formatDateGroup(date),
                                style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(vertical = 4.dp)
                            )
                        }

                        items(dateTasks, key = { it.id }) { task ->
                            if (selectionMode) {
                                // 多选模式：显示 checkbox
                                SelectableTaskCard(
                                    task = task,
                                    selected = task.id in selectedIds,
                                    onToggle = {
                                        selectedIds = if (task.id in selectedIds) {
                                            selectedIds - task.id
                                        } else {
                                            selectedIds + task.id
                                        }
                                    }
                                )
                            } else {
                                // 正常模式：左滑删除 + 长按进入多选
                                SwipeToDeleteTaskCard(
                                    task = task,
                                    onClick = { onTaskClick(task.id) },
                                    onLongClick = {
                                        selectionMode = true
                                        selectedIds = setOf(task.id)
                                    },
                                    onDelete = { deleteTask(task.id) }
                                )
                            }
                        }
                    }

                    // 加载更多
                    if (nextToken != null && !selectionMode) {
                        item(key = "load_more") {
                            LaunchedEffect(Unit) {
                                if (!isLoadingMore) isLoadingMore = true
                            }
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(16.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                if (isLoadingMore) {
                                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
                                }
                            }
                        }
                    }
                }
            }
        }

        // 底部批量删除栏
        AnimatedVisibility(
            visible = selectionMode && selectedIds.isNotEmpty(),
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
            modifier = Modifier.align(Alignment.BottomCenter)
        ) {
            Surface(
                tonalElevation = 3.dp,
                shadowElevation = 8.dp,
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 12.dp)
                        .navigationBarsPadding(),
                    horizontalArrangement = Arrangement.Center
                ) {
                    Button(
                        onClick = { batchDelete() },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error
                        ),
                        enabled = !isDeleting
                    ) {
                        if (isDeleting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = Color.White,
                                strokeWidth = 2.dp
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                        } else {
                            Icon(
                                Icons.Default.Delete,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                        }
                        Text("删除 ${selectedIds.size} 项")
                    }
                }
            }
        }
    }
}

/**
 * 多选模式下的任务卡片（带 checkbox）
 */
@Composable
fun SelectableTaskCard(
    task: TaskItem,
    selected: Boolean,
    onToggle: () -> Unit
) {
    Card(
        onClick = onToggle,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (selected)
                MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.3f)
            else
                MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
        )
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Checkbox(
                checked = selected,
                onCheckedChange = { onToggle() },
                modifier = Modifier.size(36.dp)
            )
            Spacer(modifier = Modifier.width(4.dp))
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "#${task.id} ",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = task.title ?: task.typeLabel,
                        style = MaterialTheme.typography.titleSmall,
                        maxLines = 1
                    )
                }
                if (!task.summary.isNullOrBlank() && task.isCompleted) {
                    Text(
                        text = task.summary,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1
                    )
                }
            }
        }
    }
}

/**
 * 左滑删除卡片 - 需要滑过 50% 才触发
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SwipeToDeleteTaskCard(
    task: TaskItem,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onDelete: () -> Unit
) {
    val dismissState = rememberSwipeToDismissBoxState(
        positionalThreshold = { totalDistance -> totalDistance * 0.5f },
        confirmValueChange = { value ->
            if (value == SwipeToDismissBoxValue.EndToStart) {
                onDelete()
                true
            } else false
        }
    )

    SwipeToDismissBox(
        state = dismissState,
        backgroundContent = {
            val color by animateColorAsState(
                when (dismissState.targetValue) {
                    SwipeToDismissBoxValue.EndToStart -> MaterialTheme.colorScheme.error
                    else -> Color.Transparent
                },
                label = "swipe_bg"
            )
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(color, shape = MaterialTheme.shapes.medium)
                    .padding(horizontal = 20.dp),
                contentAlignment = Alignment.CenterEnd
            ) {
                if (dismissState.targetValue == SwipeToDismissBoxValue.EndToStart) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Default.Delete,
                            contentDescription = "删除",
                            tint = Color.White
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("删除", color = Color.White, style = MaterialTheme.typography.labelMedium)
                    }
                }
            }
        },
        enableDismissFromStartToEnd = false,
        enableDismissFromEndToStart = true
    ) {
        TaskCard(task = task, onClick = onClick, onLongClick = onLongClick)
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
