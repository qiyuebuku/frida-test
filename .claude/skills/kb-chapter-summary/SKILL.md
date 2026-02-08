---
name: kb-chapter-summary
description: 为小说知识库批量生成章节结构化摘要
user_invocable: false
---

# kb-chapter-summary

为小说知识库的每个章节生成结构化摘要（8 字段：关键事件、新登场角色、已有角色出场、关系变化、重要物品、新增设定、伏笔/悬念、一句话摘要）。

## 使用方式

通过 `batch_summary.py` 脚本自动化调用 `claude -p`，无需手动操作。

### 常用命令

```bash
# 基本用法：从断点继续处理全部章节
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录>

# 指定范围
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --range 1-50

# 每批处理 10 章（默认 5）
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --batch-size 5

# 试运行（只显示计划，不执行）
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --dry-run

# 验证已生成的摘要
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --validate

# 重试失败项
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --retry-failed

# 生成索引
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --gen-index

# 使用 haiku 模型（默认 sonnet）
python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> --model haiku
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`、`novel_kb/某本小说` 等，脚本通过此路径定位 `text/`、`book_detail.md`、`plot/chapters/`。

### 全量无人值守运行

```bash
nohup python .claude/skills/kb-chapter-summary/batch_summary.py --book-dir <知识库目录> > summary.log 2>&1 &
tail -f summary.log
```

## 输入

- `{book-dir}/text/ch0001.md` ~ `chNNNN.md`：纯正文文件
- `{book-dir}/book_detail.md`：书籍详情（自动提取背景和初始角色）

## 输出

- `{book-dir}/plot/chapters/ch0001.md` ~ `chNNNN.md`：单章结构化摘要
- `{book-dir}/plot/chapters/index.md`：全部章节一行摘要索引
- `{book-dir}/plot/chapters/.progress.json`：进度文件（断点续跑）

## 文件结构

```
.claude/skills/kb-chapter-summary/
├── SKILL.md               # 本文件
├── batch_summary.py        # 主编排脚本
└── summary_prompt.md       # 通用 Prompt 模板
```
