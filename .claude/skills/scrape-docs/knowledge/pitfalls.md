# 踩坑记录与避坑指南

## 致命级

### 1. SPA 站点直接抓取只拿到骨架内容
- **现象**: API 文档页面内容极短（300-500 chars），只有标题和概述，缺少参数表和 Schema
- **原因**: VitePress/Docusaurus 等 SPA 框架的 API 参考页面通过 JS 动态渲染（如 vitepress-openapi 插件），直接 HTTP 抓取只能获取 SSR 骨架
- **方案**: 检测到内容异常短时，按优先级尝试：(1) 提取 OpenAPI spec 源文件 (2) 从 JS bundle 中提取内联数据 (3) 浏览器渲染采集
- **适用范围**: 所有使用 OpenAPI/Swagger 渲染插件的文档站

### 2. 403 Forbidden — 站点反爬/CDN 拦截
- **现象**: 直接 HTTP 请求返回 403，WebFetch 工具也返回 403
- **原因**: Cloudflare/Vercel 等 CDN 对非浏览器 User-Agent 拦截，或站点启用了反爬策略
- **方案**: (1) 设置浏览器级 User-Agent 和 Accept 头 (2) 使用 `curl` 有时比 Python httpx 更容易绕过 (3) 最终回退到远程浏览器
- **适用范围**: 有 CDN 保护的站点

### 3. URL 返回 200 但内容是 SPA 入口 HTML
- **现象**: 多个不同 URL 返回几乎相同的 HTML 内容，标题为通用的框架名
- **原因**: SPA 的所有路由都返回同一个 `index.html`，路由由前端 JS 处理
- **方案**: 不能信赖 HTTP 状态码，必须检查实际内容质量（长度、文本密度、业务关键词存在性）
- **适用范围**: 所有 SPA 站点

## 严重级

### 4. JS 对象解析比想象中复杂得多
- **现象**: 正则替换 + JSON.parse 反复失败，在各种边界情况上出错
- **原因**: JS 对象语法与 JSON 差异很大：模板字符串（反引号）、`!0`/`!1`（true/false）、科学计数法（`1e4`）、未引用键名、`$ref` 特殊键等
- **方案**: **直接用 Node.js eval** 而非手工解析。将提取的 JS 片段写入 `.js` 文件，用 `node` 执行 `JSON.stringify` 输出
- **适用范围**: 所有需要从 JS bundle 提取数据的场景

### 5. OpenAPI spec 路径探测返回 HTML 而非 JSON
- **现象**: `curl /openapi.json` 返回 200 且 Content-Type 为 text/html
- **原因**: SPA 路由捕获了所有未匹配的 URL，返回 SPA 入口页
- **方案**: 检查响应前几个字节是否为 `{` 或 `[`（JSON），或 `---`/`openapi:`（YAML），而非 `<!doctype` 或 `<html`
- **适用范围**: 所有 SPA 站点的资源路径探测

### 6. 代理环境变量干扰采集
- **现象**: `curl` 和 `requests`/`httpx` 无法连接目标站点或连接超时
- **原因**: WSL2 或开发环境中设置了 `http_proxy`/`https_proxy` 环境变量
- **方案**: `curl --noproxy '*'`，Python 中在脚本顶部清理代理变量或传 `proxies=None`
- **适用范围**: 在有代理的开发环境中采集

## 一般级

### 7. Markdown 中残留 HTML 元素和 CSS class
- **现象**: 转换后的 Markdown 中包含 `<div class="...">` 等 HTML 标签
- **原因**: html2text 对某些嵌套结构转换不完善
- **方案**: 转换前用 `clean_html_noise()` 清除 nav/footer/sidebar 等噪声元素；转换后做二次清理
- **适用范围**: 所有直接抓取的页面

### 8. 页面间去重不完善导致 docs_merged.md 冗余
- **现象**: 合并文档中同一内容出现多次（如页面头部/尾部的通用内容）
- **原因**: 采集时没有充分去除页面公共元素（导航栏、侧边栏、页脚）
- **方案**: 提取时使用精确的内容选择器（`main`、`article`），避免采集全页 HTML
- **适用范围**: 所有批量采集场景

### 9. llms-full.txt 存在但内容不完整
- **现象**: llms-full.txt 只包含概览页面，不包含 API 参考细节
- **原因**: 站点维护者可能只将部分内容导出到 llms-full.txt
- **方案**: llms-full.txt 采集后仍需检查内容完整性，对缺失的部分补充采集
- **适用范围**: 提供 llms-full.txt 的站点

### 10. 页面标题全部为框架默认值（如 "vitepress-openapi"）
- **现象**: docs_merged.md 目录中 19 个页面标题全是 "vitepress-openapi"，无法区分
- **原因**: vitepress-openapi 插件在 `<title>` 中使用了固定的框架名而非 API 操作名，`extract_title_from_html` 只看 `<title>` 标签
- **方案**: (1) `extract_title_from_html` 检测到框架默认标题时 fallback 到 `<h1>` (2) `merge_docs` 从 markdown 内容第一行 `# xxx` 提取真实标题 (3) 浏览器模式也做相同处理
- **适用范围**: 使用 vitepress-openapi/Swagger UI/ReDoc 等 OpenAPI 渲染插件的站点

### 11. deploy.sh rsync --delete 误删远程采集产物
- **现象**: 部署后远程服务器上的 scraped_docs/ 目录被清空
- **原因**: rsync `--delete` 会删除远程有但本地没有的文件，采集产物只存在于远程
- **方案**: rsync 排除列表中添加 `--exclude=scraped_docs/`
- **适用范围**: 所有在远程服务器上运行采集任务的场景

### 12. 批量采集链接发现不全
- **现象**: 只发现了部分文档页面，遗漏了某些 API 端点
- **原因**: 链接发现依赖起始页面的 `<a href>` 标签，某些链接可能通过 JS 动态生成或在侧边栏折叠区域内
- **方案**: (1) 尝试多个起始页 (2) 从 sitemap.xml 获取完整 URL 列表 (3) 手动补充关键 URL
- **适用范围**: 所有批量采集场景
