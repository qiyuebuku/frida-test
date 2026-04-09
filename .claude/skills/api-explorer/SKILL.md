---
name: api-explorer
description: 探索陌生网站的数据接口。当需要分析一个网站如何加载数据、找到隐藏的 API 接口时使用。
---

# API Explorer Skill

通过 smart-fund-server 内置的浏览器探索服务，打开目标网站，拦截所有网络请求，发现隐藏的数据 API。

**核心场景**：目标网站用 JS 动态加载数据，curl 拿不到内容，需要找到真实的 XHR/Fetch 接口。

**服务地址**：`http://119.23.227.187:8900`，接口前缀 `/api/spy/`

## 探索流程

### Phase 1: 先 curl 直接试（不启动浏览器）

大部分 API 不需要浏览器。先批量 curl 测试已知/猜测的 URL：

```bash
# 批量测试多个接口
for url in \
  "https://api.example.com/v1/list?page=1" \
  "https://api.example.com/v2/search?q=test" \
; do
  echo "=== $url ==="
  curl --noproxy '*' -s --connect-timeout 5 --max-time 8 "$url" 2>&1 | head -c 300
  echo -e "\n"
done
```

- 成功 → 直接用，不需要浏览器
- 返回 HTML/403/空 → 进入 Phase 2

### Phase 2: 浏览器探索

```bash
# 访问目标页面
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/spy/goto \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/news/list", "wait_after": 5}'
```

`wait_after` 控制等待 JS/AJAX 加载的秒数（默认 3，复杂页面建议 5-10）。首次调用会自动启动浏览器。

### Phase 3: 查看 API 请求（最关键）

```bash
# 列出所有 XHR/Fetch 请求
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/api' | python3 -m json.tool

# 过滤关键词
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/api?url=searchList'

# 如果 /api 为空，看所有请求（过滤静态资源）
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/requests' | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
for r in d.get('requests', []):
    url = r.get('url', '')
    if any(x in url for x in ['.js','.css','.png','.jpg','.gif','.woff','.ico','.svg','beacon','analytics','trace']):
        continue
    print(f'  {url[:160]}')
"
```

### Phase 4: 查看响应体

```bash
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/body?url=searchList' | python3 -m json.tool
```

### Phase 5: 反爬对抗（当 API 被保护时）

#### 5a. 获取完整 Cookie（含 httpOnly）

```bash
# 新端点：获取所有 cookie，包含 httpOnly（浏览器必须已访问过目标网站）
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/cookies?domain=xueqiu'
```

返回 Playwright `context.cookies()` 的完整结果，含 `httpOnly=true` 的 cookie。

用途：将 cookie 导出后用 `curl_cffi`（模拟 Chrome TLS 指纹）发纯 HTTP 请求，脱离浏览器。

#### 5b. 在浏览器内 fetch API（绕过 WAF）

当 API 有 WAF 保护，纯 HTTP 请求被拦截时，可以在浏览器上下文中直接 fetch：

```bash
# 浏览器内 fetch — 自动带 WAF cookie + 正确的 TLS 指纹
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/spy/eval \
  -H "Content-Type: application/json" \
  -d '{"js": "fetch(\"/api/data?count=10\", {credentials: \"include\"}).then(r => r.json()).catch(e => ({error: e.message}))"}'
```

#### 5c. 从 SSR 页面提取数据

有些网站是服务端渲染（SSR），数据直接在 HTML 中：

```bash
# 提取页面上的链接和文本
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/spy/eval \
  -H "Content-Type: application/json" \
  -d '{"js": "Array.from(document.querySelectorAll(\"a\")).filter(a => a.title && a.title.length > 5).slice(0,20).map(a => ({title: a.title, href: a.href}))"}'
```

### Phase 6: 辅助调试

```bash
# 控制台错误（JS 报错暴露接口问题）
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/console?type=error'

# 失败的请求（被拦截/404/超时）
curl --noproxy '*' -s http://119.23.227.187:8900/api/spy/failed

# 截图确认页面状态
curl --noproxy '*' -s http://119.23.227.187:8900/api/spy/screenshot

# 页面纯文本（确认内容是否加载）
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/text?limit=2000'

# 页面 HTML
curl --noproxy '*' -s 'http://119.23.227.187:8900/api/spy/html?limit=5000'

# 在页面中执行 JS
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/spy/eval \
  -H "Content-Type: application/json" \
  -d '{"js": "document.querySelectorAll(\"li\").length"}'
```

### Phase 7: 用完停止（释放资源）

```bash
curl --noproxy '*' -s -X POST http://119.23.227.187:8900/api/spy/stop
```

---

## API 参考

所有接口前缀 `/api/spy/`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/status` | GET | 浏览器状态（available/started） |
| `/start` | POST | 手动启动浏览器（goto 会自动启动） |
| `/stop` | POST | 停止浏览器，释放资源 |
| `/goto` | POST | 访问页面，自动记录所有请求 |
| `/api` | GET | **核心**：查看 XHR/Fetch 请求 |
| `/body?url=x` | GET | 查看指定请求的响应体 |
| `/requests` | GET | 查看所有请求（含静态资源） |
| `/cookies?domain=x` | GET | **获取完整 cookie（含 httpOnly）** |
| `/console` | GET | 控制台日志 |
| `/failed` | GET | 失败的请求 |
| `/screenshot` | GET | 截图 |
| `/eval` | POST | 执行 JS（支持 async/Promise） |
| `/html` | GET | 页面 HTML |
| `/text` | GET | 页面纯文本 |
| `/clear` | POST | 清空所有记录 |
| `/links` | GET | 获取页面所有链接 |
| `/click` | POST | 点击页面元素 |

---

## 探索原则

1. **先 curl 试几个明显 URL**，能拿到就不需要浏览器
2. curl 拿不到时，**立即用本 skill**，不要继续猜
3. `/api/spy/api` 是最关键的 — 90% 的动态网站数据都通过 XHR/Fetch 加载
4. `/api/spy/body` 确认数据格式后，就可以用纯 HTTP 客户端对接，不再需要浏览器
5. `/api/spy/failed` 和 `/api/spy/console` 能暴露反爬/认证问题
6. **遇到 WAF（阿里云/Cloudflare）**：用 `/cookies` 导出 httpOnly cookie，然后 `curl_cffi` 复用
7. **遇到 SSR 页面（API 请求为空）**：用 `/eval` 直接从 DOM 提取数据，或用 `/text` + `/html` 解析
8. **探索效率**：一次访问多个页面时，每次 `/clear` 再 `/goto`，避免请求混淆
9. **参考已有经验**：查看 `knowledge/` 目录中各网站的 API 格式和反爬特征

$ARGUMENTS
