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
| `9. 待优化.md` | 实施偏差、降级、妥协和后续整改记录。 |

## 3. 实施规则

1. 每篇技术方案必须包含测试用例和验收标准。
2. 实现中如果和设计方案不一致，必须记录到 `9. 待优化.md`。
3. 因依赖、性能、数据质量、工程成本导致的降级不能静默处理。
4. 事实源以 PostgreSQL Fact Store 为准，Wiki、向量索引和图邻接索引都是可重建派生数据。

## 4. 备份位置

重组前实施方案备份在：

`docs/3. 实施方案/4. 知识图谱.backup-20260512-190000`
