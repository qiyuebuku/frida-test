# 同花顺基金 API 服务

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  PC (server.py)                                         │
│                                                         │
│  1. 启动时从 auth_cache.json 加载认证参数               │
│  2. 直接向 trade.5ifund.com 发送 HTTPS 请求             │
│  3. 后台任务每 30 分钟检查 token 是否过期               │
│  4. 只有在 token 过期时才需要连接手机刷新               │
└─────────────────────────────────────────────────────────┘
                        │
                 正常情况：直接 HTTPS
                        ↓
┌─────────────────────────────────────────────────────────┐
│  同花顺服务器 (trade.5ifund.com)                        │
└─────────────────────────────────────────────────────────┘
```

**核心原则**：正常使用时不需要连接手机，只有在 token 过期需要刷新时才连接。

---

## 服务管理

### systemctl 服务

服务名称：`ths-fund-server.service`

```bash
# 查看状态
sudo systemctl status ths-fund-server

# 启动/停止/重启
sudo systemctl start ths-fund-server
sudo systemctl stop ths-fund-server
sudo systemctl restart ths-fund-server

# 查看日志
tail -f /home/yuyang/frida-test/ths/api/server.log

# 开机自启
sudo systemctl enable ths-fund-server
```

### 手动启动（开发/调试）

```bash
cd /home/yuyang/frida-test/ths/api

# 前台运行（带热重载）
uvicorn server:app --host 0.0.0.0 --port 8900 --reload

# 后台运行
nohup uvicorn server:app --host 0.0.0.0 --port 8900 > server.log 2>&1 &
```

---

## Token 刷新

### 触发时机

后台任务每 30 分钟检查一次，只有在 token 即将过期（提前 3 天）时才触发刷新。

### 刷新策略

1. **Zygisk 优先**：需要手机连接 + App 运行
2. **密码登录兜底**：会踢掉手机端

### 手动刷新 Token

```bash
# 1. 设置端口转发
./setup_port_forward.sh

# 2. 打开手机上的同花顺 App → 交易 → 基金
#    Zygisk 会自动捕获 token

# 3. 验证
curl http://localhost:18900/auth | jq .
```

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `server.py` | FastAPI 服务入口 |
| `ths_fund_client.py` | API 客户端封装 |
| `auth_cache.json` | 认证参数缓存 |
| `config.json` | 配置（账号密码） |
| `server.log` | 服务日志 |

---

## API 端点

```bash
# 交易记录
GET /api/trade/orders?days=30&op_type=all&limit=20&offset=1

# 持仓
GET /api/trade/positions

# 总览
GET /api/trade/overview
```

---

## 故障排查

### 502 Bad Gateway

1. 检查 `auth_cache.json` 是否存在且有效
2. 检查 token 是否过期（查看 `expires_at` 字段）
3. 手动刷新 token

### Token 过期

```bash
# 方法 1: 连接手机刷新
./setup_port_forward.sh
# 打开手机 App

# 方法 2: 密码登录（会踢掉手机）
# 确保 config.json 中配置了账号密码
```

### 端口被占用

```bash
# 查看占用进程
lsof -i :8900

# 杀掉进程
fuser -k 8900/tcp
```



这是日志的目录：
/home/yuyang/frida-test/ths/api/server.log