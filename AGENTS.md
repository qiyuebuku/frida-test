# AGENTS.md — frida-test workspace

当前 Smart Fund 主项目位于：

- `/home/yuyang/frida-test/smart-fund-server`

Smart Fund 的设计、实施、使用和接口文档统一存放在：

- `/home/yuyang/frida-test/smart-fund-server/docs`

金融 Agent 已合并进 `smart-fund-server`，文档随主项目维护，不再放在工作区根目录。

修改工作区代码时，必须遵守：

- `/home/yuyang/frida-test/CLAUDE.local.md`
- 本文件中对应组件的约束

## 安卓逆向服务的本地优先开发与部署门禁

涉及安卓逆向、Hook、Redroid、交易实例或相关运行时的开发和部署时，必须遵守：

1. 开发和调试必须先在非生产环境完成，不得把生产容器、生产数据或 GitHub Actions 当作日常调试环境。
2. 本地验证环境可以根据任务选择：
   - 通过 ADB 连接的真实 Android 设备；
   - 本地 Docker 中运行的 Android / Redroid 实例。
   - 当本地内核不支持 Android Binder 时，可以使用部署在生产服务器上的隔离开发环境；该环境仍属于开发环境，不得复用生产容器、生产数据卷、生产端口或生产交易会话。
3. 必须使用本次工作树构建出的代码，在本地执行与改动相关的真实功能测试；不得用 mock、旧镜像、旧 APK 或仅检查进程存活代替功能验证。
4. 涉及交易实例时，至少要验证实际账号密码登录、权威会话状态，以及本次改动涉及的资金、持仓、委托等真实只读链路；不能仅凭登录回调或日志中的 `success` 判定测试通过。
5. 只有真机、本地 Docker 或服务器隔离开发环境中的测试通过后，才允许提交并推送代码；生产部署只能由代码进入 `main` 后触发 GitHub Actions 执行，禁止从任何开发环境直接部署或修改生产。
6. GitHub Actions 用于生产构建、部署和最终验收，不承担开发阶段的反复试错。Action 或生产验收失败后，应优先回到本地复现和修复，再重新提交。
7. 如果本地真机和 Docker 的运行特性可能导致不同结果，应选择与生产最接近的 Docker / Redroid 做最终本地门禁；真机可用于快速调试、抓取调用链和验证 Hook。
8. 如果当前本地环境因 Binder、虚拟化、设备连接等原因无法执行必要测试，必须明确报告阻塞，不能把未经本地验证的版本标记为完成或直接推送生产。
9. 服务器隔离开发环境允许绕过 GitHub Actions，直接同步当前工作树、构建镜像并替换开发实例，以缩短调试周期；这一权限仅限开发命名空间。开发实例必须使用独立的容器名、网络、端口、数据卷、Secret 目录和 Android 设备身份，并设置明确的资源上限。

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
