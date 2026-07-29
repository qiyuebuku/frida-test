抛弃专用向量库，纯PostgreSQL打造企业级RAG系统
做 RAG（检索增强生成）一定要上 Milvus、Pinecone 或者 Weaviate 吗？

如果你正被多套存储系统的数据一致性折磨，被向量库的运维复杂度困扰，或者被额外的成本压得喘不过气——这篇文章可能会改变你的想法。

PostgreSQL + pgvector 的组合，已经足以支撑绝大多数生产级 RAG 场景。

作为一名同时经历过"专用向量库+关系库"双写架构和"纯 PostgreSQL"单库架构的工程师，我可以负责任地说：在 95% 的业务场景下，单 PostgreSQL 方案不仅够用，而且在数据一致性、运维复杂度、成本控制上有着碾压级优势。

今天这篇文章，我将带你走完从零搭建的完整实战流程，提供可直接 Copy 运行的代码，并分享我们在生产环境中沉淀的性能优化经验。


一、为什么是 PostgreSQL？
在深入技术细节前，先聊聊架构选型。很多团队一上来就搞"双写"：PostgreSQL 存元数据，Milvus 存向量。这样做看似专业，实则引入了不必要的复杂度：

数据一致性问题
两边数据如何保证同步？事务如何处理？
运维成本翻倍
需要维护两套集群、两套备份策略、两套监控体系
查询复杂度增加
需要应用层做 Join，网络开销和代码复杂度都上去了
而 PostgreSQL 配合 pgvector 扩展，可以实现：

ACID 事务保护
元数据和向量在同一事务中写入，强一致性
混合查询能力
向量相似度搜索 + JSON 过滤 + 全文检索，一条 SQL 搞定
成熟的运维体系
你现有的 DBA 团队、备份方案、高可用架构全部复用
成本优势
不需要额外采购向量数据库服务
目前 pgvector 已支持高达 16,000 维的向量，HNSW 和 IVFFlat 索引算法性能与专用向量库相比差距在 10% 以内，完全足以支撑百万级甚至千万级向量的实时检索。

二、环境准备与基础配置
首先确保你的 PostgreSQL 版本在 14 以上，pgvector 版本建议 0.5.0+（支持 HNSW 索引）。

-- 安装扩展（需超级用户权限）
CREATE EXTENSION IF NOT EXISTS vector;

-- 查看版本
SELECT * FROM pg_extension WHERE extname = 'vector';
如果你使用 Docker，可以直接拉取带 pgvector 的镜像：

docker run --name pgvector-demo \
  -e POSTGRES_PASSWORD=password \
  -p 5432:5432 \
  -d ankane/pgvector:latest
关键参数调优（postgresql.conf）：

# 共享内存，建议设置为内存的 25%
shared_buffers = 4GB

# 向量计算可能涉及大量数据，适当增加 work_mem
work_mem = 256MB

# 维护工作内存，影响索引创建速度
maintenance_work_mem = 1GB

# 并行度设置，视 CPU 核心数调整
max_parallel_workers_per_gather = 4
max_parallel_maintenance_workers = 4
三、完整实战：从数据到 LLM
接下来，我们实现一个完整的 RAG 流程：文档切分 → Embedding → 存储 → 检索 → Prompt 组装。

3.1 数据建模与建表
设计表结构时，建议将元数据放在 JSONB 字段中，便于灵活扩展，同时利用 GIN 索引加速过滤。

-- 创建文档表
CREATE TABLE knowledge_base (
    id bigserial PRIMARY KEY,
    doc_id varchar(64) NOT NULL,          -- 业务文档ID
    chunk_seq int NOT NULL,               -- 文档内分块序号
    content text NOT NULL,                -- 文本内容
    metadata jsonb DEFAULT '{}',          -- 元数据（作者、分类、时间等）
    embedding vector(1536),               -- OpenAI Ada-002 维度
    created_at timestamp DEFAULT now(),

    -- 唯一约束，避免重复插入
    UNIQUE(doc_id, chunk_seq)
);

-- 为业务查询创建索引
CREATE INDEX idx_kb_doc_id ON knowledge_base(doc_id);
CREATE INDEX idx_kb_metadata ON knowledge_base USING GIN(metadata);

-- 创建 HNSW 向量索引（推荐，构建慢查询快）
CREATE INDEX idx_kb_embedding_hnsw ON knowledge_base 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 备选：IVFFlat 索引（构建快查询慢，适合静态数据）
-- CREATE INDEX idx_kb_embedding_ivf ON knowledge_base 
-- USING ivfflat (embedding vector_cosine_ops)
-- WITH (lists = 100);
索引选择建议：

HNSW
动态数据、对查询速度要求高、内存充足，参数 m 建议 8-24，ef_construction 建议 64-128
IVFFlat
静态数据、批量导入后很少更新、内存紧张，lists 建议设置为数据量的平方根
3.2 Embedding 生成与批量写入
Python 端代码，使用 psycopg2 配合 execute_values 实现高效批量插入：

import openai
import psycopg2
from psycopg2.extras import execute_values
import numpy as np
from typing import List, Dict

# 配置
DB_PARAMS = {
    "dbname": "postgres",
    "user": "postgres",
    "password": "password",
    "host": "localhost",
    "port": "5432"
}

openai.api_key = "your-api-key"

def get_embeddings(texts: List[str], model="text-embedding-ada-002") -> List[List[float]]:
    """批量获取 embedding，OpenAI 支持一次最多 2048 个文本"""
    response = openai.Embedding.create(input=texts, model=model)
    return [item['embedding'] for item in response['data']]

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """简单的文本切分策略，实际生产建议使用更智能的切分"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def bulk_insert_documents(documents: List[Dict], conn):
    """
    批量插入文档
    documents: [{"doc_id": "1", "content": "...", "metadata": {...}}]
    """
    # 准备数据：切分 + 获取 embedding
    records = []
    texts_to_embed = []
    meta_records = []

    for doc in documents:
        chunks = chunk_text(doc['content'])
        for i, chunk in enumerate(chunks):
            texts_to_embed.append(chunk)
            meta_records.append({
                "doc_id": doc['doc_id'],
                "chunk_seq": i,
                "metadata": doc.get('metadata', {}),
                "content": chunk
            })

    # 批量获取 embedding（注意控制 batch size，避免触发 OpenAI 限流）
    batch_size = 100
    all_embeddings = []
    for i in range(0, len(texts_to_embed), batch_size):
        batch = texts_to_embed[i:i+batch_size]
        embs = get_embeddings(batch)
        all_embeddings.extend(embs)

    # 组装数据
    data_to_insert = [
        (m['doc_id'], m['chunk_seq'], m['content'], 
         psycopg2.extras.Json(m['metadata']), 
         emb) 
        for m, emb in zip(meta_records, all_embeddings)
    ]

    # 批量写入 PostgreSQL
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO knowledge_base 
            (doc_id, chunk_seq, content, metadata, embedding) 
            VALUES %s
            ON CONFLICT (doc_id, chunk_seq) 
            DO UPDATE SET 
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                created_at = NOW()
            """,
            data_to_insert,
            template="(%s, %s, %s, %s, %s::vector)",
            page_size=1000
        )
    conn.commit()

# 使用示例
if __name__ == "__main__":
    conn = psycopg2.connect(**DB_PARAMS)

    docs = [
        {
            "doc_id": "doc_001",
            "content": "PostgreSQL 是一个功能强大的开源关系型数据库...",
            "metadata": {"category": "database", "author": "admin"}
        },
        # ... 更多文档
    ]

    bulk_insert_documents(docs, conn)
    conn.close()
关键优化点：

批量 Embedding
OpenAI API 支持一次请求多个文本，比单条请求快 10 倍以上
execute_values
相比单条 INSERT，性能提升可达 50-100 倍
ON CONFLICT
实现 Upsert 语义，避免重复导入时出错
3.3 向量检索与混合查询
这是 RAG 的核心环节。pgvector 支持三种距离度量：

<->
欧几里得距离（L2）
<=>
余弦相似度（Cosine）
<#>
内积（Inner Product）
通常文本相似度使用 Cosine 或 Inner Product。

-- 基础向量检索：查找最相似的 5 个文档块
WITH query_embedding AS (
    SELECT '你的查询文本'::vector(1536) as embedding
    -- 实际使用时这里应该传入已经向量化好的数组
)
SELECT 
    id,
    doc_id,
    content,
    metadata,
    1 - (embedding <=> (SELECT embedding FROM query_embedding)) as similarity
FROM knowledge_base
ORDER BY embedding <=> (SELECT embedding FROM query_embedding)
LIMIT 5;
生产环境必备：混合过滤查询

实际业务中，我们很少做全库向量搜索，通常需要配合过滤条件（如特定分类、时间范围、权限控制）。

-- 混合查询：在特定分类中搜索，且时间在最近一年内
WITH query_embedding AS (
    SELECT ARRAY[0.1, -0.2, ...]::vector(1536) as vec  -- 1536维向量
)
SELECT 
    id,
    content,
    metadata,
    1 - (embedding <=> q.vec) as cosine_similarity
FROM knowledge_base, query_embedding q
WHERE 
    -- JSONB 过滤：指定分类
    metadata @> '{"category": "database"}'
    -- 时间范围过滤
    AND created_at > NOW() - INTERVAL '1 year'
    -- 其他业务过滤...
    AND doc_id IN ('doc_001', 'doc_002', 'doc_003')
ORDER BY embedding <=> q.vec
LIMIT 10;
性能关键：确保过滤条件上有索引（如 metadata 的 GIN 索引），否则可能触发全表扫描，向量索引无法发挥作用。

3.4 与 LLM 集成：完整的 RAG Pipeline
最后，将检索结果组装成 Prompt，调用 LLM 生成回答：

def retrieve_and_generate(query: str, conn) -> str:
    """完整的 RAG 流程"""

    # 1. 向量化查询
    query_embedding = get_embeddings([query])[0]

    # 2. 检索相关文档（使用参数化查询防止 SQL 注入）
    with conn.cursor() as cur:
        cur.execute("""
            SELECT content, metadata, 1 - (embedding <=> %s::vector) as sim
            FROM knowledge_base
            WHERE metadata @> %s::jsonb  -- 可以动态添加过滤条件
            ORDER BY embedding <=> %s::vector
            LIMIT 5
        """, (query_embedding, '{"status": "active"}', query_embedding))

        results = cur.fetchall()

    if not results:
        return "未找到相关文档。"

    # 3. 组装 Context
    contexts = []
    for content, metadata, score in results:
        contexts.append(f"[相关性: {score:.2f}] {content}")

    context_text = "\n\n".join(contexts)

    # 4. 构造 Prompt
    prompt = f"""基于以下参考信息回答问题。如果参考信息不足以回答问题，请明确说明。

参考信息：
{context_text}

用户问题：{query}

请给出详细回答："""

    # 5. 调用 LLM（示例使用 OpenAI）
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一个专业的助手，基于提供的参考信息回答问题。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )

    return response.choices[0].message.content

# 使用
conn = psycopg2.connect(**DB_PARAMS)
answer = retrieve_and_generate("PostgreSQL 有哪些索引类型？", conn)
print(answer)
四、性能优化：从百万到千万级
当数据量达到百万级以上，默认配置可能出现性能瓶颈。以下是经过生产验证的优化策略：

4.1 索引优化策略
HNSW 参数调优：

-- 构建阶段（追求召回率）
CREATE INDEX idx_hnsw ON knowledge_base 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 128);

-- 查询阶段（可动态调整，权衡速度与精度）
SET hnsw.ef_search = 100;  -- 默认 40，越大越慢但越准，建议 100-200
IVFFlat 的 probes 调优：

-- 查询时增加 probes 数量提高召回率（默认 1）
SET ivfflat.probes = 10;
4.2 批量写入优化
对于初始数据导入（如一次性导入百万级文档）：

# 方法 1：使用 COPY 协议（最快）
import io

def copy_insert(conn, records):
    buffer = io.StringIO()
    for record in records:
        # 格式：id|content|metadata|embedding
        line = f"{record['doc_id']}\t{record['content']}\t{record['metadata']}\t{record['embedding']}\n"
        buffer.write(line)
    buffer.seek(0)

    with conn.cursor() as cur:
        cur.copy_from(buffer, 'knowledge_base', 
                     columns=('doc_id', 'content', 'metadata', 'embedding'))
    conn.commit()

# 方法 2：异步批量插入（高并发场景）
import asyncpg
import asyncio

async def async_batch_insert(pool, data_batch):
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO knowledge_base (doc_id, content, metadata, embedding) VALUES ($1, $2, $3, $4::vector)",
            data_batch
        )
4.3 混合查询优化
当向量搜索 + 过滤条件无法有效减少数据量时（如过滤后仍有百万级数据），建议采用预过滤策略：

-- 策略 1：使用 CTE 先过滤，再向量搜索（适合过滤后数据量 < 10万）
WITH filtered_docs AS (
    SELECT id, content, embedding
    FROM knowledge_base
    WHERE metadata @> '{"project_id": "123"}'
    LIMIT 10000  -- 限制最大扫描范围
)
SELECT * FROM filtered_docs
ORDER BY embedding <=> query_vec
LIMIT 5;

-- 策略 2：分区表（按业务维度分区）
CREATE TABLE knowledge_base_partitioned (
    LIKE knowledge_base INCLUDING ALL
) PARTITION BY LIST (metadata->>'project_id');

-- 为每个大项目创建独立分区，查询时自动裁剪分区
4.4 监控与诊断
关键的监控 SQL：

-- 查看索引使用情况
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,  -- 扫描次数
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes
WHERE tablename = 'knowledge_base';

-- 检查慢查询（需开启 log_min_duration_statement）
SELECT query, mean_exec_time, calls 
FROM pg_stat_statements 
WHERE query LIKE '%knowledge_base%'
ORDER BY mean_exec_time DESC
LIMIT 10;
五、生产环境 checklist
将 pgvector 投入生产前，请确认以下事项：

高可用与备份：

向量数据支持流复制和逻辑复制，可正常配置主从
pg_dump 可以正常备份包含 vector 类型的表
建议单独测试大向量字段的备份恢复速度
连接池配置： 向量查询可能耗时较长（几十到几百毫秒），建议：

HikariCP/Pool 大小 = (核心数 * 2) + 有效磁盘数，不要设置过大
配置合理的超时时间（statement_timeout）
安全考虑：

向量字段本身不可读，但建议对 metadata JSONB 中的敏感字段加密存储
使用 Row Level Security (RLS) 实现多租户数据隔离：
-- 多租户示例：用户只能看到自己的数据
ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON knowledge_base
    FOR ALL
    TO app_user
    USING (metadata->>'user_id' = current_setting('app.current_user_id'));
扩展性预案： 当单库无法支撑时：

垂直拆分：将向量表移到独立的高配实例
读写分离：向量检索走从库（注意延迟）
分库分表：按业务维度拆分（如按项目 ID 分 16 个库）
六、总结

通过本文的实战演示，我们验证了 PostgreSQL + pgvector 完全可以作为生产级 RAG 系统的核心存储，无需引入额外的向量数据库。

这套方案的核心优势在于：

简化架构
一套系统解决事务、检索、分析，消除数据一致性问题
成本可控
复用现有 DBA 能力和监控体系，无需学习新组件
查询灵活
SQL 的表达能力远超专用向量库的 DSL，复杂业务逻辑一条语句搞定
生态丰富
可以直接使用 TimescaleDB（时序）、PostGIS（地理）等扩展，构建多模态 RAG
当然，专用向量数据库在特定极端场景（如十亿级向量、超大规模并发）仍有优势。但在大多数企业级应用场景下，"PostgreSQL 一把梭"不仅可行，而且往往是更优雅的选择。

希望这篇文章能帮你少走弯路。如果你已经在生产环境使用了 pgvector，欢迎在评论区分享你的实战经验。