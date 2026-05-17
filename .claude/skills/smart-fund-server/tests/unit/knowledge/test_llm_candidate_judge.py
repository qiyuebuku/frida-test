"""Tests for LLM-backed candidate judgement."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.application.services.llm_candidate_judge import LLMCandidateJudge
from src.domain.knowledge.agentic_retrieval import CandidateContextPackage, SupportingEvidenceExcerpt
from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor


@dataclass
class _Response:
    text: str
    structured_output: dict | None


class _LLM:
    last_prompt: str = ""

    async def generate(self, request):
        assert request.metadata["task"] == "kg_candidate_judge"
        self.last_prompt = request.prompt_text()
        self.last_request = request
        return _Response(
            text="",
            structured_output={
                "judgements": [
                    {
                        "candidate_key": "C1",
                        "role": "answer",
                        "score": 2.0,
                        "expand": True,
                        "code": "direct_answer",
                    },
                    {
                        "candidate_key": "C2",
                        "role": "drop",
                        "score": 0.05,
                        "expand": False,
                        "code": "topic_drift",
                    },
                ]
            },
        )


class _EdgeAnswerLLM:
    async def generate(self, request):
        return _Response(
            text="",
            structured_output={
                "judgements": [
                    {
                        "candidate_key": "C1",
                        "role": "answer",
                        "score": 0.9,
                        "expand": True,
                        "code": "direct_answer",
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_llm_candidate_judge_uses_semantic_decisions() -> None:
    hits = [
        RetrievalHit(
            hit_id="hit:relevant",
            hit_type="evidence",
            title="俄就波法联合军演发出警告",
            snippet="欧洲对俄全面对抗正在回归。",
            source="test",
            evidence_refs=["kg_ev:military"],
        ),
        RetrievalHit(
            hit_id="hit:noisy",
            hit_type="evidence",
            title="固态电池政策支持宁德时代",
            snippet="宁德时代 产业链",
            source="test",
            evidence_refs=["kg_ev:catl"],
        ),
    ]

    llm = _LLM()
    judgements = await LLMCandidateJudge(llm_service=llm).judge(
        query="俄就波法联合军演发出警告",
        anchor=build_guarded_query_anchor("俄就波法联合军演发出警告"),
        hits=hits,
    )

    assert {item.candidate_id: item.decision for item in judgements} == {
        "hit:relevant": "keep",
        "hit:noisy": "drop",
    }
    assert {item.judge_source for item in judgements} == {"llm"}
    assert {item.candidate_id: item.role for item in judgements} == {
        "hit:relevant": "answer",
        "hit:noisy": "drop",
    }
    assert {item.candidate_id: item.reason_code for item in judgements} == {
        "hit:relevant": "direct_answer",
        "hit:noisy": "topic_drift",
    }
    assert {item.candidate_id: item.relevance_score for item in judgements}["hit:relevant"] == 1.0
    assert '"q":' in llm.last_prompt
    assert '"c":' in llm.last_prompt
    assert '"candidate_id"' not in llm.last_prompt
    assert '"id"' not in llm.last_prompt
    assert '"key":"C1"' in llm.last_prompt
    assert llm.last_request.use_cache is True


@pytest.mark.asyncio
async def test_llm_candidate_judge_cache_can_be_disabled(monkeypatch) -> None:
    hits = [
        RetrievalHit(
            hit_id="hit:relevant",
            hit_type="evidence",
            title="俄就波法联合军演发出警告",
            snippet="欧洲对俄全面对抗正在回归。",
            source="test",
            evidence_refs=["kg_ev:military"],
        ),
    ]

    llm = _LLM()
    monkeypatch.setenv("KG_RETRIEVAL_LLM_USE_CACHE", "0")
    await LLMCandidateJudge(llm_service=llm).judge(
        query="俄就波法联合军演发出警告",
        anchor=build_guarded_query_anchor("俄就波法联合军演发出警告"),
        hits=hits,
    )

    assert llm.last_request.use_cache is False


@pytest.mark.asyncio
async def test_llm_candidate_judge_receives_controller_search_plan() -> None:
    hit = RetrievalHit(
        hit_id="hit:catl",
        hit_type="evidence",
        title="宁德时代近期事件",
        snippet="宁德时代 300750 受海外产能影响。",
        source="test",
        evidence_refs=["kg_ev:catl"],
    )
    package = CandidateContextPackage(
        candidate=hit,
        search_plan={
            "answer_targets": ["近期影响宁德时代300750的事件列表"],
            "negative_boundaries": ["宁德时代电池技术细节"],
            "expected_evidence": ["新闻事件", "公告"],
            "relation_intents": ["impact"],
            "stop_condition": "获取到至少5个近期主导事件",
        },
    )

    llm = _LLM()
    await LLMCandidateJudge(llm_service=llm).judge(
        query="宁德时代 300750 最近受哪些事件影响",
        anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响"),
        hits=[package],
    )

    assert '"plan":' in llm.last_prompt
    assert "近期影响宁德时代300750的事件列表" in llm.last_prompt
    assert "宁德时代电池技术细节" in llm.last_prompt
    assert "新闻事件" in llm.last_prompt
    assert "impact" in llm.last_prompt


@pytest.mark.asyncio
async def test_llm_candidate_judge_downgrades_edge_answer_to_support() -> None:
    hit = RetrievalHit(
        hit_id="kg_edge:financial:mentions:1",
        hit_type="edge",
        title="mentions",
        snippet="事件 --mentions--> 新能源车产业链",
        source="graph",
        evidence_refs=["kg_ev:catl"],
    )

    judgements = await LLMCandidateJudge(llm_service=_EdgeAnswerLLM()).judge(
        query="海外工厂投产会带动哪些产业链机会",
        anchor=build_guarded_query_anchor("海外工厂投产会带动哪些产业链机会"),
        hits=[hit],
    )

    assert judgements[0].decision == "keep"
    assert judgements[0].role == "support"
    assert judgements[0].reason_code == "evidence_support"


@pytest.mark.asyncio
async def test_llm_candidate_judge_uses_shared_evidence_context() -> None:
    long_excerpt = (
        "A股并购重组市场呈现三方面新变化。"
        "Wind资讯数据显示，年内A股上市公司首次披露的并购重组交易持续增长。"
    )
    packages = [
        CandidateContextPackage(
            candidate=RetrievalHit(
                hit_id="kg_ev:financial:ma",
                hit_type="evidence",
                title="A股并购重组市场呈现三方面新变化",
                snippet=long_excerpt,
                source="keyword",
                evidence_refs=["kg_ev:financial:ma"],
            ),
            supporting_evidence_excerpt=[
                SupportingEvidenceExcerpt(
                    evidence_id="kg_ev:financial:ma",
                    source_id="ft_news:83904",
                    excerpt=long_excerpt,
                )
            ],
        ),
        CandidateContextPackage(
            candidate=RetrievalHit(
                hit_id="kg:financial:industry:semi",
                hit_type="node",
                title="半导体",
                snippet=f"半导体。{long_excerpt}",
                source="keyword",
                node_refs=["kg:financial:industry:semi"],
                evidence_refs=["kg_ev:financial:ma"],
            ),
            supporting_evidence_excerpt=[
                SupportingEvidenceExcerpt(
                    evidence_id="kg_ev:financial:ma",
                    source_id="ft_news:83904",
                    excerpt=long_excerpt,
                )
            ],
        ),
    ]

    llm = _LLM()
    await LLMCandidateJudge(llm_service=llm).judge(
        query="并购重组涉及哪些行业",
        anchor=build_guarded_query_anchor("并购重组涉及哪些行业"),
        hits=packages,
    )

    assert '"evidence_context":' in llm.last_prompt
    assert llm.last_prompt.count("Wind资讯数据显示") == 1
    assert '"evidence_keys":["E1"]' in llm.last_prompt
    assert '"title":"半导体"' in llm.last_prompt
    assert '"meaning":"半导体"' not in llm.last_prompt
