# guide.md 生成模板

当 `{KB_DIR}/guide.md` 不存在时，读取知识库中以下文件的关键信息生成：

- `book_detail.md`（书名、作者、标签）
- `plot/index.md`（当前进度、故事弧数）
- `plot/chapters/index.md`（最后章节号）
- `style/narrative.md`（情感表达禁忌）
- `style/vocabulary.md`（禁忌词、称呼系统）
- `style/rhythm.md`（章节结构偏好）

生成模板结构：

```markdown
# {书名} — AI 续写知识库使用指南

## 书籍概况
- 作者：{作者}
- 类型标签：{标签}
- 当前进度：{最后章节号}，共 {N} 个故事弧
- 知识库覆盖：{已分析章节数} 章

## 知识库导航

### 必读（每次续写前）
- `plot/index.md` — 剧情层总览
- `plot/outline/plot_lines.md` — 全局主线追踪
- `plot/open_loops.md` — 伏笔状态
- `style/narrative.md` — 叙事约束（含情感禁忌）
- `style/vocabulary.md` — 用词约束（含禁忌词）
- `style/rhythm.md` — 节奏约束（含章节结构）

### 按需（根据续写内容选读）
- `characters/{name}.md` — 涉及角色的详细档案
- `characters/relationships.md` — 角色关系网
- `world/power_system.md` — 力量体系（战斗场景）
- `world/geography.md` — 地理设定（场景转换）
- `world/factions.md` — 组织势力
- `world/rules.md` — 世界规则与限制
- `plot/timeline/index.md` — 时间线（防穿帮）
- `text/chNNNN.md` — 原文正文（需要精确衔接时）

### 选读（优化方向）
- `reader/feedback/emotions.md` — 情绪触发点
- `reader/feedback/popular_characters.md` — 角色人气
- `reader/feedback/complaints.md` — 读者不满点
- `reader/feedback/expectations.md` — 读者期待
- `reader/comments/chNNNN.md` — 具体段落反应

## 续写铁则（通用）
1. 每句包含多重信息
2. 章首/章尾类型匹配原文偏好
3. 段落融合——对话与叙述必须交织

## 续写铁则（书籍特定，从 style/ 提取）
{从 narrative.md 提取的情感表达约束}
{从 vocabulary.md 提取的用词禁忌}
{从 rhythm.md 提取的节奏偏好}
{从 guide.md 中的特殊角色规则}

## 场景→加载策略
| 场景类型 | 必加载 | 可选 |
|----------|--------|------|
| 战斗 | power_system.md + 角色档案 | rules.md |
| 日常 | 角色档案 + relationships.md | geography.md |
| 悬疑 | open_loops.md + timeline | 伏笔原章节 |
| 情感 | 角色档案 + emotions.md | popular_characters.md |
| 探索 | geography.md + factions.md | power_system.md |

## 常见查询索引
| 查什么 | 去哪找 |
|--------|--------|
| 下一章该写什么 | plot/outline/arc_XX.md + plot_lines.md |
| 角色性格/能力 | characters/{name}.md |
| 某个伏笔的来龙去脉 | plot/open_loops.md → 对应章节摘要 |
| 修炼境界/法术 | world/power_system.md |
| 地点位置关系 | world/geography.md |
| 读者喜欢什么 | reader/feedback/emotions.md |
| 读者讨厌什么 | reader/feedback/complaints.md |
| 上一章写了什么 | plot/chapters/chNNNN.md（摘要）或 text/chNNNN.md（原文）|
```

控制在 3KB 以内，生成后写入 `{KB_DIR}/guide.md`。
