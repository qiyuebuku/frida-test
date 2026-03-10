# 06 - 分阶段实施计划

## 阶段总览

```
Phase 1 (基础)     Phase 2 (核心)      Phase 3 (App)       Phase 4 (扩展)
─────────────────  ─────────────────  ─────────────────  ─────────────────
 数据库表 ✅        后台任务执行器 ✅    底部导航改造 ✅      定时任务 ✅
 API 端点 ✅        claude -p 集成 ✅   任务列表页 ✅       fund_trade_run ✅
 Skill 部署 ✅      OCR→分析 流水线 ✅  任务详情页 ✅       fund_review ✅
                                       Markdown 渲染 ✅    推送通知 (未实施)
```

全部 4 个阶段已于 2026-03-10 实施完成，端到端测试通过。

---

## Phase 1：基础设施 ✅

### 1.1 数据库
- [x] 创建 `sa_tasks` 表（见 02 文档）
- [x] 新建 `services/task_db.py` 实现 CRUD（create_task / update_task / get_task / list_tasks）
- [x] `main.py` lifespan 中调用 `task_db.init_task_tables()`

### 1.2 API 端点
- [x] `POST /api/tasks` — 创建任务（接收 imageBase64 或 task_type，立即返回 task_id）
- [x] `GET /api/tasks` — 任务列表（支持 status / task_type / limit / offset 筛选）
- [x] `GET /api/tasks/{id}` — 任务详情（含完整 result Markdown）
- [x] 保留 `POST /api/screenshot` 旧端点（兼容轻量同步 SSE 模式）

**响应格式**：
```json
// POST /api/tasks
{"status": "success", "task_id": 2, "message": "任务已提交，正在处理中"}

// GET /api/tasks
{"status": "success", "data": {"total": 2, "items": [...]}}

// GET /api/tasks/{id}
{"status": "success", "data": {"id": 2, "status": "completed", "result": "# 报告...", ...}}
```

### 1.3 远端 Skill 部署
- [x] `deploy.sh` 增加 `sync_skills()` 函数
- [x] 远端符号链接 client.py / fund_db.py / config.json → smart-fund-server
- [x] 远端 Claude Code 2.1.71 可用，`claude -p "回复OK"` 正常返回

---

## Phase 2：服务端处理流水线 ✅

### 2.1 任务执行器框架 — `services/task_executor.py`
- [x] `TaskExecutor` 类：每个任务一个 daemon 线程
- [x] `Semaphore(MAX_CONCURRENT=2)` 并发控制，等待超时 300s
- [x] 进度回调 `_progress(task_id, pct, msg)` → 直接写 DB
- [x] 状态流转：pending → processing → completed / failed
- [x] 全局单例 `executor = TaskExecutor()`

### 2.2 OCR Handler — `_do_ocr()`
- [x] 调用 GLM-OCR（`http://119.23.227.187:8675/glmocr/parse`）
- [x] 结果保存到 `sa_ocr_records`（raw_text + markdown_text）
- [x] 回填 `sa_tasks.ocr_record_id`
- [x] 轻量任务（ocr / table / search）走 `_handle_simple_ocr` 直接完成

### 2.3 数据结构化 Handler — `_do_structurize()`
- [x] `claude -p` 将 OCR 文本转为结构化 JSON（holdings 列表）
- [x] **立即写入 `sa_ocr_records.structured_data` + `flat_data`**（供 ocr-latest / ocr-analyze 复用）
- [x] 同步更新 `sa_tasks.result_data`（阶段性结果）
- [x] JSON 提取支持 code block 包裹和裸 JSON 两种格式

### 2.4 持仓分析 Handler — `_handle_fund_holdings()`
- [x] 完整流水线：OCR → 结构化 → claude -p 深度分析
- [x] `_run_claude_with_progress()`：Popen 子进程 + 时间线性插值估算进度（35%→90%）
- [x] 阶段性消息：采集市场数据 → 分析基金详情 → 分析持仓配置 → 生成操作建议 → 整理报告
- [x] `_extract_summary()`：从 Markdown 提取摘要（取前几个非标题非空行，拼接到 200 字）

### 2.5 其他 Handler
- [x] `_handle_chat_reply()`：OCR → claude -p 生成 3 种风格回复建议
- [x] `_handle_skill_command()`：fund_trade_run / fund_review → claude -p 执行完整 skill 流程
- [x] `_handle_full_page()`：仅 OCR，Markdown 直接作为 result
- [x] `_handle_simple_ocr()`：兜底处理器

---

## Phase 3：App UI 改造 ✅

### 3.1 底部导航 — `MainActivity.kt`
- [x] `NavigationBar`：首页 / 任务 / 设置 三个 tab
- [x] `Screen` 枚举 + `detailTaskId` 状态管理页面路由
- [x] 详情页独立于 Scaffold，返回时清空 detailTaskId

### 3.2 首页 — `ui/screens/HomeScreen.kt`
- [x] 服务状态 `StatusDot`（绿色已连接 / 红色未连接）
- [x] 快捷动作 2x2 网格（识别文字、智能回复、表格识别、搜索内容）
- [x] 「AI 分析」区域：每日决策、持仓审视 两个按钮（Phase 4 实现）
- [x] 「最近任务」区域（最近 3 条 + 查看全部，3 秒轮询刷新）
- [x] 无障碍服务未开启时显示警告卡片

### 3.3 任务列表页 — `ui/screens/TaskListScreen.kt`
- [x] `HttpClient.getTasks()` 获取任务列表
- [x] `TaskCard` 组件：状态图标（✅⏳❌⏰）+ 进度条 + 摘要/错误
- [x] 按天分组（今天 / 昨天 / MM-DD）
- [x] 筛选器 DropdownMenu（全部/持仓分析/智能回复/交易决策/持仓审视）
- [x] 3 秒轮询自动刷新

### 3.4 任务详情页 — `ui/screens/TaskDetailScreen.kt`
- [x] 状态卡片：颜色区分（绿/红/黄/灰）+ 时间信息 + 耗时
- [x] 处理中：进度条 + 百分比 + 步骤消息 + 居中旋转指示器
- [x] 完成后：`MarkdownViewer` WebView 渲染（暗色主题 CSS 适配）
- [x] 失败：显示 error_msg
- [x] 右上角复制按钮（复制完整 Markdown 到剪贴板）

### 3.5 Markdown 渲染 — `ui/components/MarkdownViewer.kt`
- [x] WebView 包装的 Compose 组件
- [x] 自实现 Markdown → HTML 转换（标题/列表/表格/代码块/粗体/斜体/链接/引用/分割线）
- [x] 暗色/亮色主题自动适配（根据 `isSystemInDarkTheme()` 切换 CSS 配色）

### 3.6 HttpClient 改造 — `network/HttpClient.kt`
- [x] `createTask(bitmap, action)` — 截图类异步任务
- [x] `createCommandTask(taskType)` — 非截屏类任务（每日决策/持仓审视）
- [x] `getTasks(status, taskType, limit, offset)` — 任务列表
- [x] `getTask(taskId)` — 任务详情
- [x] 正确处理嵌套响应格式 `{status, data: {...}}`

### 3.7 悬浮窗分流 — `service/FloatingWindowService.kt`
- [x] `sendOrSave()` 根据任务类型分流：
  - LONG_SCROLL / fund_holdings / full_page → `createTask()` 异步 API
  - NORMAL 类型（ocr / table / search / chat_reply）→ `sendScreenshot()` SSE 同步

### 3.8 设置页 — `ui/screens/SettingsScreen.kt`
- [x] 服务器地址展示
- [x] 服务端/无障碍服务状态指示
- [x] 使用说明
- [x] 版本号 2.0.0

---

## Phase 4：扩展功能 ✅

### 4.1 定时任务 — `services/scheduler.py`
- [x] `Scheduler` 类：daemon 线程，每 30 秒检查
- [x] `fund_trade_run`：周一至周五 9:30 自动创建
- [x] `fund_review`：周五 15:30 自动创建
- [x] 每天同一任务类型最多触发一次（`last_triggered` 去重）
- [x] `main.py` lifespan 中 `scheduler.start()` / `scheduler.stop()`

### 4.2 App 触发服务端任务（非截屏类）
- [x] HomeScreen「AI 分析」区域：2 个 `ServerTask` 按钮
- [x] 每日决策（fund_trade_run）/ 持仓审视（fund_review）
- [x] 点击 → `createCommandTask()` → 获取 task_id → 跳转详情页
- [x] 服务未连接时按钮置灰

### 4.3 推送通知（可选）
- [ ] 任务完成后通过 FCM / WebSocket 通知 App（暂未实施，当前通过 3 秒轮询替代）

---

## 文件变更清单

### 新增文件（10 个）

| 文件 | 说明 |
|------|------|
| `smart-fund-server/services/task_db.py` | sa_tasks 表 CRUD |
| `smart-fund-server/services/task_executor.py` | 后台任务执行器（5 种 handler + claude -p 调用） |
| `smart-fund-server/services/scheduler.py` | 定时任务调度器 |
| `screenshot-assistant/.../data/TaskItem.kt` | 任务数据模型（fromJson 解析） |
| `screenshot-assistant/.../ui/components/TaskCard.kt` | 任务卡片组件 |
| `screenshot-assistant/.../ui/components/MarkdownViewer.kt` | Markdown → HTML WebView 渲染 |
| `screenshot-assistant/.../ui/screens/HomeScreen.kt` | 首页（快捷操作 + AI 分析 + 最近任务） |
| `screenshot-assistant/.../ui/screens/TaskListScreen.kt` | 任务列表页（按天分组 + 筛选） |
| `screenshot-assistant/.../ui/screens/TaskDetailScreen.kt` | 任务详情页（进度 + Markdown + 复制） |
| `screenshot-assistant/.../ui/screens/SettingsScreen.kt` | 设置页 |

### 修改文件（6 个）

| 文件 | 变更 |
|------|------|
| `smart-fund-server/main.py` | 添加 task_db 初始化 + scheduler 启动/停止 |
| `smart-fund-server/routers/__init__.py` | 添加 3 个任务 API 端点（POST + GET list + GET detail） |
| `smart-fund-server/deploy.sh` | 添加 `sync_skills()` 函数 + `--skills` 命令 |
| `screenshot-assistant/.../MainActivity.kt` | 重写为底部导航栏 + Screen 路由 + 详情页 |
| `screenshot-assistant/.../network/HttpClient.kt` | 添加 createTask / createCommandTask / getTasks / getTask |
| `screenshot-assistant/.../service/FloatingWindowService.kt` | sendOrSave 分流（重型→异步，轻量→SSE） |

---

## 端到端验证结果（2026-03-10）

### 验证 1：服务端 API
```bash
$ curl http://119.23.227.187:8900/health
{"status":"ok"}

$ curl -X POST http://119.23.227.187:8900/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type": "fund_review", "client_id": "test"}'
{"status":"success","task_id":2,"message":"任务已提交，正在处理中"}
```

### 验证 2：任务执行全流程
- 任务 #2（fund_review）：创建 → processing(5%→36%→46%→64%→90%) → completed(100%)
- 耗时约 5 分钟
- 生成完整 Markdown 持仓审视报告（含 portfolio_summary + logic_review + 操作建议 + cash_plan）

### 验证 3：App UI
- 首页：快捷操作网格 + AI 分析按钮 + 最近任务卡片 ✅
- 任务列表：按天分组 + 状态图标 + 进度/摘要显示 ✅
- 任务详情：状态卡片 + Markdown WebView 渲染（暗色主题） ✅
- 底部导航：首页/任务/设置切换正常 ✅

### 验证 4：定时调度器
- scheduler 在服务启动时自动启动 ✅
- 日志输出 `✅ 定时任务调度器已启动` ✅

---

## 关键注意事项

1. **数据结构化必须独立写入 DB**：Stage 3 完成后立即将 structured_data 写入 sa_ocr_records，不能等到最终分析完成
2. **claude -p 资源限制**：最多同时 2 个进程（Semaphore 控制），超出排队等待 5 分钟
3. **超时保护**：同步调用 120s，异步调用 600s（10 分钟），超时自动 kill 并标记 failed
4. **旧端点兼容**：`POST /api/screenshot` 保持不变，未升级的 App 仍可工作
5. **远端 API 代理**：claude -p 通过 `api.z.ai` 代理调用 GLM 模型（非原生 Anthropic API）
6. **定时任务**：仅在工作日触发，每天同一任务类型最多一次
7. **服务重启**：不自动恢复中断任务（进度卡在 processing），需手动处理或重新创建
