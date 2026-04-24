---
name: claude-planner
display_name: Claude 方案规划
icon: architecture
description: 用弱模型收集上下文，委托本地 planner API 服务（tmux Claude CLI）写方案。
category: tools
commands:
  - id: plan
    name: 生成技术方案
    description: 收集上下文后委托 planner API 调用真 Claude 生成方案文档
    input: text
    executor: claude
    estimated_time: 120
---

# Claude 方案规划 Skill

用当前模型（便宜）收集上下文，然后委托本地 planner API 服务（管理 tmux Claude CLI 会话）写方案。全程自动，无需用户干预。

## 模型选择

根据任务复杂度自动选择模型。优先级：**用户指定 > 自动判断 > 默认 sonnet**。

| 复杂度 | 模型 | 判断依据 |
|--------|------|----------|
| 高 | `opus` | 跨模块架构设计、复杂状态管理、并发/分布式、需要权衡多个方案、全新系统设计 |
| 中/低 | `sonnet` | 单模块功能、CRUD、配置修改、bug 修复、文档编写、已有方案的迭代改进 |

简单判断：这个需求是否需要深度思考和权衡多种方案？**需要 → opus，不需要 → sonnet**。

如果用户明确说了模型（如"用 opus"），直接用指定模型，不做自动判断。

创建任务时通过 `model` 参数传入：
- sonnet → `"model": "sonnet"`
- opus → `"model": "opus"`

## 工作流程

### 0. 确保 planner API 服务已启动

每次执行前，先检查 planner API 是否可用：

```bash
curl -s http://localhost:8899/health
```

如果无响应，启动服务：

```bash
nohup python /home/yuyang/frida-test/.claude/skills/claude-planner/server.py --port 8899 > /tmp/planner.log 2>&1 &
sleep 2 && curl -s http://localhost:8899/health
```

确认返回 `{"status": "ok", ...}` 后继续。

### 0.5 检查是否有可复用的历史会话

**每次执行 `/claude-planner` 时，先检查是否有最近完成的任务可以复用**：

```bash
curl -s http://localhost:8899/tasks?status=completed&limit=3
```

如果返回的任务列表中有**与当前需求相关的最近任务**（比如用户在追问上一次方案的细节、要求修改方案的某个部分），应该**复用该任务的会话**而不是创建新任务：

```bash
# 复用已有会话：通过追问接口发送新消息
curl -s -X POST http://localhost:8899/tasks/{task_id}/message \
  -H "Content-Type: application/json" \
  -d '{"message": "用户的追问/修改请求"}'
```

**判断标准**：
- 用户明确提到了之前的方案 → 复用（追问）
- 用户的需求是全新的、与之前无关 → 创建新任务（走下面的完整流程）
- 不确定时 → 问用户是否要基于上一次方案继续

复用会话的好处：Claude CLI 保持了完整的对话上下文，不需要重新读取上下文文件，且利用 prompt cache 大幅降低 token 消耗。

### 1. 收集上下文（当前模型 GLM-5.1）

- 分析用户需求，确定需要哪些项目上下文
- 读取相关源码、文档、schema、已有方案
- 确定方案文档的保存路径
- 这个阶段尽可能多地收集——当前模型便宜，多读不心疼

### 2. 将上下文写入临时文件

收集完成后，将所有上下文整理写入一个临时文件（Markdown 格式），供 Claude CLI 读取。**文件路径必须带时间戳防覆盖**：

```bash
# 用 Bash 生成带时间戳的路径
CONTEXT_FILE="/tmp/claude_planner_context_$(date +%s).md"
```

用 **Write 工具**写入（不要用 Bash 的 echo/cat）。

**关键：上下文文件必须做到"自包含"**，让 Claude CLI 读完这一个文件就有全部信息，不需要再去读任何项目文件。具体做法：

1. **所有引用的文件内容必须内联**——不要只写路径，要把内容贴进来
2. **每个文件块加"已读取"标注**——防止 Claude 看到路径后又去 Read
3. **提取精华而非全文粘贴**——只保留与需求相关的部分，删除无关代码

文件结构模板：
```markdown
# 方案规划上下文

> ⚠️ 本文件包含所有必要的项目上下文，已由调用方预读取并提取精华。
> 请直接基于本文件内容工作，无需也不允许自行读取任何项目文件。

## 用户需求
[用户的原始需求]

## 方案保存路径
[目标文件路径]

## 参考文档（已读取，内容如下）

### 📄 docs/xxx.md 【已读取并提取精华】
[粘贴该文件的关键内容，删除不相关部分]

### 📄 src/xxx.py 【已读取并提取精华】
[粘贴关键类/函数定义，删除具体实现细节]

### 📄 schema/xxx.sql 【已读取并提取精华】
[粘贴表结构定义]

## 项目目录结构（仅供参考，不要去读取这些文件）
[tree 输出，让 Claude 知道项目全貌但不要去读]
```

**注意**：
- 每个文件标题后必须加 `【已读取并提取精华】`
- 文件开头的 `⚠️` 警告是给 Claude CLI 看的，强化"不要自己读文件"的约束
- 目录结构部分明确标注"不要去读取这些文件"

### 3. 委托 planner API 执行

构造 prompt（简短指令 + 上下文文件路径 + 保存路径），通过 curl 调用 planner API：

```bash
curl -s -X POST http://localhost:8899/tasks \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"请先读取上下文文件，然后基于其中的信息写方案。\\n\\n【工具使用原则】上下文文件包含了调用方预收集的项目信息，优先基于它工作。如果确实缺少关键信息，可以自行 Read 补充（控制在 5 个文件以内），但禁止使用 Bash/Agent/Explore。标准流程：Read 上下文 → 思考 → Write 方案。\\n\\n上下文文件路径：${CONTEXT_FILE}\\n\\n## 用户需求\\n[简短描述]\\n\\n## 方案保存路径\\n[目标路径]\",
    \"context_file\": \"${CONTEXT_FILE}\",
    \"cwd\": \"/home/yuyang/frida-test\",
    \"model\": \"sonnet\",
    \"timeout\": 600
  }"
```

**关键原则：低成本模型收集上下文，高级模型只做决策和写文档**
- 所有文件读取、搜索、上下文收集由当前模型（GLM-5.1）在阶段 1-2 完成
- prompt 中必须包含 **严禁自行探索** 约束，避免 opus/sonnet 重复读文件浪费 token
- 上下文文件要尽可能完整，让 Claude CLI 只需 Read 一个文件就有全部信息
- `timeout`：sonnet 默认 600s，opus 建议 900s（thinking 时间长）

### 4. 监控进度

每隔几秒轮询任务状态，向用户报告当前步骤：

```bash
curl -s http://localhost:8899/tasks/{task_id}
```

关注返回值中的：
- `status`: processing / completed / failed / stopped
- `progress`: 0-100
- `progress_msg`: 当前步骤描述（如果以 `⏸️` 开头，说明在等待确认）
- `tool_calls`: 工具调用序列（向用户报告进度）
- `pending_dialog`: 如果不为空且没有 `resolved: true`，说明 Claude CLI 弹出了交互对话框

**处理交互对话框**：

轮询时如果发现 `pending_dialog` 不为空：
1. 读取 `pending_dialog.type`（confirmation / rate_limit / permission / selection）
2. 读取 `pending_dialog.title` 和 `pending_dialog.options` 了解对话框内容
3. 常见的确认对话框（创建文件、信任文件夹等）server 会自动确认
4. 需要客户端决策的（如多选项选择），通过 `/tasks/{id}/respond` 发送响应：

```bash
# 确认（Enter）
curl -s -X POST http://localhost:8899/tasks/{task_id}/respond \
  -H "Content-Type: application/json" \
  -d '{"action": "enter"}'

# 取消（Escape）
curl -s -X POST http://localhost:8899/tasks/{task_id}/respond \
  -H "Content-Type: application/json" \
  -d '{"action": "escape"}'

# 发送自定义文本
curl -s -X POST http://localhost:8899/tasks/{task_id}/respond \
  -H "Content-Type: application/json" \
  -d '{"action": "text", "text": "自定义输入内容"}'
```

5. 如果 300 秒内客户端没有响应，server 会默认发送 Enter

当 `status` 变为 `completed` 时，获取 `result` 字段。

### 5. 汇报结果

任务完成后，从 API 响应中提取并告知用户：

- 方案文件路径
- 方案概要（核心决策和步骤数）
- **Token 用量**（从 `usage` 字段获取）：实际模型、input/output tokens、估算费用
- 耗时（`duration_sec`）

`usage` 字段示例：
```json
{
  "input_tokens": 12345,
  "output_tokens": 3456,
  "cache_creation_tokens": 10000,
  "cache_read_tokens": 8000,
  "total_cost_usd": 0.1234,
  "model": "claude-sonnet-4-6",
  "turns": 5
}
```

### 6. 追问（可选）

如果用户对方案有疑问，可以通过 API 追问：

```bash
curl -s -X POST http://localhost:8899/tasks/{task_id}/message \
  -H "Content-Type: application/json" \
  -d '{"message": "用户的追问内容"}'
```

然后再次轮询进度直到完成。

### 7. 停止任务（可选）

如果需要中断：

```bash
curl -s -X POST http://localhost:8899/tasks/{task_id}/stop
```

### 8. 关闭服务（仅在用户明确要求时）

**不要主动关闭服务**。任务完成后 tmux 会话保持活跃，用户随时可能回来追问。只有用户明确说"关闭 planner"或"不需要了"时才执行：

```bash
curl -s -X POST http://localhost:8899/shutdown
```

## 注意事项

- **会话持久化**：任务完成后 tmux 中的 Claude CLI 会话保持活跃，支持追问。不要主动销毁。
- **会话复用**：下次运行 `/claude-planner` 时，先检查是否有可复用的历史会话（步骤 0.5）
- **空闲清理**：只有 30 分钟无任何活动的会话才会被自动清理
- 上下文收集是 AI 的工作——AI 根据实际情况自主决定收集什么，不写死
- planner.md（架构师 persona）会自动注入到 Claude CLI 会话中，无需手动传入
- 如果用户指定了特定后端（如"用 API key"），可以在创建任务时指定 `backend` 参数

## 用户请求

$ARGUMENTS
