# Smart Fund Server

基金智能交易服务端，提供同花顺基金数据查询、交易执行、风控管理、决策引擎等 API。

## 项目结构

```
smart-fund-server/
├── main.py                 # 入口，定义 FastAPI app
├── config.json             # 基金池、风控参数、策略配置
├── auth_cache.json         # 同花顺认证缓存（由客户端推送更新）
├── deploy.sh               # 自动化部署脚本
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
| SSH 端口 | `2210` |
| SSH 用户 | `yuyangruan` |
| 服务端口 | `8900` |
| 公网地址 | `http://119.23.227.187:8900` |
| conda 环境 | `smart-fund` |
| systemd 服务 | `smart-fund-server` |
| 项目目录 | `/home/yuyangruan/smart-fund-server` |
| 日志文件 | `/home/yuyangruan/smart-fund-server/server.log` |

### frp 端口映射

公网 `119.23.227.187:8900` 实际通过 frp 转发到内网 `10.168.1.210:8900`：
- frpc 配置：`119.23.227.187:2222` 机器上的 `/home/yuyangruan/frp_0.57.0_linux_amd64/frpc_smart_fund_8900.toml`
- 由 supervisorctl 管理，进程名 `smart_fund_8900`

## 部署

### 首次部署

```bash
./deploy.sh --init
```

这会自动完成：创建 conda 环境 → 安装依赖 → 安装 PostgreSQL → 建库 → 同步代码 → 安装 systemd 服务 → 启动

### 日常部署（改代码后）

```bash
# 同步代码 + 自动重启（最常用）
./deploy.sh

# 只同步代码不重启（改了非 Python 文件时）
./deploy.sh --sync-only

# 只重启不同步（远程直接改了代码时）
./deploy.sh --restart
```

部署脚本会自动：
1. 通过 rsync 同步本地代码到远程（排除 `__pycache__`、`images/*`、`server.log`、`.git`）
2. 通过 systemctl 重启服务
3. 等待 2 秒后调用 `/health` 验证启动成功

### 手动部署（如果 deploy.sh 不可用）

```bash
# SSH 登录
ssh -p 2210 yuyangruan@119.23.227.187

# 同步代码（从本地）
rsync -avz --delete \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='images/*' \
    --exclude='server.log' --exclude='.git' \
    -e "ssh -p 2210" \
    ./  yuyangruan@119.23.227.187:/home/yuyangruan/smart-fund-server/

# 远程重启
echo '199848' | sudo -S systemctl restart smart-fund-server
```

## 运维命令

```bash
# 查看服务状态（systemd + 健康检查 + 认证状态）
./deploy.sh --status

# 查看最近日志（默认 50 行）
./deploy.sh --logs
./deploy.sh --logs 200

# 远程健康检查
./deploy.sh --test

# 手动 SSH 登录排查
ssh -p 2210 yuyangruan@119.23.227.187
```

## 排查问题

### 服务启动失败

```bash
# 1. 查看 systemd 状态
./deploy.sh --status

# 2. 查看详细日志
./deploy.sh --logs 200

# 3. SSH 登录后手动启动看报错
ssh -p 2210 yuyangruan@119.23.227.187
cd /home/yuyangruan/smart-fund-server
/home/yuyangruan/anaconda3/envs/smart-fund/bin/python -c "from main import app; print('OK')"
/home/yuyangruan/anaconda3/envs/smart-fund/bin/uvicorn main:app --host 0.0.0.0 --port 8900
```

### 公网无法访问

排查顺序：
1. **服务本身是否正常**：SSH 登录后 `curl http://127.0.0.1:8900/health`
2. **frp 是否正常**：SSH 到 `119.23.227.187:2222`，执行 `echo '199848' | sudo -S supervisorctl status smart_fund_8900`
3. **端口是否开放**：检查云服务商安全组是否放行 8900 端口

### Token 过期 / 认证失败

Token 由客户端（`client.py`）刷新并推送到服务端。如果认证失败：
1. 检查认证状态：`curl http://119.23.227.187:8900/api/auth/status`
2. 在本地运行客户端刷新 Token：`python client.py refresh-token`
3. 确认 `auth_cache.json` 中的 token 未过期

### 数据库连接失败

```bash
# SSH 登录后检查 PostgreSQL
echo '199848' | sudo -S systemctl status postgresql
psql -h 127.0.0.1 -U jettask -d jettask -c "SELECT 1"
# 密码: 123456
```

### 新增 Python 依赖

```bash
ssh -p 2210 yuyangruan@119.23.227.187
/home/yuyangruan/anaconda3/envs/smart-fund/bin/pip install <package>
```

## 注意事项

1. **不要在服务端直接改代码** — 下次 `deploy.sh` 会用 `rsync --delete` 覆盖远程改动
2. **`auth_cache.json` 不会被同步覆盖** — rsync 会同步此文件，但 Token 由客户端推送更新，部署后需要重新 `refresh-token`
3. **`config.json` 会被同步** — 本地修改基金池/风控参数后部署即可生效
4. **`images/` 目录不同步** — 远程的截图文件不会被删除
5. **`server.log` 不同步** — 日志只在远程存在，通过 `--logs` 查看
6. **WSL2 的 SSH key 路径特殊** — deploy.sh 会自动从 `/mnt/c/Users/阮雨阳/.ssh/id_rsa` 复制到 `/tmp/` 并修正权限
7. **服务由 systemd 守护** — kill 进程后会自动重启（`Restart=always`），必须用 `systemctl stop` 停止
