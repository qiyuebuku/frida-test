Anthropic RAG 技术突破：让 AI 检索失败率直降 67%
Anthropic 在 2024 年 9 月提出的 Contextual Retrieval 技术，通过在文档分块前添加上下文信息，将 RAG 系统的检索失败率从 5.7% 降至 1.9%。结合 prompt caching，每百万 tokens 的处理成本仅需 $1.02。



封面：Contextual Retrieval 技术让检索准确率提升 67%
封面：Contextual Retrieval 技术让检索准确率提升 67%



来源链接：Anthropic 官方博客 - Contextual Retrieval

如果你正在用 RAG（检索增强生成）构建 AI 应用，一定遇到过这个头疼问题：明明知识库里有相关信息，AI 却怎么都找不到。

Anthropic 在去年 9 月发布的 Contextual Retrieval 技术，正是为了解决这个痛点。通过一个简单但巧妙的预处理步骤，他们把检索失败率直接砍掉了一半以上。

传统 RAG 的致命缺陷：上下文丢失
RAG 的工作原理很直接：把长文档切成小块（chunks），转成向量嵌入，查询时找到最相关的几块，塞进 prompt 给模型生成回答。

但这里有个隐藏的陷阱。

假设你的知识库里有一堆美股财报，用户问："ACME 公司 2023 年 Q2 的营收增长是多少？"

某个 chunk 可能写着："公司营收相比上一季度增长了 3%。"

问题来了——这个 chunk 里没说是哪家公司、哪个季度。单独拿出来，它就是一段"失忆"的文本。向量数据库很难把它和用户查询匹配上，检索就失败了。

这就是 Anthropic 所说的"上下文困境"（context conundrum）：文档分块破坏了上下文，导致相关信息变得不可检索。



Figure 1: 传统 RAG 系统架构 - 结合 Embeddings 和 BM25 进行检索
Figure 1: 传统 RAG 系统架构 - 结合 Embeddings 和 BM25 进行检索





Figure 2: 传统 RAG vs Contextual Retrieval - 上下文保留带来 49% 性能提升
Figure 2: 传统 RAG vs Contextual Retrieval - 上下文保留带来 49% 性能提升



Contextual Retrieval：让每个 chunk 自带身份证
Contextual Retrieval 的解法非常直接：在把 chunk 存入向量数据库之前，先用 LLM 给它生成一段"身份说明"。

还是刚才那个例子，原始 chunk 是：

图片
经过 contextualization 后变成：

图片
看到区别了吗？补充的上下文明确标注了公司名、时间、基准数据。现在这个 chunk 就算单独拿出来，也能被正确检索到。

Anthropic 用一个简洁的 prompt 让 Claude 3 Haiku 自动生成这些上下文（通常 50-100 tokens）：

图片
整个预处理流程如下：



Figure 3: Contextual Retrieval 预处理流程
Figure 3: Contextual Retrieval 预处理流程



两个维度的上下文：Embeddings + BM25
Contextual Retrieval 其实包含两个子技术：

1. Contextual Embeddings 在生成向量嵌入前，先给 chunk 加上下文。这提升了语义检索的准确性。

2. Contextual BM25 同样的上下文也用在 BM25 索引上。BM25 是一种传统的关键词匹配算法，特别擅长处理精确匹配查询（比如"错误代码 TS-999"这种）。

为什么要同时用两种检索方式？因为它们各有擅长：



• 语义嵌入：理解"汽车"和"车辆"是同一个意思

• BM25：精确匹配"TS-999"这种特定标识符



结合两者，再加上 rank fusion 去重，检索覆盖面更全。

性能提升：从理论到数据
Anthropic 在多个领域（代码库、小说、ArXiv 论文、科学文献）做了测试，评估指标是 1 - recall@20，也就是"在前 20 个检索结果中找不到相关文档的失败率"。

基准：传统 RAG 的失败率是 5.7%

改进效果：



• 只用 Contextual Embeddings：失败率降到 3.7%（↓ 35%）

• Contextual Embeddings + Contextual BM25：失败率降到 2.9%（↓ 49%）

• 再加上 Reranking（二次排序）：失败率降到 1.9%（↓ 67%）





Figure 4: 性能对比 - Contextual Retrieval 降低检索失败率 49%
Figure 4: 性能对比 - Contextual Retrieval 降低检索失败率 49%



Reranking 是检索后的额外过滤步骤：先用向量检索 + BM25 拿到 top-150，再用专门的 reranker 模型（Anthropic 用的是 Cohere Reranker）给这 150 个 chunks 重新打分，挑出最相关的 20 个。



Figure 5: Contextual Retrieval + Reranking 完整流程
Figure 5: Contextual Retrieval + Reranking 完整流程



这一步带来了显著提升，但也增加了一点延迟和成本。是否使用 reranking，需要根据具体场景权衡。

成本控制：Prompt Caching 的威力
给每个 chunk 生成上下文，意味着要把整个文档反复喂给 LLM。这听起来很贵？

Anthropic 的 prompt caching 功能解决了这个问题。你只需要把文档加载到缓存一次，后续处理所有 chunks 时直接引用缓存内容，不用重复传输。

按照他们的测算（800 tokens/chunk，8k tokens/document，100 tokens 上下文），每百万 document tokens 的处理成本是 $1.02。

对比一下：如果你的知识库有 1000 份 8k tokens 的文档（相当于 2000 页 A4 纸），一次性 contextualization 的成本只要 $8.16。这笔投资换来的是持续的检索准确率提升，非常划算。

实施要点：不只是套公式
Anthropic 提供了开箱即用的 cookbook，但要在生产环境用好 Contextual Retrieval，有几个细节值得注意：

1. 分块策略很重要 Chunk 大小、边界、重叠度都会影响检索效果。不要用默认配置一刀切，根据你的文档类型调优。

2. 选对 embedding 模型 Anthropic 测试发现 Gemini 和 Voyage 的 embeddings 效果最好。虽然 Contextual Retrieval 对所有模型都有提升，但好的 embedding 是基础。

3. 定制 contextualization prompt 通用 prompt 已经不错，但如果你的领域有特殊术语（比如医学、法律），在 prompt 里加上术语表，能进一步提升准确率。

4. Top-K 的权衡 Anthropic 发现传递 top-20 chunks 比 top-10 或 top-5 效果更好。但也不是越多越好——太多信息会分散模型注意力。

5. 一定要跑 evals 不同数据集、不同查询类型，表现可能差异很大。上线前用真实查询测试，找到最优配置。

社区反响：褒贬不一
Contextual Retrieval 发布后，AI 圈里反响热烈。不少开发者称赞它"简单有效""立竿见影"。

但也有批评声音。Almond AI 发文质疑，认为 Anthropic 夸大了效果，暗示这可能是为推广自家 prompt caching 服务的营销策略。他们认为，虽然技术本身有价值，但 67% 的提升数字可能不够有说服力。

客观来说，Contextual Retrieval 确实不是什么革命性的新想法——用 LLM 给文档片段加上下文，很多人都想到过。Anthropic 的贡献在于：



1. 系统化地验证了这个方法的有效性

2. 提供了开箱即用的实现方案

3. 通过 prompt caching 让成本降到了可接受范围



对于正在做 RAG 应用的开发者来说，这套方案值得一试。毕竟检索准确率提升几十个百分点，对用户体验的改善是实实在在的。

写在最后
RAG 技术还在快速演进。Contextual Retrieval 只是其中一环，但它抓住了一个关键痛点：上下文丢失。

如果你的 RAG 应用经常出现"明明有答案却检索不到"的情况，不妨试试这个方法。

成本不高（$1.02/百万 tokens），实施不复杂（一个 prompt + 预处理流程），但带来的检索准确率提升可能超出预期。

更重要的是，这种思路可以延伸：除了给 chunk 加上下文，还能不能在其他环节做类似优化？比如查询重写、多跳推理、动态 chunk 大小调整……

RAG 的进化才刚刚开始。

相关链接
Contextual Retrieval in AI Systems - Anthropic Anthropic Prompt Caching Documentation Contextual Retrieval Cookbook




暂无评论