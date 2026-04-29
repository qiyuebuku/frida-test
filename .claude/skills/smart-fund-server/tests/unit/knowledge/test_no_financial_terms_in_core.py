"""Guard against domain-specific leakage in the generic knowledge core."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_DIR = PROJECT_ROOT / "src" / "domain" / "knowledge"

FORBIDDEN_TERMS = [
    "stock",
    "fund",
    "industry",
    "concept",
    "policy",
    "market",
    "trade",
    "affected_stocks",
    "affected_concepts",
]


def test_generic_knowledge_core_has_no_domain_specific_terms() -> None:
    assert DOMAIN_DIR.is_dir()

    violations: list[str] = []
    for path in sorted(DOMAIN_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN_TERMS:
            if term.lower() in text:
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}: {term}")

    assert not violations, "Domain-specific terms leaked into generic core: " + ", ".join(violations)
