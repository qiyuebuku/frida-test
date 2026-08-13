"""HTTP response compression with Zstandard and gzip negotiation."""

from __future__ import annotations

import zlib
from collections.abc import Awaitable, Callable
from typing import Any

import zstandard
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send


_ALREADY_COMPRESSED_PREFIXES = (
    "audio/",
    "font/",
    "image/",
    "video/",
)
_ALREADY_COMPRESSED_TYPES = {
    "application/gzip",
    "application/pdf",
    "application/wasm",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-gzip",
    "application/x-rar-compressed",
    "application/zip",
}


def _accepted_encodings(value: str) -> dict[str, float]:
    accepted: dict[str, float] = {}
    for item in value.lower().split(","):
        parts = [part.strip() for part in item.split(";")]
        encoding = parts[0]
        if not encoding:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = max(0.0, min(1.0, float(parameter[2:])))
                except ValueError:
                    quality = 0.0
        accepted[encoding] = quality
    return accepted


def _select_encoding(value: str) -> str | None:
    accepted = _accepted_encodings(value)
    wildcard = accepted.get("*", 0.0)
    candidates = (
        ("zstd", accepted.get("zstd", wildcard)),
        ("gzip", accepted.get("gzip", wildcard)),
    )
    encoding, quality = max(candidates, key=lambda item: (item[1], item[0] == "zstd"))
    return encoding if quality > 0 else None


def _append_vary(headers: MutableHeaders, value: str) -> None:
    current = headers.get("vary", "")
    values = {item.strip().lower() for item in current.split(",") if item.strip()}
    if value.lower() not in values:
        headers["vary"] = f"{current}, {value}" if current else value


def _is_compressible(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/event-stream":
        return False
    if media_type in _ALREADY_COMPRESSED_TYPES:
        return False
    return not media_type.startswith(_ALREADY_COMPRESSED_PREFIXES)


class CompressionMiddleware:
    """Compress eligible HTTP responses, preferring zstd over gzip."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        minimum_size: int = 1024,
        zstd_level: int = 3,
        gzip_level: int = 6,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.zstd_level = zstd_level
        self.gzip_level = gzip_level

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "HEAD":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        encoding = _select_encoding(request_headers.get("accept-encoding", ""))
        if encoding is None:
            await self.app(scope, receive, send)
            return

        compressor: Any = None
        compress_response = False

        async def send_compressed(message: Message) -> None:
            nonlocal compressor, compress_response

            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "")
                content_length = headers.get("content-length")
                cache_control = headers.get("cache-control", "").lower()
                try:
                    size_is_eligible = content_length is None or int(content_length) >= self.minimum_size
                except ValueError:
                    size_is_eligible = True
                compress_response = (
                    200 <= status < 300
                    and status not in {204, 206}
                    and "content-encoding" not in headers
                    and "no-transform" not in cache_control
                    and size_is_eligible
                    and _is_compressible(content_type)
                )
                if compress_response:
                    del headers["content-length"]
                    if "etag" in headers:
                        del headers["etag"]
                    headers["content-encoding"] = encoding
                    _append_vary(headers, "Accept-Encoding")
                    compressor = (
                        zstandard.ZstdCompressor(level=self.zstd_level).compressobj()
                        if encoding == "zstd"
                        else zlib.compressobj(
                            self.gzip_level,
                            zlib.DEFLATED,
                            wbits=16 + zlib.MAX_WBITS,
                        )
                    )
                await send(message)
                return

            if message["type"] != "http.response.body" or not compress_response:
                await send(message)
                return

            body = message.get("body", b"")
            more_body = bool(message.get("more_body", False))
            if encoding == "zstd":
                compressed = compressor.compress(body)
                if not more_body:
                    compressed += compressor.flush(zstandard.COMPRESSOBJ_FLUSH_FINISH)
            else:
                compressed = compressor.compress(body)
                if not more_body:
                    compressed += compressor.flush()
            await send(
                {
                    "type": "http.response.body",
                    "body": compressed,
                    "more_body": more_body,
                }
            )

        await self.app(scope, receive, send_compressed)
