# 知识图谱设计方案

## 1. 阅读说明

本目录只保存知识图谱的设计方案，重点回答“为什么这样设计、业务边界是什么、各功能模块解决什么问题”。具体技术实现、接口、表结构、测试用例和验收步骤放在：

`docs/3. 实施方案/4. 知识图谱/`

当前设计方案按功能模块拆分，不按建设步骤拆分。每篇文档都要求可以独立阅读，但推荐从第 1 篇开始，因为它定义了后续文档都会使用的 Node、Edge、Evidence、Source Record 和 Domain Adapter 等基础概念。

## 2. 文档列表

| 文档 | 解决的问题 |
| --- | --- |
| `1. 知识模型与领域适配设计方案.md` | 知识图谱表达什么、通用层和领域适配器如何分工、金融适配器第一版覆盖什么。 |
| `2. 数据源投影与Source Record设计方案.md` | 业务表 Raw Row 如何变成知识编译统一输入 Source Record。 |
| `3. 知识编译、标准化与写入设计方案.md` | Source Record 如何被编译、标准化、去重并写入可追踪主图事实。 |
| `5. Wiki与消费场景设计方案.md` | Wiki 派生层如何生成，以及投研问答、事件抽取、策略解释和复盘如何消费图谱。 |
| `9. 检索索引架构重构讨论.md` | 作为检索索引架构重构的设计侧文档，说明新增能力后系统应该长什么样、整体架构图、查询链路图、数据形态图，以及 PG 确定性查询、向量语义查询、graph 按需展开、事实归并和 reranker 的核心边界。 |
| `10. 写入侧语义索引材料设计方案.md` | 说明写入侧如何从 PG facts 派生 enriched vector docs、relation preview，并让旧 `kg_retrieval_*` / Wiki / retrieval document 退出核心检索链路。 |
| `11. KG认知辅助层定位与证据优先检索重构讨论.md` | 明确知识图谱认知辅助层应该交付 evidence-backed cognitive package，而不是裸 node / edge。 |
| `12. Milvus语义检索、PG关系展开与叙事图谱设计方案.md` | 收敛最新检索架构决策：Milvus 负责语义召回和按 ID 取回可读 chunk/card，PG 负责事实、关系、原文和不含 chunk text 的证据指针，叙事图谱负责异步 topic cluster / market narrative。 |
| `13. GraphRAG数据编译入库与检索机制调研.md` | 调研 GraphRAG 如何以 TextUnit 为证据中枢构建 entity、relationship、community report 和多入口检索，并明确哪些机制可借鉴、哪些不能照搬。 |
| `14. Graph Index增量构建与多索引分层设计方案.md` | 专门说明 Graph Index 如何分层、如何划分多类索引、如何用 dirty subgraph / delta view / 局部重算降低全局 community 重构成本。 |
| `15. Graph Index社区化重构设计方案.md` | 历史方案：记录基于主题归档构建 Graph Index Community 的早期设计判断。 |
| `16. Community Topic高维信号提取验证方案.md` | 历史方案：记录原文 / Chunk -> Cognitive Card -> Community Assignment 的主题归档路线。 |
| `17. Seed Community Topic与归档Prompt优化方案.md` | 历史方案：记录 Seed Community 和 L0 主题粒度治理。 |
| `18. Community Assignment候选上下文账本缓存优化实施方案.md` | 历史方案：记录 Community Assignment 候选账本和前缀缓存优化。 |
| `20. Community Graph写入性能优化设计方案.md` | 历史方案：记录主题 Community Assignment 的 bucket、缓存、合并和并发控制。 |
| `21. Community Insight高级认知索引设计方案.md` | 历史方案：记录在主题 Community 之上单独生成 Insight 的旧路线。 |
| `22. 关系优先Graph Community重构设计方案.md` | 当前关系图主链路：先提取原子 Card，再通过 Relation Probe 按关系角色召回候选，经关系感知 Summary 筛选和双方原文核验建立 Observed / Inferred Edge，最后从关系图中发现 Graph Community。 |
| `23. 新增新闻驱动的关系知识图谱自动化任务工作流设计方案.md` | 说明新增新闻如何通过 Redis 任务驱动 Card、Relation Probe、跨 Chunk 关系发现、Edge 写入和 Community 刷新。 |
| `24. 平行Graph Community图聚类与跨社区关系设计方案.md` | 定义连通分量不再直接等于 Community；大型关系区域通过图聚类形成平行 Community，小型关系簇继续保留，跨 Community Card Edge 聚合为可追溯的 Community Relation。 |
| `26. 关系图Agent检索工具设计方案.md` | 定义 Card 搜索、Card/Community 独立展开、Card/Edge/Community 独立打开的 Agent 工具边界，以及关系图检索的使用路径和验收口径。 |

## 3. 总体设计结论

知识图谱不是一个只服务单次问答的检索插件，而是系统级知识底座。它的目标是把原始新闻、结构化数据、事件、信号、持仓、复盘结果沉淀为可追踪、可检索、可解释、可复用的事实网络。

系统采用两层结构：

1. **通用知识基础设施**
   - 负责事实、证据、关系、Wiki、索引、检索上下文和质量治理。
   - 不写死金融业务，保证后续可以支持其他领域。

2. **领域适配器**
   - 负责把具体领域的数据解释成知识图谱可理解的实体、关系、证据和规则。
   - 当前第一版领域适配器是金融适配器。

知识图谱高级认知链路以第 22、24 篇为当前设计基线：系统不再维护主题 Community 目录，也不再通过 Community Assignment 把 Card 归档到预先存在的主题中。Cognitive Card 必须先原子化，正式关系必须经过双方原文核验，`kg_graph_communities` 只能从有效关系图中产生。

有效关系图中的连通分量只作为计算边界，不直接等于 Community。大型关系区域通过图聚类形成多个平行 Community；小型关系簇继续保留；跨 Community 的有效 Card Edge 通过派生关系投影表达，不因 Community 拆分而丢失。

## 4. 核心原则

1. **先定义模型，再进入写入链路**
   - 先明确 Node、Edge、Evidence、Source Record 和 Domain Adapter 的含义，再讨论投影、编译、写入、检索和消费。

2. **写入前治理优先**
   - 脏数据不能先进入主图再事后补救。
   - 实体标准化、类型判断、关系合法性和证据约束必须在写入链路中完成。

3. **事实和索引分离**
   - PostgreSQL 中的事实库是唯一事实源。
   - Wiki、向量索引、图邻接索引都是可重建派生结果。

4. **规则和 LLM 协作**
   - 能由结构化数据、规则或已有词表确定的内容，不交给 LLM 猜。
   - LLM 用于非结构化语义抽取、歧义判断、新规则候选和质量巡检。
   - LLM 不能绕过校验直接修改主图。

5. **按功能模块回溯问题**
   - 设计文档和实施文档都按功能模块拆分。
   - 如果实现和设计不一致，或者存在降级方案，必须记录到实施目录的 `9. 待优化.md`。

## 5. 备份位置

重组前的设计方案备份在：

`docs/5. 设计方案/1. 知识图谱.backup-20260512-181244`

最近几次结构调整前的备份在：

`docs/5. 设计方案/1. 知识图谱.backup-20260514-200550`

`docs/5. 设计方案/1. 知识图谱.backup-20260515-012859`
