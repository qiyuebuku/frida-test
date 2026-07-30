# novel-write 架构优化方案

> 日期：2026-02-10
> 状态：**方案评审中**

本方案包含三项独立但可叠加的优化：

| 优化 | 解决什么 | 核心手段 |
|------|----------|----------|
| **优化一：Subagent 上下文隔离** | Research/Planning 的废气污染 Writer 上下文 | Task 工具启动独立 subagent |
| **优化二：分场景增量生成** | Writer 一次性生成整章，无法中途查阅和修正 | 按场景逐段生成，Scene Writer subagent 隔离 KB |
| **优化三：Reviewer Subagent** | Writer 与 Reviewer 共享上下文，Writer 可推断评审标准 | Reviewer 独立 subagent，形成完整闭环 |

三者解决不同层面的问题，可以独立实施，也可以组合使用。组合后形成完整的 subagent 流水线：

```
Research (sonnet) → Plan (sonnet) → Write (opus × N) → Review (sonnet)
    ↓释放              ↓释放            ↓每场景释放          ↓释放
```

---

## 一、问题分析

### 问题 1：上下文废气

`novel-write` 在**单一上下文**中执行全部流程：

```
同一上下文
┌──────────────────────────────────────────────────────┐
│  Step 1: 读 guide.md                                  │
│  Step 2: 读 10+ KB 文件 → 生成 Context Bundle + 大纲   │  ← 上下文膨胀
│  Step 3: 读近章原文 → 生成 Scene Plan                   │  ← 继续膨胀
│  Step 4: 等待用户确认                                   │
│  Step 5: 写正文                                        │  ← 上下文已经很重
│  Step 6-8: 保存 + L1 验证 + 询问修复                    │
└──────────────────────────────────────────────────────┘
```

到 Step 5 写正文时，上下文已包含：

| 内容 | 估计 token |
|------|-----------|
| SKILL.md 系统指令 | ~4K |
| guide.md | ~2K |
| 10+ KB 文件原文（plot/characters/world/style/text...） | ~15-25K |
| Context Bundle 生成过程（推理 + 产出） | ~5K |
| 大纲生成过程（推理 + 产出） | ~3K |
| Scene Plan 生成过程（推理 + 产出） | ~4K |
| 用户确认交互 | ~1K |
| **总计** | **~35-45K** |

而 Writer 真正需要的只有 ~5.5K（SKILL.md 写作规则 + Context Bundle + Scene Plan）。**80% 的上下文是废气。**

### 问题 2：一次性生成

当前 Step 5 一次性生成整章正文（~5000 字）。这等于：

```python
# 当前写作方式（类比）
context = read_all_docs()          # 一次性读完所有文档
plan = make_full_plan(context)     # 做完整规划
output = generate_entire_file()    # 一口气写完整个文件
save(output)
```

```python
# 理想写作方式（类比写代码）
plan = make_plan()
for scene in plan.scenes:
    refs = lookup_what_i_need(scene)   # 按需查阅
    text = write_scene(scene, refs)    # 写一段
    review(text)                       # 看看写得对不对
    if need_more_info:
        more = lookup_kb(question)     # 中途发现需要查资料
save(all_scenes)
```

一次性生成的问题：

1. **KB 查阅是预测式的**：Context Bundle 试图预先包含所有写作所需信息，但很多需求是写到具体场景时才出现的（"这个角色的法术叫什么来着？"）
2. **无法中途修正**：一旦开始生成，Writer 无法停下来检查角色设定、查阅地理信息、确认力量体系细节
3. **上下文利用率低**：为了覆盖所有可能场景，Context Bundle 包含很多当前段落不需要的信息
4. **错误会扩散**：前面场景的风格偏差会传染到后续场景，因为没有中间检查点

### 问题后果

| 后果 | 问题 1 导致 | 问题 2 导致 |
|------|------------|------------|
| 注意力稀释 | ✅ 从 45K 中定位 5.5K 有效信息 | |
| 格式退化 | ✅ 上下文重 → 退回训练均值 | ✅ 长文本生成后段越来越差 |
| 信息遗漏 | | ✅ 预测式加载无法覆盖所有需求 |
| 无法自我修正 | | ✅ 一次性输出没有检查点 |
| 修复效率低 | ✅ 臃肿上下文中修复 | ✅ 改一处可能破坏其他场景 |

---

## 二、优化一：Subagent 上下文隔离

### 2.1 核心思路

利用 Claude Code 的 **Task 工具**启动 subagent，将 Research 和 Planning 阶段从 Writer 上下文中剥离。

```
主 Agent（Orchestrator + Writer）
  │
  ├─ Task subagent: Researcher          ← 独立上下文
  │   输入：KB_DIR, 章节号, hint
  │   执行：读 10+ KB 文件 → 推理 → 生成 Context Bundle + 大纲
  │   产出：两个文件落盘
  │   结束后上下文释放（KB 原文、推理过程全部消失）
  │
  ├─ 主 Agent 读取 Context Bundle + 大纲（~5KB 文件内容）
  │
  ├─ Task subagent: Planner             ← 独立上下文
  │   输入：Context Bundle + 大纲 + 近章原文
  │   执行：生成 Scene Plan（6 维度）+ 选取风格锚点
  │   产出：一个文件落盘
  │   结束后上下文释放
  │
  ├─ 主 Agent 读取 Scene Plan → 展示给用户确认
  │
  └─ 主 Agent（Writer 角色）             ← 干净上下文
      只有：SKILL.md 写作规则 + Context Bundle + Scene Plan
      执行：写正文 → 保存 → L1 验证
```

### 2.2 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A | 仅 Research 分离 | 改动最小，~70% 收益 | Scene Plan 仍在主 Agent |
| **B** | **Research + Planner 分离** | **Writer 上下文最干净** | **多一次 subagent 调用** |
| C | 全分离（含 Writer） | 完全独立 | Writer 无法与用户交互，过度工程化 |

**推荐方案 B**：Research 和 Planning 的推理废气都不会污染 Writer，同时 Writer 仍在主 Agent 中可与用户交互。

### 2.3 文件结构变化

```
.claude/skills/novel-write/
├── SKILL.md                          # 改造：Orchestrator 角色
├── templates/
│   ├── context_bundle.md             # 不变
│   ├── research_agent.md             # 新增：Researcher subagent 的完整指令
│   ├── planner_agent.md              # 新增：Planner subagent 的完整指令
│   └── reviewer_agent.md             # 新增：Reviewer subagent 的完整指令
└── verify/
    ├── batch_verify.py               # 改造：仅保留 L1 定量检测（纯 Python）
    └── prompts/                      # L2/L3 逻辑迁移到 reviewer_agent.md
```

### 2.4 Researcher Subagent

#### 触发方式

主 Agent（Orchestrator）在 Step 2 中：

```
1. 读取 templates/research_agent.md（模板）
2. 填入变量：KB_DIR, CHAPTER_NUM, HINT
3. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"（Research 不需要 opus）
   - prompt: 填充后的 research_agent.md 内容
```

#### research_agent.md 内容结构

```markdown
# Research Agent 指令

你是小说续写系统的 Research Agent。你的任务是从知识库中提取写作所需的上下文，
生成 Context Bundle 和章节大纲，保存到文件。

## 输入参数

- KB_DIR: {KB_DIR}
- 目标章节号: {N}
- 用户提示（hint）: {HINT}

## 执行步骤

### 1. 确定章节号

读取 `{KB_DIR}/plot/chapters/index.md`，从中确定：
- 最新已完成章节号 → N-1
- 本次要写的章节号 → N = {N}（如果用户指定）或自动 +1

### 2. 读取文件（仅章节级信息）

Researcher 只读**章节级上下文**，不碰场景级信息（角色档案、世界设定）。

| 文件 | 提取什么 | 填充 Bundle |
|------|----------|-------------|
| `{KB_DIR}/plot/chapters/index.md` | 章节号、进度 | S1 |
| `{KB_DIR}/plot/outline/index.md` → 当前弧 `arc_XX.md` | 弧详情、本章方向 | S1 |
| `{KB_DIR}/plot/outline/plot_lines.md` | 主线状态 | S1 |
| `{KB_DIR}/plot/open_loops.md` | 待回收/推进的伏笔 | S2 |
| `{KB_DIR}/plot/chapters/ch{N-1}.md`（+ ch{N-2}.md） | 近章摘要 | S1, S2 |
| `{KB_DIR}/text/ch{N-1}.md`（最后 500 字） | 上一章尾部衔接段 | S5 |
| `{KB_DIR}/style/narrative.md` | 叙事风格 + 情感禁忌 | S3, S4 |
| `{KB_DIR}/style/vocabulary.md` | 用词禁忌 | S4 |
| `{KB_DIR}/style/rhythm.md` | 节奏指标 | S3 |

**不要读取以下文件**（这些是场景级信息，由 Planner 和 Writer 按需读取）：
- `characters/*.md` — 角色档案
- `world/*.md` — 世界设定
- `reader/feedback/*.md` — 读者反馈

S2 中的角色列表从弧详情和近章摘要中提取（名字 + 一句话状态），不需要读角色档案原文。

### 4. 生成 Context Bundle

按以下模板生成，控制在 ~3000 字以内：
{context_bundle.md 模板内容完整嵌入}

保存到 `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`

### 5. 生成章节大纲

基于已读的全部 KB 数据生成事件序列大纲：
{大纲格式完整嵌入}

大纲不含任何写法指导。

保存到 `{KB_DIR}/drafts/outline_ch{NNNN}.md`

### 6. 返回摘要

完成后返回一行摘要：
"已生成 Context Bundle 和大纲，保存到 {KB_DIR}/drafts/"
```

#### 关键设计点

1. **模板是自包含的**：research_agent.md 包含了 subagent 需要的所有指令，不依赖 SKILL.md
2. **Context Bundle 模板直接嵌入**：避免 subagent 还需要读取 `templates/context_bundle.md`
3. **变量占位符**：`{KB_DIR}`、`{N}`、`{HINT}` 由主 Agent 在构造 prompt 时替换
4. **模型选择**：sonnet 足够完成 Research 任务，成本更低、速度更快
5. **职责边界**：Researcher 只读章节级信息（剧情进度、伏笔、风格规则），不碰场景级信息（角色档案、世界设定）。场景类型判断是 Planner 的职责，角色/世界细节由 Writer 按需查阅

### 2.5 Planner Subagent

#### 触发方式

主 Agent 在 Step 3 中：

```
1. 读取 Researcher 生成的 Context Bundle 和大纲（确认文件存在）
2. 读取 templates/planner_agent.md（模板）
3. 填入变量：KB_DIR, CHAPTER_NUM, HINT
4. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 填充后的 planner_agent.md 内容
```

#### planner_agent.md 内容结构

```markdown
# Planner Agent 指令

你是小说续写系统的 Planner Agent。你的任务是基于 Context Bundle 和大纲，
生成详细的 Scene Plan（场景写作规划）和风格锚点，保存到文件。

## 输入参数

- KB_DIR: {KB_DIR}
- 目标章节号: {N}
- 用户提示（hint）: {HINT}

hint 是场景规划的核心变量——用户可能指定本章重点人物、偏好的场景类型（战斗/情感/日常）、想推进的主线。Scene Plan 的场景权重分配、KB 查阅范围都应受 hint 影响。

## 执行步骤

### 1. 读取输入文件

**必读**：

| 文件 | 用途 |
|------|------|
| `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md` | 写作上下文（硬指标 + 禁忌） |
| `{KB_DIR}/drafts/outline_ch{NNNN}.md` | 事件序列大纲 |
| `{KB_DIR}/text/ch{N-1}.md` | 近章原文（选取风格锚点） |
| `{KB_DIR}/text/ch{N-2}.md`（如存在） | 更多风格参考 |

**场景级 KB**（根据大纲中的场景类型按需读取）：

| 条件 | 文件 | 用途 |
|------|------|------|
| 大纲涉及角色互动 | `{KB_DIR}/characters/{name}.md` | 角色说话特征 → 对话风格维度 |
| 大纲涉及战斗/修炼 | `{KB_DIR}/world/power_system.md` | 能力细节 → 细节来源维度 |
| 大纲涉及地点转换 | `{KB_DIR}/world/geography.md` | 场景环境 → 细节来源维度 |
| 大纲涉及势力交涉 | `{KB_DIR}/world/factions.md` | 势力关系 → 场景规划 |

**不要读取大纲中未涉及的角色档案或世界设定。**

Planner 读取这些文件是为了生成**更精准的 Scene Plan**（特别是对话风格、细节来源两个维度），同时为每个场景生成 **KB 查阅清单**供 Writer 使用。

### 2. 为每个场景生成 6 维度写法规划

{Scene Plan 格式完整嵌入，包括 6 维度说明}

### 3. 从近章原文选取风格锚点

{锚点选取规则完整嵌入}

### 4. 合并保存

将 Scene Plan + 风格锚点合并保存到 `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`

### 5. 返回摘要

完成后返回 Scene Plan 的完整内容（主 Agent 需要展示给用户）。
```

### 2.6 模型策略

Subagent 架构的额外优势：**不同阶段可以使用不同模型**。

| 阶段 | 角色 | 推荐模型 | 理由 |
|------|------|----------|------|
| Research | Researcher subagent | sonnet | 信息提取和摘要，不需要创造力 |
| Planning | Planner subagent | sonnet | 结构化规划，按模板填充 |
| Writing | Scene Writer subagent | opus | 创意写作，需要最高质量 |
| L1 验证 | Python 脚本 | — | 纯计算，无 AI |
| L2/L3 验证 | Reviewer subagent | sonnet | 评审分析，不需要 `claude -p` 中间层 |

Research、Planning、Review 使用 sonnet 可降低 ~60% 的 token 成本（相比全程 opus）。

### 2.7 Reviewer Subagent

#### 当前问题

当前 Reviewer 通过 `batch_verify.py` + `claude -p` 子进程实现上下文隔离：

```
batch_verify.py (Python)
  → L1: Python 计算（jieba 分词、统计对比）
  → L2: subprocess claude -p（盲测对比 + 风格分析）     ← 2 次 AI 子进程
  → L3: Python + 可选 claude -p（跨章一致性）           ← 0-1 次 AI 子进程
  → 写入 verification.md + feedback.md
```

这个设计有几个问题：

1. **架构不一致**：Research/Plan/Write 都是 Task subagent，Review 却是 Python 脚本 + `claude -p` 子进程
2. **`claude -p` 是重量级操作**：每次 `claude -p` 启动一个完整的 CLI 进程，开销大于 Task subagent
3. **输入控制粗糙**：`claude -p` 的 prompt 拼接在 Python 字符串中，不如 reviewer_agent.md 模板直观
4. **Writer 可推断评分标准**：当前 SKILL.md 中描述了验证脚本的三层架构和评分权重，Writer 能看到这些信息

#### 改造方案

将 L2/L3 的 AI 分析从 `batch_verify.py` + `claude -p` 迁移到 Reviewer subagent：

```
改造后：
  Step 7: Bash → python batch_verify.py --layer 1    ← L1 纯 Python，无 AI
  Step 8: Task → Reviewer subagent (sonnet)           ← L2+L3 AI 分析
    输入：草稿 + L1 结果 + 风格规则 + 原文样本
    输出：verification + feedback + 评级
```

#### 触发方式

主 Agent 在 Step 8 中：

```
1. 读取 L1 检测结果（Step 7 产出）
2. 读取 templates/reviewer_agent.md（模板）
3. 填入变量：KB_DIR, CHAPTER_NUM, L1_RESULTS
4. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 填充后的 reviewer_agent.md 内容
```

#### reviewer_agent.md 内容结构

```markdown
# Reviewer Agent 指令

你是小说续写系统的 Reviewer Agent。你的任务是独立评审 AI 续写的章节草稿，
检测风格偏差和质量问题，生成结构化的审核报告。

**核心原则**：你与 Writer 完全隔离。你不知道 Writer 的推理过程、Scene Plan、
Context Bundle 内容。你只看到产出（草稿）和标准（风格规则 + 原文基线）。

## 输入参数

- KB_DIR: {KB_DIR}
- 目标章节号: {N}
- L1 定量结果: {L1_RESULTS}

## 执行步骤

### 1. 读取审核材料

| 文件 | 用途 |
|------|------|
| `{KB_DIR}/drafts/ch{NNNN}.md` | 待审草稿 |
| `{KB_DIR}/style/narrative.md` | 叙事风格基准 |
| `{KB_DIR}/style/vocabulary.md` | 用词基准 |
| `{KB_DIR}/style/rhythm.md` | 节奏基准 |
| `{KB_DIR}/style/.build/stats.json` | 定量基线 |
| `{KB_DIR}/text/ch{N-1}.md` | 原文样本（盲测用） |
| `{KB_DIR}/text/ch{N-2}.md` | 原文样本（盲测用） |

**不要读取以下文件**（防止被 Writer 的意图影响判断）：
- `drafts/context_bundle_ch*.md` — Writer 的上下文
- `drafts/scene_plan_ch*.md` — Writer 的设计图
- `drafts/outline_ch*.md` — Writer 的大纲

### 2. L2 深度分析

#### 2A. 盲测对比

从草稿和原文中各取 2-3 个段落，打乱顺序，标记为 A/B，
不标注来源。逐段评估：

- 段落融合度（对话+叙述交织 vs 一行一段）
- 句长分布自然度
- 对话标签多样性
- 情感表达方式（直白 vs 含蓄）
- 细节密度

然后揭示来源，分析草稿与原文的差距。

#### 2B. 风格一致性分析

对照 style/*.md 中的规则逐项检查：
- 叙事约束遵守情况
- 用词禁忌遵守情况
- 节奏偏好匹配度
- 章首/章尾类型是否匹配偏好

### 3. L3 跨章一致性（可选）

如果知识库中有多章草稿，检查：
- 重复表达检测（同一说话标签/动作描写跨章重复）
- 角色行为一致性
- 设定穿帮

### 4. 综合评分

将 L1（传入的定量结果）和 L2/L3 分析综合评分：

总分 = L1 × 40% + L2 × 45% + L3 × 15%
（单章时 L1 × 45% + L2 × 55%）

评级：A (90-100) | B (80-89) | C (70-79) | D (<70)

### 5. 生成输出

将审核报告保存到 `{KB_DIR}/drafts/ch{NNNN}_verification.md`

如果评级为 C 或 D，额外生成修复建议保存到
`{KB_DIR}/drafts/ch{NNNN}_feedback.md`，包含：
- FAIL 项（必须修复）
- WARN 项（建议改进）
- 具体修改建议（指出位置 + 问题 + 替代方案）

### 6. 返回摘要

返回评级和关键发现的摘要文本。
```

#### 关键设计点

1. **完全不看 Writer 的过程文件**：Reviewer 不读 Context Bundle、Scene Plan、大纲。它只看最终产出（草稿）和客观标准（风格规则 + 原文样本）。这比 `claude -p` 方案更彻底——以前 Writer 和 Reviewer 共享 SKILL.md 中的验证章节描述，现在 Reviewer 的评审逻辑全部在独立的 reviewer_agent.md 中
2. **L1 前置**：L1 是纯 Python 定量检测（<5 秒），先跑完把结果传给 Reviewer，避免 Reviewer 重复计算
3. **盲测在 subagent 内完成**：Reviewer 自己读原文 + 草稿，自己做盲测对比。不需要 Python 脚本拼接 prompt 再调 `claude -p`
4. **SKILL.md 不再描述评审细节**：评分权重、盲测方法、检查维度全部移到 reviewer_agent.md。SKILL.md 中只说"调用 Reviewer subagent"，Writer 看不到评审逻辑

#### 与 `batch_verify.py` 的关系

| 功能 | 改造前 | 改造后 |
|------|--------|--------|
| L1 定量检测 | `batch_verify.py`（Python） | **不变**，仍是 Python 脚本 |
| L2 盲测对比 | `batch_verify.py` → `claude -p` | Reviewer subagent |
| L2 风格分析 | `batch_verify.py` → `claude -p` | Reviewer subagent |
| L3 跨章一致性 | `batch_verify.py` → `claude -p` | Reviewer subagent |
| 评分综合 | `batch_verify.py`（Python） | Reviewer subagent |
| `--promote` | `batch_verify.py` | **不变** |
| `--history` | `batch_verify.py` | **不变** |

`batch_verify.py` 不删除，但精简为：
- `--layer 1`：纯 L1 定量检测（保留）
- `--promote`：反哺流程（保留）
- `--history`：验证历史（保留）
- L2/L3 AI 分析：移除，由 Reviewer subagent 替代

#### 上下文隔离效果

```
当前隔离方式：
  Writer（主 Agent 上下文）
  ↕ 共享 SKILL.md 中的验证描述（评分权重、盲测方法可见）
  Reviewer（claude -p 子进程）

改造后隔离方式：
  Writer（Scene Writer subagent 上下文 → 释放）
  ↕ 零共享（Writer 不看 reviewer_agent.md，Reviewer 不看 scene_plan）
  Reviewer（Reviewer subagent 上下文 → 释放）
```

Writer 完全无法推断评审标准，因为：
- SKILL.md 中不再描述评审细节（只有一行"调用 Reviewer subagent"）
- Scene Writer subagent 的 prompt 中没有任何评审信息
- reviewer_agent.md 只有 Reviewer subagent 自己能看到

---

## 三、优化二：分场景增量生成

### 3.1 核心思路

将 Step 5 从「一次性生成整章」改为「按场景逐段生成，场景间按需查阅 KB」。

Scene Plan 已经把章节拆成了 3-5 个场景，每个场景有明确的出场角色、地点、事件、情绪基调。这天然就是分段生成的单位。

```
当前：
  Context Bundle（预装所有信息） → 一次性写 5000 字

改造后：
  Context Bundle（全局信息：S1/S3/S4/S5） + Scene Plan（路线图）
  │
  ├─ 场景 1 前：读角色 A 档案 → 写场景 1（~1200 字）
  ├─ 场景 2 前：读 power_system.md（涉及战斗）→ 写场景 2（~1500 字）
  ├─ 场景 3 前：读 geography.md（场景转换）→ 写场景 3（~1000 字）
  └─ 场景 4 前：无需额外查阅 → 写场景 4（~1300 字）
  │
  拼接 → 保存章节
```

### 3.2 从「预测式加载」到「按需查阅」

这是本优化的核心范式转换：

| 维度 | 预测式（当前） | 按需式（改造后） |
|------|---------------|-----------------|
| 时机 | Research 阶段一次性加载所有可能需要的 KB | 写到具体场景时才查阅 |
| 粒度 | Context Bundle 把所有角色、设定压缩到 S2 | 只读当前场景涉及的角色/设定原文 |
| 信息质量 | 压缩摘要（~300 字覆盖所有角色） | 原始档案（完整的角色说话特征、能力细节） |
| 容错 | 预测遗漏 = 写作时没有参考 | 发现需要 = 立刻查阅 |
| 上下文效率 | 很多预载信息对当前场景无用 | 每次查阅都是当前场景直接需要的 |

### 3.3 Scene Plan 成为路线图

分场景生成中，Scene Plan 的角色从「一次性设计图」变为「逐段执行的路线图」：

```markdown
## Scene Plan 作为路线图

### 场景 1：密室对话（约 1200 字）
  KB 查阅清单：characters/张三.md, characters/李四.md
  ...（6 维度写法规划）

### 场景 2：突围战斗（约 1500 字）
  KB 查阅清单：world/power_system.md, characters/张三.md
  ...（6 维度写法规划）

### 场景 3：赶路途中（约 1000 字）
  KB 查阅清单：world/geography.md
  ...（6 维度写法规划）
```

每个场景的 **KB 查阅清单** 由 Planner 在 Scene Plan 中预规划，Writer 在写该场景前按清单读取。这是 Planner subagent 的新增产出。

### 3.4 场景间查阅策略

| 场景类型 | 写前查阅 |
|----------|----------|
| 战斗 | `world/power_system.md` + 参战角色档案 |
| 日常对话 | 角色档案 + `relationships.md` |
| 地点转换 | `world/geography.md` |
| 势力交涉 | `world/factions.md` + 相关角色档案 |
| 修炼突破 | `world/power_system.md` + 角色档案（修为部分） |
| 悬疑揭秘 | `plot/open_loops.md` + 相关伏笔原章节摘要 |
| 情感 | 角色档案 + `reader/feedback/emotions.md` |

这些信息当前全部压缩在 Context Bundle S2 中。分场景后改为 just-in-time 查阅原始档案，**信息精度更高**。

### 3.5 Context Bundle 角色变化

分场景生成后，Context Bundle 的各 Section 角色发生变化：

| Section | 当前角色 | 改造后角色 | 变化 |
|---------|---------|-----------|------|
| S1 故事位置 | 全局方向 | **不变** | 仍然全局一次性加载 |
| S2 场景角色 | 预装所有角色详情 | **简化为角色列表** | 详情改为场景前 just-in-time 查阅 |
| S3 风格硬指标 | 量化红线 | **不变** | 仍然全局一次性加载 |
| S4 禁忌速查 | 禁忌清单 | **不变** | 仍然全局一次性加载 |
| S5 上一章衔接 | 衔接段 | **仅第一个场景使用** | 后续场景的衔接是已写内容 |

S2 的简化是最大变化——从 ~300 字的角色摘要表缩减为角色列表（名字 + 一句话状态）。详细的说话特征、当前处境、能力状态等在写到具体场景时直接从 `characters/{name}.md` 原文读取。

### 3.6 增量生成的执行流程

```
Step 5（改造后）：分场景生成正文

  主 Agent 读取 Scene Plan，为每个场景构造 Scene Writer subagent：

  for each scene in Scene Plan:
    1. 构造 Scene Writer prompt，包含：
       - 写作指令（从 SKILL.md 提取的铁则 + 写作规则）
       - Scene Plan 中该场景的 6 维度规划 + 风格锚点
       - Context Bundle S3/S4（风格红线 + 禁忌）
       - 前序场景的 Scene Memory（≤150字/个）
       - 上一场景的完整正文（衔接用）
       - 该场景的 KB 查阅清单（文件路径）
       - 第一个场景额外加 Context Bundle S5（上章衔接）
    2. 调用 Task 工具：
       - subagent_type: "general-purpose"
       - model: "opus"
       - prompt: 构造的完整 prompt
    3. subagent 执行：
       - 读取 KB 查阅清单中的文件
       - 生成场景正文
       - 生成 Scene Memory（≤150字摘要）
       - 返回正文 + Scene Memory
    4. 主 Agent 接收返回，追加到章节

  所有场景完成 → 拼接 → 场景间加分隔符 → 保存
```

### 3.7 KB 上下文雪崩问题

如果 Writer 在主 Agent 中逐场景读取 KB，所有读过的文件内容会**持续累积在上下文中**（Claude Code 无法主动丢弃已读内容）：

```
❌ 无隔离的分场景生成（KB 雪崩）：

写场景 1：读 A/B 角色档案（3K）→ 写正文（1.5K）
写场景 2：读 power_system（2K）→ 写正文（2K）      ← A/B 档案仍在上下文中
写场景 3：读 geography（2K）→ 写正文（1.5K）        ← 前面全部 KB 仍在
写场景 4：读 factions（2K）→ 写正文（1.5K）          ← 累积所有 KB
写场景 5：读 relationships（1K）→ 写正文（1K）       ← 几乎持有整个 KB 子库

最终上下文：~6K SKILL + 10K KB累积 + 7.5K 正文累积 = ~23.5K
  其中 KB 累积 ~10K 大部分对当前场景无用 → 有效信息占比下降
```

5 个场景后，Writer 几乎持有整个 `characters/` + `world/` 子库。上下文又回到臃肿状态。

### 3.8 解决方案：Scene Writer Subagent

**每个场景的 KB 读取 + 写作在独立 subagent 中完成**，结束后上下文自动释放。主 Agent 只累积场景正文和 Scene Memory（≤150 字的场景摘要）。

```
✅ Scene Writer Subagent 模式：

主 Agent 持有：Scene Plan + Bundle(S3/S4) + 累积的 Scene Memory

场景 1 → Task subagent (opus):
  输入：Scene Plan(场景1) + Bundle(S3/S4/S5) + 风格锚点
        + KB: characters/A.md, characters/B.md
  输出：场景1正文 + Scene Memory 1（≤150字）
  → subagent 上下文释放（A/B 角色档案消失）

场景 2 → Task subagent (opus):
  输入：Scene Plan(场景2) + Bundle(S3/S4) + 风格锚点
        + Scene Memory 1 + 场景1正文（衔接用）
        + KB: world/power_system.md
  输出：场景2正文 + Scene Memory 2（≤150字）
  → subagent 上下文释放（power_system 消失）

场景 3 → Task subagent (opus):
  输入：Scene Plan(场景3) + Bundle(S3/S4) + 风格锚点
        + Scene Memory 1+2 + 场景2正文（衔接用）
        + KB: world/geography.md
  输出：场景3正文 + Scene Memory 3
  → subagent 上下文释放
```

#### Scene Writer Subagent 的上下文（恒定）

每个 Scene Writer 的上下文大小是**恒定的**，不随场景推进而增长：

| 内容 | 大小 | 说明 |
|------|------|------|
| 写作指令（从 SKILL.md 提取） | ~1.5K | 铁则 + 写作规则 |
| Scene Plan（当前场景部分） | ~0.5K | 6 维度规划 |
| Context Bundle S3/S4 | ~0.5K | 风格红线 + 禁忌 |
| 风格锚点 | ~1K | 近章原文示范 |
| 前序 Scene Memory | ~0.5K | 每个 ≤150 字，3-4 个 |
| 上一场景正文（衔接） | ~1.5K | 仅最近一个场景 |
| 本场景 KB 查阅 | ~2-3K | 本场景需要的角色/世界文件 |
| **合计** | **~7-8K** | **恒定，不随场景数增长** |

#### Scene Memory 格式

每个场景写完后，subagent 在返回正文的同时生成 Scene Memory：

```markdown
## Scene Memory — 场景 1：密室对话

- 张三向李四交代了矿脉之事，李四态度犹豫但最终同意
- 情绪：从紧张转为缓和
- 衔接：李四提出"明日再议"，张三独自离开时察觉暗处有人
- 留给后续：暗处跟踪者身份未揭示
```

#### 主 Agent 的上下文

主 Agent（Orchestrator）在整个 Step 5 期间只累积：

```
主 Agent 上下文（Step 5 期间）：
  Scene Plan 路线图 (~1.5K)
  + Scene Memory 1 (~0.15K)
  + 场景1正文 (~1.5K)
  + Scene Memory 2 (~0.15K)
  + 场景2正文 (~2K)
  + ...
  = 正文累积 + Memory累积（无 KB 累积）
```

### 3.9 上下文用量对比

```
方案 1 — 当前（单一上下文，一次性生成）:
  Writer 上下文 = ~43.5K，有效信息 ~13%

方案 2 — 仅 Subagent（隔离 Research/Plan，一次性生成）:
  Writer 上下文 = ~13.5K，有效信息 ~45%

方案 3 — Subagent + 分场景，无场景隔离（KB 雪崩）:
  Writer 上下文 = ~10→23.5K（递增），有效信息逐步下降

方案 4 — Subagent + Scene Writer Subagent（推荐）:
  每个 Scene Writer 上下文 = ~7-8K（恒定），有效信息 ~90%+
```

| 方案 | Writer 峰值上下文 | 有效信息占比 | KB 累积 |
|------|-------------------|-------------|---------|
| 当前 | ~43.5K | ~13% | 全部预载 |
| 仅 Subagent | ~13.5K | ~45% | 全部预载 |
| 分场景无隔离 | ~23.5K（递增） | ~50%→下降 | 逐场景累积 |
| **Scene Writer Subagent** | **~7-8K（恒定）** | **~90%+** | **每场景独立，写完释放** |

---

## 四、组合架构设计

两项优化叠加后的完整流程：

### 4.1 完整流程

```
Step 1: 主 Agent 读 guide.md

Step 2: Task → Researcher subagent (sonnet)
  读章节级 KB → 生成 Context Bundle + 大纲 → 落盘 → 上下文释放

Step 3: Task → Planner subagent (sonnet)
  读 Bundle + 大纲 + hint + 场景级 KB → 生成 Scene Plan → 落盘 → 上下文释放

Step 4: 主 Agent 展示大纲 + Scene Plan → 用户确认

Step 5: 主 Agent 编排 Scene Writer subagents (opus)
  ┌──────────────────────────────────────────────────────┐
  │  主 Agent 读 Scene Plan，逐场景调度：                   │
  │                                                       │
  │  场景 1 → Task subagent (opus):                       │
  │    输入: Plan(场景1) + S3/S4/S5 + 锚点                │
  │    读取: characters/A.md, B.md                        │
  │    输出: 场景1正文 + Scene Memory 1                    │
  │    → 上下文释放（A/B 档案消失）                        │
  │                                                       │
  │  场景 2 → Task subagent (opus):                       │
  │    输入: Plan(场景2) + S3/S4 + 锚点                   │
  │         + Memory 1 + 场景1正文（衔接）                 │
  │    读取: world/power_system.md                        │
  │    输出: 场景2正文 + Scene Memory 2                    │
  │    → 上下文释放（power_system 消失）                   │
  │                                                       │
  │  场景 3 → Task subagent (opus):                       │
  │    输入: Plan(场景3) + S3/S4 + 锚点                   │
  │         + Memory 1+2 + 场景2正文（衔接）               │
  │    读取: world/geography.md                           │
  │    输出: 场景3正文 + Scene Memory 3                    │
  │    → 上下文释放                                        │
  │                                                       │
  │  场景 4 → Task subagent (opus):                       │
  │    输入: Plan(场景4) + S3/S4 + 锚点                   │
  │         + Memory 1+2+3 + 场景3正文（衔接）             │
  │    输出: 场景4正文                                     │
  │    → 上下文释放                                        │
  └──────────────────────────────────────────────────────┘

Step 6: 主 Agent 拼接所有场景正文 → 保存草稿

Step 7: Bash → python batch_verify.py --layer 1（纯 Python，<5s）
  → L1 定量结果（12 项指标 vs stats.json 基线）

Step 8: Task → Reviewer subagent (sonnet)
  输入: 草稿 + L1 结果 + style/*.md + 原文样本
  不看: context_bundle / scene_plan / outline（Writer 过程文件）
  执行: 盲测对比 + 风格分析 + 跨章一致性
  产出: verification.md + feedback.md + 评级
  → 上下文释放（审核推理过程消失）

Step 9: 主 Agent 读取评级 → 根据结果询问用户
  ≥ B → 完成，可 --promote
  C/D → 展示 feedback，询问是否修复
```

### 4.2 架构全景图

```
                    novel-write 组合架构
                    ══════════════════
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  ┌─ Orchestrator ────────────────────────────────────────┐     │
│  │                                                       │     │
│  │  Step 1: 读 guide.md                                  │     │
│  │                                                       │     │
│  │  Step 2: ── Task ──→ ┌─────────────────────┐         │     │
│  │                      │ Researcher (sonnet)  │         │     │
│  │                      │ 章节级上下文          │         │     │
│  │                      │ → context_bundle.md  │         │     │
│  │                      │ → outline.md         │         │     │
│  │                      └─────────────────────┘         │     │
│  │                                                       │     │
│  │  Step 3: ── Task ──→ ┌─────────────────────┐         │     │
│  │                      │ Planner (sonnet)     │         │     │
│  │                      │ 场景级规划 + hint     │         │     │
│  │                      │ → scene_plan.md      │         │     │
│  │                      │   (含 KB 查阅清单)    │         │     │
│  │                      └─────────────────────┘         │     │
│  │                                                       │     │
│  │  Step 4: 展示方案 → 用户确认                            │     │
│  │                                                       │     │
│  │  Step 5: 编排 Scene Writers                            │     │
│  │    ┌───────────────────────────────────────────┐      │     │
│  │    │ ── Task ──→ ┌───────────────────────┐     │      │     │
│  │    │             │ Scene Writer 1 (opus)  │     │      │     │
│  │    │             │ ~7K 上下文（恒定）      │     │      │     │
│  │    │             │ 读 KB → 写 → Memory    │     │      │     │
│  │    │             └───────────────────────┘     │      │     │
│  │    │    ↓ 正文 + Scene Memory                   │      │     │
│  │    │ ── Task ──→ ┌───────────────────────┐     │      │     │
│  │    │             │ Scene Writer 2 (opus)  │     │      │     │
│  │    │             │ ~7K 上下文（恒定）      │     │      │     │
│  │    │             │ 读 KB → 写 → Memory    │     │      │     │
│  │    │             └───────────────────────┘     │      │     │
│  │    │    ↓ 正文 + Scene Memory                   │      │     │
│  │    │             ...                            │      │     │
│  │    └───────────────────────────────────────────┘      │     │
│  │                                                       │     │
│  │  Step 6: 拼接保存 → drafts/ch{N}.md                   │     │
│  │                                                       │     │
│  │  Step 7: Bash → batch_verify.py --layer 1 (Python)    │     │
│  │          → L1 定量结果（12 项指标）                     │     │
│  │                                                       │     │
│  │  Step 8: ── Task ──→ ┌─────────────────────┐         │     │
│  │                      │ Reviewer (sonnet)    │         │     │
│  │                      │ 草稿 + L1 + style/*  │         │     │
│  │                      │ 盲测 + 风格 + 跨章    │         │     │
│  │                      │ → verification.md    │         │     │
│  │                      │ → feedback.md        │         │     │
│  │                      └─────────────────────┘         │     │
│  │                                                       │     │
│  │  Step 9: 读取评级 → 询问用户 → 可选修复                │     │
│  │                                                       │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 4.3 上下文用量对比（详见 3.9）

| 方案 | Writer 峰值上下文 | 有效信息占比 | KB 累积 |
|------|-------------------|-------------|---------|
| 当前（单一上下文） | ~43.5K | ~13% | 全部预载 |
| 仅 Subagent（隔离 R/P） | ~13.5K | ~45% | 全部预载 |
| 分场景无隔离 | ~23.5K（递增） | ~50%→下降 | 逐场景累积 |
| **Scene Writer Subagent** | **~7-8K（恒定）** | **~90%+** | **每场景独立，写完释放** |

### 4.4 信息流对比

**当前**：
```
KB 文件 ── 全部读取 ──→ 压缩到 Bundle ── 一次性 ──→ 写整章
                        (信息损失)        (预测式)
```

**组合优化后**：
```
KB 文件 ── Researcher 读取 ──→ Bundle + 大纲（落盘，释放上下文）
                                    │
近章原文 ── Planner 读取 ──→ Scene Plan + 查阅清单（落盘，释放上下文）
                                    │
                              主 Agent 读取 Bundle + Plan
                                    │
              ┌─────────────────────┤
              │                     │
         场景 1 subagent       场景 2 subagent
         读角色档案 → 写 → 释放  读 power_system → 写 → 释放
              │                     │
              └──────── ... ────────┘
                        │
                   拼接 → 保存 → L1（Python）
                        │
                   Reviewer subagent
                   读草稿 + style/* + 原文
                   盲测 + 评审 → 释放
                        │
                   评级 + feedback
```

---

## 五、SKILL.md 改造

### 5.1 变化范围

| 部分 | 变化 | 优化来源 |
|------|------|----------|
| 执行流程概览 | 更新步骤描述（7 步 → 9 步） | 全部 |
| Step 2 | 内联指令 → Researcher subagent | 优化一 |
| Step 3 | 内联指令 → Planner subagent（含 KB 查阅清单） | 优化一 + 优化二 |
| Step 5 | 一次性生成 → Scene Writer subagent 循环 | 优化二 |
| Step 7-8 | batch_verify.py 全套 → L1 Python + Reviewer subagent | 优化三 |
| 修复流程 | 不变 | — |
| Auto 模式 | 全流程 subagent 化 | 全部 |
| 铁则 | 不变 | — |
| 验证脚本描述 | **精简**：移除评分权重/盲测方法等细节 | 优化三 |

### 5.2 Step 2 改造（Subagent）

```markdown
### Step 2：Research（Subagent）

启动 Research subagent 生成 Context Bundle 和章节大纲：

1. 读取 `templates/research_agent.md`
2. 将 `{KB_DIR}`、`{N}`、`{HINT}` 替换为实际值
3. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
4. 等待完成后，读取产出文件：
   - `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`
   - `{KB_DIR}/drafts/outline_ch{NNNN}.md`
5. 在对话中展示大纲摘要
```

### 5.3 Step 3 改造（Subagent + KB 查阅清单）

```markdown
### Step 3：Scene Plan（Subagent）

启动 Planner subagent 生成 Scene Plan：

1. 确认 Step 2 产出文件存在
2. 读取 `templates/planner_agent.md`
3. 将 `{KB_DIR}`、`{N}` 替换为实际值
4. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
5. 等待完成后，读取 `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`
6. 在对话中展示 Scene Plan

Scene Plan 中每个场景需包含 **KB 查阅清单**：
列出该场景生成前 Writer 应读取的 KB 文件路径。
```

### 5.4 Step 5 改造（Scene Writer Subagent）

```markdown
### Step 5：分场景增量生成

为每个场景启动独立的 Scene Writer subagent，避免 KB 上下文累积。

for each scene in Scene Plan:
  1. 构造 Scene Writer prompt，包含：
     - 写作指令（铁则 + 写作规则，从 SKILL.md 提取）
     - Scene Plan 中该场景的 6 维度规划 + 风格锚点
     - Context Bundle S3/S4（风格红线 + 禁忌）
     - 前序场景的 Scene Memory（≤150字/个）
     - 上一场景的完整正文（衔接用）
     - 该场景的 KB 查阅清单（文件路径）
     - 第一个场景额外加 Context Bundle S5（上章衔接）
  2. 调用 Task 工具：
     - subagent_type: "general-purpose"
     - model: "opus"
     - prompt: 构造的完整 prompt
  3. subagent 执行：
     - 读取 KB 查阅清单中的文件
     - 生成场景正文
     - 生成 Scene Memory（≤150字摘要）
     - 返回正文 + Scene Memory
  4. 主 Agent 接收返回，追加到章节

所有场景完成后，拼接为完整章节。
场景间使用分隔符 `————`。
```

### 5.5 Step 7-8 改造（L1 + Reviewer Subagent）

```markdown
### Step 7：L1 定量检测

使用 Bash 工具执行：
python .claude/skills/novel-write/verify/batch_verify.py \
  --book-dir {KB_DIR} --chapter {N} --layer 1

读取终端输出，提取 12 项指标结果。

### Step 8：Review（Subagent）

启动 Reviewer subagent 进行 L2+L3 深度分析：

1. 读取 `templates/reviewer_agent.md`
2. 将 `{KB_DIR}`、`{N}`、`{L1_RESULTS}` 替换为实际值
3. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
4. 等待完成后，读取产出文件：
   - `{KB_DIR}/drafts/ch{NNNN}_verification.md`（审核报告）
   - `{KB_DIR}/drafts/ch{NNNN}_feedback.md`（C/D 时生成）
5. 展示评级和关键发现

### Step 9：根据结果决定下一步

- 评级 ≥ B → 完成，提示可 `--promote` 反哺
- 评级 C/D → 展示 feedback 摘要，询问是否修复
```

**SKILL.md 中不再描述验证的内部逻辑**（评分权重、盲测方法、检查维度）。
这些细节全部在 `reviewer_agent.md` 中，Writer 看不到。

### 5.7 产出文件

| 文件 | 何时生成 | 生成者 |
|------|----------|--------|
| `drafts/outline_ch{NNNN}.md` | Step 2 | Researcher subagent |
| `drafts/context_bundle_ch{NNNN}.md` | Step 2 | Researcher subagent |
| `drafts/scene_plan_ch{NNNN}.md` | Step 3 | Planner subagent |
| `drafts/ch{NNNN}.md` | Step 6 | 主 Agent（拼接 Scene Writer 产出） |
| `drafts/ch{NNNN}_verification.md` | Step 8 | Reviewer subagent |
| `drafts/ch{NNNN}_feedback.md` | Step 8（C/D 时） | Reviewer subagent |

大纲从嵌入 Context Bundle 改为独立文件 `outline_ch{NNNN}.md`。

### 5.8 SKILL.md 瘦身效果

| 部分 | 改造前 | 改造后 | 原因 |
|------|--------|--------|------|
| Step 2（Research + 大纲） | ~80 行 | ~15 行 | 详细指令移到 research_agent.md |
| Step 3（Scene Plan） | ~50 行 | ~15 行 | 详细指令移到 planner_agent.md |
| Step 5（写正文） | ~40 行 | ~30 行 | 分场景循环描述更简洁 |
| Step 7-8（验证） | ~60 行 | ~20 行 | 评审逻辑移到 reviewer_agent.md |
| 其余 | ~400 行 | ~400 行 | 不变 |
| **总计** | **~630 行** | **~480 行** | **减少 ~150 行** |

---

## 六、模式适配

### 正常模式

```
Step 1: 主 Agent 读 guide.md
Step 2: Task → Researcher subagent (sonnet) → Context Bundle + 大纲（落盘）
Step 3: Task → Planner subagent (sonnet) → Scene Plan（含 KB 查阅清单，落盘）
Step 4: 主 Agent 展示方案 → 用户确认
Step 5: Task × N → Scene Writer subagents (opus) → 场景正文 + Scene Memory
Step 6: 主 Agent 拼接保存
Step 7: Bash → batch_verify.py --layer 1 → L1 定量结果
Step 8: Task → Reviewer subagent (sonnet) → verification + feedback + 评级
Step 9: 主 Agent 展示评级 → 询问用户
```

### 修复模式（--revise）

**不受影响**。修复模式的输入是已有的完整草稿 + feedback，不需要重新按场景生成。修复是在整章级别做局部修改。

```
Revise Step 1: 读取 scene_plan + context_bundle + draft + feedback
Revise Step 2: 分析问题清单
Revise Step 3: 在设计图下修复正文
Revise Step 4: 保存
```

修复后可再次触发 Step 7-8（L1 + Reviewer subagent）验证修复效果。

### 自动循环（--auto）

```
Auto Step 1:
  Task → Researcher → Context Bundle + 大纲
  Task → Planner → Scene Plan（含 KB 查阅清单）
  主 Agent 展示 → 用户确认（auto 可跳过 Step 4）
  Task × N → Scene Writer subagents → 场景正文
  拼接保存

Auto Step 2:
  Bash → batch_verify.py --layer 1 → L1
  Task → Reviewer subagent → verification + feedback + 评级
  ≥ B → 完成 ✅

Auto Step 3: 主 Agent 读 feedback → 修复（整章级别，不分场景）
Auto Step 4: 再次 L1 + Reviewer → 循环（最多 3 轮）
Auto Step 5: 输出结果
```

### 验证/反哺/历史模式

- `--verify N`：Step 7 + Step 8（L1 + Reviewer subagent）
- `--promote N`：直接调用 `batch_verify.py --promote`（纯 Python，不涉及 AI）
- `--history`：直接调用 `batch_verify.py --history`（纯 Python）

---

## 七、风险评估

### 7.1 Subagent 指令质量

**风险**：subagent 不继承 SKILL.md，完全依赖 prompt 参数中的指令。

**缓解**：research_agent.md、planner_agent.md、reviewer_agent.md 都是自包含的完整指令文件，模板直接嵌入。可通过实际运行迭代优化。

### 7.2 Subagent 失败处理

**风险**：subagent 可能因 KB 文件缺失等原因失败。

**缓解**：主 Agent 在读取产出文件前检查文件存在。subagent prompt 要求遇到缺失时尽力生成（标注缺失部分），而非直接失败。

### 7.3 Subagent 间信息损失

**风险**：Researcher 的隐性理解可能不完全体现在 Context Bundle 文本中。

**缓解**：分场景生成中 Writer 可以直接查阅 KB 原文，弥补 Bundle 的信息损失。两项优化互相补偿。

### 7.4 场景间连贯性

**风险**：分场景生成可能导致场景间过渡不自然、节奏断裂。

**缓解**：
- 已写的前序场景始终在上下文中，Writer 能看到前文
- Scene Plan 已规划了全章的情绪曲线和节奏控制
- 场景间检查步骤专门关注衔接

### 7.5 额外延迟

**风险**：subagent 启动开销 + 分场景间 Read 调用增加总延迟。

**缓解**：
- Research/Planning 用 sonnet，比 opus 快
- Read 工具读本地文件，延迟可忽略（ms 级）
- Writer 上下文更短 → 每段生成更快
- 净延迟变化预计可控

### 7.6 用户确认时机

**风险**：用户修改大纲后需重跑 Planner subagent。

**缓解**：sonnet 调用成本低。如果大纲修改率高，可改为先确认大纲再启动 Planner。

### 7.7 L1 指标计算

**风险**：分场景生成的正文在拼接后才能计算全章指标（平均句长、对话占比等），无法在场景间做 L1 检查。

**缓解**：场景间检查只做定性检查（衔接、段落融合）。L1 量化检测仍在 Step 7 对完整章节执行，与当前一致。

### 7.8 Reviewer 盲测质量

**风险**：Reviewer subagent 在自己上下文中做盲测，可能因为先读了 style 规则再看文本，产生锚定偏差（知道什么是"对的"后更容易辨别）。

**缓解**：reviewer_agent.md 中指定盲测流程——先打乱段落标签（A/B），先做对比判断，再揭示来源。盲测部分在 prompt 中排在 style 规则读取之前。

### 7.9 Reviewer 与 batch_verify.py 的 L1 一致性

**风险**：Reviewer subagent 做综合评分时需要用到 L1 结果。如果 L1 Python 脚本的指标定义或基线更新了，但 reviewer_agent.md 中的评分权重没同步更新，会出现不一致。

**缓解**：L1 结果以结构化文本传入 Reviewer（包含指标名、实际值、基线值、PASS/FAIL 状态），Reviewer 直接使用 PASS/FAIL 计数，不自行解释指标含义。

---

## 八、实施步骤

### Phase 1：Subagent 指令文件

1. 创建 `templates/research_agent.md`
   - 从 SKILL.md Step 2 提取 Research 逻辑
   - 嵌入 Context Bundle 模板
   - 嵌入大纲格式
   - 添加变量占位符

2. 创建 `templates/planner_agent.md`
   - 从 SKILL.md Step 3 提取 Planning 逻辑
   - 嵌入 Scene Plan 格式（6 维度）
   - **新增**：每个场景生成 KB 查阅清单
   - 嵌入风格锚点选取规则
   - 添加变量占位符

3. 创建 `templates/reviewer_agent.md`
   - 从 `batch_verify.py` 的 L2/L3 逻辑提取审核流程
   - 嵌入盲测对比规则
   - 嵌入风格检查维度
   - 嵌入评分公式
   - 添加变量占位符

### Phase 2：改造 SKILL.md

4. Step 2 改为 Researcher subagent 调用
5. Step 3 改为 Planner subagent 调用
6. Step 5 改为 Scene Writer subagent 循环
7. Step 7-8 改为 L1 Python + Reviewer subagent
8. **移除 SKILL.md 中的验证细节**（评分权重、盲测方法、检查维度）
9. 新增 `outline_ch{NNNN}.md` 独立文件引用
10. 更新产出文件表
11. 更新 Auto 模式描述

### Phase 3：精简 batch_verify.py

12. 移除 L2/L3 的 `claude -p` 调用逻辑
13. 保留 `--layer 1`（L1 定量检测）
14. 保留 `--promote`（反哺流程）
15. 保留 `--history`（验证历史）
16. L1 输出格式化为结构化文本（方便传入 Reviewer subagent）

### Phase 4：测试验证

17. 手动测试 Research subagent（Context Bundle + 大纲质量）
18. 手动测试 Planner subagent（Scene Plan + KB 查阅清单质量）
19. 手动测试 Reviewer subagent（对比 `claude -p` 方案的评审质量）
20. 端到端测试正常模式（分场景生成 + 自动审核）
21. 对比测试：同一章节，一次性 vs 分场景生成的 L1 指标
22. 测试 Revise 模式（确认不受影响）
23. 测试 Auto 模式（全流程 subagent 闭环）

### Phase 5：文档更新

24. 更新 `docs/写作skill/架构设计.md`

---

## 九、未来扩展

组合架构为以下方向提供了基础：

1. **场景级验证**：在场景间插入轻量级 L1 检查（对已完成场景的句长/段落长度做即时检测），发现问题立即修正，不等整章写完
2. **多章规划**：Researcher 扩展为弧级规划器，一次规划多章方向，逐章执行
3. **Research 深度增强**：Researcher 有独立上下文预算，可读更多 KB 文件、做更深入分析
4. **动态 Scene Plan 调整**：写完前半部分场景后，如果发现节奏/字数偏离计划，可以调整后续场景的规划
5. **A/B 测试**：同一场景用不同参数生成两个版本，选优后继续
6. **场景级缓存**：如果只需修改某个场景，其他场景可以复用，不必重写整章
