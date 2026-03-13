---
name: world-swarm
description: 使用 Agent Teams 蜂群模式并行处理世界观提取任务
user_invocable: true
---

# 世界观提取 - 蜂群模式

## 参数

```
/world-swarm <KB_DIR> --phase <阶段> [--concurrency N]
```

| 参数 | 说明 |
|------|------|
| `KB_DIR` | 知识库目录（绝对或相对路径） |
| `--phase` | all / preprocess / segment-classify / global-merge / refine |
| `--concurrency` | 并发数（默认 4） |

---

## 执行流程

**重要：直接执行命令，不要反复检查目录是否存在。**

### 定位脚本目录

batch_world.py 位于本 skill 的父目录（novel-kb）中。使用以下方式定位：

```bash
# 方式1：使用环境变量
SKILL_DIR="$CLAUDE_PROJECT_DIR/.claude/skills/novel-kb"

# 方式2：如果方式1不行，用 find 查找
SKILL_DIR=$(dirname $(find ~ -name "batch_world.py" -path "*/novel-kb/*" 2>/dev/null | head -1))
```

### --phase all（一键全流程）

按顺序执行：

```bash
# 1. preprocess
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase preprocess

# 2. segment-classify
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase segment-classify --concurrency {N}

# 3. global-merge
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase global-merge --concurrency {N}

# 4. refine
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase refine --concurrency {N}
```

### 单阶段执行

```bash
# 仅预处理
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase preprocess

# 仅分段分类
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase segment-classify --concurrency 4

# 仅全局合并
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase global-merge --concurrency 4

# 仅精修
python $SKILL_DIR/batch_world.py --book-dir {KB_DIR} --phase refine --concurrency 4
```

---

## 用法示例

```bash
# 一键全流程（玄鉴仙族）
/world-swarm qidian/novel_kb/玄鉴仙族 --phase all --concurrency 4

# 另一本小说
/world-swarm /path/to/another/novel_kb/斗破苍穹 --phase all --concurrency 4

# 单阶段
/world-swarm qidian/novel_kb/玄鉴仙族 --phase segment-classify --concurrency 4
```

---

## Agent Team 模式（可选）

当 `--concurrency > 1` 且章节数 > 100 时，可创建 Agent Team 并行处理：

```
创建一个 {concurrency} 人的 agent team 并行处理世界观提取任务。

每个队友负责：
- 处理指定范围的章节
- 调用 batch_world.py 处理分配的段落
- 完成后报告结果

队友配置：agent=world-extractor, tools=Read,Bash,Write
```
