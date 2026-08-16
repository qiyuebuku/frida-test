from __future__ import annotations

from pathlib import Path

import pytest

from deployment.langfuse.migrate_project_env import migrate


def test_migrate_legacy_langfuse_env_to_named_agent_project(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KEEP=value\n"
        "LANGFUSE_PUBLIC_KEY=pk-agent\n"
        "LANGFUSE_SECRET_KEY=sk-agent\n"
        "LANGFUSE_BASE_URL=http://self-hosted:3001\n"
        "LANGFUSE_HOST=http://self-hosted:3001\n",
        encoding="utf-8",
    )

    migrate(env_file, project="agent")

    result = env_file.read_text(encoding="utf-8")
    assert "KEEP=value" in result
    assert "SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY=pk-agent" in result
    assert "SMART_FUND_AGENT_LANGFUSE_SECRET_KEY=sk-agent" in result
    assert "SMART_FUND_AGENT_LANGFUSE_BASE_URL=http://self-hosted:3001" in result
    assert "\nLANGFUSE_" not in result


def test_migration_refuses_to_overwrite_another_project_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LANGFUSE_PUBLIC_KEY=pk-server\n"
        "LANGFUSE_SECRET_KEY=sk-server\n"
        "LANGFUSE_BASE_URL=http://self-hosted:3001\n"
        "SMART_FUND_SERVER_LANGFUSE_PUBLIC_KEY=pk-other\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        migrate(env_file, project="smart-fund-server")
