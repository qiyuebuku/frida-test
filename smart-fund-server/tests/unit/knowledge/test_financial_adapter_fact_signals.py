"""Tests for financial adapter fact-signal grounding."""

from __future__ import annotations

from src.domain.knowledge_adapters.financial.adapter import _candidate_package_fact_signals_for_relation


def test_single_fact_signal_does_not_attach_to_unrelated_relation() -> None:
    signal = {
        "signal_type": "policy_impact",
        "topic_tags": ["十五五规划"],
        "impact_tags": ["新能源产业链需求提振"],
        "evidence_spans": [
            {
                "chunk_id": "chunk-1",
                "text": "工信部十五五规划强调新能源产业链发展。",
            }
        ],
    }
    relation = {
        "relation_type": "affects",
        "source": "硫酸短缺",
        "target": "铜价上涨",
        "reason": "硫酸供应紧张推升冶炼成本，进而影响铜价。",
        "evidence_spans": [
            {
                "chunk_id": "chunk-1",
                "text": "硫酸短缺推升冶炼成本，铜价上涨。",
            }
        ],
    }

    assert (
        _candidate_package_fact_signals_for_relation(
            [signal],
            relation_payload=relation,
            source_name="硫酸短缺",
            target_name="铜价上涨",
        )
        == []
    )


def test_single_fact_signal_attaches_when_text_and_chunk_match_relation() -> None:
    signal = {
        "signal_type": "supply_impact",
        "topic_tags": ["硫酸短缺"],
        "impact_tags": ["铜价上涨"],
        "evidence_spans": [
            {
                "chunk_id": "chunk-1",
                "text": "硫酸短缺推升冶炼成本，铜价上涨。",
            }
        ],
    }
    relation = {
        "relation_type": "affects",
        "source": "硫酸短缺",
        "target": "铜价上涨",
        "reason": "硫酸供应紧张推升冶炼成本，进而影响铜价。",
        "evidence_spans": [
            {
                "chunk_id": "chunk-1",
                "text": "硫酸短缺推升冶炼成本，铜价上涨。",
            }
        ],
    }

    assert _candidate_package_fact_signals_for_relation(
        [signal],
        relation_payload=relation,
        source_name="硫酸短缺",
        target_name="铜价上涨",
    ) == [signal]


def test_fact_signal_without_chunk_scope_is_not_attached() -> None:
    signal = {
        "signal_type": "supply_impact",
        "topic_tags": ["硫酸短缺"],
        "impact_tags": ["铜价上涨"],
        "evidence_spans": [{"text": "硫酸短缺推升冶炼成本，铜价上涨。"}],
    }
    relation = {
        "relation_type": "affects",
        "source": "硫酸短缺",
        "target": "铜价上涨",
        "evidence_spans": [{"chunk_id": "chunk-1", "text": "硫酸短缺推升冶炼成本，铜价上涨。"}],
    }

    assert (
        _candidate_package_fact_signals_for_relation(
            [signal],
            relation_payload=relation,
            source_name="硫酸短缺",
            target_name="铜价上涨",
        )
        == []
    )
