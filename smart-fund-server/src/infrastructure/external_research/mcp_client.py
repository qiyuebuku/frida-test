"""Small Streamable HTTP MCP client used by provider adapters."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


class RemoteMcpToolError(RuntimeError):
    """Raised when an upstream MCP server rejects or cannot execute a tool."""


class RemoteMcpToolClient:
    def __init__(
        self,
        *,
        url: str,
        bearer_token: str,
        timeout_seconds: float,
    ) -> None:
        self._url = url
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if not self._bearer_token:
            raise RemoteMcpToolError(
                "external research provider API key is not configured"
            )
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
        }
        timeout = httpx.Timeout(self._timeout_seconds)
        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
                trust_env=True,
            ) as http_client:
                async with streamable_http_client(
                    self._url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(
                            seconds=self._timeout_seconds
                        ),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise RemoteMcpToolError(
                f"upstream MCP request failed: {exc}"
            ) from exc

        if result.isError:
            message = _text_content(result.content) or "unknown upstream error"
            raise RemoteMcpToolError(
                f"upstream MCP tool {tool_name} failed: {message}"
            )
        if result.structuredContent is not None:
            return result.structuredContent
        return _decode_nested_json(_text_content(result.content))


def _text_content(content: list[Any]) -> str:
    return "\n".join(
        item.text
        for item in content
        if isinstance(item, TextContent) and item.text
    ).strip()


def _decode_nested_json(value: Any) -> Any:
    decoded = value
    for _ in range(3):
        if not isinstance(decoded, str):
            break
        normalized = decoded.strip()
        if not normalized:
            return ""
        try:
            decoded = json.loads(normalized)
        except json.JSONDecodeError:
            break
    return decoded
