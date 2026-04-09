#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文档采集工具 - 直接 HTTP 抓取优先，远程浏览器回退

采集策略：
1. 直接 HTTP 抓取 + 智能内容提取（快速、可靠，适用于大多数文档站）
2. 远程浏览器渲染（回退方案，用于需要 JS 渲染或有反爬的站点）

输出目录结构：
<output_dir>/
├── docs_merged.md      # 合并后的完整文档（AI 友好）
├── html/               # HTML 文件目录
└── markdown/           # Markdown 文件目录
"""

import os
import sys
import json
import asyncio
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

try:
    import html2text
    HTML2TEXT_AVAILABLE = True
except ImportError:
    HTML2TEXT_AVAILABLE = False

# 远程浏览器 API 地址
SPY_API = os.environ.get("SPY_API", "http://119.23.227.187:8900/api/spy")

# ========== 内容选择器配置 ==========

# 已知站点的内容选择器映射
SITE_SELECTORS = {
    "blog.csdn.net": "article .markdown_views",
    "bbs.kanxue.com": ".message_md_type",
    "ihandmine.github.io": ".post-body",
    "jianshu.com": "article",
    "juejin.cn": ".article-content",
    "zhihu.com": ".RichContent-inner",
    "medium.com": "article",
    "segmentfault.com": "article",
    "cnblogs.com": "#cnblogs_post_body",
    "52pojie.cn": ".message",
    "freebuf.com": ".article-content",
    "wooyun.js.org": ".content",
}

# 文档框架检测（检测 HTML 中的特征 → 内容 CSS tag 优先级列表）
DOC_FRAMEWORK_SELECTORS = [
    # (特征关键词, 框架名, 内容提取tag列表)
    ("mintlify", "Mintlify", ["main", "article"]),
    ("nextra", "Nextra", ["article", "main"]),
    ("docusaurus", "Docusaurus", ["article", "main"]),
    ("gitbook", "GitBook", ["main"]),
    ("vitepress", "VitePress", ["main"]),
    ("vuepress", "VuePress", ["main"]),
    ("mkdocs", "MkDocs", ["main", "article"]),
    ("readme.io", "ReadMe", ["main", "article"]),
    ("sphinx", "Sphinx", ["main"]),
    ("docsify", "Docsify", ["main", "article"]),
]

# 通用内容提取 tag 优先级（当无法检测框架时使用）
GENERIC_CONTENT_TAGS = ["main", "article"]

# 已知站点的"展开全文"按钮选择器
SITE_EXPAND_SELECTORS = {
    "blog.csdn.net": [".btn-readmore", "#article_content .hide-article-box btn", ".readall_box .read-more-btn"],
    "zhihu.com": ["button.ContentItem-expandButton", ".RichContent-inner--collapsed .ContentItem-expandButton"],
    "jianshu.com": [".collapse_tips"],
    "juejin.cn": [".article-content .read-more"],
}

# 登录检测关键词
LOGIN_REQUIRED_PATTERNS = [
    r"登录.{0,6}(?:查看|阅读|浏览|获取|解锁)",
    r"(?:查看|阅读|浏览).{0,6}(?:请|需).{0,4}登录",
    r"(?:回复|点赞|关注).{0,6}(?:可|才能|即可).{0,6}(?:查看|阅读|浏览|获取|解锁|可见)",
    r"请先登录", r"需要登录", r"登录可见", r"回复可见",
    r"隐藏内容", r"本帖隐藏", r"购买主题",
    r"(?:注册|登录).{0,6}(?:才能|方可|即可).{0,6}(?:查看|阅读|下载)",
    r"(?:积分|金币|雪币|K币).{0,6}(?:才能|方可|即可).{0,6}(?:查看|下载|阅读)",
    r"您需要.{0,10}(?:登录|注册)",
    r"游客.{0,10}(?:无法|不能|不可).{0,6}(?:查看|阅读|浏览|下载)",
    r"(?:开通|购买).{0,6}(?:VIP|会员|SVIP).{0,6}(?:查看|阅读|解锁)",
    r"(?:关注|收藏).{0,6}(?:博主|作者).{0,6}(?:查看|阅读|才能)",
    r"展开阅读全文", r"阅读全文",
]


# ========== HTML 内容提取工具函数 ==========

def extract_tag_content(html: str, tag: str) -> str | None:
    """提取第一个匹配标签的 innerHTML，正确处理嵌套。

    Args:
        html: 完整 HTML 字符串
        tag: 标签名（如 "main", "article"）

    Returns:
        标签内的 HTML 内容，未找到返回 None
    """
    open_tag = f'<{tag}'
    close_tag = f'</{tag}>'

    start = html.lower().find(open_tag.lower())
    if start == -1:
        return None

    # 找到开标签的结束位置 >
    tag_end = html.find('>', start)
    if tag_end == -1:
        return None

    # 处理自闭合标签
    if html[tag_end - 1] == '/':
        return None

    # 用深度计数处理嵌套
    depth = 1
    pos = tag_end + 1
    html_lower = html.lower()
    open_lower = open_tag.lower()
    close_lower = close_tag.lower()

    while depth > 0 and pos < len(html):
        next_open = html_lower.find(open_lower, pos)
        next_close = html_lower.find(close_lower, pos)

        if next_close == -1:
            # 没有闭合标签，返回从开标签到末尾
            return html[tag_end + 1:]

        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = html.find('>', next_open) + 1
        else:
            depth -= 1
            if depth == 0:
                return html[tag_end + 1:next_close]
            pos = next_close + len(close_tag)

    return None


def strip_html_head(html: str) -> str:
    """移除 <head> 部分，只保留 <body> 内容。"""
    body_content = extract_tag_content(html, 'body')
    if body_content:
        return body_content
    # fallback: 用正则移除 head
    html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)
    return html


def detect_framework(html: str) -> tuple[str | None, list[str]]:
    """检测文档框架，返回 (框架名, 推荐提取tag列表)。"""
    html_lower = html[:5000].lower()  # 只检查头部，提高性能
    for keyword, name, tags in DOC_FRAMEWORK_SELECTORS:
        if keyword in html_lower:
            return name, tags
    return None, GENERIC_CONTENT_TAGS


def extract_main_content(html: str, url: str = "") -> str:
    """从完整 HTML 中智能提取正文内容。

    策略优先级：
    1. 域名匹配的已知站点选择器（需要浏览器，这里跳过）
    2. 文档框架检测 → 对应标签提取
    3. 通用标签提取（main > article）
    4. 去除 head 后的 body 内容
    """
    # 策略 1: 框架检测 → 按推荐 tag 尝试
    framework, candidate_tags = detect_framework(html)

    for tag in candidate_tags:
        content = extract_tag_content(html, tag)
        if content and len(content.strip()) > 200:
            if framework:
                print(f"  [提取] {framework} 框架，使用 <{tag}> 标签", flush=True)
            else:
                print(f"  [提取] 使用 <{tag}> 标签", flush=True)
            return content

    # 策略 2: 去除 head，返回 body
    body = strip_html_head(html)
    if body and len(body.strip()) > 200:
        print(f"  [提取] 使用 <body> 全文", flush=True)
        return body

    # 最后兜底
    return html


def clean_html_noise(html_content: str) -> str:
    """清理 HTML 中的噪声元素（导航、侧边栏、广告、弹窗等）"""
    # 移除 <nav>, <header>, <footer>, <aside> 等语义标签
    for tag in ['nav', 'header', 'footer', 'aside']:
        html_content = re.sub(
            rf'<{tag}[^>]*>.*?</{tag}>',
            '', html_content, flags=re.DOTALL | re.IGNORECASE
        )

    # 移除常见噪声 class
    noise_patterns = [
        r'<div[^>]*class="[^"]*(?:sidebar|recommend|related|comment|login|signin|share|toolbar|copyright|footer|advertisement|ad-wrap)[^"]*"[^>]*>.*?</div>',
    ]
    for p in noise_patterns:
        html_content = re.sub(p, '', html_content, flags=re.DOTALL | re.IGNORECASE)

    # 移除 <script>, <style>, <svg>, <noscript> 标签
    for tag in ['script', 'style', 'svg', 'noscript']:
        html_content = re.sub(
            rf'<{tag}[^>]*>.*?</{tag}>',
            '', html_content, flags=re.DOTALL | re.IGNORECASE
        )

    return html_content


def validate_markdown(md: str, min_length: int = 100) -> bool:
    """校验 Markdown 内容质量。

    检查：
    1. 长度是否足够
    2. 是否包含大量 HTML meta 标签残留（说明提取失败）
    3. 是否有实际文本内容
    """
    if not md or len(md.strip()) < min_length:
        return False

    # 检查 meta 标签残留占比
    meta_pattern = r'<(?:meta|link|script)[^>]*>'
    meta_matches = re.findall(meta_pattern, md, re.IGNORECASE)
    if len(meta_matches) > 5:
        return False

    # 检查实际可读文本占比（去除 markdown 语法和空白后）
    text_only = re.sub(r'[#*`\[\]()|\-_=~>!{}\s]', '', md)
    if len(text_only) < 50:
        return False

    return True


def match_site_selector(url: str) -> str | None:
    """根据 URL 域名匹配已知站点的内容选择器"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    for domain, selector in SITE_SELECTORS.items():
        if hostname == domain or hostname.endswith("." + domain):
            return selector
    return None


def match_site_expand_selectors(url: str) -> list:
    """根据 URL 域名匹配已知站点的"展开全文"按钮选择器"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    for domain, selectors in SITE_EXPAND_SELECTORS.items():
        if domain in hostname:
            return selectors
    return []


def preprocess_code_blocks(html: str) -> str:
    """预处理 HTML 中的代码块，移除行号列并转换为干净的 <pre><code> 格式"""

    # 0. 处理 SyntaxHighlighter 风格
    def replace_syntaxhighlighter(match):
        full = match.group(0)
        lang_match = re.search(r'<code\s+class="(\w+)\s', full)
        lang = lang_match.group(1) if lang_match else ''
        lines = []
        for line_match in re.finditer(r'<div\s+class="line\s+number\d+[^"]*"[^>]*>(.*?)</div>', full, re.DOTALL):
            line_html = line_match.group(1)
            line_html = line_html.replace('&nbsp;', ' ')
            line_text = re.sub(r'<[^>]+>', '', line_html)
            line_text = line_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
            lines.append(line_text)
        if not lines:
            return full
        code_text = '\n'.join(lines)
        return f'<pre><code class="language-{lang}">{code_text}</code></pre>'

    html = re.sub(
        r'<div\s+class="container">\s*(?:<div\s+class="line\s+number\d+[^"]*"[^>]*>.*?</div>\s*)+</div>',
        replace_syntaxhighlighter, html, flags=re.DOTALL
    )
    html = re.sub(
        r'(?:<div\s+class="line\s+number\d+[^"]*"[^>]*>.*?</div>\s*){2,}',
        replace_syntaxhighlighter, html, flags=re.DOTALL
    )

    # 1. Hexo 风格
    def replace_figure_highlight(match):
        full = match.group(0)
        lang_match = re.search(r'class="highlight\s+(\w+)"', full)
        lang = lang_match.group(1) if lang_match else ''
        code_td = re.search(r'<td\s+class="code">\s*<pre>(.*?)</pre>\s*</td>', full, re.DOTALL)
        if not code_td:
            return full
        code_html = code_td.group(1)
        code_text = re.sub(r'<span[^>]*>', '', code_html)
        code_text = code_text.replace('</span>', '')
        code_text = re.sub(r'<br\s*/?>', '\n', code_text)
        code_text = code_text.strip()
        code_text = code_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
        return f'<pre><code class="language-{lang}">{code_text}</code></pre>'

    html = re.sub(r'<figure\s+class="highlight[^"]*"[^>]*>.*?</figure>', replace_figure_highlight, html, flags=re.DOTALL)

    # 2. 通用 table 行号
    html = re.sub(r'<td\s+class="(?:gutter|line-numbers?|linenums?|hljs-ln-numbers?)"[^>]*>.*?</td>', '', html, flags=re.DOTALL)

    # 3. 独立行号 span
    html = re.sub(r'<span\s+class="(?:line-number|ln-num|hljs-ln-n)"[^>]*>[^<]*</span>', '', html, flags=re.DOTALL)

    return html


def html_to_markdown(html: str, base_url: str = "") -> str:
    """将 HTML 转换为 Markdown 格式"""
    html = preprocess_code_blocks(html)

    # 提取代码块为占位符
    code_blocks = []

    def extract_code_block(match):
        full = match.group(0)
        lang_match = re.search(r'class="language-(\w+)"', full)
        lang = lang_match.group(1) if lang_match else ''
        code_match = re.search(r'<code[^>]*>(.*?)</code>', full, re.DOTALL)
        if code_match:
            code = code_match.group(1)
        else:
            code = re.sub(r'</?pre[^>]*>', '', full)
            code = re.sub(r'</?code[^>]*>', '', code)
        code = re.sub(r'<[^>]+>', '', code)
        code = code.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
        code = code.strip('\n')
        idx = len(code_blocks)
        code_blocks.append((lang, code))
        return f'\n<p>CODEBLOCKPLACEHOLDER{idx}ENDPLACEHOLDER</p>\n'

    html = re.sub(r'<pre[^>]*>\s*<code[^>]*>.*?</code>\s*</pre>', extract_code_block, html, flags=re.DOTALL)
    html = re.sub(r'<pre[^>]*>(?!.*<code).*?</pre>', extract_code_block, html, flags=re.DOTALL)

    if HTML2TEXT_AVAILABLE:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.body_width = 0
        h.unicode_snob = True
        h.skip_internal_links = True
        h.inline_links = True
        h.protect_links = False
        h.wrap_links = False
        h.mark_code = True
        h.default_image_alt = ""
        if base_url:
            h.baseurl = base_url
        md = h.handle(html)
        md = re.sub(r'\[​\]\([^)]+\)', '', md)

        for i, (lang, code) in enumerate(code_blocks):
            placeholder = f'CODEBLOCKPLACEHOLDER{i}ENDPLACEHOLDER'
            if placeholder in md:
                md = md.replace(placeholder, f'```{lang}\n{code}\n```')

        # 压缩空行（保护代码块）
        preserved_blocks = []

        def preserve_block(m):
            idx = len(preserved_blocks)
            preserved_blocks.append(m.group(0))
            return f'PRESERVEDBLOCK{idx}END'

        md = re.sub(r'```.*?\n.*?```', preserve_block, md, flags=re.DOTALL)
        md = re.sub(r'\n{3,}', '\n\n', md)
        for i, block in enumerate(preserved_blocks):
            md = md.replace(f'PRESERVEDBLOCK{i}END', block)

        return md.strip()
    else:
        text = html
        text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<h4[^>]*>(.*?)</h4>', r'\n#### \1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>', r'\n```\n\1\n```\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)

        # 回填代码块占位符
        for i, (lang, code) in enumerate(code_blocks):
            placeholder = f'CODEBLOCKPLACEHOLDER{i}ENDPLACEHOLDER'
            if placeholder in text:
                text = text.replace(placeholder, f'```{lang}\n{code}\n```')

        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        for entity, char in [('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'), ('&amp;', '&'), ('&quot;', '"')]:
            text = text.replace(entity, char)
        return text.strip()


def url_to_filename(url: str) -> str:
    """URL -> 安全文件名"""
    parsed = urlparse(url)
    path = parsed.path.strip('/').replace('/', '_').replace('.html', '').replace('.htm', '') or 'index'
    return re.sub(r'[<>:"/\\|?*]', '_', path)[:100]


FRAMEWORK_DEFAULT_TITLES = {
    "vitepress-openapi", "vitepress", "docusaurus", "nextra", "gitbook",
    "swagger ui", "redoc", "stoplight", "readme",
}


def extract_title_from_html(html: str) -> str:
    """从 HTML 中提取标题"""
    title_from_tag = ""
    # 尝试 <title>
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    if m:
        title_from_tag = m.group(1).strip()
        # 清理常见后缀 " - Site Name", " | Site Name"
        title_from_tag = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title_from_tag).strip()
    # 检查 <title> 是否为框架默认值（如 "vitepress-openapi"）
    is_framework_default = title_from_tag.lower() in FRAMEWORK_DEFAULT_TITLES
    # <h1> 作为次选（或当 <title> 是框架默认值时优先）
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
    if m:
        h1_title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if h1_title and (is_framework_default or not title_from_tag):
            return h1_title
    if title_from_tag and not is_framework_default:
        return title_from_tag
    return title_from_tag or "Untitled"


# ========== DocScraper 类 ==========

class DocScraper:
    """文档采集器 - 直接抓取优先，远程浏览器回退"""

    def __init__(self, output_dir: str = "./scraped_docs", spy_api: str = None, mode: str = "auto"):
        """
        Args:
            output_dir: 输出目录
            spy_api: 远程浏览器 API 地址
            mode: 采集模式
                - "direct": 仅直接 HTTP 抓取（快速，适合大部分文档站）
                - "browser": 仅远程浏览器（需要 JS 渲染的站点）
                - "auto": 先直接抓取，质量不佳时回退到浏览器（默认）
        """
        self.output_dir = Path(output_dir)
        self.spy_api = spy_api or SPY_API
        self.mode = mode
        self._client = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
        )
        self._browser_client = httpx.AsyncClient(timeout=120)
        self.scraped_pages = []

        (self.output_dir / "html").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "markdown").mkdir(parents=True, exist_ok=True)

    async def close(self):
        await self._client.aclose()
        await self._browser_client.aclose()

    # ========== llms.txt 快速采集（最优先） ==========

    async def try_llms_txt(self, base_url: str, url_pattern: str = None) -> dict | None:
        """尝试通过 llms-full.txt 或 llms.txt 快速获取完整文档。

        许多文档站（Mintlify, Docusaurus, GitBook 等）提供 llms-full.txt，
        包含所有页面的完整 Markdown 内容，不需要逐页采集。

        Returns:
            成功返回 {"status": "ok", ...}，不可用返回 None
        """
        parsed = urlparse(base_url)
        site_root = f"{parsed.scheme}://{parsed.netloc}"

        # 尝试 llms-full.txt（完整内容）和 llms.txt（索引）
        for txt_path in ['/llms-full.txt', '/llms.txt']:
            txt_url = site_root + txt_path
            try:
                r = await self._client.get(txt_url)
                if r.status_code != 200 or len(r.text) < 200:
                    continue
            except Exception:
                continue

            content = r.text

            if txt_path == '/llms-full.txt':
                # llms-full.txt 包含完整文档，直接按 "# Title\nSource: URL" 分割
                pages = self._parse_llms_full(content, url_pattern)
                if not pages:
                    continue

                print(f"  [llms-full.txt] 发现 {len(pages)} 篇文档", flush=True)

                for p in pages:
                    base_filename = url_to_filename(p['url'])

                    # 保存 Markdown
                    md_with_meta = f"---\ntitle: {p['title']}\nsource: {p['url']}\ndate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n---\n\n# {p['title']}\n\n{p['content']}\n"
                    md_path = self.output_dir / "markdown" / f"{base_filename}.md"
                    md_path.write_text(md_with_meta, encoding="utf-8")

                    # 保存简单 HTML（从 Markdown 渲染）
                    html_content = f"<h1>{p['title']}</h1>\n<p><em>Source: {p['url']}</em></p>\n<pre>{p['content'][:500]}...</pre>"
                    html_path = self.output_dir / "html" / f"{base_filename}.html"
                    html_path.write_text(f"<!DOCTYPE html><html><head><meta charset='UTF-8'><title>{p['title']}</title></head><body>{html_content}</body></html>", encoding="utf-8")

                    self.scraped_pages.append({
                        "title": p['title'], "url": p['url'],
                        "html_file": f"{base_filename}.html", "md_file": f"{base_filename}.md",
                        "html_path": str(html_path), "md_path": str(md_path),
                    })
                    print(f"  -> {p['title'][:50]} ({len(p['content'])} chars)", flush=True)

                return {
                    "status": "ok", "source": "llms-full.txt",
                    "total": len(pages), "success": len(pages), "failed": 0, "errors": [],
                }

            else:
                # llms.txt 是索引，提取 .md URL 列表
                md_urls = re.findall(r'\[([^\]]+)\]\((https?://[^\s)]+\.md)\)', content)
                if not md_urls:
                    continue

                if url_pattern:
                    md_urls = [(title, url) for title, url in md_urls if url_pattern in url]

                if not md_urls:
                    continue

                print(f"  [llms.txt] 发现 {len(md_urls)} 个 .md 链接，逐个下载", flush=True)

                for title, md_url in md_urls:
                    try:
                        r = await self._client.get(md_url)
                        if r.status_code != 200:
                            continue
                        md_content = r.text
                        if len(md_content.strip()) < 50:
                            continue

                        # 推断原始页面 URL（去掉 .md 后缀）
                        page_url = re.sub(r'\.md$', '', md_url)
                        if page_url.endswith('/index'):
                            page_url = page_url[:-6]

                        base_filename = url_to_filename(page_url)

                        md_with_meta = f"---\ntitle: {title}\nsource: {page_url}\ndate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n---\n\n{md_content}\n"
                        md_path = self.output_dir / "markdown" / f"{base_filename}.md"
                        md_path.write_text(md_with_meta, encoding="utf-8")

                        html_path = self.output_dir / "html" / f"{base_filename}.html"
                        html_path.write_text(f"<!DOCTYPE html><html><head><meta charset='UTF-8'><title>{title}</title></head><body><h1>{title}</h1></body></html>", encoding="utf-8")

                        self.scraped_pages.append({
                            "title": title, "url": page_url,
                            "html_file": f"{base_filename}.html", "md_file": f"{base_filename}.md",
                            "html_path": str(html_path), "md_path": str(md_path),
                        })
                        print(f"  -> {title[:50]} ({len(md_content)} chars)", flush=True)
                    except Exception as e:
                        print(f"  x {md_url}: {e}", flush=True)

                if self.scraped_pages:
                    return {
                        "status": "ok", "source": "llms.txt",
                        "total": len(md_urls), "success": len(self.scraped_pages),
                        "failed": len(md_urls) - len(self.scraped_pages), "errors": [],
                    }

        return None

    def _parse_llms_full(self, content: str, url_pattern: str = None) -> list:
        """解析 llms-full.txt 内容，按 '# Title\\nSource: URL' 分割为页面列表。"""
        pages = []
        # 按一级标题分割
        sections = re.split(r'^# ', content, flags=re.MULTILINE)

        for section in sections:
            if not section.strip():
                continue

            lines = section.split('\n', 3)
            title = lines[0].strip()

            # 查找 Source 行
            source_url = ""
            content_start = 1
            for j, line in enumerate(lines[1:], 1):
                m = re.match(r'^Source:\s*(https?://\S+)', line)
                if m:
                    source_url = m.group(1)
                    content_start = j + 1
                    break

            if not source_url:
                continue

            # URL 过滤
            if url_pattern and url_pattern not in source_url:
                continue

            # 提取内容（标题行之后的所有内容）
            page_content = '\n'.join(section.split('\n')[content_start:]).strip()

            if page_content:
                pages.append({
                    "title": title,
                    "url": source_url,
                    "content": page_content,
                })

        return pages

    # ========== 直接 HTTP 抓取（主要方式） ==========

    async def fetch_direct(self, url: str) -> str | None:
        """直接 HTTP 抓取页面 HTML。

        Returns:
            HTML 字符串，失败返回 None
        """
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"  [直接抓取] 失败: {e}", flush=True)
            return None

    async def fetch_and_extract(self, url: str, content_selector: str = None) -> tuple[str, str, str]:
        """直接抓取页面并提取内容。

        Returns:
            (title, clean_html, markdown) 三元组
        """
        raw_html = await self.fetch_direct(url)
        if not raw_html:
            return "", "", ""

        title = extract_title_from_html(raw_html)

        # 提取正文 HTML
        if content_selector:
            # 用户指定了选择器，但直接抓取模式只能处理 tag 选择器
            # 复杂 CSS 选择器需要浏览器
            tag = content_selector.strip().split()[0].strip('.#')
            content = extract_tag_content(raw_html, tag) if tag.isalpha() else None
            if not content:
                content = extract_main_content(raw_html, url)
        else:
            content = extract_main_content(raw_html, url)

        # 清理噪声
        content = clean_html_noise(content)

        # 转换 Markdown
        markdown = html_to_markdown(content, url)

        return title, content, markdown

    # ========== 远程浏览器操作（回退方式） ==========

    async def goto(self, url: str, wait_after: int = 5) -> dict:
        """通过远程浏览器访问页面"""
        r = await self._browser_client.post(f"{self.spy_api}/goto", json={"url": url, "wait_after": wait_after})
        return r.json()

    async def get_html(self, selector: str = None) -> str:
        """从远程浏览器获取 HTML"""
        params = {}
        if selector:
            params["selector"] = selector
        r = await self._browser_client.get(f"{self.spy_api}/html", params=params)
        return r.json().get("html", "")

    async def get_text(self, selector: str = "body") -> str:
        r = await self._browser_client.get(f"{self.spy_api}/text", params={"selector": selector})
        return r.json().get("text", "")

    async def get_links(self, selector: str = "a") -> list:
        r = await self._browser_client.get(f"{self.spy_api}/links", params={"selector": selector})
        return r.json().get("links", [])

    async def filter_links(self, selector: str = "a", url_pattern: str = None, exclude_pattern: str = "#") -> list:
        r = await self._browser_client.post(f"{self.spy_api}/filter_links", json={
            "selector": selector, "url_pattern": url_pattern, "exclude_pattern": exclude_pattern
        })
        return r.json().get("links", [])

    async def click(self, selector: str) -> dict:
        r = await self._browser_client.post(f"{self.spy_api}/click", json={"selector": selector})
        return r.json()

    async def screenshot(self, path: str = "/tmp/scrape_screenshot.png") -> str:
        r = await self._browser_client.get(f"{self.spy_api}/screenshot", params={"path": path})
        return r.json().get("path", path)

    async def get_page_url(self) -> str:
        r = await self._browser_client.get(f"{self.spy_api}/status")
        return r.json().get("page_url", "")

    async def stop_browser(self):
        await self._browser_client.post(f"{self.spy_api}/stop")

    async def fetch_via_browser(self, url: str, content_selector: str = None) -> tuple[str, str, str]:
        """通过远程浏览器抓取并提取内容。

        Returns:
            (title, clean_html, markdown) 三元组
        """
        try:
            await self.goto(url, wait_after=5)
        except Exception as e:
            print(f"  [浏览器] 访问失败: {e}", flush=True)
            return "", "", ""

        # 自动匹配选择器
        if not content_selector:
            content_selector = match_site_selector(url)
            if content_selector:
                print(f"  [浏览器] 自动匹配选择器: {content_selector}", flush=True)

        # 展开折叠内容
        expand_selectors = match_site_expand_selectors(url)
        for sel in expand_selectors:
            try:
                result = await self.click(sel)
                if result.get("status") == "ok":
                    print(f"  [浏览器] 展开: {sel}", flush=True)
                    break
            except Exception:
                pass

        # 获取 HTML：优先用选择器，其次尝试 main/article，最后全页
        content = ""
        if content_selector:
            content = await self.get_html(content_selector)

        if not content or len(content.strip()) < 200:
            for tag in ["main", "article"]:
                content = await self.get_html(tag)
                if content and len(content.strip()) > 200:
                    print(f"  [浏览器] 使用 <{tag}> 标签", flush=True)
                    break

        if not content or len(content.strip()) < 200:
            content = await self.get_html()
            # 全页 HTML 需要去 head
            content = strip_html_head(content)

        content = clean_html_noise(content)

        title_text = await self.get_text("title") or ""
        title_text = title_text.strip().split("\n")[0][:200]
        title_text = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title_text).strip()
        # 如果 <title> 是框架默认值，优先用 <h1>
        if title_text.lower() in FRAMEWORK_DEFAULT_TITLES or not title_text:
            h1_text = await self.get_text("h1") or ""
            h1_text = h1_text.strip().split("\n")[0][:200]
            if h1_text:
                title_text = h1_text
        title = title_text or "Untitled"

        markdown = html_to_markdown(content, url)
        return title, content, markdown

    # ========== 页面保存（核心） ==========

    async def save_page(self, url: str, content_selector: str = None) -> dict:
        """采集并保存单个页面。

        采集策略由 self.mode 控制：
        - "direct": 直接 HTTP 抓取
        - "browser": 远程浏览器
        - "auto": 先直接抓取，验证失败后回退浏览器
        """
        title, content, markdown = "", "", ""

        # === 策略 1: 直接抓取 ===
        if self.mode in ("direct", "auto"):
            title, content, markdown = await self.fetch_and_extract(url, content_selector)
            if self.mode == "auto" and not validate_markdown(markdown):
                print(f"  [直接抓取] 内容质量不佳，回退到浏览器", flush=True)
                title, content, markdown = "", "", ""

        # === 策略 2: 浏览器回退 ===
        if not validate_markdown(markdown) and self.mode in ("browser", "auto"):
            title_b, content_b, markdown_b = await self.fetch_via_browser(url, content_selector)
            if validate_markdown(markdown_b) or (not validate_markdown(markdown) and len(markdown_b) > len(markdown)):
                title, content, markdown = title_b, content_b, markdown_b

        if not title:
            title = "Untitled"
        if not content:
            return {"status": "error", "url": url, "error": "无法提取内容"}

        # === 保存文件 ===
        base_filename = url_to_filename(url)

        # HTML 文件
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.7; color: #333; }}
        pre {{ background: #f6f8fa; padding: 16px; overflow-x: auto; border-radius: 6px; }}
        code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-family: 'Fira Code', monospace; }}
        pre code {{ background: none; padding: 0; }}
        img {{ max-width: 100%; }}
        table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #f6f8fa; }}
    </style>
</head>
<body>
    <div style="background:#f6f8fa;padding:10px 15px;border-radius:6px;margin-bottom:20px;font-size:0.9em;color:#666;">
        <strong>来源:</strong> <a href="{url}">{url}</a><br>
        <strong>采集时间:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
    {content}
</body>
</html>"""

        html_path = self.output_dir / "html" / f"{base_filename}.html"
        html_path.write_text(full_html, encoding="utf-8")

        # Markdown 文件
        md_with_meta = f"""---
title: {title}
source: {url}
date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---

# {title}

{markdown}
"""
        md_path = self.output_dir / "markdown" / f"{base_filename}.md"
        md_path.write_text(md_with_meta, encoding="utf-8")

        page_info = {
            "title": title, "url": url,
            "html_file": f"{base_filename}.html", "md_file": f"{base_filename}.md",
            "html_path": str(html_path), "md_path": str(md_path),
        }
        self.scraped_pages.append(page_info)

        result = {
            "status": "ok", "title": title, "url": url,
            "html_file": f"html/{base_filename}.html",
            "md_file": f"markdown/{base_filename}.md",
            "md_length": len(markdown),
        }

        # 登录检测（仅对 markdown 文本做）
        for pattern in LOGIN_REQUIRED_PATTERNS:
            m = re.search(pattern, markdown)
            if m:
                result["login_required"] = True
                result["warning"] = f"内容可能不完整，检测到: \"{m.group()}\""
                break

        return result

    # ========== 批量采集 ==========

    async def scrape_all(self, urls: list, content_selector: str = None, delay: float = 0.5,
                        url_pattern: str = None) -> dict:
        """批量采集多个页面。默认使用浏览器渲染，确保 JS 渲染内容完整。"""
        results = {"status": "ok", "total": len(urls), "success": 0, "failed": 0, "errors": []}

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}", flush=True)
            try:
                save_result = await self.save_page(url, content_selector)
                if save_result.get("status") == "ok":
                    results["success"] += 1
                    title = save_result.get('title', '')[:50]
                    md_len = save_result.get('md_length', 0)
                    print(f"  -> {title} ({md_len} chars)", flush=True)
                else:
                    results["failed"] += 1
                    results["errors"].append({"url": url, "error": save_result.get("error")})
                    print(f"  x {save_result.get('error')}", flush=True)

                if delay > 0 and i < len(urls):
                    await asyncio.sleep(delay)

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"url": url, "error": str(e)})
                print(f"  x {e}", flush=True)

        print(f"\n采集完成! 成功: {results['success']}, 失败: {results['failed']}", flush=True)
        return results

    # ========== 获取链接（需要浏览器） ==========

    async def get_page_links(self, url: str, url_pattern: str = None) -> list:
        """获取页面链接。先尝试直接抓取提取链接，失败回退浏览器。
        当 url_pattern 过滤后链接过少（≤2），自动向上回退 pattern 重试。
        """
        raw_html = await self.fetch_direct(url)
        if not raw_html:
            print(f"  [链接提取] 回退到浏览器", flush=True)
            await self.goto(url)
            return await self.filter_links(url_pattern=url_pattern)

        # 从 HTML 提取全部链接（不过滤）
        all_links = []
        seen = set()
        for m in re.finditer(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', raw_html, re.DOTALL | re.IGNORECASE):
            href, text = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if href.startswith('#') or href.startswith('javascript:'):
                continue
            full_url = urljoin(url, href)
            if full_url not in seen:
                seen.add(full_url)
                all_links.append({"url": full_url, "text": text[:100]})

        if not url_pattern:
            return all_links if all_links else []

        # 用 url_pattern 过滤，如果结果太少则逐级回退
        pattern = url_pattern.rstrip('/')
        while pattern:
            filtered = [l for l in all_links if pattern in l["url"]]
            if len(filtered) > 2:
                return filtered
            # 向上回退一级: /docs/api-reference/music → /docs/api-reference
            parent = pattern.rsplit('/', 1)[0] if '/' in pattern else ''
            if parent == pattern:
                break
            print(f"  [链接发现] pattern '{pattern}' 仅匹配 {len(filtered)} 个链接，回退到 '{parent}'", flush=True)
            pattern = parent

        # 回退到最短 pattern 仍然不够，返回原始 pattern 的结果（可能为空）
        filtered = [l for l in all_links if url_pattern in l["url"]]
        return filtered if filtered else all_links

    # ========== 索引与合并 ==========

    def _scan_existing_pages(self) -> list:
        """扫描已有 markdown 文件"""
        pages = []
        md_dir = self.output_dir / "markdown"
        for md_file in sorted(md_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            content = md_file.read_text(encoding="utf-8")
            title_match = re.search(r'^title:\s*(.+)$', content, re.MULTILINE)
            source_match = re.search(r'^source:\s*(.+)$', content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else md_file.stem
            url = source_match.group(1).strip() if source_match else ""
            pages.append({
                "title": title, "url": url,
                "html_file": md_file.stem + ".html", "md_file": md_file.name,
                "md_path": str(md_file),
            })
        return pages

    def generate_index(self) -> dict:
        pages = self.scraped_pages or self._scan_existing_pages()
        if not pages:
            return {"status": "error", "error": "No pages found"}

        # HTML 索引
        html_items = [f'<li><a href="{p["html_file"]}">{p["title"]}</a><br><small>{p["url"]}</small></li>' for p in pages]
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>文档索引</title>
<style>body{{font-family:sans-serif;max-width:900px;margin:0 auto;padding:20px}}li{{margin:15px 0}}a{{color:#0066cc;text-decoration:none}}small{{color:#666}}</style>
</head><body>
<h1>文档索引</h1>
<p>共采集 {len(pages)} 篇文档 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<ul>{''.join(html_items)}</ul>
</body></html>"""
        (self.output_dir / "html" / "index.html").write_text(html, encoding="utf-8")

        # Markdown 索引
        md_items = [f"{i}. [{p['title']}]({p['md_file']})" for i, p in enumerate(pages, 1)]
        md = f"# 文档索引\n\n> 共 {len(pages)} 篇 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n" + '\n'.join(md_items) + '\n'
        (self.output_dir / "markdown" / "index.md").write_text(md, encoding="utf-8")

        return {"status": "ok", "count": len(pages)}

    def merge_docs(self) -> dict:
        pages = self.scraped_pages or self._scan_existing_pages()
        if not pages:
            return {"status": "error", "error": "No pages to merge"}

        toc_items = []
        merged_parts = []

        for i, p in enumerate(pages, 1):
            md_path = p.get('md_path') or str(self.output_dir / "markdown" / p['md_file'])
            if not os.path.exists(md_path):
                continue
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL).strip()
            title = p['title']
            # 如果标题是框架默认值，从 markdown 内容中提取真实标题
            if title.lower() in FRAMEWORK_DEFAULT_TITLES or title == "Untitled":
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('# ') and not line.startswith('## '):
                        title = line[2:].strip().split('[')[0].strip()
                        break
            anchor = f"doc-{i}"
            toc_items.append(f"{i}. [{title}](#{anchor})")
            merged_parts.append(f"\n\n---\n\n<a id=\"{anchor}\"></a>\n\n## {i}. {title}\n\n> 来源: {p.get('url', '')}\n\n{content}")

        merged = f"# 文档合集\n\n> 共 {len(pages)} 篇 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n## 目录\n\n" + '\n'.join(toc_items) + '\n' + ''.join(merged_parts) + '\n'

        merged_path = self.output_dir / "docs_merged.md"
        merged_path.write_text(merged, encoding="utf-8")

        return {
            "status": "ok", "merged_file": "docs_merged.md",
            "merged_path": str(merged_path), "total_pages": len(pages),
            "size_kb": round(len(merged) / 1024, 1),
        }


# ========== OpenAPI Spec 提取 ==========

async def extract_openapi_from_vitepress(page_url: str, output_dir: str) -> dict:
    """从 VitePress + vitepress-openapi 站点的 JS bundle 中提取完整 OpenAPI spec。

    已验证的精确流程（Mureka API 等站点）：
    1. 从页面 HTML 中找到 theme chunk JS 文件路径
    2. 下载 theme chunk，搜索 export 语句找到 spec 变量名
    3. 定位 spec 数据的字节范围（从 openapi="3.x.x" 到 export 语句）
    4. 用 Node.js eval 提取为 JSON
    5. 转换为 AI 友好的 Markdown

    Returns:
        {"status": "ok", "spec_path": ..., "doc_path": ..., "paths": [...], "schemas": [...]}
        或 {"status": "error", "error": ...}
    """
    import subprocess
    import tempfile

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "markdown").mkdir(exist_ok=True)
    (out / "html").mkdir(exist_ok=True)

    parsed = urlparse(page_url)
    site_root = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
    }) as client:

        # Step 1: 下载页面 HTML，找到 theme chunk 路径
        print("[1/5] 下载页面 HTML...", flush=True)
        r = await client.get(page_url)
        html = r.text

        # 找 theme chunk: href="/docs/assets/chunks/theme.XXX.js"
        theme_match = re.search(r'href="([^"]*chunks/theme[^"]*\.js)"', html)
        if not theme_match:
            # 也尝试 src= 模式
            theme_match = re.search(r'src="([^"]*chunks/theme[^"]*\.js)"', html)
        if not theme_match:
            return {"status": "error", "error": "未找到 theme chunk JS 文件"}

        theme_path = theme_match.group(1)
        theme_url = urljoin(site_root, theme_path)
        print(f"  找到 theme chunk: {theme_path}", flush=True)

        # Step 2: 下载 theme chunk
        print("[2/5] 下载 theme chunk...", flush=True)
        r = await client.get(theme_url)
        theme_js = r.text
        print(f"  大小: {len(theme_js)} bytes", flush=True)

        # Step 3: 找到 export 语句，确定 spec 变量名
        print("[3/5] 定位 OpenAPI spec 数据...", flush=True)
        # 典型模式: export{cMe as R,ga as h,ND as p,aMe as s}
        # 其中 "as s" 是 spec 变量（被 lean.js 中 import {s as Y} 引用）
        export_match = re.search(r'export\{([^}]+)\}', theme_js)
        if not export_match:
            return {"status": "error", "error": "未找到 export 语句"}

        exports_str = export_match.group(1)
        # 找 "xxx as s" — 导出为 s 的变量就是 spec
        spec_var = None
        for part in exports_str.split(','):
            part = part.strip()
            if part.endswith(' as s'):
                spec_var = part.replace(' as s', '').strip()
                break

        if not spec_var:
            return {"status": "error", "error": f"未找到 spec 导出变量 (exports: {exports_str})"}
        print(f"  spec 变量: {spec_var}", flush=True)

        # 找 spec_var 的赋值：aMe=rMe  → 找 rMe 的定义
        assign_match = re.search(rf'{re.escape(spec_var)}=(\w+)', theme_js)
        if not assign_match:
            return {"status": "error", "error": f"未找到 {spec_var} 的赋值"}

        real_var = assign_match.group(1)
        # 找 rMe={openapi:YLe,info:ZLe,...} 获取组成变量名
        struct_match = re.search(rf'{re.escape(real_var)}=\{{openapi:(\w+),info:(\w+),servers:(\w+),security:(\w+),tags:(\w+),paths:(\w+),components:(\w+)\}}', theme_js)
        if not struct_match:
            return {"status": "error", "error": f"未找到 {real_var} 的结构定义"}

        openapi_var = struct_match.group(1)
        print(f"  openapi 变量起始: {openapi_var}", flush=True)

        # Step 4: 提取 spec 数据段并用 Node.js eval
        print("[4/5] 提取并解析 spec 数据...", flush=True)

        # 找到数据起始位置（openapi_var="3.x.x"）
        data_start = theme_js.find(f'{openapi_var}="3.')
        if data_start < 0:
            # 也试 3.1
            data_start = theme_js.find(f'{openapi_var}="3')
        if data_start < 0:
            return {"status": "error", "error": f"未找到 {openapi_var} 的数据起始位置"}

        # 找到数据结束位置（spec_var=real_var 或 export 前）
        end_marker = f',{spec_var}={real_var}'
        data_end = theme_js.find(end_marker, data_start)
        if data_end < 0:
            end_marker = f'{spec_var}={real_var}'
            data_end = theme_js.find(end_marker, data_start)
        if data_end < 0:
            return {"status": "error", "error": "未找到数据结束位置"}

        # 也去掉 rMe={...} 定义本身
        rme_start = theme_js.rfind(f',{real_var}=', data_start, data_end)
        if rme_start > 0:
            data_end = rme_start

        spec_code = theme_js[data_start:data_end]
        print(f"  数据段: {len(spec_code)} bytes", flush=True)

        # 写入临时 .mjs 文件并用 node 执行
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mjs', delete=False, encoding='utf-8') as f:
            f.write(f"var {spec_code};\n")
            f.write(f"var spec={{openapi:{struct_match.group(1)},info:{struct_match.group(2)},servers:{struct_match.group(3)},security:{struct_match.group(4)},tags:{struct_match.group(5)},paths:{struct_match.group(6)},components:{struct_match.group(7)}}};\n")
            f.write("import { writeFileSync } from 'fs';\n")
            spec_json_path = str(out / "openapi.json")
            f.write(f"writeFileSync({json.dumps(spec_json_path)}, JSON.stringify(spec, null, 2));\n")
            f.write("console.log(JSON.stringify({paths: Object.keys(spec.paths), schemas: Object.keys(spec.components?.schemas || {})}));\n")
            tmp_path = f.name

        try:
            result = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"status": "error", "error": f"Node.js 执行失败: {result.stderr[:500]}"}

            info = json.loads(result.stdout.strip())
            print(f"  提取成功! {len(info['paths'])} 个端点, {len(info['schemas'])} 个 Schema", flush=True)
        finally:
            os.unlink(tmp_path)

        # Step 5: 转换为 Markdown
        print("[5/5] 转换为 Markdown...", flush=True)
        with open(spec_json_path) as f:
            spec = json.load(f)

        doc = _openapi_to_markdown(spec)
        doc_path = out / "docs_merged.md"
        doc_path.write_text(doc, encoding="utf-8")
        print(f"  输出: {doc_path} ({len(doc)} bytes, {doc.count(chr(10))} 行)", flush=True)

        return {
            "status": "ok",
            "spec_path": spec_json_path,
            "doc_path": str(doc_path),
            "paths": info["paths"],
            "schemas": info["schemas"],
            "size_kb": round(len(doc) / 1024, 1),
        }


def _resolve_ref(ref: str, spec: dict):
    """解析 $ref 引用"""
    parts = ref.lstrip('#/').split('/')
    obj = spec
    for p in parts:
        if isinstance(obj, dict) and p in obj:
            obj = obj[p]
        else:
            return None
    return obj


def _format_schema(schema: dict, spec: dict, indent: int = 0, seen: set = None) -> str:
    """递归格式化 Schema 为 Markdown 列表"""
    if seen is None:
        seen = set()
    if '$ref' in schema:
        ref = schema['$ref']
        if ref in seen:
            return f"{'  ' * indent}- (circular ref: {ref.split('/')[-1]})"
        seen = seen | {ref}
        resolved = _resolve_ref(ref, spec)
        if not resolved:
            return f"{'  ' * indent}- (unresolved: {ref})"
        return _format_schema(resolved, spec, indent, seen)

    lines = []
    if schema.get('type') == 'object' and 'properties' in schema:
        required = set(schema.get('required', []))
        for name, prop in schema['properties'].items():
            req = " **required**" if name in required else ""
            ptype = prop.get('type', '')
            if '$ref' in prop:
                ptype = prop['$ref'].split('/')[-1]
            fmt = f" (format: {prop['format']})" if prop.get('format') else ""
            enum_vals = prop.get('enum', [])
            enum_str = f" enum: `{'`, `'.join(str(e) for e in enum_vals)}`" if enum_vals else ""
            desc = (prop.get('description') or '').replace('\n', ' ').strip()

            lines.append(f"{'  ' * indent}- `{name}` ({ptype}{fmt}){req}{enum_str}")
            if desc:
                lines.append(f"{'  ' * indent}  {desc}")

            # Recurse
            if '$ref' in prop:
                nested = _format_schema(_resolve_ref(prop['$ref'], spec) or {}, spec, indent + 1, seen.copy())
                if nested:
                    lines.append(nested)
            elif prop.get('type') == 'array' and 'items' in prop:
                items = prop['items']
                if '$ref' in items:
                    nested = _format_schema(_resolve_ref(items['$ref'], spec) or {}, spec, indent + 1, seen.copy())
                    if nested:
                        lines.append(nested)
            elif prop.get('type') == 'object' and 'properties' in prop:
                nested = _format_schema(prop, spec, indent + 1, seen.copy())
                if nested:
                    lines.append(nested)
    return '\n'.join(lines)


def _openapi_to_markdown(spec: dict) -> str:
    """将 OpenAPI spec 转换为 AI 友好的 Markdown 文档"""
    out = []
    info = spec.get('info', {})
    out.append(f"# {info.get('title', 'API Documentation')}")
    out.append(f"\n{info.get('description', '')}")
    out.append(f"\n**Version:** {info.get('version', '')}")
    if spec.get('servers'):
        out.append(f"\n**Base URL:** {spec['servers'][0].get('url', '')}")
    out.append("\n**Authentication:** Bearer Token")

    # Tags
    if spec.get('tags'):
        out.append("\n## API Overview\n")
        for tag in spec['tags']:
            out.append(f"- **{tag['name']}**: {tag.get('description', '')}")

    out.append("\n---\n")

    # Endpoints
    for path, methods in spec.get('paths', {}).items():
        for method, op in methods.items():
            if method not in ('get', 'post', 'put', 'delete', 'patch'):
                continue
            out.append(f"## {op.get('summary', path)}")
            out.append(f"\n`{method.upper()} {path}`")
            tags = op.get('tags', [])
            if tags:
                out.append(f"\n**Tags:** {', '.join(tags)}")
            desc = op.get('description', '')
            if desc:
                out.append(f"\n{desc}")

            # Parameters
            params = op.get('parameters', [])
            if params:
                out.append("\n### Parameters\n")
                for p in params:
                    req = " **required**" if p.get('required') else ""
                    out.append(f"- `{p['name']}` ({p.get('in', '')}, {p.get('schema', {}).get('type', '')}){req}")
                    if p.get('description'):
                        out.append(f"  {p['description']}")

            # Request Body
            rb = op.get('requestBody', {})
            if rb:
                out.append("\n### Request Body\n")
                for ct, content in rb.get('content', {}).items():
                    out.append(f"**Content-Type:** `{ct}`\n")
                    schema = content.get('schema', {})
                    if '$ref' in schema:
                        out.append(f"**Schema:** {schema['$ref'].split('/')[-1]}\n")
                        resolved = _resolve_ref(schema['$ref'], spec)
                        if resolved:
                            out.append(_format_schema(resolved, spec))
                    else:
                        out.append(_format_schema(schema, spec))

            # Code samples
            samples = op.get('x-codeSamples', [])
            if samples:
                out.append("\n### Code Samples\n")
                for s in samples:
                    lang = s.get('lang', '')
                    out.append(f"**{lang}:**\n```{lang.lower()}\n{s['source']}\n```")

            # Responses
            resps = op.get('responses', {})
            if resps:
                out.append("\n### Responses\n")
                for code, resp in resps.items():
                    out.append(f"**{code}:** {resp.get('description', '')}\n")
                    for ct, content in resp.get('content', {}).items():
                        schema = content.get('schema', {})
                        if '$ref' in schema:
                            out.append(f"Schema: {schema['$ref'].split('/')[-1]}\n")
                            resolved = _resolve_ref(schema['$ref'], spec)
                            if resolved:
                                out.append(_format_schema(resolved, spec))
                        elif schema:
                            out.append(_format_schema(schema, spec))
            out.append("\n---\n")

    # Schemas
    schemas = spec.get('components', {}).get('schemas', {})
    if schemas:
        out.append("## Schemas\n")
        for name, schema in schemas.items():
            out.append(f"### {name}\n")
            if schema.get('description'):
                out.append(schema['description'])
            out.append(f"\nType: {schema.get('type', 'object')}\n")
            out.append(_format_schema(schema, spec))
            out.append("")

    return '\n'.join(out)


# ========== CLI ==========

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="文档采集工具")
    parser.add_argument("--mode", choices=["direct", "browser", "auto"], default="browser",
                        help="采集模式: direct=直接HTTP, browser=远程浏览器(默认), auto=先HTTP后浏览器")
    sub = parser.add_subparsers(dest="command")

    # scrape
    p_scrape = sub.add_parser("scrape", help="采集单个页面")
    p_scrape.add_argument("url", help="目标 URL")
    p_scrape.add_argument("--output", "-o", default="./scraped_docs")
    p_scrape.add_argument("--selector", help="内容选择器")
    p_scrape.add_argument("--api", help="远程 API 地址")

    # scrape-all
    p_all = sub.add_parser("scrape-all", help="批量采集")
    p_all.add_argument("--urls-file", help="URL 列表文件")
    p_all.add_argument("--start-url", help="起始页 URL")
    p_all.add_argument("--url-pattern", help="链接过滤模式")
    p_all.add_argument("--output", "-o", default="./scraped_docs")
    p_all.add_argument("--selector", help="内容选择器")
    p_all.add_argument("--delay", type=float, default=0.5)
    p_all.add_argument("--api", help="远程 API 地址")

    # links
    p_links = sub.add_parser("links", help="获取页面链接")
    p_links.add_argument("url", help="目标 URL")
    p_links.add_argument("--pattern", help="URL 过滤模式")
    p_links.add_argument("--api", help="远程 API 地址")

    # merge
    p_merge = sub.add_parser("merge", help="合并文档")
    p_merge.add_argument("--output", "-o", default="./scraped_docs")

    # index
    p_index = sub.add_parser("index", help="生成索引")
    p_index.add_argument("--output", "-o", default="./scraped_docs")

    # extract-openapi
    p_extract = sub.add_parser("extract-openapi", help="从 VitePress/vitepress-openapi JS bundle 中提取 OpenAPI spec")
    p_extract.add_argument("url", help="任意一个 API 文档页面 URL")
    p_extract.add_argument("--output", "-o", default="./scraped_docs")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    output_dir = getattr(args, 'output', './scraped_docs')

    # extract-openapi 不需要 DocScraper 实例
    if args.command == "extract-openapi":
        result = await extract_openapi_from_vitepress(args.url, output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    spy_api = getattr(args, 'api', None)
    scraper = DocScraper(output_dir=output_dir, spy_api=spy_api, mode=args.mode)

    try:
        if args.command == "scrape":
            result = await scraper.save_page(args.url, args.selector)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.command == "scrape-all":
            if args.urls_file:
                with open(args.urls_file) as f:
                    urls = [l.strip() for l in f if l.strip()]
            elif args.start_url:
                links = await scraper.get_page_links(args.start_url, args.url_pattern)
                urls = [l["url"] for l in links]
                print(f"发现 {len(urls)} 个链接")
            else:
                print("需要 --urls-file 或 --start-url")
                return
            result = await scraper.scrape_all(urls, args.selector, args.delay,
                                              url_pattern=getattr(args, 'url_pattern', None))
            print(f"\n完成: {result['success']} 成功, {result['failed']} 失败")
            scraper.generate_index()
            merge_result = scraper.merge_docs()
            if merge_result.get("status") == "ok":
                print(f"已合并为 {merge_result['merged_file']} ({merge_result['size_kb']} KB)")

        elif args.command == "links":
            links = await scraper.get_page_links(args.url, args.pattern)
            for link in links:
                print(f"  {link.get('text', '')[:40]:40s} {link['url']}")
            print(f"\n共 {len(links)} 个链接")

        elif args.command == "merge":
            result = scraper.merge_docs()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.command == "index":
            result = scraper.generate_index()
            print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
