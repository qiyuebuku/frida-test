# 案例：同花顺基金 (com.hexin.plat.android)

## 基本信息
- **加固**: 360加固
- **难度**: ⭐⭐⭐⭐
- **详细文档**: /home/yuyang/frida-test/ths/docs/
- **核心代码**: /home/yuyang/frida-test/ths/app/src/main/java/com/yuyang/thshook/MainHook.java

## 逆向目标
- 基金持仓查询、资产查询
- 基金买入/卖出自动化
- Token 自动续期（完全脱离手机）

## 成功方案
Zygisk + Pine + 内嵌 HTTP 代理（18900 端口）

**四个 Hook 点**:
1. OkHttp 拦截 — 捕获完整 HTTP 请求（含 Query 参数）
2. WebView JSBridge — 拦截 JavaScript 与 Native 通信
3. Cipher — 解密本地存储的认证参数
4. SQLiteDatabase.rawQuery — 直接查询本地股票持仓

## 核心发现

### 认证参数位置（最重要的坑）
- **错误**: 把 key1-key5 放在 HTTP Header ❌
- **正确**: 放在 URL Query 参数中 ✅ + Cookie 和 token 在 Header

### 认证参数结构
```json
{
  "key1": "设备ID(16字符)",
  "key2": "设备签名(32字符)",
  "key3": "custId",
  "key4": "auth",
  "key5": "JWT Token(28天有效期)"
}
```

### Token 自动刷新（无需手机）
```python
POST /rz/account/login/noauth/v1/result/safe/check
Body: {
    "password": "密码MD5",  # 不需要明文密码
    "account": "custId",
    ...
}
→ 获得新 JWT Token
```

### 基金买入三步流程
1. 初始化: GET `/rz/trade/dubbo/buy/init`
2. 获取序列号: POST `/rz/trade/dubbo/buy/getTradeInfoSeq`
3. 提交: POST `/rz/trade/dubbo/buy`

## Python 工具
- `api/server.py` — FastAPI 服务 (8900 端口)
- `api/ths_fund_client.py` — 异步 API 客户端
- `api/auth_manager.py` — 认证管理器（自动刷新）
- `api/fund_login_client.py` — 密码 MD5 登录
