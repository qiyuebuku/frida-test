# 07 - Skill 化架构重构方案

## 一、核心思路

**一句话**：所有功能统一封装为 Skill，App 只是 Skill 的展示层和触发器。

### 现状问题

1. **功能散乱**：OCR、智能回复、表格识别、持仓分析、每日决策等功能分散在 App 硬编码的 `ActionConfig` 和服务端的 `TaskExecutor` 中，新增功能需要改动 App + 服务端两处
2. **两套体系并存**：悬浮球触发的 "截屏操作" 和 fund-trade 这样的 "Claude Skill" 是完全独立的两套系统，无法统一管理
3. **App 缺乏项目视角**：用户无法从 App 中看到自己有哪些 Skill 项目，也无法从 App 端触发 Skill 的各种命令

### 目标架构

```
┌─────────────────────────────────────────────────┐
│                    App（展示 + 触发）              │
│                                                  │
│   项目列表 ──→ 项目详情 ──→ 命令执行 ──→ 任务结果  │
│   (Skills)     (Commands)   (Task)     (Detail)  │
└──────────────────────┬───────────────────────────┘
                       │ HTTP API
┌──────────────────────▼───────────────────────────┐
│              smart-fund-server（服务端）            │
│                                                   │
│  /api/skills          ← Skill 注册表（读 SKILL.md）│
│  /api/skills/:id/run  ← 触发 Skill 命令           │
│  /api/tasks           ← 统一任务管理（已有）        │
└──────────────────────┬───────────────────────────┘
                       │ 本地调用
┌──────────────────────▼───────────────────────────┐
│              Skills 仓库（git 管理）               │
│                                                   │
│  fund-trade/    ← SKILL.md + client.py + prompts  │
│  screenshot/    ← SKILL.md（OCR/回复/表格等）      │
│  stock-trade/   ← SKILL.md + client.py            │
│  ...                                              │
└──────────────────────────────────────────────────┘
```

**核心变化**：
- 服务端扫描 skills 目录，解析每个 SKILL.md，暴露标准化 API
- App 通过 API 获取项目列表和命令列表，动态渲染 UI
- 新增功能 = 新增 Skill 目录 + 写 SKILL.md，**不改 App 不改服务端**

### 关键设计决策：Skill ≠ 全部交给 Claude

**问题**：像持仓分析这样的功能，完整流程是：`截屏 → OCR → 结构化 → 存 DB → Claude 分析`。只有最后一步需要 LLM，前面全是确定性代码。如果整个流程都扔给 Claude 来编排，会导致：
- 浪费大量 token（Claude 做 OCR 调用、JSON 解析、DB 写入这些固定操作）
- 响应变慢（每个步骤都要等 Claude 思考+调工具）
- 不稳定（Claude 可能跳步骤或改变流程）

**解法**：Skill 分两层——**管理层统一，执行层分离**。

```
┌────────────────────────────────────────────────────────┐
│  Skill 管理层（统一）                                    │
│  SKILL.md frontmatter → 项目列表 / 命令列表 / UI 展示    │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
┌──────────────────────┐     ┌──────────────────────────┐
│  executor: pipeline  │     │  executor: claude         │
│  服务端原生代码执行     │     │  整体交给 Claude 编排      │
│                      │     │                          │
│  OCR → 结构化 → 存DB  │     │  claude -p "..."         │
│  → 调 LLM 分析（可选） │     │  完整的 Skill 工作流      │
│                      │     │                          │
│  快、省 token、确定性  │     │  灵活、能处理复杂决策      │
└──────────────────────┘     └──────────────────────────┘
```

每个命令通过 `executor` 字段声明自己的执行方式：

| executor | 适用场景 | 特点 |
|----------|---------|------|
| `pipeline` | 流程固定、步骤可预定义的任务 | 服务端代码编排，仅在需要时调用 LLM，**快+省 token** |
| `claude` | 需要 Claude 整体编排的复杂任务 | 完整交给 Claude 执行，**灵活但慢** |

OCR、智能回复等只需一次 LLM 调用的功能，本质上就是只有一个 `llm_call` 步骤的 pipeline，不需要额外的 executor 类型。

---

## 二、Skill 标准化规范

### 2.1 目录结构

每个 Skill 是 `skills/` 下的一个目录，必须包含 `SKILL.md`：

```
skills/
├── fund-trade/
│   ├── SKILL.md          # 必须：Skill 描述 + 命令定义
│   ├── client.py         # 可选：CLI 客户端
│   ├── prompts/          # 可选：Prompt 模板
│   └── ...
├── screenshot/           # 新建：原来的截屏功能重构为 Skill
│   └── SKILL.md
├── stock-trade/
│   └── SKILL.md
└── smart-fund-server/    # 服务端（不是 Skill）
```

### 2.2 SKILL.md 元数据规范

在 SKILL.md 头部添加结构化的 YAML frontmatter，供服务端解析：

```yaml
---
name: fund-trade
display_name: 基金智能交易
icon: trending_up
description: LLM 决策引擎 + 量化信号 + 风控硬约束
category: finance          # finance / tools / creative / dev
commands:
  - id: run
    name: 每日交易决策
    description: 采集数据+新闻 → Claude 分析决策 → 执行交易
    input: none             # none / screenshot / text / file
    executor: claude        # 完整交给 Claude 编排（复杂多步决策）
    estimated_time: 300
    args:
      - name: "--dry"
        description: 模拟运行，只看决策不执行交易
        required: false

  - id: review
    name: 持仓绩效审视
    description: 持仓审视 + 调仓建议
    input: none
    executor: claude
    estimated_time: 180

  - id: ocr-analyze
    name: 截屏持仓分析
    description: 从截图识别持仓数据，采集市场行情，综合分析
    input: screenshot
    executor: pipeline      # 服务端 Pipeline：OCR → 结构化 → 存 DB → LLM 分析
    estimated_time: 120
    pipeline:               # Pipeline 步骤定义
      - step: ocr
        handler: ocr_service
        description: OCR 识别截图文字
      - step: structure
        handler: fund_parser
        description: 解析持仓数据结构
      - step: store
        handler: db_save
        description: 保存到数据库
      - step: analyze
        handler: llm_call
        description: Claude 综合分析
        prompt_template: prompts/ocr_analyze.md

  - id: analyze
    name: 单基深度分析
    description: 单只基金全维度分析报告
    input: text
    executor: claude        # 需要 Claude 灵活调用多种 API
    estimated_time: 60
    args:
      - name: fund_code
        description: 基金代码
        required: true
---
```

### 2.3 截屏相关功能的 Skill 化

将原有的 7 个 ActionConfig 封装为一个 `screenshot` Skill：

```yaml
# skills/screenshot/SKILL.md
---
name: screenshot
display_name: 截屏工具箱
icon: screenshot
description: 截屏识别、智能回复、表格提取等通用截屏分析能力
category: tools
commands:
  - id: ocr
    name: 识别文字
    description: 提取屏幕中的文字内容，保持原始格式
    input: screenshot
    capture_types: [normal, long_scroll, manual_scroll]
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: recognize
        handler: llm_call
        prompt_template: "识别图片中的文字，保持原始格式"

  - id: chat_reply
    name: 智能回复
    description: 分析聊天截图，生成多种风格的回复建议
    input: screenshot
    capture_type: normal
    executor: pipeline
    estimated_time: 15
    pipeline:
      - step: reply
        handler: llm_call
        prompt_template: "根据聊天截图生成 3 种风格回复"

  - id: table
    name: 表格识别
    description: 识别图片中的表格，输出 Markdown 表格
    input: screenshot
    capture_type: normal
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: extract
        handler: llm_call
        prompt_template: "识别图片中的表格，输出 Markdown 表格"

  - id: search
    name: 搜索内容
    description: 识别文字并在内容中搜索
    input: screenshot
    capture_type: normal
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: recognize
        handler: llm_call
        prompt_template: "识别文字并搜索相关内容"

  - id: full_page
    name: 完整页面
    description: 自动滚动截取完整页面内容
    input: screenshot
    capture_types: [normal, long_scroll]
    executor: pipeline       # 多帧拼接 → OCR → 合并，固定流程
    estimated_time: 30
    pipeline:
      - step: capture_frames
        handler: scroll_capture
      - step: stitch
        handler: image_stitch
      - step: ocr
        handler: llm_call
        prompt_template: "识别完整页面内容"

  - id: manual_scroll
    name: 手动长截
    description: 手动滑动，自动采集每一帧
    input: screenshot
    capture_types: [manual_scroll]
    executor: pipeline
    estimated_time: 30
    pipeline:
      - step: capture_frames
        handler: manual_capture
      - step: stitch
        handler: image_stitch
      - step: ocr
        handler: llm_call
        prompt_template: "识别完整页面内容"
---
```

**关键设计**：
- `capture_type` 指定截屏方式，App 读取后自动选择截屏策略
- `executor` 决定执行方式：`llm_only` 最快（截图直接给 LLM），`pipeline` 用服务端代码编排多步骤

---

## 三、App 端 UI 改造

### 3.1 页面结构（v3）

从 2 Tab 扩展为 3 Tab：

```
┌──────────────────────────────────────────────┐
│                                              │
│               (页面内容区域)                   │
│                                              │
├────────────┬──────────────┬──────────────────┤
│  📂 项目    │    📋 任务    │     ⚙️ 设置     │
└────────────┴──────────────┴──────────────────┘
```

### 3.2 项目列表页（新增）

展示所有 Skill 项目，按 category 分组：

```
┌──────────────────────────────────────┐
│  我的项目                    🔄 刷新  │
├──────────────────────────────────────┤
│                                      │
│  ── 金融 ──                          │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ 📈 基金智能交易               │   │
│  │ LLM 决策引擎 + 风控硬约束     │   │
│  │ 6 个命令                     │   │
│  └──────────────────────────────┘   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ 📊 股票交易                   │   │
│  │ 同花顺 JSBridge A 股交易     │   │
│  │ 4 个命令                     │   │
│  └──────────────────────────────┘   │
│                                      │
│  ── 工具 ──                          │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ 📷 截屏工具箱                 │   │
│  │ OCR / 智能回复 / 表格识别     │   │
│  │ 6 个命令 · 悬浮球可触发       │   │
│  └──────────────────────────────┘   │
│                                      │
│  ── 创作 ──                          │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ 📚 小说知识库                 │   │
│  │ 7 阶段 Pipeline 构建知识库    │   │
│  │ 2 个命令                     │   │
│  └──────────────────────────────┘   │
│                                      │
├────────────┬──────────────┬──────────┤
│  📂 项目    │    📋 任务    │  ⚙️ 设置 │
└────────────┴──────────────┴──────────┘
```

### 3.3 项目详情页（命令列表）

点击项目卡片进入，展示该 Skill 的所有命令：

```
┌──────────────────────────────────────┐
│  ← 返回      基金智能交易             │
├──────────────────────────────────────┤
│                                      │
│  LLM 决策引擎 + 量化信号 + 风控硬约束 │
│                                      │
│  ── 命令 ──                          │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ▶ 每日交易决策                │   │
│  │   采集数据+分析+执行交易       │   │
│  │   ⏱ ~5min  📥 无需输入       │   │
│  └──────────────────────────────┘   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ▶ 持仓绩效审视                │   │
│  │   持仓审视 + 调仓建议         │   │
│  │   ⏱ ~3min  📥 无需输入       │   │
│  └──────────────────────────────┘   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ▶ 截屏持仓分析                │   │
│  │   从截图识别持仓并分析         │   │
│  │   ⏱ ~2min  📸 需要截图       │   │
│  └──────────────────────────────┘   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ▶ 单基深度分析                │   │
│  │   单只基金全维度分析报告       │   │
│  │   ⏱ ~1min  ✏️ 需输入基金代码  │   │
│  └──────────────────────────────┘   │
│                                      │
│  ── 最近任务 ──                      │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ✅ 每日交易决策   今天 09:30   │   │
│  │ 买入军工C 500元，黄金C 持有   │   │
│  └──────────────────────────────┘   │
│                                      │
└──────────────────────────────────────┘
```

### 3.4 命令执行交互

点击命令卡片后，根据 `input` 类型决定交互方式：

| input 类型 | 交互流程 |
|-----------|---------|
| `none` | 直接确认执行 → 创建任务 → 跳转任务详情 |
| `screenshot` | 弹出图片选择器（从相册选择 / 拍照）→ 创建任务 |
| `text` | 弹出输入框（如输入基金代码）→ 创建任务 |
| `file` | 弹出文件选择器 → 创建任务 |

**截图类命令的两种入口**：

```
入口 1：悬浮球（在其他 App 中使用）
  点击悬浮球菜单 → 自动截屏当前屏幕 → 提交任务
  适用场景：正在看聊天/持仓页面，顺手截屏分析

入口 2：项目详情页（在自己 App 中使用）
  点击命令卡片 → 弹出图片选择器（相册/拍照）→ 提交任务
  适用场景：想分析之前保存的截图，或主动拍照
```

两种入口最终调用同一个 `/api/skills/{name}/run` 接口，只是图片来源不同。

**命令参数处理**（如 `--dry`）：

```
┌──────────────────────────────────────┐
│  确认执行                             │
├──────────────────────────────────────┤
│                                      │
│  每日交易决策                         │
│  采集数据+新闻 → Claude 分析 → 执行   │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ☐ 模拟运行（不实际交易）       │   │  ← --dry 参数
│  └──────────────────────────────┘   │
│                                      │
│  ┌─────────┐  ┌────────────────┐   │
│  │  取消    │  │    执行         │   │
│  └─────────┘  └────────────────┘   │
│                                      │
└──────────────────────────────────────┘
```

### 3.5 任务列表页（改造）

保持现有功能，增加按 Skill 项目筛选：

```
┌──────────────────────────────────────┐
│  任务           [所有项目 ▼] 🔍 筛选  │  ← 新增项目筛选
├──────────────────────────────────────┤
│  ... 任务卡片列表（同现有设计）       │
└──────────────────────────────────────┘
```

筛选器选项来自 `/api/skills` 返回的项目列表，不再硬编码。

### 3.6 悬浮球与 Skill 的关系

悬浮球仍然存在，但菜单项来自 Skill 命令中 `input: screenshot` 的那些：

```
悬浮球菜单 = 所有 Skill 中 input=screenshot 且 enabled=true 的命令
```

- 服务端 API 返回命令列表时标注 `floatable: true`（input 为 screenshot 的命令）
- 用户在设置页可配置哪些截屏命令出现在悬浮球中
- 点击悬浮球菜单项 = 截屏 + 触发对应 Skill 命令

---

## 四、服务端 API 设计

### 4.1 Skill 注册与发现

服务端启动时扫描 `skills/` 目录，解析每个 `SKILL.md` 的 frontmatter：

```python
# services/skill_registry.py

class SkillRegistry:
    """扫描 skills 目录，解析 SKILL.md，提供 Skill 查询"""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: dict[str, SkillInfo] = {}
        self.scan()

    def scan(self):
        """扫描所有 SKILL.md，解析 frontmatter"""
        for skill_dir in Path(skills_dir).iterdir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                meta = parse_frontmatter(skill_md)
                self.skills[meta["name"]] = SkillInfo(
                    name=meta["name"],
                    display_name=meta["display_name"],
                    icon=meta["icon"],
                    description=meta["description"],
                    category=meta["category"],
                    commands=[CommandInfo(**cmd) for cmd in meta["commands"]],
                    path=str(skill_dir),
                )

    def list_skills(self) -> list[SkillInfo]:
        return list(self.skills.values())

    def get_skill(self, name: str) -> SkillInfo | None:
        return self.skills.get(name)
```

### 4.2 新增 API 端点

```
GET  /api/skills                          → 所有 Skill 列表
GET  /api/skills/{skill_name}             → 单个 Skill 详情（含命令列表）
POST /api/skills/{skill_name}/run         → 触发 Skill 命令
GET  /api/skills/{skill_name}/tasks       → 该 Skill 的任务历史
POST /api/skills/reload                   → 重新扫描 skills 目录
```

#### `GET /api/skills` 响应示例

```json
{
  "status": "success",
  "data": [
    {
      "name": "fund-trade",
      "display_name": "基金智能交易",
      "icon": "trending_up",
      "description": "LLM 决策引擎 + 量化信号 + 风控硬约束",
      "category": "finance",
      "command_count": 6
    },
    {
      "name": "screenshot",
      "display_name": "截屏工具箱",
      "icon": "screenshot",
      "description": "OCR / 智能回复 / 表格识别等",
      "category": "tools",
      "command_count": 6
    }
  ]
}
```

#### `GET /api/skills/{skill_name}` 响应示例

```json
{
  "status": "success",
  "data": {
    "name": "fund-trade",
    "display_name": "基金智能交易",
    "icon": "trending_up",
    "description": "LLM 决策引擎 + 量化信号 + 风控硬约束",
    "category": "finance",
    "commands": [
      {
        "id": "run",
        "name": "每日交易决策",
        "description": "采集数据+新闻 → Claude 分析决策 → 执行交易",
        "input": "none",
        "mode": "async",
        "estimated_time": 300,
        "floatable": false,
        "args": [
          { "name": "--dry", "description": "模拟运行", "required": false }
        ]
      },
      {
        "id": "ocr-analyze",
        "name": "截屏持仓分析",
        "description": "从截图识别持仓数据，综合分析",
        "input": "screenshot",
        "capture_type": "long_scroll",
        "mode": "async",
        "estimated_time": 120,
        "floatable": true
      }
    ]
  }
}
```

#### `POST /api/skills/{skill_name}/run` 请求

```json
{
  "command_id": "run",
  "args": { "--dry": true },
  "input_data": null,
  "image_base64": null,
  "client_id": "phone-001"
}
```

响应复用现有的任务系统：

```json
{
  "status": "success",
  "task_id": 42,
  "message": "任务已创建：基金智能交易 - 每日交易决策"
}
```

### 4.3 三种 Executor 的执行逻辑

`TaskExecutor` 改造：不再按 `task_type` 硬编码，而是按 `executor` 类型路由。

```python
# 统一路由
skill = registry.get_skill(task.skill_name)
command = skill.get_command(task.command_id)

match command.executor:
    case "pipeline":
        result = await self._execute_pipeline(task, command)
    case "claude":
        result = await self._execute_claude(task, skill, command)
```

#### Executor 1: `pipeline`（快+省 token，固定流程）

服务端代码按 SKILL.md 定义的 `pipeline` 步骤顺序执行，只在需要时调用 LLM。

```python
async def _execute_pipeline(self, task, command):
    """按 pipeline 步骤依次执行"""
    context = {"task": task, "results": {}}

    for step in command.pipeline:
        self._update_progress(task, step.description)

        match step.handler:
            case "ocr_service":
                # 服务端 OCR（或调 LLM 识别），固定代码
                context["results"]["ocr_text"] = await ocr_service.recognize(task.image)

            case "fund_parser":
                # 纯代码：正则/JSON 解析持仓数据，零 token 消耗
                context["results"]["holdings"] = parse_fund_holdings(context["results"]["ocr_text"])

            case "db_save":
                # 纯代码：写数据库，零 token 消耗
                await save_to_db(context["results"]["holdings"])

            case "llm_call":
                # 只有这一步调用 LLM，token 只花在真正需要智能的地方
                prompt = load_prompt(step.prompt_template, context["results"])
                context["results"]["analysis"] = await llm_client.chat(prompt)

            case _:
                # 可扩展的自定义 handler
                handler = handler_registry.get(step.handler)
                context["results"][step.step] = await handler(context)

    return context["results"].get("analysis") or context["results"]
```

**对比优势**（以持仓分析为例）：

| 步骤 | pipeline 模式 | 全 claude 模式 |
|------|-------------|---------------|
| OCR 识别 | 服务端代码，~2s | Claude 调 Bash 工具跑 OCR，~10s + 工具调用 token |
| 结构化解析 | Python 代码，~0.1s | Claude 思考+解析，~5s + 思考 token |
| 存数据库 | Python 代码，~0.1s | Claude 调 Bash 执行 SQL，~5s + 工具 token |
| AI 分析 | 一次 LLM 调用，~15s | 同样 LLM，~15s |
| **总计** | **~17s, 1 次 LLM** | **~35s, 4+ 次 LLM 交互** |

#### Executor 3: `claude`（最灵活，适合复杂编排）

完整交给 Claude CLI 执行，Claude 自主调用工具、做决策、写代码。适合 fund-trade run 这种需要**多步决策+灵活调整**的场景。

```python
async def _execute_claude(self, task, skill, command):
    """在 Skill 目录下调用 claude -p 执行"""
    prompt = self._build_prompt(skill, command, task)
    cmd = f'claude -p "{prompt}" --allowedTools Bash,Read,Glob,Grep'
    # 工作目录 = skill.path
    process = subprocess.Popen(cmd, cwd=skill.path, ...)
    # 流式读取输出，推送 SSE 事件...
```

**适用场景**：每日交易决策（7 步流程、中途需要判断是否深入）、持仓审视（需要灵活调用 API）等。

#### 如何选择 executor？

```
命令是否需要 Claude 自主决策/灵活调工具？
├─ 是 → executor: claude
│     例：每日交易决策、持仓审视、单基分析
└─ 否 → executor: pipeline
      例（多步）：持仓分析（OCR→结构化→存DB→LLM分析）
      例（多步）：完整页面（滚动截取→拼接→OCR）
      例（单步）：OCR、智能回复、表格识别（只有一个 llm_call 步骤）
```

---

## 五、数据模型变更

### 5.1 sa_tasks 表扩展

```sql
ALTER TABLE sa_tasks ADD COLUMN skill_name VARCHAR(64);
ALTER TABLE sa_tasks ADD COLUMN command_id VARCHAR(64);

-- task_type 保留兼容，新任务同时填写 skill_name + command_id
-- 例如：skill_name='fund-trade', command_id='run', task_type='fund_trade_run'
```

### 5.2 App 端数据模型

```kotlin
// 新增：Skill 项目
data class SkillProject(
    val name: String,           // "fund-trade"
    val displayName: String,    // "基金智能交易"
    val icon: String,           // "trending_up"
    val description: String,
    val category: String,       // "finance"
    val commandCount: Int,
)

// 新增：Skill 命令
data class SkillCommand(
    val id: String,             // "run"
    val name: String,           // "每日交易决策"
    val description: String,
    val input: String,          // "none" / "screenshot" / "text" / "file"
    val captureType: String?,   // "normal" / "long_scroll" / "manual_scroll"
    val mode: String,           // "async" / "sync"
    val estimatedTime: Int,     // 秒
    val floatable: Boolean,     // 是否可在悬浮球中显示
    val args: List<CommandArg>,
)

data class CommandArg(
    val name: String,
    val description: String,
    val required: Boolean,
)
```

### 5.3 ActionConfig 改造

原有的 `ActionConfig` 变为 `SkillCommand` 的薄封装：

```kotlin
// 悬浮球菜单项 = SkillCommand 的视图模型
data class FloatingAction(
    val skillName: String,      // 所属 Skill
    val commandId: String,      // 命令 ID
    val displayName: String,    // 显示名称
    val icon: String,
    val captureType: CaptureType,
    val enabled: Boolean,       // 用户可配置显示/隐藏
    val sortOrder: Int,         // 用户可配置排序
    val customPrompt: String?,  // 用户自定义提示词（覆盖 default_prompt）
)
```

---

## 六、悬浮球菜单的 Skill 化

### 6.1 两级菜单交互

现有设计将截屏方式和功能绑定在一起（如"持仓分析"固定用长截图），用户没有选择权。改为两级菜单：

**第一级：选择截屏动作**（怎么截）

```
┌──────────────────────┐
│                      │
│     📷 截图          │  ← 截取当前屏幕
│                      │
│     📜 长截图        │  ← 自动滚动采集
│                      │
│     ✋ 手动长截       │  ← 手动滑动采集
│                      │
└──────────────────────┘
```

用户点击后立即执行截屏/长截图，完成后弹出第二级菜单。

**第二级：选择 Skill 功能**（截完干嘛）

展示所有支持当前截屏类型的 Skill 命令：

```
┌───────────────────────────┐
│  截图完成，选择处理方式：    │
│                           │
│  ── 截屏工具箱 ──          │
│  📝 识别文字               │
│  💬 智能回复               │
│  📊 表格识别               │
│                           │
│  ── 基金智能交易 ──        │
│  📈 截屏持仓分析           │
│                           │
│  ── 股票交易 ──            │
│  📊 持仓截图分析           │
│                           │
└───────────────────────────┘
```

**完整交互流程**：

```
点击悬浮球 → 展开菜单
  │
  ├─ ⭐ 快捷操作区（最顺手位置）
  │    一键执行：截图方式 + Skill 功能 已绑定
  │    点击 → 立即截屏 → 立即提交 → 完成
  │
  └─ 📷📜✋ 截屏方式区（下方）
       点击 → 截屏 → 弹出第二级菜单选功能
```

### 6.1.1 快捷操作（一键执行）

悬浮球展开后，**最顺手的位置**显示最近常用的操作。每个快捷操作是一个"截屏方式 + Skill 命令"的绑定组合，点一次就执行完整流程：

```
          ┌───────────────────┐
          │   📝 识别文字      │  ← 快捷操作 1（截图+OCR）
          │   💬 智能回复      │  ← 快捷操作 2（截图+回复）
          │   📈 持仓分析      │  ← 快捷操作 3（长截图+持仓）
          ├───────────────────┤
          │  📷  📜  ✋       │  ← 截屏方式（两级流程入口）
          └───────────────────┘
                  🔵  ← 悬浮球
```

**快捷操作数据结构**：

```kotlin
data class QuickAction(
    val skillName: String,      // "screenshot"
    val commandId: String,      // "ocr"
    val captureType: String,    // "normal"
    val displayName: String,    // "识别文字"
    val icon: String,
    val useCount: Int,          // 使用次数（用于排序）
    val lastUsedAt: Long,       // 最近使用时间
)
```

**排序规则**：按使用频率 + 最近使用时间加权排序，最多显示 3 个。

**自动学习**：每次通过两级菜单完成操作后，该"截屏方式+命令"组合的 `useCount++`，自动晋升为快捷操作。用户无需手动配置，越用越顺手。

**手动配置**：设置页也可以手动固定/移除快捷操作。

### 6.2 命令与截屏类型的匹配

每个命令声明自己支持的截屏类型（`capture_types`，复数），悬浮球根据用户选择的截屏方式过滤：

```yaml
# 命令定义中
- id: ocr
  name: 识别文字
  capture_types: [normal, long_scroll, manual_scroll]  # 三种都支持

- id: chat_reply
  name: 智能回复
  capture_types: [normal]  # 只支持普通截图

- id: ocr-analyze
  name: 截屏持仓分析
  capture_types: [normal, long_scroll]  # 支持普通和长截图
```

匹配规则：
```
用户选了"截图" → 过滤出 capture_types 包含 normal 的命令
用户选了"长截图" → 过滤出 capture_types 包含 long_scroll 的命令
用户选了"手动长截" → 过滤出 capture_types 包含 manual_scroll 的命令
```

### 6.3 菜单数据来源

```
App 启动 / 刷新
    ↓
GET /api/skills → 所有 Skill
    ↓
提取所有 input=screenshot 的命令 + 各自的 capture_types
    ↓
缓存到本地（FloatingWindowService 使用）
    ↓
用户点击悬浮球 → 第一级菜单（截屏方式）
    ↓
截屏完成 → 按 capture_type 过滤命令 → 第二级菜单（Skill 命令）
```

### 6.4 设置页 - 悬浮窗配置

用户可配置第二级菜单中哪些命令显示/隐藏：

```
┌──────────────────────────────────────┐
│  ── 悬浮窗功能管理 ──                 │
│                                      │
│  截图后可用的功能：                    │
│                                      │
│  ┌──────────────────────────────┐   │
│  │ ≡  📝 识别文字   [截屏工具箱] [✓] │   │
│  │ ≡  💬 智能回复   [截屏工具箱] [✓] │   │
│  │ ≡  📊 表格识别   [截屏工具箱] [✓] │   │
│  │ ≡  📈 截屏持仓   [基金交易]  [✓] │   │
│  │ ≡  📊 持仓截图   [股票交易]  [ ] │   │
│  └──────────────────────────────┘   │
│                                      │
│  功能来源于各 Skill 的命令定义，       │
│  新增功能请在对应 Skill 中添加命令。   │
│                                      │
└──────────────────────────────────────┘
```

---

## 七、实施步骤

### Phase 1：服务端 Skill 注册（改动最小，立即可用）

1. 实现 `SkillRegistry`：扫描 skills 目录，解析 SKILL.md frontmatter
2. 新增 API：`GET /api/skills`、`GET /api/skills/{name}`
3. 为已有的 fund-trade、stock-trade 的 SKILL.md 添加 frontmatter
4. 新建 `skills/screenshot/SKILL.md`，定义截屏类命令

**产出**：App 可通过 API 获取项目列表和命令列表。

### Phase 2：App 项目管理页面

1. 新增项目列表页 `SkillListScreen.kt`
2. 新增项目详情页 `SkillDetailScreen.kt`
3. 底部导航从 2 Tab 改为 3 Tab（项目 / 任务 / 设置）
4. `HttpClient` 新增 `getSkills()`、`getSkillDetail(name)` 方法

**产出**：用户可在 App 中浏览所有 Skill 和命令。

### Phase 3：命令执行打通

1. 新增 `POST /api/skills/{name}/run` 接口
2. `TaskExecutor` 改造：按 skill_name + command_id 路由
3. App 命令卡片点击 → 根据 input 类型弹出对应交互 → 调用 run API
4. `sa_tasks` 表新增 `skill_name`、`command_id` 字段

**产出**：从 App 项目详情页直接触发 Skill 命令。

### Phase 4：悬浮球 Skill 化

1. 悬浮球菜单从硬编码改为从 `/api/skills` 动态获取
2. 设置页菜单配置改为显示 Skill 命令来源
3. 删除 `CaptureModels.kt` 中的硬编码 `BUILTIN_ACTIONS`

**产出**：悬浮球菜单完全由 Skill 驱动。

### Phase 5：清理与迁移

1. 删除旧的 `ActionConfig` 硬编码体系
2. 任务列表筛选改为按 Skill 项目筛选
3. 迁移历史任务数据（补填 skill_name / command_id）

---

## 八、兼容性考虑

| 项目 | 策略 |
|------|------|
| 旧 task_type 字段 | 保留，新任务同时写入 task_type 和 skill_name/command_id |
| 旧 `/api/screenshot` 接口 | 保留，sync 模式的截屏命令仍走此接口 |
| 旧 `/api/tasks` 接口 | 保留不变，task 创建改为通过 `/api/skills/{name}/run` |
| SKILL.md 无 frontmatter | SkillRegistry 跳过，不影响已有 Skill 的 Claude 使用 |

---

## 九、文件改动清单

### 服务端（smart-fund-server）

| 文件 | 改动 |
|------|------|
| 新增 `services/skill_registry.py` | Skill 注册表，扫描 + 解析 SKILL.md |
| 修改 `routers/__init__.py` | 新增 `/api/skills` 系列端点 |
| 修改 `services/task_executor.py` | 按 skill_name + command_id 路由 |
| 修改 `services/task_db.py` | sa_tasks 表新增字段 |

### Skills

| 文件 | 改动 |
|------|------|
| 修改 `fund-trade/SKILL.md` | 添加 YAML frontmatter |
| 修改 `stock-trade/SKILL.md` | 添加 YAML frontmatter |
| 新增 `screenshot/SKILL.md` | 截屏工具箱 Skill 定义 |

### App 端

| 文件 | 改动 |
|------|------|
| 新增 `SkillListScreen.kt` | 项目列表页 |
| 新增 `SkillDetailScreen.kt` | 项目详情页（命令列表） |
| 修改 `MainActivity.kt` | 3 Tab 导航 |
| 修改 `HttpClient.kt` | 新增 getSkills / runSkillCommand |
| 修改 `FloatingWindowService.kt` | 菜单从 API 动态获取 |
| 修改 `SettingsScreen.kt` | 悬浮窗菜单标注 Skill 来源 |
| 删除 `CaptureModels.kt` 中硬编码 | 由 Skill 命令替代 |
