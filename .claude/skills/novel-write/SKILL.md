---
name: novel-write
description: 基于六层知识库续写小说章节（剧情/角色/世界/读者/风格/文本）
user_invocable: true
---

# 小说续写 Skill

## 参数解析

从用户输入中提取 `KB_DIR`（必须）和可选标志：`--hint`、`--chapter N`、`--revise N`、`--verify N`、`--layer`、`--auto`、`--promote N`、`--history`。

**模式判断**（按优先级）：
1. `--history` → 调用验证脚本后结束
2. `--promote N` → 调用验证脚本后结束
3. `--verify N` → L1 Python + Reviewer subagent
4. `--auto` → 自动循环模式
5. `--revise N` → 修复流程
6. 默认 → 正常续写流程

**`--promote`/`--history`**：用 Bash 执行 `python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} [--promote --chapter N] [--history]`，完成后展示结果。

**`--verify N`**：执行 Step 6 + Step 7（L1 定量 + Reviewer subagent 深度分析）。

> **知识库构建**：KB 构建已独立为 `novel-kb` skill，使用 `/novel-kb` 调用。

---

## 执行流程

### 正常模式（7 步）

```
Step 1: 读 guide.md，确定章节号
Step 2: Task(sonnet) → Researcher subagent → Context Bundle + 大纲 + 涉及名词清单（落盘，释放）
Step 3: Task(sonnet) → Enricher subagent → 按名词清单搜 KB → Scene Plan（落盘，释放）
Step 4: 主 Agent 读落盘文件 → 展示大纲 + Scene Plan → 用户确认
Step 5: 主 Agent 写全章正文（落盘）
Step 6: Task(sonnet) → Reviewer subagent → Bash L1 + L2/L3 深度分析 → 评级（落盘，释放）
Step 7: 主 Agent 展示评级 → 根据结果询问用户
```

### 修复模式（--revise，4 步）

```
Revise Step 1: 加载已保存的设计图（context_bundle + scene_plan + draft + feedback）
Revise Step 2: 分析问题清单
Revise Step 3: 在既定设计图下修复正文
Revise Step 4: 保存修复后的草稿 → 再次执行 Step 6-7
```

详见 [修复流程](#修复流程revise-模式)。

### 自动循环模式（--auto，全自动）

```
Auto Step 1: 正常写作（Step 1-7）
Auto Step 2: 读取评级 → >= B 则完成
Auto Step 3: 读取 feedback → 修复 → 再次 Step 6-7
Auto Step 4: 循环直到 >= B 或 3 轮
Auto Step 5: 输出最终结果
```

详见 [自动循环](#自动循环auto-模式)。

**架构原则**：
- **三 Subagent 隔离**：Researcher/Enricher/Reviewer 各在独立 Task subagent 中执行，上下文用完即释放，不污染主 Agent
- **主 Agent 上下文极度干净**：只有 Bundle（~3K）+ 大纲（~1K）+ Scene Plan（~3-5K）+ 用户交互
- **主 Agent 写正文**：不再用 Scene Writer subagent，主 Agent 直接基于干净上下文写全章
- **无自检**：Writer 不自审。质量审核由 Reviewer subagent 在独立上下文中完成
- **信息流单向**：审核 → 写作（feedback 文件），Writer 不能访问审核标准

---

### Step 1：读取 guide.md + 确定章节号

1. 读取 `{KB_DIR}/guide.md`。如果文件不存在，提示用户先运行 `/novel-kb` 构建知识库，终止流程。
2. **确定章节号 N**：
   - 如果用户指定了 `--chapter N`：**直接使用 N，不读 index.md，不检查原文是否存在，不做任何验证**
   - 如果未指定，读取 `{KB_DIR}/plot/chapters/index.md`，找到最后一个已完成章节号，N = 最后章节号 + 1
3. 计算零填充格式 `{NNNN}`（如 N=42 → NNNN=0042，N=1168 → NNNN=1168）

**Step 1 完成后立即进入 Step 2，不做任何额外的文件探索。**

---

### Step 2：Research（Subagent）

启动 Researcher subagent 生成 Context Bundle、章节大纲和涉及名词清单：

1. 读取 `templates/research_agent.md`
2. 将 `{KB_DIR}`、`{N}`（章节号）、`{NNNN}`（零填充）、`{HINT}` 替换为 Step 1 确定的实际值
3. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
4. 等待完成后，读取产出文件：
   - `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`
   - `{KB_DIR}/drafts/outline_ch{NNNN}.md`（含"涉及名词清单"section）

Researcher 只读章节级信息（剧情进度、伏笔、风格规则），不碰角色档案、世界设定。详见 `templates/research_agent.md`。

---

### Step 3：Enricher（Subagent）

启动 Enricher subagent 按名词清单搜 KB，生成信息完备的 Scene Plan：

1. 确认 Step 2 产出文件存在
2. 读取 `templates/enricher_agent.md`
3. 将 `{KB_DIR}`、`{N}`、`{NNNN}`、`{HINT}` 替换为实际值
4. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
5. 等待完成后，读取 `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`

Enricher 按名词清单逐项搜 KB（角色档案、世界设定），补全信息到 Scene Plan。详见 `templates/enricher_agent.md`。

---

### Step 4：等待用户确认

使用 AskUserQuestion 工具向用户展示**大纲 + Scene Plan 完整方案**并等待反馈：

- 选项 1：确认方案，开始生成正文
- 选项 2：需要调整（用户提供修改意见）

如用户选择调整，根据反馈修改后**再次确认**，直到用户满意。

---

### Step 5：主 Agent 写全章正文

**主 Agent 直接写全章**，不拆分到 Scene Writer subagent。此时主 Agent 上下文干净（~7-9K 全是有效信息）：

| 内容 | 大小 |
|------|------|
| Context Bundle（S1-S5） | ~3K |
| 章节大纲 | ~1K |
| Scene Plan（角色设定+世界设定+风格锚点+注意事项） | ~3-5K |
| 续写铁则 | ~0.5K |

**写作步骤**：

1. 读取已落盘的三个文件：
   - `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`
   - `{KB_DIR}/drafts/outline_ch{NNNN}.md`
   - `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`
2. 基于大纲中的场景规划，按顺序写每个场景
3. 场景间使用分隔符 `————`
4. 保存到 `{KB_DIR}/drafts/ch{NNNN}.md`

#### 正文硬指标（从 Context Bundle S3 获取具体阈值）

| 指标 | 目标来源 | 如何达标 |
|------|----------|----------|
| 总字数 | S3 字数目标 | 按大纲中场景字数预算写够 |
| 平均句长 | S3 句长目标 | **遵守铁则 3 段落融合** |
| 对话占比 | S3 对话占比范围 | 对话之间插入叙述/环境段落 |
| 对话标签偏好 | S3 标签偏好 | 遵守书籍的标签风格 |
| 禁忌词 | S4 禁忌清单 | 按书籍规则替代 |
| 章首/章尾类型 | S3 偏好 | 参照风格锚点 |
| 段落长度 | S3 段落长度范围 | **对话与动作融为同段** |
| 短句率 | S3 短句率范围 | 减少独立短对话行 |

> **注意**：具体阈值不在 SKILL.md 中固定。每本书的基线不同，由 Researcher 从 KB 提取后写入 Context Bundle S3。

#### 输出格式

```markdown
# 第{N}章 {章节标题}

{正文内容}
```

正文中段落之间用**单个空行**分隔（非连续空行）。场景切换使用分隔符 `————`。

保存到 `{KB_DIR}/drafts/ch{NNNN}.md`。如果 `{KB_DIR}/drafts/` 目录不存在则自动创建。

保存后在对话中输出正文供用户阅读。

**重要**：不修改知识库正式目录（text/、plot/、characters/ 等）中的任何文件。drafts/ 是独立的草稿区。

---

### Step 6：Review（Subagent，L1 + L2/L3）

启动 Reviewer subagent，**内部先跑 L1 再做 L2/L3 深度分析**：

1. 读取 `templates/reviewer_agent.md`
2. 将 `{KB_DIR}`、`{N}`、`{NNNN}` 替换为实际值
3. 调用 Task 工具：
   - subagent_type: "general-purpose"
   - model: "sonnet"
   - prompt: 替换后的模板内容
4. Reviewer subagent 内部流程：
   - **先 Bash 执行** `python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} --chapter {N} --layer 1` 获取 L1 结果
   - **再基于 L1 结果** + 草稿 + style/*.md + 原文样本做 L2/L3 深度分析
5. 等待完成后，读取产出文件：
   - `{KB_DIR}/drafts/ch{NNNN}_verification.md`（审核报告）
   - `{KB_DIR}/drafts/ch{NNNN}_feedback.md`（C/D 时生成）
6. 展示评级和关键发现

**Reviewer 不看 context_bundle / scene_plan / outline（Writer 过程文件）**。L1 输出和审核推理过程全部留在 subagent 上下文中，释放后消失。

**SKILL.md 不描述审核的内部逻辑**（评分权重、盲测方法、检查维度）。这些细节在 `reviewer_agent.md` 中，Writer 看不到。

---

### Step 7：根据结果决定下一步

根据 Reviewer 评级分情况处理：

| 情况 | 处理 |
|------|------|
| 评级 A/B | 完成。提示可用 `--promote` 反哺到正式知识库 |
| 评级 C/D + 有 FAIL | 强提示修复：使用 AskUserQuestion 询问"评级 {X}，有 {N} 项 FAIL，是否自动修复？" |
| 评级 C/D + 无 FAIL | 温和提示：展示 feedback 摘要，询问是否修复 |

如用户选择修复，按 [修复流程](#修复流程revise-模式) 的 Revise Step 1-4 执行修复，修复后**再次执行 Step 6-7**（L1 + Reviewer）确认修复效果。

---

## 续写铁则

### 1. 每句包含多重信息

一句承载 >= 2 种信息（动作+情绪、状态+背景、细节+暗示）。禁止一句只传达单一信息。

### 2. 章首/章尾类型匹配 Context Bundle S3 中的偏好

S3 已从 `{KB_DIR}/style/rhythm.md` 提取了章首/章尾类型偏好，生成时**严格遵守**。

### 3. 段落融合——对话与叙述必须交织

对话+标签+动作+环境必须融为同一段落，禁止一行一段格式。Scene Plan 中的段落融合规划和风格锚点已提供具体指引。

---

## 修复流程（Revise 模式）

当使用 `--revise N` 时，进入修复流程。修复流程与正常续写流程完全不同：

**核心原则**：Writer 在**既定设计图下施工修复**，不重新规划。

### Revise Step 1：加载已保存的设计图

读取以下已保存文件（**全部必须存在**，任一缺失则报错提示用户先用正常模式生成）：

| # | 文件 | 用途 |
|---|------|------|
| 1 | `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md` | 场景设计图（**不修改，只遵循**） |
| 2 | `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md` | 写作上下文（硬指标 + 禁忌） |
| 3 | `{KB_DIR}/drafts/ch{NNNN}.md` | 当前草稿（待修复的正文） |
| 4 | `{KB_DIR}/drafts/ch{NNNN}_feedback.md` | 审核反馈（必须修改的问题 + 改进建议） |


### Revise Step 2：分析问题清单

从 `ch{NNNN}_feedback.md` 中提取：
- **FAIL 项**（必须修复）：如平均句长、短句率等量化指标不达标
- **WARN 项**（建议改进）：如"道"系标签占比偏低、段落长度偏短
- **AI 建议**（具体改进点）：如重复表达、动作描写套路化等

按优先级排列修复任务：FAIL 项 > WARN 项 > AI 建议。

### Revise Step 3：在既定设计图下修复

**关键约束**：
1. **不修改 Scene Plan**——场景结构、角色设定、世界设定保持不变
2. **不修改大纲**——事件序列、伏笔计划、情绪曲线保持不变
3. **只改正文**——针对 feedback 中的具体问题逐项修复

**修复策略**（按问题类型）：

| 问题类型 | 修复方法 |
|----------|----------|
| 平均句长过短 | 合并对话+标签+动作为长段落（铁则 3） |
| 短句率过高 | 将独立短对话行与动作描写融合 |
| 段落长度偏短 | 在段落中嵌入背景信息、环境描写、心理活动 |
| "道"系标签不足 | 替换"说""回答"为"道""答道""笑道" |
| 重复表达 | 按 AI 建议替换为变体 |
| 直白情感词 | 替换为动作/细节/生理反应 |
| 总字数不足 | 在场景转换处补充环境描写，在对话间补充动作细节 |

**严格遵守续写铁则 1-3**，特别是铁则 3（段落融合）。

### Revise Step 4：保存修复后的草稿

覆盖保存到原文件 `{KB_DIR}/drafts/ch{NNNN}.md`。

在对话中输出修复后的完整正文，并附上修复摘要：

```markdown
## 修复摘要

| 问题 | 修复前 | 修复后 | 状态 |
|------|--------|--------|------|
| 平均句长 | 15.9 | {新值} | /  |
| 短句率 | 0.409 | {新值} | /  |
| ... | ... | ... | ... |

### 主要修改
- {具体修改了什么}
- {具体修改了什么}
```

**不自审**——修复后再次执行 Step 6-7 验证。

---

## 完整写作-审核循环

```
正常模式（默认）:
  /novel-write KB --chapter N
  → Step 1-5: Researcher → Enricher → 确认 → 主 Agent 写正文 → 保存
  → Step 6-7: Reviewer subagent（L1+L2/L3）→ 评级 → 可选修复

手动深度验证（可选）:
  /novel-write KB --verify N
  → Step 6 + Step 7（Reviewer subagent）

修复模式:
  /novel-write KB --revise N
  → 读取 scene_plan + feedback → 修复 draft → 再次 Step 6-7
```

也可以用 `--auto` 模式自动完成整个循环。

**信息流方向**：
- 写作上下文（Context Bundle + Scene Plan）→ 只有 Writer（主 Agent）访问
- 审核反馈（feedback）→ 单向流入 Writer，Writer 不能访问审核标准
- Style KB（续写铁则 + `{KB_DIR}/style/`）→ 写作和审核共享
- Reviewer 的评审逻辑（`reviewer_agent.md`）→ 只有 Reviewer subagent 访问

---

## 自动循环（Auto 模式）

当使用 `--auto` 时，自动编排 **写作→验证→修复→验证** 循环，无需人工介入。

### 上下文隔离机制

所有阶段通过 Task subagent 实现上下文隔离：
- Research → sonnet subagent，用完释放
- Enricher → sonnet subagent，用完释放
- Writing → 主 Agent 直接写（上下文干净，无需 subagent）
- Review → sonnet subagent，用完释放
- Writer 和 Reviewer 零共享：Writer 不看 `reviewer_agent.md`，Reviewer 不看 `scene_plan`

### Auto 流程

```
 Auto Round 1：正常写作
  Step 1-7（正常模式，Step 4 自动确认跳过）
  → 生成 draft + scene_plan + verification
               |
         评级 >= B？ --- 是 --→ 完成
               |
              否（C/D）
               |
 Auto Round 2：修复模式
  读取 scene_plan + feedback → 修复 draft
  → 再次 Step 6-7（Reviewer subagent）
               |
         评级 >= B？ → ...
               |
         最多 3 轮，超出则停止并报告
```

### Auto 执行步骤

#### Auto Step 1：正常写作（Step 1-7）

与正常模式完全相同（Step 4 自动确认跳过）。完成后得到：
- `{KB_DIR}/drafts/ch{NNNN}.md`
- `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`
- `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`
- `{KB_DIR}/drafts/ch{NNNN}_verification.md`

#### Auto Step 2：检查评级

- 读取 `{KB_DIR}/drafts/ch{NNNN}_verification.md` 获取评级
- 评级 A/B → 跳到 Auto Step 5
- 评级 C/D → 继续 Auto Step 3

#### Auto Step 3：读取反馈并修复

1. 读取 `{KB_DIR}/drafts/ch{NNNN}_feedback.md`
2. 按 [修复流程](#修复流程revise-模式) 的 Revise Step 1-4 执行修复
3. 保存修复后的 `{KB_DIR}/drafts/ch{NNNN}.md`
4. 重新执行 Step 6-7（Reviewer subagent）

#### Auto Step 4：再次检查

如果仍未达标且未超过最大轮数，回到 Auto Step 3。

**防死循环机制**：
- 最大修复轮数：3 轮（超过则停止）
- 每轮验证后对比分数，如果连续 2 轮分数无提升（+-2 分内），提前停止
- 停止时输出所有轮次的分数变化供用户判断

#### Auto Step 5：输出最终结果

```markdown
## 自动循环结果

| 轮次 | 操作 | 评分 | 评级 |
|------|------|------|------|
| Round 1 | 初始生成 | {分数} | {评级} |
| Round 2 | 修复 | {分数} | {评级} |
| ... | ... | ... | ... |

**最终评级**：{评级}（{分数}/100）
**总轮数**：{N} 轮

{如果最终达标}
达到目标评级 B，草稿就绪。可用以下命令反哺到正式知识库：
python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} --promote --chapter {N}

{如果未达标}
经过 3 轮修复仍未达标。建议人工检查 feedback 后手动修改。
```

---

## 产出文件

| 文件 | 内容 | 何时生成 | 生成者 |
|------|------|----------|--------|
| `drafts/outline_ch{NNNN}.md` | 章节大纲 + 涉及名词清单 | Step 2 | Researcher subagent |
| `drafts/context_bundle_ch{NNNN}.md` | 写作上下文包 | Step 2 | Researcher subagent |
| `drafts/scene_plan_ch{NNNN}.md` | 角色设定+世界设定+风格锚点 | Step 3 | Enricher subagent |
| `drafts/ch{NNNN}.md` | 章节正文 | Step 5 | 主 Agent |
| `drafts/ch{NNNN}_verification.md` | 审核报告 | Step 6 | Reviewer subagent |
| `drafts/ch{NNNN}_feedback.md` | 修复建议（C/D 时） | Step 6 | Reviewer subagent |

---

## 验证脚本（verify/batch_verify.py）

验证脚本仅保留 L1 定量检测功能（纯 Python，无 AI 调用）。L2/L3 深度分析由 Reviewer subagent 完成。

### 前置条件

- `{KB_DIR}/style/.build/stats.json` 存在（由 `novel-kb` skill 的 T7 阶段生成）
- `{KB_DIR}/drafts/chNNNN.md` 存在（由 novel-write 生成）
- Python 包 `jieba` 已安装

### 常用命令

```bash
# L1 定量检测（<5s，0 次 AI 调用）
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --layer 1

# 指定章节
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --chapter 4 --layer 1

# 反哺（验证通过后将草稿纳入正式知识库）
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --promote --chapter 902

# 查看验证历史
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --history
```

### 输出文件

| 文件 | 内容 |
|------|------|
| `{KB_DIR}/drafts/chNNNN_verification.md` | 审核报告（L1 + L2/L3 综合） |
| `{KB_DIR}/drafts/chNNNN_feedback.md` | 评级 C/D 时生成，用于 `--revise` 修复 |
| `{KB_DIR}/drafts/.verify_history.json` | 累积验证历史 |

### 反哺流程（`--promote`）

仅对**新章节号**（`{KB_DIR}/text/` 中不存在的）执行，**不覆盖原文**：
- 正文 → `{KB_DIR}/text/chNNNN.md`
- 章节摘要 → `{KB_DIR}/plot/chapters/chNNNN.md`（调 Claude 生成）
- 索引更新 → `{KB_DIR}/plot/chapters/index.md`
