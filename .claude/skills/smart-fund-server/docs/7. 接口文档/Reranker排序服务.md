# Reranker 排序服务接口文档

## 1. 概述

Reranker 排序服务为检索候选重排和 LLM Judge 预筛提供语义相关性排序能力。基于 jina-reranker-v3（Listwise 架构），一次看到全部候选做相对排序，比 pointwise 模型（如 BGE）能更好地区分语义相近但角色不同的候选。

**服务地址：** `http://119.23.227.187:8860`

## 2. 接口定义

### 2.1 健康检查

```
GET /health
```

**响应示例：**

```json
{
  "status": "ok",
  "model": "jina-reranker-v3"
}
```

### 2.2 重排序

```
POST /v1/rerank
```

对一组候选文档按与 query 的语义相关性排序，返回按分数降序排列的结果。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 查询文本 |
| `documents` | string[] | 是 | 候选文档列表，长度上限 100 |
| `top_n` | int | 否 | 返回前 N 条结果，默认返回全部 |

**请求示例：**

```json
{
  "query": "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
  "documents": [
    "A股并购重组市场呈现三方面新变化 mentions 半导体",
    "A股并购重组市场呈现三方面新变化 mentions Wind资讯",
    "特斯拉CFO豪掷250亿美元押注AI"
  ],
  "top_n": 2
}
```

**响应体：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | string | 模型标识，固定 `"jina-reranker-v3"` |
| `results` | array | 按分数降序排列的结果列表 |
| `results[].index` | int | 原始 documents 数组中的下标 |
| `results[].relevance_score` | float | 相关性分数，范围约 0.0 ~ 0.5，分数越高越相关 |
| `results[].document` | string | 原始文档内容 |
| `usage.total_documents` | int | 输入文档总数 |
| `usage.latency_ms` | float | 推理耗时（毫秒） |

**响应示例：**

```json
{
  "model": "jina-reranker-v3",
  "results": [
    {
      "index": 0,
      "relevance_score": 0.3739,
      "document": "A股并购重组市场呈现三方面新变化 mentions 半导体"
    },
    {
      "index": 1,
      "relevance_score": 0.2549,
      "document": "A股并购重组市场呈现三方面新变化 mentions Wind资讯"
    }
  ],
  "usage": {
    "total_documents": 3,
    "latency_ms": 45.2
  }
}
```

### 2.3 逐条打分

```
POST /v1/score
```

对每个文档独立打分，返回与原始输入顺序对应的分数数组。用于需要保留原始顺序、只取分数的场景。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 查询文本 |
| `documents` | string[] | 是 | 候选文档列表 |

**响应体：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `scores` | float[] | 与 documents 等长的分数数组，下标一一对应 |
| `model` | string | 模型标识 |
| `usage.total_documents` | int | 输入文档总数 |
| `usage.latency_ms` | float | 推理耗时（毫秒） |

**响应示例：**

```json
{
  "scores": [0.3739, 0.2549, 0.0021],
  "model": "jina-reranker-v3",
  "usage": {
    "total_documents": 3,
    "latency_ms": 45.2
  }
}
```

## 3. 分数特征

| 特征 | 说明 |
|------|------|
| 范围 | 约 0.0 ~ 0.5，不会接近 1.0 |
| 区分度 | 相邻排名之间通常有 0.01 ~ 0.05 的分差 |
| 不可跨 query 比较 | 分数是同一 query 内的相对排序，不同 query 的分数绝对值不可比 |
| 阈值建议 | 不建议使用绝对阈值过滤；如需过滤，建议取 top_n 或使用分数百分位 |

## 4. 性能参考

| 候选数 | top_n | 延迟 |
|--------|-------|------|
| 3 | 全部 | ~45ms |
| 60 | 30 | ~1,000ms（首次 ~2s 热身） |
| 60 | 全部 | ~1,000ms |
