---
name: kb-world-extract
description: 从章节摘要中提取世界观设定，构建完整世界层（力量体系、地理、势力、规则）
user_invocable: false
---

# kb-world-extract

从小说知识库的章节摘要 + 弧文件 + 角色档案中提取世界观设定，构建完整的世界层知识库。产出 4 个分类文件 + 1 个索引文件。

## 前置条件

- T2（章节摘要）必须完成：`{book-dir}/plot/chapters/chNNNN.md`
- T3（剧情层）必须完成：`{book-dir}/plot/outline/arc_XX.md`
- T4（角色层）必须完成：`{book-dir}/characters/*.md`

## 使用方式

通过 `batch_world.py` 脚本自动化调用 `claude -p`，四阶段 pipeline 全自动运行。

### 常用命令

```bash
# 全流程运行（从断点继续）
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录>

# 只运行特定阶段
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --phase preprocess
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --phase segment-classify
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --phase global-merge
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --phase refine

# 控制参数
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --model sonnet
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --dry-run
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --validate
python .claude/skills/kb-world-extract/batch_world.py --book-dir <知识库目录> --segment-size 200
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`

## 四阶段 Pipeline

0. **Python 预处理**（0 次调用）：解析 T2 "新增设定"/"重要物品" + T3 "新世界信息" + T4 角色势力 → `raw_world_data.json`
1. **分段提取与分类**（~1-10 次调用）：每 100 章设定 → 按 4+1 类分类 → 段级 JSON
2. **全局融合**（1-2 次调用）：合并段级 JSON → 去重/演化追踪 → 4 个 MD 文件
3. **精修与原文补充**（~0-10 次调用）：关键设定精修 + 一致性验证

## 输入

- `{book-dir}/plot/chapters/chNNNN.md`：T2 章节摘要（"新增设定" + "重要物品"）
- `{book-dir}/plot/outline/arc_XX.md`：T3 弧文件（"新世界信息"）
- `{book-dir}/characters/*.md`：T4 角色档案（势力归属）

## 输出

```
{book-dir}/world/
├── power_system.md           # 力量体系
├── geography.md              # 地理空间
├── factions.md               # 组织势力
├── rules.md                  # 规则与限制
├── index.md                  # 世界观总览
├── .progress.json            # 进度文件
└── .build/                   # 中间产物
    ├── raw_world_data.json           # 阶段 0: Python 预提取
    ├── segment_XX.json               # 阶段 1: 段级分类
    └── consistency_validation.json   # 阶段 3: 一致性验证
```

## 文件结构

```
.claude/skills/kb-world-extract/
├── SKILL.md                          # 本文件
├── batch_world.py                    # 主编排脚本
└── prompts/
    ├── segment_classify.md           # 阶段 1: 分段分类
    ├── global_merge.md               # 阶段 2: 全局融合
    ├── refine_power_system.md        # 阶段 3: 力量体系精修
    ├── refine_geography.md           # 阶段 3: 地理空间精修
    ├── refine_factions.md            # 阶段 3: 组织势力精修
    └── validate_consistency.md       # 阶段 3: 一致性验证
```
