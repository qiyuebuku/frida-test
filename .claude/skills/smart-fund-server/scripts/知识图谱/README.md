# 知识图谱验证脚本

## 原子 Cognitive Card 验证

脚本：`atomic_cognitive_card_validation.py`

默认从 PostgreSQL 读取最近的少量真实 Evidence Chunk，只执行原子 Card 提取、证据校验和 Relation Probe 生成，不写入数据库：

```bash
python scripts/知识图谱/atomic_cognitive_card_validation.py --limit 3
```

只验证指定 Chunk：

```bash
python scripts/知识图谱/atomic_cognitive_card_validation.py \
  --chunk-id 'kg_chunk:...' \
  --limit 1
```

执行 PG manifest 和 Milvus 发布：

```bash
python scripts/知识图谱/atomic_cognitive_card_validation.py --limit 3 --persist
```

首次使用新表结构前，需要先执行 `docs/database/260711.sql`。该 SQL 会清理无法迁移的旧 Card 和旧 Assignment 数据，并把 `kg_cognitive_cards` 切换为原子 Card manifest 结构。

脚本会创建名为 `kg.atomic_card.validation_demo` 的 Langfuse trace，并在终端打印 Session ID 和结果文件路径。完整链路包含分片、LLM、校验、可选修复、PG 替换、Milvus upsert 和陈旧 target 清理。
