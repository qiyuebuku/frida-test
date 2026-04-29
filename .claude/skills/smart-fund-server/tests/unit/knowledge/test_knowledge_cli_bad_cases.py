"""Tests for KG bad case CLI helpers."""

from __future__ import annotations

import json

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
