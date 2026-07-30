# 写作 Skill 实施任务

> 基于 [架构设计.md](../架构设计.md) 拆分的分步实施任务
> 架构文档回答"为什么这样设计、整体长什么样"，本文档回答"怎么做、做到哪了"
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
T8  写作 Skill ✅   → SKILL.md + guide.md + verify/            skill 编写（含验证系统）
T9  验证测试   ✅   → 已合并到 T8，四层验证体系                集成到 novel-write Skill
```

### 依赖关系

```
T1 ──→ T2 ──→ T3 ──┐
                    ├──→ T8（含 T9 验证系统）
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
- T9 已合并到 T8（验证系统是 Skill 的内置组件，非独立任务）
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

**类型**：skill 编写（含验证系统）
**产出**：`.claude/skills/novel-write/SKILL.md` + `templates/` + `verify/` + `novel_kb/{book}/guide.md`
**详细方案**：[T8_写作Skill.md](./T8_写作Skill.md)

### 目标

编写统一的续写 Skill，**写作和验证一体化**——写作流程内置 L1 自动检测，深度验证通过独立进程实现上下文隔离。

### 文件结构

```
.claude/skills/novel-write/
├── SKILL.md                          # Skill 定义（9 步流程 + 7 种模式）
├── templates/
│   ├── context_bundle.md             # Bundle 模板（S1-S5 结构框架）
│   └── style_anchors.md              # 风格锚点生成模板（6 类场景）
└── verify/
    ├── batch_verify.py               # 三层验证脚本（独立进程执行）
    └── prompts/
        ├── blind_test.md             # 盲测对比 prompt
        ├── style_deep_analysis.md    # 深度风格分析 prompt（含 {BOOK_RULES} 注入）
        └── cross_chapter.md          # 跨章一致性 prompt
```

### 关键流程（v2：9 步）

```
Step 1-2: 读 guide.md → Research → Context Bundle（~3KB）
Step 3-4: 大纲（写什么）→ 用户确认
Step 5:   Scene Plan + Style Anchors（怎么写）⭐
Step 6-7: 写正文 → 保存到 drafts/
Step 8:   自动运行 L1 定量检测（"编译器"，必须执行）⭐
Step 9:   根据结果询问用户（FAIL→强提示 / WARN→温和 / PASS→结束）⭐
```

**设计理念**：

- **L1 = 编译器**：检查格式合法性（禁忌词、句长、短句率），每次写作后自动运行，<5s，0 次 AI 调用
- **L2/L3 = 编辑+市场**：风格分析 + 读者对齐，各 1 次 AI 调用，由用户通过 `--verify` 手动触发
- **L4 = 连续性**：跨章重复检测，纯 Python
- **盲测 → 降级为 `--calibrate`**：仅校准用，不参与评级
- **自动检测 ≠ 自动修复**：检测结果交给用户决定，不擅自改动

### 7 种模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 正常续写 | `--chapter N` | 9 步流程（含自动 L1） |
| 带提示续写 | `--hint "方向"` | 同上，用户提示融入大纲 |
| 修复 | `--revise N` | 在既定设计图下修复正文 |
| 验证 | `--verify N` | 手动触发 L1+L2+L3 深度验证 |
| 自动循环 | `--auto` | 写作→验证→修复→循环（≤3轮） |
| 反哺 | `--promote N` | 草稿纳入正式知识库 |
| 历史 | `--history` | 查看验证历史 |

### 四层验证体系

| 层级 | 内容 | AI 调用 | 耗时 |
|------|------|---------|------|
| Layer 1 | 纯 Python 定量检测（12 项指标 vs `stats.json` 基准线） | 0 次 | <5s |
| Layer 2 | 深度风格分析（注入 `guide.md` 书籍规则防误判） | 1 次 `claude -p` | ~60s |
| Layer 3 | 读者对齐（爽点命中/槽点规避/角色人气/读者期待） | 1 次 `claude -p` | ~60s |
| Layer 4 | 跨章一致性（重复表达检测，2+ 章时触发） | 0 次 | ~5s |
| — | 盲测校准（`--calibrate`，需原文） | 1 次 `claude -p` | ~60s |

**评分**：
- 单章+读者数据：`L1×0.35 + L2×0.40 + L3×0.25`
- 单章无读者：`L1×0.45 + L2×0.55`（向后兼容）
- 多章+读者数据：`L1×0.25 + L2×0.35 + L3×0.20 + L4×0.20`
- 多章无读者：`L1×0.40 + L2×0.45 + L4×0.15`（向后兼容）

### 实现状态

- ✅ SKILL.md 完成（v2：9 步流程、Context Bundle + Scene Plan + Style Anchors、3 通用铁则 + 书籍特定规则）
- ✅ templates/ 完成（context_bundle.md + style_anchors.md）
- ✅ verify/ 完成（batch_verify.py + 3 个 prompt 模板 + reader_alignment.md）
- ✅ guide.md 完成（`qidian/novel_kb/玄鉴仙族/guide.md`：知识库导航、续写铁则、场景加载策略）
- ✅ L2 prompt 注入 guide.md 书籍规则（修复穿越者现代词汇误判）
- ✅ 无原文时 L2 评分调整（风格分析独占 100%，不被盲测稀释）
- ✅ 端到端测试通过（ch0004：L1=90.8(A), L2=79.3, 综合=84.5(B)，**首次生成即 B 级**）
- ✅ 反哺流程完成（`--promote`：正文→text/，摘要→plot/chapters/，索引更新）
- ✅ 验证历史追踪（`--history`：recurring_issues 追踪反复问题）

---

## T9：验证测试 ✅（已合并到 T8）

> **注意**：T9 已不再是独立 Skill。验证系统已完全合并到 `novel-write/verify/` 中，通过 `batch_verify.py` 实现。此处保留 T9 条目仅作为历史记录和详细方案索引。

**详细方案**：[T9_验证测试.md](./T9_验证测试.md)

### 合并说明

T9 原为独立的 `kb-verify-draft` Skill，后合并到 `novel-write/verify/` 子目录中：

- Writer 和 Reviewer 仍通过独立进程（`claude -p`）实现上下文隔离
- 验证脚本由 `/novel-write` Skill 统一调度（`--verify`/`--auto`/Step 8 自动触发）
- 信息流单向：审核→写作（feedback 文件），Writer 不能访问审核的评分标准和盲测细节

### 相对独立时的改进

| 改进项 | 说明 |
|--------|------|
| Step 8 自动 L1 | 写作后自动运行 L1，无需用户手动触发 |
| Step 9 用户决策 | FAIL→强提示修复，WARN→温和提示，PASS→结束 |
| Book rules 注入 | L2 prompt 从 guide.md 提取书籍规则，防止合法用词误判 |
| 无盲测评分 | 新章节无原文时，风格分析独占 100%（不降权） |

### 最新测试结果（2026-02-08）

```
v1（独立T9）：L1=85.0, L2=76.8, 综合=80.5 (B)，3 项 FAIL
v2（合并后） ：L1=90.8, L2=79.3, 综合=84.5 (B)，0 项 FAIL，首次生成即 B 级
```

---

## 建议实施顺序

```
第一轮（基础层）：T1 → T2
第二轮（并行）：  T3 + T4 | T6 | T7
第三轮（依赖层）：T5
第四轮（集成）：  T8（含验证系统）
```

其中 T2 是工作量最大的任务（900+ 章需要多次 skill 调用），建议优先启动。

> **当前全部代码已完成**，T2 待全量运行后，T3/T4/T5 需基于完整数据重跑。
