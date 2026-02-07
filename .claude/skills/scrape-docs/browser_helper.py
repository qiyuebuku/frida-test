#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文档采集浏览器助手 - HTTP 服务器模式

通过 HTTP API 提供浏览器操作能力，保持会话持久化。
AI 分析由 Claude Code 直接完成。

输出目录结构：
<output_dir>/
├── docs_merged.md      # 合并后的完整文档（AI 友好）
├── html/               # HTML 文件目录
│   ├── index.html
│   └── *.html
└── markdown/           # Markdown 文件目录
    ├── index.md
    └── *.md

主要改进 (v2):
- 添加 /scrape_all 批量采集 API
- 添加 /filter_links POST 方法获取链接（避免 URL 编码问题）
- 增加页面加载超时时间
- 优化错误处理和日志输出
"""

import os
import sys
import json
import asyncio
import re
import html as html_module
from datetime import datetime
from urllib.parse import urljoin, urlparse
from aiohttp import web

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    print("请先安装 camoufox: pip install camoufox[geoip] && camoufox fetch")
    sys.exit(1)

try:
    import html2text
    HTML2TEXT_AVAILABLE = True
except ImportError:
    HTML2TEXT_AVAILABLE = False

# OCR 支持（延迟初始化）
_ocr_engine = None
OCR_AVAILABLE = False

try:
    from rapidocr_onnxruntime import RapidOCR
    OCR_AVAILABLE = True
except ImportError:
    pass


# 默认浏览器 profile 目录（用于保持登录状态）
DEFAULT_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".scrape-docs-profile")

# 已知站点的内容选择器映射（用户未指定 content_selector 时自动匹配）
SITE_SELECTORS = {
    "blog.csdn.net": "article .markdown_views",
    "bbs.kanxue.com": ".message_md_type",
    "ihandmine.github.io": ".post-body",   # Hexo 通用
    "jianshu.com": "article",
    "juejin.cn": ".article-content",
    "zhihu.com": ".RichContent-inner",
    "medium.com": "article",
    "segmentfault.com": "article",
    "cnblogs.com": "#cnblogs_post_body",
    "52pojie.cn": ".message",              # 吾爱破解
    "freebuf.com": ".article-content",
    "wooyun.js.org": ".content",
}

# 已知站点的"展开全文/查看更多"按钮选择器
SITE_EXPAND_SELECTORS = {
    "blog.csdn.net": [
        ".btn-readmore",                    # "阅读全文" 按钮
        "#article_content .hide-article-box btn",
        ".readall_box .read-more-btn",      # 另一种样式
    ],
    "zhihu.com": [
        "button.ContentItem-expandButton",  # "展开阅读全文"
        ".RichContent-inner--collapsed .ContentItem-expandButton",
    ],
    "jianshu.com": [
        ".collapse_tips",                   # "展开全文"
    ],
    "juejin.cn": [
        ".article-content .read-more",      # "展开阅读全文"
    ],
}

# 页面内容被截断/需要登录的关键词检测
# 在页面的可见文本中匹配这些正则，命中任意一条即判定为"内容不完整，需要登录"
LOGIN_REQUIRED_PATTERNS = [
    r"登录.{0,6}(?:查看|阅读|浏览|获取|解锁)",       # 登录后查看
    r"(?:查看|阅读|浏览).{0,6}(?:请|需).{0,4}登录",   # 查看请登录
    r"(?:回复|点赞|关注).{0,6}(?:可|才能|即可).{0,6}(?:查看|阅读|浏览|获取|解锁|可见)", # 回复或点赞可查看完整内容
    r"请先登录",
    r"需要登录",
    r"登录可见",
    r"回复可见",
    r"隐藏内容",
    r"本帖隐藏",
    r"购买主题",
    r"(?:注册|登录).{0,6}(?:才能|方可|即可).{0,6}(?:查看|阅读|下载)",
    r"(?:积分|金币|雪币|K币).{0,6}(?:才能|方可|即可).{0,6}(?:查看|下载|阅读)",
    r"您需要.{0,10}(?:登录|注册)",
    r"游客.{0,10}(?:无法|不能|不可).{0,6}(?:查看|阅读|浏览|下载)",
    r"(?:开通|购买).{0,6}(?:VIP|会员|SVIP).{0,6}(?:查看|阅读|解锁)",
    r"(?:关注|收藏).{0,6}(?:博主|作者).{0,6}(?:查看|阅读|才能)",
    r"展开阅读全文",
    r"阅读全文",
]


def match_site_selector(url: str) -> str | None:
    """根据 URL 域名匹配已知站点的内容选择器"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    for domain, selector in SITE_SELECTORS.items():
        if hostname == domain or hostname.endswith("." + domain):
            return selector
    return None


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


def get_ocr():
    """获取 OCR 引擎实例（延迟加载）"""
    global _ocr_engine
    if _ocr_engine is None:
        if not OCR_AVAILABLE:
            raise ImportError("OCR 不可用，请安装: pip install rapidocr_onnxruntime")
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_image_bytes(img_bytes: bytes) -> str:
    """对图片字节进行 OCR 识别，返回识别文本"""
    ocr = get_ocr()
    result, _ = ocr(img_bytes)
    if not result:
        return ""
    lines = [item[1] for item in result]
    return "\n".join(lines)


def preprocess_code_blocks(html: str) -> str:
    """预处理 HTML 中的代码块，移除行号列并转换为干净的 <pre><code> 格式

    支持的常见代码块 HTML 结构：
    1. Hexo/highlight.js: <figure class="highlight LANG"><table><tr><td class="gutter">行号</td><td class="code">代码</td></tr></table></figure>
    2. 通用 table 行号: <table><tr><td class="line-numbers/linenums/gutter">行号</td><td class="code/content">代码</td></tr></table>
    3. 行号 span: <pre> 中包含 <span class="line-number"> 等行号元素
    4. SyntaxHighlighter (看雪等): <div class="container"><div class="line number1..."><code class="lang type">text</code>...</div>...</div>
    """

    # 0. 处理 SyntaxHighlighter 风格（看雪论坛等）
    # 结构: <div class="container"><div class="line number1 ..."><code class="java plain">text</code>...</div>...</div>
    # 注意: container 外面可能还有重复的散落 <div class="line ..."> 行
    def replace_syntaxhighlighter(match):
        full = match.group(0)
        # 提取语言（从第一个 <code> 的 class 中获取）
        lang_match = re.search(r'<code\s+class="(\w+)\s', full)
        lang = lang_match.group(1) if lang_match else ''
        # 提取每行内容
        lines = []
        for line_match in re.finditer(r'<div\s+class="line\s+number\d+[^"]*"[^>]*>(.*?)</div>', full, re.DOTALL):
            line_html = line_match.group(1)
            # &nbsp; -> 空格
            line_html = line_html.replace('&nbsp;', ' ')
            # 移除所有 HTML 标签，保留文本
            line_text = re.sub(r'<[^>]+>', '', line_html)
            # 解码 HTML 实体
            line_text = line_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
            lines.append(line_text)
        if not lines:
            return full
        code_text = '\n'.join(lines)
        return f'<pre><code class="language-{lang}">{code_text}</code></pre>'

    # 先处理 <div class="container"> 包裹的代码块
    html = re.sub(
        r'<div\s+class="container">\s*(?:<div\s+class="line\s+number\d+[^"]*"[^>]*>.*?</div>\s*)+</div>',
        replace_syntaxhighlighter,
        html,
        flags=re.DOTALL
    )

    # 处理 container 外面重复的散落行（SyntaxHighlighter 有时会重复输出一份不带 container 的）
    # 这些行紧跟在刚处理完的 </pre> 后面，格式相同
    def replace_loose_syntaxhighlighter_lines(match):
        return replace_syntaxhighlighter(match)

    html = re.sub(
        r'(?:<div\s+class="line\s+number\d+[^"]*"[^>]*>.*?</div>\s*){2,}',
        replace_loose_syntaxhighlighter_lines,
        html,
        flags=re.DOTALL
    )

    # 1. 处理 Hexo 风格: <figure class="highlight LANG">...<td class="gutter">...</td><td class="code"><pre>...</pre></td>...</figure>
    def replace_figure_highlight(match):
        full = match.group(0)
        # 提取语言
        lang_match = re.search(r'class="highlight\s+(\w+)"', full)
        lang = lang_match.group(1) if lang_match else ''
        # 提取 <td class="code"> 中的内容
        code_td = re.search(r'<td\s+class="code">\s*<pre>(.*?)</pre>\s*</td>', full, re.DOTALL)
        if not code_td:
            return full
        code_html = code_td.group(1)
        # 移除 <span> 标签，保留文本内容
        code_text = re.sub(r'<span[^>]*>', '', code_html)
        code_text = code_text.replace('</span>', '')
        # <br> 转换为换行
        code_text = re.sub(r'<br\s*/?>', '\n', code_text)
        # 移除 <span class="line"> 包装
        code_text = code_text.strip()
        # 解码 HTML 实体
        code_text = code_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
        return f'<pre><code class="language-{lang}">{code_text}</code></pre>'

    html = re.sub(
        r'<figure\s+class="highlight[^"]*"[^>]*>.*?</figure>',
        replace_figure_highlight,
        html,
        flags=re.DOTALL
    )

    # 2. 处理通用 table 行号结构: <td class="gutter/line-numbers/linenums">...</td>
    html = re.sub(
        r'<td\s+class="(?:gutter|line-numbers?|linenums?|hljs-ln-numbers?)"[^>]*>.*?</td>',
        '',
        html,
        flags=re.DOTALL
    )

    # 3. 移除独立的行号 span 元素
    html = re.sub(
        r'<span\s+class="(?:line-number|ln-num|hljs-ln-n)"[^>]*>[^<]*</span>',
        '',
        html,
        flags=re.DOTALL
    )

    return html


def html_to_markdown(html: str, base_url: str = "") -> str:
    """将 HTML 转换为 Markdown 格式"""
    # 预处理代码块，移除行号
    html = preprocess_code_blocks(html)

    # 提取代码块，替换为占位符，避免 html2text 错误处理
    code_blocks = []

    def extract_code_block(match):
        full = match.group(0)
        # 提取语言
        lang_match = re.search(r'class="language-(\w+)"', full)
        lang = lang_match.group(1) if lang_match else ''
        # 提取代码内容
        code_match = re.search(r'<code[^>]*>(.*?)</code>', full, re.DOTALL)
        if code_match:
            code = code_match.group(1)
        else:
            code = re.sub(r'</?pre[^>]*>', '', full)
            code = re.sub(r'</?code[^>]*>', '', code)
        # 清理残余 HTML 标签
        code = re.sub(r'<[^>]+>', '', code)
        # 解码 HTML 实体
        code = code.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
        code = code.strip('\n')
        idx = len(code_blocks)
        code_blocks.append((lang, code))
        return f'\n<p>CODEBLOCKPLACEHOLDER{idx}ENDPLACEHOLDER</p>\n'

    html = re.sub(r'<pre[^>]*>\s*<code[^>]*>.*?</code>\s*</pre>', extract_code_block, html, flags=re.DOTALL)
    # 处理没有 <code> 包裹的 <pre> 块
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

        # 还原代码块占位符为 markdown 围栏
        for i, (lang, code) in enumerate(code_blocks):
            placeholder = f'CODEBLOCKPLACEHOLDER{i}ENDPLACEHOLDER'
            if placeholder in md:
                md = md.replace(placeholder, f'```{lang}\n{code}\n```')

        # 压缩空行（保护代码块内的空行不被压缩）
        # 先提取代码块 -> 压缩空行 -> 再放回
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
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&quot;', '"', text)
        return text.strip()


def match_site_expand_selectors(url: str) -> list:
    """根据 URL 域名匹配已知站点的"展开全文"按钮选择器"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    for domain, selectors in SITE_EXPAND_SELECTORS.items():
        if domain in hostname:
            return selectors
    return []


class BrowserSession:
    """浏览器会话管理"""

    def __init__(self, output_dir: str = "./scraped_docs", headless: bool = True, profile_dir: str = None):
        self.output_dir = output_dir
        self.html_dir = os.path.join(output_dir, 'html')
        self.markdown_dir = os.path.join(output_dir, 'markdown')
        self.headless = headless
        self.profile_dir = profile_dir
        self.browser = None
        self.page = None
        self._camoufox = None
        self.current_url = None
        self.scraped_pages = []
        self._login_event = None  # 用于等待用户登录完成

        # 创建目录结构
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.html_dir, exist_ok=True)
        os.makedirs(self.markdown_dir, exist_ok=True)

    async def start(self):
        """启动浏览器"""
        if self.browser:
            return {"status": "already_running"}

        camoufox_kwargs = {
            "headless": self.headless,
            "humanize": 1.5,
            "os": "windows",
            "locale": "zh-CN",
            "i_know_what_im_doing": True,
        }

        # 使用 persistent context 保留浏览器登录状态
        if self.profile_dir:
            os.makedirs(self.profile_dir, exist_ok=True)
            camoufox_kwargs["persistent_context"] = True
            camoufox_kwargs["user_data_dir"] = self.profile_dir
            print(f"  使用 profile 目录: {self.profile_dir}", flush=True)

        self._camoufox = AsyncCamoufox(**camoufox_kwargs)
        self.browser = await self._camoufox.__aenter__()
        self.page = await self.browser.new_page()
        await self.page.set_viewport_size({"width": 1440, "height": 900})
        return {"status": "started", "profile_dir": self.profile_dir}

    async def stop(self):
        """关闭浏览器"""
        if self._camoufox:
            await self._camoufox.__aexit__(None, None, None)
            self.browser = None
            self.page = None
            self._camoufox = None
        return {"status": "stopped"}

    async def goto(self, url: str, timeout: int = 60000) -> dict:
        """访问页面

        Args:
            url: 要访问的 URL
            timeout: 超时时间（毫秒），默认 60 秒
        """
        if not self.page:
            await self.start()

        try:
            # 先尝试 networkidle，如果超时则使用 domcontentloaded
            try:
                await self.page.goto(url, wait_until='networkidle', timeout=timeout)
            except Exception:
                # networkidle 超时，尝试 domcontentloaded
                await self.page.goto(url, wait_until='domcontentloaded', timeout=timeout)

            await asyncio.sleep(0.5)
            self.current_url = self.page.url
            title = await self.page.title()
            login_check = await self.check_login_required()
            result = {"status": "ok", "url": self.current_url, "title": title}
            result.update(login_check)
            return result
        except Exception as e:
            # 即使超时，页面可能已经加载了大部分内容
            self.current_url = self.page.url if self.page else url
            return {"status": "partial", "error": str(e), "url": self.current_url}

    async def screenshot(self, path: str = None) -> dict:
        """截图"""
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        if not path:
            path = os.path.join(self.output_dir, "screenshot.png")

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        await self.page.screenshot(path=path)
        return {"status": "ok", "path": os.path.abspath(path)}

    async def get_html(self, selector: str = None, simplified: bool = True) -> dict:
        """获取 HTML"""
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        if selector:
            try:
                element = self.page.locator(selector).first
                if await element.count() > 0:
                    html = await element.inner_html()
                else:
                    html = await self.page.content()
            except Exception:
                html = await self.page.content()
        else:
            html = await self.page.content()

        if simplified:
            html = self._simplify_html(html)

        return {"status": "ok", "html": html, "url": self.page.url, "title": await self.page.title()}

    def _simplify_html(self, html: str, max_length: int = 30000) -> str:
        """简化 HTML"""
        html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<!--[\s\S]*?-->', '', html)
        html = re.sub(r'<svg[^>]*>[\s\S]*?</svg>', '<svg/>', html, flags=re.IGNORECASE)
        html = re.sub(r'\s+data-[a-z0-9-]+="[^"]*"', '', html, flags=re.IGNORECASE)
        html = re.sub(r'\s+', ' ', html)
        if len(html) > max_length:
            html = html[:max_length] + "\n... (truncated)"
        return html

    async def get_links(self, selector: str) -> dict:
        """获取链接列表"""
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        links = []
        try:
            elements = self.page.locator(selector)
            count = await elements.count()
            for i in range(count):
                el = elements.nth(i)
                href = await el.get_attribute('href')
                text = (await el.inner_text()).strip()
                if href:
                    full_url = urljoin(self.page.url, href)
                    links.append({"url": full_url, "text": text})
            return {"status": "ok", "links": links, "count": len(links)}
        except Exception as e:
            return {"status": "error", "error": str(e), "links": []}

    async def filter_links(self, selector: str = "a", url_pattern: str = None, exclude_pattern: str = None) -> dict:
        """获取并过滤链接列表

        Args:
            selector: CSS 选择器，默认 "a"
            url_pattern: URL 必须包含的字符串（如 "/docs"）
            exclude_pattern: URL 必须不包含的字符串（如 "#"）

        Returns:
            去重后的链接列表
        """
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        links = []
        seen_urls = set()

        try:
            elements = self.page.locator(selector)
            count = await elements.count()

            for i in range(count):
                el = elements.nth(i)
                href = await el.get_attribute('href')
                if not href:
                    continue

                full_url = urljoin(self.page.url, href)

                # 应用过滤
                if url_pattern and url_pattern not in full_url:
                    continue
                if exclude_pattern and exclude_pattern in full_url:
                    continue

                # 去重
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                text = (await el.inner_text()).strip()
                links.append({"url": full_url, "text": text})

            return {"status": "ok", "links": links, "count": len(links)}
        except Exception as e:
            return {"status": "error", "error": str(e), "links": []}

    async def scrape_all(self, urls: list, content_selector: str = None, delay: float = 0.5, ocr_images: bool = False) -> dict:
        """批量采集多个 URL

        Args:
            urls: 要采集的 URL 列表
            content_selector: 内容选择器（可选）
            delay: 每个页面之间的延迟（秒）
            ocr_images: 是否对图片进行 OCR 识别

        Returns:
            采集结果统计
        """
        if not self.page:
            await self.start()

        results = {
            "status": "ok",
            "total": len(urls),
            "success": 0,
            "failed": 0,
            "errors": []
        }

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}", flush=True)

            try:
                # 访问页面
                goto_result = await self.goto(url)
                if goto_result.get("status") == "error":
                    results["failed"] += 1
                    results["errors"].append({"url": url, "error": goto_result.get("error")})
                    continue

                # 等待页面稳定
                await asyncio.sleep(delay)

                # 保存页面
                save_result = await self.save_page(content_selector=content_selector, ocr_images=ocr_images)
                if save_result.get("status") == "ok":
                    results["success"] += 1
                    print(f"  -> {save_result.get('title', 'Unknown')}", flush=True)
                else:
                    results["failed"] += 1
                    results["errors"].append({"url": url, "error": save_result.get("error")})
                    print(f"  -> Error: {save_result.get('error')}", flush=True)

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"url": url, "error": str(e)})
                print(f"  -> Exception: {e}", flush=True)

        print(f"\n采集完成! 成功: {results['success']}, 失败: {results['failed']}", flush=True)
        return results

    async def get_href(self, selector: str) -> dict:
        """获取单个链接"""
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        try:
            element = self.page.locator(selector).first
            if await element.count() > 0:
                href = await element.get_attribute('href')
                if href:
                    return {"status": "ok", "href": urljoin(self.page.url, href)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

        return {"status": "ok", "href": None}

    async def wait_login(self, url: str = None, timeout: int = 300) -> dict:
        """等待用户在浏览器中完成登录

        Args:
            url: 可选的登录页面 URL，如果提供则先导航到该页面
            timeout: 最长等待时间（秒），默认 300 秒（5 分钟）

        Returns:
            登录完成后的状态信息
        """
        if not self.page:
            await self.start()

        if self.headless:
            return {"status": "error", "error": "wait_login 仅在非无头模式下可用，请使用 --no-headless 启动"}

        if url:
            await self.goto(url)

        print(f"  请在浏览器窗口中完成登录操作...", flush=True)
        print(f"  登录成功后，调用 POST /login_done 通知服务器继续", flush=True)

        self._login_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._login_event.wait(), timeout=timeout)
            current_url = self.page.url
            title = await self.page.title()
            print(f"  登录完成! 当前页面: {title}", flush=True)
            return {"status": "ok", "url": current_url, "title": title}
        except asyncio.TimeoutError:
            print(f"  登录等待超时", flush=True)
            return {"status": "timeout", "error": f"登录等待超时 ({timeout}s)"}
        finally:
            self._login_event = None

    async def notify_login_done(self) -> dict:
        """通知登录已完成"""
        if self._login_event:
            self._login_event.set()
            return {"status": "ok", "message": "已通知登录完成"}
        return {"status": "ok", "message": "没有等待中的登录请求"}

    async def expand_content(self, url: str = None) -> dict:
        """自动展开页面中被折叠的内容（如"阅读全文"按钮）

        Args:
            url: 可选，用于匹配站点规则。如果不传则使用当前页面 URL

        Returns:
            展开操作的结果
        """
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        target_url = url or self.page.url
        expand_selectors = match_site_expand_selectors(target_url)

        if not expand_selectors:
            return {"status": "ok", "expanded": False, "message": "该站点无需展开操作"}

        expanded = False
        for sel in expand_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
                    expanded = True
                    print(f"  已点击展开按钮: {sel}", flush=True)
                    break
            except Exception as e:
                print(f"  展开按钮点击失败 ({sel}): {e}", flush=True)

        return {"status": "ok", "expanded": expanded}

    async def check_login_required(self) -> dict:
        """检测当前页面是否需要登录才能查看完整内容

        通过提取页面可见文本，匹配 LOGIN_REQUIRED_PATTERNS 中的关键词来判断。

        Returns:
            {
                "login_required": bool,     # 是否需要登录
                "matched_text": str | None, # 命中的关键词文本
            }
        """
        if not self.page:
            return {"login_required": False, "matched_text": None}

        try:
            # 提取页面可见文本（比解析 HTML 快，且只包含用户可见的内容）
            text = await self.page.inner_text("body")
            for pattern in LOGIN_REQUIRED_PATTERNS:
                m = re.search(pattern, text)
                if m:
                    matched = m.group()
                    print(f"  检测到需要登录: \"{matched}\"", flush=True)
                    return {"login_required": True, "matched_text": matched}
        except Exception as e:
            print(f"  登录检测异常: {e}", flush=True)

        return {"login_required": False, "matched_text": None}

    async def click(self, selector: str) -> dict:
        """点击元素"""
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        try:
            element = self.page.locator(selector).first
            if await element.count() > 0:
                await element.click()
                await self.page.wait_for_load_state('networkidle')
                self.current_url = self.page.url
                return {"status": "ok", "url": self.current_url}
        except Exception as e:
            return {"status": "error", "error": str(e)}

        return {"status": "error", "error": "Element not found"}

    async def ocr_page_images(self, content_selector: str = None, min_width: int = 80, min_height: int = 30) -> dict:
        """对当前页面中的图片进行 OCR 识别

        Args:
            content_selector: 限定在此选择器范围内的图片
            min_width: 最小图片宽度（像素），过小的图标不处理
            min_height: 最小图片高度（像素）

        Returns:
            {src: ocr_text} 的映射
        """
        if not self.page:
            return {}

        if not OCR_AVAILABLE:
            print("OCR 不可用，跳过图片识别", flush=True)
            return {}

        # 尝试关闭常见弹窗/遮罩层，避免弹窗文字混入 OCR 结果
        close_selectors = [
            '.modal .close', '.dialog .close', '.popup .close',
            '[class*="login"] .close', '[class*="mask"]',
            '.passport-login-container .close',  # CSDN
            '.btn-close', '[aria-label="Close"]',
        ]
        for sel in close_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        selector = f"{content_selector} img" if content_selector else "img"
        images = self.page.locator(selector)
        count = await images.count()
        ocr_results = {}

        for i in range(count):
            img = images.nth(i)
            try:
                # 滚动到图片位置确保加载
                await img.scroll_into_view_if_needed(timeout=5000)
                await asyncio.sleep(0.3)

                box = await img.bounding_box()
                if not box or box['width'] < min_width or box['height'] < min_height:
                    continue

                src = await img.get_attribute('src') or f"img_{i}"

                img_bytes = await img.screenshot()
                text = ocr_image_bytes(img_bytes)
                if text.strip():
                    ocr_results[src] = text.strip()
                    print(f"  OCR [{i}] ({box['width']:.0f}x{box['height']:.0f}): {len(text)} chars", flush=True)
            except Exception as e:
                print(f"  OCR [{i}] error: {e}", flush=True)

        return ocr_results

    async def save_page(self, title: str = None, content_selector: str = None, ocr_images: bool = False) -> dict:
        """保存当前页面

        Args:
            title: 自定义标题
            content_selector: 内容选择器
            ocr_images: 是否对图片进行 OCR 识别
        """
        if not self.page:
            return {"status": "error", "error": "No page loaded"}

        url = self.page.url
        title = title or await self.page.title() or "Untitled"
        clean_title = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title).strip()

        # 自动匹配已知站点的内容选择器（仅当用户未手动指定时）
        if not content_selector:
            auto_selector = match_site_selector(url)
            if auto_selector:
                content_selector = auto_selector
                print(f"  自动匹配选择器: {content_selector}", flush=True)

        # 自动展开被折叠的内容（如 CSDN 的"阅读全文"）
        expand_result = await self.expand_content(url)
        if expand_result.get("expanded"):
            print(f"  已自动展开折叠内容", flush=True)

        # OCR 图片（在获取 HTML 之前执行，因为需要与页面交互）
        ocr_map = {}
        if ocr_images:
            print("  正在 OCR 识别图片...", flush=True)
            ocr_map = await self.ocr_page_images(content_selector)
            print(f"  OCR 完成: {len(ocr_map)} 张图片识别成功", flush=True)

        # 获取内容
        if content_selector:
            result = await self.get_html(content_selector, simplified=False)
            content = result.get("html", "")
        else:
            content = await self.page.content()

        # 清理 HTML 噪声（导航、侧边栏、广告等）
        content = clean_html_noise(content)

        # 将 OCR 结果插入 HTML 中 <img> 标签后面
        if ocr_map:
            for src, ocr_text in ocr_map.items():
                escaped_src = re.escape(src)
                # 在 <img> 标签后插入 OCR 文本块（转义防止 HTML 注入）
                ocr_html = f'<pre class="ocr-result"><code>{html_module.escape(ocr_text)}</code></pre>'
                # 找到包含该 src 的 img 标签，在其后插入 OCR 文本
                match = re.search(rf'<img[^>]*src="[^"]*{escaped_src}"[^>]*/?>',  content)
                if match:
                    insert_pos = match.end()
                    content = content[:insert_pos] + '\n' + ocr_html + content[insert_pos:]

        # 生成文件名
        parsed = urlparse(url)
        path = parsed.path.strip('/').replace('/', '_').replace('.html', '').replace('.htm', '') or 'index'
        base_filename = re.sub(r'[<>:"/\\|?*]', '_', path)[:100]

        # === 保存 HTML 文件到 html/ 目录 ===
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
        h1, h2, h3, h4 {{ color: #1a1a1a; margin-top: 1.5em; }}
        a {{ color: #0066cc; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        blockquote {{ border-left: 4px solid #ddd; margin: 1em 0; padding-left: 1em; color: #666; }}
        .source-info {{ background: #f6f8fa; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 0.9em; color: #666; }}
    </style>
</head>
<body>
    <div class="source-info">
        <strong>来源:</strong> <a href="{url}">{url}</a><br>
        <strong>采集时间:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
    {content}
</body>
</html>"""

        html_filepath = os.path.join(self.html_dir, base_filename + '.html')
        with open(html_filepath, 'w', encoding='utf-8') as f:
            f.write(full_html)

        # === 保存 Markdown 文件到 markdown/ 目录 ===
        markdown_content = html_to_markdown(content, url)
        markdown_with_meta = f"""---
title: {clean_title}
source: {url}
date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---

# {clean_title}

{markdown_content}
"""

        md_filepath = os.path.join(self.markdown_dir, base_filename + '.md')
        with open(md_filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_with_meta)

        # 记录
        page_info = {
            "title": clean_title,
            "url": url,
            "html_file": base_filename + '.html',
            "md_file": base_filename + '.md',
            "filepath": html_filepath,
            "markdown_filepath": md_filepath
        }
        self.scraped_pages.append(page_info)

        # 检测内容是否完整（是否需要登录）
        login_check = await self.check_login_required()
        result = {
            "status": "ok",
            "title": clean_title,
            "url": url,
            "html_file": f"html/{base_filename}.html",
            "md_file": f"markdown/{base_filename}.md"
        }
        result.update(login_check)
        if login_check.get("login_required"):
            result["warning"] = f"内容可能不完整，检测到: \"{login_check['matched_text']}\"。建议使用 --no-headless --profile default 启动并登录后重新采集。"
        return result

    async def generate_index(self, pages: list = None) -> dict:
        """生成索引页"""
        pages = pages or self.scraped_pages

        # === HTML 索引 ===
        html_items = []
        for p in pages:
            html_items.append(f'<li><a href="{p["html_file"]}">{p["title"]}</a><br><small>{p["url"]}</small></li>')

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>文档索引</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #1a1a1a; border-bottom: 2px solid #3eaf7c; padding-bottom: 10px; }}
        li {{ margin: 15px 0; }}
        a {{ font-size: 1.1em; color: #0066cc; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        small {{ color: #666; }}
        .stats {{ background: #f6f8fa; padding: 15px; border-radius: 6px; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <h1>文档索引</h1>
    <div class="stats">
        <strong>共采集 {len(pages)} 篇文档</strong><br>
        <small>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small>
    </div>
    <ul>{''.join(html_items)}</ul>
</body>
</html>"""

        html_path = os.path.join(self.html_dir, 'index.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # === Markdown 索引 ===
        md_items = []
        for i, p in enumerate(pages, 1):
            md_items.append(f"{i}. [{p['title']}]({p['md_file']})")

        markdown = f"""# 文档索引

> 共采集 {len(pages)} 篇文档
> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 目录

{chr(10).join(md_items)}
"""

        md_path = os.path.join(self.markdown_dir, 'index.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown)

        return {
            "status": "ok",
            "html_index": "html/index.html",
            "md_index": "markdown/index.md",
            "count": len(pages)
        }

    async def merge_markdown(self) -> dict:
        """合并所有 Markdown 为单个文件，放在根目录"""
        pages = self.scraped_pages
        if not pages:
            return {"status": "error", "error": "No pages scraped yet"}

        merged_parts = []
        toc_items = []

        for i, p in enumerate(pages, 1):
            md_path = p.get('markdown_filepath')
            if md_path and os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
                    content = content.strip()

                title = p['title']
                anchor = f"doc-{i}"
                toc_items.append(f"{i}. [{title}](#{anchor})")
                merged_parts.append(f"\n\n---\n\n<a id=\"{anchor}\"></a>\n\n## {i}. {title}\n\n> 来源: {p['url']}\n\n{content}")

        merged_content = f"""# 文档合集

> 共 {len(pages)} 篇文档
> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 目录

{chr(10).join(toc_items)}

{''.join(merged_parts)}
"""

        # 保存到根目录
        merged_path = os.path.join(self.output_dir, 'docs_merged.md')
        with open(merged_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)

        return {
            "status": "ok",
            "merged_file": "docs_merged.md",
            "total_pages": len(pages),
            "size_kb": round(len(merged_content) / 1024, 1)
        }


# 全局会话
session: BrowserSession = None

# 后台任务状态
background_task: dict = {
    "running": False,
    "task_type": None,
    "progress": 0,
    "total": 0,
    "current_url": None,
    "success": 0,
    "failed": 0,
    "errors": [],
    "completed": False,
    "result": None
}


async def handle_goto(request):
    data = await request.json()
    url = data.get('url')
    if not url:
        return web.json_response({"status": "error", "error": "url is required"}, status=400)
    result = await session.goto(url)
    return web.json_response(result)


async def handle_screenshot(request):
    path = request.query.get('path')
    result = await session.screenshot(path)
    return web.json_response(result)


async def handle_html(request):
    selector = request.query.get('selector')
    simplified = request.query.get('simplified', 'true').lower() == 'true'
    result = await session.get_html(selector, simplified)
    return web.json_response(result)


async def handle_links(request):
    selector = request.query.get('selector', 'a')
    result = await session.get_links(selector)
    return web.json_response(result)


async def handle_filter_links(request):
    """POST /filter_links - 获取并过滤链接（避免 URL 编码问题）"""
    data = await request.json()
    selector = data.get('selector', 'a')
    url_pattern = data.get('url_pattern')  # URL 必须包含的字符串
    exclude_pattern = data.get('exclude_pattern', '#')  # URL 必须不包含的字符串
    result = await session.filter_links(selector, url_pattern, exclude_pattern)
    return web.json_response(result)


async def run_scrape_all_background(urls: list, content_selector: str, delay: float, ocr_images: bool = False):
    """后台运行批量采集任务"""
    global background_task

    background_task["running"] = True
    background_task["task_type"] = "scrape_all"
    background_task["progress"] = 0
    background_task["total"] = len(urls)
    background_task["success"] = 0
    background_task["failed"] = 0
    background_task["errors"] = []
    background_task["completed"] = False
    background_task["result"] = None

    for i, url in enumerate(urls, 1):
        background_task["progress"] = i
        background_task["current_url"] = url
        print(f"[{i}/{len(urls)}] {url}", flush=True)

        try:
            # 访问页面
            goto_result = await session.goto(url)
            if goto_result.get("status") == "error":
                background_task["failed"] += 1
                background_task["errors"].append({"url": url, "error": goto_result.get("error")})
                continue

            # 等待页面稳定
            await asyncio.sleep(delay)

            # 保存页面
            save_result = await session.save_page(content_selector=content_selector, ocr_images=ocr_images)
            if save_result.get("status") == "ok":
                background_task["success"] += 1
                print(f"  -> {save_result.get('title', 'Unknown')}", flush=True)
            else:
                background_task["failed"] += 1
                background_task["errors"].append({"url": url, "error": save_result.get("error")})
                print(f"  -> Error: {save_result.get('error')}", flush=True)

        except Exception as e:
            background_task["failed"] += 1
            background_task["errors"].append({"url": url, "error": str(e)})
            print(f"  -> Exception: {e}", flush=True)

    background_task["running"] = False
    background_task["completed"] = True
    background_task["result"] = {
        "status": "ok",
        "total": len(urls),
        "success": background_task["success"],
        "failed": background_task["failed"],
        "errors": background_task["errors"][:10]  # 只返回前10个错误
    }
    print(f"\n采集完成! 成功: {background_task['success']}, 失败: {background_task['failed']}", flush=True)


async def handle_scrape_all(request):
    """POST /scrape_all - 批量采集多个 URL（后台执行）

    立即返回，任务在后台运行。通过 /task_status 查询进度。
    """
    global background_task

    # 检查是否有任务正在运行
    if background_task["running"]:
        return web.json_response({
            "status": "error",
            "error": "A background task is already running. Check /task_status for progress."
        }, status=409)

    data = await request.json()
    urls = data.get('urls', [])
    if not urls:
        return web.json_response({"status": "error", "error": "urls is required"}, status=400)
    content_selector = data.get('selector')
    delay = data.get('delay', 0.5)
    ocr_images = data.get('ocr_images', False)

    # 启动后台任务
    asyncio.create_task(run_scrape_all_background(urls, content_selector, delay, ocr_images))

    return web.json_response({
        "status": "ok",
        "message": f"Background task started for {len(urls)} URLs. Check /task_status for progress.",
        "total": len(urls)
    })


async def handle_task_status(_request):
    """GET /task_status - 查询后台任务状态"""
    return web.json_response({
        "status": "ok",
        "task": background_task.copy()
    })


async def handle_href(request):
    selector = request.query.get('selector')
    if not selector:
        return web.json_response({"status": "error", "error": "selector is required"}, status=400)
    result = await session.get_href(selector)
    return web.json_response(result)


async def handle_click(request):
    data = await request.json()
    selector = data.get('selector')
    if not selector:
        return web.json_response({"status": "error", "error": "selector is required"}, status=400)
    result = await session.click(selector)
    return web.json_response(result)


async def handle_wait_login(request):
    """POST /wait_login - 等待用户在浏览器中登录"""
    data = await request.json()
    url = data.get('url')
    timeout = data.get('timeout', 300)
    result = await session.wait_login(url=url, timeout=timeout)
    return web.json_response(result)


async def handle_login_done(_request):
    """POST /login_done - 通知服务器登录已完成"""
    result = await session.notify_login_done()
    return web.json_response(result)


async def handle_expand(_request):
    """POST /expand - 手动触发展开页面内容"""
    result = await session.expand_content()
    return web.json_response(result)


async def handle_check_login(_request):
    """GET /check_login - 检测当前页面是否需要登录才能查看完整内容"""
    result = await session.check_login_required()
    return web.json_response({"status": "ok", **result})


async def handle_save(request):
    data = await request.json()
    title = data.get('title')
    selector = data.get('selector')
    ocr_images = data.get('ocr_images', False)
    result = await session.save_page(title, selector, ocr_images=ocr_images)
    return web.json_response(result)


async def handle_index(_request):
    result = await session.generate_index()
    return web.json_response(result)


async def handle_merge(_request):
    result = await session.merge_markdown()
    return web.json_response(result)


async def handle_status(_request):
    return web.json_response({
        "status": "ok",
        "running": session.browser is not None,
        "current_url": session.current_url,
        "scraped_count": len(session.scraped_pages),
        "output_dir": session.output_dir,
        "ocr_available": OCR_AVAILABLE,
        "headless": session.headless,
        "profile_dir": session.profile_dir,
        "waiting_login": session._login_event is not None and not session._login_event.is_set()
    })


async def handle_stop(_request):
    result = await session.stop()
    asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
    return web.json_response(result)


async def start_server(port: int, output_dir: str, headless: bool, profile_dir: str = None):
    global session
    session = BrowserSession(output_dir=output_dir, headless=headless, profile_dir=profile_dir)
    await session.start()

    app = web.Application()
    app.router.add_post('/goto', handle_goto)
    app.router.add_get('/screenshot', handle_screenshot)
    app.router.add_get('/html', handle_html)
    app.router.add_get('/links', handle_links)
    app.router.add_post('/filter_links', handle_filter_links)  # 新增：POST 方式获取过滤后的链接
    app.router.add_get('/href', handle_href)
    app.router.add_post('/click', handle_click)
    app.router.add_post('/wait_login', handle_wait_login)
    app.router.add_post('/login_done', handle_login_done)
    app.router.add_post('/expand', handle_expand)
    app.router.add_get('/check_login', handle_check_login)
    app.router.add_post('/save', handle_save)
    app.router.add_post('/scrape_all', handle_scrape_all)  # 新增：批量采集（后台执行）
    app.router.add_get('/task_status', handle_task_status)  # 新增：查询后台任务状态
    app.router.add_post('/index', handle_index)
    app.router.add_post('/merge', handle_merge)
    app.router.add_get('/status', handle_status)
    app.router.add_post('/stop', handle_stop)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()

    print(json.dumps({
        "status": "server_started",
        "port": port,
        "output_dir": output_dir,
        "headless": headless,
        "profile_dir": profile_dir
    }))

    while True:
        await asyncio.sleep(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='文档采集浏览器助手')
    parser.add_argument('command', choices=['serve'], help='启动 HTTP 服务器')
    parser.add_argument('--port', type=int, default=9222, help='端口')
    parser.add_argument('--output', default='./scraped_docs', help='输出目录')
    parser.add_argument('--headless', action='store_true', default=True, help='无头模式')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='显示浏览器')
    parser.add_argument('--profile', default=None, help=f'浏览器 profile 目录（保持登录状态），默认不使用。传 "default" 则使用 {DEFAULT_PROFILE_DIR}')

    args = parser.parse_args()

    if args.command == 'serve':
        profile_dir = args.profile
        if profile_dir == "default":
            profile_dir = DEFAULT_PROFILE_DIR
        asyncio.run(start_server(args.port, args.output, args.headless, profile_dir=profile_dir))


if __name__ == '__main__':
    main()
