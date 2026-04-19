#!/usr/bin/env python3
"""
Smart API Router — 根据请求中的 model 字段路由到不同 AI 后端。

功能：
  - 模型路由：GLM-5.1 → 智谱, claude-* → pincc, plan-opus → 改写后转 pincc
  - 认证：x-api-key 白名单
  - 会话日志：完整记录每次对话（问题 + 回答 + 模型 + 耗时），按会话分文件
  - 配置热更新：修改 config.json 无需重启
"""

import asyncio
import fnmatch
import json
import logging
import ssl
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

CONFIG_PATH = Path(__file__).parent / "config.json"
LOG_DIR = Path(__file__).parent / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("router")

# ── 配置 ──────────────────────────────────────────────

_config_cache = {"data": None, "mtime": 0}


def load_config() -> dict:
    try:
        st = CONFIG_PATH.stat()
        if st.st_mtime != _config_cache["mtime"]:
            _config_cache["data"] = json.loads(CONFIG_PATH.read_text())
            _config_cache["mtime"] = st.st_mtime
    except Exception:
        if _config_cache["data"] is None:
            raise
    return _config_cache["data"]


def find_route(model: str, config: dict) -> dict:
    for route in config["routes"]:
        for pattern in route["patterns"]:
            if fnmatch.fnmatch(model, pattern):
                return route
    return config["routes"][config.get("default_route", 0)]


# ── 认证 ──────────────────────────────────────────────

def check_auth(request, config) -> bool:
    tokens = config.get("auth_tokens", [])
    if not tokens:
        return True
    key = request.headers.get("x-api-key", "")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]
    # 接受白名单 token 或 Anthropic OAuth/API token（sk-ant- 前缀）
    return key in tokens or key.startswith("sk-ant-")


# ── 全局连接池 ────────────────────────────────────────

_session: ClientSession | None = None


async def get_session() -> ClientSession:
    global _session
    if _session is None or _session.closed:
        ssl_ctx = ssl.create_default_context()
        connector = TCPConnector(ssl=ssl_ctx, limit=50, ttl_dns_cache=300)
        timeout = ClientTimeout(total=600, sock_read=300)
        _session = ClientSession(timeout=timeout, connector=connector)
    return _session


async def on_shutdown(app):
    global _session
    if _session and not _session.closed:
        await _session.close()


# ── 会话日志 ──────────────────────────────────────────

def _extract_user_message(body_json: dict | None) -> str:
    """提取最后一条用户消息。"""
    if not body_json or "messages" not in body_json:
        return ""
    for msg in reversed(body_json["messages"]):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "tool_result":
                    parts.append(f"[tool_result: {str(p.get('content',''))[:200]}]")
                elif p.get("type") == "image":
                    parts.append("[image]")
            return "\n".join(parts)
    return ""


def _extract_message_count(body_json: dict | None) -> int:
    """请求中的消息总数。"""
    if not body_json or "messages" not in body_json:
        return 0
    return len(body_json["messages"])


def _format_content_blocks(blocks: list) -> str:
    """将 content blocks 格式化为可读的 Markdown（完整记录 thinking/text/tool_use）。"""
    parts = []
    for block in blocks:
        btype = block.get("type", "")
        if btype == "thinking":
            text = block.get("thinking", "")
            if text:
                parts.append(f"**🧠 Thinking:**\n\n{text}")
        elif btype == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif btype == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            inp_str = json.dumps(inp, ensure_ascii=False, indent=2)
            parts.append(f"**🔧 Tool Use: `{name}`**\n\n```json\n{inp_str}\n```")
        elif btype == "tool_result":
            content = block.get("content", "")
            if isinstance(content, list):
                content = "\n".join(p.get("text", str(p)) for p in content)
            parts.append(f"**📎 Tool Result:**\n\n{str(content)[:5000]}")
        else:
            parts.append(f"**[{btype}]**: {json.dumps(block, ensure_ascii=False)[:500]}")
    return "\n\n---\n\n".join(parts)


def _parse_json_response(resp_body: bytes) -> tuple[str, str | None, dict | None]:
    """从非流式 JSON 响应提取完整内容（thinking + text + tool_use）。"""
    try:
        d = json.loads(resp_body)
        formatted = _format_content_blocks(d.get("content", []))
        return formatted, d.get("model"), d.get("usage")
    except Exception:
        return resp_body.decode("utf-8", errors="replace")[:2000], None, None


def _parse_sse_chunks(raw: bytes) -> tuple[str, str | None, dict | None]:
    """从 SSE 流重建完整的 content blocks，再格式化。"""
    resp_model = None
    usage = None
    # 按 index 收集每个 content block
    blocks: dict[int, dict] = {}  # index → {type, parts: [str]}
    current_index = -1

    for line in raw.decode("utf-8", errors="replace").split("\n"):
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
            evt = d.get("type", "")

            if evt == "message_start":
                msg = d.get("message", {})
                resp_model = msg.get("model")
                usage = msg.get("usage")

            elif evt == "content_block_start":
                idx = d.get("index", 0)
                cb = d.get("content_block", {})
                blocks[idx] = {
                    "type": cb.get("type", "text"),
                    "name": cb.get("name", ""),  # tool_use name
                    "id": cb.get("id", ""),
                    "parts": [],
                    "json_parts": [],
                }
                current_index = idx

            elif evt == "content_block_delta":
                idx = d.get("index", current_index)
                delta = d.get("delta", {})
                dtype = delta.get("type", "")
                block = blocks.get(idx)
                if not block:
                    continue
                if dtype == "text_delta":
                    block["parts"].append(delta.get("text", ""))
                elif dtype == "thinking_delta":
                    block["parts"].append(delta.get("thinking", ""))
                elif dtype == "input_json_delta":
                    block["json_parts"].append(delta.get("partial_json", ""))

            elif evt == "message_delta":
                u = d.get("usage")
                if u:
                    usage = {**(usage or {}), **u}

        except json.JSONDecodeError:
            pass

    # 重建 content blocks 结构
    content_blocks = []
    for idx in sorted(blocks.keys()):
        b = blocks[idx]
        btype = b["type"]
        text = "".join(b["parts"])
        json_text = "".join(b["json_parts"])

        if btype == "thinking":
            content_blocks.append({"type": "thinking", "thinking": text})
        elif btype == "text":
            content_blocks.append({"type": "text", "text": text})
        elif btype == "tool_use":
            try:
                inp = json.loads(json_text) if json_text else {}
            except json.JSONDecodeError:
                inp = {"_raw": json_text[:2000]}
            content_blocks.append({"type": "tool_use", "name": b["name"], "input": inp})

    formatted = _format_content_blocks(content_blocks)
    return formatted, resp_model, usage


def _get_session_id(request, body_json: dict | None) -> str:
    """从请求头中提取会话 ID，用于日志分文件。"""
    # Claude Code 常用的 session 相关 header
    for h in ("x-session-id", "x-conversation-id", "x-request-id"):
        val = request.headers.get(h)
        if val:
            return val
    # 从 query string 提取
    sid = request.query.get("session_id", "")
    if sid:
        return sid
    # 用 User-Agent 的 hash 兜底
    ua = request.headers.get("User-Agent", "unknown")
    ip = request.remote or "unknown"
    return f"{ip}_{hash(ua) % 0xFFFF:04x}"


def _write_session_log(
    session_id: str,
    model_requested: str,
    model_actual: str | None,
    model_rewrite: str | None,
    route_name: str,
    question: str,
    answer: str,
    usage: dict | None,
    duration: float,
    msg_count: int,
    status_code: int,
    path: str,
):
    """写入会话日志文件（Markdown 格式，按日期+会话分文件）。"""
    LOG_DIR.mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_sid = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:32]
    filepath = LOG_DIR / f"{date_str}_{safe_sid}.md"

    now = datetime.now().strftime("%H:%M:%S")
    display_model = model_actual or model_rewrite or model_requested
    rewrite_note = f" (requested: {model_requested} → {model_rewrite})" if model_rewrite else ""

    usage_str = ""
    if usage:
        parts = []
        if usage.get("input_tokens"):
            parts.append(f"input={usage['input_tokens']}")
        if usage.get("output_tokens"):
            parts.append(f"output={usage['output_tokens']}")
        if usage.get("cache_read_input_tokens"):
            parts.append(f"cache_read={usage['cache_read_input_tokens']}")
        if usage.get("cache_creation_input_tokens"):
            parts.append(f"cache_create={usage['cache_creation_input_tokens']}")
        usage_str = " | ".join(parts)

    # 记录完整内容，不截断

    entry = f"""
---

### {now} | `{display_model}` | {route_name} | {duration:.1f}s | HTTP {status_code}{rewrite_note}

**Messages**: {msg_count} | **Path**: `{path}` | **Tokens**: {usage_str or 'N/A'}

<details><summary>Question</summary>

{question}

</details>

<details><summary>Answer</summary>

{answer}

</details>
"""

    with open(filepath, "a", encoding="utf-8") as f:
        # 首次写入时加文件头
        if f.tell() == 0:
            f.write(f"# Session Log: {date_str} / {session_id}\n\n")
            f.write(f"Created: {datetime.now().isoformat()}\n")
        f.write(entry)


# ── 连接管理 ──────────────────────────────────────────

async def _invalidate_session():
    """关闭并清空全局 session，下次请求会创建新连接。"""
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


# ── 请求处理 ──────────────────────────────────────────

SKIP_HEADERS = frozenset({
    "host", "transfer-encoding", "content-encoding",
    "content-length", "connection", "keep-alive",
})

# 不记录日志的路径（非对话请求）
SKIP_LOG_PATHS = {"/v1/messages/count_tokens"}


async def handle(request: web.Request) -> web.StreamResponse:
    t0 = time.monotonic()
    config = load_config()

    if not check_auth(request, config):
        return web.json_response(
            {"error": {"type": "auth_error", "message": "Invalid API key"}},
            status=401,
        )

    body = await request.read()

    model = "unknown"
    is_stream = False
    body_json = None
    try:
        body_json = json.loads(body)
        model = body_json.get("model", "unknown")
        is_stream = body_json.get("stream", False)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    route = find_route(model, config)

    # 模型名改写
    model_rewrite = route.get("model_map", {}).get(model)
    if model_rewrite and body_json is not None:
        body_json["model"] = model_rewrite
        body = json.dumps(body_json).encode()
        log.info("  rewrite: %s → %s", model, model_rewrite)

    target_url = route["base_url"].rstrip("/") + request.path

    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in SKIP_HEADERS:
            headers[k] = v
    if route.get("api_key"):
        headers["x-api-key"] = route["api_key"]
        headers["Authorization"] = f"Bearer {route['api_key']}"

    log.info("→ %s | model=%s | %s %s", route["name"], model, request.method, request.path)

    # 提取日志信息
    should_log = request.path not in SKIP_LOG_PATHS and request.method == "POST" and body_json
    question = _extract_user_message(body_json) if should_log else ""
    msg_count = _extract_message_count(body_json) if should_log else 0
    session_id = _get_session_id(request, body_json) if should_log else ""

    if is_stream:
        return await _handle_stream_with_retry(
            request, body, headers, target_url, route, model, model_rewrite,
            should_log, question, msg_count, session_id, t0,
        )
    else:
        return await _handle_non_stream(
            request, body, headers, target_url, route, model, model_rewrite,
            should_log, question, msg_count, session_id, t0,
        )


async def _handle_stream_with_retry(
    request, body, headers, target_url, route, model, model_rewrite,
    should_log, question, msg_count, session_id, t0,
) -> web.StreamResponse:
    """流式请求：原生边收边转发，失败时销毁连接池让客户端重试拿新连接。"""

    try:
        session = await get_session()
        async with session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=body,
        ) as upstream:

            resp_headers = {
                k: v for k, v in upstream.headers.items()
                if k.lower() not in SKIP_HEADERS
            }

            response = web.StreamResponse(status=upstream.status, headers=resp_headers)
            await response.prepare(request)

            # 原生流式：边收边转发
            chunks = bytearray()
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
                if should_log:
                    chunks.extend(chunk)

            await response.write_eof()
            duration = time.monotonic() - t0
            log.info("← %s | %d | stream %d B | %.1fs",
                     route["name"], upstream.status, len(chunks), duration)

            # 写会话日志
            if should_log and upstream.status == 200:
                answer, resp_model, usage = _parse_sse_chunks(bytes(chunks))
                _write_session_log(
                    session_id=session_id, model_requested=model,
                    model_actual=resp_model, model_rewrite=model_rewrite,
                    route_name=route["name"], question=question, answer=answer,
                    usage=usage, duration=duration, msg_count=msg_count,
                    status_code=upstream.status, path=request.path_qs,
                )

            return response

    except Exception as e:
        duration = time.monotonic() - t0
        log.error("✗ %s | %s | %.1fs | %s | 销毁连接池",
                  route["name"], model, duration, e)
        # 关键：销毁连接池，下次重试会拿全新连接
        await _invalidate_session()
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
        )


async def _handle_non_stream(
    request, body, headers, target_url, route, model, model_rewrite,
    should_log, question, msg_count, session_id, t0,
) -> web.StreamResponse:
    """非流式请求：直接透传，无重试。"""

    try:
        session = await get_session()
        async with session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=body,
        ) as upstream:

            resp_body = await upstream.read()
            resp_headers = {
                k: v for k, v in upstream.headers.items()
                if k.lower() not in SKIP_HEADERS
            }

            duration = time.monotonic() - t0
            log.info("← %s | %d | %d B | %.1fs",
                     route["name"], upstream.status, len(resp_body), duration)

            # 写会话日志
            if should_log and upstream.status == 200:
                answer, resp_model, usage = _parse_json_response(resp_body)
                _write_session_log(
                    session_id=session_id, model_requested=model,
                    model_actual=resp_model, model_rewrite=model_rewrite,
                    route_name=route["name"], question=question, answer=answer,
                    usage=usage, duration=duration, msg_count=msg_count,
                    status_code=upstream.status, path=request.path_qs,
                )

            return web.Response(status=upstream.status, body=resp_body, headers=resp_headers)

    except Exception as e:
        duration = time.monotonic() - t0
        log.error("✗ %s | %s | %.1fs | %s", route["name"], model, duration, e)
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
        )


# ── 启动 ──────────────────────────────────────────────

app = web.Application()
app.router.add_route("*", "/{path:.*}", handle)
app.on_shutdown.append(on_shutdown)


def main():
    config = load_config()
    port = config.get("port", 8462)

    LOG_DIR.mkdir(exist_ok=True)

    print(f"\n  Smart API Router · http://0.0.0.0:{port}")
    print(f"  认证: {'开启' if config.get('auth_tokens') else '关闭'}")
    print(f"  日志: {LOG_DIR}\n")
    for r in config["routes"]:
        maps = r.get("model_map", {})
        map_str = f" (rewrite: {', '.join(f'{k}→{v}' for k, v in maps.items())})" if maps else ""
        print(f"    {', '.join(r['patterns']):20s} → {r['name']}{map_str}")
    print(f"\n    (default) → {config['routes'][config.get('default_route', 0)]['name']}\n")

    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
