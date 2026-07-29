from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "知识图谱"
    / "06_relation_graph_agent_scenario_validation.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "relation_graph_agent_scenario_validation",
        SCRIPT_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_final_payload_requires_grounding_fields():
    module = _load_module()

    result = module._parse_final_payload(
        """```json
        {
          "answer": "存在因果关系",
          "card_ids": ["kg_cognitive_card:a"],
          "edge_ids": ["kg_card_relation:e"],
          "community_ids": [],
          "insufficient_evidence": false
        }
        ```"""
    )

    assert result["edge_ids"] == ["kg_card_relation:e"]
    natural = module._parse_final_payload(
        "存在因果关系 "
        "[Card:kg_cognitive_card:a] "
        "[Edge:kg_card_relation:e]"
    )
    assert natural["card_ids"] == ["kg_cognitive_card:a"]
    assert natural["edge_ids"] == ["kg_card_relation:e"]
    assert module._parse_final_payload("") is None


def test_evaluate_case_rejects_unopened_or_hallucinated_references():
    module = _load_module()
    case = {
        "expected_card_ids": ["kg_cognitive_card:a", "kg_cognitive_card:b"],
        "expected_edge_ids": ["kg_card_relation:e"],
        "expected_relation_kinds": ["causal_influence"],
        "required_tools": [
            "kg_relation_graph_search",
            "kg_card_expand",
            "kg_edge_open",
        ],
    }
    transcript = [
        {
            "tool_name": "kg_relation_graph_search",
            "output": {
                "cards": [
                    {"card_id": "kg_cognitive_card:a"},
                    {"card_id": "kg_cognitive_card:b"},
                ]
            },
        },
        {
            "tool_name": "kg_card_expand",
            "output": {
                "edges": [
                    {
                        "edge_id": "kg_card_relation:e",
                        "relation_kind": "causal_influence",
                        "source_card_id": "kg_cognitive_card:a",
                        "target_card_id": "kg_cognitive_card:b",
                    }
                ]
            },
        },
    ]
    final_payload = {
        "answer": "存在因果关系",
        "card_ids": ["kg_cognitive_card:a", "kg_cognitive_card:b"],
        "edge_ids": ["kg_card_relation:e", "kg_card_relation:fake"],
        "community_ids": [],
        "insufficient_evidence": False,
    }

    result = module.evaluate_case(
        case,
        transcript=transcript,
        final_payload=final_payload,
        failure="",
    )

    assert result["passed"] is False
    assert result["checks"]["expected_edges_opened"] is False
    assert result["checks"]["cited_edges_are_grounded"] is False
    assert result["hallucinated_edge_ids"] == ["kg_card_relation:fake"]


def test_evaluate_hard_negative_requires_insufficient_evidence():
    module = _load_module()
    case = {
        "expected_insufficient_evidence": True,
        "required_tools": ["kg_relation_graph_search"],
    }
    transcript = [
        {
            "tool_name": "kg_relation_graph_search",
            "output": {"cards": []},
        }
    ]

    accepted = module.evaluate_case(
        case,
        transcript=transcript,
        final_payload={
            "answer": "图中没有直接关系证据。",
            "card_ids": [],
            "edge_ids": [],
            "community_ids": [],
            "insufficient_evidence": True,
        },
        failure="",
    )
    rejected = module.evaluate_case(
        case,
        transcript=transcript,
        final_payload={
            "answer": "存在关系。",
            "card_ids": [],
            "edge_ids": [],
            "community_ids": [],
            "insufficient_evidence": False,
        },
        failure="",
    )

    assert accepted["passed"] is True
    assert rejected["passed"] is False
