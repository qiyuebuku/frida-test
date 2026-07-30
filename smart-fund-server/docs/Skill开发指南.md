# Claude Code Skill 开发指南

> 基于项目中 apifox-manager、api-explorer、fund-trade、event-extract、claude-planner 等已有 Skill 的实际结构总结

---

## 1. 核心概念

### 什么是 Skill

Skill 是 `.claude/skills/` 下的一个目录，最少只需一个 `SKILL.md` 文件。它干两件事：

1. **声明元数据**（frontmatter YAML）：Skill 的名称、命令列表、每个命令接受什么参数。
2. **给 Claude 注入知识**（Markdown body）：执行命令时，`SKILL.md` 的正文直接注入进 Claude 的 prompt，作为工作流指引。

### 执行原理（重要）

```
用户调用 /skill-name <args>
       │
       ▼
smart-fund-server 收到请求
       │
       ▼
TaskExecutor 创建 tmux 会话
       │
       ▼
在 tmux 中启动 claude CLI（--dangerously-skip-permissions）
  工作目录 = skill 目录（SKILL.md 所在的目录）
       │
       ▼
注入 prompt = SKILL.md 正文 + $ARGUMENTS 替换为实际参数
       │
       ▼
Claude 自主执行：读文件、运行脚本、调 API...
```

**关键推论**：
- Claude 的 `Bash` 工具的 `cwd` 就是 skill 目录，所以脚本用**相对路径**即可（`python client.py ...`）
- SKILL.md 正文就是 prompt，要像写提示词一样写——清晰、结构化、有约束
- `$ARGUMENTS` 是 Claude 收到用户参数的占位符，必须放在 SKILL.md 末尾

---

## 2. Skill 分类

### 类型 A：纯指令型

**特征**：只有 `SKILL.md`，没有任何 Python 脚本。所有操作通过 curl / Bash 命令描述，Claude 直接执行。

**适用场景**：操作一个已有的 HTTP API，逻辑简单，无需本地处理。

**代表**：`api-explorer`

```
api-explorer/
├── SKILL.md             # 所有工作流都在这里，一个文件搞定
└── knowledge/           # 可选：积累的经验（各网站 API 模式）
    ├── site-patterns.md
    └── exploration-techniques.md
```

### 类型 B：CLI 工具型

**特征**：`SKILL.md` + 一个 Python CLI 脚本（argparse）。SKILL.md 只描述"如何调用这个脚本"，业务逻辑全在脚本里。

**适用场景**：需要本地计算、文件处理、复杂的 HTTP 请求逻辑（签名、分页、格式转换）。

**代表**：`apifox-manager`

```
apifox-manager/
├── SKILL.md             # 描述脚本用法和参数映射
├── apifox_client.py     # Python CLI（argparse 子命令）
└── output/              # 运行时产物（.gitignore 忽略）
```

### 类型 C：重型 Pipeline 型

**特征**：`SKILL.md` + 多个 Python 文件 + 专门的目录结构。有复杂的多阶段工作流，Claude 扮演"决策引擎"角色，Python 服务扮演"数据/执行层"。

**适用场景**：业务复杂（交易、采集、分析），涉及数据库、多个外部 API、状态管理。

**代表**：`fund-trade`、`claude-planner`

```
fund-trade/
├── SKILL.md             # 完整工作流（Step 0 到 Step 7）
├── client.py            # 对服务端 API 的统一客户端
├── prompts/             # Claude 决策用的 Prompt 模板
│   ├── daily_decision.md
│   └── review_decision.md
├── docs/                # 文档（避免 Claude 读超大文件）
│   └── CLIENT_USAGE.md
├── knowledge/           # 可选：经验积累
└── data/                # 运行时数据（.gitignore 忽略）
```

### 类型 D：纯提示型（System Prompt Skill）

**特征**：`SKILL.md` 正文就是一个精心设计的 system prompt。不被用户直接调用，而是**被其他系统程序调用**，通过 `claude -p "$(cat SKILL.md)"` 方式将其作为系统提示。

**适用场景**：结构化信息抽取、格式转换、分类打分——输出格式严格固定，不需要工具调用。

**代表**：`event-extract`

```
event-extract/
└── SKILL.md             # 完整的 system prompt + 字段定义 + few-shot 示例
```

---

## 3. SKILL.md 格式规范

### 3.1 Frontmatter 字段全表

```yaml
---
# ===== 必填 =====
name: my-skill                 # 唯一 ID，用于 URL 路由和 CLI 调用（小写 + 连字符）
commands:                      # 至少一个命令，否则 SkillRegistry 跳过此 Skill
  - id: run                    # 命令唯一 ID（必填）
    name: 执行操作              # 前端显示名（必填）

# ===== 推荐填写 =====
display_name: 我的技能          # 前端/移动端显示的完整名称
description: 一句话描述这个 Skill 的用途
icon: star                     # Material Design 图标名（见 fonts.google.com/icons）
category: tools                # 分类：tools / finance / security / creative
user-invocable: true           # false = 只供程序调用，不出现在用户界面

# ===== 命令字段详解 =====
commands:
  - id: export                 # 命令 ID，/skill-name export 触发
    name: 导出数据              # 显示名
    description: 从...导出...   # 详细描述（前端 tooltip）
    input: text                # 输入类型：none / text / screenshot / file
    executor: claude           # 执行器（目前只有 claude）
    estimated_time: 60         # 预估耗时（秒），用于前端进度提示
    args:                      # 参数列表（用户填写，注入到 $ARGUMENTS）
      - name: project_id
        description: 项目 ID 或 URL
        required: true
      - name: format
        description: 输出格式（JSON / YAML），默认 JSON
        required: false
---
```

**字段说明**：

| 字段 | 是否必填 | 说明 |
|------|---------|------|
| `name` | 必填 | 全局唯一，小写字母 + 连字符，用于 `/skill-name` 命令 |
| `commands[].id` | 必填 | 命令唯一 ID |
| `commands[].name` | 必填 | 前端显示名 |
| `display_name` | 推荐 | 可以包含中文和特殊字符 |
| `description` | 推荐 | 一句话，帮助用户理解触发时机 |
| `icon` | 可选 | Material Design 图标名，控制前端图标 |
| `category` | 可选 | 用于前端分类展示 |
| `user-invocable` | 可选 | 默认 true；设为 false 则不在用户界面出现 |
| `input` | 推荐 | `none`=无输入 / `text`=文本 / `screenshot`=截图 / `file`=文件 |
| `estimated_time` | 推荐 | 秒，前端显示"预计 X 秒" |
| `args[].required` | 推荐 | true=必填，false=可选 |

### 3.2 Markdown Body 写法

Body 直接注入 Claude 的 prompt，**就是提示词**。写法准则：

**✅ 应该包含**：
- 执行命令的精确语法（完整的 bash 命令，不要省略参数名）
- 工作流阶段划分（Phase 1 / Phase 2 / Step N）
- 约束和禁止事项（"禁止直接 curl API"、"必须先 preflight"）
- 常见的错误处理（"如果返回 402，说明..."）
- 输出格式要求（"最终汇总输出包含...字段"）
- `$ARGUMENTS`（必须放在文件末尾，接收用户参数）

**❌ 不应该包含**：
- 对话口吻（"好的，我来帮你..."）
- 模糊描述（"适当地处理错误"、"视情况而定"）
- 重复 frontmatter 已有的元数据
- 大段实现细节（这些放进 Python 脚本里）

**Body 模板结构**：

```markdown
# [Skill 名称] Skill

## 执行方式 / 核心指引

[最关键的一句话：怎么运行 + 工作目录在哪]

## 工作流程

### Phase 1: [阶段名]

[具体命令，带完整参数]

### Phase 2: [阶段名]

[...以此类推]

## 约束 / 注意事项

- 约束 1
- 约束 2

## 参考

[可选：API 参考表、常见模式]

$ARGUMENTS
```

---

## 4. 目录结构规范

### 最小结构（纯指令型）

```
my-skill/
└── SKILL.md
```

### 标准结构（CLI 工具型）

```
my-skill/
├── SKILL.md              # Skill 定义 + 工作流指引
├── my_tool.py            # Python CLI（argparse）
└── output/               # 运行时产物（sync 时排除）
```

### 完整结构（重型 Pipeline 型）

```
my-skill/
├── SKILL.md              # Skill 定义 + 完整工作流
├── client.py             # 服务端 API 的统一客户端（thin wrapper）
│
├── prompts/              # Claude 决策用的 Prompt 模板
│   ├── main_decision.md  # 核心决策 prompt（供 Claude Read 后填充）
│   └── review.md
│
├── docs/                 # 文档（避免 Claude 直接读超大的 client.py）
│   └── CLIENT_USAGE.md   # 命令速查表（精简版）
│
├── knowledge/            # 经验积累（Claude 自主更新）
│   ├── pitfalls.md       # 踩坑记录
│   └── patterns.md       # 常见模式
│
├── data/                 # 运行时数据（.gitignore 忽略）
├── output/               # 输出产物（sync 时排除）
└── config.json           # 配置文件（如果有）
```

**目录命名约定**：

| 目录 | 用途 | 是否同步到远程 |
|------|------|--------------|
| `output/` | 运行产物（JSON 导出、生成文件） | 否（rsync 排除） |
| `data/` | 运行时数据（持久化状态） | 否 |
| `knowledge/` | 经验知识库（Claude 可更新） | 是 |
| `prompts/` | Prompt 模板 | 是 |
| `docs/` | 给 Claude 看的文档 | 是 |
| `__pycache__/` | Python 缓存 | 否（rsync 排除） |

---

## 5. Python CLI 设计模式

适用于**类型 B（CLI 工具型）**和**类型 C（Pipeline 型）**中的客户端脚本。

### 5.1 文件结构模板

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[Skill 名称] 客户端

一行描述功能。

用法:
    python client.py <command> [args...]
    python client.py --help
"""

import os
import sys
import json
import argparse

# ========== 1. 清理代理（WSL2 环境必须） ==========
# WSL2 的 http_proxy 会导致请求走代理，干扰局域网/私有 API 访问
for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
    os.environ.pop(k, None)

import httpx  # 或 requests，在清理代理之后 import

# ========== 2. 配置常量 ==========
BASE_URL = os.environ.get("API_BASE_URL", "http://default-host:8900")
DEFAULT_TIMEOUT = 30

# ========== 3. 工具函数 ==========

def output_json(data: dict):
    """统一输出 JSON（AI 解析的标准格式）"""
    print(json.dumps(data, ensure_ascii=False, indent=2))

def output_error(msg: str, exit_code: int = 1):
    """统一错误输出"""
    print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False), file=sys.stderr)
    sys.exit(exit_code)

def get_client() -> httpx.Client:
    """构造 HTTP 客户端"""
    return httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)

# ========== 4. 命令实现 ==========

def cmd_health(args):
    """健康检查"""
    with get_client() as c:
        r = c.get("/health")
        r.raise_for_status()
        output_json(r.json())

def cmd_do_something(args):
    """执行某操作"""
    # 实现...
    result = {"status": "ok", "data": {...}}
    output_json(result)

# ========== 5. CLI 入口 ==========

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    
    # 全局参数（如 token）
    parser.add_argument("--token", default=os.environ.get("MY_TOKEN"), help="API 令牌（或 MY_TOKEN 环境变量）")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # 子命令：health
    subparsers.add_parser("health", help="健康检查")
    
    # 子命令：do-something
    p = subparsers.add_parser("do-something", help="执行某操作")
    p.add_argument("target", help="目标 ID 或 URL")
    p.add_argument("--format", choices=["json", "yaml"], default="json", help="输出格式")
    p.add_argument("--dry", action="store_true", help="模拟运行，不实际执行")
    
    args = parser.parse_args()
    
    # 分发
    dispatch = {
        "health": cmd_health,
        "do-something": cmd_do_something,
    }
    
    try:
        dispatch[args.command](args)
    except httpx.HTTPStatusError as e:
        output_error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except httpx.RequestError as e:
        output_error(f"请求失败: {e}")
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()
```

### 5.2 关键设计决策

**输出必须是 JSON**：

Claude 通过 Bash 工具执行脚本，靠解析 stdout 获取结果。JSON 格式便于 Claude 理解。

```python
# ✅ 好的输出
{"status": "ok", "order_no": "00000001234", "amount": 500}

# ❌ 坏的输出
订单提交成功！订单号：00000001234，金额 500 元
```

**统一的 status 字段**：

```python
# 成功
{"status": "ok", "data": {...}, "stats": {"processed": 10}}

# 失败
{"status": "error", "message": "具体错误原因"}
```

**Token 优先级**：命令行参数 > 环境变量 > 配置文件

```python
token = args.token or os.environ.get("MY_TOKEN") or load_from_config()
if not token:
    output_error("未设置 Token，用 --token 参数或 MY_TOKEN 环境变量")
```

**大文件问题**：当 client.py 超过 ~60K tokens 时，Claude 不能直接读取。解决方案：
- 提供 `docs/CLIENT_USAGE.md`（精简版命令速查）
- 在 SKILL.md 中明确写："不要直接读取 client.py，执行 `python client.py --help`"

---

## 6. 完整 Skill 创建模板

### Step 1：复制 SKILL.md 模板

```yaml
---
name: my-skill
display_name: 我的技能（中文名）
icon: auto_fix_high
description: 一句话描述：当用户需要[什么]时使用，可以[做什么]
category: tools
commands:
  - id: run
    name: 执行主操作
    description: 详细说明这个命令做什么
    input: text
    executor: claude
    estimated_time: 60
    args:
      - name: target
        description: 目标（ID / URL / 关键词）
        required: true
      - name: mode
        description: 模式（fast / thorough），默认 fast
        required: false

  - id: check
    name: 检查状态
    description: 检查...的当前状态
    input: none
    executor: claude
    estimated_time: 10
---

# 我的技能 Skill

## 执行方式

**工作目录已是 skill 目录**，直接运行：

```bash
python my_tool.py [command] [args...]
```

## 工作流程

### Step 1: 前置检查

```bash
python my_tool.py health
```

如果服务不可用，[说明恢复方式]。

### Step 2: 执行主操作

```bash
python my_tool.py run <target> [--mode fast]
```

**参数说明**：
- `target`: 目标 ID 或 URL
- `--mode fast`：快速模式（默认）；`--mode thorough`：彻底模式

### Step 3: 处理结果

命令输出 JSON，字段含义：
- `status`: ok / error
- `data.result`: 操作结果
- `data.count`: 处理数量

## 约束

- [约束 1，如：必须在 Step 1 之后才能执行 Step 2]
- [约束 2，如：失败时不要重试超过 3 次]
- [WSL2 注意：所有 curl 命令加 `--noproxy '*'`]

$ARGUMENTS
```

### Step 2：根据类型选择是否创建 Python 脚本

| Skill 类型 | 需要的文件 |
|-----------|-----------|
| 纯指令型 | 只需 SKILL.md |
| CLI 工具型 | SKILL.md + `my_tool.py`（用上面的 Python 模板） |
| Pipeline 型 | SKILL.md + `client.py` + `prompts/` + `docs/CLIENT_USAGE.md` |
| 纯提示型 | 只需 SKILL.md（body 是 system prompt） |

---

## 7. 开发流程

### 7.1 创建

```bash
# 1. 建目录
mkdir -p /home/yuyang/frida-test/.claude/skills/my-skill

# 2. 写 SKILL.md（参考模板）
vi /home/yuyang/frida-test/.claude/skills/my-skill/SKILL.md

# 3. 如果需要，创建 Python 脚本
vi /home/yuyang/frida-test/.claude/skills/my-skill/client.py
```

### 7.2 本地测试

**方式一：直接用 Claude CLI 测试**

```bash
cd /home/yuyang/frida-test/.claude/skills/my-skill

# 用当前目录作为工作目录，注入 SKILL.md 内容测试
claude --dangerously-skip-permissions \
  -p "$(cat SKILL.md | sed 's/\$ARGUMENTS/用户参数在这里/')"
```

**方式二：测试 Python 脚本**

```bash
cd /home/yuyang/frida-test/.claude/skills/my-skill
python client.py health
python client.py run "test-target" --dry
```

### 7.3 同步到本地 cc-switch

**每次修改 SKILL.md 或相关文件后必须运行**：

```bash
bash /home/yuyang/frida-test/.claude/skills/sync-to-cc-switch.sh
```

这个脚本会把所有包含 `SKILL.md` 的目录 rsync 到 `~/.cc-switch/skills/`，让本地 Claude Code 能识别到新 skill。

### 7.4 部署到远程服务器

```bash
cd /home/yuyang/frida-test/.claude/skills

# 同步代码 + 重启所有服务（标准部署）
./deploy.sh

# 只同步代码，不重启（服务在运行中不想中断）
./deploy.sh --sync-only

# 查看服务状态
./deploy.sh --status

# 远程健康检查
./deploy.sh --test
```

部署后，通过 smart-fund-server API 重载 Skill 注册表：

```bash
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/skills/reload
curl --noproxy '*' -s http://119.23.227.187:8900/api/skills | python3 -m json.tool
```

### 7.5 迭代优化

```
发现问题
  │
  ├─ 工作流逻辑问题 → 修改 SKILL.md 正文
  │
  ├─ 脚本 bug → 修改 Python 文件
  │
  ├─ 经验沉淀 → 更新 knowledge/ 下的 .md 文件（Claude 执行时会引用）
  │
  └─ Prompt 调优 → 修改 prompts/ 下的模板文件
  
修改后：sync-to-cc-switch.sh → （需要远程测试时）deploy.sh
```

---

## 8. 最佳实践与注意事项

### 8.1 SKILL.md 写作

**把所有约束写成"禁止"而非"建议"**

```markdown
# ✅ 清晰的约束
- 禁止直接读取 client.py（超过 60K tokens）
- 必须通过 `python client.py --help` 查询用法

# ❌ 模糊的建议
- 如果 client.py 太大可以考虑不直接读取
```

**工作流用 Phase/Step 编号**，让 Claude 有明确的推进节点

```markdown
### Phase 1: 数据采集（必须完成后才进入 Phase 2）

### Phase 2: 分析决策

### Phase 3: 执行
```

**需要 Claude 读取文件的，给出精确路径**

```markdown
# ✅ 精确路径
读取 `prompts/daily_decision.md` 模板

# ❌ 模糊描述
读取决策模板
```

### 8.2 Python 脚本

**WSL2 代理问题**：所有 Python 脚本头部必须清理代理环境变量（如模板所示），否则私有 API 会走代理失败。

**curl 命令**：SKILL.md 中所有 curl 必须加 `--noproxy '*'`

```bash
# ✅ WSL2 兼容
curl --noproxy '*' -s http://119.23.227.187:8900/health

# ❌ 可能被代理劫持
curl -s http://119.23.227.187:8900/health
```

**大文件保护**：当你的脚本超过 2000 行时，拆分为 `client.py`（Claude 调用入口，保持精简）+ 内部模块（`services/`, `utils/` 等）。

**参数文档**：每个子命令加 `help=` 说明，Claude 通过 `--help` 自学用法时会用到。

### 8.3 服务依赖的 Skill

如果 Skill 依赖一个后台服务（如 fund-trade 依赖 smart-fund-server），在 SKILL.md 中必须有**服务检查 + 恢复步骤**：

```markdown
### Step 0: 服务检查

```bash
python client.py health
```

如果不可用，重启服务：

```bash
cd /home/yuyang/frida-test/.claude/skills && ./deploy.sh --restart
```
```

### 8.4 知识库文件（knowledge/）

知识库用于积累不适合写死在 SKILL.md 里的"动态经验"：

- `pitfalls.md`：踩过的坑（每次遇到新问题追加）
- `patterns.md`：发现的规律（网站 API 模式、常见响应格式）
- `strategies.md`：有效的策略（哪种方法在什么场景有效）

Claude 可以在执行过程中自主更新这些文件，形成"自学习"能力。在 SKILL.md 中显式告知：

```markdown
## 经验沉淀

遇到新问题或发现新模式时，更新 `knowledge/pitfalls.md`，避免重复犯错。
```

### 8.5 `$ARGUMENTS` 的位置

`$ARGUMENTS` **必须放在 SKILL.md 末尾**，是用户参数的注入点。如果放在中间，可能与其他内容混淆。

```markdown
# ... 所有指引内容 ...

$ARGUMENTS
```

---

## 9. 各类型 Skill 对照速查

| 维度 | 纯指令型 | CLI 工具型 | Pipeline 型 | 纯提示型 |
|------|---------|-----------|------------|---------|
| 代表 | api-explorer | apifox-manager | fund-trade | event-extract |
| 最少文件 | SKILL.md | SKILL.md + .py | SKILL.md + client.py + prompts/ | SKILL.md |
| 工作流复杂度 | 简单 | 中等 | 复杂 | N/A |
| Python 脚本 | 无 | 1 个（argparse） | 多个 | 无 |
| 用户直接调用 | 是 | 是 | 是 | 否（程序调用） |
| 有后台服务 | 否 | 否 | 通常有 | 否 |
| 适合新手 | ✅ | ✅ | ⚠️ | ✅ |

---

## 10. 快速检查清单

新建 Skill 后，逐项验证：

- [ ] `SKILL.md` frontmatter 有 `name` 和至少一个 `commands[].id`
- [ ] `commands[].input` 设置正确（`none` / `text` / `screenshot`）
- [ ] `$ARGUMENTS` 在 SKILL.md 末尾
- [ ] 所有 curl 命令加了 `--noproxy '*'`
- [ ] Python 脚本头部清理了代理环境变量
- [ ] Python 脚本输出 JSON（不是纯文本）
- [ ] 如有后台服务依赖，SKILL.md 有服务检查 + 恢复步骤
- [ ] 运行了 `sync-to-cc-switch.sh`
- [ ] 如需远程部署，运行了 `deploy.sh` 并验证 `/api/skills/reload`
