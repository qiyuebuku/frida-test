# 同花顺基金交易 API 分析

## 数据来源
- 采集时间：2026-03-06
- 测试操作：完整的基金买入流程（基金代码 012922，金额 1.00 元）
- Hook 方式：Zygisk + JSBridge 拦截

## 关键发现

### 架构特点
1. **前端是 WebView**：基金交易页面是 H5 页面，通过 JavaScript Bridge 与 Native 通信
2. **请求通过 JSBridge 代理**：所有 HTTP 请求不直接从 WebView 发起，而是通过 `clientRequestHX` handler 由 Native 层代理
3. **认证机制**：`needToken: true` 的请求会由 Native 层自动添加认证 Token（key5）

---

## 核心 API 接口

### 1. 买入初始化（获取交易信息）
```
URL: https://trade.5ifund.com/rs/trade/buy/{custId}/initwithincome2/safeforhand/{fundCode}
Method: GET
认证: K5type="normal"

路径参数：
- custId: 客户ID（例如：100113970166）
- fundCode: 基金代码（例如：012922）

用途：获取可用余额、费率、协议等信息
```

### 2. 买入初始化（获取交易序列号）
```
URL: https://trade.5ifund.com/rz/trade/dubbo/subscribe/init
Method: POST
认证: needToken=true, requestType="guomiSSL"

请求头：
{
  "Accept": "application/json, text/plain, */*",
  "Content-Type": "application/x-www-form-urlencoded",
  "referer": "https://trade.5ifund.com/hxapp/ifundBuyInit/dist/index.html?...",
  "custId": "100113970166",
  "source": "SDK"
}

请求体：
{
  "fundCode": "012922"
}

返回：
- tradeInfoSeq: 交易序列号（用于后续买入请求）
- transactionAccountId: 交易账户ID
```

### 3. **核心买入接口**（提交订单）
```
URL: https://trade.5ifund.com/rz/trade/dubbo/buy
Method: POST
认证: needToken=true

请求参数：
{
  "buyType": "1",                                // 买入类型（1=普通买入）
  "transactionAccountId": "600113970167",       // 交易账户ID（从 init 获取）
  "tradePassword": "DB53EF5F124897EA9DD2C33DE1566592",  // MD5加密的交易密码
  "money": "1.00",                              // 买入金额（元）
  "fundCode": "012922",                         // 基金代码
  "useWallet": "1",                             // 是否使用钱包（1=是）
  "signFlag": "1",                              // 签名标识
  "tradeInfoSeq": "0000000000052224945",        // 交易序列号（从 init 获取）
  "operator": "145",                            // 操作员代码
  "agreementStr": "[...]"                       // 协议列表（JSON 数组字符串）
}

返回：
- appSheetSerialNo: 申请单号（用于查询结果）
```

### 4. 买入结果查询
```
URL: https://trade.5ifund.com/rs/tz/trade/paywithcoupon/{custId}/result
Method: POST
认证: K5type="normal", requestType="http"

请求参数：
{
  "appSheetSerialNo": "00000000002782090811"  // 申请单号（从买入接口返回）
}

用途：查询买入是否成功，获取订单详情
```

---

## 辅助 API

### 查询持仓
```
URL: https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/{custId}
Method: GET
用途：查询当前基金持仓
```

### 查询资产
```
URL: https://trade.5ifund.com/rs/incomequery/queryzcsharemobilehomenine/{custId}
Method: GET
用途：查询总资产和收益
```

### 查询账户状态
```
URL: https://trade.5ifund.com/rz/account/dubbo/accountInfo/getCustAccoStatus
Method: POST
认证: needToken=true
请求体: {"version": "VOCATIONCODE_22"}
用途：查询账户状态（是否可交易）
```

### 查询钱包余额
```
URL: https://trade.5ifund.com/rz/wallet/dubbo/v1/queryWalletHomePage
Method: POST
认证: needToken=true
请求头: {"custId": "100113970166", "source": "SDK"}
请求体: {"custId": "100113970166"}
```

---

## 认证机制

### 1. K5 Token（核心认证）
- **存储位置**：本地 AES 加密文件（`key5` 字段）
- **格式**：JWT-like 字符串，例如：
  ```
  eyJjSWQiOiIxMDAxMTM5NzAxNjYiLCJleHAiOjE3NzUzNjI1MTE5MzksInNvdSI6IlNESyIsInR5cGUiOiJQV0QiLCJ2IjoiMS4wIn0=.Z0N0MldsYkpqNnJnVGt6TEhRQXJDLzNRMGdLRkFldGFBU2tsWjZGLzkzemxZbW1MMkxOb24...
  ```
- **用途**：在 `needToken: true` 的请求中，Native 层会自动在请求头中添加 K5 Token
- **过期时间**：从 `exp` 字段解析（UNIX 时间戳，毫秒）

### 2. 其他认证参数
```
custId: "100113970166"          // 客户ID
userId: "690359103"             // 用户ID
sessionId: "195d198e3a1ef6ebb8e08acd628dc3c4a"  // 会话ID
operator: "145"                 // 操作员代码
```

### 3. K5 Token 的捕获
在日志中可以看到 AES 加密的用户信息文件包含：
```json
{
  "100113970166": {
    "certificateNo": "610121199804283977",
    "certificateType": "0",
    "clientRiskRate": "4",
    "custId": "100113970166",
    "investorName": "阮雨阳",
    "key3": "100113970166",
    "key4": "auth",
    "key5": "eyJjSWQiOiIxMDAxMTM5NzAxNjYi..."  // ← K5 Token
  }
}
```

---

## 交易密码加密

### tradePassword 字段
- **明文示例**：用户输入的 6 位数字交易密码（如 `123456`）
- **加密后**：`DB53EF5F124897EA9DD2C33DE1566592`
- **加密算法**：**MD5（32位大写）**
- **验证**：
  ```python
  import hashlib
  password = "123456"
  encrypted = hashlib.md5(password.encode()).hexdigest().upper()
  print(encrypted)  # DB53EF5F124897EA9DD2C33DE1566592
  ```

---

## JSBridge 调用机制

### clientRequestHX Handler
WebView 中的 JavaScript 通过以下方式调用 Native HTTP 请求：

```javascript
window.WebViewJavascriptBridge.callHandler('clientRequestHX', {
  method: 'POST',
  url: 'https://trade.5ifund.com/rz/trade/dubbo/buy',
  params: { /* 请求参数 */ },
  Header: { /* 自定义请求头 */ },
  needToken: true,        // Native 自动添加 K5 Token
  K5type: 'normal',       // K5 加密类型
  requestType: 'guomiSSL' // 请求类型（guomiSSL=国密SSL，http=普通HTTP）
}, function(response) {
  // 处理响应
});
```

### K5type 类型
- `normal`: 普通认证（自动添加 K5 Token）
- `none`: 无认证

### requestType 类型
- `guomiSSL`: 使用国密 SSL（SM2/SM3/SM4）
- `http`: 普通 HTTP/HTTPS

---

## 完整的买入流程

### 步骤 1：初始化（获取交易参数）
```
GET https://trade.5ifund.com/rs/trade/buy/{custId}/initwithincome2/safeforhand/{fundCode}
```
返回：
- 可用余额
- 费率信息
- 协议列表

### 步骤 2：获取交易序列号
```
POST https://trade.5ifund.com/rz/trade/dubbo/subscribe/init
Body: {"fundCode": "012922"}
```
返回：
- `tradeInfoSeq`: 交易序列号
- `transactionAccountId`: 交易账户ID

### 步骤 3：提交买入订单
```
POST https://trade.5ifund.com/rz/trade/dubbo/buy
Body: {
  "buyType": "1",
  "transactionAccountId": "600113970167",  // 从步骤2获取
  "tradePassword": "DB53EF5F124897EA9DD2C33DE1566592",  // MD5加密
  "money": "1.00",
  "fundCode": "012922",
  "useWallet": "1",
  "signFlag": "1",
  "tradeInfoSeq": "0000000000052224945",  // 从步骤2获取
  "operator": "145",
  "agreementStr": "[...]"
}
```
返回：
- `appSheetSerialNo`: 申请单号

### 步骤 4：查询买入结果
```
POST https://trade.5ifund.com/rs/tz/trade/paywithcoupon/{custId}/result
Body: {"appSheetSerialNo": "00000000002782090811"}
```

---

## 卖出接口（推测）

基于买入接口的结构，卖出接口可能为：

### 卖出初始化
```
URL: https://trade.5ifund.com/rs/trade/sell/{custId}/init/safeforhand/{fundCode}
Method: GET
```

### 卖出提交
```
URL: https://trade.5ifund.com/rz/trade/dubbo/sell
或: https://trade.5ifund.com/rz/trade/dubbo/redeem  (赎回)
Method: POST
```

**建议**：执行一次卖出操作，捕获实际接口进行验证。

---

## 数据库本地存储

### SQLite 数据库
App 使用本地 SQLite 数据库存储交易记录：

```sql
-- 股票交易历史
SELECT trans_date, trans_time, stock_code, stock_name, op, op_name,
       trans_count, price_trans, stock_remain, market
FROM stock_history
WHERE fund_key = ?  -- AES加密的 fund_key
  AND (op='1' OR op='2')  -- 1=买入, 2=卖出
ORDER BY trans_date DESC
```

### fund_key 加密
- **密文**：`7qZ1IO8msos2SsiJgFqWREwrMVnGfXlV974+tulFq0k=`
- **算法**：AES-CBC
- **解密后**：`690359103_9133857_16` （userId_zjzh_qsid）

---

## 下一步工作

### 1. 验证卖出接口
执行一次基金卖出操作，捕获并分析：
- 卖出初始化 URL
- 卖出提交 URL
- 参数差异（如 `sellType`, `份额` vs `金额`）

### 2. K5 Token 认证绕过
两种方案：

#### 方案A：直接使用 Hook 捕获的 Token
```python
# 从 Hook 日志中提取
k5_token = "eyJjSWQiOiIxMDAxMTM5NzAxNjYi..."
custId = "100113970166"
userId = "690359103"

# 直接调用 API（需要手动构造请求头）
headers = {
    "key5": k5_token,
    "custId": custId,
    "source": "SDK",
    # ... 其他必要的请求头
}
```

**问题**：Token 可能在服务端校验设备指纹、IP 等，外部请求可能被拒绝。

#### 方案B：通过 JSBridge 代理（推荐）
在 Hook 中暴露一个 HTTP 代理服务器，接收外部请求后通过 `clientRequestHX` 转发：

```java
// MainHook.java 中已有的代理服务器（端口 18900）
// 外部请求 → 代理服务器 → JSBridge → Native 层发起请求 → 返回响应
```

**优势**：
- Token 由 Native 层自动注入
- 完全模拟 App 内部请求
- 绕过设备指纹校验

### 3. 编写交易 SDK
基于捕获的接口，编写 Python SDK：
```python
class THSFundTrader:
    def __init__(self, proxy_url="http://localhost:18900"):
        self.proxy = proxy_url
        self.cust_id = None

    def buy_fund(self, fund_code, amount, trade_password):
        """买入基金"""
        # 步骤1: 初始化
        init_data = self._init_buy(fund_code)

        # 步骤2: 提交订单
        order = self._submit_buy(
            fund_code=fund_code,
            amount=amount,
            trade_password=self._encrypt_password(trade_password),
            trade_info_seq=init_data['tradeInfoSeq'],
            trans_account_id=init_data['transactionAccountId']
        )

        # 步骤3: 查询结果
        return self._query_result(order['appSheetSerialNo'])
```

---

## 安全性分析

### 潜在风险
1. **交易密码为 MD5 加密**：MD5 已被认为不安全，容易被彩虹表破解
2. **K5 Token 无设备绑定**：如果 Token 被盗取，可能在其他设备使用
3. **无二次验证**：买入接口没有短信验证码等二次确认

### 防护措施
- **SSL Pinning**：App 使用国密 SSL（`guomiSSL`），防止中间人攻击
- **360 加固**：防止静态分析和代码注入
- **Session 管理**：`sessionId` 用于追踪会话状态

---

## 附录：日志示例

### 买入接口完整日志（已脱敏）
```
JSBridge.android.onActionEvent("[{
  \"handlerName\":\"clientRequestHX\",
  \"data\":{
    \"method\":\"POST\",
    \"url\":\"https://trade.5ifund.com/rz/trade/dubbo/buy\",
    \"params\":{
      \"buyType\":\"1\",
      \"transactionAccountId\":\"600113970167\",
      \"tradePassword\":\"DB53EF5F124897EA9DD2C33DE1566592\",
      \"money\":\"1.00\",
      \"fundCode\":\"012922\",
      \"useWallet\":\"1\",
      \"signFlag\":\"1\",
      \"tradeInfoSeq\":\"0000000000052224945\",
      \"operator\":\"145\",
      \"agreementStr\":\"[...]\"
    },
    \"needToken\":true
  },
  \"callbackId\":\"cb_71_1772786231138\"
}]")
```

---

## 总结

1. ✅ **已捕获核心买入接口**：`/rz/trade/dubbo/buy`
2. ✅ **认证机制分析完成**：K5 Token + custId
3. ✅ **交易密码加密算法**：MD5（32位大写）
4. ⏳ **待验证卖出接口**：需要执行卖出操作捕获日志
5. 💡 **推荐方案**：使用 JSBridge 代理服务器（端口 18900）绕过认证

下一步可以直接通过 `http://localhost:18900` 代理调用交易接口，无需处理 Token 认证！
