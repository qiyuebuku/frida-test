# API 探索技巧

从实战中总结的高效探索方法。

---

## 1. 批量 URL 扫描（Phase 1 快速排查）

在启动浏览器前，先用 curl 批量测试猜测的 URL 模式：

```bash
for url in \
  "https://target.com/api/v1/list" \
  "https://target.com/api/v2/search?q=test" \
  "https://api.target.com/data" \
; do
  echo "=== $url ==="
  resp=$(curl --noproxy '*' -s --connect-timeout 5 --max-time 8 "$url" 2>&1)
  echo "$resp" | head -c 300
  echo -e "\n"
done
```

**常见 API 路径模式：**
- `/api/v1/` `/api/v2/` — RESTful 标准
- `/api/getData?path=xxx` — 东方财富模式
- `/appstock/app/xxx/yyy` — 腾讯模式
- `*.json?param=value` — 雪球/SPA 模式

## 2. 浏览器请求过滤（Phase 3 关键）

api-explorer 捕获的请求很多（100+），需要高效过滤：

```python
# 过滤噪音请求的标准模板
for r in requests:
    url = r.get('url', '')
    # 过滤静态资源和统计
    if any(x in url for x in [
        '.js','.css','.png','.jpg','.gif','.woff','.ico','.svg',
        'beacon','analytics','trace','report','log','tdw','pvuv',
        'sentry','aegis','imgnode'
    ]):
        continue
    # 只保留目标域名
    if 'target-domain.com' in url:
        print(url[:160])
```

## 3. eval 高级用法

### 3a. 提取页面中的 JSON 数据（SSR/预渲染）

```javascript
// 找 window.__INITIAL_STATE__ 或类似的全局数据
window.__INITIAL_STATE__ || window.__NEXT_DATA__ || window.__APP_DATA__
```

### 3b. 获取所有导航链接

```javascript
Array.from(document.querySelectorAll("a"))
  .filter(a => a.textContent.trim().length > 3 && a.href.includes("target-domain"))
  .map(a => a.textContent.trim() + " → " + a.href)
  .filter((v,i,arr) => arr.indexOf(v) === i)  // 去重
  .join("\n")
```

### 3c. 在浏览器内 fetch API（绕过 WAF）

```javascript
// 同步返回 JSON
fetch("/api/data?count=10", {credentials: "include"})
  .then(r => r.json())
  .catch(e => ({error: e.message}))
```

### 3d. 拦截 XHR 获取请求详情

```javascript
// 拦截下一个匹配的请求
new Promise(resolve => {
  let origXHR = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    if (url.includes("target-keyword")) {
      let origSend = this.send;
      this.send = function(body) {
        this.addEventListener("readystatechange", () => {
          if (this.readyState === 4) {
            resolve(JSON.stringify({
              url: url,
              status: this.status,
              body: this.responseText.substring(0, 500)
            }));
          }
        });
        origSend.call(this, body);
      };
    }
    origXHR.apply(this, arguments);
  };
  setTimeout(() => resolve("timeout"), 10000);
})
```

## 4. 反爬识别快速判断

| 响应特征 | 反爬类型 | 下一步 |
|---------|---------|--------|
| `aliyun_waf` meta 标签 | 阿里云 WAF | 浏览器通过挑战 → `/cookies` 导出 → curl_cffi |
| `cf-ray` header | Cloudflare | curl_cffi impersonate 通常够用 |
| `{"error_code":"400016"}` | 雪球认证 | 需要 httpOnly cookie（xq_a_token） |
| `captcha_url` 字段 | 验证码触发 | Token 已废，需重新生成 |
| status 403 + 空 body | IP 封禁或 UA 检测 | 换 User-Agent 或用代理 |
| status 200 + HTML 而非 JSON | WAF 挑战页或 SSR | 检查 HTML 是否有 WAF 特征 |

## 5. 多页面探索工作流

探索同一网站的多个页面时：

```bash
# 每个页面前清除记录，避免请求混淆
curl --noproxy '*' -s -X POST http://server:8900/api/spy/clear >/dev/null
curl --noproxy '*' -s -X POST http://server:8900/api/spy/goto \
  -H "Content-Type: application/json" \
  -d '{"url": "https://target.com/page2", "wait_after": 8}'

# 查看新页面的请求
curl --noproxy '*' -s 'http://server:8900/api/spy/requests' | python3 -c "..."
```

## 6. 数据格式逆向

当 API 返回非标准格式时的处理技巧：

### GBK 编码处理
```python
resp = await client.get(url)
text = resp.content.decode("gbk", errors="replace")
```

### JSONP 解包
```python
text = response_text
if "=" in text:
    text = text.split("=", 1)[1]  # var xxx = {...}
if text.startswith("callback("):
    text = text[text.index("(")+1:text.rindex(")")]
data = json.loads(text)
```

### HTML 表格解析
```python
from html.parser import HTMLParser
class TDParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_td = False
        self.cells = []
        self.current = ''
    def handle_starttag(self, tag, attrs):
        if tag == 'td': self.in_td = True; self.current = ''
    def handle_endtag(self, tag):
        if tag == 'td': self.in_td = False; self.cells.append(self.current.strip())
    def handle_data(self, data):
        if self.in_td: self.current += data
```
