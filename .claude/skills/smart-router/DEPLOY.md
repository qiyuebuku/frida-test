# Smart API Router 部署指南

## 架构

```
任意电脑 Claude Code                        远端服务器
┌─────────────────────┐                 ┌──────────────────────┐
│ settings.json:      │                 │  Smart Router (:8462) │
│   BASE_URL=远端服务器│ ──── HTTPS ──→ │                      │
│   sonnet → GLM-5.1  │                 │  GLM-5.1  → 智谱     │
│   opus → 不设置      │                 │  claude-* → pincc    │
└─────────────────────┘                 └──────────────────────┘
```

## 远端服务器部署

### 1. 上传文件

```bash
scp -r ~/.claude/smart-router/ user@server:~/.claude/smart-router/
```

### 2. 安装依赖

```bash
pip3 install aiohttp
```

### 3. 修改 config.json

```bash
vi ~/.claude/smart-router/config.json
# 修改 auth_tokens 为一个随机字符串（作为你的路由密码）
# 例如: "auth_tokens": ["my-secret-router-key-abc123"]
```

### 4. 注册 systemd 服务

```bash
sudo cp ~/.claude/smart-router/smart-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-router
sudo systemctl status smart-router
```

### 5. 配置 nginx 反向代理（HTTPS）

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8462;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # SSE 流式响应必须
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        chunked_transfer_encoding on;
    }
}
```

## 本地配置（任意电脑通用）

`~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-domain.com",
    "ANTHROPIC_AUTH_TOKEN": "my-secret-router-key-abc123",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "GLM-5.1",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "GLM-5.1",
    "ANTHROPIC_MODEL": "GLM-5.1",
    "ANTHROPIC_REASONING_MODEL": "GLM-5.1",
    "API_TIMEOUT_MS": "3000000"
  },
  "model": "sonnet",
  "skipDangerousModePermissionPrompt": true
}
```

**关键**：不设置 `ANTHROPIC_DEFAULT_OPUS_MODEL`，这样 opus 别名保持默认的 `claude-opus-4-6`，路由器会将其转发到真正的 Claude API。

## 模型使用方式

| 场景 | 模型 | 实际后端 | 费用 |
|------|------|----------|------|
| 日常编码 | sonnet (默认) | GLM-5.1 → 智谱 | 低 |
| 轻量探索 | haiku | GLM-5.1 → 智谱 | 低 |
| **写方案** | **opus** | **claude-opus-4-6 → pincc** | **高** |

### Skill 自动切换

在 claude-planner 技能中：
```
Agent(model: "opus", prompt: "写技术方案...")
```
子 agent 自动使用真 Claude opus，不需要用户手动切换。

### 手动切换

也可以在会话中手动切换：
- `/model opus` → 切到真 Claude（写方案/做决策）
- `/model sonnet` → 切回 GLM（日常编码）

## 运维

```bash
# 查看日志
sudo journalctl -u smart-router -f

# 重启（修改 config.json 后不需要重启，自动热更新）
sudo systemctl restart smart-router

# 添加新的后端：编辑 config.json，加一条 route 即可
```
