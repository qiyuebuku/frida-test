import asyncio

import pytest

from src.infrastructure.clients.xueqiu import XueqiuClient


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _SpyHttpClient:
    def __init__(self):
        self.visited = False
        self.goto_calls = 0

    async def get(self, *_args, **_kwargs) -> _Response:
        if not self.visited:
            return _Response({"cookies": []})
        return _Response(
            {
                "cookies": [
                    {
                        "name": "xq_a_token",
                        "value": "token-value",
                        "domain": ".xueqiu.com",
                    }
                ]
            }
        )

    async def post(self, *_args, **_kwargs) -> _Response:
        self.goto_calls += 1
        await asyncio.sleep(0.01)
        self.visited = True
        return _Response({"status": "ok"})


@pytest.mark.asyncio
async def test_concurrent_cookie_initialization_visits_spy_once() -> None:
    client = XueqiuClient()
    spy_client = _SpyHttpClient()
    original_client = client._client
    client._client = spy_client
    await original_client.aclose()

    cookies = await asyncio.gather(
        client._ensure_cookies(),
        client._ensure_cookies(),
        client._ensure_cookies(),
    )

    assert cookies == [
        "xq_a_token=token-value",
        "xq_a_token=token-value",
        "xq_a_token=token-value",
    ]
    assert spy_client.goto_calls == 1
