# novel-write Skill

基于六层知识库的小说续写系统。所有操作通过 `/novel-write` 统一入口调用。

## 用法

### 续写章节

```
/novel-write novel_kb/书名
/novel-write novel_kb/书名 --chapter 902
/novel-write novel_kb/书名 --hint "本章重点写李木田和张氏的矛盾"
/novel-write novel_kb/书名 --auto
```

### 验证与修复

```
/novel-write novel_kb/书名 --verify 902              # 完整四层验证
/novel-write novel_kb/书名 --verify 902 --layer 1    # 仅 Layer 1 定量检测
/novel-write novel_kb/书名 --revise 902              # 基于反馈修复
/novel-write novel_kb/书名 --promote 902             # 反哺到正式知识库
/novel-write novel_kb/书名 --history                 # 查看验证历史
```

### 知识库构建（--kb-build）

```
/novel-write novel_kb/书名 --kb-build --stage status                      # 查看进度
/novel-write novel_kb/书名 --kb-build --stage t1 --input 清洗后JSON目录    # 原始数据处理
/novel-write novel_kb/书名 --kb-build --stage t2                          # 章节摘要
/novel-write novel_kb/书名 --kb-build --stage t3                          # 剧情层
/novel-write novel_kb/书名 --kb-build --stage t3 --phase segment-scan     # 剧情层（指定子阶段）
/novel-write novel_kb/书名 --kb-build --stage t4                          # 角色层
/novel-write novel_kb/书名 --kb-build --stage t5                          # 世界层
/novel-write novel_kb/书名 --kb-build --stage t6                          # 读者层
/novel-write novel_kb/书名 --kb-build --stage t7                          # 风格层
/novel-write novel_kb/书名 --kb-build --stage all                         # T2-T7 按依赖顺序全部执行
/novel-write novel_kb/书名 --kb-build --stage t3 --dry-run                # 试运行
/novel-write novel_kb/书名 --kb-build --stage t3 --validate               # 验证产出
```

## KB 构建 Pipeline

```
原始 JSON ─T1─→ text/ + reader/comments/
                │
                ├─T2─→ plot/chapters/（章节摘要）
                │       │
                │       ├─T3─→ plot/outline/（剧情结构）
                │       │       │
                │       │       ├─T4─→ characters/（角色档案）
                │       │       └─T5─→ world/（世界设定）
                │
                ├─T6─→ reader/feedback/（读者反馈）
                │
                └─T7─→ style/（写作风格）
```

### 各阶段

| 阶段 | 功能 | 前置依赖 | --phase 可选值 |
|------|------|----------|---------------|
| T1 | 原始 JSON → 纯正文 + 结构化评论 | 清洗后 JSON（需 `--input`） | - |
| T2 | 章节结构化摘要 | `text/`（T1 产出） | - |
| T3 | 剧情结构（故事弧/主线/伏笔/时间线） | `plot/chapters/`（T2） | segment-scan, global-merge, refine |
| T4 | 角色档案 + 关系网 + 索引 | T2 + T3 | preprocess, alias-merge, deep-dive, relationship, status-update |
| T5 | 世界设定（力量/地理/势力/规则） | T2 + T3 | preprocess, segment-classify, global-merge, refine |
| T6 | 读者反馈（情绪/人气/不满/期待） | `reader/comments/`（T1） | preprocess, segment, merge |
| T7 | 写作风格（叙事/用词/节奏） | `text/`（T1） | preprocess, sample, merge |

## 模式优先级

| 优先级 | 参数 | 行为 |
|--------|------|------|
| 1 | `--history` | 查看验证历史 |
| 2 | `--promote N` | 草稿反哺到正式知识库 |
| 3 | `--verify N` | 四层验证 |
| 4 | `--kb-build` | 知识库构建 |
| 5 | `--auto` | 自动循环（写→验→修→验） |
| 6 | `--revise N` | 修复流程 |
| 7 | 默认 | 正常续写（9 步） |

## 目录结构

```
novel-write/
├── SKILL.md                    # Skill 定义（Claude Code 读取）
├── README.md                   # 本文件
├── templates/                  # 写作模板
│   ├── context_bundle.md
│   └── style_anchors.md
├── verify/                     # 验证子系统
│   ├── batch_verify.py
│   └── prompts/
└── kb/                         # 知识库构建子系统
    ├── kb_orchestrator.py      # 编排入口
    ├── process_chapters.py     # T1
    ├── batch_summary.py        # T2
    ├── batch_plot.py           # T3
    ├── batch_character.py      # T4
    ├── batch_world.py          # T5
    ├── batch_reader.py         # T6
    ├── batch_style.py          # T7
    └── prompts/                # 各阶段 prompt（前缀区分）

知识库产出目录：
novel_kb/书名/
├── text/                       # T1: 纯正文
├── reader/comments/            # T1: 结构化评论
├── reader/feedback/            # T6: 读者反馈分析
├── plot/chapters/              # T2: 章节摘要
├── plot/outline/               # T3: 剧情结构
├── characters/                 # T4: 角色档案
├── world/                      # T5: 世界设定
├── style/                      # T7: 风格分析
└── drafts/                     # 续写草稿区
```

## 依赖

- Python 3.12+
- `jieba`（T7 + 验证必需）
- `pypinyin`（T4 可选）
- Claude CLI（T2-T7 的 AI 调用）
