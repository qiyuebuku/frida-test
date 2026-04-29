"""Architecture boundary tests for knowledge API service layer."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_knowledge_route_stays_thin() -> None:
    source = (PROJECT_ROOT / "src/interfaces/api/routes/knowledge.py").read_text(encoding="utf-8")

    forbidden = [
        "KnowledgeCompiler",
        "FinancialKGAdapter",
        "KnowledgeRepositoryImpl",
        "sqlalchemy",
        "src.domain.knowledge.compiler",
        "src.domain.knowledge_adapters.financial",
        "src.infrastructure.persistence.repositories",
    ]
    for token in forbidden:
        assert token not in source


def test_domain_knowledge_does_not_depend_on_interfaces_or_infrastructure() -> None:
    for path in (PROJECT_ROOT / "src/domain/knowledge").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "fastapi" not in source
        assert "click" not in source
        assert "src.interfaces" not in source
        assert "src.infrastructure" not in source
