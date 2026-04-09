# Claude Skills 平台

通过 **smart-fund-server** 将 Claude Code 的 Skill 能力封装为 HTTP API，支持远程调用、实时进度推送、会话追问。

## API 使用

**服务地址**: `http://119.23.227.187:8900`

外部调用方（Agent / 脚本 / App）只需请求一个接口即可了解全部用法：

```bash
curl -s --noproxy '*' 'http://119.23.227.187:8900/api/guide'
```

返回完整的调用协议：发现能力 → 提交任务 → 轮询状态 → 获取文件 → 追问。

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        客户端（调用方）                           │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Android App  │  │  curl / HTTP │  │  外部 Agent / 脚本     │  │
│  │ (skill-      │  │   客户端     │  │                        │  │
│  │  assistant)  │  │              │  │                        │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬────────────┘  │
└─────────┼─────────────────┼──────────────────────┼───────────────┘
          │ HTTP/WS         │ HTTP                  │ HTTP
          ▼                 ▼                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                    smart-fund-server (FastAPI)                    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                       API 路由层                            │  │
│  │                                                            │  │
│  │  /api/guide                 API 使用指南（自描述入口）      │  │
│  │  /api/skills                Skill 列表 / 详情 / 重载       │  │
│  │  /api/skills/{name}/run     执行 Skill 命令 → 创建任务     │  │
│  │  /api/tasks                 任务 CRUD / 分页查询           │  │
│  │  /api/tasks/{id}/message    追问（会话恢复）                │  │
│  │  /api/tasks/{id}/steps      结构化执行步骤                  │  │
│  │  /api/files/read            读取 Skill 产出文件             │  │
│  │  /terminal/{id}/ws          WebSocket 实时终端流            │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                      │
│  ┌────────────────────────▼───────────────────────────────────┐  │
│  │                    TaskExecutor                             │  │
│  │                                                            │  │
│  │  ┌─────────────┐    ┌──────────────────────────────────┐  │  │
│  │  │ SkillRegistry│    │ tmux 会话池                      │  │  │
│  │  │ 扫描 SKILL.md│    │                                  │  │  │
│  │  │ 解析命令定义 │    │  每个 Task 一个 tmux session      │  │  │
│  │  └──────┬──────┘    │  内运行 claude CLI 交互模式       │  │  │
│  │         │           │  完成后保留 30min 支持追问         │  │  │
│  │         │           └──────────────────────────────────┘  │  │
│  │         ▼                                                  │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │                  PostgreSQL (task_db)                 │  │  │
│  │  │  sa_tasks: id, status, progress, result, messages,   │  │  │
│  │  │           session_id, tool_calls, terminal_log ...   │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
          │
          │ Claude CLI 在 skill 目录下执行
          ▼
┌──────────────────────────────────────────────────────────────────┐
│                     .claude/skills/                               │
│                                                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │ fund-trade  │ │ scrape-docs │ │reverse-app  │ │screenshot │  │
│  │ SKILL.md    │ │ SKILL.md    │ │ SKILL.md    │ │ SKILL.md  │  │
│  │ client.py   │ │ doc_scraper │ │ knowledge/  │ │           │  │
│  │ knowledge/  │ │ knowledge/  │ │ templates/  │ │           │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## 核心概念

### Skill

一个 Skill 是一个目录，包含 `SKILL.md`（描述 + 命令定义）和相关工具脚本/知识库。
`SKILL.md` 的 YAML frontmatter 定义了 Skill 的元数据和可执行命令。

### Command

Skill 中的一个可执行单元。每个 Command 有 `id`、`name`、`executor` 等属性。
`executor: claude` 表示通过 Claude CLI 执行（Claude 读取 SKILL.md 中的指引自主完成任务）。

### Task

一次 Command 执行产生一个 Task。Task 有完整的生命周期：

```
pending → processing → completed / failed / stopped
                ↑              │
                └──── message ─┘  (追问后重新进入 processing)
```

## 执行原理

当 `POST /api/skills/{name}/run` 被调用时：

1. **SkillRegistry** 查找 Skill 和 Command
2. **TaskExecutor** 创建一个 tmux 会话
3. 在 tmux 中启动 `claude --dangerously-skip-permissions --session-id <uuid>`
4. 工作目录设为 Skill 目录（`cwd=skill.path`）
5. 构建 prompt：注入 SKILL.md 正文 + 命令参数 + 用户 rules，Claude 无需自己读 SKILL.md
6. Claude 按注入的知识库指引自主调用工具完成任务
7. TaskExecutor 轮询 tmux 输出，解析工具调用和文本步骤，写入 DB
8. 客户端通过 `GET /api/tasks/{id}` 轮询获取进度和结果
9. 任务完成后，tmux 会话保留 30 分钟，支持追问（`POST /api/tasks/{id}/message`）

## SKILL.md 规范

### Frontmatter 格式

```yaml
---
name: my-skill              # 唯一标识（必填）
display_name: 我的技能        # 显示名称
icon: star                   # Material Design 图标名
description: 技能描述         # 一句话描述
category: tools              # 分类：tools / finance / security / creative
commands:                    # 命令列表（必填，否则 SkillRegistry 跳过）
  - id: do-something         # 命令唯一 ID（必填）
    name: 执行某操作           # 命令显示名（必填）
    description: 详细描述
    input: text               # 输入类型：none / text / screenshot / file
    executor: claude           # 执行器：claude = Claude CLI 执行
    estimated_time: 60         # 预估耗时（秒）
    args:
      - name: url
        description: 目标 URL
        required: true
---

# Skill 正文（Markdown）

Claude 执行命令时会直接在 prompt 中收到这部分内容作为指引。
```

### 推荐目录结构

```
my-skill/
├── SKILL.md           # 配置 + 工作流指引（必须）
├── knowledge/         # 知识库（可选，Claude 执行时可引用）
│   ├── pitfalls.md
│   └── strategies.md
├── templates/         # 模板文件（可选）
└── tools/             # 工具脚本（可选）
    └── helper.py
```

## 快速开始

### 新增一个 Skill

1. 在 `.claude/skills/` 下创建目录
2. 创建 `SKILL.md`，写好 frontmatter（必须有 `name` 和 `commands`）
3. 调用 `POST /api/skills/reload` 重新扫描
4. 通过 `GET /api/skills` 确认新 Skill 已加载

### 调试技巧

```bash
# 查看所有活跃的 tmux 会话
tmux ls

# 直接进入某个任务的 tmux 会话观察 Claude 执行过程
tmux attach -t sa_claude_42

# 退出 tmux（不关闭会话）
# 按 Ctrl+B 然后按 D
```

### 部署

```bash
cd /home/yuyang/frida-test/.claude/skills
./deploy.sh              # 同步代码 + 重启服务
./deploy.sh --sync-only  # 只同步代码
./deploy.sh --status     # 查看服务状态
./deploy.sh --logs 100   # 查看最近日志
```
