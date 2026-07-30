"""Langfuse instrumentation and Agents SDK lifecycle auditing."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from agents.lifecycle import RunHooksBase

from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext, ToolInvocation


logger = logging.getLogger(__name__)
_instrumentation_lock = Lock()
_instrumented = False


def configure_observability(settings: AgentSettings) -> Any | None:
    """Configure one OpenInference processor that exports Agent spans to Langfuse."""

    global _instrumented
    if not settings.langfuse_configured:
        return None

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_base_url)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_base_url)

    with _instrumentation_lock:
        if not _instrumented:
            from openinference.instrumentation.openai_agents import (
                OpenAIAgentsInstrumentor,
            )

            OpenAIAgentsInstrumentor().instrument()
            _instrumented = True

    from langfuse import get_client

    return get_client()


def _parse_arguments(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


class AgentAuditHooks(RunHooksBase[AgentRunContext, Any]):
    async def on_llm_start(
        self,
        context,
        agent,
        system_prompt,
        input_items,
    ) -> None:
        context.context.llm_calls += 1
        logger.info(
            "agent_llm_start run_id=%s agent=%s call=%s items=%s",
            context.context.run_id,
            agent.name,
            context.context.llm_calls,
            len(input_items),
        )

    async def on_llm_end(self, context, agent, response) -> None:
        logger.info(
            "agent_llm_end run_id=%s agent=%s outputs=%s",
            context.context.run_id,
            agent.name,
            len(response.output),
        )

    async def on_tool_start(self, context, agent, tool) -> None:
        call_id = str(getattr(context, "tool_call_id", "") or "")
        invocation = ToolInvocation(
            name=str(getattr(context, "tool_name", "") or tool.name),
            call_id=call_id,
            arguments=_parse_arguments(getattr(context, "tool_arguments", None)),
        )
        context.context.tool_invocations.append(invocation)
        logger.info(
            "agent_tool_start run_id=%s tool=%s call_id=%s",
            context.context.run_id,
            invocation.name,
            call_id,
        )

    async def on_tool_end(self, context, agent, tool, result: str) -> None:
        call_id = str(getattr(context, "tool_call_id", "") or "")
        invocation = next(
            (
                item
                for item in reversed(context.context.tool_invocations)
                if item.call_id == call_id and item.result is None
            ),
            None,
        )
        if invocation is None:
            invocation = ToolInvocation(name=tool.name, call_id=call_id)
            context.context.tool_invocations.append(invocation)
        invocation.result = result
        invocation.finished_at = datetime.now(UTC)
        logger.info(
            "agent_tool_end run_id=%s tool=%s call_id=%s result_chars=%s",
            context.context.run_id,
            invocation.name,
            call_id,
            len(str(result)),
        )
