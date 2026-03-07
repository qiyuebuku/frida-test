# Fund-Trade 架构重构技术方案

**文档版本**: v2.0
**创建日期**: 2026-03-06
**负责人**: Claude Code

---

## 一、背景与目标

### 1.1 当前问题

**现有文件分布**：

```
/home/yuyang/frida-test/ths/api/         (服务端 - 已有)
├── server.py (879行)                     FastAPI 服务
├── client.py (4211行)                    CLI 工具（70+ 命令）
└── ths_fund_client.py (4047行)           API 封装

/home/yuyang/frida-test/.claude/skills/fund-trade/  (客户端 - 混乱)
├── server.py (38KB)                      ❌ 重复
├── client.py (183KB)                     ❌ 重复
├── ths_fund_client.py (186KB)            ❌ 重复
├── ths_trade_client.py (14KB)            ❌ 重复
├── trader.py (6KB)                       ❌ 重复
├── fund_db.py (51KB)                     ⚠️ 应移到服务端
├── fund_api.py (27KB)                    ⚠️ 应移到服务端
├── review_decision_executor.py (16KB)    ⚠️ 应移到服务端（重业务逻辑）
├── risk_manager.py (14KB)                ⚠️ 应移到服务端（重业务逻辑）
├── indicators.py (19KB)                  ⚠️ 应移到服务端（重业务逻辑）
└── skill.py, config.json                 ✅ 保留
```

**存在的问题**：
1. ❌ **文件重复**：fund-trade 中有大量与 ths/api 重复的文件
2. ❌ **职责混乱**：重业务逻辑（数据库、复杂计算）散落在客户端
3. ❌ **架构不清**：服务端和客户端边界不明确

### 1.2 重构目标

**核心原则**：
- ✅ **服务端** = `/home/yuyang/frida-test/ths/api/`（所有重逻辑）
- ✅ **客户端** = `/home/yuyang/frida-test/.claude/skills/fund-trade/`（轻量级连接 + Skill 定义）
- ✅ **消除重复**：所有重复文件合并到服务端
- ✅ **职责清晰**：客户端只做 HTTP 请求 + 参数解析，不做业务逻辑

---

## 二、新架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│ 客户端（轻量级 Skill）                                │
├─────────────────────────────────────────────────────┤
│ /home/yuyang/frida-test/.claude/skills/fund-trade/  │
│                                                       │
│ client.py          轻量级 HTTP 客户端（~100 行）      │
│ skill.py           Skill 定义                        │
│ config.json        配置文件                          │
│ agent_cron.sh      定时任务脚本                       │
│                                                       │
│ 职责：                                                │
│ - 连接服务端（requests.get/post）                    │
│ - 参数解析和传递                                      │
│ - 结果格式化输出                                      │
│ - 无业务逻辑，无数据库操作，无 API 调用               │
└─────────────────────────────────────────────────────┘
                         ↓ HTTP (8900)
┌─────────────────────────────────────────────────────┐
│ 服务端（重逻辑）                                      │
├─────────────────────────────────────────────────────┤
│ /home/yuyang/frida-test/ths/api/                     │
│                                                       │
│ server.py                  FastAPI 主服务             │
│ ths_fund_client.py         同花顺 API 封装           │
│ fund_db.py                 数据库操作                │
│ review_decision_executor.py 决策复盘执行器           │
│ risk_manager.py            风控硬约束模块             │
│ indicators.py              量化信号计算               │
│                                                       │
│ 职责：                                                │
│ - 所有同花顺 API 调用（JSBridge）                    │
│ - 所有数据库操作（PostgreSQL）                       │
│ - 业务逻辑（交易、风控、复盘、量化信号）              │
│ - httpx.AsyncClient 连接管理                         │
└─────────────────────────────────────────────────────┘
                         ↓ HTTP (18900)
┌─────────────────────────────────────────────────────┐
│ 手机端 Hook Server                                    │
└─────────────────────────────────────────────────────┘
```

### 2.2 文件迁移计划

#### 服务端（`/home/yuyang/frida-test/ths/api/`）

**已有文件**（保留）：
```
server.py              ✅ FastAPI 主服务
ths_fund_client.py     ✅ API 封装
client.py              ✅ CLI 工具（可选）
```

**需要迁移过来的文件**（从 fund-trade 移动）：
```
fund_db.py                     数据库操作模块
review_decision_executor.py    决策复盘执行器
risk_manager.py                风控硬约束模块
indicators.py                  量化信号计算
config.json                    配置文件（合并）
```

**迁移后的服务端结构**：
```
/home/yuyang/frida-test/ths/api/
├── server.py                       FastAPI 主服务
├── ths_fund_client.py              同花顺 API 封装
├── fund_db.py                      数据库操作
├── review_decision_executor.py     决策复盘
├── risk_manager.py                 风控模块
├── indicators.py                   量化指标
├── config.json                     配置文件
└── requirements.txt                依赖清单
```

#### 客户端（`/home/yuyang/frida-test/.claude/skills/fund-trade/`）

**需要删除的文件**（重复或属于服务端）：
```
❌ server.py                    删除（服务端在 ths/api）
❌ client.py (183KB)            删除（旧版本，重复）
❌ ths_fund_client.py           删除（服务端专属）
❌ ths_trade_client.py          删除（服务端专属）
❌ trader.py                    删除（服务端专属）
❌ fund_db.py                   迁移到 ths/api
❌ fund_api.py                  迁移到 ths/api
❌ review_decision_executor.py  迁移到 ths/api
❌ risk_manager.py              迁移到 ths/api
❌ indicators.py                迁移到 ths/api
```

**保留/新增的文件**：
```
✅ client.py (NEW)         轻量级 HTTP 客户端（~100 行）
✅ skill.py                Skill 定义
✅ config.json             客户端配置（server_url 等）
✅ agent_cron.sh           定时任务脚本
✅ README.md               使用文档
```

**迁移后的客户端结构**：
```
/home/yuyang/frida-test/.claude/skills/fund-trade/
├── client.py              轻量级 HTTP 客户端
├── skill.py               Skill 定义
├── config.json            客户端配置
├── agent_cron.sh          定时任务脚本
└── README.md              使用文档
```

---

## 三、详细设计

### 3.1 客户端设计（client.py）

**职责**：仅做 HTTP 请求转发，不包含任何业务逻辑

```python
#!/usr/bin/env python3
"""Fund Trade Client - 轻量级 HTTP 客户端"""

import json
import os
import sys
import requests

# 配置
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    """加载客户端配置"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

class FundTradeClient:
    """轻量级 HTTP 客户端（仅负责请求转发）"""

    def __init__(self, server_url=None):
        config = load_config()
        self.server_url = server_url or config.get("server_url", "http://localhost:8900")
        self.timeout = config.get("timeout", 60)

    def request(self, method: str, endpoint: str, **kwargs):
        """通用 HTTP 请求"""
        url = f"{self.server_url}{endpoint}"

        try:
            if method == "GET":
                resp = requests.get(url, params=kwargs.get("params"), timeout=self.timeout)
            elif method == "POST":
                resp = requests.post(url, json=kwargs.get("json"), timeout=self.timeout)
            else:
                return {"status": "error", "message": f"不支持的方法: {method}"}

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": f"请求失败: {e}"}

    # ========== 交易相关 ==========

    def buy(self, fund_code: str, amount: float, reason: str = None):
        """买入基金"""
        return self.request("POST", "/api/trade/buy", json={
            "fund_code": fund_code,
            "amount": amount,
            "reason": reason
        })

    def sell(self, fund_code: str, pct: float, reason: str = None):
        """卖出基金"""
        return self.request("POST", "/api/trade/sell", json={
            "fund_code": fund_code,
            "pct": pct,
            "reason": reason
        })

    # ========== 持仓查询 ==========

    def get_positions(self):
        """查询所有持仓"""
        return self.request("GET", "/api/position")

    def get_position(self, fund_code: str):
        """查询指定基金持仓"""
        return self.request("GET", f"/api/position/{fund_code}")

    # ========== 风控相关 ==========

    def snapshot(self):
        """风控快照"""
        return self.request("GET", "/api/risk/snapshot")

    def check_decisions(self, decisions: dict):
        """检查决策是否违反风控"""
        return self.request("POST", "/api/risk/check", json=decisions)

    def preflight(self):
        """交易前置检查"""
        return self.request("GET", "/api/risk/preflight")

    # ========== 量化信号 ==========

    def evaluate_signals(self):
        """计算量化信号"""
        return self.request("POST", "/api/indicators/evaluate")

    # ========== 决策复盘 ==========

    def review_decisions(self, limit: int = 30, days_back: int = 7):
        """执行决策复盘"""
        return self.request("POST", "/api/review/execute", json={
            "limit": limit,
            "days_back": days_back
        })

    # ========== 基金数据 ==========

    def get_fund_detail(self, fund_code: str):
        """基金详情"""
        return self.request("GET", f"/api/fund/{fund_code}/detail")

    def get_fund_ranking(self, sort_type: str = "year", page: int = 1):
        """基金排名"""
        return self.request("GET", "/api/fund/ranking", params={
            "sort_type": sort_type,
            "page": page
        })

# ========== CLI 入口 ==========

def main():
    """命令行入口（用于测试）"""
    if len(sys.argv) < 2:
        print("用法: python client.py <method> [args...]")
        print("示例:")
        print("  python client.py buy 008087 100 --reason '测试'")
        print("  python client.py snapshot")
        sys.exit(1)

    client = FundTradeClient()
    command = sys.argv[1]

    if command == "buy":
        fund_code = sys.argv[2]
        amount = float(sys.argv[3])
        reason = sys.argv[4] if len(sys.argv) > 4 else None
        result = client.buy(fund_code, amount, reason)

    elif command == "sell":
        fund_code = sys.argv[2]
        pct = float(sys.argv[3])
        reason = sys.argv[4] if len(sys.argv) > 4 else None
        result = client.sell(fund_code, pct, reason)

    elif command == "snapshot":
        result = client.snapshot()

    elif command == "preflight":
        result = client.preflight()

    elif command == "evaluate":
        result = client.evaluate_signals()

    else:
        result = {"status": "error", "message": f"未知命令: {command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
```

**配置文件（config.json）**：
```json
{
  "server_url": "http://localhost:8900",
  "timeout": 60
}
```

### 3.2 服务端增强

#### 需要在 server.py 中新增的 API 路由

```python
# ========== 风控相关 ==========

@app.get("/api/risk/snapshot")
async def risk_snapshot():
    """风控快照"""
    import risk_manager
    return risk_manager.snapshot()

@app.post("/api/risk/check")
async def risk_check(decisions: dict):
    """检查决策"""
    import risk_manager
    return risk_manager.check(decisions)

@app.get("/api/risk/preflight")
async def risk_preflight():
    """交易前置检查"""
    import risk_manager
    return risk_manager.preflight()

# ========== 量化信号 ==========

@app.post("/api/indicators/evaluate")
async def evaluate_indicators():
    """计算量化信号"""
    import indicators
    result = indicators.cmd_evaluate()
    return result

# ========== 决策复盘 ==========

@app.post("/api/review/execute")
async def execute_review(limit: int = 30, days_back: int = 7):
    """执行决策复盘"""
    import review_decision_executor
    result = review_decision_executor.execute_decision_review(limit, days_back)
    return result
```

---

## 四、实施步骤

### 4.1 阶段 1：备份与迁移准备

```bash
# 1. 备份现有代码
cd /home/yuyang/frida-test/.claude/skills/fund-trade
mkdir -p backup_$(date +%Y%m%d_%H%M%S)
cp *.py *.json *.sh backup_$(date +%Y%m%d_%H%M%S)/

cd /home/yuyang/frida-test/ths/api
mkdir -p backup_$(date +%Y%m%d_%H%M%S)
cp *.py *.json backup_$(date +%Y%m%d_%H%M%S)/
```

### 4.2 阶段 2：迁移文件到服务端

```bash
# 迁移业务逻辑文件到服务端
cd /home/yuyang/frida-test/.claude/skills/fund-trade

mv fund_db.py /home/yuyang/frida-test/ths/api/
mv review_decision_executor.py /home/yuyang/frida-test/ths/api/
mv risk_manager.py /home/yuyang/frida-test/ths/api/
mv indicators.py /home/yuyang/frida-test/ths/api/

# 合并配置文件（手动合并内容）
# config.json 中的 fund_pool, risk 等配置移到服务端
```

### 4.3 阶段 3：实现轻量级客户端

```bash
# 在 fund-trade 创建新的轻量级 client.py
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 创建新的 client.py（按上面的设计）
# 创建新的 config.json（只包含 server_url 等客户端配置）
```

### 4.4 阶段 4：更新服务端 API

```bash
# 在 ths/api/server.py 中添加新的 API 路由
cd /home/yuyang/frida-test/ths/api

# 编辑 server.py，添加：
# - /api/risk/snapshot
# - /api/risk/check
# - /api/risk/preflight
# - /api/indicators/evaluate
# - /api/review/execute
```

### 4.5 阶段 5：测试验证

```bash
# 1. 启动服务端
cd /home/yuyang/frida-test/ths/api
python server.py &

# 2. 测试客户端
cd /home/yuyang/frida-test/.claude/skills/fund-trade
python client.py snapshot
python client.py buy 008087 100 "测试"
python client.py preflight
python client.py evaluate
```

### 4.6 阶段 6：清理旧文件

```bash
cd /home/yuyang/frida-test/.claude/skills/fund-trade

# 删除重复/迁移的文件
rm -f server.py ths_fund_client.py ths_trade_client.py trader.py fund_api.py

# 只保留：
# - client.py (新的轻量级版本)
# - skill.py
# - config.json (新的客户端配置)
# - agent_cron.sh
# - README.md
```

---

## 五、文件对比

### 5.1 迁移前后对比

**迁移前 - fund-trade (客户端)**：
```
11 个 Python 文件，总计 ~500KB
- 大量重复代码
- 混杂重业务逻辑
- 职责不清
```

**迁移后 - fund-trade (客户端)**：
```
1 个 Python 文件（client.py），约 ~5KB
- 无重复代码
- 仅 HTTP 请求转发
- 职责清晰
```

**迁移后 - ths/api (服务端)**：
```
8 个 Python 文件：
- server.py              FastAPI 服务
- ths_fund_client.py     API 封装
- fund_db.py             数据库
- review_decision_executor.py 复盘
- risk_manager.py        风控
- indicators.py          量化
- config.json            配置
- requirements.txt       依赖
```

---

## 六、API 设计

### 6.1 交易相关

| 路由 | 方法 | 参数 | 返回 |
|-----|------|-----|-----|
| `/api/trade/buy` | POST | `{fund_code, amount, reason?}` | `{status, order_no, message}` |
| `/api/trade/sell` | POST | `{fund_code, pct, reason?}` | `{status, shares_sold, message}` |
| `/api/position` | GET | - | `{status, data: [...]}` |
| `/api/position/{code}` | GET | `code` | `{status, data: {...}}` |

### 6.2 风控相关

| 路由 | 方法 | 参数 | 返回 |
|-----|------|-----|-----|
| `/api/risk/snapshot` | GET | - | `{total_capital, invested, cash, positions, risk_alerts}` |
| `/api/risk/check` | POST | `{decisions: [...]}` | `{results: [...], summary}` |
| `/api/risk/preflight` | GET | - | `{can_trade, current_time, alerts}` |

### 6.3 量化信号

| 路由 | 方法 | 参数 | 返回 |
|-----|------|-----|-----|
| `/api/indicators/evaluate` | POST | - | `{fund_code: {signals, rule_suggestion}}` |

### 6.4 决策复盘

| 路由 | 方法 | 参数 | 返回 |
|-----|------|-----|-----|
| `/api/review/execute` | POST | `{limit?, days_back?}` | `{review_date, total_reviewed, correct, wrong}` |

---

## 七、总结

### 7.1 核心变化

1. **服务端** (`/home/yuyang/frida-test/ths/api/`)
   - ✅ 承载所有重业务逻辑
   - ✅ 提供统一 HTTP API
   - ✅ 管理数据库连接
   - ✅ 管理异步 HTTP 客户端

2. **客户端** (`/home/yuyang/frida-test/.claude/skills/fund-trade/`)
   - ✅ 极简轻量级（~100 行代码）
   - ✅ 仅负责 HTTP 请求转发
   - ✅ 无业务逻辑，无数据库操作
   - ✅ 作为 Claude Code Skill 的连接器

### 7.2 优势

- ✅ **清晰的职责分离**：服务端重逻辑，客户端轻连接
- ✅ **消除代码重复**：所有重复文件移除
- ✅ **易于维护**：业务逻辑集中在服务端
- ✅ **易于扩展**：新功能只需在服务端添加 API 路由

### 7.3 文件精简对比

| 位置 | 迁移前 | 迁移后 | 减少 |
|-----|-------|-------|------|
| fund-trade (客户端) | 11 个 Python 文件 (~500KB) | 1 个 Python 文件 (~5KB) | **-99%** |
| ths/api (服务端) | 3 个文件 | 8 个文件 | 整合业务逻辑 |

---

## 八、下一步

1. **确认方案**：确认架构设计符合预期
2. **开始迁移**：按照实施步骤逐步迁移
3. **测试验证**：确保所有功能正常工作
4. **清理文档**：更新 README 和使用文档
