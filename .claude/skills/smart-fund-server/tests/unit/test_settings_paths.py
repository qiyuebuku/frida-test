from __future__ import annotations

from pathlib import Path

from src.infrastructure.config import settings


def test_milvus_relative_uri_resolves_against_project_root() -> None:
    expected = Path(settings.__file__).resolve().parents[3] / "data" / "milvus" / "kg_vectors.db"

    assert settings._resolve_local_path_setting("./data/milvus/kg_vectors.db") == str(expected.resolve())


def test_milvus_remote_uri_is_not_treated_as_local_path() -> None:
    assert settings._resolve_local_path_setting("http://localhost:19530") == "http://localhost:19530"
    assert settings._resolve_local_path_setting("unix:/tmp/milvus.sock") == "unix:/tmp/milvus.sock"
