# AI 事件抽取系统 — 整体架构方案

> 版本：v10（planner API gateway + 每批独立 one-shot + graphify adapter 层）
> 定位：本文档是事件抽取子系统的"宪法"。规定数据流、模块边界、接口契约、LLM 调用方式、graphify 集成方式。
>
> **v10 相对 v9 的关键改动**（基于 Opus 4.7 架构评审）：
> 1. **graphify 降级为无状态计算层**：不再直接 import graphify，通过 `GraphifyAdapter` 封装。业务代码零依赖 graphify API。
> 2. **新增时间衰减加权器**：边权重按半衰期分档衰减（宏观60天/行业30天/公司14天/传闻7天），在 build 和 cluster 之间注入。
> 3. **新增事件生命周期状态机**：`lifecycle_state` 字段（dormant/emerging/confirmed/peaking/fading），由自研服务维护。
> 4. **新增语义虚拟边注入器**：embedding 相似度 >0.85 的节点对补虚拟边，补足 Leiden 纯拓扑聚类盲区。
> 5. **主线血缘追踪**：`ft_event_communities` 加 `lineage_id` + `cluster_run_id`，跨时间追踪同一主线的演化。
> 6. **file_type 做兜底**：LLM 不感知 file_type，adapter 层自动补 `'document'`，避免语义无关字段干扰提取质量。
>
> **v9 遗留（继承不变）**：
> 1. LLM 调用走 planner API `/chat` 端点
> 2. 每批独立（planner 服务端实现）
> 3. 尽力而为、高峰降级 batch size

---

## 1. 需求分析

### 1.1 核心需求

事件抽取系统是交易决策的"大脑前置层"，三项职责：

1. **结构化去噪**：把 ft_news / ft_sentiment 的原始文本流过滤成高质量的结构化事件，淘汰纯播报、标题党、重复信息。
2. **关系织网**：把孤立事件织成关系图（因果 / 时序 / 主题 / 冲突），再通过 Leiden 社区发现聚合成事件流（"市场主线"）。
3. **AI 能力统一入口**：承接政策方向判断（待优化 1.4）、情感极性（待优化 2.2）、主题标注（2.6 / 3.4 / 3.5）等下游需求。

### 1.2 边界与约束

| 约束 | 状态 |
|------|------|
| 月预算 | 移除（Coding Plan 订阅制） |
| 单条延迟 | 移除（可接受 15-30 min 延迟） |
| DDL 向后兼容 | 移除（全新项目） |
| 幻觉控制 | certainty 字段 + 下游加权 |
| **LLM 调用方式** | **HTTP → planner API 的 `/chat` 端点** |
| 图能力 | `GraphifyAdapter` 封装 + 自研时间衰减/语义边/生命周期 |
| **会话策略** | **每批独立**（planner 服务端实现，jettask 无感知） |
| **处理策略** | **尽力而为**（处理不完留到下个 tick） |

### 1.3 吞吐量评估

| 指标 | 计算值 | 说明 |
|------|--------|------|
| 单批处理时间 | ~2 min | 5 条新闻 + HTTP 往返 + tmux 启动 + LLM + validate + 入库 |
| 15 min tick 能处理 | 7 批 ≈ 35 条 | 保守估计（比 v8 略多一点 HTTP 开销） |
| 日均新闻量 | 300 条 | 经验值 |
| 每个 tick 平均 | 19 条 | 300 / (24 × 60 / 15) |
| **结论** | **1 个处理流程平日够用** | 高峰期最多积压 1-2 tick |

**极端情况应对**：
- 积压 > 100 条：降级 batch size 5 → 3，吞吐提升到 ~55 条/tick
- 积压 > 200 条：人工介入（检查是否有异常）

### 1.4 复杂度判定

**低复杂度任务**。相比 v8 进一步简化：jettask 端不再维护 tmux、不解析 TUI、不处理交互对话框，只剩 HTTP client。复杂度从"中"降到"低"。

---

## 2. 技术方案

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  smart-fund-server (jettask worker)                             │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Application Layer                                        │  │
│  │    ExtractionAppService                                   │  │
│  │    ├─ run_extraction_tick()   [15 min]                    │  │
│  │    ├─ run_relation_tick()     [30 min]                    │  │
│  │    ├─ run_cluster_tick()      [60 min]                    │  │
│  │    └─ run_sentiment_tick()    [15 min]                    │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                    │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │  Domain Layer — domain/extraction/                        │  │
│  │    EventExtractionService / EventGraphService / ...       │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                    │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │  Infrastructure — src/infrastructure/ai/                  │  │
│  │    PlannerClient（纯 HTTP 客户端，~30 行）                │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
└────────────────────────────┼────────────────────────────────────┘
                             │ HTTP POST /chat
                             │ (同步阻塞 ≤ 180s)
┌────────────────────────────▼────────────────────────────────────┐
│  Planner API (localhost:8899) — 独立部署                        │
│    POST /chat   【新增，本方案核心改造点】                      │
│      ├─ acquire semaphore（与 /tasks 共享 MAX_CONCURRENT=2）    │
│      ├─ 新建临时 PlannerSession（无 task_id 绑定）              │
│      ├─ _poll_until_done（复用 run_streaming 核心循环）         │
│      ├─ 解析 usage（parse_session_usage）                       │
│      └─ 关闭 session → 返回 JSON                                │
│                                                                 │
│    POST /tasks, /tasks/{id}/message, ...  【既有，不动】        │
│      （planner skill 自己用的交互式任务通道，写 tasks DB）      │
└────────────────────────────┬────────────────────────────────────┘
                             │ subprocess
┌────────────────────────────▼────────────────────────────────────┐
│  tmux session (planner_N / chat_XXX) → claude CLI → 后端 API    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 关键技术选型

| 决策点 | 选型 | 理由 |
|--------|------|------|
| LLM 调用 | **HTTP → planner API `/chat`** | planner 已是通用 CLI 网关，复用其 tmux/多后端/rate-limit 切换/usage 解析 |
| 并发控制 | **planner 内部 semaphore（MAX_CONCURRENT=2）** | 与 planner skill 共享 tmux 资源池，避免双方打架 |
| **会话策略** | **每批独立**（planner `/chat` 内部实现） | 零上下文污染；jettask 侧完全无感知 |
| 端点设计 | **同步阻塞 HTTP（timeout ≤ 300s）** | 简单直接；jettask 是后台 worker 不怕阻塞；无需引入轮询/SSE |
| 不复用 `/tasks` | **`/tasks` 和事件抽取解耦** | `/tasks` 深度绑定 tasks DB / pending_dialog / messages，不适合高频后台批处理 |
| 模型选择 | 抽取/关系：`sonnet`；情感：`haiku` | sonnet 结构化强；haiku 够用且快 |
| 输出格式 | graphify schema `{nodes, edges}` | 通过 `GraphifyAdapter.validate()` 校验 |
| Schema 校验 | `GraphifyAdapter.validate()` + 重试 1 次 | adapter 封装 graphify.validate |
| 图库 | NetworkX + Postgres `ft_graph_nodes` / `ft_graph_edges` | graphify 原生 + adapter |
| 社区发现 | `GraphifyAdapter.cluster_weighted()` | Leiden + 时间衰减 + 语义虚拟边（自研增强） |
| 预筛选 | 规则黑名单仅 | 简化；LLM 自己判断纯播报 |
| Prompt 结构 | system prompt 简洁（~700 tokens），详细规则放 user message | 便于服务端内容 cache |

### 2.3 为什么选 A（走 planner API）而不是 B（jettask 自建子进程）

| 维度 | A：走 planner API（**本方案**） | B：jettask 自建 claude -p 子进程（v8） |
|------|---------------------------|----------------------------------|
| jettask 新增代码 | **~30 行**（PlannerClient） | ~300 行（ClaudeCliOneShot + 复制 tmux_manager 的纯函数 + 交互检测） |
| planner 改造 | **~100 行**（新增 `/chat` 端点 + 重构公共循环） | 0 |
| tmux 管理 | **统一由 planner 负责**（一处装、一处维护） | jettask/planner 各装一份 |
| 多后端切换 | **免费继承**（planner 已有 priority 重试 + rate-limit 降级） | 需要自己实现 |
| Usage 解析 | **免费继承**（`parse_session_usage` 已成熟） | 需要复制函数 |
| 交互对话框 | **免费继承**（自动 Enter/Escape confirmation/permission） | 需要复制 `_detect_interactive_dialog` |
| 并发控制 | 与 planner skill **共享** semaphore，互不冲撞 | jettask 自建，需要协调（或两个系统各跑各的） |
| Session 策略灵活性 | 由 planner 端参数控制（one-shot / keyed 复用），未来可扩展 | 硬编码在 jettask |
| 部署复杂度 | jettask 镜像**无需装 tmux** | jettask 镜像需要装 tmux + claude CLI |
| 故障域 | planner 崩了事件抽取停（可 supervisord 拉起） | 各自独立，但要监控两份 |
| 代码重复 | 无 | tmux 工具函数在两处维护（`_is_noise`、`_clean_result` 等） |

**决策**：A 胜出。核心理由是"planner API 已经是通用 Claude CLI 网关"这个前提——重新实现一套 tmux 管理违反 DRY 原则，且放弃了 planner 已经打磨过的多后端切换/交互对话框自动处理等能力。

### 2.4 为什么坚持"每批独立"而不是"session 复用 + follow-up"

| 维度 | 每批独立（**本方案**） | Session 复用（follow-up） |
|------|---------------------|-----------------------|
| 上下文污染 | **无**（每批干净） | **有**（前批的 prompt+response 累积在对话历史里） |
| Cache 命中 | 依赖 **Anthropic 服务端内容 cache**（按 system prompt 内容 hash） | 显式复用同一 session |
| Cache 差值 | 约 $0.57/月（见 §2.5 计算） | 同 |
| 单批启动开销 | ~5s（tmux + claude CLI 启动） | ~0s |
| 单批启动开销占比 | 5s / 120s = **4%** | - |
| 结果可复现性 | **高**（同输入→同输出） | 低（依赖对话历史） |
| 幻觉风险 | 无 | 有（"该公司财报受降息政策影响"式跨批次污染） |
| 调试成本 | 出错只看单批 | 要追溯历史对话 |
| 代码复杂度 | 低 | 高（需要命名 session + TTL + 强制清空 or 重置） |

**量化权衡**：
- 节省 245s/天启动时间（50 批 × 5s）= 0.07% 的 15min tick 利用率
- 代价：质量风险 + 调试成本 + 代码复杂度上升

**结论**：启动开销只占 4%，不值得为了 0.07% 的时间节省引入质量风险。坚持每批独立。

> 注：用户反驳 v7 分析时提到"可以用 follow-up 复用 session 解决 cache 命中问题"——这在**技术上成立**，但它和"每批独立"互斥。本方案选"每批独立"是在"架构简化"和"质量稳定"之间的权衡，不是"技术上做不到"。未来若 cache 成本变显著（如 system prompt 变成 5K+ tokens），可以在 planner 的 `/chat` 端点上加 `session_key` 参数支持命名复用——**这是 planner 端的扩展，不影响 jettask**。

### 2.5 Cache 成本详细分析

```
system prompt: ~700 tokens

Anthropic 定价（Sonnet，2026-04）：
- Cache write（首次或变更）：$3.75/1M input tokens
- Cache read（命中）：$0.30/1M input tokens
- 无 cache：$3.0/1M input tokens

按内容 hash 缓存（Claude CLI 启用 cache_control=ephemeral，服务端内容 cache 5min TTL）：
- 一个 tick 内的连续批次，system prompt 内容不变 → 第 2 批开始命中 cache
- 跨 tick（间隔 15min）cache 已过期 → 每 tick 的第一批 cache write

日 60 批，每 tick 约 7 批：
- 每 tick：1 次 write + 6 次 read
- 日：24 次 write + 36 × 6 = 以 tick 计算 96 tick → 约 24 次 write + 剩余 cache hit
- 成本：24 × 700 × $3.75/1M + 剩余 × 700 × $0.30/1M
- 月估算：< $2

v8 方案（每批独立）vs 虚拟的 session 复用方案：
- 差值 < $1/月
```

**结论**：cache 收益极小（每月不到 $1），不值得引入质量风险或架构复杂度。

### 2.6 graphify 集成策略：adapter 层封装

**核心原则**：graphify 是"无状态图算法工具包"，不是"图数据库"。数据模型、时间维度、事件生命周期、语义增强全部自研。

**判定规则**：无状态纯算法 → 复用 graphify；有状态业务逻辑 → 自研。

#### graphify 复用清单（通过 `GraphifyAdapter` 封装，业务代码不直接 import）

| graphify 模块 | 复用理由 | 调用时机 |
|---------------|---------|---------|
| `validate_extraction(data)` | 通用 schema 校验，已验证 100% 通过率 | LLM 输出落库前 |
| `build_from_json(ext)` | NetworkX 构建，保留自定义字段 | 每次聚类前从 DB 读取数据后 |
| `cluster(G)` | Leiden 质量已验证，加权支持 | 主线计算任务中 |
| `cohesion_score(G, nodes)` | 通用指标 | 主线质量评估 |
| `to_json` / `to_html` | 可视化导出 | 调试和前端展示 |

#### 自研清单（所有与"时间+状态+业务语义"相关的部分）

| 模块 | 自研理由 |
|------|---------|
| 数据模型（5 张表） | graphify 无持久化层，DB 是核心资产 |
| 时间衰减加权器 | graphify 原生不支持，金融硬需求（半衰期分档） |
| 事件生命周期状态机 | 业务状态机，不该让图库管 |
| 语义虚拟边注入器 | 补足 Leiden 的拓扑盲区 |
| 主线演化追踪 | 聚类是快照，主线要跨时间追踪（Jaccard 相似度匹配） |
| 连续置信度加权 | graphify 的三级枚举粒度不够 |
| LLM 提取链 | graphify 的 extract.py 只针对代码 AST，完全不适用 |

#### graphify 禁用清单

以下模块明确不复用：`extract.py`（AST 代码提取）、`cache.py`（SHA256 文件缓存）、`graph_diff`、`suggest_questions`、`to_cypher` / `push_to_neo4j`（除非明确需要 Neo4j）。

#### 聚类流水线：build 和 cluster 之间的三个自研介入点

```
LLM 提取
  → GraphifyAdapter.validate()        # graphify：校验 schema
  → 落库（持久层，自研）
  → [计算主线触发时]
  → 从 DB 查询活跃节点/边
  → GraphifyAdapter.build()           # graphify：构建 NetworkX 图
  → 注入时间衰减权重                   # 自研：半衰期分档加权
  → 注入语义虚拟边                     # 自研：embedding 相似度 >0.85 补边
  → GraphifyAdapter.cluster()         # graphify：Leiden 聚类
  → 社区同一性判定                     # 自研：Jaccard 匹配上一轮社区 → lineage_id
  → 写回 ft_event_communities
```

这三个自研介入点必须在文档中明确标出，不能暗示成"graphify 全包了"。

---

## 3. 数据模型

### 3.1 `ft_graph_nodes`（事件图节点）

对齐 graphify schema `REQUIRED_NODE_FIELDS = {id, label, file_type, source_file}`。**注意**：`file_type` 和 `source_file` 是 graphify 适配字段，LLM 不感知，由 adapter 层自动补充。业务语义使用 `event_type` 等。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | SHA256 of normalized title+body |
| label | TEXT | 事件标题（30 字内） |
| file_type | VARCHAR(16) DEFAULT 'document' | **占位符**，给 graphify 用的适配字段，LLM 不感知 |
| source_file | VARCHAR(128) | 源新闻 URL 或 `ft_news:{id}` |
| event_type | VARCHAR(16) | `policy` / `macro` / `industry` / `company` / `capital` / `rumor` |
| event_subtype | VARCHAR(32) NULL | |
| summary | TEXT | |
| entities | JSONB | `{organizations, industries, companies, regions}` |
| impact | JSONB | `{direction, strength, scope, duration}` |
| stance | VARCHAR(16) NULL | `support` / `restrict` / `neutral`（仅 policy） |
| sentiment | REAL | 0-1 |
| certainty | REAL | 0-1 |
| novelty | REAL | 0-1 |
| related_hints | TEXT[] | |
| news_id | BIGINT | FK → ft_news.id |
| lifecycle_state | VARCHAR(16) DEFAULT 'emerging' | `dormant` / `emerging` / `confirmed` / `peaking` / `fading`（自研状态机） |
| session_uuid | VARCHAR(64) | **planner `/chat` 返回的 session_uuid**（审计用） |
| model | VARCHAR(32) | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 3.2 `ft_graph_edges`（事件图边）

对齐 graphify schema `REQUIRED_EDGE_FIELDS = {source, target, relation, confidence, source_file}`。额外字段 `confidence_score`、`edge_type`、`weight` 由自研层维护。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| source | VARCHAR(64) | FK → ft_graph_nodes.id |
| target | VARCHAR(64) | FK → ft_graph_nodes.id |
| relation | VARCHAR(32) | `causal` / `temporal` / `thematic` / `contradicts` |
| confidence | VARCHAR(16) | `EXTRACTED` / `INFERRED` / `AMBIGUOUS` |
| confidence_score | REAL NULL | 0-1 |
| source_file | VARCHAR(128) | |
| edge_type | VARCHAR(16) DEFAULT 'explicit' | `explicit`（LLM 抽取）/ `semantic`（虚拟边，自研注入） |
| weight | REAL DEFAULT 1.0 | 边权重（时间衰减后，用于 Leiden 加权聚类） |
| evidence_text | TEXT NULL | |
| session_uuid | VARCHAR(64) | |
| created_at | TIMESTAMPTZ | |

索引：`UNIQUE(source, target, relation)`

### 3.3 `ft_event_communities`（主线表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| cluster_run_id | VARCHAR(36) | 本次聚类运行的唯一 ID（UUID），同一 run 下的社区一起产出 |
| lineage_id | VARCHAR(36) | **主线血缘 ID**，跨时间追踪同一主线的演化（Jaccard 匹配上一轮社区） |
| community_id | INTEGER | 本次聚类内的社区编号（仅 run 内有效） |
| tick_ts | TIMESTAMPTZ | |
| node_ids | TEXT[] | |
| cohesion_score | REAL | |
| dominant_industry | VARCHAR(64) NULL | |
| size | INTEGER | |
| label | TEXT NULL | |

索引：`INDEX(cluster_run_id)`, `INDEX(lineage_id)`

### 3.4 `ft_sentiment_labels`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| source_table | VARCHAR(32) | |
| source_id | BIGINT | |
| sentiment | VARCHAR(16) | |
| certainty | REAL | |
| related_symbols | TEXT[] | |
| related_industries | TEXT[] | |
| session_uuid | VARCHAR(64) | |
| created_at | TIMESTAMPTZ | |

索引：`UNIQUE(source_table, source_id)`

### 3.5 `ft_ai_cli_log`（AI 调用审计日志）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| category | VARCHAR(32) | `extraction` / `relation` / `sentiment` |
| session_uuid | VARCHAR(64) | planner `/chat` 返回 |
| model | VARCHAR(32) | |
| input_tokens | INTEGER | |
| output_tokens | INTEGER | |
| cache_creation_tokens | INTEGER | |
| cache_read_tokens | INTEGER | |
| duration_sec | INTEGER | |
| success | BOOLEAN | |
| error_msg | TEXT NULL | |
| created_at | TIMESTAMPTZ | |

---

## 4. 接口设计

### 4.1 planner API 新增端点 `POST /chat`

**契约**：

```
POST /chat
Content-Type: application/json

Request body:
{
  "prompt": "用户消息（user message，含详细规则与待抽取新闻）",
  "system_prompt": "系统提示词（可选；默认 None，不加载 planner.md）",
  "model": "sonnet" | "opus" | "haiku",   // 可选，默认取 backend 的 model
  "backend": "official" | ...,              // 可选，默认 active backend
  "cwd": "/path/to/cwd",                    // 可选
  "timeout": 180,                           // 服务端等待 Claude 响应的秒数（默认 180）
  "session_key": null                       // 【保留字段】未来命名 session 复用用，本版本不启用
}

Response 200:
{
  "result": "Claude 原始输出文本（已清理 TUI 噪音）",
  "session_uuid": "uuid-xxx",               // Claude CLI session 的 UUID（审计用）
  "duration_sec": 123,
  "usage": {
    "input_tokens": 3000,
    "output_tokens": 800,
    "cache_creation_tokens": 700,
    "cache_read_tokens": 0,
    "total_cost_usd": 0.0234,
    "model": "claude-sonnet-4-6",
    "turns": 1
  },
  "tool_calls": []                          // 事件抽取场景通常为空（LLM 只输出 JSON，不调工具）
}

Response 429（rate limit）:
{"detail": "Claude CLI rate limited on all backends"}

Response 500（启动失败 / 超时等）:
{"detail": "..."}
```

**行为**：

1. 同步阻塞：直到 Claude CLI 输出完成或 timeout
2. 会话生命周期：请求开始 → 新建 tmux + claude CLI → 发送 prompt → 等待输出 → 关闭 session
3. 并发控制：`_session_manager.acquire(timeout=300)` 等待空闲槽位
4. Rate limit：复用 `_run_task` 的多后端优先级重试（按 `priority` 列表依次尝试）
5. **不写 tasks DB**，不产生 `pending_dialog`
6. 自动对话框处理：沿用 `_detect_interactive_dialog` 的 `auto_action`（Enter/Escape），交互式选择（如多选项）**视为错误**（事件抽取场景不该出现），直接 Ctrl-C 返回 500

### 4.2 planner 侧代码改造方案

**新增：`server.py` 增加 `/chat` 端点（~80 行）**

```python
# server.py

class ChatRequest(BaseModel):
    prompt: str
    system_prompt: str | None = None
    model: str | None = None
    backend: str | None = None
    cwd: str | None = None
    timeout: int = 180
    session_key: str | None = None  # 保留字段

class ChatResponse(BaseModel):
    result: str
    session_uuid: str
    duration_sec: int
    usage: dict
    tool_calls: list

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    cfg = load_backends()
    priority = get_backend_priority(req.backend or cfg.get("active"))
    last_error = "无可用后端"

    if not _session_manager.acquire(timeout=300):
        raise HTTPException(503, "服务器繁忙")

    try:
        for backend_name in priority:
            backend_config = cfg["backends"][backend_name]
            model = req.model or backend_config.get("model", "sonnet")

            # 用新的一次性会话类（见下方 tmux_manager.py 改造）
            session = EphemeralSession(
                backend_config=backend_config,
                cwd=req.cwd,
                model=model,
                system_prompt=req.system_prompt,
                env_unset=cfg.get("env_unset", []),
                model_map=cfg.get("model_map", {}),
            )
            if not session.ready:
                last_error = f"后端 {backend_name} 启动失败"
                session.close()
                continue

            try:
                result = run_chat(session, req.prompt, timeout=req.timeout)
            finally:
                session.close()

            if result.get("rate_limited"):
                last_error = f"后端 {backend_name} rate limited"
                log.warning(f"[chat] {last_error}，切换下一个后端")
                continue

            if result.get("timeout"):
                raise HTTPException(504, f"执行超时 ({req.timeout}s)")

            if not result.get("result"):
                last_error = f"后端 {backend_name} 未获得有效输出"
                continue

            return ChatResponse(
                result=result["result"],
                session_uuid=session.claude_session_id,
                duration_sec=int(result["duration"]),
                usage=result.get("usage", {}),
                tool_calls=result.get("tool_calls", []),
            )

        raise HTTPException(429, f"所有后端均失败: {last_error}")
    finally:
        _session_manager.release()
```

**新增/改造：`tmux_manager.py`**

两件事：
1. 新增 `EphemeralSession`（或把 `PlannerSession` 的 `task_id` 改为可选 + session_name 生成逻辑调整）
2. 新增 `run_chat(session, prompt, timeout)` — 从 `run_streaming` 裁剪，去掉 DB 写入和 `pending_dialog` 写入

**推荐做法（侵入最小）**：

```python
# tmux_manager.py

class EphemeralSession:
    """一次性会话（不绑定 task_id，不写 DB）。
    
    与 PlannerSession 的差异：
    - session_name 基于 session_uuid（不基于 task_id）
    - 不在 SessionManager._sessions 中注册（无需生命周期管理）
    - 调用方用完即 close()
    """
    def __init__(self, backend_config, cwd=None, model=None,
                 system_prompt=None, env_unset=None, model_map=None):
        self.model = model or backend_config.get("model", "sonnet")
        self.claude_session_id = str(uuid.uuid4())
        self.session_name = f"chat_{self.claude_session_id[:8]}"
        self._known_lines: set[str] = set()
        self.ready = False
        
        # ── 启动 tmux（直接复用 PlannerSession.__init__ 的构建命令逻辑）──
        # ... 参照 PlannerSession.__init__ 第 384-450 行（kill 旧 session、env_unset、
        #     model_map、--session-id、--setting-sources ""、--system-prompt $(cat file)
        #     等）——完全一致，唯一差异是 self.session_name 的生成方式
        
        # 启动后等提示符
        if self._wait_for_prompt(timeout=30):
            screen = self.capture(-500)
            self._known_lines = set(_extract_content(screen))
            self.ready = True

    # capture / _wait_for_prompt / mark / send / send_ctrl_c /
    # get_new_lines / is_at_prompt / close —— 与 PlannerSession 完全相同
```

**重构建议（可选，不做也行）**：把 `PlannerSession` 和 `EphemeralSession` 的公共代码抽到基类 `BaseCliSession`。本方案**不强制**，因为 PlannerSession 逻辑未来可能演化，提前抽象容易过度设计——先让 EphemeralSession 抄一份，等稳定后再统一。

```python
def run_chat(session, prompt: str, timeout: int = 180) -> dict:
    """无 DB 依赖的 one-shot 执行。
    
    与 run_streaming 的差异：
    - 不接收 task_id 参数
    - 不调 update_task()
    - 不写 pending_dialog；检测到需要客户端决策的对话框直接 Ctrl-C + raise
    - 不解析 progress（事件抽取场景不需要进度）
    
    完成检测逻辑、tool_calls 解析、timeout/rate_limited/usage 解析均复用
    run_streaming 的相应代码段。
    """
    session.mark()
    for line in prompt.split('\n'):
        s = line.strip()
        if s:
            session._known_lines.add(s)
    session.send(prompt)

    accumulated: list[str] = []
    tool_calls: list[dict] = []
    printed: set[str] = set()
    start = time.time()
    idle_count = 0
    last_content = ""

    while time.time() - start < timeout:
        time.sleep(1)
        new_lines = session.get_new_lines()

        if new_lines:
            idle_count = 0
            for line in new_lines:
                if line in printed:
                    continue
                accumulated.append(line)
                printed.add(line)
                tool_info = _parse_tmux_tool_line(line)
                if tool_info:
                    tool_calls.append(tool_info)
        else:
            idle_count += 1

        # 对话框：rate_limit → 返回 rate_limited；auto_action → 自动处理；
        # 其他（需要客户端决策）→ Ctrl-C + 报错
        if idle_count >= 2 and idle_count % 2 == 0:
            raw_screen = session.capture()
            dialog = _detect_interactive_dialog(raw_screen)
            if dialog:
                if dialog["type"] == "rate_limit":
                    session.send_ctrl_c()
                    return {"result": None, "tool_calls": tool_calls,
                            "duration": time.time() - start,
                            "timeout": False, "rate_limited": True, "usage": {}}
                if dialog["auto_action"]:
                    key = {"enter": "Enter", "escape": "Escape"}[dialog["auto_action"]]
                    _tmux("send-keys", "-t", session.session_name, key)
                    idle_count = 0
                    continue
                # 需要客户端决策 → 事件抽取场景不该出现，直接报错
                session.send_ctrl_c()
                raise RuntimeError(f"[chat] 未预期的交互对话框: {dialog['title'][:80]}")

        # 完成检测
        content = "\n".join(accumulated)
        if content == last_content:
            raw_screen = session.capture()
            if accumulated and idle_count >= 2 \
                    and _is_at_prompt(raw_screen) and not _is_thinking(raw_screen):
                break
        else:
            last_content = content

    duration = time.time() - start

    if time.time() - start >= timeout:
        session.send_ctrl_c()
        usage = parse_session_usage(session.claude_session_id)
        return {"result": None, "tool_calls": tool_calls, "duration": duration,
                "timeout": True, "usage": usage}

    raw_result = "\n".join(accumulated).strip()
    result = _clean_result(raw_result)
    usage = parse_session_usage(session.claude_session_id)

    return {"result": result, "tool_calls": tool_calls, "duration": duration,
            "timeout": False, "usage": usage}
```

**不改动的部分**：
- `PlannerSession` / `SessionManager` / `run_streaming` / `send_followup`：原样不动，planner skill 继续用
- `/tasks` / `/tasks/{id}/message` / `/tasks/{id}/respond` 等既有端点：原样不动
- `parse_session_usage` / `_is_noise` / `_clean_result` / `_detect_interactive_dialog` / `_is_at_prompt` / `_is_thinking` / `_parse_tmux_tool_line`：原样不动（直接复用）

### 4.3 jettask 侧模块划分

```
src/
├── infrastructure/ai/
│   └── planner_client.py             ← 【新建】HTTP 客户端（~50 行）
│
└── domain/extraction/
    ├── graph/                         ← 【v10 新增】图谱计算层
    │   ├── graphify_adapter.py        ← 【新建】graphify 封装（唯一 import graphify 的文件）
    │   ├── time_decay.py              ← 【新建】时间衰减加权器
    │   ├── semantic_edges.py          ← 【新建】语义虚拟边注入器
    │   ├── lifecycle.py               ← 【新建】事件生命周期状态机
    │   └── lineage_tracker.py         ← 【新建】主线血缘追踪（Jaccard 匹配）
    ├── services/
    │   ├── event_extraction.py        ← 【新建】主抽取入口
    │   ├── event_graph_service.py     ← 【新建】图谱服务（调 graph/ 层）
    │   └── sentiment_classifier.py    ← 【新建】情感分类
    ├── ai/
    │   └── prompts/
    │       ├── extraction.md         ← system prompt
    │       ├── extraction_ref.md     ← 详细规则/示例（放进 user message）
    │       ├── relation.md
    │       └── sentiment.md
    ├── queries/
    │   └── extraction_query_service.py
    └── repositories/
        ├── graph_node_repository.py
        ├── graph_edge_repository.py
        ├── community_repository.py
        ├── sentiment_label_repository.py
        └── ai_cli_log_repository.py
```

### 4.4 核心接口

#### 4.4.1 `PlannerClient`（基础设施层）

```python
# src/infrastructure/ai/planner_client.py

import httpx
import json

class PlannerError(RuntimeError): ...
class PlannerRateLimitError(PlannerError): ...
class PlannerTimeoutError(PlannerError): ...

class PlannerClient:
    """planner API (/chat) 的同步 HTTP 客户端。
    
    用法：
        client = PlannerClient()
        resp = client.chat(
            prompt="抽取下面新闻的事件：...",
            system_prompt=open("extraction.md").read(),
            model="sonnet",
            timeout=180,
        )
        data = json.loads(client.extract_json(resp["result"]))
    """
    
    def __init__(self, base_url="http://localhost:8899", http_timeout=300):
        self.base_url = base_url.rstrip("/")
        # http_timeout 要略大于 chat 的 timeout（给服务端收尾留余量）
        self._client = httpx.Client(timeout=http_timeout)

    def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str = "sonnet",
        timeout: int = 180,
        backend: str | None = None,
        cwd: str | None = None,
    ) -> dict:
        """返回 {result, session_uuid, duration_sec, usage, tool_calls}"""
        try:
            r = self._client.post(
                f"{self.base_url}/chat",
                json={
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                    "model": model,
                    "backend": backend,
                    "cwd": cwd,
                    "timeout": timeout,
                },
            )
        except httpx.ReadTimeout as e:
            raise PlannerTimeoutError(f"HTTP 超时: {e}") from e
        except httpx.HTTPError as e:
            raise PlannerError(f"HTTP 错误: {e}") from e

        if r.status_code == 429:
            raise PlannerRateLimitError(r.text)
        if r.status_code == 504:
            raise PlannerTimeoutError(r.text)
        if r.status_code >= 400:
            raise PlannerError(f"HTTP {r.status_code}: {r.text}")

        return r.json()

    @staticmethod
    def extract_json(text: str) -> dict:
        """从 Claude 输出中提取第一个 JSON 对象（容错：允许前后有 markdown/文本）"""
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1:
            raise ValueError(f"no JSON object in output: {text[:200]}")
        return json.loads(text[start:end + 1])

    def health(self) -> bool:
        try:
            r = self._client.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
```

#### 4.4.2 `EventExtractionService`（domain 层）

```python
# src/domain/extraction/services/event_extraction.py

import time
import logging
from pathlib import Path
from domain.extraction.graph.graphify_adapter import GraphifyAdapter

log = logging.getLogger(__name__)

class EventExtractionService:
    BATCH_SIZE_NORMAL = 5
    BATCH_SIZE_DEGRADED = 3
    DEGRADE_THRESHOLD = 100
    TICK_BUDGET_SEC = 13 * 60  # 15 min 留 2 min buffer

    def __init__(self, news_repo, rule_filter, node_repo, log_repo,
                 planner_client, prompt_dir: Path):
        self.news_repo = news_repo
        self.rule_filter = rule_filter
        self.node_repo = node_repo
        self.log_repo = log_repo
        self.planner = planner_client
        self._adapter = GraphifyAdapter()
        self._sp = (prompt_dir / "extraction.md").read_text(encoding="utf-8")
        self._ref = (prompt_dir / "extraction_ref.md").read_text(encoding="utf-8")

    def extract_tick(self) -> dict:
        """
        尽力而为：
        1. 拉取未抽取新闻
        2. 规则过滤
        3. 判断是否降级（积压 > DEGRADE_THRESHOLD）
        4. 每 5/3 条一批，POST /chat
        5. validate + 入库
        6. 记录 ft_ai_cli_log
        7. tick 超时则停止，剩余留下个 tick
        """
        tick_start = time.time()
        pending = self.news_repo.list_unextracted(limit=1000)
        filtered = self.rule_filter.filter(pending)

        batch_size = (self.BATCH_SIZE_DEGRADED
                      if len(filtered) > self.DEGRADE_THRESHOLD
                      else self.BATCH_SIZE_NORMAL)

        processed = 0
        failed = 0

        for batch in _chunked(filtered, batch_size):
            if time.time() - tick_start > self.TICK_BUDGET_SEC:
                log.info("tick budget exhausted, %d items remain",
                         len(filtered) - processed)
                break

            t0 = time.time()
            session_uuid = None
            try:
                resp = self.planner.chat(
                    prompt=self._make_user_message(batch),
                    system_prompt=self._sp,
                    model="sonnet",
                    timeout=180,
                )
                session_uuid = resp["session_uuid"]
                data = PlannerClient.extract_json(resp["result"])

                # validate + 重试 1 次（通过 adapter，不直接依赖 graphify）
                errors = self._adapter.validate(data)
                if errors:
                    log.warning("validate 失败，重试一次: %s", errors[:3])
                    resp2 = self.planner.chat(
                        prompt=self._make_user_message(batch, retry_errors=errors),
                        system_prompt=self._sp,
                        model="sonnet",
                        timeout=180,
                    )
                    session_uuid = resp2["session_uuid"]
                    data = PlannerClient.extract_json(resp2["result"])
                    errors = self._adapter.validate(data)
                    if errors:
                        raise ValueError(f"validate 二次失败: {errors[:3]}")

                self._persist(batch, data, session_uuid, model=resp.get("usage", {}).get("model", "sonnet"))

                self.log_repo.insert(
                    category="extraction",
                    session_uuid=session_uuid,
                    model=resp.get("usage", {}).get("model", "sonnet"),
                    duration_sec=int(time.time() - t0),
                    success=True,
                    **_extract_usage_fields(resp.get("usage", {})),
                )
                processed += len(batch)

            except PlannerRateLimitError:
                log.warning("rate limited, 中止 tick")
                self._log_failure(session_uuid, t0, "rate_limited")
                break
            except (PlannerTimeoutError, PlannerError, ValueError,
                    json.JSONDecodeError) as e:
                log.warning("batch 失败 (%s)，跳过继续", e)
                self._log_failure(session_uuid, t0, str(e)[:300])
                failed += 1
                continue

        return {"processed": processed, "failed": failed,
                "remaining": max(0, len(filtered) - processed - failed)}

    def _make_user_message(self, batch, retry_errors=None) -> str:
        parts = [self._ref, "\n【待抽取新闻】\n"]
        for n in batch:
            parts.append(json.dumps({
                "id_hint": n.id,
                "title": n.title,
                "body": n.body[:3000],  # 截断防超长
                "published_at": n.published_at.isoformat(),
            }, ensure_ascii=False))
        if retry_errors:
            parts.append(f"\n【上一次校验错误，请修正】\n{retry_errors}")
        parts.append("\n仅输出 JSON，不要 markdown 代码块。")
        return "\n".join(parts)

    def _persist(self, batch, data, session_uuid, model):
        # 后端重写 node id（SHA256），避免 LLM 幻觉 id
        for node in data.get("nodes", []):
            node["id"] = _hash_node(node)
            node["session_uuid"] = session_uuid
            node["model"] = model
            node["news_id"] = _resolve_news_id(node, batch)
        self.node_repo.bulk_upsert(data["nodes"])

    def _log_failure(self, session_uuid, t0, msg):
        self.log_repo.insert(
            category="extraction",
            session_uuid=session_uuid or "",
            model="sonnet",
            duration_sec=int(time.time() - t0),
            success=False,
            error_msg=msg,
        )


def _chunked(iterable, n):
    buf = []
    for item in iterable:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _extract_usage_fields(usage: dict) -> dict:
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
    }
```

#### 4.4.3 `EventGraphService`（domain 层）

```python
from domain.extraction.graph.graphify_adapter import GraphifyAdapter
from domain.extraction.graph.time_decay import apply_time_decay
from domain.extraction.graph.semantic_edges import inject_semantic_edges
from domain.extraction.graph.lineage_tracker import match_lineage

class EventGraphService:
    def __init__(self, node_repo, edge_repo, community_repo,
                 planner_client, prompt_dir):
        self.node_repo = node_repo
        self.edge_repo = edge_repo
        self.community_repo = community_repo
        self.planner = planner_client
        self._adapter = GraphifyAdapter()
        self._relation_sp = (prompt_dir / "relation.md").read_text()

    def extract_relations_tick(self, window_hours=24):
        nodes = self.node_repo.list_recent(hours=window_hours)
        for batch in _chunked(nodes, 20):
            resp = self.planner.chat(
                prompt=self._make_relation_prompt(batch),
                system_prompt=self._relation_sp,
                model="sonnet",
                timeout=180,
            )
            data = PlannerClient.extract_json(resp["result"])
            self.edge_repo.bulk_upsert(
                [{**e, "session_uuid": resp["session_uuid"]} for e in data.get("edges", [])]
            )

    def cluster_latest(self, window_hours=72):
        """
        完整聚类流水线（v10）：
        1. 从 DB 查询活跃节点/边
        2. adapter.build() → NetworkX 图
        3. 注入时间衰减权重（自研）
        4. 注入语义虚拟边（自研）
        5. adapter.cluster() → Leiden 聚类
        6. 社区同一性判定 → lineage_id（自研）
        7. 写回 ft_event_communities
        """
        nodes = self.node_repo.list_recent(hours=window_hours)
        edges = self.edge_repo.list_recent(hours=window_hours)

        # 1-2: 构建 NetworkX 图
        G = self._adapter.build({"nodes": nodes, "edges": edges})

        # 3: 注入时间衰减权重
        apply_time_decay(G, half_life_config={
            "policy": 60, "macro": 60, "industry": 30,
            "company": 14, "capital": 14, "rumor": 7,
        })

        # 4: 注入语义虚拟边（embedding 相似度 >0.85）
        inject_semantic_edges(G, similarity_threshold=0.85, weight_factor=0.3)

        # 5: Leiden 聚类
        communities = self._adapter.cluster(G)

        # 6: 社区同一性判定（Jaccard 匹配上一轮社区 → lineage_id）
        prev_communities = self.community_repo.list_latest()
        communities = match_lineage(communities, prev_communities)

        # 7: 写回
        run_id = uuid.uuid4().hex
        self.community_repo.bulk_insert(communities, cluster_run_id=run_id)
```

#### 4.4.4 `GraphifyAdapter`（graph 计算层）

```python
# src/domain/extraction/graph/graphify_adapter.py
# 【唯一 import graphify 的文件】业务代码通过此类间接调用。

from graphify.validate import validate_extraction
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.cluster import cohesion_score

class GraphifyAdapter:
    """graphify 的无状态封装层。

    职责：校验、构建、聚类、内聚度评分——全是纯函数，无副作用。
    业务代码（services/）不直接 import graphify，只通过本类调用。
    """

    def validate(self, data: dict) -> list[str]:
        """校验 extraction 输出，返回错误列表（空=通过）。"""
        return validate_extraction(data)

    def build(self, data: dict):
        """从 {nodes, edges} 构建 NetworkX DiGraph。保留所有自定义字段。"""
        # 补充 graphify 必填字段（file_type 占位）
        for node in data.get("nodes", []):
            node.setdefault("file_type", "document")
        for edge in data.get("edges", []):
            edge.setdefault("source_file", "")
        return build_from_json(data)

    def cluster(self, G) -> list[dict]:
        """Leiden 社区发现。G 可以已有 weight 属性（被时间衰减修改过）。"""
        return cluster(G)

    def cohesion(self, G, nodes) -> float:
        """社区内聚度评分。"""
        return cohesion_score(G, nodes)
```

#### 4.4.5 自研图谱增强模块

**时间衰减加权器**（`time_decay.py`）：

```python
def apply_time_decay(G, half_life_config: dict[str, int]):
    """对每条边按其 event_type 对应的半衰期计算衰减权重。

    G.edges 必须有 'created_at' 和 'event_type'（从节点继承）属性。
    衰减公式：weight = base_weight * exp(-age_days / half_life_days)
    """
    import math
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for u, v, data in G.edges(data=True):
        event_type = data.get("event_type", "industry")
        half_life = half_life_config.get(event_type, 30)
        created = data.get("created_at")
        if created:
            age_days = max(0, (now - created).total_seconds() / 86400)
        else:
            age_days = 0
        base = data.get("weight", 1.0)
        data["weight"] = base * math.exp(-age_days / half_life)
```

**语义虚拟边注入器**（`semantic_edges.py`）：

```python
def inject_semantic_edges(G, similarity_threshold=0.85, weight_factor=0.3):
    """对 embedding 余弦相似度 > threshold 且无显式边的节点对注入虚拟边。

    虚拟边 weight = 显式边 weight * weight_factor（避免喧宾夺主）。
    edge_type='semantic' 标记，区别于 LLM 抽取的 'explicit' 边。
    """
    nodes = list(G.nodes(data=True))
    for i, (u, u_data) in enumerate(nodes):
        for j, (v, v_data) in enumerate(nodes):
            if i >= j:
                continue
            if G.has_edge(u, v) or G.has_edge(v, u):
                continue
            u_emb = u_data.get("embedding")
            v_emb = v_data.get("embedding")
            if u_emb is None or v_emb is None:
                continue
            sim = _cosine_similarity(u_emb, v_emb)
            if sim > similarity_threshold:
                G.add_edge(u, v, edge_type="semantic", weight=weight_factor,
                           confidence="INFERRED", relation="thematic",
                           similarity=sim, source_file="semantic_injection")
```

**事件生命周期状态机**（`lifecycle.py`）：

```python
# 触发规则：
# - dormant → emerging：首次被提取
# - emerging → confirmed：48h 内被 ≥2 条独立新闻引用
# - confirmed → peaking：所在社区 cohesion_score 达到阈值 + 节点入度激增
# - peaking → fading：入度增长率连续 N 天 < 0
# - fading → 软删除：最近 30 天无新边

LIFECYCLE_TRANSITIONS = {
    "dormant": "emerging",      # 首次提取
    "emerging": "confirmed",    # 多源引用
    "confirmed": "peaking",     # 社区热度爆发
    "peaking": "fading",        # 热度消退
    # fading 节点不物理删除，只在查询时降权
}

def advance_lifecycle(node, edges_count, hours_since_first,
                      community_cohesion=None, indeed_growth_rate=None):
    """根据条件推进节点生命周期状态。"""
    ...
```

**主线血缘追踪**（`lineage_tracker.py`）：

```python
def match_lineage(new_communities: list[dict],
                  prev_communities: list[dict],
                  jaccard_threshold=0.4) -> list[dict]:
    """用 Jaccard 相似度匹配本轮和上一轮的社区，为延续的社区分配相同 lineage_id。

    Leiden 每次产出的 community_id 是随机的，无法跨时间对比。
    lineage_id 是稳定的主线标识，前端展示用。
    """
    for new_c in new_communities:
        best_match = None
        best_score = 0
        new_set = set(new_c["node_ids"])
        for prev_c in prev_communities:
            prev_set = set(prev_c["node_ids"])
            jaccard = len(new_set & prev_set) / max(1, len(new_set | prev_set))
            if jaccard > best_score:
                best_score = jaccard
                best_match = prev_c
        if best_score >= jaccard_threshold and best_match:
            new_c["lineage_id"] = best_match["lineage_id"]
        else:
            new_c["lineage_id"] = uuid.uuid4().hex
    return new_communities
```

### 4.5 Prompt 结构

**`extraction.md`（system prompt，~700 tokens）**：
```
你是一个金融新闻的结构化事件抽取器。

【输出格式】严格的 JSON schema：{nodes: [...], edges: []}
  node 必须字段：id（留空，由后端重写）、label、source_file="ft_news:{id_hint}"、
                 event_type、summary、entities、impact、certainty、novelty
  edges 在本阶段可为空数组（由关系抽取阶段填充）
  注意：不要输出 file_type 字段（由后端自动补充）

【事件类型】policy/macro/industry/company/capital/rumor

【核心原则】
- 一篇文章可拆出多个事件（例：政策+受益行业）
- 纯播报/标题党/重复新闻：nodes 返回空数组
- certainty 0-1：明确事实 > 0.8；推论 0.5-0.8；传闻 < 0.5
- 只输出 JSON，不要 markdown 代码块
```

**`extraction_ref.md`（放 user message 前半段，~1500 tokens）**：详细字段语义、正反例各 3 条、常见错误、边界判断规则。

**user message 结构**（每批 ~2500 tokens）：
```
{extraction_ref.md 内容}

【待抽取新闻】
{"id_hint": 12345, "title": "...", "body": "...", "published_at": "..."}
{"id_hint": 12346, ...}
...× 5 条

仅输出 JSON，不要 markdown 代码块。
```

---

## 5. 实现步骤

### Step 1: planner API 改造 — 新增 `/chat` 端点

**目标**：planner API 支持无 DB 副作用的一次性 LLM 调用。

**涉及文件**：
- `.claude/skills/claude-planner/tmux_manager.py` — 新增 `EphemeralSession` 类 + `run_chat()` 函数
- `.claude/skills/claude-planner/server.py` — 新增 `ChatRequest`/`ChatResponse` pydantic 模型 + `@app.post("/chat")` 路由

**实现要点**：

1. `EphemeralSession.__init__` **完全复制** `PlannerSession.__init__` 的 tmux 启动逻辑（§tmux_manager.py 第 384-450 行），**唯一差异**是：
   - 构造参数不接收 `task_id`
   - `self.session_name = f"chat_{self.claude_session_id[:8]}"`（不是 `planner_{task_id}`）
   - 不调 `update_task()`（PlannerSession 原本也没调，只是 log.info）

2. `run_chat()` 从 `run_streaming()` 剪掉以下部分：
   - 所有 `update_task(task_id, ...)` 调用
   - `last_db_write` / `last_log_time` / `last_pct` 进度相关变量
   - `_estimate_progress()` 调用
   - `pending_dialog` 写 DB 分支 —— 替换为"**需要客户端决策的对话框直接 Ctrl-C + raise RuntimeError**"
   - `parse_session_usage` 调用时机不变

3. `server.py` 的 `/chat` 路由按 §4.2 实现。**关键**：
   - `_session_manager.acquire(timeout=300)` 共享并发控制（与 `/tasks` 共用 semaphore）
   - `finally` 块必须 `session.close()` + `_session_manager.release()`，防止 tmux 泄漏
   - 多后端 priority 重试逻辑参考 `_run_task()`（第 152-219 行）

4. **易错点**：
   - `EphemeralSession` 不加入 `SessionManager._sessions` 字典（它是一次性的，无需管理生命周期）
   - 服务端 `timeout` 是 Claude 响应等待上限；HTTP 层应另配一个更大的 socket timeout（jettask 端 httpx 300s，planner 内部 180s，留 120s 余量给启动+收尾）
   - `/chat` 返回 `session_uuid` 是 Claude CLI 的 UUID（可以定位 `~/.claude/projects/*/{uuid}.jsonl`），不是 tmux session name

**测试用例**：

| 用例 | 输入/操作 | 预期结果 |
|------|-----------|----------|
| 基本调用 | `curl -X POST localhost:8899/chat -d '{"prompt":"输出 JSON: {\"a\":1}","timeout":60}'` | 200，`result` 含 `{"a":1}`，`usage.input_tokens > 0` |
| 带 system_prompt | 传入"你是 JSON 翻译机" | 200，response 严格按 system prompt 行为 |
| 超时 | `timeout=5`，复杂任务 | 504，且服务端 tmux 无残留 `tmux list-sessions \| grep chat_` 为空 |
| 连续调用 cache | 同 system_prompt 连续调 2 次 | 第 2 次 `cache_read_tokens > 0` |
| Rate limit 切后端 | 模拟首 backend rate limit | 200（切到第二个 backend） |
| 未预期对话框 | 手工构造会触发 selection 对话框的 prompt | 500，错误含"未预期的交互对话框"，session 已关闭 |
| 并发 | 开 3 个并发 `/chat`，等待 semaphore=2 | 2 个先执行，1 个等待 semaphore 释放 |
| 不写 tasks 表 | 调用前后 `SELECT count(*) FROM tasks` | 数字不变 |

**验收标准**：
- [ ] `POST /chat` 返回 200 且 `result` 字段有值
- [ ] 调用完成后 `tmux list-sessions` 无 `chat_*` 残留
- [ ] `/chat` 不写 `tasks` 表（SQLite 中行数不变）
- [ ] `usage` 字段所有子字段非空
- [ ] 与 `/tasks` 共享 semaphore（连续打满 2 个 `/chat` 时，`/tasks` 请求会排队）
- [ ] `/chat` 原有的 `/tasks` / `/tasks/{id}/message` / `/health` 等端点行为**完全不变**（回归测试）

**完成后检查点**：确认以上验收全部通过后再进入 Step 2。

---

### Step 2: jettask 侧 `PlannerClient` + schema 建表

**目标**：jettask 能够通过 HTTP 调用 planner `/chat`，并有审计日志表。

**涉及文件**：
- `src/infrastructure/ai/planner_client.py` — 新建，按 §4.4.1 实现
- `src/infrastructure/ai/__init__.py` — 导出 `PlannerClient`, `PlannerError`, `PlannerRateLimitError`, `PlannerTimeoutError`
- `schema/02_extraction.sql` — 新增 `ft_ai_cli_log` 表（§3.5）
- `src/domain/extraction/repositories/ai_cli_log_repository.py` — 新建 CRUD
- `tests/unit/infrastructure/ai/test_planner_client.py` — 单测（mock httpx）
- `tests/integration/test_planner_client_e2e.py` — 集成测试（真打 localhost:8899）

**实现要点**：

1. `PlannerClient` 用 httpx 而不是 requests——事件抽取未来可能改异步，httpx 同时支持 sync/async
2. `http_timeout` 默认 300s，比服务端 `/chat` 的默认 `timeout=180` 大 120s（给启动+收尾留余量）
3. `extract_json` 用首个 `{` 到末尾 `}` 的区间解析，能容错 markdown 代码块包裹（` ```json ... ``` `）
4. 单测必须覆盖：200 正常路径、429 rate limit → `PlannerRateLimitError`、504 超时 → `PlannerTimeoutError`、500 → `PlannerError`、网络错误 → `PlannerError`
5. **易错点**：`PlannerClient` 要有 `health()` 方法，`EventExtractionService` 在 tick 入口调用 `health()` 失败时直接 skip tick（避免 tmux 挂了还重试 60 次）

**测试用例**：

| 用例 | 输入/操作 | 预期结果 |
|------|-----------|----------|
| health check | `client.health()` planner 运行中 | `True` |
| health check 失败 | 关闭 planner 后 `client.health()` | `False`，无异常抛出 |
| 基本 chat | `client.chat("输出 JSON: {\"a\":1}", timeout=60)` | 返回 dict 含 `result`、`session_uuid`、`usage` |
| rate limit | mock 429 | 抛 `PlannerRateLimitError` |
| 超时 | mock 504 | 抛 `PlannerTimeoutError` |
| HTTP timeout | mock ReadTimeout | 抛 `PlannerTimeoutError` |
| extract_json | 输入 `"abc {\"x\":1} def"` | 返回 `{"x":1}` |
| extract_json 无 JSON | 输入 `"plain text"` | 抛 `ValueError` |

**验收标准**：
- [ ] `planner_client.py` 单测 100% 覆盖 4 种错误路径
- [ ] 集成测试（真调 planner）通过，能成功返回 JSON
- [ ] `ft_ai_cli_log` 表创建成功，包含 §3.5 所有字段
- [ ] `AiCliLogRepository.insert()` 单测通过

**完成后检查点**：确认以上验收全部通过后再进入 Step 3。

---

### Step 3: 通用事件抽取 `EventExtractionService`

**目标**：LLM 驱动的事件抽取主流程，每批独立调用 `/chat`，入库 `ft_graph_nodes`。

**涉及文件**：
- `src/domain/extraction/services/event_extraction.py` — 新建（§4.4.2）
- `src/domain/extraction/services/rule_filter.py` — 新建（简单黑名单）
- `src/domain/extraction/ai/prompts/extraction.md` — system prompt
- `src/domain/extraction/ai/prompts/extraction_ref.md` — 详细规则
- `src/domain/extraction/repositories/graph_node_repository.py` — 新建
- `schema/02_extraction.sql` — 新增 `ft_graph_nodes` 表（§3.1）
- `src/application/services/extraction_app_service.py` — 新建 `run_extraction_tick()`
- `tests/integration/test_event_extraction_e2e.py`

**实现要点**：

1. **预筛选规则**（`rule_filter.py`）：仅黑名单
   - 标题长度 < 5 字：drop
   - 正文长度 < 50 字：drop
   - 标题含纯播报关键词（"盘前回顾"、"今日涨停板"）：drop
   - 其余全部放行（让 LLM 判断）

2. **后端重写 node id**：
   ```python
   def _hash_node(node) -> str:
       import hashlib
       norm = (node["label"].strip() + "|" + node.get("summary", "").strip()[:200]).lower()
       return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]
   ```
   理由：LLM 容易幻觉 id；后端强制重写保证幂等

3. **`validate_extraction` 失败重试 1 次**：第二次 prompt 中附上错误信息（见 `_make_user_message(batch, retry_errors=...)`）

4. **Tick 预算**：`TICK_BUDGET_SEC = 13 * 60`，超时停止，剩余留下个 tick；**关键**：这是 `time.time() - tick_start > budget` 判断，每批循环前检查一次

5. **rate limit 中止 tick**：捕获 `PlannerRateLimitError` 后 `break`（不是 continue），整个 tick 停止，等 15 min 后下个 tick 重试

6. **幂等**：`node_repo.bulk_upsert` 用 `INSERT ... ON CONFLICT (id) DO UPDATE`，重复抽取同一新闻时更新而不新增

7. **易错点**：
   - `body[:3000]` 截断避免超长 prompt（3000 字节约 1000 tokens）
   - `news_repo.list_unextracted` 必须返回按 `published_at` 升序的列表（先处理老的）
   - `validate_extraction` 返回的是错误 list，空 list 表示通过
   - 单条失败不影响整批：`validate` 二次失败时抛 `ValueError`，外层 catch 后 `continue`

**测试用例**：

| 用例 | 输入/操作 | 预期结果 |
|------|-----------|----------|
| 正常抽取 | 5 条真实央行降息新闻 | `processed=5`, `ft_graph_nodes` 新增 ≥ 5 行 |
| 纯播报过滤 | 5 条"今日沪指收涨 X%"新闻 | LLM 返回 `nodes=[]`, `ft_graph_nodes` 不新增，但 `ft_ai_cli_log` 有 success=True 记录 |
| 一篇多事件 | 1 条政策 + 2 条受益行业新闻混合 | `ft_graph_nodes` 新增 ≥ 3 条（跨 event_type） |
| validate 失败重试 | mock LLM 首次返回缺 `label` 字段 | 重试一次后通过，日志有 "validate 失败重试" |
| tick 超时 | mock 每批 5 min，填充 10 批 | 第 3 批后停止（13min），`remaining > 0` |
| rate limit 中止 | mock 第 2 批 429 | tick 返回 `processed=1`，日志有 "rate limited, 中止 tick" |
| 幂等 | 同一批新闻跑两次 | 第二次 `processed=5` 但 `ft_graph_nodes` 不重复 |

**验收标准**：
- [ ] `validate_extraction` 通过率 ≥ 95%（抽样 100 条人工复核）
- [ ] 跨类别文章能同时产出多个 event_type 不同的事件
- [ ] 单 tick 平日能处理 ≥ 30 条
- [ ] `ft_graph_nodes.session_uuid` 每批不同（验证"每批独立"生效）
- [ ] `ft_ai_cli_log` 按 tick 维度行数匹配（处理 N 批 → 至少 N 行）
- [ ] 积压 > 100 时 batch_size 自动降到 3

**完成后检查点**：确认以上验收全部通过后再进入 Step 4。

---

### Step 4: 关系图抽取 `EventGraphService.extract_relations_tick`

**目标**：给事件节点织网，`graphify.build` 构图。

**涉及文件**：
- `src/domain/extraction/services/event_graph_service.py` — 新建（`extract_relations_tick` 方法）
- `src/domain/extraction/ai/prompts/relation.md`
- `src/domain/extraction/repositories/graph_edge_repository.py`
- `schema/02_extraction.sql` — 新增 `ft_graph_edges` 表（§3.2）
- `src/application/services/extraction_app_service.py` — `run_relation_tick()`

**实现要点**：
- 每 20 个事件节点一批（比 extraction 大，因为关系抽取只看 label+summary，token 密度低）
- `relation` 类型严格约束在 `causal/temporal/thematic/contradicts`
- `confidence` 必须是 `EXTRACTED`/`INFERRED`/`AMBIGUOUS` 三选一
- edges 的 `UNIQUE(source, target, relation)` 冲突时 ON CONFLICT DO UPDATE confidence_score（取较高值）
- `source`/`target` 必须都在 `ft_graph_nodes` 中存在（外键检查）——LLM 幻觉的 id 丢弃并记 warning

**测试用例**：

| 用例 | 输入/操作 | 预期结果 |
|------|-----------|----------|
| 基本关系 | 20 个最近事件 | `ft_graph_edges` 新增，各 `relation` 类型都有 |
| id 幻觉过滤 | mock LLM 返回不存在的 target id | 该 edge 丢弃，日志 warning |
| 单 tick 耗时 | 24h 窗口 | ≤ 10 min |
| graphify.build 成功 | `build_from_json({nodes, edges})` | 返回 NetworkX DiGraph |

**验收标准**：
- [ ] 单 tick（24h 窗口）≤ 10 min
- [ ] `graphify.build.build_from_json` 无报错
- [ ] `ft_graph_edges` 日均新增 ≥ 100 条
- [ ] id 幻觉被安全丢弃（不会导致外键错误）

**完成后检查点**：通过后进入 Step 5。

---

### Step 5: 事件主线 `EventGraphService.cluster_latest`（v10 增强）

**目标**：完整的 7 步聚类流水线，产出带血缘追踪的"市场主线"。

**涉及文件**：
- `src/domain/extraction/services/event_graph_service.py` — 新增 `cluster_latest` 方法（§4.4.3）
- `src/domain/extraction/graph/graphify_adapter.py` — adapter 层（§4.4.4）
- `src/domain/extraction/graph/time_decay.py` — 时间衰减加权器
- `src/domain/extraction/graph/semantic_edges.py` — 语义虚拟边注入器
- `src/domain/extraction/graph/lineage_tracker.py` — 主线血缘追踪
- `schema/02_extraction.sql` — 新增 `ft_event_communities` 表（§3.3，含 lineage_id + cluster_run_id）
- `src/application/services/extraction_app_service.py` — `run_cluster_tick()`

**实现要点**：

1. 不走 LLM（纯算法 + 可能调 embedding 服务做语义边）
2. 72h 窗口
3. `graspologic` 必须已安装（依赖 `graphify` 的 extras）
4. **7 步流水线**：build → time_decay → semantic_edges → cluster → lineage_match → lifecycle_advance → write_back
5. 时间衰减半衰期配置（按 event_type 分档）：
   - `policy` / `macro`：60 天
   - `industry`：30 天
   - `company` / `capital`：14 天
   - `rumor`：7 天
6. 语义虚拟边：embedding 余弦相似度 >0.85 的节点对注入 `edge_type='semantic'` 边，weight 为显式边的 0.3
7. 主线血缘：Jaccard 相似度 ≥0.4 匹配上一轮社区，延续 lineage_id
8. 结果去重：同一 `cluster_run_id` 下的 community 全量重写

**验收标准**：
- [ ] 单 tick（72h 窗口）≤ 5 min
- [ ] `graspologic` 可 import，无 native 库错误
- [ ] `ft_event_communities` 每小时刷新，每次覆盖上一次
- [ ] 时间衰减生效：72h 前的边 weight < 近期边 weight
- [ ] 语义虚拟边：无显式边但语义相关的节点对被聚类到同一社区
- [ ] 主线血缘：`lineage_id` 跨 tick 稳定（同一主线不同 run 的 lineage_id 相同）
- [ ] `GraphifyAdapter` 是唯一 import graphify 的文件（`grep -r "from graphify" src/` 仅匹配 adapter）

**完成后检查点**：通过后进入 Step 6。

---

### Step 6: 情感分类 `SentimentClassifier`

**目标**：给 `ft_news` / `ft_sentiment` 打情感标签，替换现有规则分类。

**涉及文件**：
- `src/domain/extraction/services/sentiment_classifier.py` — 新建
- `src/domain/extraction/ai/prompts/sentiment.md` — system prompt
- `src/domain/extraction/repositories/sentiment_label_repository.py`
- `schema/02_extraction.sql` — 新增 `ft_sentiment_labels` 表（§3.4）

**实现要点**：
- `model="haiku"`（够用且快）
- 每批 20 条（纯文本短任务）
- timeout=60s
- 幂等：`UNIQUE(source_table, source_id)`

**验收标准**：
- [ ] 批量 20 条 ≤ 20s
- [ ] 人工抽样准确率 ≥ 85%
- [ ] `ft_sentiment_labels` 覆盖率 ≥ 80%

**完成后检查点**：通过后进入 Step 7。

---

### Step 7: 下游切换（待优化 1.4 / 2.2 / 2.6 / 3.4 / 3.5）

**目标**：把原有规则型情感/主题分类切到新表。

**涉及文件**：
- `src/domain/extraction/queries/extraction_query_service.py` — 新建查询接口
- `src/domain/collection/services/macro.py` — 替换政策方向来源
- `src/domain/collection/services/market.py` — 替换主题标注来源
- `src/domain/collection/services/sentiment.py` — 替换情感分类来源

**实现要点**：
- 保持原有接口签名不变，只换数据源
- 历史数据用规则型，新数据用 AI 型
- 兜底：`ft_graph_nodes` / `ft_sentiment_labels` 查询失败时降级规则

**验收标准**：
- [ ] 4 个下游切换完成
- [ ] 回归测试：历史日期查询结果不变
- [ ] 新日期查询走新表，结果语义合理

---

## 6. 总体验收标准

- [ ] **planner `/chat` 端点独立可用**（能被 jettask 以外的客户端调用）
- [ ] planner `/tasks` 相关端点行为完全不受影响（回归测试通过）
- [ ] jettask 侧**无 tmux 代码**，只依赖 httpx
- [ ] 每批独立（`ft_graph_nodes.session_uuid` 在一个 tick 内各不相同）
- [ ] 日均 `ft_graph_nodes` 新增 ≥ 200 条
- [ ] 日均 `ft_graph_edges` 新增 ≥ 100 条
- [ ] `ft_event_communities` 每小时刷新，`lineage_id` 跨 tick 稳定
- [ ] `ft_sentiment_labels` 覆盖率 ≥ 80%
- [ ] 4 个下游待优化项完成切换
- [ ] `ft_ai_cli_log` 全量覆盖（每批一条），`session_uuid` 可追溯到 `~/.claude/projects/*/{uuid}.jsonl`
- [ ] **`GraphifyAdapter` 是唯一 import graphify 的文件**（`grep -r "from graphify" src/` 仅匹配 adapter）
- [ ] 时间衰减、语义虚拟边、主线血缘 3 个自研介入点在聚类流水线中显式执行
- [ ] 单测覆盖率 ≥ 70%
- [ ] **平日单 tick 延迟 ≤ 5 min，极端高峰 ≤ 30 min**

---

## 7. 风险与注意事项

### 7.1 planner API 可用性风险

**风险**：planner API 崩溃 → 事件抽取全部失败。

**对冲**：
1. jettask tick 入口调 `PlannerClient.health()`，失败时 skip tick 并告警（不重试 60 次 LLM 调用白烧成本）
2. planner 用 `systemd` / `supervisord` 托管，崩溃自动重启
3. 监控：tmux session 累积告警（`tmux list-sessions | wc -l > 10` 时告警，可能有泄漏）
4. **启动脚本**：jettask worker 启动时检查 planner 是否就绪（`curl localhost:8899/health`），未就绪等待 10s 后重试 3 次

### 7.2 tmux session 泄漏

**风险**：`/chat` 请求 `session.close()` 未调 → tmux session 残留，多次后 tmux 资源耗尽。

**对冲**：
1. `/chat` 端点用 `try/finally` 双保险：`except` 记错，`finally` 强制 `close()`
2. planner 启动时清理历史残留：`tmux list-sessions -F '#{session_name}' | grep '^chat_' | xargs -r -n1 tmux kill-session -t`
3. 定时 cron：每小时清理 > 30 min 无活动的 chat_* session（idle 检测用 `tmux display-message -t NAME -p '#{session_activity}'`）

### 7.3 并发挤占

**风险**：事件抽取的 `/chat` 占满 semaphore（MAX_CONCURRENT=2）→ planner skill 的 `/tasks` 请求排队。

**对冲**：
1. 事件抽取 tick 都是后台任务，能等；planner skill 是人机交互任务，等 1-2 分钟可接受
2. 未来若明显冲突：把 `MAX_CONCURRENT` 提到 3，或分两个 semaphore（chat vs tasks 各 2）——**本版本不做**

### 7.4 HTTP 长连接超时

**风险**：`/chat` 耗时 > httpx client 的 timeout → 客户端超时但服务端仍在执行。

**对冲**：
1. 客户端 `http_timeout=300`，服务端 `timeout=180`，余量 120s
2. 服务端超时走 `session.send_ctrl_c()`，24 路径的 504 响应
3. **关键**：客户端收到 `PlannerTimeoutError` 不要重试，因为服务端可能已经消费了 Claude 额度

### 7.5 Claude CLI 层面

1. **Rate limit**：/chat 返回 429 时调用方 break 当前 tick，15 min 后下个 tick 重试（符合 rate limit 恢复时间）
2. **Session 文件累积**：`~/.claude/projects/*/xxx.jsonl` 会累积。**对冲**：cron 删 7 天前的文件
3. **`--setting-sources ""` 必须保留**（避免 CC Switch 干扰 env 注入），planner 既有逻辑已处理

### 7.6 每批独立调用特有风险

1. **无显式 cache 复用**：每批 cache write，月增量 < $1。**可接受**
2. **启动开销**：每批 3-5 秒 tmux + claude CLI 启动。**已评估**：占比仅 4%，不值得为了优化引入质量风险
3. **Anthropic 服务端内容 cache 是否命中**：依赖内容 hash，system prompt 文本完全一致即可。**验证**：观察连续调用的 `cache_read_tokens` 是否 > 0

### 7.7 graphify 依赖层面

1. **schema 版本**：固定 graphify commit（requirements.txt 写死 commit hash）
2. **graspologic 安装**：生产镜像预装
3. **dangling edges**：validate 报 warning 时记日志但不丢弃
4. **adapter 层隔离**：graphify API 变动只改 `graphify_adapter.py` 一个文件，业务代码零影响
5. **file_type 冗余**：统一写死 `'document'`，不进入业务判断逻辑
6. **社区 ID 随机性**：Leiden 每次产出的 community_id 不同，必须用 lineage_id 做跨时间追踪

### 7.8 时间衰减与语义虚拟边风险

1. **半衰期参数敏感性**：衰减过快会导致活跃社区碎片化，过慢则旧事件噪声太大。**对冲**：半衰期配置化（YAML/env），初期用保守值，上线后根据聚类质量调整。
2. **embedding 质量依赖**：语义虚拟边依赖 embedding 模型质量。**对冲**：threshold 0.85 足够高（误补概率低），上线后观察虚拟边占比（应 <20% 的总边数）。
3. **Jaccard 阈值**：0.4 偏低可能导致不相关社区被错误关联。**对冲**：上线后人工抽样 lineage 匹配准确率，不达标时调高到 0.5。

### 7.9 数据模型独立性（Opus 建议）

数据是系统的永久资产，代码可以换，数据不能换。核心原则：

1. **持久层的 Pydantic 模型和 graphify 的 Pydantic 模型应该是两套**，在 adapter 层做转换
2. `ft_graph_nodes` / `ft_graph_edges` 是金融领域的数据模型，graphify 的字段（file_type/source_file）只是适配层
3. 新增业务字段不需要和 graphify 协调（graphify 的"额外字段自动透传"保证了这一点）

---

## 8. 附录：吞吐量详细计算

### 平日场景

| 参数 | 值 |
|------|-----|
| 日均新闻 | 300 条 |
| 每个 tick 平均 | 300 / 96 = 3.125 条 |
| 单批处理时间 | 2 min（~5s HTTP + tmux 启动 + ~110s LLM + ~5s validate/入库） |
| 15 min 能处理 | 7 批 = 35 条 |
| **结论** | **远超需求，单处理流程够用** |

### 高峰场景

| 参数 | 值 |
|------|-----|
| 早盘 9:30-10:00 新闻 | 80 条（2 个 tick） |
| 每个 tick 平均 | 40 条 |
| batch_size = 5 | 能处理 35 条，积压 5 条 |
| batch_size = 3（降级） | 能处理 55 条，完全处理 |
| **结论** | **降级后能应对** |

### 极端场景

| 参数 | 值 |
|------|-----|
| 突发重大政策 | 单小时 150 条新闻 |
| 单 tick 能处理 | 35 条 (normal) / 55 条 (degraded) |
| 降级后每 tick 积压 | 150/4 - 55 = -17 条（无积压） |
| **结论** | **降级后能处理完** |

---

## 9. 与 v8/v9 的变更对照

| 变更项 | v8（jettask 自建 tmux） | v9（走 planner `/chat`） | **v10（+ graphify adapter 层）** |
|-------|-------------------------|-------------------------|-------------------------------|
| 基础设施类 | `ClaudeCliOneShot` + 复制 8 个纯函数 | `PlannerClient`（纯 HTTP） | 同 v9 + `GraphifyAdapter` |
| jettask 代码量 | ~300 行 | ~50 行 | ~150 行（+ adapter + 3 个自研模块） |
| graphify 依赖 | 直接 import 4 个函数 | 直接 import 4 个函数 | **adapter 层封装，业务零依赖** |
| 图谱增强 | 无 | 无 | 时间衰减 + 语义虚拟边 + 生命周期 |
| 主线追踪 | community_id（随机） | community_id（随机） | **lineage_id（Jaccard 匹配，稳定）** |
| 事件生命周期 | 无 | 无 | 5 阶段状态机 |
| 数据模型独立性 | 低（graphify schema 定义表结构） | 低 | **高（adapter 隔离，DB 模型独立）** |
| planner 改造 | 无 | ~100 行（新增 `/chat`） | 同 v9（已完成） |
| tmux 依赖 | jettask 镜像需装 | jettask 镜像不需装 | 同 v9 |
| 并发控制 | jettask 独立 | 共享 semaphore | 同 v9 |
| 会话策略 | 每批独立 | 每批独立 | 同 v9 |
| 代码重复 | tmux 工具函数复制一份 | 无重复 | 无重复 |
