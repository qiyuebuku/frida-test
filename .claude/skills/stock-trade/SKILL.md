---
name: stock-trade
display_name: 股票交易
icon: show_chart
description: 基于同花顺 WebView JSBridge 的 A 股交易接口
category: finance
user-invocable: true
commands:
  - id: status
    name: 账户状态
    description: 查看资产、持仓、可用资金
    input: none
    executor: claude
    estimated_time: 30

  - id: positions
    name: 持仓详情
    description: 查看当前持仓股票明细
    input: none
    executor: claude
    estimated_time: 30

  - id: orders
    name: 委托查询
    description: 查看今日和历史委托单
    input: none
    executor: claude
    estimated_time: 30

  - id: history
    name: 历史成交
    description: 查看历史成交记录
    input: none
    executor: claude
    estimated_time: 30
---

# 股票智能交易 Skill

## 概述

本 Skill 通过 Hook 同花顺 App 的 WebView JSBridge 接口，实现 A 股股票交易功能。与基金交易（fund-trade）使用 HTTP API 不同，股票交易走的是本地 WebView + JSBridge 架构。

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                    同花顺 App (股票交易)                       │
├─────────────────────────────────────────────────────────────┤
│  Native Layer (Java)                                         │
│  ├── BridgeWebView$b (WeiTuoWebViewBridge)                  │
│  │   └── sendMessageFromJs(String json)                     │
│  ├── WebViewJavaScriptBridgePlus                            │
│  │   └── onActionEvent / onClearOnlineCache                 │
│  └── WebViewBridge (FalconJavaInterface)                    │
│      └── invokeAsync / invokeSync                           │
├─────────────────────────────────────────────────────────────┤
│  WebView Layer                                               │
│  ├── database_adapter.html (本地资产计算)                     │
│  ├── calcLocal.html (本地计算页面)                           │
│  └── WebViewJavascriptBridge (JS 库)                        │
├─────────────────────────────────────────────────────────────┤
│  Data Layer                                                  │
│  └── SQLite Database (xcs2.db)                              │
│      ├── stock_position (持仓)                               │
│      ├── account_d (资产)                                    │
│      ├── stock_history (历史成交)                            │
│      ├── daily_trans (当日成交)                              │
│      └── money_history (资金流水)                            │
└─────────────────────────────────────────────────────────────┘
```

## 已发现的接口

### 1. JSBridge 接口

**Java → JS (Native to Web):**
```javascript
WebViewJavascriptBridge._handleMessageFromNative(json)
```

**JS → Java (Web to Native):**
```javascript
WeiTuoWebViewBridge.sendMessageFromJs(json)
```

### 2. 消息格式

**请求格式 (JS → Java):**
```json
[{
  "handlerName": "executeSql",
  "data": {
    "sqlString": "SELECT * FROM stock_position WHERE fund_key = ?",
    "values": ["encrypted_fund_key"],
    "type": "query"
  },
  "callbackId": "cb_1_1772702350328"
}]
```

**响应格式 (Java → JS):**
```json
{
  "responseData": {"result": [...]},
  "responseId": "cb_1_1772702350328"
}
```

### 3. 已发现的 Handler

| Handler | 功能 | 参数 |
|---------|------|------|
| `wt_xcslocal_register_complete` | 注册完成通知 | `{}` |
| `executeSql` | 执行 SQL 查询 | `sqlString, values, type` |
| `decryptAES` | AES 解密 | `value` |
| `bs_point_list` | 买卖点列表 | `fundkey, userid, startdate, enddate` |

### 4. 数据库表结构

**stock_position (持仓表):**
| 字段 ID | 名称 | 说明 |
|---------|------|------|
| 2102 | stock_code | 证券代码 |
| 2103 | stock_name | 证券名称 |
| 2106 | stock_account | 股东账户 |
| 2108 | market | 市场代码 |
| 2117 | stock_remain | 证券余额(总数) |
| 2118 | stock_freeze | 冻结数量 |
| 2121 | stock_avl | 股票可用余额 |
| 2122 | price_buy_av | 买入均价 |
| 2124 | price_curr | 最新价格 |
| 2125 | stock_market_value | 股票市值 |
| 2147 | stock_profit | 浮动盈亏 |
| 3616 | profit_loss_ratio | 盈亏比例 |

**stock_entrust (委托表):**
| 字段 ID | 名称 | 说明 |
|---------|------|------|
| 2126 | entrust_count | 委托数量 |
| 2127 | entrust_price | 委托价格 |
| 2128 | trans_count | 成交数量 |
| 2129 | trans_price | 成交价格 |
| 2135 | contract_NO | 合同编号 |
| 2109 | op | 操作类别 |

**业务代码 (op):**
| 代码 | 含义 |
|------|------|
| 4001 | 证券卖出 |
| 4002 | 证券买入 |
| 4018 | 股息入账 |
| 4015 | 红股入账 |

## 账户信息

当前已捕获的账户信息：

| 字段 | 值 |
|------|------|
| 券商 | 川财证券 (qsid=16) |
| 用户ID | 690359103 |
| 资金账号 | 9133857 |
| fund_key | 7qZ1IO8msos2SsiJgFqWREwrMVnGfXlV974+tulFq0k= (AES 加密) |
| fund_key 解密 | 690359103_9133857_16 |

## 可用的查询 SQL

通过 JSBridge `executeSql` Handler 可以执行以下查询：

### 持仓查询
```sql
SELECT stock_code, stock_name, stock_avl, price_buy_av, price_curr,
       stock_market_value, stock_profit, profit_loss_ratio, market
FROM stock_position
WHERE fund_key = ?
```

### 资产查询
```sql
SELECT fund_key, money_type, money_remain, money_freeze, money_avl,
       asset_total, can_draw
FROM account_d
WHERE fund_key = ?
```

### 历史成交查询
```sql
SELECT trans_date, trans_time, stock_code, stock_name, op, op_name,
       trans_count, price_trans, stock_remain, market
FROM stock_history
WHERE fund_key = ? AND (op='1' OR op='2')
ORDER BY trans_date DESC
```

### 当日委托查询
```sql
SELECT entrust_date, entrust_time, stock_code, stock_name, op,
       entrust_count, entrust_price, trans_count, trans_price, contract_NO
FROM stock_entrust
WHERE fund_key = ?
ORDER BY entrust_date DESC
```

**fund_key 格式**: AES 加密的 `{userid}_{account}_{qsid}`
- 当前账户: `7qZ1IO8msos2SsiJgFqWREwrMVnGfXlV974+tulFq0k=`
- 解密后: `690359103_9133857_16`

## 参数解析

```bash
/stock-trade <command> [options]
```

| 命令 | 说明 | 状态 |
|------|------|------|
| `status` | 查看账户状态（资产、持仓、可用资金） | ✅ 可通过 JSBridge |
| `positions` | 查看持仓详情 | ✅ 可通过 JSBridge |
| `orders` | 查看委托单（今日、历史） | ✅ 可通过 JSBridge |
| `history` | 查看历史成交 | ✅ 可通过 JSBridge |
| `buy <股票代码> <数量> <价格>` | 买入股票 | ❌ 需要 Native Hook |
| `sell <股票代码> <数量> <价格>` | 卖出股票 | ❌ 需要 Native Hook |
| `cancel <订单号>` | 撤销委托 | ❌ 需要 Native Hook |
| `quote <股票代码>` | 获取实时行情 | ✅ 可通过 OkHttp |

## 前置条件

1. **同花顺 App 已登录股票账户**（川财证券）
2. **Zygisk Hook 模块已加载**（THSHook）
3. **手机与 WSL2 在同一网络**

## Hook 模块路径

- 源码：`/home/yuyang/frida-test/ths/app/src/main/java/com/yuyang/thshook/MainHook.java`
- 部署：`/data/adb/modules/thshook_zygisk/dex/`

## 核心发现：分层架构

经过深入分析，同花顺股票交易模块采用**分层架构**：

| 功能 | 技术栈 | 可 Hook |
|------|--------|---------|
| 持仓/资产查询 | WebView JSBridge (WeiTuoWebViewBridge + executeSql) | ✅ 可以 |
| 行情数据 | OkHttp + JSBridge (UnifiedRequestBridge, FalconJavaInterface) | ✅ 可以 |
| **股票交易 (买入/卖出)** | **原生 SDK (JNI/Native)** | ❌ 无法通过 Java Hook |

### 交易 SDK 分析

**错误消息格式**（从对话框捕获）:
```
[251067][有股东限制][stock_account=0926764077,restriction_t= AB]
```

**交易流程**:
1. Java UI 层调用 Native 方法
2. Native 代码通过专有协议连接券商服务器（可能是恒生 SDK）
3. 券商返回响应（包括错误码）
4. Native 代码回调 Java 层显示对话框

**账户限制说明**:
- 错误码 `251067` = 有股东限制
- `restriction_t= AB` = A股和B股都受限
- 新开户需要 T+1 日（开户成功后第二个交易日）才能交易

## 当前进度

### 已完成
- [x] 确认股票交易走 WebView + JSBridge 架构（持仓/资产查询）
- [x] 分析 JSBridge 数据格式和接口
- [x] Hook Java 层股票数据接口 (addJavascriptInterface, sendMessageFromJs)
- [x] Hook SQLite 数据库操作
- [x] 捕获账户初始化数据
- [x] 捕获数据库 Schema 定义
- [x] **确认交易操作走原生 SDK (JNI)**

### 可实现功能（基于当前 Hook）
- [x] 持仓查询 (stock_position 表)
- [x] 资产查询 (account_s_shard 表)
- [x] 委托查询 (stock_entrust 表)
- [x] 历史成交查询 (stock_history 表)
- [x] 当日成交查询 (daily_trans 表)

### 无法通过 Java Hook 实现
- [ ] 股票买入（需要 Frida 或 Native Hook）
- [ ] 股票卖出（需要 Frida 或 Native Hook）
- [ ] 撤单（需要 Frida 或 Native Hook）

## 替代方案

### 方案 A: 使用 Frida Hook Native 层
1. 找到恒生 SDK 的 JNI 接口 (libhstrade.so 或类似)
2. 使用 Frida hook native 函数
3. 拦截交易参数并替换

### 方案 B: 模拟 UI 操作
1. 使用 ADB 模拟点击进入买入页面
2. 输入股票代码、价格、数量
3. 点击买入按钮
4. 读取结果对话框

### 方案 C: 抓包分析券商协议
1. 使用 mitmproxy + frida-ssl-pinning-bypass
2. 分析同花顺与券商服务器的通信协议
3. 直接构造交易请求（需要解决加密/签名问题）

## API 接口

### 代理服务器端点

Hook 模块在手机上启动 HTTP 代理服务器（端口 18900），提供以下端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/stock/status` | GET | 数据库状态和表列表 |
| `/stock/positions` | GET | 持仓查询 |
| `/stock/assets` | GET | 资产查询 |
| `/stock/orders` | GET | 委托查询 |
| `/stock/history` | GET | 历史成交 |
| `/stock/daily` | GET | 当日成交 |
| `/stock/databases` | GET | 列出所有数据库文件 |
| `/stock/opendb?path=xxx` | GET | 打开指定数据库 |
| `/stock/query?sql=xxx` | GET | 执行任意 SQL（调试用） |

### Python 客户端

```python
from ths_stock_client import THSStockClient

async with THSStockClient(phone_ip="192.168.111.58") as client:
    # 获取资产
    assets = await client.get_assets()
    print(f"总资产: {assets.total_asset}")
    print(f"可用资金: {assets.money_avl}")

    # 获取持仓
    positions = await client.get_positions()
    for p in positions:
        print(f"{p.stock_code} {p.stock_name}: {p.stock_remain}股")

    # 获取投资组合摘要
    summary = await client.get_portfolio_summary()
```

### FastAPI 服务器

启动服务器：
```bash
cd /home/yuyang/frida-test/.claude/skills/stock-trade
uvicorn server:app --host 0.0.0.0 --port 8001
```

API 端点：
- `GET /status` - 数据库状态
- `GET /positions` - 持仓列表
- `GET /assets` - 资产信息
- `GET /orders` - 委托列表
- `GET /history` - 历史成交
- `GET /daily` - 当日成交
- `GET /portfolio` - 投资组合摘要

## 开发说明

### 调试方法

1. 启动 App 并进入股票交易页面
2. 查看日志：
   ```bash
   adb logcat -s THSHook
   ```

3. 搜索特定数据：
   ```bash
   adb logcat -d -s THSHook | grep "JSBridge\|SQLite"
   ```

4. 测试 API：
   ```bash
   curl http://192.168.111.58:18900/stock/status
   curl http://192.168.111.58:18900/stock/assets
   ```

### 添加新 Hook

编辑 `MainHook.java`，添加新的 Hook 方法，然后：

```bash
# 编译
cd /home/yuyang/frida-test/ths && ./gradlew :app:assembleDebug

# 提取 DEX
unzip -o app/build/outputs/apk/debug/app-debug.apk "*.dex" -d /tmp/ths_hook_dex/

# 部署
adb push /tmp/ths_hook_dex/*.dex /sdcard/Download/
adb shell "su -c 'cp /sdcard/Download/classes*.dex /data/adb/modules/thshook_zygisk/dex/'"

# 重启 App
adb shell am force-stop com.hexin.plat.android
adb shell monkey -p com.hexin.plat.android -c android.intent.category.LAUNCHER 1
```
