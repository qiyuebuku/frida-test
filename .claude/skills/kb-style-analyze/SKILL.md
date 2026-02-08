---
name: kb-style-analyze
description: 分析原文写作风格，生成结构化风格参考（叙事特征、用词特征、节奏特征）
user_invocable: false
---

# kb-style-analyze

分析小说知识库的原文写作风格，生成结构化风格参考文件。产出 3 个分析文件 + 1 个索引文件。

## 前置条件

- T1（数据提取）必须完成：`{book-dir}/text/chNNNN.md`
- T3（剧情层）必须完成：`{book-dir}/plot/outline/arc_*.md`
- T6（读者层）必须完成：`{book-dir}/reader/feedback/emotions.md`
- Python 包 `jieba` 必须安装：`pip install jieba`

> T7 硬依赖 T1 + T3 + T6 + jieba，缺一不可。

## 使用方式

通过 `batch_style.py` 脚本自动化调用 `claude -p`，三阶段 pipeline 全自动运行。

### 常用命令

```bash
# 全流程运行（从断点继续）
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录>

# 只运行特定阶段
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --phase preprocess
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --phase sample
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --phase merge

# 控制参数
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --model sonnet
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --dry-run
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --validate
python .claude/skills/kb-style-analyze/batch_style.py --book-dir <知识库目录> --sample-size 30
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`

## 三阶段 Pipeline

0. **Python 全量统计 + 辅助数据解析**（0 次调用）：全 901 章定量统计（句长/对话/段落/词频）+ 解析 T3 弧文件 + 解析 T6 读者反馈 → `stats.json` + `sampling_plan.json`
1. **Claude 抽样精析**（~5-8 次调用）：按弧智能抽样 15-30 章原文 → 7 维定性分析（含读者验证维度）→ `sample_analysis_XX.json`
2. **全局融合**（1 次调用）：合并 Python 统计 + Claude 分析 + 读者反馈 → 生成 4 个 MD 文件

## 输入

- `{book-dir}/text/chNNNN.md`：T1 提取的纯正文文件（901 个，共 9 MB）
- `{book-dir}/plot/outline/arc_*.md`：T3 弧文件（按弧分段 + 转折章抽样）
- `{book-dir}/reader/feedback/emotions.md`：T6 读者反馈（高赞段落 + 写作启示）

## 输出

```
{book-dir}/style/
├── index.md                  # 风格概览
├── narrative.md              # 叙事特征
├── vocabulary.md             # 用词特征
├── rhythm.md                 # 节奏特征
├── .progress.json            # 进度文件
└── .build/                   # 中间产物
    ├── stats.json            # 阶段 0: 全量统计
    ├── sampling_plan.json    # 阶段 0: 抽样计划
    └── sample_XX.json        # 阶段 1: 抽样分析
```

## 文件结构

```
.claude/skills/kb-style-analyze/
├── SKILL.md                  # 本文件
├── batch_style.py            # 主编排脚本
└── prompts/
    ├── sample_analyze.md     # 阶段 1: 抽样精析
    └── global_merge.md       # 阶段 2: 全局融合
```
