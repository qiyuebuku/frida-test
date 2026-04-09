# 链接发现与 URL 补全策略

## 链接发现的重要性

批量采集的第一步是发现所有需要采集的 URL。链接发现不全会导致文档缺页。

## 发现方式（按优先级）

### 1. sitemap.xml

许多站点提供 sitemap，包含完整的 URL 列表：
```
/sitemap.xml
/sitemap-0.xml
/docs/sitemap.xml
```

### 2. llms.txt / llms-full.txt

AI 友好的站点可能提供此文件，包含文档 URL 列表和内容：
```
/llms.txt
/llms-full.txt
/docs/llms.txt
```

### 3. 起始页面的 `<a>` 链接

从起始页面提取所有链接，按 `--pattern` 过滤。

**局限性**：
- 侧边栏折叠区域的链接可能未在 HTML 中（需要 JS 展开）
- 某些链接通过 JS 路由动态生成
- 分页内容的后续页面不在当前页链接中

### 4. 多起始页补充

当单一起始页发现的链接不全时，可以从多个入口页面采集链接：
- 文档首页
- API Reference 索引页
- 侧边栏导航页

## URL 过滤策略

### pattern 参数的使用

`--pattern` 用于过滤 URL，只采集匹配的页面：
- `/docs` — 所有文档页面
- `/docs/api` — 仅 API 文档
- `/docs/guide` — 仅指南

### 排除规则

自动排除的 URL 模式：
- `#` 锚点链接（同页内跳转）
- `javascript:` 伪链接
- 外部站点链接（不同 domain）
- 静态资源（`.css`、`.js`、`.png` 等）

## 去重

采集前对 URL 做规范化去重：
- 去掉 URL 末尾的 `/` 和 `#`
- 去掉 query string 中的无关参数（如 `utm_*`）
- 合并 `http` 和 `https` 的重复
