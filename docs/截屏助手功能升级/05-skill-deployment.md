# 05 - fund-trade Skill 远端部署方案

## 一、问题

`claude -p` 需要在远端服务器（119.23.227.187）上执行，但 fund-trade skill 目前只存在于本地：

```
本地:  /home/yuyang/frida-test/.claude/skills/fund-trade/
远端:  /home/yuyangruan/claude-skills/fund-trade/  ← 已部署 ✅
```

`claude -p` 要能使用 skill，需要：
1. 远端安装 Claude Code CLI ✅
2. fund-trade skill 文件部署到远端 ✅
3. `client.py` 和 `prompts/` 等依赖文件同步 ✅

## 二、远端环境准备

### 2.1 Claude Code CLI ✅

远端已安装 Claude Code 2.1.71：

```bash
$ claude --version
2.1.71 (Claude Code)
```

> **注意**：远端使用 API 代理（`api.z.ai`），通过环境变量配置在 `~/.claude/settings.json` 中，
> 模型映射为 GLM 系列（haiku→glm-4.5-air, sonnet→glm-4.7, opus→glm-5）。

### 2.2 Skill 目录结构 ✅

远端实际部署路径：

```
/home/yuyangruan/
├── smart-fund-server/                  # API 服务
│   ├── client.py                       # 基金 API 客户端
│   ├── config.json                     # 基金池配置
│   ├── services/
│   │   ├── fund_db.py                  # 基金数据库
│   │   ├── task_db.py                  # 异步任务数据库
│   │   ├── task_executor.py            # 后台任务执行器
│   │   └── scheduler.py               # 定时任务调度器
│   └── main.py                         # FastAPI 入口
├── claude-skills/
│   └── fund-trade/
│       ├── SKILL.md                    # Skill 定义（22KB）
│       ├── client.py → ../smart-fund-server/client.py          # 符号链接
│       ├── fund_db.py → ../smart-fund-server/services/fund_db.py
│       ├── config.json → ../smart-fund-server/config.json
│       ├── prompts/                    # 分析模板（8个）
│       │   ├── daily_decision.md       # 每日决策模板
│       │   ├── ocr_analyze.md          # OCR 持仓分析
│       │   ├── ocr_analyze_flow.md     # OCR 分析流程
│       │   ├── portfolio_review.md     # 持仓审视
│       │   ├── review_decision.md      # 决策复盘
│       │   ├── select_fund.md          # 选基模板
│       │   ├── analyze_fund.md         # 单基分析
│       │   └── alipay_review.md        # 支付宝持仓
│       └── data/                       # 数据文件
└── .claude/
    └── settings.json                   # Claude Code 全局配置
```

### 2.3 远端 client.py 的 API_BASE 调整

远端 client.py 连接的是 localhost（同一台机器），通过环境变量覆盖：

```python
# task_executor.py 中设置
env = os.environ.copy()
env["FUND_API_BASE"] = "http://127.0.0.1:8900"
```

采用**方案 A**（环境变量），在 `task_executor.py` 的 `_run_claude()` 和 `_run_claude_with_progress()` 中自动设置。

## 三、部署同步脚本 ✅

`deploy.sh` 中的 `sync_skills()` 函数（实际代码）：

```bash
# deploy.sh 中的实际实现
SKILL_LOCAL="/home/yuyang/frida-test/.claude/skills/fund-trade"
SKILL_REMOTE="/home/${REMOTE_USER}/claude-skills/fund-trade"

sync_skills() {
    echo "📦 同步 Claude Skills..."
    ssh_cmd "mkdir -p ${SKILL_REMOTE}/prompts ${SKILL_REMOTE}/data"

    # 同步 SKILL.md 和 prompts 目录
    rsync -avz \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='test_*.py'   --exclude='*.bak' \
        -e "ssh -p ${REMOTE_PORT} -i ${SSH_KEY_TMP} -o StrictHostKeyChecking=no" \
        "${SKILL_LOCAL}/SKILL.md" \
        "${SKILL_LOCAL}/prompts/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${SKILL_REMOTE}/"

    # 单独同步 prompts 子目录
    rsync -avz \
        -e "ssh -p ${REMOTE_PORT} -i ${SSH_KEY_TMP} -o StrictHostKeyChecking=no" \
        "${SKILL_LOCAL}/prompts/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${SKILL_REMOTE}/prompts/"

    # 创建符号链接
    ssh_cmd "
        cd ${SKILL_REMOTE}
        ln -sf ${REMOTE_DIR}/client.py client.py 2>/dev/null || true
        ln -sf ${REMOTE_DIR}/services/fund_db.py fund_db.py 2>/dev/null || true
        ln -sf ${REMOTE_DIR}/config.json config.json 2>/dev/null || true
    "
    echo "✅ Skills 同步完成"
}
```

**同步触发方式**：

| 命令 | 说明 |
|------|------|
| `deploy.sh` | 默认流程（sync_code + sync_skills + restart） |
| `deploy.sh --skills` | 仅同步 skills |

## 四、claude -p 调用方式

### 4.1 TaskExecutor 中的实际调用

`task_executor.py` 实现了两种调用模式：

**同步调用**（短任务，如结构化）：
```python
def _run_claude(self, prompt: str, timeout: int = 120) -> str | None:
    env = os.environ.copy()
    env["FUND_API_BASE"] = "http://127.0.0.1:8900"

    result = subprocess.run(
        ["claude", "-p", prompt,
         "--allowedTools", "Bash,Read,Glob,Grep",
         "--output-format", "text"],
        capture_output=True, text=True, timeout=timeout,
        cwd=SKILL_DIR if Path(SKILL_DIR).exists() else None,
        env=env
    )
    return result.stdout.strip() if result.returncode == 0 else None
```

**异步调用 + 进度估算**（长任务，如持仓分析/交易决策）：
```python
def _run_claude_with_progress(self, task_id, prompt,
                              timeout=600, progress_range=(35, 90),
                              messages=None) -> str | None:
    process = subprocess.Popen(
        ["claude", "-p", prompt,
         "--allowedTools", "Bash,Read,Glob,Grep",
         "--output-format", "text"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=SKILL_DIR, env=env
    )
    # 每 3 秒按时间线性插值估算进度并写入 DB
    while process.poll() is None:
        elapsed = time.time() - start_time
        ratio = min(1.0, elapsed / estimate_total)
        pct = int(start_pct + (end_pct - start_pct) * ratio)
        self._progress(task_id, pct, msg)
        time.sleep(3)
    return process.stdout.read().strip()
```

### 4.2 各任务类型的实际 Prompt

**持仓分析**（`_handle_fund_holdings`）：
```python
analysis_prompt = f"""请执行基金持仓分析。

已有 OCR 结构化数据：
{data_desc}

分析步骤：
1. 执行 `python client.py market_overview` 采集市场环境
2. 执行 `python client.py hot_board` 查看热门板块
3. 执行 `curl -s --noproxy '*' http://127.0.0.1:8900/api/news_overview` 获取新闻
4. 对持仓中的基金，执行 `python client.py <代码> detail` 和 `python client.py <代码> rank`
5. 综合分析持仓配置、行业分布、风险点
6. 给出操作建议（加仓/减仓/持有）

输出完整的 Markdown 分析报告。只输出最终的 Markdown 报告。"""
```

**每日交易决策**（`_handle_skill_command`）：
```python
"请执行 /fund-trade run 的完整流程，输出今日操作汇总报告。"
```

**持仓审视**：
```python
"请执行 /fund-trade review 的完整流程，输出持仓审视报告。"
```

## 五、远端 Claude Code 配置

### `~/.claude/settings.json`（实际配置）

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
  "permissions": {
    "allow": [],
    "deny": []
  },
  "model": "opus",
  "skipDangerousModePermissionPrompt": true
}
```

**要点**：
- 使用 API 代理 `api.z.ai`，非原生 Anthropic API
- 模型映射：opus → glm-5, sonnet → glm-4.7, haiku → glm-4.5-air
- `skipDangerousModePermissionPrompt: true` — 允许 `claude -p` 无需确认执行命令
- 超时 300 万毫秒（50 分钟），满足长任务需求
- permissions 为空，`claude -p` 通过 `--allowedTools` 参数按需授权

## 六、验证清单

- [x] 远端 `claude --version` → `2.1.71 (Claude Code)` ✅
- [x] 远端 `claude -p "回复OK"` → 返回 `OK` ✅
- [x] 远端 skill 目录存在，SKILL.md + 8 个 prompts 已同步 ✅
- [x] 远端符号链接正确（client.py → smart-fund-server/client.py 等） ✅
- [x] `deploy.sh` 能正确同步 skills 文件 ✅
- [x] 重启服务后 skill 文件仍在（非临时目录） ✅
- [x] 端到端测试：App 创建 fund_review 任务 → claude -p 执行 → 返回 Markdown 报告 ✅
  - 测试时间：2026-03-10 17:32
  - 耗时：约 5 分钟
  - 结果：生成完整持仓审视报告（含 portfolio_summary + logic_review + 操作建议）

## 七、注意事项

1. **SKILL_DIR 环境变量**：`task_executor.py` 通过 `os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/fund-trade")` 配置，如需修改工作目录可通过环境变量覆盖
2. **并发限制**：`TaskExecutor` 使用 `Semaphore(2)` 限制最多 2 个 claude -p 并发
3. **超时保护**：同步调用 120s，异步调用 600s（10 分钟），超时自动 kill
4. **服务重启恢复**：当前不会恢复重启前正在处理的任务，需手动标记为 failed 或重新创建
