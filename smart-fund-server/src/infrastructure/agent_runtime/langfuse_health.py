"""Deterministic Langfuse authentication and OTLP write health checks."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from src.infrastructure.agent_runtime.config import AgentSettings


@dataclass(frozen=True, slots=True)
class LangfuseHealth:
    configured: bool
    authenticated: bool
    writable: bool
    status: str
    auth_http_status: int | None = None
    write_http_status: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _otel_payload() -> dict[str, object]:
    start_ns = time.time_ns()
    end_ns = start_ns + 1_000_000
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "smart-fund-agent-check"},
                        },
                        {
                            "key": "deployment.environment.name",
                            "value": {"stringValue": "health-check"},
                        },
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "smart-fund.langfuse.health"},
                        "spans": [
                            {
                                "traceId": secrets.token_hex(16),
                                "spanId": secrets.token_hex(8),
                                "name": "smart-fund.langfuse.write-health",
                                "kind": 1,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "attributes": [
                                    {
                                        "key": "langfuse.health_check",
                                        "value": {"boolValue": True},
                                    },
                                    {
                                        "key": "langfuse.trace.tags",
                                        "value": {
                                            "arrayValue": {
                                                "values": [
                                                    {
                                                        "stringValue": (
                                                            "system-health-check"
                                                        )
                                                    }
                                                ]
                                            }
                                        },
                                    }
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _response_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)[:500]
    return str(body)[:500]


async def check_langfuse_health(
    settings: AgentSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> LangfuseHealth:
    """Distinguish configuration, authentication and actual OTLP writability."""

    if not settings.langfuse_enabled:
        return LangfuseHealth(False, False, False, "disabled")
    if not settings.langfuse_configured:
        return LangfuseHealth(False, False, False, "unconfigured")

    auth = httpx.BasicAuth(
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
    )
    try:
        async with httpx.AsyncClient(
            auth=auth,
            timeout=timeout,
            transport=transport,
        ) as client:
            auth_response = await client.get(
                f"{settings.langfuse_base_url}/api/public/projects"
            )
            if auth_response.status_code != 200:
                status = (
                    "authentication_failed"
                    if auth_response.status_code in {401, 403}
                    else "unavailable"
                )
                return LangfuseHealth(
                    True,
                    False,
                    False,
                    status,
                    auth_http_status=auth_response.status_code,
                    error=_response_text(auth_response),
                )

            write_response = await client.post(
                f"{settings.langfuse_base_url}/api/public/otel/v1/traces",
                headers={
                    "Content-Type": "application/json",
                    "x-langfuse-ingestion-version": "4",
                },
                json=_otel_payload(),
            )
    except httpx.HTTPError as exc:
        return LangfuseHealth(
            True,
            False,
            False,
            "unavailable",
            error=f"{type(exc).__name__}: {exc}",
        )

    if 200 <= write_response.status_code < 300:
        return LangfuseHealth(
            True,
            True,
            True,
            "writable",
            auth_http_status=auth_response.status_code,
            write_http_status=write_response.status_code,
        )

    error = _response_text(write_response)
    normalized = error.lower()
    suspended = write_response.status_code == 403 and any(
        marker in normalized
        for marker in ("usage threshold", "quota", "ingestion suspended")
    )
    return LangfuseHealth(
        True,
        True,
        False,
        "suspended_quota" if suspended else "write_failed",
        auth_http_status=auth_response.status_code,
        write_http_status=write_response.status_code,
        error=error,
    )
