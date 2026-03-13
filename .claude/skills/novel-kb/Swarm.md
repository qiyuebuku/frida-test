# 世界观提取 - 蜂群模式

启动 Agent Teams 并行处理世界观提取任务。

## 使用方式

```
/swarm --book-dir <知识库路径> --phase <阶段> --concurrency <并发数>
```

## 参数

- `--book-dir`: 知识库目录路径（必需）
- `--phase`: 处理阶段（segment-classify / entity-expand / refine）
- `--concurrency`: 并发队友数量（默认 4）

## 任务分配策略

### segment-classify 阶段

创建 N 个队友（N = min(段数, concurrency)），每个队友负责：
- 读取 `world/.build/raw_world_data.json` 中对应的段数据
- 调用 AI 分类为 5 个类别
- 将结果写入 `world/.build/seg_XX.json`

### entity-expand 阶段

创建 4 个队友，每个负责一个类别：
- 队友1: power_system（力量体系）
- 队友2: geography（地理空间）
- 队友3: factions（组织势力）
- 队友4: rules（规则与限制）

每个队友读取 `merged_entities.json` 中对应类别的数据，执行重要性标注和描述扩写。

### refine 阶段

创建 N 个队友（N = min(high实体数, concurrency)），每个队友负责：
- 读取对应实体的 source_chapters
- 获取相关章节摘要
- 执行精修并更新实体文件

## 队友配置

每个队友继承以下配置：
- 模型: sonnet（可通过 --model 覆盖）
- 工具: Read, Glob, Grep, Write, Edit
- 权限模式: bypassPermissions

## 输出

- 进度追踪: `world/.progress.json`
- 结果文件: 见 batch_world.py 文档

## 示例

```bash
# 分段分类（4 并发）
claude "使用蜂群模式执行世界观提取：--book-dir qidian/novel_kb/玄鉴仙族 --phase segment-classify --concurrency 4"

# 实体扩写（4 类别并行）
claude "使用蜂群模式执行世界观提取：--book-dir qidian/novel_kb/玄鉴仙族 --phase entity-expand"

# 精修 high 实体（8 并发）
claude "使用蜂群模式执行世界观提取：--book-dir qidian/novel_kb/玄鉴仙族 --phase refine --concurrency 8"
```
