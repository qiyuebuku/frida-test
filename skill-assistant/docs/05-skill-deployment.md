# 05 - 远端部署方案

## 一、当前状态（v1 已完成）

```
本地:  /home/yuyang/frida-test/.claude/skills/fund-trade/
远端:  /home/yuyangruan/claude-skills/fund-trade/  ← 已部署 ✅
```

远端环境：
- Claude Code 2.1.71 ✅
- API 代理 `api.z.ai`（GLM 模型映射）✅
- fund-trade skill + 8 个 prompts + 符号链接 ✅

---

## 二、v2 需要的远端变更

### 2.1 新增文件部署

v2 新增 `services/event_bus.py`，需要同步到远端：

```
/home/yuyangruan/smart-fund-server/
├── services/
│   ├── event_bus.py          ← 新增：内存事件总线
│   ├── task_executor.py      ← 修改：stream-json 解析 + emit 事件
│   ├── task_db.py            ← 修改：新增 partial_result / tool_calls 字段
│   ├── fund_db.py
│   ├── scheduler.py
│   └── ...
├── routers/
│   └── __init__.py           ← 修改：新增 SSE 端点 /api/tasks/{id}/stream
├── main.py
└── ...
```

这些文件通过现有 `deploy.sh` 的 `sync_code()` 自动同步，**无需额外操作**。

### 2.2 claude -p 调用方式变更

**v1（短任务，不变）**：
```python
def _run_claude(self, prompt: str, timeout: int = 120) -> str | None:
    result = subprocess.run(
        ["claude", "-p", prompt,
         "--allowedTools", "Bash,Read,Glob,Grep",
         "--output-format", "text"],
        capture_output=True, text=True, timeout=timeout,
        cwd=SKILL_DIR, env=env
    )
    return result.stdout.strip() if result.returncode == 0 else None
```

**v2（长任务，改为 stream-json）**：
```python
def _run_claude_streaming(self, task_id: int, prompt: str,
                          timeout: int = 600, ...) -> str | None:
    process = subprocess.Popen(
        ["claude", "-p", prompt,
         "--allowedTools", "Bash,Read,Glob,Grep",
         "--output-format", "stream-json", "--verbose"],  # ← 关键变更
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,   # 行缓冲，逐行读取
        cwd=SKILL_DIR, env=env
    )
    # 逐行读取 JSONL → 解析事件 → emit 到 EventBus + 写 DB
    for line in process.stdout:
        event = json.loads(line)
        # ... 解析 tool_use / text / result 事件
```

**前置验证**：
```bash
# 远端必须验证 stream-json 模式可用
ssh remote 'claude -p "回复OK" --output-format stream-json --verbose 2>/dev/null | head -5'
```

> **注意**：`stream-json` 需要 `--verbose` 参数配合使用，Claude Code 2.1.71+ 支持。

### 2.3 数据库 DDL

```sql
-- 新增字段（在远端 PostgreSQL 执行）
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS partial_result TEXT;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS tool_calls JSONB;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS config JSONB;  -- 任务配置（自定义提示词/规则）
```

### 2.4 远端目录结构（v2 完整）

```
/home/yuyangruan/
├── smart-fund-server/
│   ├── main.py
│   ├── routers/
│   │   └── __init__.py              # +SSE 端点 /api/tasks/{id}/stream
│   ├── services/
│   │   ├── event_bus.py             # 新增：TaskEventBus
│   │   ├── task_executor.py         # 改造：_run_claude_streaming()
│   │   ├── task_db.py               # 改造：partial_result / tool_calls / config
│   │   ├── fund_db.py
│   │   ├── scheduler.py
│   │   └── ocr_service.py
│   └── handlers/
│       └── screenshot_handler.py
├── claude-skills/
│   └── fund-trade/
│       ├── SKILL.md
│       ├── client.py → ../smart-fund-server/client.py
│       ├── fund_db.py → ../smart-fund-server/services/fund_db.py
│       ├── config.json → ../smart-fund-server/config.json
│       └── prompts/ (8个模板)
└── .claude/
    └── settings.json
```

---

## 三、部署同步脚本

### 现有流程（不变）

```bash
# deploy.sh 的默认流程
deploy.sh              # sync_code + sync_skills + restart
deploy.sh --skills     # 仅同步 skills
deploy.sh --restart    # 仅重启服务
```

`sync_code()` 会 rsync 整个 `smart-fund-server/` 目录，新增的 `event_bus.py` 自动包含。

### v2 部署额外步骤

首次 v2 部署时需要：

```bash
# 1. 同步代码（包含 event_bus.py + 修改后的 task_executor.py / task_db.py / routers）
deploy.sh

# 2. 执行 DDL（远端 PostgreSQL）
ssh remote 'psql -U jettask -d jettask -c "
    ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS partial_result TEXT;
    ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS tool_calls JSONB;
    ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS config JSONB;
"'

# 3. 验证 stream-json 模式
ssh remote 'claude -p "回复OK" --output-format stream-json --verbose 2>/dev/null | head -3'
```

后续迭代只需 `deploy.sh` 即可。

---

## 四、远端 Claude Code 配置

### `~/.claude/settings.json`（不变）

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "97186f...（API 代理 Token）",
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.5-air",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5"
  },
  "permissions": { "allow": [], "deny": [] },
  "model": "opus",
  "skipDangerousModePermissionPrompt": true
}
```

**要点**：
- API 代理 `api.z.ai` → GLM 模型（opus→glm-5, sonnet→glm-4.7）
- 超时 50 分钟，满足长任务
- `--allowedTools` 参数按需授权

### stream-json 兼容性

`--output-format stream-json --verbose` 在 API 代理模式下的行为：
- 事件格式与原生 Anthropic API 相同（`type: system/assistant/result`）
- tool_use / tool_result 事件正常输出
- **需要验证**：GLM 模型通过代理时 stream-json 是否正常工作

---

## 五、验证清单

### v1 已验证 ✅
- [x] 远端 Claude Code 2.1.71 可用
- [x] `claude -p "回复OK"` → 返回 OK
- [x] skill 目录 + 8 个 prompts + 符号链接正确
- [x] 端到端：App 创建 fund_review → claude -p 执行 → Markdown 报告

### v2 待验证
- [ ] `claude -p --output-format stream-json --verbose` 在远端正常输出 JSONL
- [ ] stream-json 事件包含 tool_use（Bash 工具调用）
- [ ] DDL 执行成功（partial_result / tool_calls / config 字段）
- [ ] SSE 端点 `/api/tasks/{id}/stream` 能持续推送事件
- [ ] App 端 SSE 客户端能实时接收并渲染
- [ ] 断线重连后能通过 `GET /api/tasks/{id}` 恢复中间状态

---

## 六、注意事项

1. **SKILL_DIR**：`os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/fund-trade")`
2. **并发限制**：`Semaphore(2)`，最多 2 个 claude -p 并发
3. **超时**：短任务 120s，长任务 600s（stream-json 模式下通过 for-line 循环内检查）
4. **服务重启恢复**：不自动恢复，中断的任务需手动处理
5. **EventBus 内存**：每个任务的事件列表在任务完成后清理，不持久化
6. **stream-json 降级**：如果远端 stream-json 不可用，`_run_claude_streaming` 自动降级为 `_run_claude_with_progress`（时间估算模式）
