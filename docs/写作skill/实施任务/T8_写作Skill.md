# T8：写作 Skill

> 状态：✅ 已完成

## 目标

将 T3-T7 构建的六层知识库（剧情/角色/世界/读者/风格/文本）整合为一个可用的续写 Skill，使 Claude Agent 能够自主读取知识库并生成风格一致、剧情连贯的续写章节。

## 与 T3-T7 的本质区别

| 维度 | T3-T7 | T8 |
|------|-------|-----|
| 本质 | Python 自动化 pipeline | Claude Agent 续写方案 |
| 实现 | `batch_*.py` + `prompts/` + JSON 中间产物 | 纯 `SKILL.md` + `guide.md` |
| AI 角色 | 被 Python 调度，按 prompt 模板输出 | 自主导航知识库，自主决定读取范围 |
| 触发 | 开发者运行脚本 | 用户 `/novel-write` |
| 产出 | 结构化知识库文件 | 小说正文（自审已移除，由 verify/ 独立完成） |

**核心区别**：T8 写作流程由纯 SKILL.md 指导 Claude 自主执行。验证脚本（`verify/batch_verify.py`）已合并到 novel-write Skill 内，但通过独立 Python 进程运行，保持上下文隔离。

## 核心难题（5 大挑战）

### 1. 上下文调度——知识库太大不能全加载

六层知识库合计数十 KB markdown，加上原文 text/ 目录（9 MB+）和评论 reader/comments/（98 MB+），远超任何 LLM 上下文窗口。必须设计**导航式层层展开**策略：先读索引，按需深入，控制总预算 ~38K tokens。

### 2. 多维一致性——5 个维度同时保持

续写必须同时满足剧情连续、角色一致、设定不矛盾、风格匹配、读者导向。任何一个维度偏差都会被读者察觉。

### 3. 续写粒度——先大纲后正文的两阶段

一次性生成容易跑偏。采用**大纲→确认→正文**两阶段模式：先输出场景规划供用户审核，用户确认方向后再展开正文，避免大量返工。

### 4. 风格守恒——长篇续写中防止漂移

Claude 在生成长文本时容易逐渐偏离原作风格（如情感表达从克制变直白、对话标签从"道"变"说"）。v2 方案通过三层保障：**Style Anchors**（原文示范）+ **Scene Plan**（逐场景写法规划）+ **续写铁则**（硬约束速查）。质量验证由 T9 独立完成。

### 5. 查询路径——不同场景需要不同知识层

战斗场景需要力量体系 + 角色能力；日常场景需要人物关系 + 地理设定；悬疑场景需要伏笔状态 + 时间线。SKILL.md 需提供**场景→知识层映射表**指导 Claude 按需加载。

## 前置条件

- ✅ T1 数据提取完成（text/ + reader/comments/）
- ✅ T3 剧情层就绪（plot/outline/ + open_loops + timeline）
- ✅ T4 角色层就绪（characters/）
- ✅ T5 世界层就绪（world/）
- ✅ T6 读者层就绪（reader/feedback/）
- ✅ T7 风格层就绪（style/）

## 产出文件清单

| # | 文件 | 位置 | 职责 |
|---|------|------|------|
| 1 | 方案文档 | `docs/写作skill/实施任务/T8_写作Skill.md` | 本文件：完整方案、设计决策、验收标准 |
| 2 | SKILL.md | `.claude/skills/novel-write/SKILL.md` | Skill 定义，7 步续写 + 修复/验证/自动循环/反哺模式 |
| 3 | guide.md | `{KB_DIR}/guide.md` | 知识库导航指南，续写时首先读取（首次自动生成） |
| 4 | Bundle 模板 | `.claude/skills/novel-write/templates/context_bundle.md` | Context Bundle S1-S5 结构框架（通用模板） |
| 5 | 锚点模板 | `.claude/skills/novel-write/templates/style_anchors.md` | 风格锚点生成指南（通用模板，不含书籍特定内容） |
| 6 | 验证脚本 | `.claude/skills/novel-write/verify/batch_verify.py` | 三层验证 + 评分 + 反哺（独立进程运行） |
| 7 | 验证 prompts | `.claude/skills/novel-write/verify/prompts/*.md` | 盲测对比/深度分析/跨章检查 3 个 Prompt |

## 续写流程设计（7 步）

> v2 重构：引入 Context Bundle + Scene Writing Plan，将 Research 和 Writing 解耦到不同阶段。

| 步骤 | 操作 | 产出 |
|------|------|------|
| 1 | 读 guide.md（如不存在则从 SKILL.md 内模板生成） | 获取知识库导航指引 |
| 2 | **Research → 生成 Context Bundle**：读 KB ~35KB → 提炼 ~3KB 高密度写作上下文 | `drafts/context_bundle_ch{NNNN}.md`（5 Section） |
| 3 | **大纲（写什么）**：只读 Bundle，生成事件序列大纲 | 大纲文本（不含写法指导） |
| 4 | **等待用户确认**（用户可调整方向、增减场景） | 确认的大纲 |
| 5 | **Scene Plan + Style Anchors（怎么写）**⭐：为每个场景生成 6 维度写法规划 + 从锚点库选取原文示范 | `drafts/scene_plan_ch{NNNN}.md`（保存到文件） |
| 6 | **写正文**：参照 Scene Plan → Style Anchors → Bundle 硬指标 → 禁忌清单 | 正文（~3200-4500 字） |
| 7 | 保存到 `{KB_DIR}/drafts/chNNNN.md`，并在对话中输出 | 持久化的草稿文件 |

### 三个中间产物

| 产物 | 位置 | 说明 |
|------|------|------|
| Context Bundle | `drafts/context_bundle_ch{NNNN}.md` | ~3KB，5 Section：故事位置/场景角色/风格硬指标/禁忌速查/上一章衔接 |
| Scene Writing Plan | `drafts/scene_plan_ch{NNNN}.md` | 每场景 6 维度：视角距离/情绪表达/对话风格/节奏控制/细节来源/段落融合规划 |
| Style Anchors | `drafts/scene_plan_ch{NNNN}.md`（与 Scene Plan 合并） | 从 `{KB_DIR}/style/anchors.md` 按场景类型选取的 3-5 段原文示范 |

### 新增模板文件

| 文件 | 位置 | 说明 |
|------|------|------|
| Bundle 模板 | `.claude/skills/novel-write/templates/context_bundle.md` | S1-S5 结构框架 + 占位符（通用模板） |
| 锚点模板 | `.claude/skills/novel-write/templates/style_anchors.md` | 6 类场景锚点生成指南（通用模板，不含书籍特定内容） |
| 书籍锚点 | `{KB_DIR}/style/anchors.md` | 书籍专属风格锚点（原文片段集，由 Research Phase 生成） |

### 输出存储

续写产物保存到 `{KB_DIR}/drafts/` 目录（独立于正式知识库）：

| 文件 | 路径 | 内容 |
|------|------|------|
| 正文 | `drafts/chNNNN.md` | 章节标题 + 正文 |
| Bundle | `drafts/context_bundle_ch{NNNN}.md` | 高密度写作上下文 |

**不修改正式知识库**（text/、plot/、characters/ 等）。T9（novel-write/verify）从 drafts/ 读取独立评估，通过后再反哺。

### 核心设计理念

1. **Context Bundle 解决上下文调度问题**：~35KB KB → ~3KB Bundle，Writer 只读 Bundle 不重新读 KB
2. **Scene Plan 解决"怎么写"缺失问题**：大纲只回答"写什么"（事件序列），Scene Plan 回答"怎么写"（5 维度写法规划）
3. **Style Anchors 解决风格漂移问题**：给 Writer 具体原文片段作为模仿对象，而非仅靠抽象规则
4. **无自检**：生成侧不做任何自审，质量审核完全交给 T9（独立上下文评审）

## 上下文调度策略

核心原则：**AI 通过索引文件层层展开上下文，而非一次性加载全部内容。**

### 分阶段加载表

| 阶段 | 类别 | 文件 | 触发条件 | 预算 |
|------|------|------|----------|------|
| A | 必读 | `guide.md`, `plot/index.md`, `plot/outline/index.md`, `plot/outline/plot_lines.md`, `plot/open_loops.md`, `style/index.md` + `style/narrative.md` + `style/vocabulary.md` + `style/rhythm.md` | 每次续写前 | ~15K |
| B | 定位 | 当前弧 `plot/outline/arc_XX.md`, `plot/chapters/index.md`, 最近 3-5 章摘要 `plot/chapters/chNNNN.md` | 确定续写位置后 | ~10K |
| C | 按需 | `characters/index.md` → 涉及角色 `characters/{name}.md` + `characters/relationships.md`, `world/index.md` → 涉及设定 `world/{file}.md`, `plot/timeline/index.md`, 最近 1 章原文 `text/chNNNN.md` | 大纲规划中发现需要 | ~8K |
| D | 参考 | `reader/index.md` → `reader/feedback/emotions.md` + `reader/feedback/popular_characters.md` + `reader/feedback/complaints.md` + `reader/feedback/expectations.md`, `reader/comments/chNNNN.md` | 规划情节走向时 | ~5K |

**总计 ~38K tokens 上下文预算**。Claude Agent 自主决定在 B/C/D 阶段加载哪些文件，而非全部加载。

### 场景类型→加载策略

| 场景类型 | 必加载 | 可选加载 |
|----------|--------|----------|
| 战斗 | `world/power_system.md`, 涉及角色档案 | `world/rules.md` |
| 日常 | 涉及角色档案, `characters/relationships.md` | `world/geography.md` |
| 悬疑 | `plot/open_loops.md`, `plot/timeline/index.md` | 相关伏笔原章节 |
| 情感 | 涉及角色档案, `reader/feedback/emotions.md` | `reader/feedback/popular_characters.md` |
| 世界探索 | `world/geography.md`, `world/factions.md` | `world/power_system.md` |

## 质量审核（已迁移至 T9）

> 原 T8 设计中的「5 维自审检查」已完全迁移至 T9（novel-write/verify）。
>
> **原因**：同一个 Claude 上下文的自审是"自己改卷"，无法发现系统性偏差（如持续使用直白情感词、对话标签偏移等）。T9 作为独立环节，提供三层验证：
> 1. **Layer 1 定量检测**：纯 Python，12 项指标，与 stats.json 基准线对比
> 2. **Layer 2 AI 独立评审**：盲测对比 + 深度风格分析（独立 Claude 上下文）
> 3. **Layer 3 跨章一致性**：角色连续性、时间线、重复表达检测

## guide.md 设计

放在知识库根目录的 AI 使用说明，续写时**第一个读取的文件**。

控制 < 3KB，包含：
- 书籍概况（作者、标签、当前进度）
- 知识库导航（必读 / 按需 / 选读三级文件列表）
- 续写铁则（3 条通用 + 书籍特定规则，从 style/ 动态提取）
- 场景类型→加载策略映射表
- 常见查询快速索引表

## SKILL.md 设计

Skill 定义文件，Claude Agent 的完整续写操作手册。

包含：
- frontmatter（`user_invocable: true`）
- 使用方式（`/novel-write <知识库目录>` + 6 种模式：续写/修复/验证/自动循环/反哺/历史）
- 7 步续写流程（正常模式）
- 4 步修复流程（--revise 模式：在既定设计图下修复，防止 Plan Drift）
- 自动循环（--auto 模式：写作→验证→修复→验证，最多 3 轮）
- 验证/反哺/历史（--verify/--promote/--history：调用 verify/batch_verify.py）
- Step 2 Context Bundle 生成指引（必读文件表 + 5 Section 结构）
- Step 5 Scene Plan 6 维度规范 + Style Anchors 选取逻辑 + 落盘到文件
- 续写铁则（3 条通用硬约束 + 书籍特定规则动态提取）
- 验证脚本文档（三层架构 + 评分 + 反哺）
- guide.md 生成模板（首次调用时自动生成）

## 关键设计决策

| 决策 | 选定 | 理由 |
|------|------|------|
| 续写粒度 | 大纲+正文两阶段 | 用户确认方向后再生成，避免返工 |
| 知识库反哺 | T9 通过后才反哺 | 避免低质量续写污染知识库 |
| Python 脚本 | 不需要 | Claude Agent 自主执行，SKILL.md 即完整流程 |
| guide.md 生成 | SKILL.md 内嵌模板 | 首次调用时自动生成 |
| user_invocable | true | 用户通过 `/novel-write` 直接触发 |
| 自动反哺 | 不做 | 续写质量未验证前不应修改知识库 |
| **自审移除** | **交给 T9** | 同一上下文自审是"自己改卷"，T9 独立评审更可靠 |
| **Context Bundle** | **Research 压缩为 ~3KB** | Writer 不直接读 KB，避免 Research 和 Writing 争抢上下文 |
| **Scene Plan** | **独立步骤（Step 5）** | 大纲回答"写什么"，Scene Plan 回答"怎么写"，分离关注点 |
| **Style Anchors** | **原文片段而非抽象规则** | LLM 缺乏具体示范时会退回训练语料均值 |
| **中间产物落盘** | **Bundle + Scene Plan 保存到文件** | 修复循环时防止 Plan Drift（设计图蒸发导致写作漂移） |
| **验证合并** | **verify/ 合并到 novel-write** | 验证脚本非独立 Skill（无法 Claude 调用），通过 Python 子进程运行保持上下文隔离 |
| **通用化** | **Skill 不含书籍特定内容** | 风格规则从 style/ 动态提取，锚点从 anchors.md 加载，适用于任意小说 |

## 验收标准

1. 调用 `/novel-write qidian/novel_kb/玄鉴仙族` 可正常启动续写流程
2. Claude 按 7 步流程执行：读 guide → Research(Bundle) → 大纲 → 确认 → Scene Plan → 正文 → 保存
3. `drafts/context_bundle_ch{NNNN}.md` 正确生成，5 Section 完整
4. 大纲只含事件序列（"写什么"），不含写法指导
5. Scene Plan 为每个场景生成 5 维度写法规划
6. Style Anchors 正确匹配场景类型（章首+章尾必选）
7. 正文不含 narrative.md / vocabulary.md 中的禁忌词
8. 章首为动作/环境开篇，章尾为动作/环境收尾
9. **不**自动修改知识库任何文件
10. 质量审核由 `verify/batch_verify.py` 独立进程完成
11. `--revise N` 能加载已保存的设计图并修复草稿
12. `--auto` 能自动完成写作→验证→修复循环
13. `--verify N` 能调用验证脚本并展示结果
14. `--promote N` 能将验证通过的草稿反哺到正式知识库

## 关键参考文件

| 文件 | 用途 |
|------|------|
| `docs/写作skill/架构设计.md` 第 9 节 | T8 续写流程的原始设计 |
| `style/narrative.md` | 最关键约束源（情感表达禁忌 + 写作指导） |
| `style/vocabulary.md` | 用词约束（禁忌词、称呼系统、修炼术语） |
| `style/rhythm.md` | 节奏约束（章节结构、段落长度、场景切换） |
| `plot/index.md` | 续写起点定位 |
| `.claude/skills/kb-style-analyze/SKILL.md` | SKILL.md frontmatter 格式参考 |
