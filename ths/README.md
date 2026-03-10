# 同花顺基金API逆向工程总结

## 🎯 项目成果

成功破解同花顺基金交易API的认证机制，实现**纯HTTP调用**，无需WebView依赖。

**核心突破**：发现认证参数必须放在URL Query参数中（而非Header），从而绕过403错误。

---

## 🔧 使用的技术栈

### 主要Hook框架：Zygisk模块

**位置**：`/home/yuyang/frida-test/ths/app/src/main/java/com/yuyang/thshook/MainHook.java`

**部署**：
- Root方案：KernelSU Ultra
- Hook框架：Zygisk + Pine (ART Hook)
- 安装位置：`/data/adb/modules/thshook_zygisk/`

**功能**：
- ✅ Hook JSBridge (`clientRequestHX`) - 捕获API调用
- ✅ Hook WebView.addJavascriptInterface
- ✅ Hook WebView.evaluateJavascript
- ✅ HTTP代理服务器（端口18900）
- ✅ 提供`/auth`端点获取认证参数

**日志标签**：`THSHook`（不是MainHook）

### 调试工具：Frida（临时使用）

**使用场景**：仅在最后阶段用于调试OkHttp请求层

**作用**：
- 🔍 Hook `okhttp3.Request.Builder.build()`
- 🔍 捕获完整的HTTP请求格式（Headers + Query参数）
- 🔍 **发现关键突破**：认证参数在URL Query中，而非Header！

**脚本**：`/tmp/hook_http_headers.js`

**运行命令**：
```bash
# 1. 启动frida-server（手机上）
su -c '/data/local/tmp/frida-server -l 0.0.0.0:27042 &'

# 2. 端口转发
adb forward tcp:27042 tcp:27042

# 3. 运行Hook脚本
frida -H localhost:27042 -n "同花顺" -l /tmp/hook_http_headers.js
```

**捕获结果**：
```
URL: https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/100113970166?key1=7246091a5f126b63&key2=2293a78f6581c12bbb334759458d4de3&key5=eyJjSWQi...&key3=100113970166&key4=auth

Headers:
  cookie: user_status=0; userid=690359103; ...
  User-Agent: Hexin_Gphone/11.48.03 ...
  custId: 100113970166
  token: eyJjSWQi...
  source: SDK
```

---

## 📋 技术对比

| 工具 | 作用 | 是否必需 | 使用频率 |
|------|------|---------|---------|
| **Zygisk模块** | 主要Hook框架，捕获JSBridge调用 | ✅ 必需 | 持续运行 |
| **Frida** | 临时调试工具，Hook OkHttp层 | ❌ 可选 | 仅调试时 |
| **ADB自动化** | 触发API请求 | ✅ 必需 | Token更新时 |
| **HTTP代理(18900)** | 获取认证参数 | ✅ 必需 | Token更新时 |

---

## 🔑 认证机制

### 完整请求格式

```http
GET /rs/fundpositionquery/fundpositionassemble/100113970166?key1=XXX&key2=XXX&key5=XXX&key3=XXX&key4=auth
Host: trade.5ifund.com
Cookie: user_status=0; userid=690359103; sess_tk=eyJ0eXAi...
User-Agent: Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 ...
custId: 100113970166
token: eyJjSWQiOiIxMDAxMTM5NzAxNjYi...
source: SDK
```

### 认证参数说明

**URL Query参数**（关键！必须在URL中）：
- `key1`: 会话密钥1（16字符十六进制）
- `key2`: 会话密钥2（32字符十六进制）
- `key3`: 客户ID (custId)
- `key4`: 固定为 "auth"
- `key5`: JWT Token（Base64编码）

**HTTP Headers**：
- `cookie`: 包含sess_tk（JWT）、userid等
- `token`: 与key5相同的JWT Token
- `custId`: 客户ID
- `source`: 固定为 "SDK"
- `User-Agent`: 同花顺App的完整UA

**Token有效期**：28天

---

## 🚀 使用方法

### 1. Token更新流程（每28天一次）

```bash
# 确保Zygisk模块已安装并运行
adb shell "su -c 'ls /data/adb/modules/thshook_zygisk/'"

# 端口转发
adb forward tcp:18900 tcp:18900

# 运行自动化脚本触发API请求
python scripts/goto_fund_holdings.py

# 获取认证参数
curl http://localhost:18900/auth

# 或从logcat获取
adb logcat -s THSHook:I | grep -E "(key1|key2|key5|token)"
```

### 2. 纯HTTP调用（28天内无需手机）

```python
from ths_fund_api import THSFundAPI

api = THSFundAPI()
holdings = api.get_holdings()  # 完全独立调用
print(f"总资产: {holdings['singleData']['fundGeneral']['sumValue']:.2f} 元")
```

---

## 📊 关键发现

### 之前的错误尝试

1. ❌ 将key5放在HTTP Header中 → 403 "非法访问"
2. ❌ 使用Cookie但缺少key参数 → 403 "Illegal authorization"
3. ❌ 尝试从电脑直接请求 → 403（因为参数位置错误）

### 正确方法（通过Frida发现）

✅ 将key1-key5放在**URL Query String**中 + 完整的Headers → 200 成功！

**发现工具**：
- Zygisk模块提供了初步的key5信息（通过HTTP代理API）
- **Frida Hook OkHttp**揭示了完整的请求格式（包括Query参数）
- 两者结合才完成了最终突破

---

## 📂 项目文件结构

```
/home/yuyang/frida-test/ths/
├── app/src/main/java/com/yuyang/thshook/
│   └── MainHook.java                    # Zygisk模块源码
├── README.md                             # 本文档
└── zygisk/                               # 编译输出（已部署到手机）

/home/yuyang/frida-test/.claude/skills/reverse-app-skill/
├── ths_fund_api.py                       # Python API客户端
├── scripts/
│   ├── adb_automation.py                 # ADB自动化库
│   └── goto_fund_holdings.py             # 自动导航脚本
└── examples/
    └── monitor_holdings.py               # 持仓监控示例

/home/yuyang/frida-test/docs/
└── THS_Fund_API_Analysis.md              # 详细逆向分析报告

/tmp/
├── hook_http_headers.js                  # Frida Hook脚本（临时）
├── frida_http_log.txt                    # Frida捕获的日志
├── ths_cookie.txt                        # 提取的Cookie
└── ths_api_config.json                   # API配置文件
```

---

## 🎯 核心优势

1. **Token长期有效**：28天内无需重新认证
2. **完全脱离App**：纯HTTP调用，可部署到云服务器
3. **认证参数固定**：同一session中key1-key5不变
4. **易于集成**：Python客户端，简单易用

---

## ⚠️ 技术总结

### 主要工作流程

1. **Zygisk模块**（持续运行）：
   - Hook JSBridge捕获API端点
   - 提供HTTP代理API获取部分认证信息
   - 捕获Cookie和基础token

2. **Frida**（调试阶段，这次会话中使用）：
   - Hook OkHttp捕获完整HTTP请求
   - **发现关键突破**：认证参数在Query中
   - 确定完整的请求格式

3. **Python客户端**（日常使用）：
   - 使用捕获的认证参数
   - 纯HTTP调用，无需Hook
   - 28天内完全独立

### 技术栈角色

- **Zygisk**：提供持久化Hook能力，是基础设施
- **Frida**：临时调试工具，在这次会话中发现了关键突破点
- **Python**：最终的实用工具，日常使用

---

**创建时间**：2026-03-08
**作者**：逆向工程研究
**版本**：v1.0

---

## ✅ 验证测试记录

### 2026-03-08: App 关闭状态测试

**测试目的**：验证是否真正脱离 App 依赖

**测试步骤**：
1. 强制关闭 App：`adb shell am force-stop com.hexin.plat.android`
2. 确认进程结束：`ps -A | grep hexin` → 无输出
3. 调用 API：`python3 ths_fund_api.py`
4. 重复测试：连续调用2次

**测试结果**：
```
✅ 测试1: HTTP 200, 数据正常返回
✅ 测试2: HTTP 200, 数据一致
✅ App 状态: 始终保持关闭
✅ 响应时间: ~1秒
```

**结论**：
- ✅ **完全独立运行确认**
- ✅ 无需 App 在后台
- ✅ 无需手机唤醒
- ✅ 纯 HTTP 调用成功

**待验证**：
- Token 长期有效性（28天，至 2026-03-24）
- 跨网络环境（IP 限制）
- 重启手机后的有效性
