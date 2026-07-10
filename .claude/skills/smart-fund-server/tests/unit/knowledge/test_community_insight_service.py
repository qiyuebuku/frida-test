"""Community Insight material construction tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import src.application.services.community_insight_service as insight_module
from src.application.services.community_insight_service import (
    COMMUNITY_INSIGHT_SYSTEM_PROMPT,
    CommunityInsightService,
    _build_prompt,
    _dedupe_materials,
    _display_source_id,
    _filter_grounded_materials,
    _insight_materials_payload,
    _insight_retrieval_text,
    _intent_contexts,
    _material_from_metric_assignment,
    _quality_diagnostics,
    _quality_retry_reason,
    _trim_material,
)


def test_insight_prompt_separates_stable_system_prompt_from_dynamic_input():
    community = SimpleNamespace(
        community_id="kgc:financial:l0:1",
        title="半导体产业链",
        adapter_name="financial",
        projection="cognitive_topic",
        evidence_ids=[],
        metrics={"scope": "半导体产业链事件"},
    )
    prompt = _build_prompt(
        community,
        [
            {
                "source_id": "ft_news:1",
                "title_candidate": "半导体设备订单",
                "evidence_span": "半导体设备订单增长",
            }
        ],
    )

    assert "写作要求" in COMMUNITY_INSIGHT_SYSTEM_PROMPT
    assert "不要输出证据清单" in COMMUNITY_INSIGHT_SYSTEM_PROMPT
    assert "写作要求" not in prompt
    assert "不要输出证据清单" not in prompt
    assert prompt.lstrip().startswith("{")
    payload = json.loads(prompt)
    assert payload["materials"][0]["source"] == "ft_news:1"
    assert payload["materials"][0]["evidence"] == "半导体设备订单增长"
    assert "source_id" not in payload["materials"][0]
    assert "evidence_span" not in payload["materials"][0]
    assert "why_in_community" not in payload["materials"][0]
    assert "fit" not in payload["materials"][0]


def test_insight_prompt_keeps_community_signals_compact():
    community = SimpleNamespace(
        community_id="kgc:financial:l0:1",
        title="资管行业产品业绩与理财市场",
        adapter_name="financial",
        projection="cognitive_topic",
        evidence_ids=[],
        metrics={
            "scope": "资管产品和理财市场",
            "parent_themes": ["资管行业", "理财市场"],
            "topic_tags": ["ETF规模变化", "资管行业"],
            "event_threads": ["不应进入prompt"],
            "impact_tags": ["黄金ETF", "银行理财"],
            "risk_tags": ["流动性风险"],
            "future_coverage": ["不应进入prompt"],
            "coverage_contract": "不应进入prompt",
        },
    )
    prompt = _build_prompt(
        community,
        [
            {
                "source_id": "ft_news:1",
                "title_candidate": "ETF市场规模格局变化",
                "parent_themes": ["资管行业", "理财市场"],
                "broad_topics": ["ETF规模变化", "资管行业"],
                "impact_target": ["黄金ETF", "银行理财"],
                "risk_type": ["流动性风险"],
                "evidence_span": "黄金ETF规模阶段性超过沪深300ETF。",
            }
        ],
    )
    payload = json.loads(prompt)

    assert payload["community_signals"]["topic_profile"] == ["资管行业", "理财市场", "ETF规模变化"]
    assert payload["community_signals"]["key_targets"] == ["黄金ETF", "银行理财"]
    assert payload["community_signals"]["risk_hints"] == ["流动性风险"]
    assert "event_threads" not in payload["community_signals"]
    assert "future_coverage" not in payload["community_signals"]
    assert "coverage_contract" not in payload["community_signals"]


def test_insight_prompt_counts_only_final_materials():
    community = SimpleNamespace(
        community_id="kgc:financial:l0:1",
        title="半导体产业链",
        adapter_name="financial",
        projection="cognitive_topic",
        evidence_ids=["ev-old-1", "ev-old-2", "ev-old-3"],
        metrics={
            "scope": "半导体产业链事件",
            "source_count": 99,
            "cognitive_card_count": 88,
            "assignment_count": 77,
            "earliest_source_published_at": "2020-01-01T00:00:00+00:00",
            "latest_source_published_at": "2030-01-01T00:00:00+00:00",
        },
    )

    payload = json.loads(
        _build_prompt(
            community,
            [
                {
                    "source_id": "ft_news:1",
                    "cognitive_card_id": "card-1",
                    "assignment_id": "assignment-1",
                    "source_published_at": "2026-07-01T10:00:00+00:00",
                    "evidence_span": "材料一",
                },
                {
                    "source_id": "ft_news:2",
                    "cognitive_card_id": "card-2",
                    "assignment_id": "assignment-2",
                    "source_published_at": "2026-07-02T10:00:00+00:00",
                    "evidence_span": "材料二",
                },
            ],
        )
    )

    assert payload["community"]["source_count"] == 2
    assert payload["community"]["cognitive_card_count"] == 2
    assert payload["community"]["assignment_count"] == 2
    assert payload["community"]["time_range"] == {
        "start": "2026-07-01T10:00:00+00:00",
        "end": "2026-07-02T10:00:00+00:00",
    }


def test_load_materials_uses_pg_assignments_instead_of_community_metric_copies(monkeypatch):
    assignment = SimpleNamespace(
        assignment_id="assignment-pg",
        adapter_name="financial",
        cognitive_card_id="card-pg",
        intent_index=1,
        intent_id="card-pg:intent:1",
        community_id="kgc:financial:l0:1",
        action="attach_existing",
        weight=0.9,
        confidence=0.8,
        reason="fit_type=new_subtopic; 归属理由",
        topic_intent={
            "source_id": "ft_news:1",
            "source_published_at": "2026-07-01T10:00:00+00:00",
            "title_candidate": "半导体设备景气",
            "evidence_span": "半导体设备订单增长。",
        },
        decision={"assignments": []},
        status="active",
    )

    class _Scalars:
        def all(self):
            return [assignment]

    class _Session:
        def scalars(self, _query):
            return _Scalars()

    @contextmanager
    def _fake_get_session(_target):
        yield _Session()

    monkeypatch.setattr(insight_module, "get_session", _fake_get_session)
    community = SimpleNamespace(
        community_id="kgc:financial:l0:1",
        adapter_name="financial",
        metrics={
            "assignments": [
                {
                    "assignment_id": "assignment-metric",
                    "source_id": "ft_news:999",
                    "evidence_span": "不应读取的 metrics 副本。",
                }
            ]
        },
    )
    service = object.__new__(CommunityInsightService)
    service._target = "prod"

    materials = service._load_materials(community)

    assert len(materials) == 1
    assert materials[0]["assignment_id"] == "assignment-pg"
    assert materials[0]["source_id"] == "ft_news:1"


def test_metric_assignment_material_keeps_evidence_attachment_unit_only():
    intent = {
        "intent_id": "card-1:intent:1",
        "cognitive_card_id": "card-1",
        "source_id": "ft_news:1",
        "source_published_at": "2026-07-06T10:00:00+00:00",
        "title_candidate": "半导体产业链",
        "evidence_span": "半导体设备订单增长，带动产业链景气改善",
        "evidence_support": 0.92,
        "event_classification": {
            "event_domain": "company_operation",
            "event_scope": "industry",
            "market_relevance": "high",
            "importance": 0.82,
        },
        "driver": ["设备订单增长"],
        "impact_target": ["半导体产业链"],
        "topic_intents": [{"should": "not_enter_material"}],
        "risk_signals": [{"should": "not_enter_material"}],
    }
    assignment = {
        "assignment_id": "assign-1",
        "intent_id": "card-1:intent:1",
        "cognitive_card_id": "card-1",
        "action": "attach_existing",
        "community_id": "kgc:financial:l0:1",
        "resolved_community_id": "kgc:financial:l0:1",
        "weight": 0.9,
        "confidence": 0.88,
        "fit_type": "new_subtopic",
        "reason": "证据直接落入半导体产业链主题。",
        "insight_delta": "补充半导体设备订单向产业链景气传导的核心证据。",
    }

    material = _trim_material(_material_from_metric_assignment(assignment, _intent_contexts([intent])))
    payload = _insight_materials_payload([material])

    assert material["evidence_span"] == "半导体设备订单增长，带动产业链景气改善"
    assert material["insight_delta"] == "补充半导体设备订单向产业链景气传导的核心证据。"
    assert "topic_intents" not in material
    assert "risk_signals" not in material
    assert payload[0]["evidence"] == "半导体设备订单增长，带动产业链景气改善"
    assert "claim" not in payload[0]
    assert payload[0]["importance"] == 0.82
    assert "why_in_community" not in payload[0]
    assert "event_classification" not in payload[0]
    assert "card_summary" not in payload[0]
    assert "insight_delta" not in payload[0]


def test_dedupe_materials_collapses_demo_wrapped_ft_news_source_ids():
    materials = [
        {
            "source_id": "ft_news:109283",
            "assignment_weight": 1.0,
            "assignment_confidence": 0.8,
            "evidence_span": "短证据",
        },
        {
            "source_id": "community_insight_e2e:20260707T074543:03c8630f:ft_news:109283",
            "assignment_weight": 0.9,
            "assignment_confidence": 0.9,
            "evidence_span": "更完整的上市公司理财认购额下降证据",
            "insight_delta": "强化企业资金管理收缩判断",
        },
        {
            "source_id": "ft_news:109284",
            "assignment_weight": 0.7,
            "assignment_confidence": 0.7,
            "evidence_span": "另一条新闻",
        },
    ]

    deduped = _dedupe_materials(materials)

    assert len(deduped) == 2
    assert any(item["source_id"] == "community_insight_e2e:20260707T074543:03c8630f:ft_news:109283" for item in deduped)
    assert any(item["source_id"] == "ft_news:109284" for item in deduped)


def test_filter_grounded_materials_requires_exact_evidence_only():
    materials = [
        {
            "source_id": "ft_news:1",
            "evidence_span": "半导体设备订单增长，带动产业链景气改善。",
        },
        {
            "source_id": "ft_news:2",
            "evidence_span": "只有原文证据，没有事实判断。",
            "driver": ["设备订单增长"],
        },
        {
            "source_id": "ft_news:3",
            "legacy_claim": "只有事实判断，没有原文证据。",
            "impact_target": ["半导体产业链"],
        },
        {
            "source_id": "ft_news:4",
            "driver": ["只有标签信号"],
            "impact_target": ["半导体产业链"],
        },
    ]

    grounded = _filter_grounded_materials(materials)

    assert [item["source_id"] for item in grounded] == ["ft_news:1", "ft_news:2"]


def test_insight_material_payload_uses_canonical_ft_news_source_ref():
    material = {
        "source_id": "community_insight_e2e:20260707T074543:03c8630f:ft_news:109282",
        "title_candidate": "银行理财REITs投资挑战",
        "evidence_span": "REITs流动性弱于传统债券。",
    }

    payload = _insight_materials_payload([material])

    assert _display_source_id(material) == "ft_news:109282"
    assert payload[0]["source"] == "ft_news:109282"
    assert "community_insight_e2e" not in json.dumps(payload, ensure_ascii=False)


def test_quality_diagnostics_no_longer_requires_basis_fields():
    report = "这是一份面向 Agent 的整合型认知报告。" * 8
    report_json = {
        "summary": "多条材料共同说明半导体产业链景气改善。",
        "findings": [
            {
                "summary": "订单增长与产业链景气形成相互印证",
                "explanation": "设备订单和产业链景气改善分别从需求和传导角度支持同一判断。",
                "supporting_sources": ["ft_news:1", "ft_news:2"],
            }
        ],
        "key_entities": ["半导体设备", "半导体产业链"],
        "key_relationships": ["设备订单增长 -> 产业链景气改善"],
    }

    diagnostics = _quality_diagnostics(report=report, report_json=report_json)

    assert diagnostics["warnings"] == []
    assert "basis_count" not in diagnostics
    assert diagnostics["finding_count"] == 1
    assert diagnostics["key_relationship_count"] == 1


def test_quality_diagnostics_requires_graphrag_report_json():
    report = "这是一份面向 Agent 的整合型认知报告。" * 8

    diagnostics = _quality_diagnostics(report=report, report_json={})

    assert "report_json_missing_summary" in diagnostics["warnings"]
    assert "report_json_missing_findings" in diagnostics["warnings"]
    assert "report_json_missing_key_relationships" in diagnostics["warnings"]
    assert "跨材料发现" in _quality_retry_reason(report=report, report_json={})


def test_quality_diagnostics_flags_advice_tone():
    report = "这些材料共同说明行业供需结构正在变化。" * 6 + "建议投资者关注相关配置价值。"

    diagnostics = _quality_diagnostics(report=report, report_json={})

    assert "report_contains_advice_tone" in diagnostics["warnings"]
    assert "建议口吻" in _quality_retry_reason(report=report, report_json={})


def test_quality_diagnostics_flags_category_listing():
    report = (
        "第一，产品发行端回暖。\n"
        "第二，产品业绩分化。\n"
        "第三，ETF规模变化。\n"
        "第四，REITs投资增加。"
    )

    diagnostics = _quality_diagnostics(report=report, report_json={})

    assert "report_looks_like_category_listing" in diagnostics["warnings"]


def test_insight_retrieval_text_indexes_findings_and_relationships():
    insight = SimpleNamespace(
        title="半导体产业链",
        community_id="kgc:financial:l0:1",
        insight_full_report="半导体产业链报告正文。",
        report_json={
            "summary": "多条材料共同说明半导体产业链景气改善。",
            "findings": [
                {
                    "summary": "订单增长与产业链景气形成相互印证",
                    "explanation": "设备订单和产业链景气改善分别从需求和传导角度支持同一判断。",
                    "supporting_sources": ["ft_news:1", "ft_news:2"],
                }
            ],
            "key_entities": ["半导体设备", "半导体产业链"],
            "key_relationships": ["设备订单增长 -> 产业链景气改善"],
        },
    )

    text = _insight_retrieval_text(insight)

    assert "Finding 1" in text
    assert "ft_news:1" in text
    assert "Key Relationships" in text
    assert "设备订单增长 -> 产业链景气改善" in text
