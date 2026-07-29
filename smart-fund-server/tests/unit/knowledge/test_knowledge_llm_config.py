from src.application.services import knowledge_llm_config as config


def test_resolve_kg_llm_model_uses_active_plan(monkeypatch):
    monkeypatch.setattr(config.settings, "KG_LLM_FORCE_MODEL", "")
    monkeypatch.setattr(config.settings, "KG_LLM_PLAN", "deepseek_balanced")
    monkeypatch.setattr(
        config.settings,
        "KG_LLM_PLANS",
        {
            "deepseek_balanced": {
                "*": "deepseek-v4-flash",
                "kg_graph_community_report": "deepseek-v4-pro",
            }
        },
    )

    assert config.resolve_kg_llm_model("financial_news_extraction") == "deepseek-v4-flash"
    assert config.resolve_kg_llm_model("kg_graph_community_report") == "deepseek-v4-pro"


def test_community_insight_uses_pro_model_by_default(monkeypatch):
    monkeypatch.setattr(config.settings, "KG_LLM_FORCE_MODEL", "")
    monkeypatch.setattr(config.settings, "KG_LLM_PLAN", "deepseek_balanced")

    assert config.settings.KG_LLM_PLANS["deepseek_cheap"]["kg_community_insight"] == "deepseek-v4-pro"
    assert config.settings.KG_LLM_PLANS["deepseek_balanced"]["kg_community_insight"] == "deepseek-v4-pro"
    assert config.resolve_kg_llm_model("kg_community_insight") == "deepseek-v4-pro"


def test_resolve_kg_llm_model_force_model_wins(monkeypatch):
    monkeypatch.setattr(config.settings, "KG_LLM_FORCE_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(config.settings, "KG_LLM_PLAN", "glm_subscription")
    monkeypatch.setattr(
        config.settings,
        "KG_LLM_PLANS",
        {"glm_subscription": {"*": "glm-5.1"}},
    )

    assert config.resolve_kg_llm_model("kg_graph_community_report") == "deepseek-v4-flash"
