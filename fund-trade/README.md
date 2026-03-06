# 基金智能交易 Agent

基于 Claude Code Skill 的自动化基金交易系统，支持 LLM 决策 + 量化信号 + 风控硬约束。

## 快速开始

### 1. 初始化

```bash
# 进入 Claude Code
claude

# 执行初始化
/fund-trade init
```

### 2. 手动交易（交互模式）

```bash
claude
/fund-trade run          # 完整决策+执行
/fund-trade run --dry    # 模拟运行，不执行交易
```

### 3. 自动化交易（定时任务）

见下方"自动化配置"章节。

---

## 自动化配置

### 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         cron job                                 │
│  50 14 * * 1-5  agent_cron.sh run        # 尾盘决策+执行         │
│  30 15 * * 1-5  agent_cron.sh retrospect # 盘后复盘             │
│  0 18 * * 1-5   agent_cron.sh sync       # 持仓同步             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Claude Code (claude -p)                       │
│   读取 SKILL.md → 执行完整流程 → 自动决策 → 自动执行交易          │
└─────────────────────────────────────────────────────────────────┘
```

### 定时任务

| 时间 | 任务 | 命令 | 说明 |
|------|------|------|------|
| **14:50** | 尾盘决策 | `/fund-trade run --auto` | 基于当天数据做决策并执行 |
| 15:30 | 盘后复盘 | `/fund-trade retrospect` | 分析决策效果，更新知识库 |
| 18:00 | 持仓同步 | `fund_api.py sync` | 净值公布后同步持仓 |

### 为什么是 14:50？

- 场外基金 15:00 截止申购，以当天收盘净值成交
- 14:50 决策可利用当天完整市场信息
- 如果用 15:30 决策，只能次日执行，慢一天 + 隔夜风险

### 前置条件（交易日）

以下条件会由 `agent_cron.sh` **全部自动完成**：
- ✅ server.py 启动（8900 端口）
- ✅ adb forward 设置（tcp:18900）
- ✅ 同花顺 App 自动启动

**唯一需要手动确保的**：
- 📱 **手机已通过 USB 连接电脑**（且已授权 ADB 调试）

### 配置 cron job

```bash
# 编辑 crontab
crontab -e

# 添加以下内容
# Fund Trade Agent - 自动化交易定时任务
50 14 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh run
30 15 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh retrospect
0 18 * * 1-5 /home/yuyang/frida-test/.claude/skills/fund-trade/agent_cron.sh sync
```

---

## 使用命令

### 查看/管理定时任务

```bash
# 查看定时任务
crontab -l

# 暂停自动化（删除所有定时任务）
crontab -r

# 恢复自动化
crontab -e  # 粘贴上面的配置
```

### 手动测试

```bash
# 通过包装脚本测试
.claude/skills/fund-trade/agent_cron.sh run

# 通过 claude -p 测试（dry-run）
claude -p "/fund-trade run --dry --auto"

# 通过 claude -p 测试（实际执行，谨慎！）
claude -p "/fund-trade run --auto"
```

### 查看日志

```bash
# 实时查看日志
tail -f ~/fund-agent.log

# 查看最近 100 行
tail -100 ~/fund-agent.log
```

---

## 日志说明

- **位置**：`~/fund-agent.log`
- **内容**：每次执行的时间戳、命令、完整输出

示例：
```
[2026-03-05 14:50:01] ========== Starting: run ==========
[2026-03-05 14:50:01] Executing: /fund-trade run --auto
... Claude 决策输出 ...
[2026-03-05 14:58:32] ========== Finished: run ==========
```

---

## 注意事项

### claude -p 输出特性

- `claude -p` 是**缓冲输出**，执行完成后才显示结果
- `/fund-trade run` 完整流程需要 **5-10 分钟**
- 如需流式输出（JSON 格式）：
  ```bash
  claude -p "/fund-trade run --dry --auto" --output-format stream-json --verbose
  ```

### --auto 模式

`--auto` 参数用于非交互模式，会：
- 跳过 14:50 后的用户确认，直接执行交易
- 连通性检查失败时自动退出（不等待用户干预）

### 交易截止时间

- **14:50 前下单**：当天确认，T+1 到账
- **14:50 后下单**：次日确认，T+2 到账，可在次日 15:00 前撤销

---

## 文件结构

```
.claude/skills/fund-trade/
├── SKILL.md          # Skill 定义（Claude Code 读取）
├── README.md         # 本文档
├── agent_cron.sh     # cron job 包装脚本
├── config.json       # 基金池和策略配置
├── .env              # 交易密码（不进 git）
├── fund_api.py       # 数据采集 API
├── fund_db.py        # 数据库操作
├── indicators.py     # 量化指标计算
├── risk_manager.py   # 风控管理
├── trader.py         # 交易执行
├── server.py         # 同花顺 API 服务
├── client.py         # CLI 工具
└── prompts/          # LLM 决策 Prompt 模板
    ├── daily_decision.md
    ├── review_decision.md
    └── ...
```

---

## 常见问题

### Q: cron job 没有执行？

1. 检查 cron 服务：`service cron status`
2. 检查 crontab：`crontab -l`
3. 检查日志：`tail ~/fund-agent.log`

### Q: 交易失败？

1. 检查手机是否连接：`adb devices`
2. 检查同花顺 App 是否打开
3. 查看日志中的错误信息：`tail -50 ~/fund-agent.log`

> 注：server.py 和 adb forward 会自动启动/设置，无需手动处理

### Q: 如何修改定时任务时间？

```bash
crontab -e
# 修改时间后保存
```

### Q: 如何暂时停止自动化？

```bash
crontab -r  # 删除所有定时任务
```
