# Web 反爬与 WAF 绕过指南

本文档覆盖 Web 数据采集中遇到的反爬机制识别与绕过方案。与 App 逆向不同，Web 反爬的核心在于**模拟真实浏览器环境**。

---

## 反爬机制分类

### 1. TLS 指纹检测

**检测方式：** 服务端分析 TLS 握手过程中的 cipher suite 顺序、扩展列表、ALPN 协议等，识别客户端类型。

**现象：**
- 同样的 URL + Cookie + Headers，浏览器请求成功，curl/httpx/requests 返回 403/400/验证码
- 响应体为空或返回 WAF 挑战页面

**绕过方案：**
```python
# curl_cffi：模拟 Chrome/Firefox 的 TLS 指纹
from curl_cffi import requests as cf

session = cf.Session(impersonate="chrome120")  # 或 "firefox" "safari"
r = session.get("https://target.com/api/data", headers={...})
```

**可用的 impersonate 值：** `chrome120` / `chrome110` / `chrome100` / `safari17` / `firefox120`

**关键注意：**
- `httpx`、`requests`、标准 `curl` 都有独特的 TLS 指纹，会被检测
- `curl_cffi` 通过底层模拟解决了这个问题
- 如果 curl_cffi 也被检测，需要用真实浏览器（Playwright/Selenium）

### 2. Cookie 反爬（httpOnly + JS 挑战）

**检测方式：** 服务端设置 `httpOnly=true` 的关键认证 cookie，只有浏览器执行 JS 挑战后才能获得。

**现象：**
- `document.cookie` 拿不到关键 token（如 `xq_a_token`）
- 首页返回 JS 挑战页面而非真实内容
- 即使复制 `document.cookie` 发请求也返回 401/403

**识别方法：**
```html
<!-- WAF 挑战页特征 -->
<meta name="aliyun_waf_aa" content="...">        <!-- 阿里云 WAF -->
<meta name="__waf_captcha_key__" content="...">   <!-- 通用 WAF -->
<script src="/challenge.js"></script>               <!-- JS 挑战脚本 -->
```

**绕过方案：**
```python
# Step 1: 用 Playwright 获取完整 cookie（含 httpOnly）
# Playwright 的 context.cookies() 可以获取所有 cookie，包括 httpOnly
all_cookies = await page.context.cookies()

# Step 2: 构造 cookie 字符串
cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in all_cookies)

# Step 3: 用 curl_cffi 带上完整 cookie 请求
session = cf.Session(impersonate="chrome120")
r = session.get(url, headers={"Cookie": cookie_str, "Referer": origin_url})
```

**关键注意：**
- `document.cookie` 永远拿不到 httpOnly cookie，这是浏览器安全机制
- 必须通过浏览器自动化 API（Playwright `context.cookies()` / Selenium `driver.get_cookies()`）获取
- Cookie 有有效期（通常数小时到数天），需定期刷新

### 3. 阿里云 WAF（常见于国内网站）

**检测特征：**
```html
<meta name="aliyun_waf_aa" content="...">
<meta name="aliyun_waf_oo" content="...">
<meta name="aliyun_waf_00" content="...">
<script src="/u21pn7x6/r8lw5pzu/psk8uqfi"></script>  <!-- 路径随机但格式固定 -->
```

**WAF 工作流程：**
1. 首次访问 → 返回 JS 挑战页（含加密脚本）
2. 浏览器执行 JS → 计算验证值 → 自动提交
3. 服务端验证通过 → 设置 httpOnly cookie（如 `acw_tc`）
4. 后续请求带 cookie → 正常响应
5. **WAF 可能在请求 URL 中注入签名参数**（如 `md5__1038=...`），这是在网络层自动添加的

**绕过策略：**
- **不要试图模拟 JS 挑战**（加密算法会频繁更新）
- **用真实浏览器通过挑战 → 导出 cookie → curl_cffi 复用**
- cookie 刷新频率取决于服务端策略，通常 2-8 小时

### 4. Cloudflare（常见于国际网站）

**检测特征：**
```
HTTP/2 403
cf-ray: xxx
server: cloudflare
```

**绕过方案（按优先级）：**
1. `curl_cffi` + `impersonate="chrome120"` — 大部分场景够用
2. `cloudscraper` — 专门绕过 Cloudflare 的库
3. `undetected-chromedriver` — 修改版 ChromeDriver
4. 真实浏览器 + cookie 导出 — 终极方案

### 5. 请求频率限制与验证码

**检测方式：** 服务端监控请求频率，超过阈值触发验证码或临时封禁。

**关键区分 — 封禁粒度：**

| 粒度 | 现象 | 恢复方式 |
|------|------|---------|
| IP 级 | 换 IP 后恢复 | 代理池 |
| Cookie/Session 级 | 换 cookie 恢复 | 重新获取 session |
| **Token 级** | 换 IP/cookie 都无效 | 必须重新生成 token |
| 账号级 | 登录后也被封 | 换号或申诉 |

**Token 级封禁最难处理**（如雪球、问财）：一旦某个 token 触发验证码，该 token 永久失效，只能重新生成。

**防范策略：**
- 严格控制请求频率（逆向调试阶段尤其注意）
- 先单次验证成功，再逐步测试频率边界
- **切忌在调试阶段连续请求** — 一旦触发验证码，token 可能废掉

---

## 通用绕过工具链

### 推荐工具（按场景）

| 场景 | 工具 | 说明 |
|------|------|------|
| TLS 指纹绕过 | `curl_cffi` | Python 库，模拟浏览器 TLS |
| httpOnly cookie 获取 | Playwright `context.cookies()` | 获取包括 httpOnly 在内的所有 cookie |
| JS 渲染 | Playwright / Camoufox | 完整浏览器环境 |
| 反检测浏览器 | Camoufox / undetected-chromedriver | 修改指纹的浏览器 |
| Cloudflare 绕过 | `cloudscraper` / `curl_cffi` | 专用库 |
| 代理池 | 自建或第三方 | IP 级封禁时使用 |

### 请求头最佳实践

```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://target.com/",       # 必须与目标域名匹配
    "Origin": "https://target.com",          # 跨域请求需要
    "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
```

---

## Cookie 生命周期管理

当目标网站使用 httpOnly cookie + WAF 时，推荐的架构：

```
浏览器服务（Playwright）
  │  定期访问目标首页（通过 WAF 挑战）
  │  context.cookies() 导出完整 cookie
  ▼
Cookie 存储（数据库/内存）
  │  缓存 cookie，记录过期时间
  ▼
业务客户端（curl_cffi）
  │  从存储读取 cookie
  │  模拟 Chrome TLS 指纹
  ▼
目标 API
```

**刷新策略：**
- 每次请求前检查 cookie 是否过期
- 请求返回 401/403 时自动触发刷新
- 定时刷新（间隔 = cookie 有效期 / 2）

---

## 调试流程

当目标网站的 API 请求失败时，按以下顺序排查：

```
1. 浏览器能否正常访问？
   ├─ NO → 网站本身有问题或 IP 被封
   └─ YES ↓

2. 用 curl_cffi + impersonate 请求
   ├─ 成功 → TLS 指纹是关键，用 curl_cffi 即可
   └─ 失败 ↓

3. 首页是否返回 WAF 挑战页？
   ├─ YES → 需要浏览器执行 JS 获取 cookie
   └─ NO ↓

4. document.cookie 是否包含认证 token？
   ├─ YES → cookie 够用，检查其他 headers
   └─ NO → httpOnly cookie，用 context.cookies() 获取

5. 完整 cookie + curl_cffi 仍失败？
   ├─ 检查是否有额外的请求签名（如 md5__1038）
   └─ 尝试从浏览器网络面板复制完整请求 → 对比差异
```
