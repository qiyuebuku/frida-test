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

文件结构：
```markdown
# 方案规划上下文

## 用户需求
[用户的原始需求]

## 相关文档 / 源码 / Schema
[收集到的所有上下文]
```

### 3. 委托 planner API 执行

构造 prompt（简短指令 + 上下文文件路径 + 保存路径），通过 curl 调用 planner API：

```bash
curl -s -X POST http://localhost:8899/tasks \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"请先读取上下文文件，然后基于其中的信息写方案。\\n\\n上下文文件路径：${CONTEXT_FILE}\\n\\n## 用户需求\\n[简短描述]\\n\\n## 方案保存路径\\n[目标路径]\",
    \"context_file\": \"${CONTEXT_FILE}\",
    \"cwd\": \"/home/yuyang/frida-test\",
    \"timeout\": 600
  }"
```

**注意**：
- `cwd` 设为项目根目录，让 Claude CLI 和主会话共享同一开发环境
- prompt 保持简短，详细上下文都在临时文件中，由 Claude CLI 通过 Read 工具自行加载
- `timeout` 默认 600 秒（10 分钟），复杂任务可加大

### 4. 监控进度

每隔几秒轮询任务状态，向用户报告当前步骤：

```bash
curl -s http://localhost:8899/tasks/{task_id}
```

关注返回值中的：
- `status`: processing / completed / failed / stopped
- `progress`: 0-100
- `progress_msg`: 当前步骤描述
- `tool_calls`: 工具调用序列（向用户报告进度）

当 `status` 变为 `completed` 时，获取 `result` 字段。

### 5. 汇报结果

- 方案文件路径
- 方案概要（核心决策和步骤数）

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

### 8. 关闭服务（任务全部完成后）

所有任务处理完毕、不再需要追问时，关闭 planner 服务释放资源：

```bash
curl -s -X POST http://localhost:8899/shutdown
```

## 注意事项

- planner API 服务是持久运行的，tmux 中的 Claude CLI 会话在任务完成后保持活跃，支持追问
- 上下文收集是 AI 的工作——AI 根据实际情况自主决定收集什么，不写死
- planner.md（架构师 persona）会自动注入到 Claude CLI 会话中，无需手动传入
- 如果用户指定了特定后端（如"用 API key"），可以在创建任务时指定 `backend` 参数
- 30 分钟无活动的 tmux 会话会被自动清理

## 用户请求

$ARGUMENTS
