# 09 - 悬浮球菜单重构（v3）

## 背景

**v2 的问题**：
- 用户点击截屏方式（截图/长截图/手动长截）后，需等待截屏完成，再弹出二级菜单选择处理命令
- 截屏过程中用户只能干等，特别是长截图可能耗时数秒
- 快捷操作（QuickAction）缓存旧命令数据导致 404 错误

**v3 方案**：先选命令、后截屏。将截屏方式和处理命令合并到一个面板中，用户在截屏前完成所有选择。

---

## 一、交互流程对比

### v2 流程（旧）

```
点击悬浮球
    ↓
弹出菜单：快捷操作 + 截屏方式
    ↓
点击截屏方式（如"长截图"）
    ↓
开始截屏... 等待完成...
    ↓
弹出二级菜单：选择处理命令
    ↓
选择命令 → 执行
```

### v3 流程（新）

```
点击悬浮球
    ↓
弹出统一菜单：
  上层 = 截屏方式（默认选中"截图"）
  下层 = 对应的处理命令列表
    ↓
点击其他截屏方式 → 下层命令动态刷新
    ↓
点击具体命令 → 开始截屏 → 截完直接执行
```

---

## 二、菜单布局

```
┌──────────────────────────────────┐
│          截屏方式                 │
│  ┌────────┐┌────────┐┌────────┐ │
│  │  📷    ││  📜    ││  ✋    │ │
│  │  截图  ││ 长截图 ││手动长截│ │
│  │[选中]  ││        ││        │ │
│  └────────┘└────────┘└────────┘ │
│  ─────────────────────────────  │
│  ── 截屏工具箱 ──                │
│  ┌────────┐┌────────┐┌────────┐ │
│  │  🔤   ││  💬    ││  📊    │ │
│  │ 文字  ││ 智能   ││ 表格   │ │
│  │ 识别  ││ 回复   ││ 识别   │ │
│  └────────┘└────────┘└────────┘ │
│  ┌────────┐┌────────┐┌────────┐ │
│  │  🔍   ││  📈    ││  📄    │ │
│  │ 搜索  ││ 持仓   ││ 完整   │ │
│  │ 内容  ││ 分析   ││ 页面   │ │
│  └────────┘└────────┘└────────┘ │
│  ── 基金智能交易 ──              │
│  ┌────────┐                     │
│  │  📈   │                     │
│  │ 截屏   │                     │
│  │ 分析   │                     │
│  └────────┘                     │
│            ✕                     │
└──────────────────────────────────┘
```

### 布局说明

| 区域 | 内容 | 交互 |
|------|------|------|
| 上层 - 截屏方式 | 📷截图 / 📜长截图 / ✋手动长截 | 点击切换，选中项高亮（蓝色边框+底色） |
| 分隔线 | 细线分隔 | — |
| 下层 - 命令列表 | 按 Skill 分组显示，3列网格 | 点击命令 → 开始截屏 → 截完执行 |
| 底部 | ✕ 关闭 | 点击关闭菜单 |

---

## 三、核心交互

### 3.1 截屏方式切换

点击上层的截屏方式按钮时：
1. 更新选中状态（蓝色高亮）
2. 下层命令列表**立即刷新**，只显示支持该截屏方式的命令
3. 不执行截屏，不关闭菜单

```
FloatingMenuManager.getActionsForCaptureType(context, captureType)
```

每个 FloatingAction 有 `captureTypes` 字段（如 `["normal", "long_scroll"]`），用于过滤。

### 3.2 命令点击

点击下层某个命令时：
1. 关闭菜单
2. 记录使用频率（`FloatingMenuManager.recordUsage`）
3. 调用 `startCaptureAndExecute(captureType, skillName, commandId)`
4. 根据截屏方式执行截屏：
   - `normal`：隐藏悬浮球 → 150ms延迟 → 截屏 → 执行
   - `long_scroll`：进入自动滚动截屏流程 → 拼接 → 执行
   - `manual_scroll`：进入手动截屏流程 → 拼接 → 执行

### 3.3 选中状态样式

```
未选中：背景 #40FFFFFF（半透明白）
选中：  背景 #6040A0FF（半透明蓝）+ 1dp 边框 #80A0D0FF
```

---

## 四、数据流

```
服务端 /api/skills → FloatingMenuManager.refreshFromServer()
    ↓
缓存到 SharedPreferences + 内存
    ↓
pruneQuickActions() 清理无效快捷操作
    ↓
showFirstLevelMenu() → getActionsForCaptureType()
    ↓ 用户选择截屏方式
动态刷新命令列表
    ↓ 用户点击命令
startCaptureAndExecute() → 截屏 → executeSkillWithBitmap()
    ↓
POST /api/skills/{name}/run
```

### 缓存一致性保证

`refreshFromServer` 成功后自动调用 `pruneQuickActions`：
- 遍历已缓存的 QuickAction
- 检查 `skillName:commandId` 是否在最新的 actions 中存在
- 不存在的直接删除，防止调用已下线的命令

---

## 五、与 v2 的差异

| 维度 | v2 | v3 |
|------|----|----|
| 菜单层级 | 两次弹窗（先选截屏方式，截完再选命令） | 一次弹窗（截屏方式+命令同屏） |
| 截屏时机 | 选完截屏方式立即截 | 选完命令后才截 |
| 等待体验 | 截屏期间无操作，截完还要选命令 | 所有选择提前完成，截完直接执行 |
| 快捷操作 | 独立区域，缓存旧命令有风险 | 已移除快捷操作区，统一走命令列表 |
| 二级菜单 | 截屏完成后弹出 | 不再需要，命令列表内嵌在主菜单 |

---

## 六、关键代码

### 菜单入口

```kotlin
// FloatingWindowService.kt
private fun showFirstLevelMenu() {
    // 上层：截屏方式选择（默认 normal）
    // 下层：commandsContainer（动态刷新）
    // 点击截屏方式 → updateSelectedStyle() + refreshCommands()
    // 点击命令 → dismissMenu() + startCaptureAndExecute()
}
```

### 截屏并执行

```kotlin
private fun startCaptureAndExecute(
    captureType: String,
    skillName: String,
    commandId: String
) {
    pendingSkillAction = Pair(skillName, commandId)
    when (captureType) {
        "normal" -> 直接截屏 → executeSkillWithBitmap()
        else -> startScrollCapture() → finishCapture() 中走 skillAction 分支
    }
}
```

### 缓存清理

```kotlin
// FloatingMenuManager.kt
private fun pruneQuickActions(context: Context, validActions: List<FloatingAction>) {
    // 对比 validKeys 和已缓存 QuickAction
    // 删除 skillName:commandId 不在 validKeys 中的记录
}
```
