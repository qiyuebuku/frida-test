# 知识图谱验证脚本

## 01. ft_news 到正式 Card Relation 全链路验证

脚本：`01_ft_news_card_relation_workflow.py`

该脚本把原来的 Card 提取和 Relation Discovery 两个分段脚本合并为一个同步工作流。每次运行默认先清理当前 `target` 下该验证链路维护的 Evidence、Chunk、Card、Assignment、Card Relation、编译记录及对应 Milvus 文档，但不会删除 `ft_news`、LLM 调用审计和其他业务数据。之后读取最新 3 条真实 `ft_news`，依次完成：

1. `ft_news` 投影为 Evidence；
2. Evidence Chunk manifest 和 Primary Chunk Milvus 发布；
3. 单次 LLM 调用提取原子 Cognitive Card、Focus Evidence、Relation Probe 和同 Chunk Relations；
4. Card manifest、Summary/Focus 双视图发布，并直接持久化同 Chunk Edge；
5. 跨 Chunk Relation Probe 多路召回和 Summary rerank；
6. 跨 Chunk Summary LLM 初筛；
7. 不同 Primary Chunk 原文精确取回和一对一核验；
8. 跨 Chunk Observed/Inferred 正关系写入 `kg_card_relations`；
9. 所有正式 Edge 完成 Milvus 发布和 `kg_graph_changed` 事件投递。

处理最新 3 条新闻：

```bash
python scripts/知识图谱/01_ft_news_card_relation_workflow.py --limit 3
```

精确处理或重复验证指定新闻：

```bash
python scripts/知识图谱/01_ft_news_card_relation_workflow.py \
  --news-id 109003 \
  --news-id 109004
```

需要验证幂等，或者希望本批 Card 与此前已经存在的 Card 建立跨批关系时，显式保留已有数据：

```bash
python scripts/知识图谱/01_ft_news_card_relation_workflow.py \
  --limit 5 \
  --keep-existing-data
```

保留各阶段候选 ID 以便离线评测：

```bash
python scripts/知识图谱/01_ft_news_card_relation_workflow.py \
  --limit 5 \
  --include-evaluation-details
```

该工作流会写入正式 PG/Milvus 当前态，不提供只执行后半段的隐式 dry-run。默认清理模式用于观察一批输入从空状态生成的质量；使用 `--keep-existing-data` 时，相同 `ft_news` 可以重复执行，Evidence、Card、Milvus target 和 Edge 均按稳定 identity 覆盖，Edge 当前态没有变化时，结果中的 `changed_edge_ids` 和 `graph_event_ids` 应为空。

Langfuse Trace 名称为 `kg.ft_news_card_relation.workflow`，完整结果默认保存到 `/tmp/01_ft_news_card_relation_workflow_<session>.json`。首次使用新表结构前，需要执行当前知识图谱 DDL，确保原子 Card manifest、Relation Probe 字段和 `kg_card_relations` 已存在。

仅从最新新闻并发验证 Card 抽取质量，不清理数据、不写入 PG/Milvus、不执行跨 Chunk 关系发现：

```bash
python scripts/知识图谱/01_ft_news_card_relation_workflow.py \
  --mode cards \
  --limit 20 \
  --concurrency 5 \
  --chunk-timeout 120
```

Card 模式按真实 Chunk 并发执行，每完成一个 Chunk 立即输出进度。单个 Chunk 超时或失败只会记录到当前结果，不会取消其他已成功请求。完整结果默认保存到 `/tmp/01_ft_news_card_relation_cards_<session>.json`，Langfuse Trace 名称为 `kg.atomic_card.batch_validation`。

## Relation Discovery 批量质量评测

脚本：`relation_discovery_eval.py`

默认读取人工标注集 `datasets/relation_discovery_eval.json`，逐案例运行完整关系发现链路，并计算每阶段 Recall、Observed/Inferred precision/recall、硬负样本拒绝率和关系类型覆盖率：

```bash
python scripts/知识图谱/relation_discovery_eval.py --fail-on-quality
```

执行指定案例或自定义标注集：

```bash
python scripts/知识图谱/relation_discovery_eval.py \
  --dataset /path/to/relation_eval.json \
  --case-id ai_memory_price_and_equipment_demand
```

生产服务默认不返回候选身份明细；该脚本会显式开启评测模式，记录每条 route 的双视图召回 IDs、rerank IDs、合并、候选预算、Summary 初筛、原文核验和最终关系。Trace 名称为 `kg.relation_discovery.eval`。

## Relation 任务可靠性验收

脚本：`relation_task_reliability_validation.py`

脚本使用唯一 jettask/Redis 前缀启动隔离 Worker，不监听或终止现有业务 Worker。它会强制 SIGKILL 正在处理测试消息的 Worker，再启动新 Worker 验证 pending 恢复，同时验证重复逻辑消息拥有独立 event ID 且均能完成：

```bash
python scripts/知识图谱/relation_task_reliability_validation.py --timeout 30
```

重复消息不会被 jettask 自动做业务去重；正式 Edge 幂等必须由下一阶段 Edge Writer 根据 canonical pair 和关系身份实现。Trace 名称为 `kg.relation.task_reliability_validation`。
