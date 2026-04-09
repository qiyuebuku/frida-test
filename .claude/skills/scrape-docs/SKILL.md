---
name: scrape-docs
display_name: 文档采集
icon: cloud_download
description: 智能文档采集工具，支持 SSR/SPA 站点、OpenAPI 提取、多级回退策略
category: tools
commands:
  - id: scrape-all
    name: 批量采集文档站
    description: 从起始 URL 发现所有文档页面并批量采集，自动检测 llms-full.txt → 直接 HTTP → 浏览器回退
    input: text
    executor: claude
    estimated_time: 300
    args:
      - name: start_url
        description: 起始页 URL（如 https://docs.example.com/）
        required: true
      - name: url_pattern
        description: URL 过滤模式（如 /docs、/api），只采集包含此模式的链接
        required: false
      - name: site_name
        description: 站点名称，用作输出目录名（如 mureka-api），不指定则从域名推导
        required: false

  - id: scrape-single
    name: 采集单个页面
    description: 采集指定 URL 的单个文档页面并转为 Markdown
    input: text
    executor: claude
    estimated_time: 60
    args:
      - name: url
        description: 目标页面 URL
        required: true
      - name: selector
        description: 内容 CSS 选择器（不指定则自动匹配）
        required: false

  - id: extract-openapi
    name: 提取 OpenAPI Spec
    description: 从 SPA 站点的 JS Bundle 中提取内联的 OpenAPI/Swagger 规范并转为 Markdown
    input: text
    executor: claude
    estimated_time: 180
    args:
      - name: url
        description: API 文档页面 URL（用于定位 JS 文件）
        required: true
      - name: site_name
        description: 站点名称，用作输出目录名
        required: false

  - id: merge
    name: 合并文档
    description: 将已采集的 Markdown 文件合并为 docs_merged.md
    input: text
    executor: claude
    estimated_time: 30
    args:
      - name: site_name
        description: 站点名称（即 scraped_docs/ 下的目录名）
        required: true
---

# 文档采集 Skill

智能文档采集工具，**默认使用浏览器渲染**，确保 JS 渲染内容（代码块、交互组件）完整。

## 核心能力

- **浏览器渲染采集**（默认模式，兼容所有站点类型）
- **自动检测文档框架**（Mintlify/Docusaurus/VitePress/GitBook/MkDocs 等）
- **OpenAPI spec 自动提取与转换**（VitePress 站点最佳方案）
- **采集后质量校验**

## 输出目录结构

```
scraped_docs/<site_name>/
├── docs_merged.md      # 合并后的完整文档（AI 友好，最重要）
├── html/               # HTML 文件目录
└── markdown/           # Markdown 文件目录
```

## 执行方式（必须使用 doc_scraper.py，禁止手写脚本）

**所有采集操作必须通过 `doc_scraper.py` 执行**，它内置了 HTML→Markdown 转换、内容选择器匹配、代码块提取、质量校验等能力。禁止自己写 curl + python 脚本手动转换。

```bash
SCRAPER="python doc_scraper.py"

# 批量采集（推荐）
$SCRAPER scrape-all --start-url <URL> --url-pattern "/docs" --output <输出目录>

# 采集单个页面
$SCRAPER scrape <URL> --output <输出目录>

# 获取页面链接
$SCRAPER links <URL> --pattern "/docs"

# 合并已采集的文档
$SCRAPER merge --output <输出目录>

# 从 VitePress JS bundle 提取 OpenAPI spec
$SCRAPER extract-openapi <URL> --output <输出目录>
```

### VitePress + vitepress-openapi 站点

当检测到 VitePress + vitepress-openapi 框架时，**优先使用 extract-openapi**，
从 JS bundle 直接提取完整 OpenAPI spec，比浏览器渲染更快更完整。

**识别特征**：`<title>` 含 `vitepress-openapi`、有 `chunks/theme.*.js` 引用、URL 含 `/api/operations/`

## 通用参数

| 参数 | 说明 |
|------|------|
| `--output`, `-o` | 输出目录（默认 `./scraped_docs`） |
| `--selector` | 内容 CSS 选择器（不指定则自动匹配） |
| `--delay` | 页面间延迟秒数（默认 0.5） |
| `--pattern` | URL 过滤模式（如 `/docs`） |
| `--api` | 远程浏览器 API 地址 |

## 依赖

```bash
pip install httpx html2text
```

> 浏览器操作由远程 smart-fund-server 提供，本地不需要安装 camoufox。

---

## 采集工作流

### Phase 1: 执行采集

直接用 `doc_scraper.py` 采集，默认浏览器渲染模式，无需判断站点类型：

```bash
$SCRAPER scrape-all --start-url <URL> --output <输出目录>
```

**VitePress + vitepress-openapi 站点例外**：优先用 `extract-openapi`（从 JS bundle 提取，更快更完整）。

### Phase 2: 质量校验

1. 检查 `docs_merged.md` 总大小和页面数
2. 抽查 2-3 个关键页面：API 参数、代码示例、Schema 是否完整
3. API 文档页 < 500 chars 说明内容可能不完整

### Phase 3: 补充采集（如需）

对不完整的页面单独重新采集：`$SCRAPER scrape <URL> --output <输出目录>`，然后 `$SCRAPER merge --output <输出目录>`。

---

## 知识库结构

知识库是本 Skill 自我迭代的核心。每次遇到新问题后都应更新。

```
knowledge/
├── spa-frameworks.md       # SPA 框架文档站采集策略（核心知识）
├── pitfalls.md              # 踩坑记录与避坑指南
├── quality-validation.md    # 采集质量校验策略
└── link-discovery.md        # 链接发现与 URL 补全策略
```

## 自我迭代规则

当完成一次新的文档采集后，如果遇到了本知识库未覆盖的问题：

1. **更新踩坑记录**：在 `pitfalls.md` 中新增条目
2. **更新框架策略**：如果遇到新的文档框架或 SPA 渲染模式
3. **更新质量校验**：如果发现了新的内容质量问题类型

格式要求：每个知识条目必须包含：
- **现象**：遇到了什么问题
- **原因**：根因分析
- **方案**：如何解决
- **适用范围**：在什么场景下适用

## 用户请求

$ARGUMENTS
