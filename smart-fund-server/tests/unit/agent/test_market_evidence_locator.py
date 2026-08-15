from __future__ import annotations

import pytest

from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    decode_market_evidence_locator,
    encode_market_evidence_locator,
    normalize_market_evidence_locator,
    technical_state_evidence_locator,
    with_evidence_field,
)
from src.application.services.agent_research_commit_service import (
    _normalized_evidence_reference,
)


def test_market_evidence_locator_round_trips_complete_record_identity() -> None:
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 123},
            data_type="ths_cn_market_breadth",
            subject_id="cn:a_share:ths_breadth",
            provider="ths",
            fact_time="2026-08-09T07:00:00+00:00",
            version="payload-hash",
        )
    )

    decoded = decode_market_evidence_locator(locator)

    assert decoded.identity == {"id": 123}
    assert decoded.data_type == "ths_cn_market_breadth"
    assert decoded.subject_id == "cn:a_share:ths_breadth"
    assert decoded.fact_time == "2026-08-09T07:00:00+00:00"


def test_market_evidence_field_locator_cannot_change_record_identity() -> None:
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="domain",
            domain="sentiment_signal",
            identity={"snapshot_date": "2026-08-09"},
        )
    )

    field_locator = with_evidence_field(locator, "market_temperature")
    decoded = decode_market_evidence_locator(field_locator)

    assert decoded.identity == {"snapshot_date": "2026-08-09"}
    assert decoded.field == "market_temperature"


def test_market_evidence_locator_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        decode_market_evidence_locator("market:old:123")


def test_market_evidence_locator_normalizes_optional_base64_padding() -> None:
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 123},
        )
    )

    assert normalize_market_evidence_locator(locator + "==") == locator
    assert _normalized_evidence_reference(locator + "==") == locator


def test_technical_state_locator_carries_latest_trade_date() -> None:
    locator = technical_state_evidence_locator({
        "subject_id": "ths:industry:881129",
        "data_type": "ths_sector_daily",
        "latest_trade_date": "2026-08-14",
        "latest_close": 100.0,
        "volume_confirmation": {"latest_to_prior_median_ratio": 1.2},
    })

    identity = decode_market_evidence_locator(locator)
    assert identity.domain == "market_technical_state"
    assert identity.fact_time == "2026-08-14"
