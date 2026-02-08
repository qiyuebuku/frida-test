---
name: kb-plot-extract
description: 从章节摘要中提取全局剧情结构（故事弧、主线追踪、伏笔汇总、时间线）
user_invocable: false
---

# kb-plot-extract

从小说知识库的章节摘要中提取全局剧情结构，产出故事弧划分、主线追踪、伏笔汇总、事件时间线等结构化文件。

## 前置条件

T2（章节摘要）必须全量完成：
- `{book-dir}/plot/chapters/index.md` 存在
- `{book-dir}/plot/chapters/chNNNN.md` 覆盖全部章节

## 使用方式

通过 `batch_plot.py` 脚本自动化调用 `claude -p`，三阶段 pipeline 全自动运行。

### 常用命令

```bash
# 全流程运行（从断点继续）
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录>

# 只运行特定阶段
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --phase segment-scan
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --phase global-merge
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --phase refine

# 控制参数
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --segment-size 100
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --model sonnet
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --dry-run
python .claude/skills/kb-plot-extract/batch_plot.py --book-dir <知识库目录> --validate
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`、`novel_kb/某本小说` 等，脚本通过此路径定位 `plot/chapters/` 下的 T2 产出。

## 三阶段 Pipeline

1. **分段扫描**（~10 次调用）：每 100 章完整摘要 + 全局 index → 段级分析 JSON
2. **全局融合**（1 次调用）：全部段级 JSON + 全局 index → 6 个产出文件初稿
3. **精修验证**（~25 次调用）：回查章节摘要 → 弧边界校正 + 内容充实 + 伏笔验证

## 输入

- `{book-dir}/plot/chapters/chNNNN.md`：T2 生成的章节摘要
- `{book-dir}/plot/chapters/index.md`：T2 生成的全局索引

## 输出

```
{book-dir}/plot/
├── index.md                   # 剧情层总览导航
├── open_loops.md              # 伏笔与未完成事项汇总
├── outline/
│   ├── index.md               # 大纲概览 + 分弧索引
│   ├── plot_lines.md          # 全局主线追踪
│   └── arc_01.md ~ arc_XX.md  # 弧级摘要
└── timeline/
    └── index.md               # 事件时间线
```

## 中间产物

```
{book-dir}/plot/outline/
├── .progress.json             # 进度文件
└── .segments/
    └── segment_01.json ~ segment_XX.json
```

## 文件结构

```
.claude/skills/kb-plot-extract/
├── SKILL.md                      # 本文件
├── batch_plot.py                  # 主编排脚本
└── prompts/
    ├── segment_scan.md            # 阶段 1: 分段扫描
    ├── global_merge.md            # 阶段 2: 全局融合
    ├── arc_boundary_validate.md   # 阶段 3a: 弧边界验证
    ├── arc_detail.md              # 阶段 3b: 弧内容充实
    ├── foreshadow_validate.md     # 阶段 3c: 伏笔验证
    └── final_update.md            # 阶段 3d: 最终更新
```
