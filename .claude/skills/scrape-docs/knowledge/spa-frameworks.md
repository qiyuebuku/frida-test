# SPA 框架文档站采集策略

## 问题本质

许多现代文档站（VitePress、Docusaurus、Next.js 等）使用 SPA（Single Page Application）架构，
API 参考等复杂页面的内容通过 JavaScript 动态渲染，直接 HTTP 抓取只能拿到骨架 HTML。

**典型症状**：
- 页面标题能正常提取，但正文内容极短（<500 chars）
- 内容只有标题和概述，缺少详细的参数表、Schema、响应结构
- 多个页面提取的内容结构雷同（只有框架模板文字）

## 识别 SPA 渲染内容

### 自动检测信号

1. **内容长度异常短**：API 文档页通常 2000+ chars，提取 <500 chars 大概率是 JS 渲染内容
2. **标题为框架默认值**：如 `vitepress-openapi`、`Untitled` 等非业务标题
3. **内容中存在组件占位符**：如 `SchemaJSONobject`、`Content-Typeapplication/json` 等文字粘连
4. **多个不同 URL 提取出相同模板文字**

### 框架特征

| 框架 | 特征 | 数据存储位置 |
|------|------|-------------|
| VitePress + vitepress-openapi | `<link>` 引用 `chunks/theme.*.js` | OpenAPI spec 内联在 theme chunk 中 |
| Docusaurus + docusaurus-openapi | `<script>` 中有 `docusaurus` 关键词 | 可能在 `api-spec.json` 或 JS chunk 中 |
| Swagger UI | `<div id="swagger-ui">` | `swagger-config-url` 参数或内联 spec |
| Redoc | `<redoc>` 或 `redocly` | `spec-url` 属性或内联 JSON |
| Stoplight Elements | `elements-api` | `apiDescriptionUrl` 属性 |

## 通用回退策略（按优先级）

### 策略 1：寻找 OpenAPI Spec 源文件

许多 API 文档站的数据来源是 OpenAPI/Swagger JSON/YAML 文件。找到源文件就能获取完整的结构化数据。

**常见路径探测**：
```
/openapi.json
/openapi.yaml
/swagger.json
/api-docs
/docs/openapi.json
/api/openapi.json
/v1/openapi.json
/_next/data/*/api-reference.json
```

**从 HTML 源码中查找**：
```bash
# 在页面 HTML 中搜索 spec 引用
grep -oP '(openapi|swagger|spec|api-docs)[^"'"'"']*\.(json|yaml|yml)' page.html

# 在 JS 文件中搜索
grep -oP '"[^"]*openapi[^"]*"' app.js
```

**注意**：有些站点返回 200 但内容是 HTML（SPA 路由），需要检查 Content-Type 或前几个字节。

### 策略 2：从 JS Bundle 中提取内联数据

当 OpenAPI spec 被打包进 JS 文件时（如 VitePress），需要从 JS 中提取。

**通用步骤**：
1. 从 HTML 中找到所有 `<script src="...">` 和 `<link ... href="...js">`
2. 下载最大的 JS 文件（通常是 theme/vendor chunk）
3. 搜索 API 路径特征（如 `/v1/`、`/api/`）定位数据区域
4. 用 **Node.js eval** 而非手工正则解析 JS 对象（JS 有模板字符串、!0/!1、科学计数法等语法）

**Node.js 提取模板**：
```javascript
// 将 JS 对象赋给变量，用 JSON.stringify 输出
const paths = { /* 从 JS 中提取的对象 */ };
const components = { /* schemas 等 */ };
const spec = { openapi: "3.1.0", paths, components };
console.log(JSON.stringify(spec, null, 2));
```

**关键陷阱**：
- JS 中 `!0` = `true`，`!1` = `false`
- 模板字符串（反引号）中的换行和转义
- 科学计数法（`1e4` = 10000）
- 未引用的对象键名
- `$ref` 等以特殊字符开头的键

### 策略 3：浏览器渲染采集

当上述方法都不可行时，使用浏览器渲染获取完整 DOM。

**适用场景**：
- 数据通过运行时 API 请求加载（非内联）
- 页面有复杂的交互展开逻辑（如点击展开 Schema）
- 有反爬保护（Cloudflare 等）

**注意事项**：
- 需要等待足够长时间让 JS 完成渲染（API 文档通常需要 5-10 秒）
- 某些组件需要点击展开才能获取完整内容
- 延迟要设置得足够长（3-5 秒），避免触发反爬

## OpenAPI Spec 转 Markdown

获取到 OpenAPI spec 后，需要转换为 AI 友好的 Markdown 文档。

**转换要点**：
1. **解析 `$ref` 引用**：递归解析所有 `#/components/schemas/...` 引用
2. **展开嵌套对象**：用缩进层级展示嵌套的请求/响应结构
3. **标注 required/optional**：从 schema 的 `required` 数组中判断
4. **展示 enum 值**：列出所有可选枚举值
5. **生成示例**：从 schema 的 `example`/`default` 字段生成请求示例
6. **保留 description**：API 的文字描述是最重要的上下文

**输出格式建议**：
```markdown
### API Name
`METHOD /path`
Description...

#### 请求体
- **`field`** (type, required/optional): description

#### 响应
**200**: description
- **`field`** (type): description
  - **`nested_field`** (type): description
```
