---
name: claude-planner
display_name: Claude 方案规划
icon: architecture
description: 调用真实 Claude 账号（多账号降级）生成高质量技术方案文档，供本地模型按文档实现代码
category: tools
commands:
  - id: plan
    name: 生成技术方案
    description: 将需求发送给真实 Claude 账号，生成详细的技术方案文档
    input: text
    executor: claude
    estimated_time: 120
---

# Claude 方案规划 Skill

用高能力模型做规划决策（发散性思维），用低成本模型做编码实现（收敛性执行），在效果和成本之间取得平衡。支持多账号自动降级。

## 账号配置与降级

账号配置文件：`.claude/skills/claude-planner/accounts.json`

**必须按 `priority` 字段从小到大依次尝试**（priority=1 最先尝试）。当前账号调用失败（额度不足、网络错误、rate limit）时降级到下一个。**禁止跳过低 priority 账号**——即使用户指定了 opus 模型，也必须从 priority=1 的账号开始尝试（用 opus 调用它），而非跳到 default_model=opus 的账号。

### 模型选择

优先级：**用户指定 > 自动判断 > 账号的 `default_model`**。

- 用户指定了模型（如"用 opus"）→ 所有账号都用该模型调用，忽略 `default_model`
- 用户未指定 → 根据任务难度自动选择（见下表），如果也无法判断 → 使用当前账号的 `default_model`

自动判断规则：

| 难度 | 模型 | 判断依据 |
|------|------|----------|
| 高 | `opus` | 跨模块架构设计、复杂状态管理、并发/分布式、需要权衡多个方案 |
| 中/低 | `sonnet` | 单模块功能、CRUD、配置修改、bug 修复、明确需求的实现 |

做判断时简单想一下：这个需求是否需要深度思考和权衡？需要 → opus，不需要 → sonnet。

### 构建命令

读取 `accounts.json`，根据账号类型构建命令。

**关键**：必须同时做两件事：
1. `--setting-sources ''` 禁止子进程加载 `~/.claude/settings.json`
2. `env -u` 清除**所有**从父进程继承的 Anthropic 相关环境变量（包括模型映射变量）

**必须清除的环境变量清单**（父进程 settings.json 的 env 块会注入这些）：
```
ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_MODEL,
ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL,
ANTHROPIC_DEFAULT_HAIKU_MODEL, ANTHROPIC_REASONING_MODEL,
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, API_TIMEOUT_MS
```

对于 OAuth 账号：
```bash
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN \
  -u ANTHROPIC_MODEL -u ANTHROPIC_DEFAULT_OPUS_MODEL \
  -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL \
  -u ANTHROPIC_REASONING_MODEL -u CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC \
  -u API_TIMEOUT_MS \
  claude -p --model <模型> --setting-sources '' --output-format json \
  "$(cat /tmp/claude_planner_prompt.md)"
```

对于 API 账号，清除所有变量后重新设置需要的：
```bash
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN \
  -u ANTHROPIC_MODEL -u ANTHROPIC_DEFAULT_OPUS_MODEL \
  -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL \
  -u ANTHROPIC_REASONING_MODEL -u CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC \
  -u API_TIMEOUT_MS \
  ANTHROPIC_AUTH_TOKEN="<token>" ANTHROPIC_BASE_URL="<url>" \
  claude -p --model <模型> --setting-sources '' --settings '{"skipDangerousModePermissionPrompt":true}' --output-format json \
  "$(cat /tmp/claude_planner_prompt.md)"
```

**注意**：`--model` 参数必须用完整模型 ID（如 `claude-sonnet-4-6`、`claude-opus-4-6`），不要用别名（如 `sonnet`、`opus`），因为别名可能被残留的环境变量映射到错误模型。

**判断额度用完**：`is_error: true` 且 result 包含 `hit your limit` 或 `resets`，降级到下一个账号。

## 工作流程

你（当前 agent）收到用户需求后：

### 1. 收集上下文

根据需求自行判断需要哪些项目上下文（源码、目录结构、文档、schema 等）。

### 2. 确定输出路径

先确定方案文档要保存到哪里（根据项目结构自行判断），确保目录存在。

### 3. 构建 prompt（必须使用固定模板）

**禁止自己编写 prompt**。必须使用固定模板文件。

**重要：所有 /tmp/ 文件操作必须通过 Bash 工具执行，禁止使用 Write 工具**（Write 工具没有 /tmp 写入权限）。

上下文内容应包括：
1. **用户需求**（原文或整理后的版本）
2. **关键文件内容**（源码、文档、schema 等）
3. **项目目录结构概览**（让 Claude 知道还有哪些文件可以自己去读）

Claude 在 `-p` 模式下仍然可以使用 Read/Grep/Glob/Bash 工具自主探索项目文件。模板中已告诉 Claude：如果提供的上下文不够，可以自行读取补充。所以你不需要事无巨细地收集所有文件——**收集核心文件，提供目录结构让 Claude 自己判断还需要什么**。

用一条 Bash 命令完成全部操作：
```bash
# 1. 先把上下文+需求写入临时文件（建议在末尾附上项目目录结构）
cat > /tmp/claude_planner_context.md << 'CONTEXT_EOF'
你收集到的项目上下文和用户需求...

## 项目目录结构（供自行探索）
（在这里贴上 tree 或 ls 的输出，让 Claude 知道有哪些文件可以读取）
CONTEXT_EOF

# 2. 复制模板并替换占位符
cp .claude/skills/claude-planner/prompt_template.md /tmp/claude_planner_prompt.md
python3 -c "
t = open('/tmp/claude_planner_prompt.md').read()
c = open('/tmp/claude_planner_context.md').read()
open('/tmp/claude_planner_prompt.md', 'w').write(t.replace('{{CONTEXT_AND_REQUIREMENT}}', c))
"
echo "Prompt ready: $(wc -c < /tmp/claude_planner_prompt.md) bytes"
```

### 4. 调用 Claude

读取 `accounts.json`，**按 `priority` 字段升序排列后**依次尝试。调用前**必须先告诉用户**：

> 正在尝试账号 `claude-oauth`（OAuth 登录），模型 `claude-sonnet-4-6`，API: `Anthropic 官方`

```bash
env ... claude -p --model <模型> --setting-sources '' --output-format json \
  "$(cat /tmp/claude_planner_prompt.md)" \
  > /tmp/claude_planner_result.json 2>/tmp/claude_planner_stderr.log
```

调用成功后提取内容：
```bash
python3 << 'PYEOF'
import json
d = json.load(open('/tmp/claude_planner_result.json'))
result = d.get('result', '')
if 'hit your limit' in result or 'resets' in result:
    print('QUOTA_EXCEEDED')
else:
    with open('<目标路径>.md', 'w') as f:
        f.write(result)
    mu = d.get('modelUsage', {})
    for model, info in mu.items():
        print(f'model={model} input={info.get("inputTokens",0)} output={info.get("outputTokens",0)} cost=${info.get("costUSD",0):.4f}')
    print(f'duration={d.get("duration_ms",0)}ms')
PYEOF
```

输出 `QUOTA_EXCEEDED` 则降级到下一个账号重试。

### 5. 告知用户（必须包含以下信息）

从 `/tmp/claude_planner_result.json` 中提取报告信息：

- 方案文件路径
- 使用了哪个账号（accounts.json 中的 `name`）及其 API 地址（OAuth 为 `Anthropic 官方`，API 账号为 `env_set.ANTHROPIC_BASE_URL` 的值）
- **实际使用的模型**：从 `modelUsage` 字段的 key 提取（如 `claude-sonnet-4-6`），这是 API 返回的真实模型 ID
- Token 用量：从 `modelUsage` 中提取 `inputTokens`、`outputTokens`
- 费用：`modelUsage` 中的 `costUSD` 或顶层 `total_cost_usd`
- 耗时：`duration_ms` 字段
- 如果发生了降级，说明原因

## Prompt 模板

**模板文件**：`.claude/skills/claude-planner/prompt_template.md`

**禁止修改模板内容**。你唯一要做的是把收集到的上下文和用户需求写入 `/tmp/claude_planner_context.md`，然后用 python 替换模板中的 `{{CONTEXT_AND_REQUIREMENT}}` 占位符。模板中的系统指令、输出格式、原则等已经设计好了，不需要你重写。

## 注意事项

- `claude -p` 的 prompt 大小有限制，上下文太大时只挑关键文件
- 超时默认 2 分钟，复杂需求可能需要更久
- 所有账号都失败时，如实告诉用户每个账号的失败原因

## 用户请求

$ARGUMENTS
