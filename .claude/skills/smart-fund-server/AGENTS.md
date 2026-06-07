# AGENTS.md — smart-fund-server

本文件是当前项目的 Codex / Agent 编码约束。生成、审查或调试本项目代码时必须遵守。

## 完成定义与实现边界

1. **禁止补丁伪装完成。**
   不允许用占位实现、最小版、临时绕过、部分覆盖、mock 行为、硬编码结果或“看起来能跑”的补丁来宣称设计目标已经完成。

2. **实现状态必须如实表达。**
   如果当前代码只完成了目标方案的一部分，必须明确区分：
   - 已完成的能力；
   - 未完成的能力；
   - 当前降级点或技术债；
   - 后续达到完整目标需要补齐的工作。

3. **偏差必须记录。**
   任何实现与设计文档、实施文档、用户明确要求不一致的地方，都必须记录到对应模块的 `docs/3. 实施方案/{模块}/9. 待优化.md`。不能只在对话里口头说明，也不能在最终回复中把降级方案包装成完整交付。

## Milvus / PyMilvus 规则

当代码连接 Milvus、Milvus Lite 或 Zilliz Cloud 时，必须遵守以下规则。

### Client 与连接

1. **必须使用 `MilvusClient`。**
   不要使用旧 ORM API：
   - `connections.connect()`
   - `Collection()`
   - `utility.list_collections()`

   如果遇到旧 ORM 写法，应改造成 `pymilvus.MilvusClient`。

2. **连接方式。**
   - 本地未认证 Milvus：只传 `uri`。
   - Zilliz Cloud 或认证 Milvus：传 `uri` + `token`。

### Schema 与数据写入

3. **字段类型必须使用 `DataType` 枚举。**
   使用 `DataType.FLOAT_VECTOR`、`DataType.VARCHAR`，不要写字符串 `"FLOAT_VECTOR"`。

4. **Schema 设计要前置。**
   Milvus 2.5.x 及以前版本 schema 基本不可变；字段设计错了通常需要 drop/recreate collection。Milvus 2.6+ 虽可新增 nullable 字段，但仍不能修改或删除既有字段。

5. **主键只能是 `INT64` 或 `VARCHAR`，且不能是组合主键。**
   本项目的语义检索 target 应优先使用稳定 `VARCHAR target_id` 作为主键，方便 PG refs 指向 Milvus target。

6. **更新实体使用 `upsert()`。**
   没有 `client.update()`。索引刷新、target 重建、chunk/card 覆盖写入应使用 `upsert()`；只有确认没有主键冲突时才使用 `insert()`。

7. **BM25 / analyzer 必须在 collection 创建时定义。**
   如果需要 Milvus hybrid search 的 BM25 能力，必须在建 collection 时设计好函数和 analyzer，不能后补到既有 collection。

### Index、Load 与 Search

8. **先建 index，再 load，再 search。**
   vector field 必须有 index 后 collection 才能 load；collection 必须 load 后才能 search / query。优先在 `create_collection()` 时同时传入 `schema` 和 `index_params`。

9. **默认从 `AUTOINDEX` 开始。**
   没有明确性能和召回目标前，优先使用 `index_type="AUTOINDEX"`。只有在明确需求下再选 HNSW、DiskANN、IVF_FLAT 等。

10. **Hybrid search 每个 `AnnSearchRequest` 只能包含一个 query vector。**
    不要把多个 query vectors 塞进同一个 sub-request。

11. **一次 `hybrid_search()` 只能选择一个 ranker。**
    不能链式叠加 `WeightedRanker` 和 `RRFRanker`，必须选择其中一种。

### 本项目额外约束

12. **Milvus 是可读检索层。**
    Milvus target payload 必须能直接服务 rerank / Agent，至少包含可读 `text`、`target_id`、`target_type`、`source_id`、`evidence_id`、相关 `chunk_ids` / `node_ids` / `edge_ids` 等元数据。

13. **Milvus 需要同时支持语义搜索和按 ID 精准取回。**
    检索链路允许：
    - `vector search(query_embedding)` 做语义召回；
    - `get/query by target_id or chunk_id` 在 PG graph expand 后精准取回 chunk/card text。

14. **PG 不保存完整 `chunk_text` / `card_text`。**
    PG 只保存 evidence、node、edge、`kg_evidence_chunk_refs`、`kg_edge_evidence_refs` 等事实和指针关系；Milvus 保存完整可读 chunk/card text。

15. **裸 node / edge 不进入 rerank 主链路。**
    PG 命中 node / edge 后，必须通过 refs 转成 `target_id` / `chunk_id`，再从 Milvus 取回可读 target 后进入 merge / rerank / Agent。
