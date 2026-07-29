"""Redis-backed temporary storage for large provider responses."""

from __future__ import annotations

import json
from uuid import uuid4

import redis.asyncio as redis

from src.domain.external_research.models import ExternalContent


class RedisExternalContentStore:
    _KEY_PREFIX = "smart_fund:external_content:"

    def __init__(self, *, redis_url: str, ttl_seconds: int) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds

    async def save(self, content: ExternalContent, *, provider: str) -> str:
        handle = f"external_content:{uuid4().hex}"
        payload = {
            "provider": provider,
            "content": content.content,
            "title": content.title,
            "url": content.url,
            "media_type": content.media_type,
            "metadata": content.metadata,
        }
        client = redis.from_url(self._redis_url, decode_responses=True)
        try:
            await client.set(
                self._redis_key(handle),
                json.dumps(payload, ensure_ascii=False),
                ex=self._ttl_seconds,
            )
        finally:
            await client.aclose()
        return handle

    async def load(self, handle: str) -> tuple[ExternalContent, str] | None:
        if not handle.startswith("external_content:"):
            return None
        client = redis.from_url(self._redis_url, decode_responses=True)
        try:
            raw_payload = await client.get(self._redis_key(handle))
        finally:
            await client.aclose()
        if not raw_payload:
            return None
        payload = json.loads(raw_payload)
        return (
            ExternalContent(
                content=str(payload.get("content") or ""),
                title=str(payload.get("title") or ""),
                url=str(payload.get("url") or ""),
                media_type=str(payload.get("media_type") or "text/plain"),
                metadata=dict(payload.get("metadata") or {}),
            ),
            str(payload.get("provider") or ""),
        )

    @classmethod
    def _redis_key(cls, handle: str) -> str:
        identifier = handle.removeprefix("external_content:")
        return f"{cls._KEY_PREFIX}{identifier}"
