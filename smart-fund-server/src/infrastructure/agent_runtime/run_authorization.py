"""Short-lived signed authorization for one financial Agent run."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable


TOKEN_VERSION = "v1"
RUN_MODES = frozenset({"production", "shadow", "replay", "debug"})
MAX_CLOCK_SKEW = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class RunAuthorizationClaims:
    run_id: str
    role: str
    task: str
    cutoff_at: datetime
    expires_at: datetime
    tools: frozenset[str]
    run_mode: str
    account_ids: tuple[str, ...] = ()

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.tools


def issue_run_authorization(
    *,
    secret: str,
    run_id: str,
    role: str,
    task: str,
    cutoff_at: datetime,
    tools: Iterable[str],
    run_mode: str,
    ttl_seconds: int,
    account_ids: Iterable[str] = (),
    now: datetime | None = None,
) -> str:
    if not secret:
        raise ValueError("run authorization secret is required")
    cutoff = _aware_utc(cutoff_at, "cutoff_at")
    issued_at = _aware_utc(now or datetime.now(UTC), "now")
    expires_at = issued_at + timedelta(seconds=max(30, int(ttl_seconds)))
    payload = {
        "run_id": _required(run_id, "run_id"),
        "role": _required(role, "role"),
        "task": _required(task, "task"),
        "cutoff_at": cutoff.isoformat(),
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "tools": sorted({_required(item, "tool") for item in tools}),
        "run_mode": _required(run_mode, "run_mode"),
        "account_ids": sorted(
            {_required(item, "account_id") for item in account_ids}
        ),
    }
    encoded = _b64encode(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _sign(secret, f"{TOKEN_VERSION}.{encoded}".encode("ascii"))
    return f"{TOKEN_VERSION}.{encoded}.{signature}"


def verify_run_authorization(
    token: str,
    *,
    secret: str,
    tool_name: str,
    expected_role: str,
    expected_task: str | None = None,
    now: datetime | None = None,
) -> RunAuthorizationClaims:
    if not secret:
        raise ValueError("run authorization secret is required")
    parts = str(token or "").split(".")
    if len(parts) != 3 or parts[0] != TOKEN_VERSION:
        raise ValueError("invalid run authorization format")
    version, encoded, supplied_signature = parts
    expected_signature = _sign(
        secret,
        f"{version}.{encoded}".encode("ascii"),
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("invalid run authorization signature")
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid run authorization payload") from exc

    claims = RunAuthorizationClaims(
        run_id=_required(payload.get("run_id"), "run_id"),
        role=_required(payload.get("role"), "role"),
        task=_required(payload.get("task"), "task"),
        cutoff_at=_parse_datetime(payload.get("cutoff_at"), "cutoff_at"),
        expires_at=_parse_datetime(payload.get("expires_at"), "expires_at"),
        tools=frozenset(
            _required(item, "tool") for item in payload.get("tools") or []
        ),
        run_mode=_required(payload.get("run_mode"), "run_mode"),
        account_ids=tuple(
            _required(item, "account_id")
            for item in payload.get("account_ids") or []
        ),
    )
    current = _aware_utc(now or datetime.now(UTC), "now")
    if claims.expires_at <= current:
        raise ValueError("run authorization expired")
    if claims.cutoff_at > current + MAX_CLOCK_SKEW:
        raise ValueError("run authorization cutoff_at is in the future")
    if claims.role != expected_role:
        raise ValueError("run authorization role is not allowed")
    if expected_task is not None and claims.task != expected_task:
        raise ValueError("run authorization task is not allowed")
    if claims.run_mode not in RUN_MODES:
        raise ValueError("run authorization run_mode is not allowed")
    if not claims.allows(tool_name):
        raise ValueError(f"tool is not authorized for this run: {tool_name}")
    return claims


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"run authorization {name} is required")
    return normalized


def _parse_datetime(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid run authorization {name}") from exc
    return _aware_utc(parsed, name)


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"run authorization {name} must include a timezone")
    return value.astimezone(UTC)


def _sign(secret: str, value: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), value, hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
