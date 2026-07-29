A-RAG 解读：能做好混合检索策略的RAG，才是真 Agentic RAG
图片
市面上的 RAG 系统，不管叫什么名字，本质上只有两种做法：
第一种，一次性检索。把用户的 query 向量化，从语料库里捞出 Top-K 个文档片段，拼成一个大 prompt 塞给模型。GraphRAG、HippoRAG、LightRAG 都属于这一类——区别只是检索前怎么组织索引（知识图谱、层级树、还是线性图），但检索本身是一锤子买卖，模型没有第二次机会。
第二种，预定义工作流。人提前写好一套流程——先检索、再判断够不够、不够就改写 query 再检索——模型按步骤执行。IRCoT、FLARE、Self-RAG、MA-RAG 都是这个路子。看起来是多轮的，但每一步干什么、什么顺序，都是人定死的，模型只是流水线上的工人。
这两种做法有一个共同问题：模型不参与检索决策。用什么方式检索、检索几次、什么时候该停，全是人预先规定好的。模型的推理能力再强，在检索这个环节上也使不上劲。
针对这个困境，前不久中科大团队提出了一套全新的A-RAG框架，通过把检索的决策权交还给模型，RAG可以更聪明的决定应该搜怎么，怎么搜。
接下来，本文将深度解读A-RAG 是什么，以及如何把它与Milvus的混合检索能力相结合。
01
A-RAG 是什么
A-RAG 论文中，作者定义真正的 Agentic RAG 需要同时满足三个条件：
自主策略选择（Autonomous Strategy）
迭代执行（Iterative Execution）
交错式工具调用（Interleaved Tool Use）
现有方法最多满足其中一两个，A-RAG 是唯一三个全满足的。
图片
具体怎么做？A-RAG 给 Agent 暴露三个不同粒度的检索接口：
keyword_search：关键词精确匹配。不建倒排索引，查询时直接对语料做文本匹配，返回命中的句子片段和所在 chunk 的 ID。适合查专有名词、型号、人名这类精确实体。
semantic_search：语义向量检索。把 query 编码成向量，和预先计算好的句子级 embedding 做余弦相似度匹配，返回语义最相关的句子片段。适合理解模糊的、自然语言描述的问题。
chunk_read：读取完整文档块。前两个工具只返回片段摘要，Agent 觉得某个 chunk 值得深入看，就调这个工具读全文。
没有预定义流程，没有固定顺序。Agent 自己决定什么时候用哪个工具，用几次，什么时候停，什么时候直接给答案。这三个工具覆盖了从关键词级、句子级到文档块级的三层信息粒度，论文把它叫做层级检索接口（Hierarchical Retrieval Interfaces）——Agent 可以先粗筛再精读，也可以直接精确命中，完全取决于问题本身的特征。
02 
实验结果说明了什么
在 HotpotQA、2WikiMultiHopQA、MuSiQue 等多跳问答基准上，A-RAG 全面超过 GraphRAG、HippoRAG2 和各类 Workflow RAG 方法。在 MuSiQue 这类需要跨段落多步跳转的难题上，A-RAG 对最优基线的领先幅度超过 10 个百分点。
但分数不是重点，重点是 Agent 的行为。论文做了消融实验：单独去掉 keyword_search，准确率明显下滑；单独去掉 semantic_search，下滑幅度更大。两个工具都在起作用，但分工不同——Agent 碰到精确实体时会主动选 keyword_search，碰到模糊描述时走 semantic_search。没人教它这么做，是模型自己根据问题特征选的。
真正值得关注的是上下文效率。只给 Agent 一个 embedding 检索工具（A-RAG Naive），它平均要消耗 56,360 个 token 才能回答 MuSiQue 的问题；给齐三个工具（A-RAG Full），降到 5,663 个 token，准确率反而更高。
工具越丰富，Agent 检索得越少越准。不是因为它更懒，而是因为它能直接用对的方式找到对的东西，不再需要靠反复撒网来弥补单一工具的盲区。
但A-RAG 的代价是显性的：每次查询，Agent 都要先推理一轮该用哪个工具，这个决策本身在消耗 token 和响应时间。如果检索融合能在数据库层完成，Agent 就能把全部算力用在问题推理上，而不是工具选择上。
03
Milvus 2.6 把检索决策做进了数据库
A-RAG 给 Agent 配备了两个检索工具，每次查询都需要运行时决策。Milvus 2.6 的 Full-Text Search 把这个决策从运行时移到了写入时。
具体做法是：在 Collection 里定义一个开启了enable_analyzer=True的文本字段，同时挂一个 BM25 Function——Milvus 在写入文档时自动分词、构建关键词权重，输出成一个SPARSE_FLOAT_VECTOR字段存进去。这个稀疏向量字段始终和稠密向量字段并排存在，不需要 Agent 在推理时决定“要不要走关键词这条路”，两条路在数据层面从写入起就都准备好了。
图片
A-RAG 的理论设计和 Milvus 2.6 的工程决策，在结构上是同构的：
A-RAG（运行时决策）
Milvus 2.6（写入时构建）
keyword_search：精确词汇匹配
SPARSE_FLOAT_VECTOR + BM25 Function：自动构建关键词稀疏索引
semantic_search：向量相似度检索
FLOAT_VECTOR：稠密向量语义检索
Agent 每次推理决定走哪条路
hybrid_search：两路并发，RRF 自动融合
决策过程消耗 Agent token
数据库层透明完成，零决策成本
这个对应关系说明了一件事：A-RAG 在理论层面证明了混合检索的必要性，Milvus 2.6 把这个必要性变成了一个字段类型。你不再需要维护两套独立的检索系统，也不需要在 Agent 的 prompt 里教它什么时候该用哪个工具——写入时定义好 schema，查询时一个hybrid_search接口把两条路都走完，结果融合好再返回。
这也是第 04 节代码里enable_analyzer=True和SPARSE_FLOAT_VECTOR两行定义的实际含义：前者告诉 Milvus“这个文本字段需要分词”，后者告诉 Milvus“把分词结果转成 BM25 稀疏向量存进来”。查询时你只需要提交原始文本，Milvus 把向量化这一步也替你做了。
04
怎么落地
Schema 定义
核心就一件事：建 Collection 时同时定义稠密向量、稀疏向量两个字段，并挂上 BM25 Function。

这里有一个容易忽略的细节——enable_analyzer=True只是告诉 Milvus 这个文本字段需要分词，真正把分词结果转成 BM25 稀疏向量的，是schema.add_function()这一步。少了这一步，sparse_vector字段在写入时永远是空的，关键词检索会静默失败，不报错，只是什么都查不到。
写入数据时，sparse_vector字段无需手动提供，Milvus 在写入时自动完成 text →分词 → BM25 权重 → 稀疏向量的完整链路。
from pymilvus import MilvusClient, DataType, Function, FunctionType
import numpy as np
import time
client = MilvusClient(uri="http://localhost:19530")
# 若 Collection 已存在，先清除，方便重复运行
if client.has_collection("arag_docs"):
    client.drop_collection("arag_docs")
# ── 1. Schema 定义 ──────────────────────────────────────────
schema = client.create_schema()
schema.add_field("id",DataType.INT64,is_primary=True, auto_id=True)
schema.add_field("text",        DataType.VARCHAR,         max_length=2000, enable_analyzer=True)
schema.add_field("dense_vector",DataType.FLOAT_VECTOR,    dim=768)
schema.add_field("sparse_vector",DataType.SPARSE_FLOAT_VECTOR)   # BM25 输出字段
schema.add_field("user_id",     DataType.VARCHAR,         max_length=64)
schema.add_field("create_time", DataType.INT64)
# ── 2. BM25 Function（核心：text → sparse_vector 的自动映射）──
bm25_function = Function(
    name="bm25",
    function_type=FunctionType.BM25,
    input_field_names=["text"],          # 从 text 字段读原文
    output_field_names=["sparse_vector"] # 自动写入稀疏向量字段
)
schema.add_function(bm25_function)
# ── 3. 索引定义 ───────────────────────────────────────────────
index_params = client.prepare_index_params()
index_params.add_index(
    field_name="dense_vector",
    index_type="AUTOINDEX",
    metric_type="COSINE"
)
index_params.add_index(
    field_name="sparse_vector",
    index_type="SPARSE_INVERTED_INDEX",
    metric_type="BM25"          # ⚠️ 必须是 BM25，不能写IP
)
# ── 4. 创建 Collection ────────────────────────────────────────
client.create_collection(
    collection_name="arag_docs",
    schema=schema,
    index_params=index_params
)
# ── 5. 写入测试数据 ──────────────────────────────────────────
# 生产环境中 dense_vector 替换为真实 embedding（如 sentence-transformers 输出）
# sparse_vector 字段无需手动提供，BM25 Function 在写入时自动生成
data = [
    {
        "text": "Milvus 是一个高性能云原生向量数据库，支持十亿级向量的毫秒级检索。",
        "dense_vector": np.random.rand(768).tolist(),
        "user_id": "u_001",
        "create_time": 1700000100
    },
    {
        "text": "A-RAG 通过层级检索接口，让 LLM 在keyword_search 和 semantic_search 之间自主决策。",
        "dense_vector": np.random.rand(768).tolist(),
        "user_id": "u_001",
        "create_time": 1700001000
    },
    {
        "text": "BM25 是一种经典的关键词检索算法，擅长精确匹配型号、版本号等专有名词。",
        "dense_vector": np.random.rand(768).tolist(),
        "user_id": "u_002",
        "create_time": 1700002000
    },
    {
        "text": "RRF（Reciprocal Rank Fusion）将多路检索结果按排名加权合并，无需手动调权重。",
        "dense_vector": np.random.rand(768).tolist(),
        "user_id": "u_002",
        "create_time": 1700003000
    },
]
client.insert(collection_name="arag_docs", data=data)
#等待数据刷入（生产环境可改为 flush + wait_for_loading）
time.sleep(2)
print("✅ Collection 创建完成，数据写入就绪。")
Hybrid Search 无 Filter
两路检索同时发出，RRF 自动融合，一个接口搞定。

sparse_req的data传的是原始文本字符串，不是向量——Milvus 内部会调用写入时定义的同一套 BM25 Function 完成查询向量化。这和dense_req需要你自己传 embedding 是不同的：稠密向量这边，模型的选择（768 维还是 1536 维）、归一化方式都由你控制；稀疏向量这边，Milvus 全权接管。
from pymilvus import MilvusClient, AnnSearchRequest, RRFRanker
import numpy as np
client = MilvusClient(uri="http://localhost:19530")
# 查询文本（生产环境中query_embedding 替换为真实 embedding 结果）
query_text      = "向量数据库如何进行关键词检索"
query_embedding = np.random.rand(768).tolist()
# ── 语义检索：理解查询意图 ────────────────────────────────────
dense_req = AnnSearchRequest(
    data=[query_embedding],
    anns_field="dense_vector",
    param={"metric_type": "COSINE"},
    limit=10
)
# ── 关键词检索：精确匹配专有名词、版本号、型号 ─────────────────
# data 传原始文本字符串，Milvus 内部通过 BM25 Function 自动向量化
sparse_req = AnnSearchRequest(
    data=[query_text],
    anns_field="sparse_vector",
    param={"metric_type": "BM25"},
    limit=10
)
# ── 两路并发，RRF 融合排序后返回 Top 5 ────────────────────────
results = client.hybrid_search(
    collection_name="arag_docs",
    reqs=[dense_req, sparse_req],
    ranker=RRFRanker(k=60),# k=60 是经验值，大多数场景无需调整
    limit=5,
    output_fields=["text", "user_id", "create_time"]
)
print(f"查询：{query_text}\n{'─'*50}")
for hit in results[0]:
    print(f"Score : {hit['distance']:.4f}")
    print(f"Text  : {hit['entity']['text']}")
    print(f"User  : {hit['entity']['user_id']}|  Time: {hit['entity']['create_time']}")
    print()
Hybrid Search 带 Filter
生产环境里通常还需要元数据过滤——比如多租户场景下只检索当前用户的文档，或者只检索特定时间范围内的内容。

加一个filter参数，不影响两路向量检索的并发执行。Milvus 的执行顺序是：先做向量检索召回候选集，再对候选集做标量过滤——不是全量扫描，所以加 filter 不会拖慢检索性能。
from pymilvus import MilvusClient, AnnSearchRequest, RRFRanker
import numpy as np
client = MilvusClient(uri="http://localhost:19530")
query_text      = "向量数据库如何进行关键词检索"
query_embedding = np.random.rand(768).tolist()
dense_req = AnnSearchRequest(
    data=[query_embedding],
    anns_field="dense_vector",
    param={"metric_type": "COSINE"},
    limit=10
)
sparse_req = AnnSearchRequest(
    data=[query_text],
    anns_field="sparse_vector",
    param={"metric_type": "BM25"},
    limit=10
)
# ── 只检索 u_001 用户、指定时间之后的文档 ──────────────────────
results = client.hybrid_search(
    collection_name="arag_docs",
    reqs=[dense_req, sparse_req],
    ranker=RRFRanker(k=60),
    filter='user_id == "u_001" and create_time > 1700000000',
    limit=5,
    output_fields=["text", "user_id", "create_time"]
)
print(f"查询（已过滤 user_id=u_001）：{query_text}\n{'─'*50}")
for hit in results[0]:
    print(f"Score : {hit['distance']:.4f}")
    print(f"Text  : {hit['entity']['text']}")
    print(f"User  : {hit['entity']['user_id']}  |  Time: {hit['entity']['create_time']}")
    print()
这三段代码，覆盖了从写入到查询的完整链路——BM25 Function 在写入时自动构建稀疏向量，hybrid_search 在查询时两路并发融合。Agent 不需要做任何检索决策，Milvus 在底层替它做完了。
05
尾声
语义检索理解意思、关键词匹配精确命中，各有各的死角——A-RAG 的答案是让模型自己决定走哪条，Milvus 2.6 的答案是两条路同时跑、数据库层融好再返回。
前者每次查询多花一轮 Agent 推理，后者在写入时就把准备工作做完了。
解法不同，指向同一件事：单走一条路不够用。
论文地址：https://arxiv.org/pdf/2602.03442
作者介绍

图片
Zilliz黄金写手：尹珉

阅读推荐
官宣：Zilliz Cloud&Milvus发布CLI工具与官方Skill，让AI Agent成为专业VDB运维与开发助手
Harness的Managed Agents好在哪里？如何解决它的经验复用短板
黄仁勋GTC演讲上，Milvus为什么能够站稳非结构化数据处理C位
2026年，Embedding要怎么选？（实测Gemini 、jina、Qwen、BGE、OpenAI十大模型）
用RAG的思路做agent知识管理，为什么跑不通
图片
图片


留言 9
写留言

抓走十只小熊猫
上海
3天前
学不过来了

Zilliz
作者
3天前
2
嘴上说着学不过来了，手却诚实的打开了

Zilliz
作者
3天前
2
别否认了，你就是想学，爱学

季
北京
2天前
hybrid_search 有 filter?

李玉恒
北京
3天前
Milvus 的执行顺序是：先做向量检索召回候选集，再对候选集做标量过滤——不是全量扫描。
这点有点疑惑，不应该是先filter出标量筛选条件，然后再向量检索吗？

Alden
天津
2天前
client.hybrid_search 里设置filter的话，是先检索后过滤

Dreamt
重庆
3天前
确实好用，现在做的agentic rag延迟大多在API请求和向量嵌入上了

code2t
上海
3天前
不愧是黄金写手

Zilliz
作者
3天前
核动力写手
已无更多数据

暂无评论