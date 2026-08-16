"""Runtime configuration for the Smart Fund financial agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_path(value: str, *, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _first(values: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return default


def load_environment(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Reuse the server's single environment loader."""

    from src.infrastructure.config import settings as server_settings

    values = dict(os.environ)
    values.setdefault(
        "SMART_FUND_MCP_PUBLIC_URL",
        server_settings.SMART_FUND_MCP_PUBLIC_URL,
    )
    values.setdefault(
        "AICLIENT2API_LLM_BASE_URL",
        server_settings.AICLIENT2API_LLM_BASE_URL,
    )
    values.setdefault(
        "AICLIENT2API_LLM_API_KEY",
        server_settings.AICLIENT2API_LLM_API_KEY,
    )
    values.setdefault(
        "AICLIENT2API_LLM_DEFAULT_MODEL",
        server_settings.AICLIENT2API_LLM_DEFAULT_MODEL,
    )
    return values


@dataclass(frozen=True, slots=True)
class AgentSettings:
    project_root: Path
    mcp_url: str
    mcp_bearer_token: str
    mcp_connect_timeout: float
    mcp_tool_timeout: float
    llm_base_url: str
    llm_api_key: str
    model: str
    llm_timeout: float
    max_turns: int
    max_input_chars: int
    session_db_path: Path
    langfuse_enabled: bool
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_base_url: str
    trace_sensitive_data: bool

    @classmethod
    def from_env(cls, project_root: Path = PROJECT_ROOT) -> "AgentSettings":
        return cls.from_mapping(load_environment(project_root), project_root=project_root)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        project_root: Path = PROJECT_ROOT,
    ) -> "AgentSettings":
        return cls(
            project_root=project_root.resolve(),
            mcp_url=_first(
                values,
                "SMART_FUND_AGENT_MCP_URL",
                "SMART_FUND_MCP_URL",
                "SMART_FUND_MCP_PUBLIC_URL",
                default="http://119.23.227.187:8900/mcp",
            ),
            mcp_bearer_token=_first(values, "SMART_FUND_MCP_BEARER_TOKEN"),
            mcp_connect_timeout=float(
                _first(
                    values,
                    "SMART_FUND_AGENT_MCP_CONNECT_TIMEOUT",
                    "SMART_FUND_MCP_CONNECT_TIMEOUT",
                    default="15",
                )
            ),
            mcp_tool_timeout=float(
                _first(
                    values,
                    "SMART_FUND_AGENT_MCP_TOOL_TIMEOUT",
                    "SMART_FUND_MCP_TOOL_TIMEOUT",
                    default="120",
                )
            ),
            llm_base_url=_first(
                values,
                "SMART_FUND_AGENT_LLM_BASE_URL",
                "AICLIENT2API_LLM_BASE_URL",
                default="http://119.23.227.187:13000/v1",
            ).rstrip("/"),
            llm_api_key=_first(
                values,
                "SMART_FUND_AGENT_LLM_API_KEY",
                "AICLIENT2API_LLM_API_KEY",
                "AICLIENT2API_API_KEY",
            ),
            model=_first(
                values,
                "SMART_FUND_AGENT_MODEL",
                "AICLIENT2API_LLM_DEFAULT_MODEL",
                default="glm-5.2",
            ),
            llm_timeout=float(
                _first(values, "SMART_FUND_AGENT_LLM_TIMEOUT", default="500")
            ),
            max_turns=int(
                _first(values, "SMART_FUND_AGENT_MAX_TURNS", default="24")
            ),
            max_input_chars=int(
                _first(values, "SMART_FUND_AGENT_MAX_INPUT_CHARS", default="20000")
            ),
            session_db_path=_resolve_path(
                _first(
                    values,
                    "SMART_FUND_AGENT_SESSION_DB",
                    default="data/agent_sessions.sqlite3",
                ),
                root=project_root,
            ),
            langfuse_enabled=_as_bool(
                values.get("SMART_FUND_AGENT_LANGFUSE_ENABLED"),
                default=True,
            ),
            langfuse_public_key=_first(
                values,
                "SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY",
            ),
            langfuse_secret_key=_first(
                values,
                "SMART_FUND_AGENT_LANGFUSE_SECRET_KEY",
            ),
            langfuse_base_url=_first(
                values,
                "SMART_FUND_AGENT_LANGFUSE_BASE_URL",
                default="",
            ).rstrip("/"),
            trace_sensitive_data=_as_bool(
                values.get("SMART_FUND_AGENT_TRACE_SENSITIVE_DATA"),
                default=False,
            ),
        )

    @property
    def langfuse_configured(self) -> bool:
        return bool(
            self.langfuse_enabled
            and self.langfuse_public_key
            and self.langfuse_secret_key
            and self.langfuse_base_url
        )

    def validate(self) -> None:
        missing: list[str] = []
        if not self.mcp_url:
            missing.append("SMART_FUND_MCP_URL")
        if not self.mcp_bearer_token:
            missing.append("SMART_FUND_MCP_BEARER_TOKEN")
        if not self.llm_base_url:
            missing.append("SMART_FUND_AGENT_LLM_BASE_URL")
        if not self.llm_api_key:
            missing.append("SMART_FUND_AGENT_LLM_API_KEY")
        if not self.model:
            missing.append("SMART_FUND_AGENT_MODEL")
        if missing:
            raise ValueError(f"Missing required Agent configuration: {', '.join(missing)}")
        if self.max_turns < 1:
            raise ValueError("SMART_FUND_AGENT_MAX_TURNS must be at least 1")
        if self.max_input_chars < 1:
            raise ValueError("SMART_FUND_AGENT_MAX_INPUT_CHARS must be at least 1")
