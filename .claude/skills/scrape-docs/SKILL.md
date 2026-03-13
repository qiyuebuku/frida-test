---
name: scrape-docs
description: 采集文档网站。当用户要求采集、爬取或抓取文档网站时使用。
---

# 文档采集 Skill

使用 Camoufox 浏览器 + Claude 智能分析来采集任意文档网站。

## 输出目录结构

```
scraped_docs/<site_name>/
├── docs_merged.md          # 合并后的完整文档（AI 友好，最重要）
├── html/                   # HTML 文件目录
│   ├── index.html          # HTML 索引
│   └── *.html              # 单页 HTML
└── markdown/               # Markdown 文件目录
    ├── index.md            # Markdown 索引
    └── *.md                # 单页 Markdown
```

## 快速采集流程（推荐）

### 第一步：启动服务器并尝试访问

**始终先用无头模式启动**，让程序自动检测是否需要登录：

```bash
cd /home/yuyang/公司项目/tamar_console
python .claude/skills/scrape-docs/browser_helper.py serve --output ./scraped_docs/<site_name> &
sleep 5
curl -s http://localhost:9222/status
```

然后访问目标页面：

```bash
curl -s -X POST http://localhost:9222/goto -H "Content-Type: application/json" \
  -d '{"url": "<目标URL>"}'
```

**关键**：检查 `/goto` 返回的 JSON 中 `login_required` 字段：
- `"login_required": false` → 内容完整，继续正常采集（跳到第三步）
- `"login_required": true` → 内容被截断，需要登录，进入第二步

> `/goto` 会自动扫描页面文本，检测"登录查看"、"回复可见"、"阅读全文"等关键词。
> `matched_text` 字段会显示具体命中了什么关键词。

### 第二步（仅 login_required=true 时）：切换到登录模式

如果检测到需要登录，**必须先停掉当前无头服务器，再用登录模式重新启动**：

```bash
# 停掉无头服务器
curl -s -X POST http://localhost:9222/stop
sleep 2

# 用非无头 + profile 重新启动
python .claude/skills/scrape-docs/browser_helper.py serve --output ./scraped_docs/<site_name> --no-headless --profile default &
sleep 5
curl -s http://localhost:9222/status
```

- `--no-headless`：显示浏览器窗口，让用户可以手动操作登录
- `--profile default`：使用 `~/.scrape-docs-profile` 保存登录状态，下次无需重新登录

然后引导用户登录：

```bash
# 先尝试直接访问目标页面（profile 中可能有上次的登录状态）
curl -s -X POST http://localhost:9222/goto -H "Content-Type: application/json" \
  -d '{"url": "<目标URL>"}'
```

再次检查返回的 `login_required`：
- 如果 `false`：说明 profile 中的登录状态仍有效，跳过登录直接进入第三步
- 如果 `true`：需要用户手动登录，执行以下操作：

```bash
# 导航到登录页面
curl -s -X POST http://localhost:9222/goto -H "Content-Type: application/json" \
  -d '{"url": "<站点登录页URL>"}'
```

然后**必须使用 AskUserQuestion 工具询问用户**："请在弹出的浏览器窗口中完成登录，登录完成后请告诉我。"

等用户回复确认后，再继续后续步骤。

> **常见站点登录页参考**（按需使用，不限于此列表）：
> - CSDN: `https://passport.csdn.net/login`
> - 知乎: `https://www.zhihu.com/signin`
> - 看雪: `https://passport.kanxue.com/user-login.htm`
> - 简书: `https://www.jianshu.com/sign_in`
>
> **profile 记忆**：登录状态会保存到 `~/.scrape-docs-profile`，下次使用相同 profile 启动时无需重新登录（Cookie 未过期的情况下）。

### 第三步：访问起始页面并获取链接

```bash
# 访问起始页面
curl -s -X POST http://localhost:9222/goto -H "Content-Type: application/json" \
  -d '{"url": "<起始URL>"}'

# 使用 POST 方法获取过滤后的链接（避免 URL 编码问题）
curl -s -X POST http://localhost:9222/filter_links -H "Content-Type: application/json" \
  -d '{"selector": "a", "url_pattern": "/docs", "exclude_pattern": "#"}'
```

### 第四步：批量采集所有页面

提取上一步获取的 URL 列表，然后调用批量采集 API。采集任务会在后台运行：

```bash
# 启动批量采集（后台执行，立即返回）
# 如果页面包含代码截图等图片，添加 "ocr_images": true 来启用 OCR 识别
curl -s -X POST http://localhost:9222/scrape_all -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://example.com/docs/page1", "https://example.com/docs/page2", ...],
    "delay": 0.5
  }'

# 查询采集进度（轮询直到完成）
curl -s http://localhost:9222/task_status
```

**重要**：`/scrape_all` 会立即返回，任务在后台执行。使用 `/task_status` 查询进度：
- `task.running`: 是否正在运行
- `task.progress` / `task.total`: 当前进度
- `task.completed`: 是否已完成
- `task.result`: 完成后的结果

> `save`/`scrape_all` 会自动检测并点击已知站点的"阅读全文/查看更多"按钮（如 CSDN 的 `.btn-readmore`），无需手动处理。
> `save` 返回值中也包含 `login_required` 字段，如果为 `true` 则附带 `warning` 提示内容不完整。

### 第五步：生成索引和合并文档

```bash
# 生成索引
curl -s -X POST http://localhost:9222/index -H "Content-Type: application/json" -d '{}'

# 合并所有 Markdown 为单文件（最重要）
curl -s -X POST http://localhost:9222/merge -H "Content-Type: application/json" -d '{}'
```

### 第六步：停止服务器

```bash
curl -s -X POST http://localhost:9222/stop
```

## 完整工作流程（手动控制）

如果需要更精细的控制，可以使用以下步骤：

### 1. 启动浏览器服务器

```bash
cd /home/yuyang/公司项目/tamar_console
python .claude/skills/scrape-docs/browser_helper.py serve --output ./scraped_docs/<site_name> &
sleep 5
curl -s http://localhost:9222/status
```

### 2. 访问起始页面

```bash
curl -s -X POST http://localhost:9222/goto -H "Content-Type: application/json" -d '{"url": "<URL>"}'
```

### 3. 获取 HTML 并分析页面结构

```bash
curl -s "http://localhost:9222/html" | python3 -c "import sys,json; print(json.load(sys.stdin).get('html','')[:5000])"
```

分析 HTML，识别：
- **内容选择器**：文档正文（如 `.vp-doc`, `article`）
- **导航选择器**：侧边栏链接（如 `.VPSidebar a.link`）

### 4. 获取导航链接

**方法一：POST 请求（推荐，避免 URL 编码问题）**
```bash
curl -s -X POST http://localhost:9222/filter_links -H "Content-Type: application/json" \
  -d '{"selector": "a", "url_pattern": "/docs", "exclude_pattern": "#"}'
```

**方法二：GET 请求（简单选择器）**
```bash
curl -s "http://localhost:9222/links?selector=a"
```

### 5. 批量采集

**方法一：使用内置批量采集 API（推荐）**

```bash
curl -s -X POST http://localhost:9222/scrape_all -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://example.com/docs/page1",
      "https://example.com/docs/page2"
    ],
    "selector": ".content",
    "delay": 0.5
  }'
```

**方法二：使用 Python 脚本（需要更多控制时）**

```python
import requests
import time

urls = [...]  # 从步骤 4 获取的链接列表

for i, url in enumerate(urls, 1):
    print(f"[{i}/{len(urls)}] {url}")
    requests.post("http://localhost:9222/goto", json={"url": url}, timeout=60)
    time.sleep(0.5)  # 等待页面稳定
    result = requests.post("http://localhost:9222/save", json={}, timeout=30).json()
    if result.get("status") == "ok":
        print(f"  -> {result['title']}")
    else:
        print(f"  -> Error: {result.get('error')}")

print("采集完成!")
```

### 6. 生成索引和合并文档

```bash
# 生成索引
curl -s -X POST http://localhost:9222/index -H "Content-Type: application/json" -d '{}'

# 合并所有 Markdown 为单文件（最重要）
curl -s -X POST http://localhost:9222/merge -H "Content-Type: application/json" -d '{}'
```

### 7. 停止服务器

```bash
curl -s -X POST http://localhost:9222/stop
```

## API 参考

所有响应都包含 `status` 字段（"ok"、"partial" 或 "error"）。

### 基础操作

| 端点 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/goto` | POST | `{"url": "...", "timeout": 60000}` | 访问页面，返回含 `login_required` 和 `matched_text` |
| `/screenshot` | GET | `?path=...` | 截图 |
| `/html` | GET | `?selector=...` | 获取 HTML |
| `/status` | GET | - | 获取服务器状态 |
| `/stop` | POST | - | 停止服务器 |

### 链接操作

| 端点 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/links` | GET | `?selector=a` | 获取链接列表 |
| `/filter_links` | POST | `{"selector": "a", "url_pattern": "/docs", "exclude_pattern": "#"}` | **推荐**：获取过滤后的链接 |

### 登录检测与内容展开

| 端点 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/check_login` | GET | - | 检测当前页面是否需要登录（返回 `login_required` 和 `matched_text`） |
| `/wait_login` | POST | `{"url": "...", "timeout": 300}` | 等待用户在浏览器中登录（仅非无头模式） |
| `/login_done` | POST | - | 通知服务器用户已完成登录 |
| `/expand` | POST | - | 手动触发展开页面折叠内容 |

### 采集操作

| 端点 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/save` | POST | `{"selector": "...", "ocr_images": false}` | 保存当前页面，返回含 `login_required` 和 `warning`（自动展开折叠内容，可选 OCR） |
| `/scrape_all` | POST | `{"urls": [...], "selector": "...", "delay": 0.5, "ocr_images": false}` | **推荐**：批量采集（后台执行，可选 OCR） |
| `/task_status` | GET | - | 查询后台任务状态 |
| `/index` | POST | `{}` | 生成索引页 |
| `/merge` | POST | `{}` | 合并所有 Markdown |

### filter_links 参数说明

```json
{
  "selector": "a",           // CSS 选择器，默认 "a"
  "url_pattern": "/docs",    // URL 必须包含的字符串
  "exclude_pattern": "#"     // URL 必须不包含的字符串（默认排除锚点）
}
```

### save 参数说明

```json
{
  "selector": ".content",    // 内容选择器（可选）
  "ocr_images": true         // 是否对图片进行 OCR 识别（可选，默认 false）
}
```

### scrape_all 参数说明

```json
{
  "urls": ["url1", "url2"],  // 要采集的 URL 列表
  "selector": ".content",    // 内容选择器（可选）
  "delay": 0.5,              // 每页之间的延迟（秒）
  "ocr_images": true         // 是否对图片进行 OCR 识别（可选，默认 false）
}
```

**注意**：`/scrape_all` 会立即返回，任务在后台执行。响应示例：
```json
{"status": "ok", "message": "Background task started for 50 URLs...", "total": 50}
```

### task_status 响应说明

```json
{
  "status": "ok",
  "task": {
    "running": true,           // 任务是否正在运行
    "task_type": "scrape_all", // 任务类型
    "progress": 25,            // 当前进度
    "total": 50,               // 总数
    "current_url": "...",      // 当前正在采集的 URL
    "success": 24,             // 成功数
    "failed": 1,               // 失败数
    "completed": false,        // 是否已完成
    "result": null             // 完成后的结果
  }
}
```

## 常见文档框架的选择器

| 框架 | 内容选择器 | 导航选择器 |
|------|-----------|-----------|
| VitePress | `.vp-doc` | `.VPSidebar a.link` |
| VuePress | `.theme-default-content` | `.sidebar a` |
| Docusaurus | `article` | `.menu a` |
| GitBook | `.markdown-section` | `.chapter a` |
| MkDocs | `.md-content` | `.md-nav a` |
| Next.js (shadcn/ui) | 不指定（全页） | `a`（配合 url_pattern） |

### 中文技术社区（已内置自动匹配）

以下站点在不指定 `selector` 时会**自动匹配**最佳内容选择器：

| 站点 | 域名 | 自动选择器 |
|------|------|-----------|
| CSDN | `blog.csdn.net` | `article .markdown_views` |
| 看雪 | `bbs.kanxue.com` | `.message_md_type` |
| Hexo 博客 | `ihandmine.github.io` | `.post-body` |
| 简书 | `jianshu.com` | `article` |
| 掘金 | `juejin.cn` | `.article-content` |
| 知乎 | `zhihu.com` | `.RichContent-inner` |
| Medium | `medium.com` | `article` |
| SegmentFault | `segmentfault.com` | `article` |
| 博客园 | `cnblogs.com` | `#cnblogs_post_body` |
| 吾爱破解 | `52pojie.cn` | `.message` |
| FreeBuf | `freebuf.com` | `.article-content` |

> **自动匹配机制**：`save_page` 和 `scrape_all` 在用户未指定 `content_selector` 时，
> 会根据当前 URL 的域名自动查表匹配选择器。匹配成功时会在日志中打印 `自动匹配选择器: ...`。
> 如果自动匹配的选择器不合适，可以手动指定 `selector` 参数覆盖。

### 自动展开折叠内容（已内置自动匹配）

以下站点在 `save` 时会**自动点击**"阅读全文/查看更多"按钮：

| 站点 | 域名 | 展开按钮选择器 |
|------|------|---------------|
| CSDN | `blog.csdn.net` | `.btn-readmore` 等 |
| 知乎 | `zhihu.com` | `.ContentItem-expandButton` |
| 简书 | `jianshu.com` | `.collapse_tips` |
| 掘金 | `juejin.cn` | `.read-more` |

> 自动展开在 `save_page` 中执行，在获取内容之前会自动检测并点击展开按钮。
> 也可以通过 `POST /expand` 手动触发。

## 常见问题

### 1. 获取链接时 URL 编码出错

**问题**：使用 GET `/links?selector=a[href^='/docs']` 时出现编码问题

**解决**：使用 POST `/filter_links` 代替：
```bash
curl -s -X POST http://localhost:9222/filter_links -H "Content-Type: application/json" \
  -d '{"selector": "a", "url_pattern": "/docs"}'
```

### 2. 采集超时

**问题**：页面加载超时（默认 60 秒）

**解决**：
- 服务器会自动降级到 `domcontentloaded` 等待策略
- 如果仍然超时，状态会返回 `"partial"`，但通常内容已可用
- 可以适当增加 delay 参数

### 3. 采集内容包含侧边栏/导航

**问题**：保存的内容包含了页面的导航元素

**解决**：指定更精确的内容选择器：
```bash
curl -s -X POST http://localhost:9222/save -H "Content-Type: application/json" \
  -d '{"selector": ".vp-doc"}'
```

### 4. 采集大量页面时超时

**问题**：采集几十上百个页面时，HTTP 请求超时

**解决**：
- `/scrape_all` API 现在会在后台执行，立即返回
- 使用 `/task_status` 查询采集进度
- 任务完成后，`task.completed` 为 `true`，结果在 `task.result` 中

### 5. 采集内容包含导航/弹窗/广告

**问题**：采集结果中混入了导航菜单、登录弹窗、底部广告等无关内容

**解决**：
- 采集时会**自动清理** `<nav>`, `<header>`, `<footer>`, `<aside>`, `<script>`, `<style>` 等标签
- 常见噪声 class（sidebar, recommend, comment, login, share 等）也会被自动移除
- 已知中文技术社区（CSDN、看雪、掘金等）会自动匹配内容选择器，无需手动指定
- 如果仍有噪声残留，请手动指定更精确的 `selector`

### 6. OCR 结果混入弹窗文字

**问题**：OCR 识别的图片中混入了页面弹窗（如 CSDN 的登录提示）

**解决**：
- OCR 截图前会自动尝试关闭常见弹窗（modal、dialog、login 等）
- 如果弹窗无法自动关闭，可以在采集前先手动关闭：
```bash
curl -s -X POST http://localhost:9222/click -H "Content-Type: application/json" \
  -d '{"selector": ".modal .close"}'
```

### 7. 图片中的文字无法识别

**问题**：页面中的代码截图、终端截图等图片内容无法被采集为文本（OCR 文本已自动进行 HTML 转义，不会破坏输出格式）

**解决**：在 `/save` 或 `/scrape_all` 中启用 OCR：
```bash
curl -s -X POST http://localhost:9222/save -H "Content-Type: application/json" \
  -d '{"selector": ".content", "ocr_images": true}'
```

OCR 会通过浏览器截图获取图片（避免防盗链问题），使用 `rapidocr_onnxruntime` 识别文字，
并将识别结果作为代码块插入到图片下方。

**注意**：OCR 需要额外安装 `rapidocr_onnxruntime`（见依赖部分）。
启动服务器时可通过 `/status` 接口的 `ocr_available` 字段确认 OCR 是否可用。

## 依赖

```bash
# 基础依赖（必需）
pip install camoufox[geoip] aiohttp html2text
camoufox fetch

# OCR 依赖（可选，启用图片文字识别）
pip install rapidocr_onnxruntime
```

## 用户请求

$ARGUMENTS
