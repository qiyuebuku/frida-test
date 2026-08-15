"""Stable, reversible locators for persisted market evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Mapping


LOCATOR_PREFIX = "market:v1:"


@dataclass(frozen=True, slots=True)
class MarketEvidenceIdentity:
    kind: str
    domain: str
    identity: dict[str, Any]
    data_type: str | None = None
    subject_id: str | None = None
    provider: str | None = None
    fact_time: str | None = None
    version: str | None = None
    field: str | None = None


def encode_market_evidence_locator(
    identity: MarketEvidenceIdentity,
) -> str:
    payload = {
        "kind": identity.kind,
        "domain": identity.domain,
        "identity": identity.identity,
        "data_type": identity.data_type,
        "subject_id": identity.subject_id,
        "provider": identity.provider,
        "fact_time": identity.fact_time,
        "version": identity.version,
        "field": identity.field,
    }
    compact = {
        key: _json_value(value)
        for key, value in payload.items()
        if value not in (None, "", {}, [])
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            compact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return LOCATOR_PREFIX + encoded


def decode_market_evidence_locator(locator: str) -> MarketEvidenceIdentity:
    value = str(locator or "")
    if not value.startswith(LOCATOR_PREFIX):
        raise ValueError("unsupported market evidence locator")
    encoded = value[len(LOCATOR_PREFIX) :]
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid market evidence locator") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("invalid market evidence locator payload")
    kind = _required(payload.get("kind"), "kind")
    domain = _required(payload.get("domain"), "domain")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("market evidence identity is required")
    return MarketEvidenceIdentity(
        kind=kind,
        domain=domain,
        identity={str(key): value for key, value in identity.items()},
        data_type=_optional(payload.get("data_type")),
        subject_id=_optional(payload.get("subject_id")),
        provider=_optional(payload.get("provider")),
        fact_time=_optional(payload.get("fact_time")),
        version=_optional(payload.get("version")),
        field=_optional(payload.get("field")),
    )


def normalize_market_evidence_locator(locator: str) -> str:
    """Return the one canonical spelling for a semantic market identity.

    URL-safe Base64 padding is optional by definition.  Some compatible model
    providers append ``=`` while copying a locator, so evidence validation must
    compare the decoded identity rather than reject that harmless spelling
    difference.  Invalid or non-market references still fail at their caller's
    normal membership check.
    """

    return encode_market_evidence_locator(
        decode_market_evidence_locator(locator)
    )


def with_evidence_field(locator: str, field: str) -> str:
    identity = decode_market_evidence_locator(locator)
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind=identity.kind,
            domain=identity.domain,
            identity=identity.identity,
            data_type=identity.data_type,
            subject_id=identity.subject_id,
            provider=identity.provider,
            fact_time=identity.fact_time,
            version=identity.version,
            field=_required(field, "field"),
        )
    )


def historical_analogue_evidence_locator(value: Mapping[str, Any]) -> str:
    """Identify one deterministic historical-analogue aggregate."""

    material = {
        key: value.get(key)
        for key in (
            "subject_id",
            "benchmark_subject_id",
            "signal_definition",
            "forward_window_bars",
            "sample_count",
            "statistics",
            "robustness",
        )
    }
    digest = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="calculation",
            domain="market_historical_analogue",
            identity={"sha256": digest},
            data_type=str(value.get("data_type") or "historical_analogue"),
            subject_id=str(value.get("subject_id") or "") or None,
            provider="smart_fund_deterministic",
            version="v1",
        )
    )


def technical_state_evidence_locator(value: Mapping[str, Any]) -> str:
    """Identify one deterministic technical-state aggregate."""

    material = {
        key: value.get(key)
        for key in (
            "subject_id",
            "benchmark_subject_id",
            "latest_trade_date",
            "latest_close",
            "windows",
            "recent_swing",
            "peak_drawdown_pct",
            "relative_strength",
            "volume_confirmation",
        )
    }
    digest = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="calculation",
            domain="market_technical_state",
            identity={"sha256": digest},
            data_type=str(value.get("data_type") or "technical_state"),
            subject_id=str(value.get("subject_id") or "") or None,
            provider="smart_fund_deterministic",
            fact_time=str(value.get("latest_trade_date") or "") or None,
            version="v1",
        )
    )


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"market evidence {name} is required")
    return normalized


def _optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market evidence datetime must include a timezone")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
