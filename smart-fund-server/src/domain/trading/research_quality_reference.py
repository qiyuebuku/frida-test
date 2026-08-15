"""Compact, reversible model-facing references for Research quality records."""

from __future__ import annotations

import base64
from uuid import UUID


_RUN_ID_PREFIX = "research-run-"
_QUALITY_REF_PREFIX = "Q_"


def quality_ref_from_run_id(run_id: str) -> str:
    """Encode a Research run UUID without exposing its storage identifier."""

    value = str(run_id)
    if not value.startswith(_RUN_ID_PREFIX):
        raise ValueError("quality evaluation has an unsupported run identifier")
    run_uuid = UUID(hex=value.removeprefix(_RUN_ID_PREFIX))
    token = base64.urlsafe_b64encode(run_uuid.bytes).decode("ascii").rstrip("=")
    return f"{_QUALITY_REF_PREFIX}{token}"


def run_id_from_quality_ref(quality_ref: str) -> str:
    """Decode a model-facing quality reference to the owning Research run."""

    value = str(quality_ref).strip()
    if not value.startswith(_QUALITY_REF_PREFIX):
        raise ValueError("quality_ref must use the Q_<token> format")
    token = value.removeprefix(_QUALITY_REF_PREFIX)
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4),
            altchars=b"-_",
            validate=True,
        )
        run_uuid = UUID(bytes=raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("quality_ref is invalid") from exc
    return f"{_RUN_ID_PREFIX}{run_uuid.hex}"
