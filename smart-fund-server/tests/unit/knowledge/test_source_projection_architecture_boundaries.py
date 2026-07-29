"""Architecture guardrails for KG source projection."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_domain_source_projection_does_not_depend_on_infrastructure() -> None:
    source = (
        PROJECT_ROOT / "src/domain/knowledge_adapters/financial/source_projection.py"
    ).read_text(encoding="utf-8")

    assert "src.infrastructure" not in source
    assert "sqlalchemy" not in source
    assert "get_session" not in source


def test_domain_source_classification_does_not_read_environment_or_database() -> None:
    source = (
        PROJECT_ROOT / "src/domain/knowledge_adapters/financial/source_classification.py"
    ).read_text(encoding="utf-8")

    assert "os.getenv" not in source
    assert "src.infrastructure" not in source
    assert "get_session" not in source


def test_interfaces_do_not_import_projection_repository_or_compiler() -> None:
    api_source = (PROJECT_ROOT / "src/interfaces/api/routes/knowledge.py").read_text(encoding="utf-8")
    cli_source = (PROJECT_ROOT / "src/interfaces/cli/knowledge.py").read_text(encoding="utf-8")

    for source in [api_source, cli_source]:
        assert "knowledge_source_projection_repository" not in source
        assert "KnowledgeCompiler" not in source
        assert "get_session" not in source


def test_compiler_does_not_reference_business_tables() -> None:
    source = (PROJECT_ROOT / "src/domain/knowledge/compiler.py").read_text(encoding="utf-8")

    for token in ["ft_news", "ft_market_flow", "ft_market_cache", "ft_sentiment", "ft_macro_indicators"]:
        assert token not in source
