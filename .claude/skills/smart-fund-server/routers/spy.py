"""浏览器探索 API — 拦截网络请求，发现隐藏的数据接口"""

from typing import Optional
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services.tools.browser_spy import spy

router = APIRouter(prefix="/api/spy", tags=["浏览器探索"])


class GotoRequest(BaseModel):
    url: str
    wait_after: int = 3
    timeout: int = 60000
    clear: bool = True


class EvalRequest(BaseModel):
    js: str


@router.get("/status", summary="浏览器状态")
async def status():
    return {
        "available": spy.available,
        "started": spy.started,
        "page_url": spy.page.url if spy.started else None,
        "requests_count": len(spy.requests),
        "responses_count": len(spy.responses),
        "console_logs_count": len(spy.console_logs),
    }


@router.post("/start", summary="启动浏览器")
async def start(headless: bool = True):
    if not spy.available:
        return {"status": "error", "error": "camoufox 未安装，请: pip install camoufox[geoip] && camoufox fetch"}
    if spy.started:
        return {"status": "ok", "message": "已在运行"}
    await spy.start(headless=headless)
    return {"status": "ok"}


@router.post("/stop", summary="停止浏览器")
async def stop():
    await spy.stop()
    return {"status": "ok"}


@router.post("/goto", summary="访问页面（自动记录所有请求）")
async def goto(req: GotoRequest):
    if not spy.started:
        await spy.start(headless=True)
    if req.clear:
        spy.clear()
    try:
        await spy.page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout)
    except Exception:
        pass
    await spy.page.wait_for_timeout(req.wait_after * 1000)
    api_reqs = spy.get_api_requests()
    return {
        "status": "ok",
        "url": spy.page.url,
        "title": await spy.page.title(),
        "total_requests": len(spy.requests),
        "api_requests": len(api_reqs),
        "failed_requests": len([r for r in spy.requests if r.get("failed")]),
    }


@router.get("/api", summary="查看 API 请求（XHR/Fetch）")
async def api_requests(url: Optional[str] = None, limit: int = 30):
    if not spy.started:
        return {"count": 0, "api_requests": [], "hint": "浏览器未启动，先 POST /api/spy/start"}
    results = spy.get_api_requests(url)
    simplified = []
    for r in results[-limit:]:
        parsed = urlparse(r["url"])
        simplified.append({
            "url": r["url"], "method": r["method"], "status": r["status"],
            "content_type": r["content_type"].split(";")[0].strip(),
            "path": parsed.path,
            "query": parse_qs(parsed.query),
            "post_data": r["post_data"][:200] if r.get("post_data") else None,
        })
    return {"count": len(simplified), "api_requests": simplified}


@router.get("/body", summary="查看指定请求的响应体")
async def response_body(url: str = Query(..., description="URL 关键词")):
    if not spy.started:
        return {"error": "浏览器未启动"}
    return await spy.get_response_body(url)


@router.get("/requests", summary="查看所有请求")
async def all_requests(url: Optional[str] = None, type: Optional[str] = None, limit: int = 50):
    results = []
    for r in spy.requests:
        if r.get("failed"):
            continue
        if url and url not in r["url"]:
            continue
        if type and r["resource_type"] != type:
            continue
        results.append({"url": r["url"], "method": r["method"], "type": r["resource_type"]})
    return {"count": len(results), "requests": results[-limit:]}


@router.get("/console", summary="查看控制台日志")
async def console(type: Optional[str] = None, limit: int = 50):
    logs = spy.console_logs
    if type:
        logs = [l for l in logs if l["type"] == type]
    return {"count": len(logs), "logs": logs[-limit:]}


@router.get("/failed", summary="查看失败的请求")
async def failed():
    f = [r for r in spy.requests if r.get("failed")]
    return {"count": len(f), "requests": [{"url": r["url"], "failure": r.get("failure")} for r in f[-30:]]}


@router.get("/screenshot", summary="截图")
async def screenshot(path: str = "/tmp/spy_screenshot.png"):
    if not spy.started:
        return {"error": "浏览器未启动"}
    await spy.page.screenshot(path=path, full_page=False)
    return {"status": "ok", "path": path}


@router.post("/eval", summary="在页面中执行 JS")
async def eval_js(req: EvalRequest):
    if not spy.started:
        return {"error": "浏览器未启动"}
    try:
        result = await spy.page.evaluate(req.js)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/html", summary="获取页面 HTML")
async def html(selector: Optional[str] = None, limit: int = 0):
    if not spy.started:
        return {"error": "浏览器未启动"}
    if selector:
        try:
            el = await spy.page.query_selector(selector)
            content = await el.inner_html() if el else ""
        except Exception:
            content = ""
    else:
        content = await spy.page.content()
    return {"html": content[:limit] if limit > 0 else content, "length": len(content)}


@router.get("/text", summary="获取页面纯文本")
async def text(selector: str = "body", limit: int = 0):
    if not spy.started:
        return {"error": "浏览器未启动"}
    try:
        content = await spy.page.inner_text(selector)
    except Exception:
        content = ""
    return {"text": content[:limit] if limit > 0 else content, "length": len(content)}


@router.get("/cookies", summary="获取所有 cookie（含 httpOnly）")
async def cookies(domain: Optional[str] = None):
    """通过 Playwright context.cookies() 获取完整 cookie，包含 httpOnly"""
    if not spy.started:
        return {"error": "浏览器未启动"}
    all_cookies = await spy.page.context.cookies()
    if domain:
        all_cookies = [c for c in all_cookies if domain in c.get("domain", "")]
    return {"cookies": all_cookies, "count": len(all_cookies)}


@router.post("/clear", summary="清空所有记录")
async def clear():
    spy.clear()
    return {"status": "ok"}


# ==================== 页面操作（从 browser_helper 迁移） ====================


class ClickRequest(BaseModel):
    selector: str


class FilterLinksRequest(BaseModel):
    selector: str = "a"
    url_pattern: Optional[str] = None
    exclude_pattern: Optional[str] = "#"


@router.get("/links", summary="获取页面所有链接")
async def links(selector: str = "a"):
    if not spy.started:
        return {"error": "浏览器未启动"}
    result = await spy.get_links(selector)
    return {"status": "ok", "links": result, "count": len(result)}


@router.post("/filter_links", summary="获取并过滤链接（去重）")
async def filter_links(req: FilterLinksRequest):
    if not spy.started:
        return {"error": "浏览器未启动"}
    result = await spy.filter_links(req.selector, req.url_pattern, req.exclude_pattern)
    return {"status": "ok", "links": result, "count": len(result)}


@router.post("/click", summary="点击页面元素")
async def click(req: ClickRequest):
    if not spy.started:
        return {"error": "浏览器未启动"}
    return await spy.click(req.selector)


@router.get("/href", summary="获取元素的 href 属性")
async def href(selector: str = Query(..., description="CSS 选择器")):
    if not spy.started:
        return {"error": "浏览器未启动"}
    result = await spy.get_href(selector)
    return {"status": "ok", "href": result}
