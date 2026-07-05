"""Cognitive Card based community index tests."""

from __future__ import annotations

import json
import asyncio

import pytest

from src.application.services.cognitive_index_service import (
    AssignmentBucketStore,
    AssignmentBucketSemanticCache,
    AssignmentCandidateOrderStore,
    CognitiveCardExtractor,
    CommunityBucketPlanner,
    CommunityCardBuilder,
    _candidate_append_log_base_count,
    _dedupe_assignment_candidates,
)
from src.domain.knowledge.cognitive_index import (
    ASSIGNMENT_MAX_TOKENS,
    COGNITIVE_CARD_MAX_TOKENS,
    CognitiveCard,
    _drafts_from_existing,
    assignment_query_text,
    cognitive_card_from_llm,
    seed_community_drafts,
    seed_graph_communities,
    validate_assignment_decision,
)
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.config import settings
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


def test_assignment_validation_does_not_override_llm_market_boundary_decision():
    validate_assignment_decision(
        {
            "assignments": [
                {
                    "action": "create_new",
                    "community_id": "new_1",
                    "weight": 0.82,
                    "confidence": 0.86,
                    "fit_type": "new_parent_topic",
                    "reason": "模型认为该主题可长期承接市场微观结构变化。",
                }
            ],
            "new_communities": [
                {
                    "client_id": "new_1",
                    "title": "市场微观结构变化",
                    "scope": "承接成交结构、资金行为和交易制度变化等可复用市场结构主题。",
                }
            ],
        },
        [],
        topic_intent={
            "raw_theme": "成交结构变化影响板块轮动",
            "parent_themes": ["市场微观结构变化"],
            "specific_topics": ["成交额放量"],
        },
    )


def test_assignment_validation_allows_driver_topic_as_new_l0():
    validate_assignment_decision(
        {
            "assignments": [
                {
                    "action": "create_new",
                    "community_id": "new_1",
                    "weight": 0.88,
                    "confidence": 0.9,
                    "fit_type": "new_parent_topic",
                    "reason": "现有候选无法承接能源供应风险，创建可长期复用的驱动主题。",
                }
            ],
            "new_communities": [{"client_id": "new_1", "title": "能源供应安全", "scope": "承接油气运输、地缘冲突和能源供需扰动"}],
        },
        [],
        topic_intent={
            "raw_theme": "霍尔木兹海峡紧张导致油气供应风险",
            "parent_themes": ["能源供应安全"],
            "driver": ["地缘冲突", "运输中断风险"],
            "impact_target": ["油气", "能源价格"],
        },
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


class _ConcurrentAssignmentLLM:
    def __init__(self, planning_output: dict, *, assignment_delay: float = 0.02) -> None:
        self.planning_output = planning_output
        self.assignment_delay = assignment_delay
        self.requests = []
        self.active_assignments = 0
        self.max_active_assignments = 0

    async def generate(self, request):
        self.requests.append(request)
        task = request.metadata.get("task")
        if task == "kg_assignment_bucket_planning":
            output = self.planning_output
        elif task == "kg_community_assignment":
            self.active_assignments += 1
            self.max_active_assignments = max(self.max_active_assignments, self.active_assignments)
            try:
                await asyncio.sleep(self.assignment_delay)
            finally:
                self.active_assignments -= 1
            prompt = json.loads(request.prompt)
            title = str(prompt["topic_intent"].get("title_candidate") or "测试主题")
            output = _create_assignment(title)
        else:
            output = {}
        return LLMProxyResponse(
            text="",
            structured_output=output,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
        )

    async def repair_with_feedback(self, request, response, validation_issues, **kwargs):
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


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttl[key] = ttl
        return True

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.data:
                deleted += 1
                self.data.pop(key, None)
                self.ttl.pop(key, None)
        return deleted


class _BrokenRedis:
    def get(self, _key):
        raise ConnectionError("redis unavailable")


class _BucketSemanticCache:
    def __init__(self, responses=None, *, has_entries: bool = True):
        self.responses = list(responses or [])
        self.has_entries_value = has_entries
        self.resolve_calls = []
        self.upserts = []
        self.deletes = []

    def has_entries(self, **_kwargs):
        return self.has_entries_value

    async def resolve(self, **kwargs):
        self.resolve_calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return {"direct": None, "candidates": []}

    async def upsert_buckets(self, **kwargs):
        self.upserts.append(kwargs)
        return len(kwargs.get("buckets") or [])

    def delete_buckets(self, **kwargs):
        self.deletes.append(kwargs)


def _order_store() -> AssignmentCandidateOrderStore:
    return AssignmentCandidateOrderStore(target="test", redis_client=_MemoryRedis())


def _ledger_candidate(
    community_id: str,
    title: str,
    *,
    source_count: int = 0,
    intent_count: int = 0,
    future_coverage: list[str] | None = None,
) -> dict:
    return {
        "community_id": community_id,
        "title": title,
        "origin": "emergent",
        "level": 0,
        "scope": f"{title} scope",
        "canonical_labels": [title],
        "source_count": source_count,
        "assigned_intent_count": intent_count,
        "maturity": "single_evidence",
        "future_coverage": future_coverage or [],
        "retrieval_score": 0.8,
        "retrieval_lane": "semantic:merged",
        "recent_examples": [],
    }


def test_candidate_ledger_uses_single_append_log_without_reordering_or_midstream_updates():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(target="test", redis_client=redis)

    first_log, first_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:1", "AI算力链", source_count=1, intent_count=1),
            _ledger_candidate("kgc:financial:l0:2", "资本市场改革", source_count=1, intent_count=1),
        ],
    )
    second_log, second_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:2", "资本市场改革", source_count=1, intent_count=1),
            _ledger_candidate("kgc:financial:l0:1", "AI算力链", source_count=1, intent_count=1),
            _ledger_candidate("kgc:financial:l0:3", "新能源出海", source_count=1, intent_count=1),
        ],
    )
    third_log, third_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate(
                "kgc:financial:l0:1",
                "AI算力链",
                source_count=2,
                intent_count=2,
                future_coverage=["AI芯片供需", "光模块/CPO"],
            ),
        ],
    )

    assert [item["entry_type"] for item in first_log] == ["candidate_base", "candidate_base"]
    assert [item["community_id"] for item in second_log] == [
        "kgc:financial:l0:1",
        "kgc:financial:l0:2",
        "kgc:financial:l0:3",
    ]
    assert [item["entry_type"] for item in third_log] == [
        "candidate_base",
        "candidate_base",
        "candidate_base",
    ]
    assert first_diag["redis_available"] is True
    assert second_diag["appended_base"] == 1
    assert third_diag["appended_update"] == 0
    assert _candidate_append_log_base_count(third_log) == 3
    assert sum(1 for item in third_log if item["entry_type"] == "candidate_update") == 0


def test_candidate_ledger_compacts_legacy_entries_loaded_from_redis():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(target="test", redis_client=redis)
    redis.setex(
        store._ledger_key(adapter_name="financial"),
        3600,
        json.dumps(
            {
                "schema_version": "candidate_append_log_v1",
                "candidate_append_log": [
                    {
                        "entry_type": "candidate_base",
                        "community_id": "kgc:financial:l0:1",
                        "title": "AI算力链",
                        "origin": "seed",
                        "level": 0,
                        "scope": "承接 AI 芯片、算力硬件和数据中心。",
                        "include_rules": [],
                        "exclude_rules": [],
                        "canonical_labels": ["AI算力链"],
                        "granularity_note": "",
                        "dynamic_context": {
                            "absorbed_subtopics": ["AI芯片供需", "光模块/CPO"],
                            "recent_signal_summary": ["旧格式摘要不应继续发送"],
                            "maturity": "seed_reference",
                        },
                    },
                    {
                        "entry_type": "candidate_update",
                        "community_id": "kgc:financial:l0:1",
                        "title": "AI算力链",
                        "update_type": "absorbed_signals",
                        "source_count_delta": 1,
                        "intent_count_delta": 1,
                        "maturity": "multi_evidence",
                        "change_summary": "旧格式变化摘要不应继续发送",
                    },
                ],
                "candidate_stats": {},
                "candidate_counters": {},
                "checkpoint_meta": {},
            },
            ensure_ascii=False,
        ),
    )

    append_log, _diag = store.prepare_append_log(adapter_name="financial", candidates=[])

    assert append_log == [
        {
            "entry_type": "candidate_base",
            "community_id": "kgc:financial:l0:1",
            "title": "AI算力链",
            "scope": "承接 AI 芯片、算力硬件和数据中心。",
            "canonical_labels": ["AI算力链"],
            "absorbed_subtopics": ["AI芯片供需", "光模块/CPO"],
            "maturity": "seed_reference",
        }
    ]
    saved = json.loads(redis.get(store._ledger_key(adapter_name="financial")))
    assert saved["candidate_append_log"] == append_log


def test_candidate_ledger_does_not_append_update_after_attach_decision():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(target="test", redis_client=redis)
    store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate(
                "kgc:financial:l0:1",
                "AI算力链",
                future_coverage=["AI算力链"],
            )
        ],
    )

    store.record_assignment_decision(
        adapter_name="financial",
        decision={
            "assignments": [
                {
                    "action": "attach_existing",
                    "community_id": "kgc:financial:l0:1",
                    "weight": 0.9,
                    "confidence": 0.9,
                    "fit_type": "new_subtopic",
                    "reason": "吸收 AI 芯片供需和光模块方向。",
                }
            ],
            "new_communities": [],
        },
        topic_intent={
            "parent_themes": ["AI算力链"],
            "broad_topics": ["AI基础设施"],
            "mid_topics": ["AI芯片供需"],
            "specific_topics": ["光模块/CPO"],
            "driver": ["云厂商资本开支"],
            "impact_target": ["算力硬件"],
            "event_thread": ["AI算力扩张"],
            "event_action": ["某公司扩建数据中心"],
            "actors": ["某云厂商"],
        },
    )

    saved = json.loads(redis.get(store._ledger_key(adapter_name="financial")))
    assert [item["entry_type"] for item in saved["candidate_append_log"]] == ["candidate_base"]
    assert saved["candidate_stats"]["kgc:financial:l0:1"]["future_coverage"] == [
        "AI算力链",
    ]


def test_candidate_ledger_does_not_append_update_for_weak_adjacent_assignment():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(target="test", redis_client=redis)
    store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate(
                "kgc:financial:l0:6",
                "资本市场改革",
                future_coverage=["资本市场改革"],
            )
        ],
    )

    store.record_assignment_decision(
        adapter_name="financial",
        decision={
            "assignments": [
                {
                    "action": "attach_existing",
                    "community_id": "kgc:financial:l0:6",
                    "weight": 0.45,
                    "confidence": 0.7,
                    "fit_type": "adjacent_context",
                    "reason": "只是相邻背景。",
                }
            ],
            "new_communities": [],
        },
        topic_intent={
            "parent_themes": ["并购重组"],
            "broad_topics": ["上市公司资本运作"],
            "mid_topics": ["重大资产重组"],
            "specific_topics": ["东方证券收购上海证券100%股权"],
            "impact_target": ["证券行业"],
        },
    )

    saved = json.loads(redis.get(store._ledger_key(adapter_name="financial")))
    assert [item["entry_type"] for item in saved["candidate_append_log"]] == ["candidate_base"]
    assert saved["candidate_stats"]["kgc:financial:l0:6"]["future_coverage"] == ["资本市场改革"]


def test_candidate_ledger_checkpoint_skips_rebuild_when_hot_prefix_overlap_is_high():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(
        target="test",
        redis_client=redis,
        max_base_candidates=3,
        keep_base_candidates=3,
        max_chars=100_000,
    )

    first_log, first_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:1", "AI算力链"),
            _ledger_candidate("kgc:financial:l0:2", "资本市场改革"),
            _ledger_candidate("kgc:financial:l0:3", "新能源出海"),
        ],
    )
    second_log, second_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:4", "宏观流动性"),
        ],
    )

    assert [item["community_id"] for item in first_log] == [
        "kgc:financial:l0:1",
        "kgc:financial:l0:2",
        "kgc:financial:l0:3",
    ]
    assert [item["community_id"] for item in second_log if item["entry_type"] == "candidate_base"] == [
        "kgc:financial:l0:1",
        "kgc:financial:l0:2",
        "kgc:financial:l0:3",
        "kgc:financial:l0:4",
    ]
    assert first_diag["checkpointed"] is False
    assert second_diag["checkpointed"] is False
    assert second_diag["checkpoint_skipped_by_overlap"] is True
    ledger = json.loads(next(iter(redis.data.values())))
    assert ledger["checkpoint_meta"]["last_checkpoint_skipped_by_overlap"] is True


def test_candidate_ledger_checkpoint_rebuilds_when_hot_prefix_overlap_is_low():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(
        target="test",
        redis_client=redis,
        max_base_candidates=3,
        keep_base_candidates=2,
        max_chars=100_000,
    )

    store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:1", "AI算力链"),
            _ledger_candidate("kgc:financial:l0:2", "资本市场改革"),
            _ledger_candidate("kgc:financial:l0:3", "新能源出海"),
        ],
    )
    store.record_assignment_decision(
        adapter_name="financial",
        decision={
            "assignments": [
                {"action": "attach_existing", "community_id": "kgc:financial:l0:3"},
                {"action": "attach_existing", "community_id": "kgc:financial:l0:3"},
                {"action": "attach_existing", "community_id": "kgc:financial:l0:2"},
            ]
        },
    )
    second_log, second_diag = store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate("kgc:financial:l0:4", "宏观流动性"),
        ],
    )

    assert [item["community_id"] for item in second_log if item["entry_type"] == "candidate_base"] == [
        "kgc:financial:l0:2",
        "kgc:financial:l0:3",
        "kgc:financial:l0:4",
    ]
    assert second_diag["checkpointed"] is True
    assert second_diag["checkpoint_skipped_by_overlap"] is False
    ledger = json.loads(next(iter(redis.data.values())))
    assert ledger["checkpoint_meta"]["checkpoint_count"] == 1
    assert ledger["checkpoint_meta"]["last_checkpointed"] is True


def test_candidate_ledger_does_not_create_updates_from_candidate_stat_changes():
    redis = _MemoryRedis()
    store = AssignmentCandidateOrderStore(
        target="test",
        redis_client=redis,
        max_base_candidates=100,
        keep_base_candidates=10,
        max_chars=100_000,
    )

    store.prepare_append_log(
        adapter_name="financial",
        candidates=[
            _ledger_candidate(
                "kgc:financial:l0:1",
                "AI算力链",
                source_count=1,
                intent_count=1,
                future_coverage=["初始方向"],
            ),
        ],
    )
    last_log = []
    last_diag = {}
    for value in range(2, 65):
        last_log, last_diag = store.prepare_append_log(
            adapter_name="financial",
            candidates=[
                _ledger_candidate(
                    "kgc:financial:l0:1",
                    "AI算力链",
                    source_count=value,
                    intent_count=value,
                    future_coverage=["初始方向", f"新增方向{value}"],
                ),
            ],
        )

    assert [item["entry_type"] for item in last_log] == ["candidate_base"]
    assert last_diag["checkpointed"] is False
    assert last_diag["checkpoint_skipped_by_overlap"] is False
    assert last_diag["candidate_append_log_update_count"] == 0
    ledger = json.loads(next(iter(redis.data.values())))
    assert ledger["checkpoint_meta"]["checkpoint_count"] == 0
    assert ledger["checkpoint_meta"]["last_checkpoint_skipped_by_overlap"] is False


@pytest.mark.asyncio
async def test_bucket_planner_uses_llm_then_reuses_theme_cache():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "ai_infra",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片"],
                    }
                ],
                "theme_bucket_updates": [{"canonical_theme": "AI芯片", "bucket_id": "ai_infra"}],
            }
        ]
    )
    planner = CommunityBucketPlanner(store=store, llm=llm, model="test-model")
    first = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "broad_topics": ["人工智能基础设施"],
                    "mid_topics": ["AI芯片供需"],
                    "event_action": ["扩产"],
                    "driver": ["AI需求增长"],
                    "risk_type": ["供应短缺"],
                    "impact_target": ["某公司"],
                    "title_candidate": "AI算力链",
                    "summary": (
                        "AI芯片供需紧张，算力基础设施资本开支提升，光模块和服务器需求持续增长，"
                        "产业链公司订单和产能利用率同步改善，海外云厂商资本开支继续上修。"
                    ),
                },
            }
        ],
    )
    second = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-2",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "broad_topics": ["人工智能基础设施"],
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片继续短缺。",
                },
            }
        ],
    )

    assert len(llm.requests) == 1
    prompt_payload = json.loads(llm.requests[0].prompt)
    prompt_signature = prompt_payload["topic_intent_signatures"][0]
    assert "intent_id" not in prompt_signature
    assert "source_id" not in prompt_signature
    assert "chunk_index" not in prompt_signature
    assert "broad_topics" not in prompt_signature
    assert "mid_topics" not in prompt_signature
    assert "impact_target" not in prompt_signature
    assert "summary" not in prompt_signature
    assert "event_action" not in prompt_signature
    assert "driver" not in prompt_signature
    assert "risk_type" not in prompt_signature
    assert prompt_signature["routing_signals"] == ["扩产", "AI需求增长", "供应短缺"]
    expected_summary = (
        "AI芯片供需紧张，算力基础设施资本开支提升，光模块和服务器需求持续增长，"
        "产业链公司订单和产能利用率同步改善，海外云厂商资本开支继续上修。"
    )
    assert prompt_signature["context_hint"] == expected_summary[:60]
    assert len(prompt_signature["context_hint"]) <= 60
    assert set(first["buckets"]) == {"ai_infra"}
    assert set(second["buckets"]) == {"ai_infra"}
    assert second["unknown_intents"] == 0
    saved = json.loads(redis.get(store._state_key(adapter_name="financial")))
    assert saved["theme_bucket_map"]["ai芯片"] == "ai_infra"


def test_assignment_bucket_store_force_clear_deletes_state_and_stale_lock():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    state_key = store._state_key(adapter_name="financial")
    lock_key = store._lock_key(adapter_name="financial", name="bucket_catalog")
    redis.setex(state_key, 60, "{}")
    redis.setex(lock_key, 60, "stale")

    result = store.clear(adapter_name="financial", force_stale_lock=True)

    assert result["deleted"] == 1
    assert result["lock_deleted"] == 1
    assert result["force_stale_lock"] is True
    assert redis.get(state_key) is None
    assert redis.get(lock_key) is None


@pytest.mark.asyncio
async def test_bucket_planner_uses_semantic_cache_direct_hit_without_llm():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    store.apply_planning_decision(
        adapter_name="financial",
        decision={
            "assignments": [],
            "new_buckets": [
                {
                    "bucket_id": "bucket_ai_infra",
                    "bucket_title": "AI算力链",
                    "scope": "承接 AI 芯片、光模块和数据中心。",
                    "canonical_themes": ["AI算力链"],
                }
            ],
            "theme_bucket_updates": [],
        },
    )
    semantic_cache = _BucketSemanticCache(
        [
            {
                "direct": {
                    "bucket_id": "bucket_ai_infra",
                    "bucket_title": "AI算力链",
                    "semantic_score": 0.93,
                },
                "candidates": [],
            }
        ]
    )
    llm = _LLM([])
    planner = CommunityBucketPlanner(
        store=store,
        llm=llm,
        model="test-model",
        semantic_bucket_cache=semantic_cache,
    )

    result = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {"parent_themes": ["人工智能基础设施"], "title_candidate": "AI硬件链"},
            }
        ],
    )

    assert llm.requests == []
    assert len(semantic_cache.resolve_calls) == 1
    assert result["assignments"]["intent-1"]["bucket_id"] == "bucket_ai_infra"
    assert result["assignments"]["intent-1"]["cache_source"] == "semantic"
    saved = json.loads(redis.get(store._state_key(adapter_name="financial")))
    assert saved["theme_bucket_map"]["人工智能基础设施"] == "bucket_ai_infra"


@pytest.mark.asyncio
async def test_bucket_planner_passes_semantic_candidates_to_llm_and_upserts_bucket_cache():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    store.apply_planning_decision(
        adapter_name="financial",
        decision={
            "assignments": [],
            "new_buckets": [
                {
                    "bucket_id": "bucket_ai_infra",
                    "bucket_title": "AI算力链",
                    "scope": "承接 AI 芯片、光模块和数据中心。",
                    "canonical_themes": ["AI算力链"],
                }
            ],
            "theme_bucket_updates": [],
        },
    )
    semantic_cache = _BucketSemanticCache(
        [
            {
                "direct": None,
                "candidates": [
                    {
                        "bucket_id": "bucket_ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块和数据中心。",
                        "semantic_score": 0.84,
                    }
                ],
            }
        ]
    )
    llm = _LLM(
        [
            {
                "assignments": [{"canonical_theme": "AI硬件产业链", "bucket_id": "bucket_ai_infra"}],
                "new_buckets": [],
                "theme_bucket_updates": [],
            }
        ]
    )
    planner = CommunityBucketPlanner(
        store=store,
        llm=llm,
        model="test-model",
        semantic_bucket_cache=semantic_cache,
    )

    await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {"parent_themes": ["AI硬件产业链"], "title_candidate": "AI硬件链"},
            }
        ],
    )

    prompt = json.loads(llm.requests[0].prompt)
    assert prompt["topic_intent_signatures"][0]["semantic_bucket_candidates"] == [
        {
            "bucket_id": "bucket_ai_infra",
            "bucket_title": "AI算力链",
            "scope": "承接 AI 芯片、光模块和数据中心。",
            "semantic_score": 0.84,
        }
    ]
    assert semantic_cache.upserts
    assert semantic_cache.upserts[0]["buckets"][0]["bucket_id"] == "bucket_ai_infra"


@pytest.mark.asyncio
async def test_bucket_planner_repairs_non_object_output():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            ["not", "object"],
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "ai_infra",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片"],
                    }
                ],
                "theme_bucket_updates": [{"canonical_theme": "AI芯片", "bucket_id": "ai_infra"}],
            },
        ]
    )
    planner = CommunityBucketPlanner(store=store, llm=llm, model="test-model")

    result = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "broad_topics": ["人工智能基础设施"],
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片供需紧张。",
                },
            }
        ],
    )

    assert set(result["buckets"]) == {"ai_infra"}
    assert len(llm.repairs) == 1
    assert "must be JSON object" in llm.repairs[0]["validation_issues"][0]
    assert llm.repairs[0]["kwargs"]["retry_reason"] == "bucket_planning_validation_invalid"


@pytest.mark.asyncio
async def test_bucket_planner_can_bypass_llm_cache_for_validation_runs():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "ai_infra",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片"],
                    }
                ],
                "theme_bucket_updates": [],
            }
        ]
    )
    planner = CommunityBucketPlanner(store=store, llm=llm, model="test-model", use_cache=False)

    await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片供需紧张。",
                },
            }
        ],
    )

    assert llm.requests[0].use_cache is False
    assert llm.requests[0].metadata["llm_use_cache"] is False
    assert llm.requests[0].provider_options["thinking_type"] == "disabled"
    assert llm.requests[0].metadata["thinking_type"] == "disabled"
    assert llm.requests[0].metadata["merge_decoupled"] is True


@pytest.mark.asyncio
async def test_bucket_planner_uses_semantic_cache_when_redis_catalog_is_empty():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    semantic_cache = _BucketSemanticCache(
        [
            {
                "direct": {
                    "bucket_id": "bucket_ai_infra",
                    "bucket_title": "AI算力链",
                    "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                    "canonical_themes": ["AI算力链", "AI芯片"],
                    "semantic_score": 0.93,
                    "semantic_cache_source": "milvus_metadata",
                },
                "candidates": [
                    {
                        "bucket_id": "bucket_ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "semantic_score": 0.93,
                    }
                ],
                "raw_hits": 1,
            }
        ]
    )
    llm = _LLM([])
    planner = CommunityBucketPlanner(
        store=store,
        llm=llm,
        model="test-model",
        semantic_bucket_cache=semantic_cache,
    )

    result = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI算力链"],
                    "title_candidate": "AI算力链",
                    "summary": "AI 芯片供需紧张。",
                },
            }
        ],
    )

    assert llm.requests == []
    assert result["assignments"]["intent-1"]["bucket_id"] == "bucket_ai_infra"
    assert result["assignments"]["intent-1"]["cache_source"] == "semantic"
    assert result["semantic_cache"]["semantic_requests"] == 1
    assert result["semantic_cache"]["semantic_raw_hits"] == 1
    assert result["semantic_cache"]["semantic_candidate_hits"] == 1
    assert result["semantic_cache"]["semantic_direct_hits"] == 1
    saved = json.loads(redis.get(store._state_key(adapter_name="financial")))
    assert saved["bucket_catalog"]["bucket_ai_infra"]["bucket_title"] == "AI算力链"
    assert saved["bucket_catalog"]["bucket_ai_infra"]["scope"] == "承接 AI 芯片、光模块、数据中心和算电协同。"
    assert saved["theme_bucket_map"]["ai算力链"] == "bucket_ai_infra"


@pytest.mark.asyncio
async def test_assignment_bucket_semantic_cache_directs_by_metadata_theme_match(monkeypatch):
    class _Store:
        def list_target_ids(self, **_kwargs):
            return ["bucket_doc"]

        def vector_search(self, **_kwargs):
            from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit

            return [
                MilvusHybridHit(
                    chunk_id="bucket_doc",
                    evidence_id="",
                    text="",
                    score=0.55,
                    metadata={
                        "bucket_id": "bucket_ma",
                        "bucket_title": "并购重组",
                        "scope": "承接并购重组相关写冲突域。",
                        "canonical_themes": ["A股并购重组热潮"],
                    },
                )
            ]

    async def _fake_embed_texts(_texts):
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr("src.application.services.cognitive_index_service.embed_texts", _fake_embed_texts)
    cache = AssignmentBucketSemanticCache(target="test", store=_Store())

    result = await cache.resolve(
        adapter_name="financial",
        topic_intent={
            "parent_themes": ["A股并购重组热潮"],
            "title_candidate": "A股并购重组热潮",
        },
        catalog={},
    )

    assert result["direct"]["bucket_id"] == "bucket_ma"
    assert result["direct"]["semantic_direct_reason"] == "exact_theme_match"
    assert result["candidates"][0]["semantic_score"] == 0.55


@pytest.mark.asyncio
async def test_bucket_planner_default_model_uses_bucket_specific_pro_model(monkeypatch):
    monkeypatch.setattr(settings, "KG_ASSIGNMENT_BUCKET_MODEL", "deepseek-v4-pro")
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "ai_infra",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片"],
                    }
                ],
                "theme_bucket_updates": [],
            }
        ]
    )
    planner = CommunityBucketPlanner(store=store, llm=llm)

    await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片供需紧张。",
                },
            }
        ],
    )

    assert llm.requests[0].model == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_bucket_planner_can_enable_thinking_for_ab_validation():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "ai_infra",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片"],
                    }
                ],
                "theme_bucket_updates": [],
            }
        ]
    )
    planner = CommunityBucketPlanner(
        store=store,
        llm=llm,
        model="test-model",
        bucket_thinking="enabled",
    )

    await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {
                    "parent_themes": ["AI芯片"],
                    "title_candidate": "AI算力链",
                    "summary": "AI芯片供需紧张。",
                },
            }
        ],
    )

    assert llm.requests[0].provider_options["thinking_type"] == "enabled"
    assert llm.requests[0].metadata["thinking_type"] == "enabled"


@pytest.mark.asyncio
async def test_bucket_planner_batches_unknown_intents_and_updates_cache_between_batches():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {
                        "canonical_theme": "AI芯片",
                        "bucket_id": "bucket_ai_infra",
                    },
                    {
                        "canonical_theme": "AI芯片供需",
                        "bucket_id": "bucket_ai_infra",
                    },
                ],
                "new_buckets": [
                    {
                        "bucket_id": "bucket_ai_infra",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
                        "canonical_themes": ["AI芯片", "AI芯片供需"],
                    }
                ],
                "theme_bucket_updates": [{"canonical_theme": "AI芯片", "bucket_id": "bucket_ai_infra"}],
            },
            {
                "assignments": [
                    {
                        "canonical_theme": "券商自营风险",
                        "bucket_id": "bucket_broker_risk",
                    }
                ],
                "new_buckets": [
                    {
                        "bucket_id": "bucket_broker_risk",
                        "bucket_title": "券商业务风险",
                        "scope": "承接券商业务风险、信用风险和自营业务困境。",
                        "canonical_themes": ["券商自营风险"],
                    }
                ],
                "theme_bucket_updates": [],
            },
        ]
    )
    planner = CommunityBucketPlanner(store=store, llm=llm, model="test-model", planning_batch_size=2)
    intent_refs = [
        {
            "intent_id": "intent-1",
            "card": None,
            "intent_index": 1,
            "topic_intent": {"parent_themes": ["AI芯片"], "title_candidate": "AI算力链"},
        },
        {
            "intent_id": "intent-2",
            "card": None,
            "intent_index": 1,
            "topic_intent": {"parent_themes": ["AI芯片供需"], "title_candidate": "AI算力链"},
        },
        {
            "intent_id": "intent-3",
            "card": None,
            "intent_index": 1,
            "topic_intent": {"parent_themes": ["券商自营风险"], "title_candidate": "券商业务风险"},
        },
        {
            "intent_id": "intent-4",
            "card": None,
            "intent_index": 1,
            "topic_intent": {"parent_themes": ["AI芯片"], "title_candidate": "AI算力链"},
        },
    ]

    result = await planner.plan(adapter_name="financial", intent_refs=intent_refs)

    assert len(llm.requests) == 2
    assert llm.requests[0].metadata["batch_index"] == 1
    assert llm.requests[0].metadata["unknown_intents"] == 2
    assert llm.requests[1].metadata["batch_index"] == 2
    assert llm.requests[1].metadata["unknown_intents"] == 1
    first_prompt = json.loads(llm.requests[0].prompt)
    second_prompt = json.loads(llm.requests[1].prompt)
    assert first_prompt["existing_bucket_catalog"] == []
    assert second_prompt["existing_bucket_catalog"] == [
        {
            "bucket_id": "bucket_ai_infra",
            "bucket_title": "AI算力链",
            "scope": "承接 AI 芯片、光模块、数据中心和算电协同。",
        }
    ]
    assert "canonical_themes" not in second_prompt["existing_bucket_catalog"][0]
    assert result["planning_batches"] == 2
    assert result["assignments"]["intent-4"]["from_cache"] is True
    assert result["assignments"]["intent-4"]["bucket_id"] == "bucket_ai_infra"
    saved = json.loads(redis.get(store._state_key(adapter_name="financial")))
    assert saved["bucket_catalog"]["bucket_ai_infra"]["canonical_themes"] == ["AI芯片", "AI芯片供需"]


@pytest.mark.asyncio
async def test_bucket_planner_auto_runs_independent_merge_when_catalog_exceeds_threshold():
    redis = _MemoryRedis()
    store = AssignmentBucketStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            {
                "assignments": [
                    {"canonical_theme": "AI芯片", "bucket_id": "bucket_ai_chip"},
                    {"canonical_theme": "AI算力", "bucket_id": "bucket_ai_compute"},
                    {"canonical_theme": "券商风险", "bucket_id": "bucket_broker_risk"},
                ],
                "new_buckets": [
                    {
                        "bucket_id": "bucket_ai_chip",
                        "bucket_title": "AI芯片",
                        "scope": "承接 AI 芯片供需。",
                        "canonical_themes": ["AI芯片"],
                    },
                    {
                        "bucket_id": "bucket_ai_compute",
                        "bucket_title": "AI算力链",
                        "scope": "承接 AI 算力基础设施。",
                        "canonical_themes": ["AI算力"],
                    },
                    {
                        "bucket_id": "bucket_broker_risk",
                        "bucket_title": "券商业务风险",
                        "scope": "承接券商业务风险。",
                        "canonical_themes": ["券商风险"],
                    },
                ],
                "theme_bucket_updates": [],
            },
            {
                "merge_actions": [
                    {
                        "source_bucket_id": "bucket_ai_chip",
                        "target_bucket_id": "bucket_ai_compute",
                        "confidence": 0.88,
                    }
                ],
                "rejected_merge_candidates": [],
            },
        ]
    )
    planner = CommunityBucketPlanner(
        store=store,
        llm=llm,
        model="test-model",
        auto_merge_threshold=2,
        auto_merge_candidate_limit=8,
    )

    result = await planner.plan(
        adapter_name="financial",
        intent_refs=[
            {
                "intent_id": "intent-1",
                "card": None,
                "intent_index": 1,
                "topic_intent": {"parent_themes": ["AI芯片"], "title_candidate": "AI芯片"},
            },
            {
                "intent_id": "intent-2",
                "card": None,
                "intent_index": 1,
                "topic_intent": {"parent_themes": ["AI算力"], "title_candidate": "AI算力链"},
            },
            {
                "intent_id": "intent-3",
                "card": None,
                "intent_index": 1,
                "topic_intent": {"parent_themes": ["券商风险"], "title_candidate": "券商业务风险"},
            },
        ],
    )

    assert len(llm.requests) == 2
    assert llm.requests[0].metadata["task"] == "kg_assignment_bucket_planning"
    assert llm.requests[1].metadata["task"] == "kg_assignment_bucket_merge"
    merge_prompt = json.loads(llm.requests[1].prompt)
    assert "current_bucket_catalog" not in merge_prompt
    assert "community_summaries" not in merge_prompt
    assert 1 <= len(merge_prompt["merge_candidates"]) <= 4
    assert all("source_scope" in item and "target_scope" in item for item in merge_prompt["merge_candidates"])
    assert llm.requests[1].provider_options["thinking_type"] == "disabled"
    assert result["merge_result"]["triggered"] is True
    assert result["merge_result"]["merged"] == 1
    saved = json.loads(redis.get(store._state_key(adapter_name="financial")))
    assert "bucket_ai_chip" not in saved["bucket_catalog"]
    assert saved["theme_bucket_map"]["ai芯片"] == "bucket_ai_compute"


@pytest.mark.asyncio
async def test_community_builder_skips_bucket_planning_for_single_intent():
    card = cognitive_card_from_llm(_chunk(), _card_payload("AI算力链"))
    redis = _MemoryRedis()
    bucket_store = AssignmentBucketStore(target="test", redis_client=redis)
    order_store = AssignmentCandidateOrderStore(target="test", redis_client=redis)
    llm = _LLM(
        [
            _create_assignment("AI算力链"),
        ]
    )

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_order_store=order_store,
        bucket_planner=CommunityBucketPlanner(store=bucket_store, llm=llm, model="test-model"),
        bucket_concurrency=1,
        community_id_factory=lambda adapter_name, level, title: f"kgc:{adapter_name}:l{level}:501",
    ).build(adapter_name="financial", cards=[card], existing_communities=[])

    assert len(llm.requests) == 1
    assert llm.requests[0].metadata["task"] == "kg_community_assignment"
    assert result.diagnostics["bucket_planning"]["enabled"] is False
    assert result.diagnostics["bucket_planning"]["bucket_count"] == 1
    assert result.diagnostics["bucket_concurrency"] == 1
    assert result.assignments[0].community_id == "kgc:financial:l0:501"


@pytest.mark.asyncio
async def test_community_builder_runs_assignment_buckets_concurrently():
    chunk_a = _chunk()
    chunk_b = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news_articles:test:2:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news_articles:test:2",
        content="AI 芯片供需紧张带动光模块和数据中心资本开支。",
        chunk_index=0,
        start_offset=0,
        end_offset=25,
        previous_chunk_id="",
        next_chunk_id="",
        text_hash="h2",
        chunker_version="recursive_zh_v1",
        payload={
            "source_type": "news_articles",
            "source_id": "test:2",
            "title": "AI芯片供需紧张",
        },
    )
    card_a = cognitive_card_from_llm(chunk_a, _card_payload("A股并购重组"))
    card_b = cognitive_card_from_llm(chunk_b, _card_payload("AI算力链"))
    planning_output = {
        "assignments": [
            {
                "intent_id": f"{card_a.cognitive_card_id}:1",
                "canonical_theme": "A股并购重组",
                "bucket_id": "bucket_ma",
            },
            {
                "intent_id": f"{card_b.cognitive_card_id}:1",
                "canonical_theme": "AI算力链",
                "bucket_id": "bucket_ai",
            },
        ],
        "new_buckets": [
            {
                "bucket_id": "bucket_ma",
                "bucket_title": "并购重组",
                "scope": "上市公司并购重组和产业整合。",
            },
            {
                "bucket_id": "bucket_ai",
                "bucket_title": "AI算力链",
                "scope": "AI 芯片、光模块和数据中心。",
            },
        ],
        "theme_bucket_updates": [
            {"canonical_theme": "A股并购重组", "bucket_id": "bucket_ma"},
            {"canonical_theme": "AI算力链", "bucket_id": "bucket_ai"},
        ],
    }
    redis = _MemoryRedis()
    llm = _ConcurrentAssignmentLLM(planning_output)

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_order_store=AssignmentCandidateOrderStore(target="test", redis_client=redis),
        bucket_planner=CommunityBucketPlanner(store=AssignmentBucketStore(target="test", redis_client=redis), llm=llm, model="test-model"),
        bucket_concurrency=2,
        community_id_factory=lambda adapter_name, level, title: f"kgc:{adapter_name}:l{level}:{title}",
    ).build(adapter_name="financial", cards=[card_a, card_b], existing_communities=[])

    assert [request.metadata["task"] for request in llm.requests].count("kg_community_assignment") == 2
    assert llm.max_active_assignments == 2
    assert result.diagnostics["bucket_planning"]["bucket_count"] == 2
    assert result.diagnostics["bucket_concurrency"] == 2
    assert result.diagnostics["bucket_planning"]["bucket_sizes"] == {"bucket_ai": 1, "bucket_ma": 1}


@pytest.mark.asyncio
async def test_community_builder_defers_candidate_checkpoint_between_assignment_calls():
    payload = _card_payload("A股并购重组")
    payload["topic_intents"].append(
        {
            **payload["topic_intents"][0],
            "raw_theme": "AI算力链供需变化",
            "title_candidate": "AI算力链",
            "parent_themes": ["AI算力链"],
            "broad_topics": ["人工智能基础设施"],
            "mid_topics": ["AI芯片供需"],
            "specific_topics": ["AI芯片短缺"],
        }
    )
    card = cognitive_card_from_llm(_chunk(), payload)
    redis = _MemoryRedis()
    order_store = AssignmentCandidateOrderStore(
        target="test",
        redis_client=redis,
        max_base_candidates=1,
        keep_base_candidates=1,
        max_chars=100_000,
    )
    candidate_rows = [
        {
            "community_id": "kg_community:cognitive_topic:l0:1",
            "title": "资本市场改革",
            "origin": "seed",
            "level": 0,
            "scope": "承接 IPO、并购重组、区域股权市场和券商投行。",
            "canonical_labels": ["资本市场改革", "并购重组"],
            "maturity": "seed_reference",
        },
        {
            "community_id": "kg_community:cognitive_topic:l0:2",
            "title": "AI算力链",
            "origin": "seed",
            "level": 0,
            "scope": "承接 AI 芯片、光模块、数据中心。",
            "canonical_labels": ["AI算力链"],
            "maturity": "seed_reference",
        },
    ]
    communities = [
        GraphIndexCommunity(
            community_id=str(row["community_id"]),
            version_id=f"{row['community_id']}:v1",
            adapter_name="financial",
            projection="cognitive_topic",
            level=0,
            parent_community_id="",
            title=str(row["title"]),
            summary=str(row["scope"]),
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=[],
            chunk_ids=[],
            metrics={
                "origin": row["origin"],
                "scope": row["scope"],
                "canonical_labels": row["canonical_labels"],
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
        def __init__(self):
            self.calls = 0

        async def recall(self, **_kwargs):
            self.calls += 1
            return [candidate_rows[self.calls - 1]]

    llm = _LLM(
        [
            lambda request: _attach_assignment(_candidate_alias_by_title(request, "资本市场改革")),
            lambda request: _attach_assignment(_candidate_alias_by_title(request, "AI算力链")),
        ]
    )

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=order_store,
    ).build(adapter_name="financial", cards=[card], existing_communities=communities)

    assert len(llm.requests) == 2
    first_prompt = json.loads(llm.requests[0].prompt)
    second_prompt = json.loads(llm.requests[1].prompt)
    assert [item["title"] for item in _prompt_candidate_bases(first_prompt)] == ["资本市场改革"]
    assert [item["title"] for item in _prompt_candidate_bases(second_prompt)] == ["资本市场改革", "AI算力链"]
    assert llm.requests[0].metadata["candidate_ledger"]["checkpointed"] is False
    assert llm.requests[1].metadata["candidate_ledger"]["checkpointed"] is False
    assert result.diagnostics["candidate_ledger"]["checkpoint_skipped_by_overlap_count"] == 1


@pytest.mark.asyncio
async def test_cognitive_card_extractor_repairs_non_object_output():
    llm = _LLM([["not", "object"], _card_payload()])

    cards = await CognitiveCardExtractor(llm=llm, model="test-model", concurrency=1).extract([_chunk()])

    assert len(cards) == 1
    assert cards[0].source_id == "test:1"
    assert cards[0].topic_intents[0]["raw_theme"] == "并购重组政策推动产业链整合"
    assert llm.requests[0].max_tokens == COGNITIVE_CARD_MAX_TOKENS
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
    return _prompt_candidate_bases(prompt)[0]["community_id"]


def _candidate_alias_by_title(request, title: str) -> str:
    prompt = json.loads(request.prompt)
    for candidate in _prompt_candidate_bases(prompt):
        if candidate.get("title") == title:
            return candidate["community_id"]
    raise AssertionError(f"candidate title not found: {title}")


def _prompt_candidate_bases(prompt: dict) -> list[dict]:
    return [
        item
        for item in prompt.get("candidate_append_log") or []
        if item.get("entry_type") == "candidate_base"
    ]


def test_dedupe_assignment_candidates_merges_duplicate_candidate_payloads():
    candidates = [
        {
            "community_id": "kgc:financial:l0:1",
            "title": "美联储货币政策",
            "retrieval_score": 0.4,
            "retrieval_lane": "semantic:title",
            "canonical_labels": ["美联储货币政策"],
        },
        {
            "community_id": "kgc:financial:l0:1",
            "title": "美联储货币政策",
            "scope": "承接美联储利率决策和美债市场预期。",
            "retrieval_score": 0.9,
            "retrieval_lane": "semantic:labels",
            "canonical_labels": ["美国货币政策", "美联储货币政策"],
        },
    ]

    result = _dedupe_assignment_candidates(candidates)

    assert len(result) == 1
    assert result[0]["community_id"] == "kgc:financial:l0:1"
    assert result[0]["scope"] == "承接美联储利率决策和美债市场预期。"
    assert result[0]["retrieval_score"] == 0.9
    assert result[0]["retrieval_lane"] == "semantic:title|semantic:labels"
    assert result[0]["canonical_labels"] == ["美联储货币政策", "美国货币政策"]


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
        candidate_order_store=_order_store(),
        community_id_factory=lambda adapter_name, level, title: f"kgc:{adapter_name}:l{level}:101",
    ).build(
        adapter_name="financial",
        cards=[card1, card2],
        existing_communities=[],
    )

    assigned_communities = [item for item in result.communities if item.metrics["assigned_intent_count"]]
    assert len(assigned_communities) == 1
    community = assigned_communities[0]
    assert community.community_id == "kgc:financial:l0:101"
    assert community.title == "A股并购重组"
    assert "并购重组政策与产业整合" in community.metrics["future_coverage"]
    assert community.metrics["assigned_intent_count"] == 2
    assert community.metrics["assignment_count"] == 2
    assert community.metrics["source_count"] == 2
    assert community.metrics["unique_source_count"] == 2
    assert community.metrics["evidence_count"] == 2
    assert community.metrics["chunk_count"] == 2
    assert community.metrics["cognitive_card_count"] == 2
    assert community.metrics["avg_assignment_weight"] == 0.9
    assert community.metrics["high_weight_assignment_count"] == 2
    assert community.metrics["topic_diversity_count"] >= 4
    assert len(result.assignments) == 2
    assert result.assignments[1].action == "attach_existing"
    assert result.diagnostics["communities"] == 9


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
        llm=_LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "A股并购重组"))]),
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=_order_store(),
    ).build(
        adapter_name="financial",
        cards=[card],
        existing_communities=[existing],
    )

    community = next(item for item in result.communities if item.community_id == existing_id)
    assert len(community.metrics["assigned_intents"]) == 1
    assert community.metrics["assigned_intent_count"] == 1
    assert community.metrics["assignment_count"] == 1
    assert community.metrics["avg_assignment_weight"] == 0.88
    assert len(community.metrics["cognitive_card_ids"]) == 1


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
    llm = _LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "AI算力链"))])

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

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=_order_store(),
    ).build(
        adapter_name="financial",
        cards=[card],
        existing_communities=seed_communities,
    )

    prompt = llm.requests[0].prompt
    prompt_payload = json.loads(prompt)
    prompt_candidates = _prompt_candidate_bases(prompt_payload)
    assert llm.requests[0].max_tokens == ASSIGNMENT_MAX_TOKENS
    assert all("origin" not in item for item in prompt_candidates)
    assert all("level" not in item for item in prompt_candidates)
    assert "AI算力链" in prompt
    assert any(item["title"] == "AI算力链" and item.get("scope") for item in prompt_candidates)
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
    for index in range(13):
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

    reranker = _Reranker([12, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    result = await CommunityCardBuilder(
        llm=_LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "候选主题12"))]),
        model="test-model",
        candidate_provider=_Provider(),
        reranker_client=reranker,
        candidate_order_store=_order_store(),
    ).build(adapter_name="financial", cards=[card], existing_communities=candidates)

    prompt = result.assignments[0].decision
    assert reranker.calls
    assert reranker.calls[0]["top_n"] == 12
    assert result.assignments[0].community_id == candidates[12].community_id
    assert prompt["assignments"][0]["community_id"] == candidates[12].community_id


@pytest.mark.asyncio
async def test_assignment_prompt_uses_only_recalled_candidates_without_seed_injection():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    seed_communities = seed_graph_communities("financial")
    recalled_seed = next(item for item in seed_communities if item.title == "资本市场改革")
    emergent = GraphIndexCommunity(
        community_id="kgc:financial:l0:99",
        version_id="v99",
        adapter_name="financial",
        projection="cognitive_topic",
        level=0,
        parent_community_id="",
        title="美联储货币政策",
        summary="承接美联储利率决策、政策路径和美债市场预期。",
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=[],
        chunk_ids=[],
        metrics={
            "origin": "emergent",
            "scope": "承接美联储利率决策、政策路径和美债市场预期。",
            "canonical_labels": ["美联储货币政策", "美国货币政策"],
        },
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="lineage-fed",
        previous_community_ids=[],
    )

    recalled = [
        recalled_seed.metrics | {
            "community_id": recalled_seed.community_id,
            "title": recalled_seed.title,
            "origin": "seed",
            "level": recalled_seed.level,
            "scope": recalled_seed.summary,
        },
        {
            "community_id": emergent.community_id,
            "title": emergent.title,
            "origin": "emergent",
            "level": emergent.level,
            "scope": emergent.summary,
            "canonical_labels": ["美联储货币政策", "美国货币政策"],
            "maturity": "single_evidence",
            "retrieval_score": 0.9,
            "retrieval_lane": "semantic:merged",
            "recent_examples": [],
        },
    ]

    class _Provider:
        async def recall(self, **_kwargs):
            return list(recalled)

    llm = _LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "资本市场改革"))])

    await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=_order_store(),
    ).build(
        adapter_name="financial",
        cards=[card],
        existing_communities=[*seed_communities, emergent],
    )

    prompt = json.loads(llm.requests[0].prompt)
    candidate_ids = [item["community_id"] for item in _prompt_candidate_bases(prompt)]
    assert set(candidate_ids) == {recalled_seed.community_id, emergent.community_id}
    assert len(candidate_ids) == len(set(candidate_ids))


@pytest.mark.asyncio
async def test_assignment_prompt_caps_reranked_candidates_sent_to_llm():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    candidates = []
    for index in range(20):
        community = GraphIndexCommunity(
            community_id=f"kg_community:cognitive_topic:l0:cap_{index}",
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

    llm = _LLM([lambda request: _attach_assignment(_first_candidate_alias(request))])
    reranker = _Reranker(list(range(19, -1, -1)))

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        reranker_client=reranker,
        candidate_order_store=_order_store(),
    ).build(adapter_name="financial", cards=[card], existing_communities=candidates)

    prompt = json.loads(llm.requests[0].prompt)
    assert reranker.calls[0]["top_n"] == 12
    prompt_candidates = _prompt_candidate_bases(prompt)
    assert len(prompt_candidates) == 12
    assert {item["title"] for item in prompt_candidates}.issubset(
        {community.title for community in candidates}
    )
    assert result.assignments[0].community_id in {
        candidate["community_id"] for candidate in prompt_candidates
    }


@pytest.mark.asyncio
async def test_assignment_skips_rerank_when_candidate_count_is_at_prompt_cap():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    candidates = []
    for index in range(12):
        community = GraphIndexCommunity(
            community_id=f"kg_community:cognitive_topic:l0:no_rerank_{index}",
            version_id=f"v{index}",
            adapter_name="financial",
            projection="cognitive_topic",
            level=0,
            parent_community_id="",
            title=f"无需精排候选{index}",
            summary=f"无需精排候选{index} summary",
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=[],
            chunk_ids=[],
            metrics={"origin": "emergent", "scope": f"无需精排候选{index} scope", "canonical_labels": [f"无需精排候选{index}"]},
            status="active",
            previous_version_id="",
            change_reason="cognitive_assignment",
            lineage_id=f"lineage-no-rerank-{index}",
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

    llm = _LLM([lambda request: _attach_assignment(_first_candidate_alias(request))])
    reranker = _Reranker(list(range(11, -1, -1)))

    await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        reranker_client=reranker,
        candidate_order_store=_order_store(),
    ).build(adapter_name="financial", cards=[card], existing_communities=candidates)

    prompt = json.loads(llm.requests[0].prompt)
    assert reranker.calls == []
    prompt_candidates = _prompt_candidate_bases(prompt)
    assert len(prompt_candidates) == 12
    assert {item["title"] for item in prompt_candidates} == {
        community.title for community in candidates
    }


@pytest.mark.asyncio
async def test_assignment_prompt_keeps_active_ledger_prefix_even_when_not_recalled_this_turn():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    old_row = {
        "community_id": "kg_community:cognitive_topic:l0:1",
        "title": "AI算力链",
        "origin": "seed",
        "level": 0,
        "scope": "承接 AI 芯片、光模块、数据中心。",
        "canonical_labels": ["AI算力链"],
        "maturity": "seed_reference",
    }
    new_row = {
        "community_id": "kg_community:cognitive_topic:l0:2",
        "title": "资本市场改革",
        "origin": "seed",
        "level": 0,
        "scope": "承接 IPO、并购重组、区域股权市场和券商投行。",
        "canonical_labels": ["资本市场改革", "并购重组"],
        "maturity": "seed_reference",
    }
    order_store = AssignmentCandidateOrderStore(target="test", redis_client=_MemoryRedis())
    order_store.prepare_append_log(adapter_name="financial", candidates=[old_row])

    class _Provider:
        async def recall(self, **_kwargs):
            return [new_row]

    communities = [
        GraphIndexCommunity(
            community_id=str(row["community_id"]),
            version_id=f"{row['community_id']}:v1",
            adapter_name="financial",
            projection="cognitive_topic",
            level=0,
            parent_community_id="",
            title=str(row["title"]),
            summary=str(row["scope"]),
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=[],
            chunk_ids=[],
            metrics={
                "origin": row["origin"],
                "scope": row["scope"],
                "canonical_labels": row["canonical_labels"],
            },
            status="active",
            previous_version_id="",
            change_reason="cognitive_assignment",
            lineage_id="lineage",
            previous_community_ids=[],
        )
        for row in [old_row, new_row]
    ]
    llm = _LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "资本市场改革"))])

    await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=order_store,
    ).build(adapter_name="financial", cards=[card], existing_communities=communities)

    prompt = json.loads(llm.requests[0].prompt)
    assert [item["title"] for item in _prompt_candidate_bases(prompt)] == ["AI算力链", "资本市场改革"]


@pytest.mark.asyncio
async def test_community_builder_raises_when_candidate_ledger_redis_unavailable():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))
    candidate = {
        "community_id": "kg_community:cognitive_topic:l0:1",
        "title": "资本市场改革",
        "origin": "seed",
        "level": 0,
        "scope": "承接 IPO、并购重组、区域股权市场和券商投行。",
        "canonical_labels": ["资本市场改革", "并购重组"],
        "maturity": "seed_reference",
    }
    community = GraphIndexCommunity(
        community_id=candidate["community_id"],
        version_id="v1",
        adapter_name="financial",
        projection="cognitive_topic",
        level=0,
        parent_community_id="",
        title=candidate["title"],
        summary=candidate["scope"],
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=[],
        chunk_ids=[],
        metrics={
            "origin": candidate["origin"],
            "scope": candidate["scope"],
            "canonical_labels": candidate["canonical_labels"],
        },
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="lineage",
        previous_community_ids=[],
    )

    class _Provider:
        async def recall(self, **_kwargs):
            return [candidate]

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await CommunityCardBuilder(
            llm=_LLM([lambda request: _attach_assignment(_candidate_alias_by_title(request, "资本市场改革"))]),
            model="test-model",
            candidate_provider=_Provider(),
            candidate_order_store=AssignmentCandidateOrderStore(target="test", redis_client=_BrokenRedis()),
        ).build(adapter_name="financial", cards=[card], existing_communities=[community])


@pytest.mark.asyncio
async def test_community_builder_requires_candidate_ledger():
    card = cognitive_card_from_llm(_chunk(), _card_payload("A股并购重组"))

    class _Provider:
        async def recall(self, **_kwargs):
            return []

    with pytest.raises(RuntimeError, match="assignment candidate ledger is required"):
        await CommunityCardBuilder(
            llm=_LLM([_create_assignment("A股并购重组")]),
            model="test-model",
            candidate_provider=_Provider(),
            candidate_order_store=None,
        ).build(adapter_name="financial", cards=[card], existing_communities=[])


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

    order_store = AssignmentCandidateOrderStore(target="test", redis_client=_MemoryRedis())
    order_store.prepare_append_log(
        adapter_name="financial",
        candidates=[candidate_rows[2], candidate_rows[0]],
    )

    def save_after_read(request):
        # LLM attaches to the recalled policy seed so the test also verifies alias resolution.
        return _attach_assignment(_candidate_alias_by_title(request, "政策监管与产业扶持"))

    llm = _LLM([save_after_read])

    result = await CommunityCardBuilder(
        llm=llm,
        model="test-model",
        candidate_provider=_Provider(),
        candidate_order_store=order_store,
    ).build(adapter_name="financial", cards=[card], existing_communities=existing_communities)

    request_prompt = json.loads(llm.requests[0].prompt)
    prompt_candidates = _prompt_candidate_bases(request_prompt)
    assert list(request_prompt) == ["candidate_append_log", "topic_intent", "max_attach"]
    assert {item["title"] for item in prompt_candidates} == {
        item["title"] for item in candidate_rows
    }
    assert [item["community_id"] for item in prompt_candidates[:2]] == [
        "kg_community:cognitive_topic:l0:capital_market",
        "kg_community:cognitive_topic:l0:policy",
    ]
    assert "seed_community_catalog" not in request_prompt
    assert "candidate_updates" not in request_prompt
    assert all(not item["community_id"].startswith("c_") for item in prompt_candidates)
    for item in prompt_candidates:
        assert "origin" not in item
        assert "level" not in item
        assert "dynamic_context" not in item
        assert "source_count" not in item
        assert "directory_scope" not in item
        assert "retrieval_score" not in item
        assert "rerank_score" not in item
        assert "recent_examples" not in item
        assert "summary" not in item
        assert "coverage_contract" not in item
        assert "future_coverage" not in item
        if "include_rules" in item:
            assert item["include_rules"]
        if "exclude_rules" in item:
            assert item["exclude_rules"]
    assert any(
        "absorbed_subtopics" in item or "recent_signal_summary" in item or "maturity" in item
        for item in prompt_candidates
    )
    assert result.assignments[0].community_id == "kg_community:cognitive_topic:l0:policy"
