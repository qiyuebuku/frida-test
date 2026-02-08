---
name: kb-reader-feedback
description: 从段落级评论数据中提炼读者反馈（情绪触发点、角色人气、不满点、期待）
user_invocable: false
---

# kb-reader-feedback

从小说知识库的段落级评论数据中提炼读者反馈，构建读者层知识库。产出 4 个分析文件 + 1 个索引文件。

## 前置条件

- T1（数据提取）必须完成：`{book-dir}/reader/comments/chNNNN.md`

> T6 只依赖 T1，可与 T2~T5 并行执行。

## 使用方式

通过 `batch_reader.py` 脚本自动化调用 `claude -p`，三阶段 pipeline 全自动运行。

### 常用命令

```bash
# 全流程运行（从断点继续）
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录>

# 只运行特定阶段
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --phase preprocess
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --phase segment
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --phase merge

# 控制参数
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --model sonnet
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --dry-run
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --validate
python .claude/skills/kb-reader-feedback/batch_reader.py --book-dir <知识库目录> --segment-size 100
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`

## 三阶段 Pipeline

0. **Python 预处理**（0 次调用）：解析 901 个评论文件 → 去重 → 去噪 → 统计聚合 → `processed_data.json`
1. **分段分析**（~5-10 次调用）：每 100 章精选数据 → Claude 四维分析（情绪/角色/不满/期待）→ 段级 JSON
2. **全局融合**（1-2 次调用）：合并段级 JSON → 生成 5 个 MD 文件

## 输入

- `{book-dir}/reader/comments/chNNNN.md`：T1 提取的段落级评论文件（901 个，共 101 MB）

## 输出

```
{book-dir}/reader/
├── index.md                  # 读者层总览
├── feedback/
│   ├── emotions.md           # 情绪触发点
│   ├── popular_characters.md # 角色人气
│   ├── complaints.md         # 读者不满点
│   └── expectations.md       # 读者期待
├── .progress.json            # 进度文件
└── .build/                   # 中间产物
    ├── processed_data.json   # 阶段 0: 预处理结果
    └── segment_XX.json       # 阶段 1: 段级分析
```

## 文件结构

```
.claude/skills/kb-reader-feedback/
├── SKILL.md                  # 本文件
├── batch_reader.py           # 主编排脚本
└── prompts/
    ├── segment_analyze.md    # 阶段 1: 分段四维分析
    └── global_merge.md       # 阶段 2: 全局融合
```
