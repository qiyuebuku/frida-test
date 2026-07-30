"""Reusable OpenAI Agents SDK runtime for automated and manual research."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import TracebackType
from uuid import uuid4

from agents import RunConfig, RunContextWrapper, Runner, SQLiteSession, trace
from agents.models.openai_provider import OpenAIProvider
from langfuse import propagate_attributes
from openai import AsyncOpenAI

from src.application.agents.financial_research.agent import create_financial_research_agent
from src.application.agents.financial_research.audit import validate_research_result
from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.instructions import build_run_input
from src.infrastructure.agent_runtime.mcp import create_mcp_server
from src.infrastructure.agent_runtime.observability import AgentAuditHooks, configure_observability
from src.application.agents.financial_research.schemas import FinancialResearchResult, ResearchTaskMode


logger = logging.getLogger(__name__)


class FinancialAgentRuntime:
    """One reusable runtime; Jettask and CLI should call this same class."""

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self.settings = settings or AgentSettings.from_env()
        self.settings.validate()
        self._openai_client = AsyncOpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            timeout=self.settings.llm_timeout,
            max_retries=2,
        )
        self._model_provider = OpenAIProvider(
            openai_client=self._openai_client,
            use_responses=True,
        )
        self._mcp_server = create_mcp_server(self.settings)
        self._agent = create_financial_research_agent(
            model=self.settings.model,
            mcp_server=self._mcp_server,
        )
        self._langfuse = configure_observability(self.settings)
        self._connected = False

    async def __aenter__(self) -> "FinancialAgentRuntime":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._connected:
            return
        await self._mcp_server.connect()
        self._connected = True

    async def close(self) -> None:
        if self._connected:
            await self._mcp_server.cleanup()
            self._connected = False
        await self._openai_client.close()
        if self._langfuse is not None:
            self._langfuse.flush()

    async def list_tools(self, *, allow_writes: bool = False) -> list[str]:
        await self.connect()
        context = AgentRunContext(
            run_id=f"check-{uuid4().hex}",
            session_id="mcp-check",
            task_mode=ResearchTaskMode.RESEARCH,
            allow_writes=allow_writes,
        )
        tools = await self._mcp_server.list_tools(
            RunContextWrapper(context),
            self._agent,
        )
        return sorted(tool.name for tool in tools)

    async def run(
        self,
        prompt: str,
        *,
        task_mode: ResearchTaskMode = ResearchTaskMode.RESEARCH,
        session_id: str | None = None,
        allow_writes: bool = False,
    ) -> FinancialResearchResult:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Agent prompt cannot be empty")
        if len(clean_prompt) > self.settings.max_input_chars:
            raise ValueError(
                f"Agent prompt exceeds {self.settings.max_input_chars} characters"
            )

        await self.connect()
        run_id = f"agent-run-{uuid4().hex}"
        effective_session_id = session_id or run_id
        context = AgentRunContext(
            run_id=run_id,
            session_id=effective_session_id,
            task_mode=task_mode,
            allow_writes=allow_writes,
        )
        run_input = build_run_input(
            prompt=clean_prompt,
            task_mode=task_mode,
            run_id=run_id,
            now=datetime.now(UTC),
            allow_writes=allow_writes,
        )

        session = None
        if session_id:
            self.settings.session_db_path.parent.mkdir(parents=True, exist_ok=True)
            session = SQLiteSession(
                session_id,
                db_path=self.settings.session_db_path,
            )

        run_config = RunConfig(
            model_provider=self._model_provider,
            workflow_name="smart-fund-financial-research",
            group_id=effective_session_id,
            trace_metadata={
                "run_id": run_id,
                "task_mode": task_mode.value,
                "model": self.settings.model,
                "allow_writes": str(allow_writes).lower(),
            },
            tracing_disabled=not self.settings.langfuse_configured,
            trace_include_sensitive_data=self.settings.trace_sensitive_data,
        )
        hooks = AgentAuditHooks()

        logger.info(
            "agent_run_start run_id=%s session_id=%s mode=%s model=%s",
            run_id,
            effective_session_id,
            task_mode.value,
            self.settings.model,
        )

        attributes = {
            "session_id": effective_session_id,
            "tags": ["smart-fund-agent", task_mode.value],
            "metadata": {
                "run_id": run_id,
                "model": self.settings.model,
                "allow_writes": str(allow_writes).lower(),
            },
            "version": "2.0.0",
        }
        propagation = (
            propagate_attributes(**attributes)
            if self._langfuse is not None
            else _null_context()
        )
        with propagation:
            with trace(
                "smart-fund-financial-research",
                group_id=effective_session_id,
                metadata=attributes["metadata"],
                disabled=not self.settings.langfuse_configured,
            ):
                result = await Runner.run(
                    self._agent,
                    run_input,
                    context=context,
                    max_turns=self.settings.max_turns,
                    hooks=hooks,
                    run_config=run_config,
                    session=session,
                )

        output = result.final_output
        if isinstance(output, str):
            output = FinancialResearchResult.model_validate_json(output)
        if not isinstance(output, FinancialResearchResult):
            raise TypeError(
                "Financial Research Agent returned an unexpected output type: "
                f"{type(output).__name__}"
            )
        validate_research_result(output, context)

        logger.info(
            "agent_run_end run_id=%s llm_calls=%s tool_calls=%s evidence=%s",
            run_id,
            context.llm_calls,
            len(context.tool_invocations),
            len(output.evidence),
        )
        if self._langfuse is not None:
            self._langfuse.flush()
        return output


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False
