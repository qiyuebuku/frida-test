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
3. `--verify N` → 调用验证脚本后结束
4. `--auto` → 自动循环模式
5. `--revise N` → 修复流程
6. 默认 → 正常续写流程

**`--verify`/`--promote`/`--history`**：用 Bash 执行 `python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} [--chapter N] [--layer 1] [--promote] [--history]`，完成后读取 `drafts/chNNNN_verification.md` 展示结果。

---

## 执行流程

### 正常模式（9 步）

```
Step 1: 读 guide.md
Step 2: Research → 生成 Context Bundle（保存到文件）
Step 3: 大纲（写什么）
Step 4: 等待用户确认
Step 5: Scene Plan + Style Anchors（怎么写）（保存到文件）⭐
Step 6: 写正文
Step 7: 保存草稿
Step 8: 自动运行 Layer 1 定量检测（编译器，必须执行）
Step 9: 根据结果询问用户是否修复
```

### 修复模式（--revise，4 步）

```
Revise Step 1: 加载已保存的设计图（context_bundle + scene_plan + draft + feedback）
Revise Step 2: 分析问题清单
Revise Step 3: 在既定设计图下修复正文
Revise Step 4: 保存修复后的草稿
```

详见 [修复流程](#修复流程revise-模式)。

### 自动循环模式（--auto，全自动）

```
Auto Step 1: 正常写作（Step 1-7）
Auto Step 2: 调用外部验证脚本（独立进程，上下文隔离）
Auto Step 3: 读取反馈 → 在设计图下修复
Auto Step 4: 再次验证 → 循环直到 ≥ B 或 3 轮
Auto Step 5: 输出最终结果
```

详见 [自动循环](#自动循环auto-模式)。

**架构原则**：
- **无自检**：Writer 不自审。质量审核由 `batch_verify.py` 在独立进程中完成（通过 `claude -p` 调用独立 AI 上下文）
- **上下文隔离**：Writer 和 Reviewer 物理隔离，Reviewer 的评分逻辑和盲测细节不泄露给 Writer
- **信息流单向**：审核 → 写作（feedback 文件），写作不能反向影响审核标准

---

### Step 1：读取 guide.md

读取 `{KB_DIR}/guide.md`。

如果文件不存在，按本文件末尾的 [guide.md 生成模板](#guidmd-生成模板) 生成一份，写入 `{KB_DIR}/guide.md` 后继续。

guide.md 包含书籍概况、知识库导航、续写铁则、场景→加载策略映射，是后续步骤的导航入口。

---

### Step 2：Research Phase — 生成 Context Bundle

**目标**：从知识库中提炼高密度写作上下文，保存为 `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`。

#### 必读文件（按顺序）

| # | 文件 | 提取到 Bundle |
|---|------|---------------|
| 1 | `plot/chapters/index.md` | 确定章节号 → S1 |
| 2 | `plot/outline/index.md` → 当前弧 `arc_XX.md` | 弧详情 → S1 |
| 3 | `plot/outline/plot_lines.md` | 主线状态 → S1 |
| 4 | `plot/open_loops.md` | 伏笔 → S2 |
| 5 | `plot/chapters/ch{N-1}.md`（+ ch{N-2}.md） | 近章摘要 → S1 |
| 6 | `text/ch{N-1}.md` | 衔接段 → S5 + 锚点候选 |
| 7 | `style/narrative.md` | 提炼 → S3 + S4 |
| 8 | `style/vocabulary.md` | 提炼 → S4 |
| 9 | `style/rhythm.md` | 提炼 → S3 |
| 10 | `{KB_DIR}/style/anchors.md`（如存在） | 书籍专属锚点库（Step 5 使用） |
| 11 | 本 Skill 的 `templates/style_anchors.md` | 锚点生成模板（当 anchors.md 不存在时参照） |

#### 按需文件

根据场景类型自主决定是否加载：
- `characters/{name}.md` — 涉及角色的详细档案
- `characters/relationships.md` — 多角色互动时
- `world/*.md` — 涉及力量体系/地理/势力时
- `reader/feedback/*.md` — 规划情节走向时

#### Bundle 生成

按 `templates/context_bundle.md` 模板生成 Context Bundle，包含 5 个 Section：

- **S1. 故事位置**（~150 字）：弧、主线进展、上一章尾原文、本章方向、用户提示
- **S2. 场景角色**（~300 字）：出场角色简表（状态+说话特征）、伏笔状态
- **S3. 风格硬指标**（~100 字）：量化目标与红线（字数/句长/对话占比/标签/禁词/首尾）
- **S4. 禁忌速查**（~200 字）：绝对禁止词 + 替代公式
- **S5. 上一章衔接**（~300 字）：上一章最后一个场景完整段落（原文直接复制）

保存到 `{KB_DIR}/drafts/context_bundle_ch{NNNN}.md`（如 `drafts/` 不存在则创建）。

---

### Step 3：生成章节大纲（只回答"写什么"）

**只读 Context Bundle**，不再重新读知识库原文。生成事件序列大纲：

```markdown
# 第{N}章 大纲

## 章节定位
- 所属故事弧：{弧名}
- 主线推进：{本章推进哪条主线，推进到什么程度}
- 与上一章衔接：{从什么场景/情绪接入}

## 场景规划
### 场景 1：{场景名}（约 XXX 字）
- 地点：{地点}
- 出场角色：{角色列表}
- 核心事件：{事件描述}
- 情绪基调：{基调}

### 场景 2：{场景名}（约 XXX 字）
...

## 伏笔计划
- 回收：{本章回收哪些 open_loops 中的伏笔}
- 新增/推进：{本章新铺或推进哪些伏笔}

## 预期情绪曲线
{简述本章从开始到结尾的情绪走向，标注高点位置}

## 用户方向提示
{如有 --hint，在此复述并说明如何融入}
```

**大纲不含任何写法指导**——"怎么写"在 Step 5 规划。

---

### Step 4：等待用户确认

使用 AskUserQuestion 工具向用户展示大纲并等待反馈：

- 选项 1：确认大纲，开始生成正文
- 选项 2：需要调整（用户提供修改意见）

如用户选择调整，根据反馈修改大纲后**再次确认**，直到用户满意。

---

### Step 5：Scene Plan + Style Anchors（怎么写）⭐核心步骤

**大纲回答"写什么"，Scene Plan 回答"怎么写"**。

**⚠️ 强制要求**：必须在对话中完整输出 Scene Plan 和 Style Anchors 后，才能进入 Step 6。不得跳过或与 Step 6 合并执行。

基于三个输入：
1. 确认的大纲（事件序列）
2. Context Bundle（S3 硬指标 + S4 禁忌 + S2 角色说话特征）
3. `{KB_DIR}/style/anchors.md`（书籍专属锚点库；不存在时按 `templates/style_anchors.md` 模板从原文生成）

#### 5A：为每个场景生成 6 维度写法规划

```markdown
## Scene Writing Plan

### 场景 1：{场景名}（约 XXX 字）

**叙事视角距离**：第三人称近距离，贴近{角色名}感官
**情绪表达方式**：
- 禁止直接写"紧张/恐惧/愤怒"
- 替代手法：{具体手法}（如"指节攥白"表紧张，"低低骂了一声"表愤怒）
**对话风格**：
- 多留白，少解释动机
- {角色A}用"{标签}"，{角色B}用"{标签}"
- 同一标签不得连续使用超过 2 次
**节奏控制**：
- {节奏模式}（如"慢铺垫→突然转折"或"均匀推进"）
- 段落长度：{目标长度}字
**细节来源**：
- 环境细节：{具体可用细节，如"油灯昏黄""月光从窗纸透入"}
- 动作细节：{具体可用动作，如"摸下巴胡须""拍手上的血"}
**⭐段落融合规划**：
- 本场景预计 {N} 个段落，每段 {30-60} 字
- 对话密集段：{哪些对话+动作应融合为同一段落}
- 叙述段：{哪里插入背景/环境/心理的长叙述段落（≥50字）}
- 禁止出现连续 3 个 <15 字段落

### 场景 2：{场景名}（约 XXX 字）
（同上 6 维度）
```

#### 5B：选取匹配的 Style Anchors

从 `{KB_DIR}/style/anchors.md` 中按场景类型选取 3-5 个锚点：

```markdown
## 本章风格锚点

### 章首示范
> {选取的锚点原文}
**技法**：{核心写法提炼}
**格式要点**：{标注段落结构，如"对话+标签+动作+心理融为一段，共42字"}

### {场景类型}示范
> {选取的锚点原文}
**技法**：{核心写法提炼}
**格式要点**：{标注段落如何融合对话与叙述}

### 章尾示范
> {选取的锚点原文}
**技法**：{核心写法提炼}
**格式要点**：{标注段落结构}
```

锚点选取原则：
- 优先选与本章场景类型最匹配的锚点
- 必选：章首锚点 + 章尾锚点
- 按需：日常/紧张/情感/对话锚点
- **重点关注锚点的段落物理结构**（对话与叙述如何交织），而非仅关注技法

**产出**：
1. 在对话中完整输出 Scene Plan 和 Style Anchors
2. **保存到文件** `{KB_DIR}/drafts/scene_plan_ch{NNNN}.md`（Scene Plan + Style Anchors 合并为一个文件）
3. 输出后告知用户"Scene Plan 已就绪并保存，开始生成正文"再进入 Step 6

**落盘原因**：Scene Plan 是章节的"设计图"。如果只在上下文中，修复循环时会蒸发，导致 Writer 在"另一套计划"下修复旧文章（Plan Drift）。落盘后修复时可重新加载，确保在既定设计图下施工。

---

### Step 6：生成完整章节正文

**⚠️ 前置检查**：确认 Step 5 的 Scene Plan 和 Style Anchors 已在对话中完整输出。如未输出，回到 Step 5。

按确认的大纲逐场景生成正文。生成时参照材料优先级：

| 优先级 | 材料 | 作用 |
|--------|------|------|
| 1 | **铁则 3（段落融合）** | 最重要——决定句长能否达标 |
| 2 | **Scene Plan 段落融合规划** | 每段的物理结构指引 |
| 3 | **Style Anchors 格式要点** | 模仿段落结构（不仅是技法） |
| 4 | **Context Bundle S3** | 量化红线（字数/句长/占比） |
| 5 | **Context Bundle S4** | 禁忌清单（书籍特定） |
| 6 | **Context Bundle S5** | 上一章衔接 |

#### 正文硬指标（从 Context Bundle S3 获取具体阈值）

| 指标 | 目标来源 | 如何达标 |
|------|----------|----------|
| 总字数 | S3 字数目标（从 `stats.json` 基线推导） | 每场景按预算写够字数 |
| 平均句长 | S3 句长目标（从 `rhythm.md` 基线推导） | **遵守铁则 3 段落融合** |
| 对话占比 | S3 对话占比范围（从 `stats.json` 推导） | 对话之间插入叙述/环境段落 |
| 对话标签偏好 | S3 标签偏好（从 `narrative.md` 提取） | 遵守书籍的标签风格 |
| 禁忌词 | S4 禁忌清单（从 `vocabulary.md` 提取） | 按书籍规则替代 |
| 章首类型 | S3 章首偏好（从 `rhythm.md` 提取） | 参照章首锚点 |
| 章尾类型 | S3 章尾偏好（从 `rhythm.md` 提取） | 参照章尾锚点 |
| 段落长度 | S3 段落长度范围 | **对话与动作融为同段** |
| 短句率 | S3 短句率范围 | 减少独立短对话行 |

> **注意**：具体阈值不在 SKILL.md 中固定。每本书的基线不同，由 Research Phase 从 `style/.build/stats.json` 和 `style/*.md` 中提取后写入 Context Bundle S3。

#### 段落融合写作要领（最关键）

生成正文时，牢记以下格式：

**✅ 正确**——对话+标签+动作融为一段：
```
"你来了？"他定睛瞧了瞧，皱紧了眉头，脸上阴晴不定，摸着下巴慢慢踱了两步。
```

**✅ 正确**——连续对话紧凑排列，不插空行：
```
"胡说。"他摆摆手：
"我见过不少这样的人，嘴上说得好听，做起事来全不是那么回事，你信他的话，迟早要吃亏。"
这番话说得对方脸色一变，皱着眉头也不知在想些什么。
```

**❌ 错误**——每行一段，空行分隔：
```
"说说。"

他沉声道。

"我……我不知道……"

她声音发抖。
```

#### 防重复规则

- 同一说话标签（如"淡淡道"）不得在全文出现超过 2 次
- 同一动作描写（如"定定地盯着"）不得在全文出现超过 2 次
- 同一结巴模式（如"小……小的"）不得连续出现超过 2 次，需变换方式

#### 输出格式

```markdown
# 第{N}章 {章节标题}

{正文内容}
```

正文中段落之间用**单个空行**分隔（非连续空行）。场景切换使用分隔符 `————`。

---

### Step 7：保存并输出最终结果

将所有产物保存到 `{KB_DIR}/drafts/` 目录：

| 文件 | 内容 | 何时生成 |
|------|------|----------|
| `drafts/ch{NNNN}.md` | 章节正文 | Step 7 |
| `drafts/context_bundle_ch{NNNN}.md` | 写作上下文包 | Step 2 |
| `drafts/scene_plan_ch{NNNN}.md` | 场景设计图 + 风格锚点 | Step 5 |

如果 `drafts/` 目录不存在则自动创建。

**正文文件格式**：
```markdown
# 第{N}章 {章节标题}

{正文内容}
```

保存后在对话中输出正文供用户阅读。

**重要**：不修改知识库正式目录（text/、plot/、characters/ 等）中的任何文件。drafts/ 是独立的草稿区，验证脚本从此处读取评估，通过后再决定是否反哺到正式知识库。

---

### Step 8：自动运行 Layer 1 定量检测

**这是写作的"编译器"，必须自动执行，不需要用户确认。**

使用 Bash 工具执行：

```bash
python .claude/skills/novel-write/verify/batch_verify.py \
  --book-dir {KB_DIR} \
  --chapter {N} \
  --layer 1
```

Layer 1 是纯 Python 定量检测，<5s 完成，0 次 AI 调用。它检查的是**格式合法性**（禁用词、句长、短句率、字数、对话标签比例），不是质量评价。

执行后读取 `drafts/ch{NNNN}_verification.md`，在对话中展示指标表格。

---

### Step 9：根据结果询问用户

根据 Layer 1 结果分三种情况处理：

| 情况 | 处理 |
|------|------|
| 有 FAIL 项 | 强提示修复：使用 AskUserQuestion 询问"有 X 项 FAIL，是否自动修复？" |
| 只有 WARN/PASS | 温和提示：使用 AskUserQuestion 询问"有 X 项 WARN，是否要修复？" |
| 全部 PASS | 直接输出结果，流程结束 |

如用户选择修复，按 [修复流程](#修复流程revise-模式) 的 Revise Step 1-4 执行修复，修复后**再次自动运行 Step 8**（Layer 1）确认修复效果。

**不自动运行 Layer 2/L3**。L2 是"请编辑读一遍"（90s + AI 调用成本），由用户手动触发：
```bash
/novel-write KB --verify N
```

---

## 续写铁则（3 条通用硬约束 + 书籍特定规则）

> 铁则 1-3 是**通用的写作质量约束**，适用于所有小说。
> 书籍特定的风格规则（禁忌词、对话标签偏好、时间表达、章首/尾偏好等）从 KB 的 `style/` 文件中提取，不在此硬编码。

### 1. 每句包含多重信息

一句承载 ≥2 种信息（动作+情绪、状态+背景、细节+暗示）。避免一句只传达单一信息的冗余铺垫。

### 2. 章首/章尾类型匹配原文偏好

从 `style/rhythm.md` 中读取章首/章尾类型偏好（如动作开篇占比、环境收尾占比），生成时**严格遵守**该偏好。

### 3. ⭐段落融合——对话与叙述必须交织（最重要的格式规则）

**这是决定句长和段落长度能否达标的根本规则。** 原文不是一行一句的聊天记录，而是对话、动作、环境、心理融为一体的叙事段落。

#### 绝对禁止的格式（ChatGPT 式一行一段）：

```
"你来了。"

他沉声说道。

"是的。"

她低头回答。
```

→ 这种格式会导致：平均句长 ~15 字、段落长度 ~20 字、短句率 >40%，**全部 FAIL**。

#### 必须使用的格式（段落融合式）：

```
"你来了。"他定睛瞧了瞧，皱紧了眉头，脸上阴晴不定，摸着下巴慢慢踱了两步。
她是他多年的旧识，两人的父亲曾在同一营中当差，只是后来各奔东西，再见面竟是这般光景。
```

```
"住手！"他猛地站起身来，一把抓住对方的手腕，力道之大让对方吃痛地叫了一声，他压低嗓子在耳边说:
"跟我出去，有话外面说。"
```

#### 融合规则：

1. **对话 + 说话标签 + 动作描写 = 同一段落**，不用空行分隔
2. **连续短对话（<10字）之间不插空行**，可用换行但不空行
3. **动作段落至少 30 字**：不能写一句短动作就单独成段，必须后接动作/环境/心理
4. **叙述段落夹带背景信息**：介绍角色身份、交代来龙去脉，自然嵌入段落中
5. **同一段落中可以有多句对话**（前后两人的一问一答可以放在同一段中）

#### 长句构造公式（目标：匹配 `style/rhythm.md` 中的平均句长）：

| 模式 | 结构 | 目标字数 |
|------|------|----------|
| 对话+标签+动作+环境 | `"短对话。"角色A+标签+动作，环境描写。` | 35-45 |
| 动作链+心理 | `角色A连续动作，角色B的反应/心理。` | 35-50 |
| 叙述+背景嵌入 | `角色介绍+身份背景+当前状态，用逗号串联。` | 35-45 |
| 环境+动作+感官 | `环境描写+角色动作+感官体验，一段多维。` | 35-45 |

**检验标准**：正文中不应出现连续 3 个以上的 <15 字段落。如果出现，说明对话没有与叙述融合。

### 书籍特定规则（从 KB style/ 文件动态提取）

以下规则**不在 SKILL.md 中硬编码**，而是在 Research Phase 从书籍 KB 的 `style/` 文件中提取，写入 Context Bundle 的 S3（硬指标）和 S4（禁忌速查）：

| 规则类型 | 来源文件 | 示例 |
|----------|----------|------|
| 情感表达约束 | `style/narrative.md` | 某些书禁止直白情感词，某些书允许 |
| 时间表达约束 | `style/vocabulary.md` | 古代书用传统时辰，现代书用现代时间 |
| 对话标签偏好 | `style/narrative.md` | 某些书偏好"道"系，某些书偏好"说"系 |
| 禁忌词列表 | `style/vocabulary.md` | 每本书的禁忌词不同 |
| 称呼系统 | `style/vocabulary.md` | 古代/现代/科幻各有不同 |
| 特殊角色规则 | `guide.md` | 如穿越者可用现代词汇等书籍特定规则 |
| 章首/尾类型偏好 | `style/rhythm.md` | 动作开篇占比、环境收尾占比等 |
| 平均句长目标 | `style/rhythm.md` 或 `style/.build/stats.json` | 每本书的句长基线不同 |

---

## 修复流程（Revise 模式）

当使用 `--revise N` 时，进入修复流程。修复流程与正常续写流程完全不同：

**核心原则**：Writer 在**既定设计图下施工修复**，不重新规划。

### Revise Step 1：加载已保存的设计图

读取以下已保存文件（**全部必须存在**，任一缺失则报错提示用户先用正常模式生成）：

| # | 文件 | 用途 |
|---|------|------|
| 1 | `drafts/scene_plan_ch{NNNN}.md` | 场景设计图 + 风格锚点（**不修改，只遵循**） |
| 2 | `drafts/context_bundle_ch{NNNN}.md` | 写作上下文（硬指标 + 禁忌） |
| 3 | `drafts/ch{NNNN}.md` | 当前草稿（待修复的正文） |
| 4 | `drafts/ch{NNNN}_feedback.md` | 审核反馈（必须修改的问题 + 改进建议） |

同时读取本 Skill 的 `templates/style_anchors.md` 作为格式参照。

### Revise Step 2：分析问题清单

从 `ch{NNNN}_feedback.md` 中提取：
- **FAIL 项**（必须修复）：如平均句长、短句率等量化指标不达标
- **WARN 项**（建议改进）：如"道"系标签占比偏低、段落长度偏短
- **AI 建议**（具体改进点）：如重复表达、动作描写套路化等

按优先级排列修复任务：FAIL 项 > WARN 项 > AI 建议。

### Revise Step 3：在既定设计图下修复

**关键约束**：
1. **不修改 Scene Plan**——场景结构、视角距离、情绪表达方式、节奏控制保持不变
2. **不修改大纲**——事件序列、伏笔计划、情绪曲线保持不变
3. **只改正文**——针对 feedback 中的具体问题逐项修复

**修复策略**（按问题类型）：

| 问题类型 | 修复方法 |
|----------|----------|
| 平均句长过短 | 合并对话+标签+动作为长段落（铁则7） |
| 短句率过高 | 将独立短对话行与动作描写融合 |
| 段落长度偏短 | 在段落中嵌入背景信息、环境描写、心理活动 |
| "道"系标签不足 | 替换"说""回答"为"道""答道""笑道" |
| 重复表达 | 按 AI 建议替换为变体 |
| 直白情感词 | 替换为动作/细节/生理反应 |
| 总字数不足 | 在场景转换处补充环境描写，在对话间补充动作细节 |

**严格遵守续写铁则 1-7**，特别是铁则 7（段落融合）。

### Revise Step 4：保存修复后的草稿

覆盖保存到原文件 `{KB_DIR}/drafts/ch{NNNN}.md`。

在对话中输出修复后的完整正文，并附上修复摘要：

```markdown
## 修复摘要

| 问题 | 修复前 | 修复后 | 状态 |
|------|--------|--------|------|
| 平均句长 | 15.9 | {新值} | ✅/❌ |
| 短句率 | 0.409 | {新值} | ✅/❌ |
| ... | ... | ... | ... |

### 主要修改
- {具体修改了什么，如"将第3-5段的对话+动作分行格式合并为段落融合式"}
- {具体修改了什么}
```

**不自审**——修复后的质量验证由用户再次调用验证脚本完成：
```bash
python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} --chapter N
```

---

## 完整写作-审核循环

```
正常模式（默认）:
  /novel-write KB --chapter N
  → Step 1-7: 生成 context_bundle + scene_plan + draft
  → Step 8: 自动 Layer 1 检测（<5s，编译器）
  → Step 9: 有 FAIL/WARN → 询问用户 → 可选修复
  → 修复后再次 Step 8 确认

手动深度验证（可选，用户主动触发）:
  /novel-write KB --verify N
  → Layer 1 + Layer 2（AI 分析）+ Layer 3（跨章）
  → 90s + AI 调用成本

修复模式:
  /novel-write KB --revise N
  → 读取 scene_plan + feedback → 修复 draft
```

也可以用 `--auto` 模式自动完成整个循环（含 L2 验证）。

**信息流方向**：
- 写作上下文（Author KB）→ 只有 Writer 访问
- 审核反馈（feedback）→ 单向流入 Writer，Writer 不能访问审核的评分标准和原文对比细节
- Style KB（续写铁则 + Style Anchors）→ 写作和审核共享

---

## 自动循环（Auto 模式）

当使用 `--auto` 时，自动编排 **写作→验证→修复→验证** 循环，无需人工介入。

### 上下文隔离机制

`verify/batch_verify.py` 通过 `claude -p` 子进程调用 AI，Writer 和 Reviewer 在不同进程、不同上下文中工作，天然避免"自己审自己"的自证循环。Reviewer 的评分标准、盲测对比细节不会泄露给 Writer，Writer 只看到 `feedback.md` 中的具体修改建议。

### Auto 流程

```
┌──────────────────────────────────────────────────┐
│  Auto Round 1：正常写作                            │
│  Step 1-7（正常模式）→ 生成 draft + scene_plan     │
└──────────────┬───────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────┐
│  Auto Verify：调用外部验证脚本                      │
│  python batch_verify.py --book-dir KB --chapter N │
│  （独立进程，独立 Claude 上下文）                    │
│  → 生成 verification.md + feedback.md              │
└──────────────┬───────────────────────────────────┘
               ↓
         评级 ≥ B？ ─── 是 ──→ 完成 ✅
               │
              否（C/D）
               ↓
┌──────────────────────────────────────────────────┐
│  Auto Round 2：修复模式                            │
│  读取 scene_plan + feedback → 修复 draft           │
└──────────────┬───────────────────────────────────┘
               ↓
         再次验证 → 评级 ≥ B？ → ...
               │
         最多 3 轮，超出则停止并报告
```

### Auto 执行步骤

#### Auto Step 1：正常写作（Step 1-9）

与正常模式完全相同（包括 Step 8 自动 L1 + Step 9 自动修复 FAIL 项）。完成后得到：
- `drafts/ch{NNNN}.md`
- `drafts/context_bundle_ch{NNNN}.md`
- `drafts/scene_plan_ch{NNNN}.md`

#### Auto Step 2：调用外部验证

使用 Bash 工具执行：

```bash
python .claude/skills/novel-write/verify/batch_verify.py \
  --book-dir {KB_DIR} \
  --chapter {N} \
  --timeout 300
```

**关键**：这是**外部 Python 进程**，其中的 Claude 调用与当前对话完全隔离。Writer 不会看到 Reviewer 的推理过程。

执行后检查生成的文件：
- 读取 `drafts/ch{NNNN}_verification.md` 获取评级和分数
- 如果评级为 A 或 B → 跳到 Auto Step 5（完成）
- 如果评级为 C 或 D → 继续 Auto Step 3

#### Auto Step 3：读取反馈并修复

1. 读取 `drafts/ch{NNNN}_feedback.md`（由验证脚本自动生成）
2. 按 [修复流程](#修复流程revise-模式) 的 Revise Step 1-4 执行修复
3. 保存修复后的 `drafts/ch{NNNN}.md`

#### Auto Step 4：再次验证

重复 Auto Step 2。如果仍未达标且未超过最大轮数（3 轮），回到 Auto Step 3。

**防死循环机制**：
- 最大修复轮数：3 轮（超过则停止）
- 每轮验证后对比分数，如果连续 2 轮分数无提升（±2 分内），提前停止
- 停止时输出所有轮次的分数变化供用户判断

#### Auto Step 5：输出最终结果

```markdown
## 自动循环结果

| 轮次 | 操作 | 评分 | 评级 |
|------|------|------|------|
| Round 1 | 初始生成 | {分数} | {评级} |
| Verify 1 | 验证 | — | — |
| Round 2 | 修复 | {分数} | {评级} |
| ... | ... | ... | ... |

**最终评级**：{评级}（{分数}/100）
**总轮数**：{N} 轮
**总耗时**：{时间}

{如果最终达标}
✅ 达到目标评级 B，草稿就绪。可用以下命令反哺到正式知识库：
python .claude/skills/novel-write/verify/batch_verify.py --book-dir {KB_DIR} --promote --chapter {N}

{如果未达标}
⚠️ 经过 3 轮修复仍未达标。建议人工检查 feedback 后手动修改。
```

---

## 验证脚本（verify/batch_verify.py）

验证脚本位于 `verify/batch_verify.py`，通过 Python 独立运行（不是 Claude Skill 调用），上下文与 Writer 物理隔离。

### 前置条件

- `style/.build/stats.json` 存在（由 kb-style-analyze 生成）
- `drafts/chNNNN.md` 存在（由 novel-write 生成）
- Python 包 `jieba` 已安装

### 常用命令

```bash
# 完整验证（Layer 1 + Layer 2 + Layer 3）
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录>

# 只 Layer 1（纯定量，<5s，0 次 AI 调用）
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --layer 1

# 指定章节
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --chapter 4

# 反哺（验证通过后将草稿纳入正式知识库）
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --promote --chapter 902

# 查看验证历史
python .claude/skills/novel-write/verify/batch_verify.py --book-dir <知识库目录> --history
```

### 三层验证架构

| 层 | 内容 | AI 调用 |
|----|------|---------|
| Layer 1 | 定量检测：12 项指标与 stats.json 基准对比 | 0 次 |
| Layer 2 | AI 深度分析：盲测对比 + 风格分析 | 2 次 `claude -p` |
| Layer 3 | 跨章一致性：重复表达检测 + 角色状态追踪 | 0-1 次 |

### 评分系统

```
总分 = L1 × 40% + L2 × 45% + L3 × 15%
（单章时 L1 × 45% + L2 × 55%）

A (90-100) 优秀    B (80-89) 良好
C (70-79) 及格     D (<70) 不合格
```

### 输出文件

| 文件 | 内容 |
|------|------|
| `drafts/chNNNN_verification.md` | 三层验证结果 + 综合评分 |
| `drafts/chNNNN_feedback.md` | 评级 C/D 时生成，用于 `--revise` 修复 |
| `drafts/.verify_history.json` | 累积验证历史 |

### 反哺流程（`--promote`）

仅对**新章节号**（`text/` 中不存在的）执行，**不覆盖原文**：
- 正文 → `text/chNNNN.md`
- 章节摘要 → `plot/chapters/chNNNN.md`（调 Claude 生成）
- 索引更新 → `plot/chapters/index.md`

---

## guide.md 生成模板

当 `{KB_DIR}/guide.md` 不存在时，读取知识库中以下文件的关键信息生成：

- `book_detail.md`（书名、作者、标签）
- `plot/index.md`（当前进度、故事弧数）
- `plot/chapters/index.md`（最后章节号）
- `style/narrative.md`（情感表达禁忌）
- `style/vocabulary.md`（禁忌词、称呼系统）
- `style/rhythm.md`（章节结构偏好）

生成模板结构：

```markdown
# {书名} — AI 续写知识库使用指南

## 书籍概况
- 作者：{作者}
- 类型标签：{标签}
- 当前进度：{最后章节号}，共 {N} 个故事弧
- 知识库覆盖：{已分析章节数} 章

## 知识库导航

### 必读（每次续写前）
- `plot/index.md` — 剧情层总览
- `plot/outline/plot_lines.md` — 全局主线追踪
- `plot/open_loops.md` — 伏笔状态
- `style/narrative.md` — 叙事约束（含情感禁忌）
- `style/vocabulary.md` — 用词约束（含禁忌词）
- `style/rhythm.md` — 节奏约束（含章节结构）

### 按需（根据续写内容选读）
- `characters/{name}.md` — 涉及角色的详细档案
- `characters/relationships.md` — 角色关系网
- `world/power_system.md` — 力量体系（战斗场景）
- `world/geography.md` — 地理设定（场景转换）
- `world/factions.md` — 组织势力
- `world/rules.md` — 世界规则与限制
- `plot/timeline/index.md` — 时间线（防穿帮）
- `text/chNNNN.md` — 原文正文（需要精确衔接时）

### 选读（优化方向）
- `reader/feedback/emotions.md` — 情绪触发点
- `reader/feedback/popular_characters.md` — 角色人气
- `reader/feedback/complaints.md` — 读者不满点
- `reader/feedback/expectations.md` — 读者期待
- `reader/comments/chNNNN.md` — 具体段落反应

## 续写铁则（通用）
1. 每句包含多重信息
2. 章首/章尾类型匹配原文偏好
3. 段落融合——对话与叙述必须交织

## 续写铁则（书籍特定，从 style/ 提取）
{从 narrative.md 提取的情感表达约束}
{从 vocabulary.md 提取的用词禁忌}
{从 rhythm.md 提取的节奏偏好}
{从 guide.md 中的特殊角色规则}

## 场景→加载策略
| 场景类型 | 必加载 | 可选 |
|----------|--------|------|
| 战斗 | power_system.md + 角色档案 | rules.md |
| 日常 | 角色档案 + relationships.md | geography.md |
| 悬疑 | open_loops.md + timeline | 伏笔原章节 |
| 情感 | 角色档案 + emotions.md | popular_characters.md |
| 探索 | geography.md + factions.md | power_system.md |

## 常见查询索引
| 查什么 | 去哪找 |
|--------|--------|
| 下一章该写什么 | plot/outline/arc_XX.md + plot_lines.md |
| 角色性格/能力 | characters/{name}.md |
| 某个伏笔的来龙去脉 | plot/open_loops.md → 对应章节摘要 |
| 修炼境界/法术 | world/power_system.md |
| 地点位置关系 | world/geography.md |
| 读者喜欢什么 | reader/feedback/emotions.md |
| 读者讨厌什么 | reader/feedback/complaints.md |
| 上一章写了什么 | plot/chapters/chNNNN.md（摘要）或 text/chNNNN.md（原文）|
```

控制在 3KB 以内，生成后写入 `{KB_DIR}/guide.md`。
