# Knowledge Compiler / KG-RAG 参考源码索引

> 本目录只作为只读参考源码库使用，不直接作为业务依赖。后续开发 `Knowledge Compiler + KG-RAG Runtime + Knowledge Wiki + Hybrid Retrieval` 时，可以从这里快速查看成熟项目的代码结构、接口设计和工程取舍。

## 仓库清单

| 目录 | 来源 | 参考价值 |
|---|---|---|
| `arag/` | https://github.com/Ayanami0730/arag | A-RAG 官方实现，重点看 `keyword_search` / `semantic_search` / `chunk_read` 这类层级检索接口如何暴露给 agent |
| `LightRAG/` | https://github.com/HKUDS/LightRAG | LightRAG 官方实现，重点看增量图索引、实体/关系抽取、低层/高层检索和 query mode |
| `qmd/` | https://github.com/qntx-labs/qmd | 本地 Markdown 混合检索引擎，重点看 BM25 + vector + MCP 的轻量实现方式 |
| `karpathy-llm-wiki-gist/` | https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f | llm-wiki 原始设计说明，作为 Knowledge Wiki 的思想源头 |
| `llmwiki-test/` | https://github.com/Nipi64310/llmwiki-test | 参考文章中的 llm-wiki 实验仓库，已使用 sparse checkout 跳过超长文件名 raw 数据；重点看 `AGENTS.md`、`workflow/`、`wiki/` |
| `second-brain-skill/` | https://github.com/ChavesLiu/second-brain-skill | llm-wiki skill 化实现，重点看如何把 wiki 维护流程封装成可复用 skill |
| `llmwiki/` | https://github.com/lucasastorian/llmwiki | Karpathy LLM Wiki 的较完整开源实现，重点看 API、MCP、文档上传和 wiki 写入流程 |
| `llm-wiki-compiler/` | https://github.com/atomicmemory/llm-wiki-compiler | 面向 llm-wiki 的 compiler 化实现，重点看“知识编译器”如何组织 CLI、schema 和 wiki 生成 |

## 本项目如何使用这些参考

| 我们要实现的模块 | 优先参考 |
|---|---|
| `Knowledge Compiler Core` | `llm-wiki-compiler/`、`llmwiki-test/workflow/`、`LightRAG/` |
| `Financial KG Adapter` | `LightRAG/` 的实体/关系抽取流程；具体金融 ontology 必须自研 |
| `KG-RAG Runtime` | `arag/`、`qmd/`、`LightRAG/` |
| `Knowledge Wiki` | `karpathy-llm-wiki-gist/`、`llmwiki-test/`、`llmwiki/`、`second-brain-skill/` |
| `Hybrid Retrieval` | `qmd/`、`arag/`、`LightRAG/` |
| agent / MCP 导航 | `qmd/`、`second-brain-skill/`、`graphify/` |

## 边界

- 不把任何一个参考仓库直接作为核心框架。
- 不照搬它们的数据模型；本项目事实源仍是 PostgreSQL `kg_*` 表。
- 不让 LLM 输出直接进入主图；必须经过 evidence、confidence、version、ontology 校验。
- 参考仓库可以被删除重拉，业务代码不能依赖其未封装路径。

## 拉取记录

当前为浅克隆参考代码：

| 目录 | commit |
|---|---|
| `arag` | `a44de6b` |
| `LightRAG` | `ec7b86a` |
| `qmd` | `de818e3` |
| `karpathy-llm-wiki-gist` | `ac46de1` |
| `llmwiki-test` | `19efdba` |
| `second-brain-skill` | `0a06755` |
| `llmwiki` | `8af419e` |
| `llm-wiki-compiler` | `0a19ddc` |
| `graphify` | `7a0a5ac` |
