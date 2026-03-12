package com.example.screenshotassistant.ui.screens

import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.SkillCommand
import com.example.screenshotassistant.data.SkillDetail
import com.example.screenshotassistant.network.HttpClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SkillDetailScreen(
    skillName: String,
    onBack: () -> Unit,
    onTaskClick: (Int) -> Unit,
) {
    var detail by remember { mutableStateOf<SkillDetail?>(null) }
    var isLoading by remember { mutableStateOf(true) }

    LaunchedEffect(skillName) {
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getSkillDetail(skillName)?.let { detail = it }
        }
        isLoading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(detail?.project?.displayName ?: skillName) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        if (isLoading) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator()
            }
        } else if (detail == null) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center
            ) {
                Text("加载失败", color = MaterialTheme.colorScheme.error)
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Skill 描述
                Text(
                    detail!!.project.description,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                Spacer(modifier = Modifier.height(4.dp))

                // 命令列表
                Text(
                    "命令",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                detail!!.commands.forEach { command ->
                    CommandCard(
                        skillName = skillName,
                        command = command,
                        onTaskClick = onTaskClick,
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))
            }
        }
    }
}

@Composable
private fun CommandCard(
    skillName: String,
    command: SkillCommand,
    onTaskClick: (Int) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var showConfirmDialog by remember { mutableStateOf(false) }
    var showTextInputDialog by remember { mutableStateOf(false) }
    var isExecuting by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = !isExecuting) {
                when (command.input) {
                    "none" -> showConfirmDialog = true
                    "text" -> showTextInputDialog = true
                    "screenshot" -> {
                        // TODO Phase 3: 弹出图片选择器
                        Toast
                            .makeText(context, "截图类命令请通过悬浮球触发", Toast.LENGTH_SHORT)
                            .show()
                    }
                    else -> showConfirmDialog = true
                }
            }
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.Top
        ) {
            Icon(
                Icons.Default.PlayArrow,
                contentDescription = null,
                tint = if (isExecuting) MaterialTheme.colorScheme.outline
                       else MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .size(24.dp)
                    .padding(top = 2.dp)
            )
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(command.name, style = MaterialTheme.typography.titleSmall)
                Text(
                    command.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(modifier = Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        command.estimatedTimeLabel,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline
                    )
                    Text(
                        command.inputLabel,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline
                    )
                    if (command.executor == "pipeline") {
                        Text(
                            "Pipeline",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.tertiary
                        )
                    }
                }
            }
            if (isExecuting) {
                CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
            }
        }
    }

    // 确认执行对话框（input=none）
    if (showConfirmDialog) {
        ConfirmRunDialog(
            command = command,
            onDismiss = { showConfirmDialog = false },
            onConfirm = { args ->
                showConfirmDialog = false
                isExecuting = true
                scope.launch {
                    val taskId = withContext(Dispatchers.IO) {
                        HttpClient.instance?.runSkillCommand(skillName, command.id, args)
                    }
                    isExecuting = false
                    if (taskId != null) {
                        onTaskClick(taskId)
                    } else {
                        Toast.makeText(context, "任务创建失败", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        )
    }

    // 文本输入对话框（input=text）
    if (showTextInputDialog) {
        TextInputRunDialog(
            command = command,
            onDismiss = { showTextInputDialog = false },
            onConfirm = { inputData, args ->
                showTextInputDialog = false
                isExecuting = true
                scope.launch {
                    val taskId = withContext(Dispatchers.IO) {
                        HttpClient.instance?.runSkillCommand(
                            skillName, command.id, args, inputData = inputData
                        )
                    }
                    isExecuting = false
                    if (taskId != null) {
                        onTaskClick(taskId)
                    } else {
                        Toast.makeText(context, "任务创建失败", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        )
    }
}

@Composable
private fun ConfirmRunDialog(
    command: SkillCommand,
    onDismiss: () -> Unit,
    onConfirm: (Map<String, Any>?) -> Unit,
) {
    // 收集 boolean 类型的 args（如 --dry）
    val argStates = remember {
        command.args.filter { !it.required }.associateWith { mutableStateOf(false) }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("确认执行") },
        text = {
            Column {
                Text(command.name, style = MaterialTheme.typography.titleSmall)
                Text(
                    command.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                if (argStates.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(12.dp))
                    argStates.forEach { (arg, state) ->
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Checkbox(
                                checked = state.value,
                                onCheckedChange = { state.value = it }
                            )
                            Text(arg.description, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val args = argStates
                    .filter { it.value.value }
                    .map { it.key.name to true as Any }
                    .toMap()
                    .ifEmpty { null }
                onConfirm(args)
            }) {
                Text("执行")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

@Composable
private fun TextInputRunDialog(
    command: SkillCommand,
    onDismiss: () -> Unit,
    onConfirm: (String, Map<String, Any>?) -> Unit,
) {
    val requiredArgs = command.args.filter { it.required }
    val inputStates = remember {
        requiredArgs.associateWith { mutableStateOf("") }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(command.name) },
        text = {
            Column {
                Text(
                    command.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(modifier = Modifier.height(12.dp))

                inputStates.forEach { (arg, state) ->
                    OutlinedTextField(
                        value = state.value,
                        onValueChange = { state.value = it },
                        label = { Text(arg.description) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                }
            }
        },
        confirmButton = {
            val allFilled = inputStates.all { it.value.value.isNotBlank() }
            TextButton(
                onClick = {
                    val inputData = inputStates.entries.joinToString(" ") { it.value.value }
                    val args = inputStates
                        .map { it.key.name to it.value.value as Any }
                        .toMap()
                    onConfirm(inputData, args)
                },
                enabled = allFilled
            ) {
                Text("执行")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}
