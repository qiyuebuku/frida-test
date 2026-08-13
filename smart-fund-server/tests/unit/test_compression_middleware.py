from __future__ import annotations

import gzip

import httpx
import pytest
import zstandard
from fastapi import FastAPI

from src.interfaces.api.middleware.compression import CompressionMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CompressionMiddleware, minimum_size=32)

    @app.get("/large")
    async def large() -> dict[str, str]:
        return {"payload": "x" * 4096}

    @app.get("/small")
    async def small() -> dict[str, str]:
        return {"payload": "ok"}

    return app


async def _raw_request(encoding: str, path: str = "/large") -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = client.build_request("GET", path, headers={"accept-encoding": encoding})
        return await client.send(request, stream=True)


@pytest.mark.asyncio
async def test_prefers_zstd_and_round_trips() -> None:
    response = await _raw_request("gzip, zstd")
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
    assert response.headers["content-encoding"] == "zstd"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert b'"payload"' in zstandard.ZstdDecompressor().decompress(
        raw,
        max_output_size=8192,
    )


@pytest.mark.asyncio
async def test_falls_back_to_gzip() -> None:
    response = await _raw_request("gzip")
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
    assert response.headers["content-encoding"] == "gzip"
    assert b'"payload"' in gzip.decompress(raw)


@pytest.mark.asyncio
async def test_skips_small_responses() -> None:
    response = await _raw_request("zstd", "/small")
    assert "content-encoding" not in response.headers
