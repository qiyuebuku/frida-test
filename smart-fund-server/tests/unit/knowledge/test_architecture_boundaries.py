"""Architecture boundary checks for the knowledge package."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_DIR = PROJECT_ROOT / "src" / "domain" / "knowledge"
API_ROUTE = PROJECT_ROOT / "src" / "interfaces" / "api" / "routes" / "knowledge.py"
CLI_ROUTE = PROJECT_ROOT / "src" / "interfaces" / "cli" / "knowledge.py"
ARCH_DOCS = [
    PROJECT_ROOT / "docs" / "2. 架构设计" / "系统架构.md",
    PROJECT_ROOT / "docs" / "2. 架构设计" / "业务架构.md",
]


def _python_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _assert_no_forbidden_imports(path: Path, forbidden: list[str]) -> None:
    imports = _imports(path)
    violations = [
        name
        for name in imports
        for prefix in forbidden
        if name == prefix or name.startswith(f"{prefix}.")
    ]
    assert not violations, f"{path} has forbidden imports: {violations}"


def test_domain_knowledge_exists() -> None:
    assert DOMAIN_DIR.is_dir()
    assert (DOMAIN_DIR / "__init__.py").is_file()


def test_domain_knowledge_does_not_import_outer_layers_or_drivers() -> None:
    forbidden = [
        "src.infrastructure",
        "src.interfaces",
        "sqlalchemy",
        "psycopg2",
        "asyncpg",
    ]
    for path in _python_files(DOMAIN_DIR):
        _assert_no_forbidden_imports(path, forbidden)


def test_interface_routes_do_not_import_persistence_or_db_drivers() -> None:
    forbidden = [
        "src.infrastructure.persistence",
        "sqlalchemy",
        "psycopg2",
        "asyncpg",
    ]
    for path in (API_ROUTE, CLI_ROUTE):
        assert path.is_file()
        _assert_no_forbidden_imports(path, forbidden)


def test_architecture_docs_describe_knowledge_layer() -> None:
    for path in ARCH_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "Knowledge" in text or "知识层" in text
        assert "src/domain/knowledge" in text or "kg_nodes" in text
