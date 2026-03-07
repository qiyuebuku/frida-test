# 基金智能交易 Agent

基于 Claude Code Skill 的自动化基金交易系统，支持 LLM 决策 + 量化信号 + 风控硬约束。

## 架构说明

### 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Client 层: fund-trade/client.py (7K)                        │
│  - 轻量级 HTTP 客户端（PC 端）                                │
│  - 命令行接口封装                                             │
│  - 无业务逻辑，仅转发请求                                      │
└─────────────────────────────────────────────────────────────┘
                            │ HTTP (localhost:8900)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Server 层: ths/api/server.py (43K)                          │
│  - FastAPI 服务器 (8900端口, PC 端)                          │
│  - 业务逻辑层（风控/量化/决策复盘）                             │
│  - 调用 ths_fund_client.py API 包装层                         │
└─────────────────────────────────────────────────────────────┘
                            │ HTTP (localhost:18900, via adb forward)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Hook 层: zygisk hook 模块 (手机端)                          │
│  - Java/Kotlin HTTP 服务器 (18900端口)                       │
│  - 注入到同花顺 App，通过 WebView JSBridge 获取认证参数       │
│  - 提供实时认证参数 (K5, version, timestamp)                 │
│  - 无需硬编码 token，自动刷新                                 │
└─────────────────────────────────────────────────────────────┘
```

### 为什么需要 zygisk hook？

- **问题**：同花顺 API 的认证参数（token/K5）每次 App 重启后会变化
- **旧方案**：硬编码参数，导致频繁失效
- **新方案**：通过 zygisk hook 注入到同花顺 App，启动 HTTP 服务器，实时从 App 的 WebView JSBridge 获取参数，无需手动更新

### API 功能模块

| 模块 | 功能 | client.py 命令 |
|------|------|---------------|
| **风控** | 风控快照 | `python client.py snapshot` |
| | 交易前置检查 | `python client.py preflight` |
| **量化信号** | 计算量化信号 | `python client.py evaluate` |
| **决策复盘** | 执行决策复盘 | `python client.py review 30 7` |
| | 创建待复盘记录 | `python client.py create-reviews` |
| | 获取待复盘决策 | `python client.py pending-reviews` |
| | 获取复盘统计 | `python client.py review-stats` |
| | 获取经验知识库 | `python client.py lessons` |
| **交易** | 持仓查询 | `python client.py position` |
| | 订单查询 | `python client.py orders 7 5` |
| | 买入基金 | `python client.py buy 008087 100` |
| | 卖出基金 | `python client.py sell 008087 100` |
| **决策管理** | 今日决策 | `python client.py today-decisions` |
| | 最近决策 | `python client.py recent-decisions 5` |
| | 连续观望天数 | `python client.py watch-streaks` |
| **数据采集** | 基金扫描（完整） | `python client.py scan` |
| | 基金扫描（精简） | `python client.py scan-summary` |
| | 持仓同步 | `python client.py sync` |
| **账户信息** | 账户总览 | `python client.py account-overview` |
| | 钱包信息 | `python client.py wallet-info` |
| | 钱包首页 | `python client.py wallet-home` |

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

## 服务启动和测试

### 启动服务

```bash
# 1. 确保 zygisk hook 模块已安装
#    在 KernelSU 中安装 /home/yuyang/frida-test/ths/zygisk/thshook_zygisk.zip

# 2. 启动同花顺 App（zygisk 会自动注入并启动 HTTP 服务器 18900端口）
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 shell monkey -p com.hexin.plat.android -c android.intent.category.LAUNCHER 1

# 3. 设置 adb forward
/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000 forward tcp:18900 tcp:18900

# 4. 启动 FastAPI 服务器（8900端口）
cd /home/yuyang/frida-test/ths/api
python server.py
```

### 测试 API 功能

所有测试无需用户确认，可直接运行：

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 测试所有重构后的 API
bash /tmp/test_refactor_apis.sh

# 测试 JSBridge 集成
bash /tmp/test_summary.sh

# 测试所有 JSBridge 接口
bash /tmp/test_all_jsbridge.sh

# 测试高级功能
bash /tmp/test_advanced.sh
```

### 手动测试单个 API

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 风控快照
python client.py snapshot

# 交易前置检查
python client.py preflight

# 计算量化信号
python client.py evaluate

# 决策复盘（30天内，最多7条）
python client.py review 30 7

# 持仓查询
python client.py position

# 订单查询（7天内，最多5条）
python client.py orders 7 5

# 买入基金（基金代码 008087，金额 100元）
python client.py buy 008087 100

# 卖出基金（基金代码 008087，份额 100份）
python client.py sell 008087 100

# 基金扫描（精简版）
python client.py scan-summary

# 同步持仓
python client.py sync

# 账户总览
python client.py account-overview

# 钱包信息
python client.py wallet-info

# 今日决策
python client.py today-decisions

# 最近5天决策
python client.py recent-decisions 5

# 连续观望天数
python client.py watch-streaks
```

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

### Client 层（fund-trade/）

```
.claude/skills/fund-trade/
├── SKILL.md          # Skill 定义（Claude Code 读取）
├── README.md         # 本文档
├── agent_cron.sh     # cron job 包装脚本
├── config.json       # 客户端配置 (server_url 等)
├── client.py         # 轻量级 HTTP 客户端 (7K)
├── prompts/          # LLM 决策 Prompt 模板
│   ├── daily_decision.md
│   ├── review_decision.md
│   └── ...
└── backup_*/         # 备份目录
```

### Server 层（ths/api/）

```
ths/api/
├── server.py              # FastAPI 服务器 (43K, 8900端口)
├── ths_fund_client.py     # 同花顺 API 包装层 (178K, 104方法)
├── fund_db.py             # 数据库操作
├── risk_manager.py        # 风控管理
├── indicators.py          # 量化指标计算
└── review_decision_executor.py  # 决策复盘执行器
```

### Hook 层（ths/zygisk/）

```
ths/zygisk/
├── thshook_zygisk.zip     # KernelSU 模块安装包
├── magisk/                # 模块文件（兼容 Magisk/KernelSU 格式）
│   ├── dex/
│   │   ├── classes.dex    # Java/Kotlin 代码（HTTP 服务器 + JSBridge）
│   │   └── libpine.so     # hook 库
│   └── zygisk/
│       └── arm64-v8a.so   # zygisk 原生库
└── jni/                   # 原生代码源码
```

**说明**：
- zygisk hook 模块注入到同花顺 App 后，会启动一个 HTTP 服务器监听 0.0.0.0:18900，提供 JSBridge 接口
- 虽然目录名是 `magisk/`，但模块兼容 KernelSU（KernelSU 兼容 Magisk 模块格式）

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

### Q: 如何检查服务是否正常运行？

```bash
# 检查 FastAPI 服务器（8900端口）
curl -s http://localhost:8900/health

# 检查 JSBridge 代理（18900端口）
curl -s http://localhost:18900/health

# 检查 adb 连接
adb devices
```

### Q: API 返回错误怎么办？

1. **检查服务状态**：
   - 确保 server.py (8900) 正在运行
   - 确保手机上的 HTTP 服务器 (18900) 正在运行：`adb shell netstat -tuln | grep 18900`
2. **检查同花顺 App**：确保 App 已打开并登录
3. **检查 zygisk hook**：
   - 检查 KernelSU 模块是否启用：`adb shell su -c 'ksud module list'`
   - 查看 hook 日志：`adb logcat -s ThsHook:*`
4. **检查 adb forward**：`adb forward --list | grep 18900`
5. **测试连通性**：`curl -s --noproxy '*' http://localhost:18900/health`

### Q: 旧文件在哪里？

已清理的文件：
- `client.py.old` (180K) - 旧版本客户端
- `trader.py` (6.2K) - 旧的独立交易工具
- `ths_trade_client.py` (14K) - 旧的API客户端
- `config.json.full` (5.7K) - 旧的完整配置
- `deprecated/` - 废弃文件目录

这些功能已集成到新架构中：
- `ths/api/server.py` - 服务器端业务逻辑
- `ths/api/ths_fund_client.py` - API 包装层 (104方法)
- `fund-trade/client.py` - 轻量级客户端 (7K)
