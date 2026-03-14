package com.example.screenshotassistant.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.screenshotassistant.data.SkillProject
import com.example.screenshotassistant.network.HttpClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private val CATEGORY_LABELS = mapOf(
    "finance" to "金融",
    "tools" to "工具",
    "creative" to "创作",
    "dev" to "开发",
    "other" to "其他",
)

private val ICON_EMOJIS = mapOf(
    "trending_up" to "📈",
    "show_chart" to "📊",
    "screenshot" to "📷",
    "star" to "⭐",
)

@Composable
fun SkillListScreen(onSkillClick: (String) -> Unit) {
    var skills by remember { mutableStateOf<List<SkillProject>>(emptyList()) }
    var isLoading by remember { mutableStateOf(false) }
    var loadTrigger by remember { mutableStateOf(0) }

    fun loadSkills() {
        loadTrigger++
    }

    LaunchedEffect(loadTrigger) {
        isLoading = true
        withContext(Dispatchers.IO) {
            HttpClient.instance?.getSkills()?.let { skills = it }
        }
        isLoading = false
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
            Text("我的项目", style = MaterialTheme.typography.headlineMedium)
            IconButton(onClick = { loadSkills() }) {
                Icon(Icons.Default.Refresh, contentDescription = "刷新")
            }
        }

        if (isLoading && skills.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator()
            }
        } else if (skills.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    "暂无项目",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            val grouped = skills.groupBy { it.category }

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                grouped.forEach { (category, categorySkills) ->
                    Text(
                        text = CATEGORY_LABELS[category] ?: category,
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
                    )

                    categorySkills.forEach { skill ->
                        SkillCard(skill = skill, onClick = { onSkillClick(skill.name) })
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))
            }
        }
    }
}

@Composable
private fun SkillCard(skill: SkillProject, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = ICON_EMOJIS[skill.icon] ?: "⭐",
                modifier = Modifier.width(36.dp),
                style = MaterialTheme.typography.titleLarge
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(skill.displayName, style = MaterialTheme.typography.titleSmall)
                Text(
                    skill.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    "${skill.commandCount} 个命令",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary
                )
            }
        }
    }
}
