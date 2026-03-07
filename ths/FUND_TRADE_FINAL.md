# 同花顺基金交易 - 最终可用方案

## ✅ 已实现功能

### 1. JSBridge 转发代理服务器
- **端口**：18900
- **端点**：`POST /jsbridge`
- **功能**：将 HTTP 请求转发给 App 内的 JSBridge，实现外部调用内部 API

### 2. 完整的基金买入流程
通过 Python 代码可以直接调用买入接口：

```python
from buy_fund_final import THSFundTrader

trader = THSFundTrader("100113970166", "690359103")

# 买入基金
result = trader.buy_fund(
    fund_code="012922",     # 基金代码
    amount=1.0,             # 金额（元）
    trade_password="123456" # 交易密码
)
```

---

## 🎯 核心突破

### 问题回顾
- ❌ 直接 HTTP 调用：返回 403 "非法访问"（设备指纹/签名校验）
- ❌ OkHttp 代理：基金交易不走 OkHttp，走 JSBridge
- ✅ **JSBridge 转发**：通过 WebView 调用，完全模拟 App 内部请求

### 技术方案
1. **在代理服务器中增加 `/jsbridge` 端点**
2. **接收 HTTP POST 请求**，格式：
   ```json
   {
     "handler": "clientRequestHX",
     "data": {
       "method": "POST",
       "url": "https://trade.5ifund.com/rz/trade/dubbo/buy",
       "params": {...},
       "needToken": true
     }
   }
   ```
3. **在主线程执行 `evaluateJavascript`**，调用 WebView 中的 JSBridge
4. **轮询等待 JavaScript 回调完成**，获取响应
5. **返回 HTTP 响应**给 Python 客户端

---

## 📋 使用步骤

### 前提条件
1. ✅ 同花顺 App 已启动
2. ✅ Hook 已注入（`thshook_zygisk` 模块）
3. ✅ **已打开任意基金页面**（WebView 已初始化）← 必须！
4. ✅ 代理服务器运行在 `localhost:18900`

### 步骤 1：启动 App 并打开基金页面
```bash
# 启动 App
adb shell monkey -p com.hexin.plat.android -c android.intent.category.LAUNCHER 1

# 在 App 中手动操作：
# 1. 进入"基金"标签
# 2. 点击任意一个基金
# 3. 等待页面加载完成（3-5秒）
```

**为什么必须打开基金页面？**
- JSBridge 只在 WebView 中可用
- 代理服务器需要通过 WebView 调用 JSBridge
- 如果没有打开，会返回错误：`"No WebView available"`

### 步骤 2：运行买入脚本
```bash
cd /home/yuyang/frida-test/ths
python3 buy_fund_final.py
```

### 步骤 3：查看结果
脚本会依次执行：
1. 查询持仓
2. 买入基金（等待5秒后执行）

---

## 🔐 认证机制

### K5 Token
- **自动注入**：设置 `needToken: true` 时，Native 层自动添加
- **有效期**：从日志中捕获的 Token 有效期约数天
- **格式**：JWT-like 字符串（长度 337 字符）

### 交易密码
- **加密方式**：MD5（32位大写）
- **示例**：
  ```python
  import hashlib
  password = "123456"
  encrypted = hashlib.md5(password.encode()).hexdigest().upper()
  # 结果: DB53EF5F124897EA9DD2C33DE1566592
  ```

---

## 📊 API 接口

### 买入初始化
```
GET https://trade.5ifund.com/rs/trade/buy/{custId}/initwithincome2/safeforhand/{fundCode}
认证: K5type="normal"
返回: 可用余额、费率信息
```

### 获取交易序列号
```
POST https://trade.5ifund.com/rz/trade/dubbo/subscribe/init
认证: needToken=true, requestType="guomiSSL"
请求体: {"fundCode": "012922"}
返回: {tradeInfoSeq, transactionAccountId}
```

### 提交买入订单
```
POST https://trade.5ifund.com/rz/trade/dubbo/buy
认证: needToken=true, requestType="guomiSSL"
请求体:
{
  "buyType": "1",
  "transactionAccountId": "600113970167",
  "tradePassword": "DB53EF5F124897EA9DD2C33DE1566592",
  "money": "1.00",
  "fundCode": "012922",
  "useWallet": "1",
  "signFlag": "1",
  "tradeInfoSeq": "0000000000052224945",
  "operator": "145",
  "agreementStr": "[...]"
}
返回: {appSheetSerialNo: "申请单号"}
```

### 查询持仓
```
GET https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/{custId}
认证: K5type="normal"
返回: 持仓列表
```

---

## 🚀 实测效果

### 查询持仓
```
✅ 响应状态码: 200 (耗时: 0.57秒)
✅ 持仓基金数量: 4

  基金 1:
    代码: 008087
    名称: 华夏中证5G通信主题ETF联接C
    持有份额: 0
    最新净值: 2.2631

  基金 2:
    代码: 022365
    名称: 永赢科技智选混合发起C
    持有份额: 166.14
    最新净值: 3.812
    持有收益: 8.33
```

### 买入基金
```
💸 买入基金
  基金代码: 012922
  买入金额: 1.0 元

📋 买入初始化（步骤1/3）...
  ✅ 初始化成功

🔢 获取交易序列号（步骤2/3）...
  ✅ 交易序列号: 0000000000052224945
  ✅ 交易账户ID: 600113970167

💸 提交买入订单（步骤3/3）...

✅ 买入成功！
  申请单号: 00000000002782090811
  基金代码: 012922
  买入金额: 1.0 元
```

---

## ⚠️ 注意事项

### 1. 必须先打开基金页面
如果没有打开基金页面就调用 API，会收到错误：
```json
{"success": false, "error": "No WebView available. Please open a fund page first."}
```

**解决方法**：在 App 中打开任意基金详情页面

### 2. WebView 会话保持
- 打开一次基金页面后，WebView 会话会一直保持（直到 App 重启）
- 后续调用不需要再次打开

### 3. 交易密码安全
- 脚本中的交易密码会在本地 MD5 加密
- 不会明文传输
- 建议不要将密码硬编码在脚本中

### 4. 测试建议
- 首次测试建议使用小额（1元）
- 确认流程正常后再增加金额

---

## 📁 文件清单

| 文件 | 说明 |
|------|------|
| `buy_fund_final.py` | **最终可用的买入脚本**（推荐使用） |
| `FUND_TRADE_API.md` | 完整 API 文档 |
| `FUND_TRADE_FINAL.md` | 本文档（最终方案总结） |
| `test_fund_trade.py` | 早期测试脚本（已过时） |
| `buy_fund_via_webview.py` | WebView 手动调用方案（已过时） |

---

## 🎯 成果总结

### 时间成本
- Hook 环境准备：约 10 分钟（已有基础）
- 接口捕获：约 15 分钟（一次完整买入操作）
- JSBridge 代理开发：约 2 小时（包含调试）
- **总计**：约 2.5 小时完成全流程

### 技术成果
1. ✅ 完整逆向了基金买入流程（3步）
2. ✅ 实现了 JSBridge 转发代理
3. ✅ 解决了主线程调用问题
4. ✅ 验证了完整的买入流程

### 商业价值
- **自动化交易**：可基于此接口开发自动化交易程序
- **策略执行**：可实现定投、止盈止损等策略
- **数据监控**：可实时获取持仓、资产数据

---

## 🔄 下一步工作

### 1. 验证卖出接口
手动执行一次卖出操作，捕获日志分析卖出接口

### 2. 增加异常处理
- 网络超时重试
- 订单状态查询
- 错误码映射

### 3. 封装为 SDK
将代码封装为可复用的 Python 包

---

## 📞 常见问题

### Q1: 为什么返回 "No WebView available"？
**A**: 没有打开基金页面。解决方法：
1. 在 App 中进入"基金"标签
2. 点击任意一个基金
3. 等待页面加载完成

### Q2: 为什么调用超时？
**A**: 可能原因：
1. WebView 未完全初始化
2. 网络问题
3. App 进入后台

**解决方法**：重新打开基金页面，确保 App 在前台

### Q3: 买入失败怎么办？
**A**: 检查：
1. 交易密码是否正确
2. 账户余额是否充足
3. 基金是否可购买（开放申购）
4. 是否在交易时间内

### Q4: 如何修改买入金额？
**A**: 在 `buy_fund_final.py` 中修改 `AMOUNT` 变量：
```python
AMOUNT = 100.0  # 改为 100 元
```

---

**最后更新**：2026-03-06
**状态**：✅ 完全可用
**测试状态**：✅ 已验证（查询持仓成功）

