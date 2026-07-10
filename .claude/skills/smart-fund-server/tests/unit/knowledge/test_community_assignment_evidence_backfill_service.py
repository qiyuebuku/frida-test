"""Community Assignment 证据回填测试。"""

from __future__ import annotations

import json

import pytest

from src.application.services.community_assignment_evidence_backfill_service import (
    CommunityAssignmentEvidenceBackfillService,
    _BackfillCandidate,
    _build_grounding_prompt,
    _clear_card_intent_grounding,
    _evidence_span_from_segment_selection,
    _extract_chunk_text,
    _grounding_from_intent,
    _grounding_is_valid,
    _intent_prompt_context,
    _set_card_intent_grounding,
)
from src.infrastructure.llm_proxy.types import LLMProxyResponse


def test_extract_chunk_text_uses_evidence_text_section_only():
    document = "\n".join(
        [
            "Document Type: Evidence Chunk",
            "Title: 半导体设备景气",
            "Evidence Text: 联电6月销售额同比增长23%。",
        ]
    )

    assert _extract_chunk_text(document) == "联电6月销售额同比增长23%。"
    assert _extract_chunk_text("没有正文标记") == ""


def test_grounding_validation_requires_exact_contiguous_source_text():
    chunk_text = "联电6月销售额231.2亿元台币，同比增长23%。"

    assert _grounding_is_valid(
        {
            "evidence_span": "联电6月销售额231.2亿元台币，同比增长23%。",
        },
        chunk_text,
    )
    assert not _grounding_is_valid(
        {
            "evidence_span": "联电销售额大幅增长。",
        },
        chunk_text,
    )


def test_nested_card_intent_grounding_is_updated_without_flat_duplicate():
    intent = {
        "assignment_profile": {"title_candidate": "晶圆代工业营收表现"},
        "cognitive_material": {"driver": ["市场需求"]},
    }

    updated = _set_card_intent_grounding(
        intent,
        evidence_span="联电6月销售额同比增长23%。",
    )

    assert updated["cognitive_material"]["evidence_span"] == "联电6月销售额同比增长23%。"
    assert "evidence_claim" not in updated["cognitive_material"]
    assert "evidence_span" not in updated
    assert _grounding_from_intent(updated) == {"evidence_span": "联电6月销售额同比增长23%。"}


def test_clear_card_intent_grounding_removes_legacy_claim_and_span():
    intent = {
        "cognitive_material": {
            "driver": ["市场需求"],
            "evidence_span": "不应继续使用的证据",
            "evidence_claim": "不应继续使用的复述",
        }
    }

    cleared = _clear_card_intent_grounding(intent)

    assert cleared["cognitive_material"] == {
        "driver": ["市场需求"],
        "evidence_grounding_status": "unsupported",
    }
    assert _grounding_from_intent(cleared) is None


def test_grounding_prompt_excludes_assignment_decision_fields():
    intent = {
        "title_candidate": "晶圆代工业营收表现",
        "parent_themes": ["半导体产业链"],
        "reason": "因为属于半导体产业链，所以挂载到该 community。",
        "community_id": "kgc:financial:l0:731",
        "assignment_id": "assignment-1",
        "cognitive_card_id": "card-1",
        "evidence_id": "evidence-1",
    }

    context = _intent_prompt_context(intent)
    prompt = json.loads(_build_grounding_prompt(intent, "原始正文"))

    assert context == {
        "title_candidate": "晶圆代工业营收表现",
        "parent_themes": ["半导体产业链"],
    }
    assert prompt["topic_intent"] == context
    assert prompt["source_segments"] == [{"segment_id": 1, "text": "原始正文"}]


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, _request):
        self.calls += 1
        if self.calls == 1:
            payload = {
                "supported": True,
                "start_segment": 9,
                "end_segment": 9,
            }
        else:
            payload = {
                "supported": True,
                "start_segment": 1,
                "end_segment": 1,
            }
        return LLMProxyResponse(
            text=json.dumps(payload, ensure_ascii=False),
            structured_output=payload,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


@pytest.mark.asyncio
async def test_generate_grounding_retries_non_exact_span():
    service = object.__new__(CommunityAssignmentEvidenceBackfillService)
    service._llm = _FakeLLM()
    candidate = _BackfillCandidate(
        cognitive_card_id="card-1",
        intent_index=1,
        intent_id="card-1:intent:1",
        primary_chunk_id="chunk-1",
        source_id="ft_news:1",
        topic_intent={"title_candidate": "晶圆代工业营收表现"},
        card_topic_intent={},
        assignment_ids=("assignment-1",),
        community_ids=("kgc:financial:l0:731",),
        existing_grounding=None,
    )

    outcome = await service._generate_grounding(
        candidate,
        "联电6月销售额同比增长23%。",
    )

    assert outcome.status == "generated"
    assert outcome.llm_calls == 2
    assert outcome.evidence_span == "联电6月销售额同比增长23%。"


def test_segment_selection_copies_exact_contiguous_source_text():
    chunk_text = "野村认为短期担忧过度。\nAI需求仍然强劲，存储芯片供应偏紧。后续仍需观察产能。"

    evidence_span = _evidence_span_from_segment_selection(
        chunk_text,
        {"start_segment": 1, "end_segment": 3},
    )

    assert evidence_span == "野村认为短期担忧过度。\nAI需求仍然强劲，存储芯片供应偏紧。"
    assert evidence_span in chunk_text
