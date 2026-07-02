# 知识图谱实施方案

## 1. 阅读说明

本目录保存知识图谱的技术实施方案。文档按功能模块拆分，不按历史 Step 拆分，方便后续按模块回溯问题。

对应的设计方案在：

`docs/5. 设计方案/1. 知识图谱/`

## 2. 文档列表

| 文档 | 说明 |
| --- | --- |
| `1. 数据源投影与Source Record技术方案.md` | 业务表 Raw Row 如何投影为 Source Record。 |
| `2. 知识编译与写入技术方案.md` | Source Record 编译、标准化、Fact Store 写入、证据版本治理、事实层到检索层的解耦交接。 |
| `3. 知识建模与领域适配技术方案.md` | 通用领域模型、Adapter 协议、金融适配器、本体和置信策略。 |
| `4. 实体标准化与规则治理技术方案.md` | 标准化规则、写入前决策、LLM 规则候选、审计和迁移。 |
| `5. 检索与Query Context技术方案.md` | Query Anchor、三段式检索路由、候选裁判、受控图扩展、Chunk Window、Agentic Retrieval 和质量回放。 |
| `6. Agentic检索候选排序优化技术方案.md` | Retrieval Quality Evaluation、Retrieval Document、Keyword/BM25、Rank Fusion、Feature Rerank、Coverage Selector、Stop Verifier 和排序验收。 |
| `7. 检索索引架构重构实施方案.md` | 承接检索索引架构重构的最终设计结论，说明 PG deterministic search、Vector semantic search、Query Parser、enriched vector docs、relation preview、canonical merge、reranker、Agent graph expand 和索引一致性的实施方向。 |
| `8. 写入侧语义索引材料适配实施方案.md` | 承接写入侧语义索引材料设计，说明 relation preview、enriched vector docs、旧派生检索材料退出和索引刷新侧的实施边界。 |
| `9. 待优化.md` | 实施偏差、降级、妥协和后续整改记录。 |
| `10. Milvus语义检索与PG关系展开双层架构实施方案.md` | 承接最新双层架构设计，说明 PG 不存 chunk text、Milvus 保存可读 target、PG refs 连接关系展开和 Milvus get-by-id 的实施方向。 |
| `11. Milvus语义检索与PG关系展开写入链路实施方案.md` | 细化双层架构的写入链路，说明 Evidence、Recursive Chunk、AI 抽取、embedding 候选召回、AI 归一、PG 事实图、Milvus semantic target 和 Community/Finding 高级索引如何落地。 |
| `12. Milvus语义检索与PG关系展开查询链路实施方案.md` | 细化双层架构的查询链路，说明 Query Parser、多入口召回、PG exact / graph expand、Milvus get-by-id、chunk pool、rerank、Agent tools、Langfuse trace 和查询回归测试。 |
| `13. Milvus多集合语义索引拆分优化方案.md` | 说明 Milvus chunk/entity/relation/community 多集合拆分、各集合职责、写入刷新和查询聚合方式。 |
| `14. Graph Index增量构建与多索引分层实施方案.md` | 承接 Graph Index 增量构建设计，说明基础分析图、Projection、层级 Community、Lineage、Dirty Subgraph、Community Change Score、Report、Delta、Finding 和 Milvus 发布的实施方案。 |
| `18. Community Assignment候选上下文账本缓存优化实施方案.md` | 说明 Community Assignment 阶段如何使用 Redis 候选账本稳定 prompt 前缀、降低候选上下文抖动，并记录当前实现边界。 |
| `19. Agent检索与决策上下文实施方案.md` | 说明面向 Agent 的 search / open / expand / refine 检索运行时如何基于 Community、Cognitive Card 和 Evidence/Chunk 构建 Retrieval Decision Context。 |

## 3. 实施规则

1. 每篇技术方案必须包含测试用例和验收标准。
2. 实现中如果和设计方案不一致，必须记录到 `9. 待优化.md`。
3. 因依赖、性能、数据质量、工程成本导致的降级不能静默处理。
4. 事实源以 PostgreSQL Fact Store 为准，Wiki、向量索引和图邻接索引都是可重建派生数据。

## 4. 备份位置

重组前实施方案备份在：

`docs/3. 实施方案/4. 知识图谱.backup-20260512-190000`
