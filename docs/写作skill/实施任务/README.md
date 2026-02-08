# 写作 Skill 实施任务

> 基于 [架构设计.md](../架构设计.md) 拆分的分步实施任务
> 实施原则：**全部通过 Claude Code skill 完成**，不直接调用 AI 模型 API

---

## 数据规模备忘

| 指标 | 数值 |
|------|------|
| cleaned JSON 总大小 | **418 MB**（持续增长） |
| 总文件数 | 944（含 `_book_detail.json`） |
| 正文章节 | **901**（标准命名为主，含缺空格/缺"章"字/缺"第"字等变体） |
| 非正文文件 | 42（版权信息、感谢、请假、卷终、感想、番外等） |
| 单文件最大 | 3.2 MB（第七百九十八章） |
| 单文件最小（正文） | ~30 KB |

> 后续 skill 处理时需注意：单个 cleaned JSON 包含大量评论数据（评论未过滤），文件体积远大于纯正文。

---

## 总览

```
T1  数据提取   ✅   → text/ + reader/comments/              脚本改造
T2  章节摘要   🚧   → plot/chapters/                         skill（批量）
T3  剧情层提取 ✅   → plot/outline/ + timeline/ + open_loops skill（三阶段 pipeline）
T4  角色层提取 ✅   → characters/                            skill（五阶段 pipeline，v2）
T5  世界层提取 ✅   → world/                                 skill（四阶段 pipeline）
T6  读者层分析 ✅   → reader/feedback/                       skill（三阶段 pipeline）
T7  风格层分析 ✅   → style/                                 skill（三阶段 pipeline）
T8  写作 Skill ✅   → SKILL.md + guide.md                    skill 编写
T9  验证测试   ✅   → 续写效果评估                            skill 测试
```

### 依赖关系

```
T1 ──→ T2 ──→ T3 ──┐
                    ├──→ T8 ──→ T9
T1 ──→ T6          │
T1 ──→ T7          │
T2 ──→ T4 ──→ T5 ──┘
```

- T2 依赖 T1（需要 `text/` 纯正文）
- T3 依赖 T2（需要章节摘要来划分弧、提取主线）
- T4 依赖 T2（从章节摘要提取角色信息）
- T5 依赖 T4（角色所属势力等需要角色层先完成）
- T6 依赖 T1（需要 `reader/comments/` 评论数据）
- T7 依赖 T1（需要 `text/` 纯正文分析风格）
- T8 依赖 T3~T7（知识库全部就绪后才能写 SKILL.md）
- **T6、T7 可与 T2~T5 并行**

---

## T1：数据提取 ✅

**类型**：脚本改造
**改动文件**：`qidian/scripts/process_chapters.py`
**输出目录**：`qidian/novel_kb/玄鉴仙族/`
**详细方案**：[T1_数据提取.md](./T1_数据提取.md)

### 目标

给 `process_chapters.py` 新增 `--mode kb` 模式，从 cleaned JSON 输出到知识库目录结构。

### 输入输出

- 输入：`output/1035420986_玄鉴仙族_cleaned/*.json`（943 个章节文件 + `_book_detail.json`）
- 输出：
  - `qidian/novel_kb/玄鉴仙族/book_detail.md`（书籍详情：作者、简介、标签、角色人气榜）
  - `qidian/novel_kb/玄鉴仙族/text/ch0001.md` ~ `ch0901.md`（901 个纯正文，含评论数标注，共 9.06 MB）
  - `qidian/novel_kb/玄鉴仙族/reader/comments/ch0001.md` ~ `ch0901.md`（901 个结构化评论，共 98.19 MB）
  - 42 个非正文文件被跳过

### 验收标准

- ✅ `book_detail.md` 包含作者、简介、标签、角色人气榜（数据来源：cleaned 目录的 `_book_detail.json`）
- ✅ `text/` 中每个文件只有章节标题 + 段落正文 + 评论数标注，段间无空行
- ✅ `reader/comments/` 中每段评论前有 `> 原文：...` 引用对应段落，已压缩空行
- ✅ 非正文文件被跳过
- ✅ 评论按 `--min-agree 15` 阈值过滤
- ✅ 章节编号连续

---

## T2：章节摘要生成

**类型**：Claude Code skill
**skill 名称**：`kb-chapter-summary`（暂定）
**详细方案**：[T2_章节摘要.md](./T2_章节摘要.md)

### 目标

为每章生成结构化摘要，输出到 `plot/chapters/`。

### 难点

900+ 章无法在单次 skill 调用中完成，需要设计**断点续跑**机制：
- skill 每次调用处理 N 章（根据上下文容量决定，预估 5~10 章/次）
- 通过 `plot/chapters/index.md` 记录已完成的章节
- 下次调用时从未完成处继续

### 输入输出

- 输入：`text/ch0001.md` ~ `ch0901.md`
- 输出：
  - `plot/chapters/ch0001.md` ~ `ch0901.md`（结构化摘要，模板见架构设计 5.1.1）
  - `plot/chapters/index.md`（全部章节的一行摘要索引）

### 验收标准

- 每章摘要包含完整的 8 个字段（关键事件、新登场角色、已有角色出场、关系变化、重要物品、新增设定、伏笔/悬念、一句话摘要）
- `index.md` 包含全部 900+ 章的一行摘要

---

## T3：剧情层提取 ✅

**类型**：Claude Code skill
**skill 名称**：`kb-plot-extract`
**详细方案**：[T3_剧情层.md](./T3_剧情层.md)

### 目标

基于章节摘要，构建剧情层的高级结构。三阶段 pipeline：分段扫描 → 全局融合 → 精修验证。

### 子任务

| # | 产出 | 说明 |
|---|------|------|
| 3a | `plot/outline/plot_lines.md` | 全局主线追踪（3-8 条） |
| 3b | `plot/outline/arc_XX.md` | 故事弧摘要（每弧 20-50 章，首尾相接覆盖全部章节） |
| 3c | `plot/outline/index.md` | 全局大纲概览 + 分弧索引 |
| 3d | `plot/open_loops.md` | 伏笔与未完成事项汇总（5 类分类） |
| 3e | `plot/timeline/index.md` | 事件时间线 |
| 3f | `plot/index.md` | 剧情层总览导航 |

### 方案

- **阶段 1 分段扫描**（~10 次）：每 100 章完整摘要 + 全局 index → 段级分析 JSON
- **阶段 2 全局融合**（1 次）：全部段级 JSON → 6 个产出文件初稿
- **阶段 3 精修验证**（~25 次）：弧边界校正 + 弧内容充实 + 伏笔验证 + 最终更新
- 总计 ~36 次 Claude 调用，~20 min 全自动

### 实现状态

- ✅ Skill 文件创建完成（`batch_plot.py` + 6 个 prompt 模板 + `SKILL.md`）
- ✅ 3 章数据端到端测试通过（7 次调用，4m21s）
- 🚧 等待 T2 全量完成后运行

---

## T4：角色层提取 ✅

**类型**：Claude Code skill
**skill 名称**：`kb-character-extract`
**详细方案**：[T4_角色层.md](./T4_角色层.md)

### 目标

从章节摘要 + 剧情层产出中提取角色信息，构建人物库。五阶段 pipeline：Python 预处理 → 别名合并 → 角色深度分析 → 关系网构建 → 状态精修。

### 子任务

| # | 产出 | 说明 |
|---|------|------|
| 4a | `characters/index.md` | 人物索引（按出场频次分级：核心/重要/次要） |
| 4b | `characters/{name_pinyin}.md` | 角色档案（核心角色 5 模块，重要角色 3 模块） |
| 4c | `characters/relationships.md` | 人物关系网（分类 + 关系演化时间轴） |

### 方案

- **阶段 0 Python 预处理**（0 次）：解析 T2 摘要结构化字段 + T3 弧文件 → `raw_census.json`
- **阶段 1 别名合并与分级**（1-2 次）：语义识别别名 → `census.json` + `alias_mapping.json`
- **阶段 2 角色深度分析**（~20-40 次）：逐角色加载相关章节摘要 → 生成档案
- **阶段 3 关系网构建**（~3-5 次）：综合全部档案 → 关系网 + 交叉验证
- **阶段 4 状态精修与索引**（~5-10 次）：从最新章节更新活跃角色状态 → `index.md`
- 总计 ~30-57 次 Claude 调用，~25-42 min 全自动

### 实现状态

- ✅ Skill 文件创建完成（`batch_character.py` + 6 个 prompt 模板 + `SKILL.md`）
- ✅ 3 章数据端到端测试通过（7 次调用，2m54s）
- 🚧 等待 T2 全量完成后运行

---

## T5：世界层提取 ✅

**类型**：Claude Code skill
**skill 名称**：`kb-world-extract`
**详细方案**：[T5_世界层.md](./T5_世界层.md)

### 目标

从章节摘要 + 弧文件 + 角色档案中提取世界观设定，构建完整世界层。四阶段 pipeline：Python 预处理 → 分段分类 → 全局融合 → 精修验证。

### 子任务

| # | 产出 | 说明 |
|---|------|------|
| 5a | `world/power_system.md` | 力量体系（模板见 5.3.1） |
| 5b | `world/geography.md` | 地理空间（模板见 5.3.2） |
| 5c | `world/factions.md` | 组织势力（模板见 5.3.3） |
| 5d | `world/rules.md` | 规则与限制（模板见 5.3.4） |
| 5e | `world/index.md` | 世界观总览 |

### 方案

- **阶段 0 Python 预处理**（0 次）：解析 T2 "新增设定"/"重要物品" + T3 "新世界信息" + T4 角色势力 → `raw_world_data.json`
- **阶段 1 分段分类**（~1-10 次）：每 100 章设定 → 按 4+1 类分类 → 段级 JSON
- **阶段 2 全局融合**（1-2 次）：合并段级 JSON → 去重/演化追踪 → 4 个 MD 文件
- **阶段 3 精修验证**（~0-10 次）：关键设定精修 + 一致性验证
- 总计 ~6-22 次 Claude 调用，~2-15 min 全自动

### 实现状态

- ✅ Skill 文件创建完成（`batch_world.py` + 6 个 prompt 模板 + `SKILL.md`）
- ✅ 3 章数据端到端测试通过（6 次调用，2m25s）
- 🚧 等待 T2 全量完成后运行

---

## T6：读者层分析 ✅

**类型**：Claude Code skill
**skill 名称**：`kb-reader-feedback`
**详细方案**：[T6_读者层.md](./T6_读者层.md)

### 目标

从段落级评论数据（101 MB）中提炼读者反馈。核心思路：Python 做 90% 脏活（去重/去噪/统计），Claude 做 10% 精活（语义分类/洞察生成）。

### 子任务

| # | 产出 | 说明 |
|---|------|------|
| 6a | `reader/feedback/emotions.md` | 情绪触发点（模板见 5.4.1） |
| 6b | `reader/feedback/popular_characters.md` | 角色人气排行（模板见 5.4.2） |
| 6c | `reader/feedback/complaints.md` | 读者不满点（模板见 5.4.3） |
| 6d | `reader/feedback/expectations.md` | 读者期待（模板见 5.4.4） |
| 6e | `reader/index.md` | 读者层总览 |

### 方案

- **阶段 0 Python 预处理**（0 次调用）：解析 901 个评论文件 → 去重去噪 → 统计聚合 → `processed_data.json`
- **阶段 1 分段分析**（~5-10 次调用）：每 100 章精选数据 → Claude 四维分析（情绪/角色/不满/期待）
- **阶段 2 全局融合**（1-2 次调用）：合并段级 JSON → 生成 5 个 MD 文件
- 总计 ~10-12 次 Claude 调用，~10-20 min 全自动

### 实现状态

- ✅ Skill 文件创建完成（`batch_reader.py` + 2 个 prompt 模板 + `SKILL.md`）
- ✅ 3 章数据端到端测试通过（2 次调用，1m47s）
- ✅ 901 章预处理测试通过（4s，56 万条评论，过滤率 8.1%）
- 🚧 等待全量运行（901 章，预计 10-12 次调用）

---

## T7：风格层分析 ✅

**类型**：Claude Code skill
**skill 名称**：`kb-style-analyze`
**详细方案**：[T7_风格层.md](./T7_风格层.md)

### 目标

分析原文写作风格，生成结构化风格参考（叙事特征 + 用词特征 + 节奏特征）。三阶段 pipeline：Python 全量统计 → Claude 抽样精析 → 全局融合。

### 子任务

| # | 产出 | 说明 |
|---|------|------|
| 7a | `style/narrative.md` | 叙事特征（模板见 5.5.1） |
| 7b | `style/vocabulary.md` | 用词特征（模板见 5.5.2） |
| 7c | `style/rhythm.md` | 节奏特征（模板见 5.5.3） |
| 7d | `style/index.md` | 风格概览 |

### 方案

- **阶段 0 Python 全量统计**（0 次调用）：jieba 分词 + 901 章全量定量统计（句长/对话/段落/词频/情感）+ T3 弧解析 + T6 读者反馈解析 → `stats.json` + `sampling_plan.json`
- **阶段 1 Claude 抽样精析**（~5-8 次调用）：智能抽样 20 章（三级优先：高赞章 → 转折章 → 均匀补充）→ 7 维度分析 JSON
- **阶段 2 全局融合**（1 次调用）：定量统计 + 抽样分析 + 读者反馈 → 3 个 MD 文件
- 总计 ~8 次 Claude 调用，~10 min 全自动

### 实现状态

- ✅ Skill 文件创建完成（`batch_style.py` + 2 个 prompt 模板 + `SKILL.md`）
- ✅ 901 章全量统计测试通过（8s，302.6 万字，jieba 词频 + 全量定量指标）
- ✅ 20 章抽样精析完成（7 批，8 次调用，10m34s）
- ✅ 全局融合完成，生成 narrative.md（12KB）+ vocabulary.md（8KB）+ rhythm.md（9KB）+ index.md
- ✅ 验证通过：3 个文件均含写作指导，结构完整

---

## T8：写作 Skill 编写 ✅

**类型**：skill 编写
**产出**：`.claude/skills/novel-write/SKILL.md` + `templates/` + `novel_kb/{book}/guide.md`
**详细方案**：[T8_写作Skill.md](./T8_写作Skill.md)

### 目标

编写最终的续写 skill，指导 Claude 如何使用知识库进行续写。

### 内容

- `SKILL.md`：skill 定义 + 7 步续写流程（含 Context Bundle + Scene Plan + Style Anchors）
- `templates/context_bundle.md`：Bundle 模板（S1-S5 结构框架）
- `templates/style_anchors.md`：风格锚点候选库（6 类场景，从原文精选）
- `guide.md`：放在知识库内的 AI 使用说明

### 关键流程（v2：7 步）

```
读 guide.md → Research → Context Bundle（~3KB 高密度上下文）
→ 大纲（写什么）→ 用户确认
→ Scene Plan + Style Anchors（怎么写）⭐
→ 写正文 → 保存到 drafts/
```

> T8 只负责生成，质量审核完全交给 T9（独立评审，非"自己改卷"）。

### 实现状态

- ✅ 方案文档完成（`T8_写作Skill.md`：5 大挑战、7 步流程、三个中间产物设计）
- ✅ SKILL.md 重写完成（v2：7 步流程、Context Bundle + Scene Plan + Style Anchors、铁则速查版）
- ✅ templates/context_bundle.md 创建完成（S1-S5 结构框架 + 占位符）
- ✅ templates/style_anchors.md 创建完成（6 类场景、18 个锚点、从 ch0001-ch0004 精选）
- ✅ guide.md 创建完成（`qidian/novel_kb/玄鉴仙族/guide.md`：知识库导航、续写铁则、场景加载策略）

---

## T9：验证测试 ✅

**类型**：skill 测试
**skill 名称**：`novel-write/verify`（已合并到 novel-write）
**详细方案**：[T9_验证测试.md](./T9_验证测试.md)

### 目标

构建独立的三层验证体系，对 T8 续写草稿进行质量评估。T8 只负责生成，T9 是**唯一的质量关卡**。

### 三层验证架构

| 层级 | 内容 | AI 调用 |
|------|------|---------|
| Layer 1 | 纯 Python 定量检测（12 项指标 vs stats.json 基准线） | 0 次 |
| Layer 2 | 盲测对比 + 深度风格分析 | 2 次 |
| Layer 3 | 跨章一致性（2+ 章时触发） | 0-1 次 |

### 评分系统

```
总分 = L1 × 40% + L2 × 45% + L3 × 15%
A (90-100) 优秀 | B (80-89) 良好 | C (70-79) 及格 | D (<70) 不合格
```

### 实现状态

- ✅ 方案文档完成（`T9_验证测试.md`：三层架构、12 项指标、评分系统、反哺流程）
- ✅ Skill 文件创建完成（`batch_verify.py` + 3 个 prompt 模板 + `SKILL.md`）
- ✅ 端到端测试通过（ch0004：L1=85.0, L2=76.8, 综合=80.5, 评级 B）
- ✅ 盲测对比有效（Claude 错误地判断原文为 AI，说明草稿质量高）
- ✅ 历史追踪正常（recurring_issues 追踪反复问题）
- ✅ T8 自审已移除，质量审核完全由 T9 独立承担

---

## 建议实施顺序

```
第一轮（基础层）：T1 → T2
第二轮（并行）：  T3 + T4 | T6 | T7
第三轮（依赖层）：T5
第四轮（集成）：  T8 → T9
```

其中 T2 是工作量最大的任务（900+ 章需要多次 skill 调用），建议优先启动。
