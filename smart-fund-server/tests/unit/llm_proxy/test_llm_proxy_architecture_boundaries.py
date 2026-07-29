from pathlib import Path


LLM_PROXY_DIR = Path("src/infrastructure/llm_proxy")


def _python_sources():
    return [
        path
        for path in LLM_PROXY_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_llm_proxy_does_not_import_knowledge_domain():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _python_sources())

    assert "src.domain.knowledge" not in combined
    assert "knowledge_adapters" not in combined
    assert "knowledge_service" not in combined


def test_llm_proxy_does_not_import_event_extraction():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _python_sources())

    assert "src.domain.extraction" not in combined


def test_api_route_does_not_import_specific_provider():
    route = Path("src/interfaces/api/routes/llm_proxy.py").read_text(encoding="utf-8")

    assert "providers.deepseek_openai" not in route
    assert "providers.claude_tmux" not in route
