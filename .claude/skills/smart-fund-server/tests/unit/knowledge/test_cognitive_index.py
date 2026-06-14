"""Cognitive Card based community index tests."""

from __future__ import annotations

import pytest

from src.application.services.cognitive_index_service import CommunityCardBuilder
from src.domain.knowledge.cognitive_index import (
    CognitiveCard,
    assignment_query_text,
    cognitive_card_from_llm,
    validate_assignment_decision,
)
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.llm_proxy.types import LLMProxyResponse


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news_articles:test:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news_articles:test:1",
        content="并购重组政策推动半导体和高端装备制造产业链整合。",
        chunk_index=0,
        start_offset=0,
        end_offset=28,
        previous_chunk_id="",
        next_chunk_id="",
        text_hash="h1",
        chunker_version="recursive_zh_v1",
        payload={
            "source_type": "news_articles",
            "source_id": "test:1",
            "title": "并购重组政策推动产业链整合",
        },
    )


def _card_payload(title: str = "A股并购重组") -> dict:
    return {
        "summary": "政策推动并购重组服务产业链整合。",
        "title_candidates": [title, "并购重组政策与产业整合"],
        "topic_intents": [
            {
                "raw_theme": "并购重组政策推动产业链整合",
                "title_candidate": title,
                "parent_themes": ["A股并购重组"],
                "broad_topics": [title],
                "mid_topics": ["并购重组政策与产业整合"],
                "specific_topics": ["半导体产业链整合"],
                "topic_level_hint": "broad",
                "driver": ["政策支持"],
                "impact_target": ["半导体", "高端装备制造"],
                "risk_type": [],
                "event_thread": ["A股并购重组政策"],
                "event_action": ["推动产业链整合"],
                "actors": ["监管层"],
                "importance": 0.86,
                "impact_direction": "positive",
                "event_stage": "follow_up",
                "timeline_position": "follow_up",
                "event_time": "",
                "summary": "并购重组政策推动产业链整合。",
                "supporting_text": "并购重组政策推动半导体和高端装备制造产业链整合",
            }
        ],
        "risk_signals": [],
        "local_impact_signals": [],
        "actor_signals": {
            "actors": ["监管层"],
            "companies": [],
            "industries": ["半导体", "高端装备制造"],
            "regions": [],
            "policies": [],
            "commodities": [],
        },
        "supporting_text": ["并购重组政策推动半导体和高端装备制造产业链整合"],
    }


def test_cognitive_card_injects_system_pointers_without_llm_evidence_fields():
    card = cognitive_card_from_llm(_chunk(), _card_payload())

    assert card.source_id == "test:1"
    assert card.primary_chunk_id == "kg_chunk:kg_ev:financial:news_articles:test:1:0"
    assert card.chunk_ids == [card.primary_chunk_id]
    assert card.system_pointers["evidence_id"] == "kg_ev:financial:news_articles:test:1"
    assert card.system_pointers["previous_chunk_id"] == ""
    assert card.topic_intents[0]["parent_themes"] == ["A股并购重组"]
    assert card.topic_intents[0]["broad_topics"] == ["A股并购重组"]


def test_assignment_query_text_includes_parent_themes_before_child_topics():
    card = cognitive_card_from_llm(_chunk(), _card_payload("AI芯片产业"))

    query = assignment_query_text(card.topic_intents[0])

    assert "A股并购重组" in query
    assert query.index("A股并购重组") < query.index("AI芯片产业")


def test_assignment_validation_rejects_empty_title_as_new_l0():
    with pytest.raises(RuntimeError, match="new_community.title must be non-empty"):
        validate_assignment_decision(
            {
                "assignments": [
                    {
                        "action": "create_new_l0",
                        "community_id": None,
                        "weight": 0.9,
                        "confidence": 0.9,
                        "matched_reason": "candidate none",
                        "update_mode": "append_reference",
                        "reason": "empty title",
                        "new_community": {
                            "level": 0,
                            "title": "",
                            "scope": "新能源海外项目",
                            "title_quality": "broad_topic",
                            "level_rationale": "bad",
                            "future_coverage": ["后续项目进展"],
                            "intent_role": "topic",
                            "candidate_fit_summary": "none",
                        },
                    }
                ],
                "rejected_candidates": [],
                "maintenance_hints": {"suggest_split": False, "suggest_merge_community_ids": [], "reason": ""},
            },
            [],
            topic_intent={"specific_topics": ["细分主题"]},
        )


class _LLM:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )


def _create_assignment(title: str) -> dict:
    return {
        "assignments": [
            {
                "action": "create_new_l0",
                "community_id": None,
                "weight": 0.92,
                "confidence": 0.9,
                "matched_reason": "无候选社区",
                "update_mode": "append_reference",
                "reason": "新建父级主题",
                "new_community": {
                    "level": 0,
                    "title": title,
                    "scope": "围绕并购重组政策与产业整合的主题",
                    "title_quality": "broad_topic",
                    "level_rationale": "可承载多条资料",
                    "future_coverage": ["政策变化", "产业链整合"],
                    "intent_role": "parent_topic",
                    "candidate_fit_summary": "无候选",
                },
            }
        ],
        "rejected_candidates": [],
        "maintenance_hints": {"suggest_split": False, "suggest_merge_community_ids": [], "reason": ""},
    }


def _attach_assignment() -> dict:
    return {
        "assignments": [
            {
                "action": "attach_existing",
                "community_id": "c1",
                "weight": 0.88,
                "confidence": 0.91,
                "matched_reason": "同一并购重组父主题",
                "update_mode": "update_delta",
                "reason": "补充同一主题材料",
                "new_community": None,
            }
        ],
        "rejected_candidates": [],
        "maintenance_hints": {"suggest_split": False, "suggest_merge_community_ids": [], "reason": ""},
    }


@pytest.mark.asyncio
async def test_community_builder_creates_then_attaches_existing_l0():
    card1 = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    second_chunk = _chunk().model_copy(
        update={
            "chunk_id": "kg_chunk:kg_ev:financial:news_articles:test:2:0",
            "evidence_id": "kg_ev:financial:news_articles:test:2",
            "text_hash": "h2",
            "payload": {"source_type": "news_articles", "source_id": "test:2", "title": "产业并购继续活跃"},
        }
    )
    card2 = cognitive_card_from_llm(second_chunk, _card_payload("并购重组政策与产业整合"))
    llm = _LLM([_create_assignment("A股并购重组"), _attach_assignment()])
    committed = []

    class _Provider:
        async def recall(self, **_kwargs):
            if not committed:
                return []
            community = committed[-1]
            return [
                {
                    "community_id": community.community_id,
                    "title": community.title,
                    "level": community.level,
                    "parent_community_id": community.parent_community_id,
                    "summary": community.summary,
                    "canonical_labels": ["A股并购重组", "并购重组政策", "产业整合"],
                    "maturity": "single_evidence",
                    "retrieval_score": 0.91,
                    "retrieval_lane": "semantic_community",
                    "recent_examples": [],
                }
            ]

    async def commit(communities):
        committed[:] = communities

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        on_communities_updated=commit,
    ).build(
        adapter_name="financial",
        cards=[card1, card2],
        existing_communities=[],
    )

    assert len(result.communities) == 1
    assert result.communities[0].title == "A股并购重组"
    assert "政策变化" in result.communities[0].metrics["future_coverage"]
    assert len(result.assignments) == 2
    assert result.assignments[1].action == "attach_existing"
    assert result.diagnostics["communities"] == 1
