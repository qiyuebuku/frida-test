from __future__ import annotations

from dataclasses import replace

import pytest

from src.domain.knowledge.relation_graph_cognition import (
    CommunityCardMaterial,
    CommunityCognitionMaterial,
    CommunityEdgeMaterial,
    fact_semantic_version,
    parse_conditional_projections,
    parse_fact_report,
    projection_semantic_version,
    projection_target_id,
    render_projection_text,
)


def _material() -> CommunityCognitionMaterial:
    return CommunityCognitionMaterial(
        community_id="kgc:financial:relation:test",
        adapter_name="financial",
        graph_fingerprint="fingerprint",
        graph_version=1,
        cards=(
            CommunityCardMaterial(
                "c0001",
                "card:1",
                "原因发生",
                source_alias="s0001",
                source_published_at="2026-07-26T01:00:00+00:00",
                fact_card_count=3,
            ),
            CommunityCardMaterial(
                "c0002",
                "card:2",
                "结果发生",
                source_alias="s0001",
                source_published_at="2026-07-26T01:00:00+00:00",
            ),
        ),
        edges=(
            CommunityEdgeMaterial(
                alias="e0001",
                edge_id="edge:1",
                source_card_alias="c0001",
                target_card_alias="c0002",
                relation_kind="causal_influence",
                relation_type="因果影响",
                direction="card:1 -> card:2",
                decision_class="observed",
                basis="原文明确说明原因导致结果。",
            ),
        ),
    )


def test_fact_report_requires_complete_component_references() -> None:
    report = parse_fact_report(
        {
            "title": "原因与结果",
            "report_text": "原因发生后，结果随之出现。",
            "referenced_card_aliases": ["c0001", "c0002"],
            "referenced_edge_aliases": ["e0001"],
        },
        _material(),
    )

    assert report.referenced_card_ids == ("card:1", "card:2")
    assert report.referenced_edge_ids == ("edge:1",)


def test_llm_payload_uses_aliases_without_stable_graph_ids() -> None:
    fact_payload = _material().fact_payload()
    projection_payload = _material().projection_payload(
        title="原因与结果",
        fact_report="原因发生后，结果随之出现。",
    )
    serialized = str((fact_payload, projection_payload))

    assert "card:1" not in serialized
    assert "edge:1" not in serialized
    assert _material().community_id not in serialized
    assert fact_payload["cards"][0]["alias"] == "c0001"
    assert fact_payload["cards"][0]["fact_card_count"] == 3
    assert "fact_card_count" not in fact_payload["cards"][1]
    assert fact_payload["relations"]["observed"][0]["alias"] == "e0001"
    assert "decision_class" not in fact_payload["relations"]["observed"][0]
    assert "source_alias" not in fact_payload["cards"][0]
    assert "source_published_at" not in fact_payload["cards"][0]
    assert fact_payload["source_context"] == {
        "source_record_count": 1,
        "sources": [
            {
                "source_alias": "s0001",
                "card_aliases": ["c0001", "c0002"],
                "source_published_at": "2026-07-26T01:00:00+00:00",
            }
        ],
    }


def test_fact_payload_groups_distinct_source_records() -> None:
    material = _material()
    cards = (
        material.cards[0],
        replace(
            material.cards[1],
            source_alias="s0002",
            source_published_at="2026-07-26T02:00:00+00:00",
        ),
    )

    context = replace(material, cards=cards).source_context()

    assert context["source_record_count"] == 2
    assert context["sources"][0]["card_aliases"] == ["c0001"]
    assert context["sources"][1]["card_aliases"] == ["c0002"]


def test_edge_prompt_omits_duplicate_inference_mechanism() -> None:
    edge = _material().edges[0]
    repeated = replace(
        edge,
        decision_class="inferred",
        inference_mechanism="  原文明确说明原因导致结果。 ",
    )
    distinct = replace(
        repeated,
        inference_mechanism="原因变化可能通过供给约束影响结果。",
    )

    assert "inference_mechanism" not in repeated.prompt_dict()
    assert (
        distinct.prompt_dict()["inference_mechanism"]
        == "原因变化可能通过供给约束影响结果。"
    )


def test_fact_report_rejects_omitted_edge() -> None:
    with pytest.raises(ValueError, match="未覆盖完整关系子图"):
        parse_fact_report(
            {
                "title": "原因与结果",
                "report_text": "原因发生后，结果随之出现。",
                "referenced_card_aliases": ["c0001", "c0002"],
                "referenced_edge_aliases": [],
            },
            _material(),
        )


def test_projection_allows_empty_result() -> None:
    assert parse_conditional_projections({"projections": []}, _material()) == ()


def test_projection_maps_aliases_to_stable_graph_ids() -> None:
    projections = parse_conditional_projections(
        {
            "projections": [
                {
                    "conditional_judgement": "若原因持续，结果可能延续。",
                    "conditions": ["原因持续存在"],
                    "possible_result": "结果继续发展",
                    "observation_indicators": ["结果指标继续上升"],
                    "invalidation_conditions": ["原因消失"],
                    "time_horizon": "未来一个月",
                    "supporting_card_aliases": ["c0001", "c0002"],
                    "supporting_edge_aliases": ["e0001"],
                }
            ]
        },
        _material(),
    )

    assert projections[0].supporting_card_ids == ("card:1", "card:2")
    assert projections[0].supporting_edge_ids == ("edge:1",)
    assert "成立条件" in render_projection_text("原因与结果", projections)
    assert projection_target_id(_material().community_id).endswith(":projection")


def test_projection_rejects_external_alias() -> None:
    with pytest.raises(ValueError, match="不属于当前 Community"):
        parse_conditional_projections(
            {
                "projections": [
                    {
                        "conditional_judgement": "条件性判断",
                        "conditions": ["条件"],
                        "possible_result": "结果",
                        "observation_indicators": ["指标"],
                        "invalidation_conditions": ["失效"],
                        "time_horizon": "未来一段时间",
                        "supporting_card_aliases": ["c9999"],
                        "supporting_edge_aliases": ["e0001"],
                    }
                ]
            },
            _material(),
        )


def test_projection_rejects_alias_leaked_into_natural_language() -> None:
    with pytest.raises(ValueError, match="泄漏内部 alias"):
        parse_conditional_projections(
            {
                "projections": [
                    {
                        "conditional_judgement": "若 c0001 持续，则结果延续。",
                        "conditions": ["原因持续存在"],
                        "possible_result": "结果继续发展",
                        "observation_indicators": ["结果指标继续上升"],
                        "invalidation_conditions": ["原因消失"],
                        "time_horizon": "未明确",
                        "supporting_card_aliases": ["c0001", "c0002"],
                        "supporting_edge_aliases": ["e0001"],
                    }
                ]
            },
            _material(),
        )


def test_semantic_versions_fit_database_watermark_columns() -> None:
    fact = fact_semantic_version(
        graph_fingerprint="f" * 64,
        report_version=12,
    )
    projection = projection_semantic_version(
        graph_fingerprint="f" * 64,
        fact_report_version=12,
        projection_version=3,
    )

    assert len(fact) == 64
    assert len(projection) == 64
    assert fact != projection
