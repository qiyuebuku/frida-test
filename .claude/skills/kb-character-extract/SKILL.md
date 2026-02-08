---
name: kb-character-extract
description: 从章节摘要中提取角色信息，构建完整人物库（角色档案、关系网、人物索引）
user_invocable: false
---

# kb-character-extract

从小说知识库的章节摘要 + 剧情层产出中提取角色信息，构建完整的人物库。每个角色包含分级档案（核心 5 模块 / 重要 3 模块），另外生成人物索引和关系网。

## 前置条件

- T2（章节摘要）必须全量完成：`{book-dir}/plot/chapters/chNNNN.md` 和 `index.md`
- T3（剧情层）必须完成：`{book-dir}/plot/outline/arc_XX.md` 和 `plot_lines.md`

## 使用方式

通过 `batch_character.py` 脚本自动化调用 `claude -p`，五阶段 pipeline 全自动运行。

### 常用命令

```bash
# 全流程运行（从断点继续）
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录>

# 只运行特定阶段
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase preprocess
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase alias-merge
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase deep-dive
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase relationship
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase status-update

# 只处理特定角色（调试用）
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --phase deep-dive --character "角色名"

# 控制参数
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --model sonnet
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --dry-run
python .claude/skills/kb-character-extract/batch_character.py --book-dir <知识库目录> --validate
```

> `<知识库目录>` 示例：`qidian/novel_kb/玄鉴仙族`

## 五阶段 Pipeline

0. **Python 预处理**（0 次调用）：解析 T2 摘要结构化字段 + T3 弧文件 → `raw_census.json`
1. **别名合并与分级**（1-2 次调用）：语义识别别名 → `census.json` + `alias_mapping.json`
2. **角色深度分析**（~20-40 次调用）：逐角色加载相关章节 → 角色档案 Markdown
3. **关系网构建**（~3-5 次调用）：综合角色档案 → `relationships.md`
4. **状态精修与索引**（~5-10 次调用）：从最新章节更新活跃角色状态 → `index.md`

## 输入

- `{book-dir}/plot/chapters/chNNNN.md`：T2 生成的章节摘要
- `{book-dir}/plot/chapters/index.md`：T2 全局索引
- `{book-dir}/plot/chapters/.progress.json`：T2 累积角色名册
- `{book-dir}/plot/outline/arc_XX.md`：T3 弧划分
- `{book-dir}/plot/outline/plot_lines.md`：T3 主线追踪

## 输出

```
{book-dir}/characters/
├── index.md                    # 人物索引
├── {name_pinyin}.md            # 核心/重要角色档案
├── relationships.md            # 人物关系网
├── .progress.json              # 进度文件
└── .build/                     # 中间产物
    ├── raw_census.json         # 阶段 0: Python 预提取
    ├── census.json             # 阶段 1: 合并后全局统计
    └── alias_mapping.json      # 阶段 1: 别名映射表（可人工审查）
```

## 文件结构

```
.claude/skills/kb-character-extract/
├── SKILL.md                          # 本文件
├── batch_character.py                # 主编排脚本
└── prompts/
    ├── alias_merge.md                # 阶段 1: 别名合并与分级
    ├── character_deep_core.md        # 阶段 2: 核心角色深度分析
    ├── character_deep_important.md   # 阶段 2: 重要角色简化分析
    ├── relationship_build.md         # 阶段 3: 关系网构建
    ├── relationship_validate.md      # 阶段 3: 关系交叉验证
    └── status_update.md              # 阶段 4: 状态精修
```
