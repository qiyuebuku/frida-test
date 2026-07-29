"""Small Langfuse tracing helpers shared by application flows."""

from __future__ import annotations

import logging
import os
import re
from contextlib import nullcontext
from hashlib import sha256
from typing import Any

from src.infrastructure.config import settings  # noqa: F401  # loads .env before Langfuse imports

logger = logging.getLogger(__name__)


def langfuse_enabled() -> bool:
    return os.getenv("KG_LANGFUSE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def langfuse_client_or_none() -> Any | None:
    if not langfuse_enabled():
        return None
    try:
        from langfuse import get_client
    except Exception as exc:
        logger.warning("Langfuse client unavailable: %s", exc)
        return None
    try:
        return get_client()
    except Exception as exc:
        logger.warning("Langfuse client init failed: %s", exc)
        return None


def langfuse_propagation_context(
    *,
    trace_name: str,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
):
    if not langfuse_enabled():
        return nullcontext()
    try:
        from langfuse import propagate_attributes
    except Exception as exc:
        logger.warning("Langfuse propagate_attributes unavailable: %s", exc)
        return nullcontext()
    try:
        attributes = {
            "session_id": normalize_langfuse_session_id(session_id or _env_session_id()),
            "tags": langfuse_tags(tags),
            "metadata": _attribute_metadata(metadata or {}),
            "version": version or _env_version(),
        }
        if not _has_active_span():
            attributes["trace_name"] = trace_name
        return propagate_attributes(**attributes)
    except Exception as exc:
        logger.warning("Langfuse propagation context failed: %s", exc)
        return nullcontext()


def langfuse_observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any | None = None,
    output: Any | None = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    usage_details: dict[str, int] | None = None,
    level: str | None = None,
    status_message: str | None = None,
    version: str | None = None,
):
    client = langfuse_client_or_none()
    if client is None:
        return nullcontext()
    try:
        return client.start_as_current_observation(
            name=name,
            as_type=as_type,  # type: ignore[arg-type]
            input=input,
            output=output,
            metadata=metadata or {},
            model=model,
            model_parameters=model_parameters,
            usage_details=usage_details,
            level=level,  # type: ignore[arg-type]
            status_message=status_message,
            version=version or _env_version(),
        )
    except Exception as exc:
        logger.warning("Langfuse observation start failed name=%s: %s", name, exc)
        return nullcontext()


def langfuse_update_span(
    *,
    output: Any | None = None,
    metadata: dict[str, Any] | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    client = langfuse_client_or_none()
    if client is None:
        return
    try:
        client.update_current_span(
            output=output,
            metadata=metadata,
            level=level,  # type: ignore[arg-type]
            status_message=status_message,
        )
    except Exception as exc:
        logger.warning("Langfuse span update failed: %s", exc)


def langfuse_update_generation(
    *,
    output: Any | None = None,
    metadata: dict[str, Any] | None = None,
    usage_details: dict[str, int] | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    client = langfuse_client_or_none()
    if client is None:
        return
    try:
        client.update_current_generation(
            output=output,
            metadata=metadata,
            usage_details=usage_details,
            level=level,  # type: ignore[arg-type]
            status_message=status_message,
        )
    except Exception as exc:
        logger.warning("Langfuse generation update failed: %s", exc)


def langfuse_flush() -> None:
    client = langfuse_client_or_none()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed: %s", exc)


def langfuse_tags(extra: list[str] | None = None) -> list[str]:
    raw = os.getenv("KG_LANGFUSE_TAGS", "").strip()
    tags = [item.strip() for item in raw.split(",") if item.strip()]
    return _ordered_unique([*tags, *(extra or [])])


def normalize_langfuse_session_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(char if 32 <= ord(char) <= 126 else "-" for char in value)
    normalized = re.sub(r"\s+", "-", normalized).strip("-")
    if not normalized:
        return None
    return normalized[:199]


def clip_trace_text(value: Any, *, limit: int | None = None) -> str:
    raw = "" if value is None else str(value)
    max_chars = limit if limit is not None else int(os.getenv("KG_LANGFUSE_MAX_TEXT_CHARS", "1000000"))
    if max_chars <= 0 or len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "...[truncated]"


def _env_session_id() -> str:
    return os.getenv("KG_LANGFUSE_SESSION_ID", "").strip() or os.getenv("LANGFUSE_SESSION_ID", "").strip()


def _env_version() -> str | None:
    return os.getenv("KG_LANGFUSE_VERSION", "").strip() or None


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _attribute_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, str):
            result[key] = _clip_propagated_attribute_value(value)
            continue
        try:
            serialized = json_dumps_compact(value)
        except Exception:
            serialized = str(value)
        result[key] = _clip_propagated_attribute_value(serialized)
    return result


def _clip_propagated_attribute_value(value: str) -> str:
    max_chars = int(os.getenv("KG_LANGFUSE_PROPAGATED_ATTRIBUTE_MAX_CHARS", "190"))
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    suffix = f"...[truncated sha256={digest}]"
    if max_chars <= len(suffix):
        return suffix[-max_chars:]
    return value[: max_chars - len(suffix)] + suffix


def json_dumps_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _has_active_span() -> bool:
    try:
        from opentelemetry import trace

        return bool(trace.get_current_span().get_span_context().is_valid)
    except Exception:
        return False
