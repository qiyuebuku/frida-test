"""Knowledge-graph LLM task model selection.

The knowledge layer owns task-level model policy. The LLM proxy owns provider
routing for a resolved model name.
"""

from __future__ import annotations

from src.infrastructure.config import settings


def resolve_kg_llm_model(task: str, *, plan_name: str | None = None) -> str:
    """Resolve a KG task to a model name understood by LLM Proxy."""

    forced = (settings.KG_LLM_FORCE_MODEL or "").strip()
    if forced:
        return forced

    plan_key = (plan_name or settings.KG_LLM_PLAN or "").strip()
    plans = settings.KG_LLM_PLANS
    plan = plans.get(plan_key) or plans.get("deepseek_balanced") or {}
    return (
        str(plan.get(task) or plan.get("*") or settings.LLM_PROXY_DEFAULT_MODEL)
        .strip()
        or settings.LLM_PROXY_DEFAULT_MODEL
    )


def kg_llm_config_summary() -> dict:
    """Return non-secret KG LLM routing configuration for notebooks and health checks."""

    plan_name = (settings.KG_LLM_PLAN or "").strip()
    plans = settings.KG_LLM_PLANS
    active_plan = plans.get(plan_name) or {}
    return {
        "kg_llm_plan": plan_name,
        "kg_llm_force_model": settings.KG_LLM_FORCE_MODEL or "",
        "active_plan": dict(active_plan),
        "resolved_models": {
            "financial_news_extraction": resolve_kg_llm_model("financial_news_extraction"),
            "financial_entity_normalization": resolve_kg_llm_model("financial_entity_normalization"),
            "kg_retrieval_controller": resolve_kg_llm_model("kg_retrieval_controller"),
            "kg_candidate_judge": resolve_kg_llm_model("kg_candidate_judge"),
            "kg_agentic_retrieval": resolve_kg_llm_model("kg_agentic_retrieval"),
            "kg_cognitive_card": resolve_kg_llm_model("kg_cognitive_card"),
            "kg_community_assignment": resolve_kg_llm_model("kg_community_assignment"),
            "kg_community_report": resolve_kg_llm_model("kg_community_report"),
            "kg_delta_finding": resolve_kg_llm_model("kg_delta_finding"),
            "kg_finding_evidence_validate": resolve_kg_llm_model("kg_finding_evidence_validate"),
        },
    }
