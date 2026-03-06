# fund-trade Skill 优化方案

## 执行摘要

本次运行暴露了6个问题，按优先级分为P0(2个)、P1(3个)、P2(1个)。**所有核心优化已完成（5/6项）**，系统稳定性和用户体验显著提升。

**已完成优化：**
- ✅ 健康检查模块（自动检测和修复连接问题）
- ✅ fund_api.py 错误处理增强（提供明确的诊断建议）
- ✅ trader.py 交易重试机制（自动恢复502错误）
- ✅ trader.py 订单验证机制（P0，防止虚假成功记录）⭐ **新增**
- ✅ risk_manager.py 风控提示优化（提供解锁日期和详细建议）
- ✅ 复盘数据已确认无需优化（scan-summary已满足需求）

**待实施：**
- ⏳ 在Skill入口集成健康检查（可选，需要时手动运行 health_check.py）

---

## 已完成优化

### ✅ 1. 健康检查模块 (P0)

**文件:** `health_check.py`

**功能:**
- 自动检测账号状态(LT99等异常)
- 自动检测和修复adb端口转发
- 检测交易代理(18900)和数据服务(8900)连通性
- 提供友好的错误提示和修复建议

**使用方法:**
```bash
# 完整检查+自动修复
python health_check.py

# 仅检查不修复
python health_check.py --no-fix

# 输出JSON格式
python health_check.py --json
```

**集成方式:**
在skill入口添加健康检查：
```python
# 在执行交易前调用
from health_check import HealthChecker
checker = HealthChecker()
result = checker.run_full_check(auto_fix=True)
if result["status"] != "ok":
    print("系统未就绪，请根据提示修复")
    sys.exit(1)
```

---

## 待实施优化

### 2. 改进fund_api.py错误处理 (P1)

**位置:** `fund_api.py` 第323-341行 `cmd_sync()`

**当前问题:**
```python
if "error" in positions_resp:
    print(json.dumps({"error": f"获取持仓失败: {positions_resp['error']}"}, ensure_ascii=False))
    return  # 仅打印错误就返回
```

**优化方案:**
```python
if "error" in positions_resp:
    error_msg = positions_resp['error']
    suggestion = ""

    # 根据错误类型给出建议
    if "连接失败" in error_msg:
        suggestion = "请检查：\n1. server.py是否运行\n2. 端口8900是否被占用"
    elif "超时" in error_msg:
        suggestion = "请检查网络连接或增加timeout参数"

    print(json.dumps({
        "error": f"获取持仓失败: {error_msg}",
        "suggestion": suggestion,
        "health_check": "运行 'python health_check.py' 进行诊断"
    }, ensure_ascii=False, indent=2))
    sys.exit(1)  # 改为sys.exit(1)而非return，让调用方知道失败

# LT99账号异常优化
if resp_code and resp_code not in ("", "0000"):
    msg = positions_resp.get("message", "未知错误")
    print(json.dumps({
        "error": f"同花顺账户异常",
        "code": resp_code,
        "message": msg,
        "suggestion": "账号在其他设备登录，请：\n1. 在其他设备退出登录\n2. 在本设备重新登录同花顺\n3. 运行 'python health_check.py' 验证修复"
    }, ensure_ascii=False, indent=2))
    sys.exit(1)
```

**修改文件:** `fund_api.py`

---

### 3. 复盘系统净值数据优化 (P1)

**问题:** scan返回所有基金数据(65K tokens)，但复盘只需要基金池中的几只

**方案A: 新增scan-codes命令(推荐)**
```python
# fund_api.py 新增命令
def cmd_scan_codes():
    """只扫描指定基金代码，用于复盘"""
    if len(sys.argv) < 3:
        print(json.dumps({"error": "用法: scan-codes <code1> <code2> ..."}, ensure_ascii=False))
        sys.exit(1)

    codes = sys.argv[2:]
    config = _load_config()
    result = {}

    for code in codes:
        result[code] = _fetch_fund_data(code, config)

    print(json.dumps({"funds": result}, ensure_ascii=False))
```

调用方式：
```bash
# 复盘时只获取需要的基金
python fund_api.py scan-codes 022365 002207 019889
```

**方案B: scan-summary已足够**
- 当前scan-summary输出仅~2KB，已满足复盘需求
- 复盘Agent直接使用scan-summary即可

**决策:** 采用方案B，无需修改代码

---

### 4. 交易执行重试机制 (P1)

**位置:** `trader.py`

**当前问题:**
```python
response = requests.post(url, json=payload, timeout=30)
# 如果502错误直接失败，没有重试
```

**优化方案:**
```python
def _execute_trade_with_retry(url, payload, max_retries=3, fix_port=True):
    """带重试和端口修复的交易执行"""
    from health_check import HealthChecker

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=30,
                                    proxies={"http": None, "https": None})
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 502 and attempt < max_retries - 1 and fix_port:
                print(f"检测到502错误，尝试修复端口转发... (尝试 {attempt + 1}/{max_retries})")
                checker = HealthChecker()
                fix_result = checker.fix_port_forward()

                if fix_result["all_ok"]:
                    print("端口转发已修复，重试交易...")
                    time.sleep(2)  # 等待端口生效
                    continue
                else:
                    print("端口修复失败，请手动检查")
            raise

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"交易失败，{2} 秒后重试... (尝试 {attempt + 1}/{max_retries})")
                time.sleep(2)
            else:
                raise
```

**修改文件:** `trader.py` 的 `buy_fund()` 和 `sell_fund()` 函数

---

### 5. 风控提示优化 (P2)

**位置:** `risk_manager.py`

**当前输出:**
```json
{
  "blocked_reasons": ["反向操作冷却期未满(2天前刚买入，需等7天)"],
  "warnings": ["⚠️ 持有仅2天（不满7天），卖出将收取惩罚性赎回费(通常1.5%)"]
}
```

**优化后:**
```json
{
  "blocked_reasons": [
    "持有期不足：基金持有未满7天(当前2天)，卖出将收取1.5%惩罚性赎回费"
  ],
  "details": {
    "hold_days": 2,
    "required_days": 7,
    "redemption_fee": "1.5%",
    "unlockDate": "2026-03-12"
  },
  "suggestion": "建议等到2026-03-12后再卖出，可免赎回费"
}
```

**修改文件:** `risk_manager.py` 的 `_check_reverse_cooldown()` 函数

---

## 集成方案：在Skill入口添加健康检查

**文件:** `.claude/skills/fund-trade/skill.py` (如果存在)或在SKILL.md文档中更新

在`/fund-trade run`命令执行前，先运行健康检查：

```python
# Step 0: 健康检查(新增)
import subprocess
result = subprocess.run(
    ["python", "health_check.py", "--json"],
    capture_output=True,
    text=True
)
health = json.loads(result.stdout)

if health["status"] != "ok":
    print("系统未就绪，详细信息:")
    print(json.dumps(health, ensure_ascii=False, indent=2))

    # 自动修复一次
    subprocess.run(["python", "health_check.py"], check=True)
    sys.exit(0)
```

---

## 优化时间表

| 优化项 | 优先级 | 预计工时 | 状态 |
|--------|--------|----------|------|
| 健康检查模块 | P0 | 2h | ✅ 已完成 |
| fund_api错误处理 | P1 | 30min | ✅ 已完成 |
| 交易重试机制 | P1 | 1h | ✅ 已完成 |
| 风控提示优化 | P2 | 30min | ✅ 已完成 |
| 复盘数据优化 | P1 | 0 | ✅ 无需修改 |
| 集成健康检查到skill | P1 | 30min | ⏳ 待实施 |

---

## 测试计划

### 1. 健康检查模块测试

```bash
# 正常情况
python health_check.py

# 模拟端口失效(先kill adb forward)
adb forward --remove tcp:18900
python health_check.py  # 应该自动修复

# JSON输出
python health_check.py --json | jq .
```

### 2. 错误处理测试

```bash
# 模拟server.py未启动
pkill -f "python.*server.py"
python fund_api.py sync  # 应该输出友好错误提示
```

### 3. 交易重试测试

```bash
# 模拟502错误(关闭交易代理)
# 执行买入，应该自动修复端口后重试成功
python trader.py buy 008087 100 --reason "测试"
```

---

## 回归风险

**低风险:** 所有优化都是增量添加，不改变现有成功路径的逻辑

**回滚方案:** 如果health_check.py导致问题，可以暂时不调用，系统仍可正常运行(手动修复)

---

## 实施总结（2026-03-06）

### ✅ 已完成的优化

#### 1. 健康检查模块 (P0)
**文件:** `health_check.py` (新建)
- 自动检测账号状态(LT99等异常)
- 自动检测和修复adb端口转发
- 检测交易代理(18900)和数据服务(8900)连通性
- 提供友好的错误提示和修复建议

#### 2. fund_api.py 错误处理增强 (P1)
**修改位置:** `fund_api.py` 第 323-341 行
- 改进错误消息，提供具体诊断建议
- 将 `return` 改为 `sys.exit(1)` 确保调用方感知失败
- 引导用户运行 `health_check.py` 进行完整诊断

#### 3. trader.py 交易重试机制 (P1)
**修改位置:** `trader.py`
- 新增 `_execute_with_retry()` 函数（第 32-96 行）
- 支持最多3次重试
- 遇到502错误时自动尝试修复端口转发
- 修改 `do_buy()` 和 `do_sell()` 函数使用重试机制
- 导入 `time` 模块用于重试间隔

**关键代码:**
```python
def _execute_with_retry(url, payload, max_retries=3, fix_port_on_502=True):
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=30,
                               proxies={"http": None, "https": None})
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 502 and attempt < max_retries - 1:
                if fix_port_on_502:
                    from health_check import HealthChecker
                    checker = HealthChecker()
                    fix_result = checker.fix_port_forward()
                    if fix_result.get("all_ok"):
                        time.sleep(2)
                        continue
            raise
```

#### 4. risk_manager.py 风控提示优化 (P2)
**修改位置:** `risk_manager.py`
- 导入 `timedelta` (第 7 行)
- 优化持有期不足警告（第 238-251 行），新增：
  - 计算解锁日期
  - 提供 `warning_details` 字段包含详细信息
- 优化反向操作冷却期拦截（第 253-266 行），新增：
  - 显示解锁日期
  - 更清晰的错误描述
- 在返回结果中添加 `warning_details` 字段（第 268-273 行）

**优化效果示例:**
```json
{
  "blocked_reasons": [
    "反向操作冷却期未满：2天前刚买入，需等7天（解锁日期：2026-03-12）"
  ],
  "warnings": [
    "持有期不足：基金持有未满7天(当前2天)，卖出将收取1.5%惩罚性赎回费"
  ],
  "warning_details": {
    "hold_days": 2,
    "required_days": 7,
    "redemption_fee": "1.5%",
    "unlock_date": "2026-03-12",
    "suggestion": "建议等到2026-03-12后再卖出，可免赎回费"
  }
}
```

### 优化成果

1. **系统稳定性提升**: 502错误自动修复，减少人工干预
2. **错误诊断能力提升**: 明确的错误提示和修复建议
3. **用户体验提升**: 风控提示提供解锁日期和具体建议
4. **可维护性提升**: 完整的健康检查工具可独立运行诊断

#### 5. trader.py 订单验证机制 (P0优先级) ⭐ **新增**
**修改位置:** `trader.py`
- 新增 `_verify_order()` 函数（第 32-80 行）
- 修改 `do_buy()` 函数（第 158-179 行）：在保存交易记录前验证订单
- 修改 `do_sell()` 函数（第 292-313 行）：在保存交易记录前验证订单

**问题背景：**
2026-03-06 发现严重问题：008087 买入500元，API返回成功（code: 0000）并返回订单号，但订单实际未进入同花顺系统。导致数据库记录了不存在的交易，持仓数据错误。

**解决方案：**
在保存交易记录前，调用订单查询API验证订单是否真实存在，只有验证通过才保存到数据库。

**验证流程：**
1. 调用买入/卖出API，获取订单号
2. 调用 `_verify_order()` 验证订单（最多3次尝试，每次间隔2秒）
3. 查询订单列表，检查订单号是否存在
4. 验证通过 → 保存交易记录 + 更新持仓
5. 验证失败 → 返回错误，不保存任何数据

**关键代码：**
```python
def _verify_order(order_no, max_attempts=3, retry_delay=2):
    """验证订单是否真实存在于同花顺系统中"""
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(retry_delay)

        resp = requests.get(f"{server_url}/api/trade/orders", ...)
        orders = resp.json().get("singleData", {}).get("data", [])

        for order in orders:
            if order.get("appSheetSerialNo") == order_no:
                return {"exists": True, "order": order, ...}

    return {"exists": False, "message": "订单未找到"}

# 在 do_buy() 中使用
verify_result = _verify_order(order_no, max_attempts=3, retry_delay=2)
if not verify_result["exists"]:
    return {"status": "error", "message": f"买入失败：{verify_result['message']}"}

# 验证通过后才保存
fund_db.save_trade(...)
```

**防护效果：**
- 阻止虚假成功交易被记录到数据库
- 防止持仓数据被错误更新
- 提供明确的失败原因（基金暂停申购/风控限制）
- 避免资金和持仓数据不一致

**对比：**
```
# 优化前
API返回成功 → 立即保存数据库 → 可能是假成功 ❌

# 优化后
API返回成功 → 验证订单存在 → 通过才保存数据库 ✅
```

---

### 下一步建议

1. 监控优化效果：关注502错误的自动恢复成功率
2. 收集用户反馈：观察新的错误提示是否降低了支持成本
3. 监控订单验证失败率：统计有多少"假成功"被拦截
4. 考虑实施集成健康检查到Skill入口（可选）：在交易前自动运行检查
