"""
浏览器间谍服务 - 拦截网络请求，发现隐藏的数据 API

camoufox 为可选依赖，未安装不影响主服务启动。
路由定义在 routers/spy.py 中。
"""

import time
import json

try:
    from camoufox.async_api import AsyncCamoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False


class BrowserSpy:
    """浏览器间谍 - 拦截和记录网络请求"""

    def __init__(self):
        self._browser_ctx = None
        self._browser = None
        self._context = None
        self.page = None
        self.requests = []
        self.responses = []
        self.console_logs = []
        self.max_records = 500
        self._started = False

    @property
    def available(self) -> bool:
        return CAMOUFOX_AVAILABLE

    @property
    def started(self) -> bool:
        return self._started and self.page is not None

    async def start(self, headless=True):
        if not CAMOUFOX_AVAILABLE:
            raise RuntimeError("camoufox 未安装，请: pip install camoufox[geoip] && camoufox fetch")
        if self._started:
            return
        try:
            self._browser_ctx = AsyncCamoufox(headless=headless)
            self._browser = await self._browser_ctx.__aenter__()
            # camoufox 0.4.11 + playwright 1.61.0 cannot create a page through
            # Browser.new_page(), because Playwright sends viewport.isMobile to
            # Camoufox's Firefox protocol. Creating an explicit no_viewport
            # context avoids that incompatible protocol field.
            self._context = await self._browser.new_context(no_viewport=True)
            self.page = await self._context.new_page()
            self.page.on("request", self._on_request)
            self.page.on("response", self._on_response)
            self.page.on("requestfailed", self._on_request_failed)
            self.page.on("console", self._on_console)
            self._started = True
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        if self.page:
            await self.page.close()
            self.page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser_ctx:
            await self._browser_ctx.__aexit__(None, None, None)
            self._browser_ctx = None
        self._browser = None
        self._started = False
        self.clear()

    def _on_request(self, request):
        if len(self.requests) >= self.max_records:
            self.requests.pop(0)
        self.requests.append({
            "url": request.url, "method": request.method,
            "resource_type": request.resource_type,
            "headers": dict(request.headers) if request.headers else {},
            "post_data": request.post_data, "timestamp": time.time(),
        })

    def _on_response(self, response):
        if len(self.responses) >= self.max_records:
            self.responses.pop(0)
        self.responses.append({
            "url": response.url, "status": response.status,
            "headers": dict(response.headers) if response.headers else {},
            "resource_type": response.request.resource_type,
            "timestamp": time.time(), "_response_obj": response,
        })

    def _on_request_failed(self, request):
        if len(self.requests) >= self.max_records:
            self.requests.pop(0)
        self.requests.append({
            "url": request.url, "method": request.method,
            "resource_type": request.resource_type,
            "failure": request.failure, "timestamp": time.time(), "failed": True,
        })

    def _on_console(self, msg):
        if len(self.console_logs) >= self.max_records:
            self.console_logs.pop(0)
        self.console_logs.append({"type": msg.type, "text": msg.text, "timestamp": time.time()})

    def clear(self):
        self.requests.clear()
        self.responses.clear()
        self.console_logs.clear()

    def get_api_requests(self, url_contains: str = None) -> list:
        api_types = {"xhr", "fetch"}
        results = []
        for resp in self.responses:
            if resp["resource_type"] not in api_types:
                continue
            if url_contains and url_contains not in resp["url"]:
                continue
            req = next((r for r in self.requests if r["url"] == resp["url"] and not r.get("failed")), None)
            results.append({
                "url": resp["url"], "method": req["method"] if req else "?",
                "status": resp["status"],
                "content_type": resp["headers"].get("content-type", ""),
                "post_data": req.get("post_data") if req else None,
                "request_headers": req.get("headers", {}) if req else {},
                "response_headers": resp["headers"],
            })
        return results

    async def get_response_body(self, url_contains: str) -> dict:
        for resp in reversed(self.responses):
            if url_contains in resp["url"]:
                try:
                    body = await resp["_response_obj"].text()
                    try:
                        return {"url": resp["url"], "status": resp["status"], "body": json.loads(body)}
                    except json.JSONDecodeError:
                        return {"url": resp["url"], "status": resp["status"], "body_text": body[:5000]}
                except Exception as e:
                    return {"url": resp["url"], "error": str(e)}
        return {"error": f"未找到包含 '{url_contains}' 的响应"}

    # ==================== 页面操作（从 browser_helper 迁移） ====================

    async def get_links(self, selector: str = "a") -> list:
        """获取页面所有链接"""
        if not self.started:
            return []
        from urllib.parse import urljoin
        links = []
        elements = self.page.locator(selector)
        count = await elements.count()
        for i in range(count):
            el = elements.nth(i)
            href = await el.get_attribute("href")
            text = (await el.inner_text()).strip()
            if href:
                full_url = urljoin(self.page.url, href)
                links.append({"url": full_url, "text": text})
        return links

    async def filter_links(self, selector: str = "a", url_pattern: str = None, exclude_pattern: str = None) -> list:
        """获取并过滤链接（去重）"""
        all_links = await self.get_links(selector)
        seen = set()
        filtered = []
        for link in all_links:
            url = link["url"]
            if url_pattern and url_pattern not in url:
                continue
            if exclude_pattern and exclude_pattern in url:
                continue
            if url in seen:
                continue
            seen.add(url)
            filtered.append(link)
        return filtered

    async def click(self, selector: str) -> dict:
        """点击页面元素"""
        if not self.started:
            return {"status": "error", "error": "浏览器未启动"}
        try:
            element = self.page.locator(selector).first
            if await element.count() > 0:
                await element.click()
                await self.page.wait_for_timeout(2000)
                return {"status": "ok", "url": self.page.url}
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "error", "error": "元素未找到"}

    async def get_href(self, selector: str) -> str:
        """获取元素的 href 属性"""
        if not self.started:
            return None
        from urllib.parse import urljoin
        try:
            element = self.page.locator(selector).first
            if await element.count() > 0:
                href = await element.get_attribute("href")
                return urljoin(self.page.url, href) if href else None
        except Exception:
            pass
        return None


# 全局单例
spy = BrowserSpy()
