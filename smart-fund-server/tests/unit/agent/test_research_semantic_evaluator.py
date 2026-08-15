from src.application.agents.financial_research.quality_evaluator import (
    ResearchQualityScores,
    merge_semantic_quality,
)
from src.application.agents.financial_research.semantic_evaluator import (
    SemanticResearchEvaluation,
    _decode_nested_json_strings,
    submit_semantic_evaluation,
    create_semantic_evaluator_agent,
)


def _evaluation() -> SemanticResearchEvaluation:
    return SemanticResearchEvaluation.model_validate({
        "run_id": "research-run-1",
        "model": "glm-5.2",
        "scores": {
            "evidence_entailment": 8,
            "counterevidence_directness": 7,
            "forecast_calibration": 5,
            "source_independence": 6,
            "narrative_selection_bias": 8,
            "mechanism_completeness": 7,
            "decision_value": 8,
        },
        "claim_citation_assessments": [{
            "claim_id": "claim-1",
            "reference": "market_ref:M1",
            "verdict": "partially_supports",
            "unsupported_part": "无法证明未来延续",
            "rationale": "引用只包含当前行情。",
        }],
        "evidence_lineage_groups": ["行情快照组：market_ref:M1"],
        "strengths": ["事实对象明确"],
        "defects": ["预测校准不足"],
        "recommended_research_actions": ["补充历史条件样本"],
        "confidence": 0.8,
    })


def test_semantic_evaluator_tool_schema_requires_substantive_scores() -> None:
    schema = submit_semantic_evaluation.params_json_schema

    assert "scores" in schema["required"]
    assert "claim_citation_assessments" in schema["properties"]


def test_semantic_evaluator_limits_output_and_requires_scores_first() -> None:
    agent = create_semantic_evaluator_agent(model="glm-5.3")

    assert agent.model_settings.max_tokens == 12_000
    assert "第一个顶层字段必须是完整的 scores" in agent.instructions


def test_semantic_evaluator_normalizes_provider_stringified_nested_json() -> None:
    value = _decode_nested_json_strings({
        "scores": '{"evidence_entailment":8}',
        "groups": '[ ["market_ref:M1"] ]',
    })

    assert value["scores"] == {"evidence_entailment": 8}
    assert value["groups"] == [["market_ref:M1"]]


def test_semantic_scores_replace_heuristic_semantic_dimensions() -> None:
    deterministic = ResearchQualityScores(
        evidence_entailment=10,
        historical_calibration=8,
        counterevidence_directness=10,
        mechanism_and_source_quality=10,
        market_structure_and_pricing=10,
        portfolio_decision_value=10,
        exploration_depth=10,
        clarity_and_structure=10,
    )

    merged, overall, grade = merge_semantic_quality(
        deterministic, _evaluation()
    )

    assert merged.evidence_entailment == 8
    assert merged.historical_calibration == 5
    assert merged.counterevidence_directness == 7
    assert overall < 90
    assert grade == "needs_improvement"
