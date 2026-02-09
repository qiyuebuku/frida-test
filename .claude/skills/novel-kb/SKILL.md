---
name: novel-kb
description: 小说知识库构建 Pipeline（T1-T7：原文处理/章节摘要/剧情/角色/世界/读者/风格）
user_invocable: true
---

# 小说知识库构建 Skill

## 参数解析

从用户输入中提取 `KB_DIR`（必须）和可选参数：

```bash
/novel-kb <KB_DIR> --stage <t1|t2|t3|t4|t5|t6|t7|all|status> [--phase PHASE] [--model MODEL] [--dry-run] [--validate] [--concurrency N] [--timeout N]
```

| 参数 | 说明 | 必须 |
|------|------|------|
| `KB_DIR` | 知识库目录路径（如 `qidian/novel_kb/玄鉴仙族`） | 是 |
| `--stage` | 执行阶段（见下表） | 是 |
| `--phase` | 指定阶段内的子步骤（如 `segment-scan`、`global-merge`） | 否 |
| `--model` | Claude 模型（默认 sonnet） | 否 |
| `--dry-run` | 只显示将要执行的操作，不实际执行 | 否 |
| `--validate` | 验证已有产出的完整性 | 否 |
| `--concurrency` | 并发数（部分脚本支持） | 否 |
| `--timeout` | 单次 Claude 调用超时秒数 | 否 |

---

## 分发逻辑

收到参数后，构造命令调用对应脚本：

```bash
# 查看进度
python .claude/skills/novel-kb/kb_orchestrator.py \
  --book-dir {KB_DIR} --stage status

# 运行某阶段
python .claude/skills/novel-kb/kb_orchestrator.py \
  --book-dir {KB_DIR} --stage {STAGE} [--phase PHASE] [--model MODEL] [--dry-run] [--validate]

# T1 独立运行（不走 orchestrator）
python .claude/skills/novel-kb/process_chapters.py --book-dir {KB_DIR}
```

---

## 阶段说明

| 阶段 | 脚本 | 功能 | 前置依赖 |
|------|------|------|----------|
| T1 | `process_chapters.py` | 原文处理（分章、清洗） | 原始文本文件 |
| T2 | `batch_summary.py` | 章节摘要生成 | `text/` 目录（T1 产出） |
| T3 | `batch_plot.py` | 剧情层提取（弧、主线、伏笔） | `plot/chapters/`（T2 产出） |
| T4 | `batch_character.py` | 角色层提取（档案、关系网） | `plot/chapters/` + `plot/outline/`（T2+T3 产出） |
| T5 | `batch_world.py` | 世界层提取（力量、地理、势力） | `plot/chapters/` + `plot/outline/`（T2+T3 产出） |
| T6 | `batch_reader.py` | 读者层分析（情绪、人气、投诉） | `reader/comments/`（评论数据） |
| T7 | `batch_style.py` | 风格层分析（叙事、词汇、节奏） | `text/`（T1 产出） |

### 执行顺序（`--stage all`）

```
T2（章节摘要）→ T6（读者层）→ T3（剧情层）→ T4（角色层）→ T5（世界层）→ T7（风格层）
```

T1 需独立运行，不在 `--stage all` 编排范围内。

---

## 知识库目录结构

构建完成后的知识库目录：

```
{KB_DIR}/
├── text/                    # T1: 原文（分章 MD）
│   ├── ch0001.md
│   └── ...
├── plot/                    # T2+T3: 剧情层
│   ├── chapters/            # T2: 章节摘要
│   │   ├── index.md
│   │   ├── ch0001.md
│   │   └── ...
│   ├── outline/             # T3: 弧与主线
│   │   ├── index.md
│   │   ├── arc_01.md
│   │   ├── plot_lines.md
│   │   └── ...
│   ├── open_loops.md        # T3: 伏笔追踪
│   └── timeline/            # T3: 时间线
├── characters/              # T4: 角色层
│   ├── index.md
│   ├── {name}.md
│   └── relationships.md
├── world/                   # T5: 世界层
│   ├── power_system.md
│   ├── geography.md
│   ├── factions.md
│   └── rules.md
├── reader/                  # T6: 读者层
│   ├── comments/            # 原始评论数据
│   └── feedback/            # 分析结果
│       ├── emotions.md
│       ├── popular_characters.md
│       ├── complaints.md
│       └── expectations.md
├── style/                   # T7: 风格层
│   ├── narrative.md
│   ├── vocabulary.md
│   ├── rhythm.md
│   ├── anchors.md           # 风格锚点（可选，手动或自动生成）
│   └── .build/
│       └── stats.json       # 定量统计基线
├── guide.md                 # 由 novel-write 自动生成的导航文件
└── drafts/                  # novel-write 的草稿区
```

---

## 进度显示

`--stage status` 查看各层完成度：

```
T1 原文处理:    ✅ 已完成（1201 章）
T2 章节摘要:    ✅ 已完成（1201 个摘要）
T3 剧情层:      ✅ 已完成（6 个文件）
T4 角色层:      ✅ 已完成（15 个角色档案）
T5 世界层:      ✅ 已完成（5 个文件）
T6 读者层:      ✅ 已完成（4 个反馈文件）
T7 风格层:      ✅ 已完成（3 个分析文件 + stats.json）
```

`--stage all` 执行时实时显示：
- **阶段进度**：`[1/6] T2 章节摘要`
- **批次进度**：`批次 1/210: ch0019~ch0158 (5 章)`
- **总进度**：`总进度: 153/1201 (12%)`
- **耗时统计**：`T2 完成，耗时 15m30s`

---

## 用法示例

```bash
# 查看知识库构建进度
/novel-kb qidian/novel_kb/玄鉴仙族 --stage status

# 运行单个阶段
/novel-kb qidian/novel_kb/玄鉴仙族 --stage t2
/novel-kb qidian/novel_kb/玄鉴仙族 --stage t3 --phase segment-scan
/novel-kb qidian/novel_kb/玄鉴仙族 --stage t7 --dry-run

# 验证某阶段产出
/novel-kb qidian/novel_kb/玄鉴仙族 --stage t3 --validate

# 运行全部阶段（按依赖顺序）
/novel-kb qidian/novel_kb/玄鉴仙族 --stage all

# T1 原文处理（独立运行）
/novel-kb qidian/novel_kb/玄鉴仙族 --stage t1
```

---

## guide.md 生成

`--stage all` 完成后（或手动指定 `--stage guide`），按 `prompts/guide_template.md` 模板从知识库文件中提取信息，生成 `{KB_DIR}/guide.md`。此文件是 `novel-write` skill 的必要前置。

---

## 工具脚本说明

### json_fixer.py — 三层 JSON 修复

所有 batch 脚本共用的 JSON 解析器，处理 Claude 输出中常见的 JSON 格式错误：

| 层 | 修复方式 | 适用场景 |
|----|----------|----------|
| Layer 1 | 正则修复（尾逗号、单引号、换行） | 简单格式问题 |
| Layer 2 | 语法修复（逐行扫描转义引号） | 字段值内未转义引号 |
| Layer 3 | Claude AI 修复（`claude -p` 子进程） | 复杂嵌套错误 |

### kb_orchestrator.py — 编排脚本

管理依赖检查、进度追踪、按顺序分发调用各 batch 脚本。
