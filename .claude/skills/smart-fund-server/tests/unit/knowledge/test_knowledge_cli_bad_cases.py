"""Tests for KG bad case CLI helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.interfaces.cli.knowledge import _load_bad_case_cases


def test_load_bad_case_cases_from_file(tmp_path) -> None:
    path = tmp_path / "bad_cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "catl-events",
                        "query": "宁德时代 300750 最近受哪些事件影响",
                        "expected_hit_titles": ["news_articles:ft_news:74342"],
                        "expected_top_hit_titles": ["news_articles:ft_news:74342"],
                        "top_k": 3,
                        "expected_node_names": ["宁德时代"],
                        "expected_relation_types": ["mentions"],
                        "expected_channels_used": ["semantic_hybrid_search"],
                        "min_hits": 1,
                        "min_evidence_refs": 1,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = _load_bad_case_cases(path)

    assert len(cases) == 1
    assert cases[0].case_id == "catl-events"
    assert cases[0].expected_hit_titles == ["news_articles:ft_news:74342"]
    assert cases[0].expected_top_hit_titles == ["news_articles:ft_news:74342"]
    assert cases[0].top_k == 3
    assert cases[0].expected_node_names == ["宁德时代"]
    assert cases[0].expected_channels_used == ["semantic_hybrid_search"]
    assert cases[0].min_hits == 1
    assert cases[0].min_evidence_refs == 1


def test_financial_bad_case_suite_contains_expanded_coverage_cases() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    path = (
        repo_root
        / "docs/5. 设计方案/1. 知识图谱/bad_cases/financial_retrieval_bad_cases.json"
    )

    cases = _load_bad_case_cases(path)
    case_ids = {case.case_id for case in cases}

    assert "financial-catl-recent-events" in case_ids
    assert "financial-ma-industry-impact" in case_ids
    assert "financial-overseas-capacity-company-impact" in case_ids
    assert "financial-middle-east-conflict-assets" in case_ids
    assert "financial-low-rate-beneficiaries" in case_ids
    assert all(case.min_hits >= 1 for case in cases)
    assert all(case.min_evidence_refs >= 1 for case in cases)
