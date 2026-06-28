"""Cognitive Card based community index tests."""

from __future__ import annotations

import json

import pytest

from src.application.services.cognitive_index_service import (
    AssignmentCandidateOrderStore,
    CognitiveCardExtractor,
    CommunityCardBuilder,
)
from src.domain.knowledge.cognitive_index import (
    CognitiveCard,
    _assignment_topic_intent,
    _drafts_from_existing,
    assignment_candidate_order_key,
    assignment_query_text,
    cognitive_card_from_llm,
    seed_community_drafts,
    seed_graph_communities,
    validate_assignment_decision,
)
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.llm_proxy.types import LLMProxyResponse
from src.infrastructure.clients.reranker import RerankResponse, RerankResult


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
                        "action": "create_new",
                        "community_id": "new_1",
                        "weight": 0.9,
                        "confidence": 0.9,
                        "fit_type": "new_parent_topic",
                        "reason": "empty title",
                    }
                ],
                "new_communities": [{"client_id": "new_1", "title": "", "scope": "新能源海外项目"}],
            },
            [],
            topic_intent={"specific_topics": ["细分主题"]},
        )


def test_assignment_validation_rejects_create_new_unknown_client_id():
    with pytest.raises(RuntimeError, match="unknown new community"):
        validate_assignment_decision(
            {
                "assignments": [
                    {
                        "action": "create_new",
                        "community_id": "new_missing",
                        "weight": 0.9,
                        "confidence": 0.9,
                        "fit_type": "new_parent_topic",
                        "reason": "错误新建平级主题",
                    }
                ],
                "new_communities": [{"client_id": "new_1", "title": "AI芯片供应链", "scope": "围绕 AI 芯片供需的主题"}],
            },
            [{"community_id": "c1"}],
            topic_intent={"specific_topics": ["AI芯片短缺"]},
        )


def test_assignment_validation_rejects_attach_existing_with_new_parent_fit_type():
    with pytest.raises(RuntimeError, match="attach_existing fit_type cannot be new_parent_topic"):
        validate_assignment_decision(
            {
                "assignments": [
                    {
                        "action": "attach_existing",
                        "community_id": "c1",
                        "weight": 0.9,
                        "confidence": 0.9,
                        "fit_type": "new_parent_topic",
                        "reason": "错误 fit_type",
                    }
                ],
                "new_communities": [],
            },
            [{"community_id": "c1"}],
            topic_intent={"parent_themes": ["AI算力链"]},
        )


def test_existing_community_draft_restores_directory_signals_from_metrics():
    community = GraphIndexCommunity(
        community_id="kg_community:cognitive_topic:l0:ai",
        version_id="v1",
        adapter_name="financial",
        projection="cognitive_topic",
        level=0,
        parent_community_id="",
        title="AI算力链",
        summary="AI算力链目录",
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=["ev1"],
        chunk_ids=["chunk1"],
        metrics={
            "source_ids": ["source1"],
            "cognitive_card_ids": ["card1"],
            "assigned_intents": [
                {
                    "parent_themes": ["AI算力链"],
                    "broad_topics": ["人工智能基础设施"],
                    "mid_topics": ["AI芯片供应", "光模块CPO"],
                    "specific_topics": ["特斯拉自建芯片产能"],
                    "raw_theme": "AI芯片供应短缺",
                    "title_candidate": "AI芯片供应链",
                    "event_thread": ["AI算力硬件产业链"],
                }
            ],
            "future_coverage": ["AI服务器", "数据中心算力"],
            "scope": "承接 AI 芯片、光模块、AI服务器、数据中心等算力基础设施主题",
        },
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="lineage",
        previous_community_ids=[],
    )

    draft = _drafts_from_existing([community])[community.community_id]
    candidate = draft.to_assignment_candidate(score=0.8, lane="semantic:parent_topic")

    assert candidate["source_count"] == 1
    assert "AI芯片供应" in candidate["future_coverage"]
    assert "AI服务器" in candidate["future_coverage"]
    assert "AI算力链" in candidate["canonical_labels"]
    assert "可吸收子方向" in candidate["coverage_contract"]


def test_seed_graph_communities_preserve_existing_seed_refs():
    existing_seed = next(item for item in seed_graph_communities("financial") if item.title == "AI算力链")
    existing_seed = GraphIndexCommunity(
        community_id=existing_seed.community_id,
        version_id="v-old",
        adapter_name="financial",
        projection="cognitive_topic",
        level=0,
        parent_community_id="",
        title="AI算力链",
        summary="已经挂入真实资料的 AI 算力链主题",
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=["ev-old"],
        chunk_ids=["chunk-old"],
        metrics={
            **(existing_seed.metrics or {}),
            "origin": "seed",
            "source_ids": ["source-old"],
            "cognitive_card_ids": ["card-old"],
            "assigned_intents": [
                {
                    "cognitive_card_id": "card-old",
                    "source_id": "source-old",
                    "evidence_id": "ev-old",
                    "chunk_ids": ["chunk-old"],
                    "raw_theme": "AI芯片供应",
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片供应变化",
                    "parent_themes": ["AI算力链"],
                    "broad_topics": ["人工智能基础设施"],
                    "mid_topics": ["AI芯片供应"],
                    "specific_topics": ["先进制程芯片"],
                }
            ],
            "assignments": [{"assignment_id": "a-old", "cognitive_card_id": "card-old"}],
        },
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="lineage",
        previous_community_ids=[],
    )

    seeds = seed_graph_communities("financial", existing_communities=[existing_seed])
    ai_seed = next(item for item in seeds if item.title == "AI算力链")

    assert ai_seed.evidence_ids == ["ev-old"]
    assert ai_seed.chunk_ids == ["chunk-old"]
    assert ai_seed.summary == "已经挂入真实资料的 AI 算力链主题"
    assert ai_seed.metrics["source_ids"] == ["source-old"]
    assert ai_seed.metrics["cognitive_card_ids"] == ["card-old"]
    assert ai_seed.metrics["assigned_intents"][0]["cognitive_card_id"] == "card-old"


class _LLM:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.requests = []
        self.repairs = []

    async def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if callable(output):
            output = output(request)
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )

    async def repair_with_feedback(self, request, response, validation_issues, **kwargs):
        self.repairs.append(
            {
                "request": request,
                "response": response,
                "validation_issues": validation_issues,
                "kwargs": kwargs,
            }
        )
        return await self.generate(request)


class _Reranker:
    def __init__(self, order: list[int]) -> None:
        self.order = order
        self.calls = []

    async def rerank(self, *, query, documents, top_n=None):
        self.calls.append({"query": query, "documents": documents, "top_n": top_n})
        return RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(index=index, relevance_score=1.0 - rank * 0.01, document=documents[index])
                for rank, index in enumerate(self.order)
            ],
            latency_ms=1,
            total_documents=len(documents),
        )


class _MemoryOrderStore(AssignmentCandidateOrderStore):
    def __init__(self, initial: dict[str, list[str]] | None = None) -> None:
        self.orders = dict(initial or {})
        self.saved = []

    def order_candidates(self, *, adapter_name, query_key, candidates):
        previous = self.orders.get(query_key, [])
        by_id = {candidate["community_id"]: candidate for candidate in candidates}
        ordered = []
        for community_id in previous:
            candidate = by_id.pop(community_id, None)
            if candidate is not None:
                ordered.append(candidate)
        ordered.extend(sorted(by_id.values(), key=lambda item: (item.get("title") or "", item.get("community_id") or "")))
        return ordered

    def save_order(self, *, adapter_name, query_key, candidates):
        ordered_ids = [candidate["community_id"] for candidate in candidates]
        self.orders[query_key] = ordered_ids
        self.saved.append({"adapter_name": adapter_name, "query_key": query_key, "ordered_ids": ordered_ids})


@pytest.mark.asyncio
async def test_cognitive_card_extractor_repairs_non_object_output():
    llm = _LLM([["not", "object"], _card_payload()])

    cards = await CognitiveCardExtractor(llm=llm, model="test-model", concurrency=1).extract([_chunk()])

    assert len(cards) == 1
    assert cards[0].source_id == "test:1"
    assert cards[0].topic_intents[0]["raw_theme"] == "并购重组政策推动产业链整合"
    assert len(llm.repairs) == 1
    assert "must be JSON object" in llm.repairs[0]["validation_issues"][0]
    assert llm.repairs[0]["kwargs"]["retry_reason"] == "cognitive_card_validation_invalid"


def _create_assignment(title: str) -> dict:
    return {
        "assignments": [
            {
                "action": "create_new",
                "community_id": "new_1",
                "weight": 0.92,
                "confidence": 0.9,
                "fit_type": "new_parent_topic",
                "reason": "新建父级主题",
            }
        ],
        "new_communities": [
            {"client_id": "new_1", "title": title, "scope": "围绕并购重组政策与产业整合的主题"}
        ],
    }


def _attach_assignment(alias: str = "c1") -> dict:
    return {
        "assignments": [
            {
                "action": "attach_existing",
                "community_id": alias,
                "weight": 0.88,
                "confidence": 0.91,
                "fit_type": "new_subtopic",
                "reason": "补充同一主题材料",
            }
        ],
        "new_communities": [],
    }


def _first_candidate_alias(request) -> str:
    prompt = json.loads(request.prompt)
    return prompt["candidate_communities"][0]["community_id"]


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
    llm = _LLM([_create_assignment("A股并购重组"), lambda request: _attach_assignment(_first_candidate_alias(request))])
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
    assert "并购重组政策与产业整合" in result.communities[0].metrics["future_coverage"]
    assert result.communities[0].metrics["assigned_intent_count"] == 2
    assert result.communities[0].metrics["assignment_count"] == 2
    assert result.communities[0].metrics["source_count"] == 2
    assert result.communities[0].metrics["unique_source_count"] == 2
    assert result.communities[0].metrics["evidence_count"] == 2
    assert result.communities[0].metrics["chunk_count"] == 2
    assert result.communities[0].metrics["cognitive_card_count"] == 2
    assert result.communities[0].metrics["avg_assignment_weight"] == 0.9
    assert result.communities[0].metrics["high_weight_assignment_count"] == 2
    assert result.communities[0].metrics["topic_diversity_count"] >= 4
    assert len(result.assignments) == 2
    assert result.assignments[1].action == "attach_existing"
    assert result.diagnostics["communities"] == 1


@pytest.mark.asyncio
async def test_community_builder_deduplicates_existing_intent_when_rebuilding():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    existing_id = "kg_community:cognitive_topic:l0:merger"
    existing = GraphIndexCommunity(
        community_id=existing_id,
        version_id="v1",
        adapter_name="financial",
        projection="cognitive_topic",
        level=0,
        parent_community_id="",
        title="A股并购重组",
        summary="A股并购重组主题",
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=[card.evidence_id],
        chunk_ids=card.chunk_ids,
        metrics={
            "source_ids": [card.source_id],
            "cognitive_card_ids": [card.cognitive_card_id],
            "assigned_intents": [
                {
                    "cognitive_card_id": card.cognitive_card_id,
                    "source_id": card.source_id,
                    "evidence_id": card.evidence_id,
                    "chunk_ids": card.chunk_ids,
                    "raw_theme": card.topic_intents[0]["raw_theme"],
                    "title_candidate": card.topic_intents[0]["title_candidate"],
                    "summary": card.topic_intents[0]["summary"],
                    "parent_themes": card.topic_intents[0]["parent_themes"],
                    "broad_topics": card.topic_intents[0]["broad_topics"],
                    "mid_topics": card.topic_intents[0]["mid_topics"],
                    "specific_topics": card.topic_intents[0]["specific_topics"],
                }
            ],
        },
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="lineage",
        previous_community_ids=[],
    )

    class _Provider:
        async def recall(self, **_kwargs):
            return [
                {
                    "community_id": existing_id,
                    "title": "A股并购重组",
                    "level": 0,
                    "parent_community_id": "",
                    "summary": "A股并购重组主题",
                    "canonical_labels": ["A股并购重组"],
                    "maturity": "single_evidence",
                    "retrieval_score": 0.91,
                    "retrieval_lane": "semantic:parent_topic",
                    "recent_examples": [],
                }
            ]

    result = await CommunityCardBuilder(
        llm=_LLM([lambda request: _attach_assignment(_first_candidate_alias(request))]),
        model="test-model",
        candidate_provider=_Provider(),
    ).build(
        adapter_name="financial",
        cards=[card],
        existing_communities=[existing],
    )

    assert len(result.communities) == 1
    assert len(result.communities[0].metrics["assigned_intents"]) == 1
    assert result.communities[0].metrics["assigned_intent_count"] == 1
    assert result.communities[0].metrics["assignment_count"] == 1
    assert result.communities[0].metrics["avg_assignment_weight"] == 0.88
    assert len(result.communities[0].metrics["cognitive_card_ids"]) == 1


@pytest.mark.asyncio
async def test_materialized_seed_community_candidate_can_be_attached():
    chunk = _chunk().model_copy(
        update={
            "content": "AI芯片供给不足推动算力硬件产业链扩产，数据中心需求继续上升。",
            "end_offset": 35,
        }
    )
    payload = _card_payload("AI算力链")
    payload["topic_intents"][0].update(
        {
            "raw_theme": "AI芯片供给不足推动算力硬件扩产",
            "title_candidate": "AI算力链",
            "parent_themes": ["AI算力链"],
            "broad_topics": ["人工智能基础设施"],
            "mid_topics": ["AI芯片供应", "算力硬件扩产"],
            "specific_topics": ["AI芯片供给不足"],
            "driver": ["AI应用需求"],
            "impact_target": ["AI芯片", "数据中心", "算力硬件"],
            "event_thread": ["AI算力链供需变化"],
            "summary": "AI芯片供给不足推动算力硬件产业链扩产。",
        }
    )
    card = cognitive_card_from_llm(chunk, payload)
    seed_communities = seed_graph_communities("financial")
    ai_seed = next(item for item in seed_communities if item.title == "AI算力链")
    llm = _LLM([lambda request: _attach_assignment(_first_candidate_alias(request))])

    class _Provider:
        async def recall(self, **_kwargs):
            return [
                {
                    "community_id": ai_seed.community_id,
                    "title": ai_seed.title,
                    "origin": (ai_seed.metrics or {}).get("origin"),
                    "level": ai_seed.level,
                    "parent_community_id": ai_seed.parent_community_id,
                    "scope": (ai_seed.metrics or {}).get("scope"),
                    "include_rules": (ai_seed.metrics or {}).get("include_rules"),
                    "exclude_rules": (ai_seed.metrics or {}).get("exclude_rules"),
                    "granularity_note": (ai_seed.metrics or {}).get("granularity_note"),
                    "summary": ai_seed.summary,
                    "canonical_labels": (ai_seed.metrics or {}).get("canonical_labels"),
                    "maturity": "seed_reference",
                    "retrieval_score": 0.93,
                    "retrieval_lane": "semantic:parent_topic",
                    "recent_examples": [],
                }
            ]

    result = await CommunityCardBuilder(llm=llm, model="test-model", candidate_provider=_Provider()).build(
        adapter_name="financial",
        cards=[card],
        existing_communities=seed_communities,
    )

    prompt = llm.requests[0].prompt
    assert '"origin": "seed"' in prompt
    assert "AI算力链" in prompt
    assert "source_id" not in prompt
    assert "evidence_id" not in prompt
    assert "chunk_ids" not in prompt
    assert "primary_chunk_id" not in prompt
    assert len(seed_community_drafts("financial")) == 8
    assert len(result.communities) == 8
    ai_result = next(item for item in result.communities if item.title == "AI算力链")
    assert ai_result.metrics["origin"] == "seed"
    assert ai_result.evidence_ids == [card.evidence_id]
    assert ai_result.metrics["assigned_intent_count"] == 1
    assert ai_result.metrics["assignment_count"] == 1
    assert ai_result.metrics["avg_assignment_weight"] == 0.88
    assert "AI芯片" in ai_result.metrics["canonical_labels"]


@pytest.mark.asyncio
async def test_community_builder_reranks_many_candidates_before_assignment():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    candidates = []
    for index in range(8):
        community = GraphIndexCommunity(
            community_id=f"kg_community:cognitive_topic:l0:test_{index}",
            version_id=f"v{index}",
            adapter_name="financial",
            projection="cognitive_topic",
            level=0,
            parent_community_id="",
            title=f"候选主题{index}",
            summary=f"候选主题{index} summary",
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=[],
            chunk_ids=[],
            metrics={"origin": "emergent", "scope": f"候选主题{index} scope", "canonical_labels": [f"候选主题{index}"]},
            status="active",
            previous_version_id="",
            change_reason="cognitive_assignment",
            lineage_id=f"lineage{index}",
            previous_community_ids=[],
        )
        candidates.append(community)

    class _Provider:
        async def recall(self, **_kwargs):
            return [
                {
                    "community_id": community.community_id,
                    "title": community.title,
                    "origin": "emergent",
                    "scope": community.metrics["scope"],
                    "canonical_labels": community.metrics["canonical_labels"],
                    "maturity": "single_evidence",
                    "retrieval_score": 0.5,
                    "retrieval_lane": "semantic:merged",
                    "recent_examples": [],
                }
                for community in candidates
            ]

    reranker = _Reranker([7, 0, 1, 2, 3, 4, 5, 6])
    result = await CommunityCardBuilder(
        llm=_LLM([lambda request: _attach_assignment(_first_candidate_alias(request))]),
        model="test-model",
        candidate_provider=_Provider(),
        reranker_client=reranker,
    ).build(adapter_name="financial", cards=[card], existing_communities=candidates)

    prompt = result.assignments[0].decision
    assert reranker.calls
    assert reranker.calls[0]["top_n"] == 8
    assert result.assignments[0].community_id == candidates[0].community_id
    assert prompt["assignments"][0]["community_id"] == candidates[0].community_id


@pytest.mark.asyncio
async def test_assignment_prompt_uses_persistent_prefix_order_and_slim_candidate_fields():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    candidate_rows = [
        {
            "community_id": "kg_community:cognitive_topic:l0:capital_market",
            "title": "资本市场改革",
            "origin": "seed",
            "level": 0,
            "scope": "承接 IPO、并购重组、区域股权市场和券商投行。",
            "directory_scope": "重复目录范围",
            "include_rules": ["并购重组"],
            "exclude_rules": ["普通业绩"],
            "canonical_labels": ["资本市场改革", "并购重组"],
            "coverage_contract": "可吸收并购重组子方向",
            "future_coverage": ["IPO", "并购重组"],
            "source_count": 99,
            "retrieval_score": 0.12,
            "rerank_score": 0.99,
            "recent_examples": [{"title": "不应进入 prompt"}],
        },
        {
            "community_id": "kg_community:cognitive_topic:l0:ai_compute",
            "title": "AI算力链",
            "origin": "seed",
            "level": 0,
            "scope": "承接 AI 芯片、光模块、数据中心。",
            "canonical_labels": ["AI算力链"],
            "coverage_contract": "可吸收 AI 算力子方向",
            "future_coverage": ["AI芯片"],
            "source_count": 88,
            "retrieval_score": 0.88,
            "rerank_score": 0.88,
        },
        {
            "community_id": "kg_community:cognitive_topic:l0:policy",
            "title": "政策监管与产业扶持",
            "origin": "seed",
            "level": 0,
            "scope": "承接产业政策和监管规则。",
            "canonical_labels": ["政策监管"],
            "coverage_contract": "可吸收政策子方向",
            "future_coverage": ["地方政策"],
            "source_count": 77,
            "retrieval_score": 0.77,
            "rerank_score": 0.77,
        },
    ]
    existing_communities = [
        GraphIndexCommunity(
            community_id=str(row["community_id"]),
            version_id=f"{row['community_id']}:v1",
            adapter_name="financial",
            projection="cognitive_topic",
            level=int(row.get("level") or 0),
            parent_community_id="",
            title=str(row["title"]),
            summary=str(row.get("scope") or ""),
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=[],
            chunk_ids=[],
            metrics={
                "origin": row.get("origin"),
                "scope": row.get("scope"),
                "include_rules": row.get("include_rules") or [],
                "exclude_rules": row.get("exclude_rules") or [],
                "canonical_labels": row.get("canonical_labels") or [],
                "future_coverage": row.get("future_coverage") or [],
                "coverage_contract": row.get("coverage_contract") or "",
            },
            status="active",
            previous_version_id="",
            change_reason="cognitive_assignment",
            lineage_id="lineage",
            previous_community_ids=[],
        )
        for row in candidate_rows
    ]

    class _Provider:
        async def recall(self, **_kwargs):
            return list(candidate_rows)

    order_store = _MemoryOrderStore()

    def save_after_read(request):
        prompt = json.loads(request.prompt)
        # LLM attaches to the first prompt candidate so the test also verifies alias resolution.
        return _attach_assignment(prompt["candidate_communities"][0]["community_id"])

    # Seed the real query key after the card has been converted into the assignment intent.
    topic_intent = _assignment_topic_intent(card, card.topic_intents[0])
    query_key = assignment_candidate_order_key(topic_intent)
    order_store.orders[query_key] = [
        "kg_community:cognitive_topic:l0:policy",
        "kg_community:cognitive_topic:l0:capital_market",
    ]
    llm = _LLM([save_after_read])

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=order_store,
    ).build(adapter_name="financial", cards=[card], existing_communities=existing_communities)

    request_prompt = json.loads(llm.requests[0].prompt)
    prompt_candidates = request_prompt["candidate_communities"]
    assert [item["title"] for item in prompt_candidates] == [
        "政策监管与产业扶持",
        "资本市场改革",
        "AI算力链",
    ]
    assert all(item["community_id"].startswith("c_") for item in prompt_candidates)
    for item in prompt_candidates:
        assert "source_count" not in item
        assert "directory_scope" not in item
        assert "retrieval_score" not in item
        assert "rerank_score" not in item
        assert "recent_examples" not in item
        assert "summary" not in item
    assert result.assignments[0].community_id == "kg_community:cognitive_topic:l0:policy"
    assert order_store.saved[0]["ordered_ids"] == [
        "kg_community:cognitive_topic:l0:policy",
        "kg_community:cognitive_topic:l0:capital_market",
        "kg_community:cognitive_topic:l0:ai_compute",
    ]
