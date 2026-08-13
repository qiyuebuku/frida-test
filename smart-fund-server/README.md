# Smart Fund Server

基金智能交易服务端，提供同花顺基金数据查询、交易执行、风控管理、决策引擎等 API。

服务端同时包含基于 OpenAI Agents SDK 的自动化金融研究 Runtime。Agent 通过本服务
提供的 Streamable HTTP MCP 使用知识图谱、市场数据和外部研究工具，不直接访问
PostgreSQL 或 Milvus。

## 项目结构

```
smart-fund-server/
├── main.py                 # 入口，定义 FastAPI app
├── config.json             # 基金池、风控参数、策略配置
├── auth_cache.json         # 同花顺认证缓存（由客户端推送更新）
├── routers/
│   └── __init__.py         # 所有 API 路由（128+ 个接口）
├── services/
│   ├── ths_fund_client.py  # 同花顺基金 API 客户端
│   ├── fund_db.py          # PostgreSQL 数据库操作（持仓/决策/交易记录）
│   ├── risk_manager.py     # 风控硬约束（止损/止盈/仓位/冷却期）
│   ├── indicators.py       # 技术指标计算（RSI/MA/波动率）
│   ├── review_decision_executor.py  # 决策审核执行器
│   ├── ocr_service.py      # OCR 服务
│   ├── llm_service.py      # LLM 集成
│   └── db.py               # OCR 数据库操作
├── handlers/
│   └── screenshot_handler.py  # 截屏分析处理
└── images/                 # 截图存储目录
```

## 环境依赖

- Python 3.12（通过 conda 管理）
- PostgreSQL（数据库名 `jettask`，用户 `jettask`，密码 `123456`）
- 依赖包：`fastapi uvicorn httpx psycopg2-binary pydantic`

## 金融 Agent

检查 Agent 配置、MCP 连接和只读工具：

```bash
python -m src.interfaces.cli agent check
```

执行一次结构化 Research Agent Review：

```bash
python -m src.interfaces.cli agent run \
  --trigger-slot intraday \
  --reason "人工生产式复核" \
  --run-mode debug \
  --json-output
```

`agent run` 不再要求人工准备上下文文件。运行时先通过远程 MCP（模型上下文协议）让服务端
按截止时间组装市场框架、当前报告、有效观点和研究记忆，再启动模型。只有排查旧任务包时才使用
`agent run-context CONTEXT_FILE`。CLI（命令行界面）默认不发布权威状态；自动任务、人工调试
和历史重放统一复用 `FinancialAgentRuntime`（金融智能体运行时）。

设计和实施文档统一位于工作区根目录
`/home/yuyang/frida-test/smart-fund-server/docs`。

## 架构说明

服务端运行在云端，**不依赖手机**。Token 刷新由本地客户端（`client.py`）完成后推送到服务端。

```
┌─────────────┐     push token     ┌──────────────────┐
│  client.py  │ ──────────────────→ │  smart-fund-server│
│ （本地+手机） │                     │  （云端 8900 端口） │
└─────────────┘                     └──────────────────┘
       │                                    │
       │ Zygisk/密码登录                      │ PostgreSQL
       ▼                                    ▼
   同花顺 App                           jettask 数据库
```

## 远程服务器信息

| 项目 | 值 |
|------|-----|
| 服务器 | `119.23.227.187`（公网通过 frp 映射） |
| SSH 端口 | `1113` |
| SSH 用户 | `yuyangruan` |
| 服务端口 | `8900` |
| 公网地址 | `http://119.23.227.187:8900` |
| conda 环境 | `smart-fund` |
| systemd 服务 | `smart-fund-api`、`smart-fund-persist`、`smart-fund-scheduler`、采集 Worker（工作进程）、三个 KG Worker（知识图谱工作进程）、`smart-fund-etcd`、`smart-fund-milvus` |
| 项目目录 | `/home/yuyangruan/smart-fund/smart-fund-server` |
| 日志目录 | `/home/yuyangruan/smart-fund/logs/smart-fund-server` |

### frp 端口映射

公网 `119.23.227.187:8900` 实际通过 frp 转发到内网 `10.168.1.210:8900`：
- frpc 配置：`119.23.227.187:2222` 机器上的 `/home/yuyangruan/frp_0.57.0_linux_amd64/frpc_smart_fund_8900.toml`
- 由 supervisorctl 管理，进程名 `smart_fund_8900`

公网 `119.23.227.187:8901` 实际通过 frp 转发到内网 Embedding 服务 `10.168.1.210:8901`：
- frpc 配置：`119.23.227.187:2222` 机器上的 `/home/yuyangruan/frp_0.57.0_linux_amd64/frpc_smart_fund_8901.toml`
- 由 supervisorctl 管理，进程名 `smart_fund_8901`

## 部署

部署文件统一位于 `deployment/`，命令仍从项目根目录执行：

```bash
cd /home/yuyang/frida-test/smart-fund-server
./deployment/deploy_113.sh
```

本地 sudo 凭据保存在 Git 忽略且不会被 `rsync` 上传的
`deployment/.deployment.local.env`：

```bash
REMOTE_SUDO_PASSWORD=...
```

环境变量模板位于 `deployment/.env.example`，实际运行配置仍写入项目根目录 `.env`。

日常部署会同步代码、更新 systemd unit、重启应用服务并执行完整健康检查。它复用生产机已有的 `smart-fund` Conda 环境，不会重新创建环境或下载依赖；Milvus 已运行时也不会因普通代码发布而重启。

首次初始化或显式更新依赖时使用：

```bash
./deployment/deploy_113.sh --init
./deployment/deploy_113.sh --deps
```

常用运维命令：

```bash
./deployment/deploy_113.sh --sync-only
./deployment/deploy_113.sh --restart
./deployment/deploy_113.sh --status
./deployment/deploy_113.sh --logs worker 200
./deployment/deploy_113.sh --test
```

## 排查问题

### 服务启动失败

```bash
./deployment/deploy_113.sh --status
./deployment/deploy_113.sh --logs api 200

ssh -p 1113 yuyangruan@119.23.227.187
cd /home/yuyangruan/smart-fund/smart-fund-server
/home/yuyangruan/anaconda3/envs/smart-fund/bin/python -m src.interfaces.cli api
```

### 公网无法访问

排查顺序：
1. **服务本身是否正常**：SSH 登录后 `curl http://127.0.0.1:8900/health`
2. **embedding 服务是否正常**：`curl http://119.23.227.187:8901/health`
3. **frp 是否正常**：检查跳板机上的对应 frpc 服务状态
4. **端口是否开放**：检查云服务商安全组是否放行 8900 / 8901 端口

### Token 过期 / 认证失败

Token 由客户端（`client.py`）刷新并推送到服务端。如果认证失败：
1. 检查认证状态：`curl http://119.23.227.187:8900/api/auth/status`
2. 在本地运行客户端刷新 Token：`python client.py refresh-token`
3. 确认 `auth_cache.json` 中的 token 未过期

### 数据库连接失败

```bash
pg_isready -h 10.168.1.113 -p 5432
```

### 新增 Python 依赖

```bash
ssh -p 1113 yuyangruan@119.23.227.187
/home/yuyangruan/anaconda3/envs/smart-fund/bin/pip install <package>
```

## 注意事项

1. 本地开发使用 Conda 环境 `frida-test`；生产使用 Conda 环境 `smart-fund`。
2. 普通部署不会安装依赖。只有依赖声明变化时才显式运行 `--deps`。
3. 不要在服务端直接改代码；后续 rsync 会覆盖远程改动。
4. `.env`、`data/`、`logs/` 和其他运行时文件不会随代码同步。
5. 服务由 systemd 守护，排障时应使用 systemctl 或部署脚本，而不是直接 kill 进程。
