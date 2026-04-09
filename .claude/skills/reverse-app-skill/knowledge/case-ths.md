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

## 问财 (iwencai) 接口逆向

### 目标
问财自然语言选股 API，实现完全脱离手机的服务端调用。

### 认证机制
- **API**: `POST https://www.iwencai.com/customized/chart/get-robot-data`
- **认证**: Cookie 中的 `v=` 字段（hexin-v token，60 字符 base64url）
- **存储位置**: WebView Cookie DB（SQLite），路径不固定：
  - `app_webview/Default/Cookies`
  - `app_webview_<包名>/Default/Cookies`
- **有效期**: ~31 天（与 sess_tk JWT 同步）

### 反爬机制（极严格）
1. **Token 级验证码锁定**: 短时间连续请求触发 captcha，绑定 token 而非 IP，不可自动恢复
2. **Frida 检测**: attach 超时，spawn 模式下 360 加固阻止 Java bridge
3. **360 加固**: OkHttp interceptor 需要延迟重试才能注入

### 成功方案
**策略 8 (Cookie DB 直读)** — 不依赖 OkHttp interceptor，直接从 SQLite 读取全部 cookie：

```java
// CookieDbWatcher: 延迟 15s 启动 + 每 30 分钟轮询
SQLiteDatabase db = SQLiteDatabase.openDatabase(dbPath, null, OPEN_READONLY);
Cursor c = db.rawQuery(
    "SELECT value FROM cookies WHERE host_key='.10jqka.com.cn' AND name=?",
    new String[]{"v"});
// 读取后上报到服务端
```

**频率控制**: 服务端每次请求间隔 ≥60 秒，防止触发验证码

### Token 刷新（当被验证码锁定时）
```bash
# 1. 删除 WebView Cookie DB（强制 App 重新获取全新 token）
adb shell "su -c 'rm /data/data/com.hexin.plat.android/app_webview*/Default/Cookies'"
# 2. 重启 App → CookieDbWatcher 自动读取新 token 并上报
adb shell "am force-stop com.hexin.plat.android && am start ..."
```

### 失败方案记录
| 方案 | 结果 | 原因 |
|------|------|------|
| 直接 HTTP + v token | ❌ | 连续请求触发验证码 |
| OkHttp 代理转发 | ❌ | 代理请求也触发验证码 |
| Frida attach | ❌ | 反 Frida 检测 |
| Frida spawn (patched) | ❌ | strongR-frida 无 Java bridge |
| Frida spawn (原版) | ❌ | 360 加固阻止 Java bridge |
| **Cookie DB 直读 + 频率控制** | **✅** | 不触发任何检测 |

## Python 工具
- `api/server.py` — FastAPI 服务 (8900 端口)
- `api/ths_fund_client.py` — 异步 API 客户端
- `api/auth_manager.py` — 认证管理器（自动刷新）
- `api/fund_login_client.py` — 密码 MD5 登录
