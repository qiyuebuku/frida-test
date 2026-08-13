from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.infrastructure.agent_runtime.config import AgentSettings
from src.infrastructure.agent_runtime.langfuse_health import (
    check_langfuse_health,
)


def _settings(*, enabled: bool = True) -> AgentSettings:
    return AgentSettings.from_mapping(
        {
            "SMART_FUND_MCP_URL": "http://127.0.0.1:8900/mcp",
            "SMART_FUND_MCP_BEARER_TOKEN": "test-token",
            "SMART_FUND_AGENT_LLM_BASE_URL": "http://127.0.0.1:3000/v1",
            "SMART_FUND_AGENT_LLM_API_KEY": "test-key",
            "SMART_FUND_AGENT_MODEL": "test-model",
            "SMART_FUND_AGENT_LANGFUSE_ENABLED": str(enabled).lower(),
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_BASE_URL": "http://langfuse.test",
        },
        project_root=Path("/tmp/smart-fund-test"),
    )


@pytest.mark.asyncio
async def test_langfuse_health_reports_real_otlp_write() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": []})
        assert request.url.path == "/api/public/otel/v1/traces"
        assert request.headers["x-langfuse-ingestion-version"] == "4"
        payload = json.loads(request.content)
        resource = payload["resourceSpans"][0]
        attributes = {
            item["key"]: item["value"] for item in resource["resource"]["attributes"]
        }
        assert attributes["deployment.environment.name"]["stringValue"] == (
            "health-check"
        )
        span = resource["scopeSpans"][0]["spans"][0]
        span_attributes = {item["key"]: item["value"] for item in span["attributes"]}
        assert span_attributes["langfuse.health_check"]["boolValue"] is True
        assert span_attributes["langfuse.trace.tags"]["arrayValue"]["values"] == [
            {"stringValue": "system-health-check"}
        ]
        return httpx.Response(200, json={})

    result = await check_langfuse_health(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result.configured is True
    assert result.authenticated is True
    assert result.writable is True
    assert result.status == "writable"
    assert result.write_http_status == 200


@pytest.mark.asyncio
async def test_langfuse_health_distinguishes_suspended_quota() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            403,
            json={
                "message": "Ingestion suspended: Usage threshold exceeded.",
                "error": "ForbiddenError",
            },
        )

    result = await check_langfuse_health(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result.configured is True
    assert result.authenticated is True
    assert result.writable is False
    assert result.status == "suspended_quota"
    assert result.write_http_status == 403


@pytest.mark.asyncio
async def test_langfuse_health_reports_disabled_without_network() -> None:
    result = await check_langfuse_health(_settings(enabled=False))

    assert result.configured is False
    assert result.authenticated is False
    assert result.writable is False
    assert result.status == "disabled"
