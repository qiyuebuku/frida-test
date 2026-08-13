from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.application.services.agent_run_prepare_service import (
    AgentRunPrepareService,
)


CUTOFF = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)


class _Market:
    def market_frame(self, *, cutoff_at):
        assert cutoff_at == CUTOFF
        return {
            "frame_id": "market-frame:1",
            "trade_date": date(2026, 8, 7),
            "market_session": "continuous",
            "overview": "11 个市场维度有数据。",
            "dimensions": [
                {
                    "dimension": "a_share_market",
                    "status": "available",
                    "as_of": CUTOFF,
                    "subject_count": 12,
                    "freshness": {"fresh": 12},
                    "drilldown_handle": "market-dimension:a_share_market:1",
                }
            ],
            "significant_changes": [
                {
                    "label": "市场温度",
                    "baseline_value": 50,
                    "current_value": 80,
                    "change_pct": 60,
                }
            ],
            "quality_issues": [],
        }


class _Research:
    def current_report_at(self, *, cutoff_at):
        return {"revision_id": "report-rev-1"}

    def list_views_at(self, **_kwargs):
        return [
            {
                "view_id": "view-1",
                "revision_id": "view-rev-1",
                "title": "半导体景气",
                "status": "active",
                "thesis": "供需改善有待继续验证。",
                "confidence": {"overall": "medium"},
                "valid_until": None,
            }
        ]

    def search_memories(self, **_kwargs):
        return [
            {
                "memory_id": "memory-1",
                "summary": "量价背离时降低方向置信度。",
                "applicability": "市场温度上升但成交未确认。",
                "counterexample": "事件驱动的一次性跳空。",
                "evidence_references": ["case-1"],
                "confidence": "medium",
                "expires_at": None,
            }
        ]


def _trigger(cutoff_at=CUTOFF):
    return {
        "trigger_id": "trigger-1",
        "trigger_slot": "intraday",
        "source": "schedule",
        "reason": "盘中检查",
        "cutoff_at": cutoff_at.isoformat(),
        "run_mode": "shadow",
        "max_tool_calls": 40,
        "max_elapsed_seconds": 600,
    }


def test_run_prepare_builds_context_from_server_state() -> None:
    result = AgentRunPrepareService(
        market_service=_Market(),
        research_repository=_Research(),
    ).prepare_research(
        trigger_payload=_trigger(),
        signed_cutoff_at=CUTOFF,
        research_question="市场温度上升是否得到成交确认？",
    )

    pack = result["context_pack"]
    assert pack["market_state"]["frame_id"] == "market-frame:1"
    assert pack["current_report_revision_id"] == "report-rev-1"
    assert pack["active_views"][0]["view_id"] == "view-1"
    assert pack["memory_items"][0]["memory_id"] == "memory-1"
    assert pack["research_question"] == "市场温度上升是否得到成交确认？"


def test_run_prepare_rejects_trigger_cutoff_mismatch() -> None:
    service = AgentRunPrepareService(
        market_service=_Market(),
        research_repository=_Research(),
    )
    with pytest.raises(ValueError, match="does not match"):
        service.prepare_research(
            trigger_payload=_trigger(),
            signed_cutoff_at=datetime(2026, 8, 7, 6, 1, tzinfo=UTC),
        )


def test_run_prepare_excludes_dimension_written_after_cutoff() -> None:
    class _MarketWithConcurrentWrite(_Market):
        def market_frame(self, *, cutoff_at):
            frame = super().market_frame(cutoff_at=cutoff_at)
            frame["dimensions"].append(
                {
                    "dimension": "etf_fund",
                    "status": "available",
                    "as_of": cutoff_at + timedelta(milliseconds=5),
                    "subject_count": 10,
                    "freshness": {"realtime": 10},
                    "drilldown_handle": "market-dimension:etf_fund:future",
                }
            )
            return frame

    result = AgentRunPrepareService(
        market_service=_MarketWithConcurrentWrite(),
        research_repository=_Research(),
    ).prepare_research(
        trigger_payload=_trigger(),
        signed_cutoff_at=CUTOFF,
    )

    state = result["context_pack"]["market_state"]
    assert state["dimensions"] == []
    assert state["drilldown_handles"] == []
    assert state["quality_issues"] == []


def test_run_prepare_keeps_only_critical_market_quality_issues() -> None:
    class _MarketWithDiagnostics(_Market):
        def market_frame(self, *, cutoff_at):
            frame = super().market_frame(cutoff_at=cutoff_at)
            frame["quality_issues"] = [
                {
                    "issue_code": "mixed_trade_dates",
                    "severity": "warning",
                    "description": "诊断信息",
                    "affected_dimensions": ["sector_style"],
                },
                {
                    "issue_code": "no_market_snapshots",
                    "severity": "critical",
                    "description": "没有市场快照",
                    "affected_dimensions": ["a_share_market"],
                },
            ]
            return frame

    result = AgentRunPrepareService(
        market_service=_MarketWithDiagnostics(),
        research_repository=_Research(),
    ).prepare_research(
        trigger_payload=_trigger(),
        signed_cutoff_at=CUTOFF,
    )

    issues = result["context_pack"]["market_state"]["quality_issues"]
    assert [item["issue_code"] for item in issues] == ["no_market_snapshots"]
