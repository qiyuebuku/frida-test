package com.example.screenshotassistant.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.screenshotassistant.data.StepItem
import kotlinx.coroutines.launch

// CLI 配色
private val StepBg = Color(0xFF1A1A1A)
private val StepSurface = Color(0xFF232323)
private val StepText = Color(0xFFD4D4D4)
private val StepDim = Color(0xFF8B8B8B)
private val StepBlue = Color(0xFF5B9CF5)
private val StepGreen = Color(0xFF4EC86C)
private val StepRed = Color(0xFFE05252)
private val StepYellow = Color(0xFFD4A54E)

@Composable
fun StepsView(
    steps: List<StepItem>,
    progress: Int,
    progressMsg: String?,
    isProcessing: Boolean,
    modifier: Modifier = Modifier
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    // 全局展开/收起控制：null=各自独立，true=全部展开，false=全部收起
    var expandAll by remember { mutableStateOf<Boolean?>(null) }

    // 自动滚动到底部
    LaunchedEffect(steps.size) {
        if (steps.isNotEmpty()) {
            scope.launch {
                listState.animateScrollToItem(steps.size - 1)
            }
        }
    }

    Column(modifier = modifier.background(StepBg)) {
        // 顶部操作栏：全部展开/收起
        if (steps.any { it.isToolUse && !it.output.isNullOrBlank() }) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.End
            ) {
                TextButton(
                    onClick = {
                        expandAll = if (expandAll == true) false else true
                    },
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                    modifier = Modifier.height(28.dp)
                ) {
                    Icon(
                        if (expandAll == true) Icons.Default.UnfoldLess else Icons.Default.UnfoldMore,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = StepDim
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        if (expandAll == true) "全部收起" else "全部展开",
                        fontSize = 11.sp,
                        color = StepDim
                    )
                }
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f, fill = false),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            items(steps, key = { step ->
                val idx = steps.indexOf(step)
                "${idx}_${step.type}_${step.tool ?: ""}_${step.title ?: step.content?.take(30) ?: ""}"
            }) { step ->
                if (step.isText) {
                    TextStepCard(step)
                } else {
                    ToolStepCard(step, expandAll, onToggle = {
                        // 用户点击了单个步骤，取消全局控制
                        expandAll = null
                    })
                }
            }

            // 处理中显示进度
            if (isProcessing) {
                item(key = "progress_indicator") {
                    ProcessingIndicator(progress, progressMsg)
                }
            }
        }
    }
}

@Composable
private fun TextStepCard(step: StepItem) {
    val content = step.content ?: return

    var expanded by remember { mutableStateOf(content.length < 200) }
    val isLong = content.length >= 200

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .let { if (isLong) it.clickable { expanded = !expanded } else it }
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.Start
    ) {
        Box(
            modifier = Modifier
                .padding(top = 6.dp)
                .size(8.dp)
                .clip(CircleShape)
                .background(StepBlue)
        )
        Spacer(modifier = Modifier.width(10.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = if (expanded || !isLong) content else content.take(150) + "...",
                color = StepText,
                fontSize = 12.sp,
                lineHeight = 17.sp
            )
            if (isLong) {
                Text(
                    text = if (expanded) "收起" else "展开全部",
                    color = StepBlue,
                    fontSize = 11.sp,
                    modifier = Modifier.padding(top = 2.dp)
                )
            }
        }
    }
}

@Composable
private fun ToolStepCard(step: StepItem, expandAll: Boolean?, onToggle: () -> Unit) {
    val hasOutput = !step.output.isNullOrBlank()

    // expandAll 优先，否则独立控制
    var localExpanded by remember { mutableStateOf(false) }

    // 当 expandAll 改变时，同步 localExpanded
    LaunchedEffect(expandAll) {
        if (expandAll != null) {
            localExpanded = expandAll
        }
    }

    val showOutput = when (expandAll) {
        true -> hasOutput
        false -> false
        null -> localExpanded && hasOutput
    }

    val icon = toolIcon(step.tool ?: "")
    // _step 类型的步骤 output=null 也视为已完成（它只是状态标记）
    val isStepType = step.tool == "_step"
    val isDone = step.output != null || isStepType
    val iconColor = if (step.isError) StepRed
        else if (isDone) StepGreen
        else StepYellow

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .let {
                if (hasOutput) it.clickable {
                    localExpanded = !localExpanded
                    onToggle()  // 取消全局控制
                }
                else it
            }
            .padding(vertical = 2.dp),
        horizontalArrangement = Arrangement.Start
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = iconColor,
            modifier = Modifier
                .padding(top = 2.dp)
                .size(16.dp)
        )
        Spacer(modifier = Modifier.width(8.dp))

        Column(modifier = Modifier.weight(1f)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = step.title ?: step.tool ?: "",
                    color = StepText,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )

                if (step.output == null && !isStepType) {
                    // 进行中：旋转指示器
                    CircularProgressIndicator(
                        modifier = Modifier.size(12.dp),
                        color = StepYellow,
                        strokeWidth = 1.5.dp
                    )
                } else if (step.isError) {
                    // 失败：红色叉号
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "失败",
                        tint = StepRed,
                        modifier = Modifier.size(16.dp)
                    )
                } else if (hasOutput) {
                    // 成功且有输出：展开箭头
                    Icon(
                        imageVector = if (showOutput) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                        contentDescription = null,
                        tint = StepDim,
                        modifier = Modifier.size(18.dp)
                    )
                } else {
                    // 成功无输出：绿色勾
                    Icon(
                        imageVector = Icons.Default.Check,
                        contentDescription = "成功",
                        tint = StepGreen,
                        modifier = Modifier.size(14.dp)
                    )
                }
            }

            // 失败步骤显示错误标签
            if (step.isError && !hasOutput) {
                Text(
                    text = "执行失败",
                    color = StepRed,
                    fontSize = 10.sp,
                    modifier = Modifier.padding(top = 1.dp)
                )
            }

            AnimatedVisibility(visible = showOutput) {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 4.dp),
                    shape = RoundedCornerShape(6.dp),
                    color = StepSurface
                ) {
                    Text(
                        text = step.output?.take(1500) ?: "",
                        color = if (step.isError) StepRed else StepDim,
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                        lineHeight = 14.sp,
                        modifier = Modifier.padding(8.dp),
                        maxLines = 30,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

@Composable
private fun ProcessingIndicator(progress: Int, progressMsg: String?) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(14.dp),
            color = StepBlue,
            strokeWidth = 2.dp
        )
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            text = progressMsg ?: "处理中... ${progress}%",
            color = StepDim,
            fontSize = 11.sp
        )
    }
}

@Composable
private fun toolIcon(tool: String): ImageVector {
    return when (tool) {
        "Read" -> Icons.Default.Description
        "Write" -> Icons.Default.NoteAdd
        "Edit" -> Icons.Default.EditNote
        "Bash" -> Icons.Default.Terminal
        "Glob" -> Icons.Default.FolderOpen
        "Grep" -> Icons.Default.Search
        "WebFetch", "WebSearch" -> Icons.Default.Language
        "Agent" -> Icons.Default.AccountTree
        "Skill" -> Icons.Default.Extension
        "_step" -> Icons.Default.CheckCircle
        else -> Icons.Default.Build
    }
}
