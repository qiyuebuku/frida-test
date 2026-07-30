# AGENTS.md — frida-test workspace

当前 Smart Fund 主项目位于：

- `/home/yuyang/frida-test/smart-fund-server`

Smart Fund 的设计、实施、使用和接口文档统一存放在：

- `/home/yuyang/frida-test/smart-fund-server/docs`

金融 Agent 已合并进 `smart-fund-server`，文档随主项目维护，不再放在工作区根目录。

修改工作区代码时，必须遵守：

- `/home/yuyang/frida-test/CLAUDE.local.md`
- 本文件中对应组件的约束

## smart-fund-server

修改 `smart-fund-server` 时，还必须遵守：

- `/home/yuyang/frida-test/smart-fund-server/AGENTS.md`

其中包含 Milvus / PyMilvus 编码约束：统一使用 `MilvusClient`、使用
`DataType` 枚举、优先 `target_id` 作为 Milvus `VARCHAR` 主键、索引刷新使用
`upsert()`、PG 不保存完整 chunk/card text、Milvus 负责语义搜索和按 ID
精准取回。

## 金融 Agent 模块

金融 Agent 已合并到
`smart-fund-server/src/application/agents/financial_research`：

- 生产 Agent Runtime 统一使用 OpenAI Agents SDK。
- 数据库、Milvus、采集、检索、图谱和市场数据能力必须保留在
  `smart-fund-server`。
- Agent 只通过服务端提供的远程 Streamable HTTP MCP 使用业务能力，不得直接
  访问数据库或 Milvus。
- 自动任务、人工调试和历史重放必须复用 `FinancialAgentRuntime`，不得实现
  第二套模型工具循环。
- 默认只暴露读取工具；写工具必须由本次运行显式授权。
- 事实结论必须引用实际打开的 Card 或正式外部/市场数据。
- 关系结论必须引用实际打开的 Edge，并区分 `observed` 与 `inferred`。
- Community 只能用于导航和确定研究范围，不能单独证明 Card 之间的关系。
- Agent 输出进入业务存储前必须通过确定性的证据引用和状态校验。

## Langfuse

`scripts/fetch_langfuse_traces.py` 可以用于拉取 Langfuse Trace 记录。
