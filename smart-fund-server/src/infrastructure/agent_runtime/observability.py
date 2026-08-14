"""Langfuse instrumentation and Agents SDK lifecycle auditing."""

from __future__ import annotations

import json
import logging
from hashlib import sha256
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from agents.lifecycle import RunHooksBase

from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext, ToolInvocation


logger = logging.getLogger(__name__)
_SUBMIT_TOOLS = {
    "submit_research_conclusion",
    "submit_investment_view_revision",
}
_NOTEBOOK_TRACE_CHUNK_CHARS = 40_000


def configure_observability(settings: AgentSettings) -> Any | None:
    """Return the client used by the business-semantic Agent trace."""

    if not settings.langfuse_configured:
        return None

    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )
    resources = getattr(client, "_resources", None)
    actual_base_url = str(getattr(resources, "base_url", "") or "").rstrip("/")
    if actual_base_url != settings.langfuse_base_url:
        raise RuntimeError(
            "Agent Langfuse client is already bound to a different endpoint"
        )
    return client


def _parse_arguments(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _trace_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_trace_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _trace_value(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _trace_value(asdict(value))
    if hasattr(value, "to_input_item"):
        try:
            return _trace_value(value.to_input_item())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "__dict__"):
        return _trace_value(vars(value))
    return str(value)


def _decode_json_value(value: Any) -> Any:
    normalized = _trace_value(value)
    if isinstance(normalized, str):
        stripped = normalized.strip()
        if stripped.startswith(("{", "[", '"')):
            try:
                return _decode_json_value(json.loads(stripped))
            except json.JSONDecodeError:
                return normalized
        return normalized
    if isinstance(normalized, list):
        return [_decode_json_value(item) for item in normalized]
    if isinstance(normalized, dict):
        if normalized.get("type") == "text" and set(normalized) <= {
            "type",
            "text",
            "annotations",
            "meta",
        }:
            return _decode_json_value(normalized.get("text"))
        return {
            key: _decode_json_value(item)
            for key, item in normalized.items()
        }
    return normalized


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        normalized = _trace_value(item)
        if isinstance(normalized, dict) and normalized.get("text"):
            parts.append(str(normalized["text"]))
    return "\n".join(parts)


def _simplify_conversation_item(item: Any) -> dict[str, Any]:
    normalized = _trace_value(item)
    if not isinstance(normalized, dict):
        return {"type": type(item).__name__, "value": normalized}
    item_type = str(normalized.get("type") or "message")
    if item_type == "message":
        text = _content_text(normalized.get("content"))
        decoded = _decode_json_value(text)
        message = {
            "type": "message",
            "role": normalized.get("role"),
        }
        if isinstance(decoded, (dict, list)):
            message["content"] = decoded
        else:
            message["text"] = decoded
        return message
    if item_type in {"function_call", "tool_call"}:
        return {
            "type": "tool_call",
            "tool": normalized.get("name"),
            "call_id": normalized.get("call_id"),
            "arguments": _decode_json_value(normalized.get("arguments")),
        }
    if item_type in {"function_call_output", "tool_call_output"}:
        return {
            "type": "tool_result",
            "call_id": normalized.get("call_id"),
            "output": _decode_json_value(normalized.get("output")),
        }
    return {
        key: _decode_json_value(value)
        for key, value in normalized.items()
        if key not in {"id", "status", "phase", "summary"}
    }


def _llm_trace_output(output: Any) -> dict[str, Any]:
    normalized = _trace_value(output)
    items = normalized if isinstance(normalized, list) else [normalized]
    assistant_text: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    other: list[Any] = []
    for item in items:
        simplified = _simplify_conversation_item(item)
        if simplified.get("type") == "message" and simplified.get("text"):
            assistant_text.append(str(simplified["text"]))
        elif simplified.get("type") == "tool_call":
            tool_calls.append(
                {
                    "tool": simplified.get("tool"),
                    "call_id": simplified.get("call_id"),
                }
            )
        else:
            other.append(simplified)
    result: dict[str, Any] = {
        "assistant_text": "\n\n".join(assistant_text),
        "tool_calls": tool_calls,
    }
    if other:
        result["other_items"] = other
    return result


def _research_notebook_from_input(input_items: Any) -> dict[str, Any] | None:
    """Return the structured Runtime notebook embedded in model input, if any."""

    for item in input_items or []:
        simplified = _simplify_conversation_item(item)
        content = simplified.get("content")
        if isinstance(content, dict) and isinstance(
            content.get("research_notebook"), dict
        ):
            return content["research_notebook"]
    return None


def _bounded_notebook_chunks(
    entries: list[Any],
    *,
    max_chars: int = _NOTEBOOK_TRACE_CHUNK_CHARS,
) -> list[list[Any]]:
    """Group complete notebook entries without cutting JSON values in half."""

    chunks: list[list[Any]] = []
    current: list[Any] = []
    current_chars = 0
    for entry in entries:
        entry_chars = len(json.dumps(entry, ensure_ascii=False, default=str))
        if current and current_chars + entry_chars > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += entry_chars
    if current:
        chunks.append(current)
    return chunks


def _langfuse_usage_details(usage: Any) -> dict[str, int] | None:
    """Map Agents SDK usage onto Langfuse's generation usage fields."""

    if usage is None:
        return None
    details = {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
        "total": int(getattr(usage, "total_tokens", 0) or 0),
    }
    input_details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    if cached_tokens:
        details["cached_input"] = cached_tokens
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = int(
        getattr(output_details, "reasoning_tokens", 0) or 0
    )
    if reasoning_tokens:
        details["reasoning"] = reasoning_tokens
    return details


def _tool_trace_name(tool_name: str) -> str:
    if tool_name in {
        "submit_research_conclusion",
        "submit_investment_view_revision",
    }:
        return f"07 报告校验与提交｜{tool_name}"
    if tool_name.startswith(
        ("agent_run_", "research_current_", "research_data_", "research_view_")
    ):
        phase = "01 读取研究上下文"
    elif tool_name.startswith(
        (
            "market_frame_",
            "market_change_",
            "market_dimension_",
            "market_domain_",
            "market_topic_",
        )
    ):
        phase = "02 市场概览探索"
    elif tool_name.startswith(("market_sector_", "market_instrument_")):
        phase = "03 板块与标的下钻"
    elif tool_name.startswith(
        ("agent_evidence_", "kg_", "external_", "market_evidence_")
    ):
        phase = "04 证据与关系核验"
    elif tool_name.startswith(("role_memory_", "role_outcome_", "research_quality_")):
        phase = "05 记忆与历史复盘"
    elif tool_name.startswith(("research_position_", "research_exposure_")):
        phase = "05 账户暴露参考"
    else:
        phase = "06 其他研究动作"
    return f"{phase}｜{tool_name}"


def _llm_trace_name(call_number: int, *, correcting: bool) -> str:
    if call_number == 1:
        return "01 研究规划｜LLM 1"
    if correcting:
        return f"07 报告修正｜LLM {call_number}"
    return f"06 综合分析与观点形成｜LLM {call_number}"


class AgentAuditHooks(RunHooksBase[AgentRunContext, Any]):
    def __init__(
        self,
        *,
        langfuse_client: Any | None = None,
        include_sensitive_data: bool = False,
        model: str = "",
    ) -> None:
        self._langfuse = langfuse_client
        self._include_sensitive_data = include_sensitive_data
        self._model = model
        self._trace_context: dict[str, str] | None = None
        self._llm_observation: Any | None = None
        self._tool_observations: dict[str, Any] = {}
        self._observed_notebooks: set[str] = set()

    def set_parent(self, *, trace_id: str, parent_span_id: str) -> None:
        self._trace_context = {
            "trace_id": trace_id,
            "parent_span_id": parent_span_id,
        }

    def _start_observation(self, **kwargs: Any) -> Any | None:
        if self._langfuse is None:
            return None
        try:
            return self._langfuse.start_observation(
                trace_context=self._trace_context,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_trace_observation_start_failed: %s", exc)
            return None

    def _flush_completed_observations(self) -> None:
        """Publish long-running Agent progress instead of waiting for run exit."""

        if self._langfuse is None:
            return
        try:
            self._langfuse.flush()
        except Exception as exc:  # noqa: BLE001
            # Observability must never make the research run fail.  A later
            # phase/run-final flush can still deliver the buffered records.
            logger.warning("agent_trace_incremental_flush_failed: %s", exc)

    @staticmethod
    def _finish_observation(
        observation: Any | None,
        *,
        output: Any = None,
        usage_details: dict[str, int] | None = None,
        level: str = "DEFAULT",
        status_message: str = "completed",
    ) -> None:
        if observation is None:
            return
        try:
            observation.update(
                output=_trace_value(output),
                usage_details=usage_details,
                level=level,
                status_message=status_message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_trace_observation_update_failed: %s", exc)
        finally:
            observation.end()

    def close_open_observations(self) -> None:
        self._finish_observation(
            self._llm_observation,
            level="ERROR",
            status_message="run interrupted",
        )
        self._llm_observation = None
        for active in self._tool_observations.values():
            self._finish_observation(
                active,
                level="ERROR",
                status_message="run interrupted",
            )
        self._tool_observations.clear()

    def record_research_notebook(
        self,
        *,
        run_id: str,
        notebook: dict[str, Any],
    ) -> None:
        """Expose compaction as readable, bounded Langfuse observations.

        The LLM generation still records its exact input for forensic replay. A
        second semantic projection prevents the useful notebook from being
        buried inside a several-hundred-kilobyte conversation JSON tree.
        """

        fingerprint = sha256(
            json.dumps(
                notebook,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:16]
        if fingerprint in self._observed_notebooks:
            return
        self._observed_notebooks.add(fingerprint)

        retained = list(notebook.get("retained_results") or [])
        omitted = list(notebook.get("omitted_results") or [])
        completed = list(notebook.get("completed_operations") or [])
        overview = self._start_observation(
            name="06 上下文压缩｜研究笔记总览",
            as_type="span",
            input={
                "policy": notebook.get("compaction_policy"),
                "working_memory_revision": (
                    notebook.get("working_memory") or {}
                ).get("revision"),
            },
            metadata={
                "run_id": run_id,
                "notebook_fingerprint": fingerprint,
                "ui_chunk_chars": _NOTEBOOK_TRACE_CHUNK_CHARS,
            },
        )
        self._finish_observation(
            overview,
            output={
                "completed_operation_count": len(completed),
                "retained_result_count": len(retained),
                "omitted_result_count": len(omitted),
                "retained_result_chunks": len(_bounded_notebook_chunks(retained)),
                "recent_working_note_count": len(
                    notebook.get("recent_working_notes") or []
                ),
            },
        )
        if not self._include_sensitive_data:
            self._flush_completed_observations()
            return

        working = self._start_observation(
            name="06 上下文压缩｜研究主线与工作记忆",
            as_type="span",
            input={"notebook_fingerprint": fingerprint},
            metadata={"run_id": run_id, "notebook_fingerprint": fingerprint},
        )
        self._finish_observation(
            working,
            output={
                "working_memory": notebook.get("working_memory"),
                "recent_working_notes": notebook.get("recent_working_notes") or [],
            },
        )

        retained_chunks = _bounded_notebook_chunks(retained)
        for index, chunk in enumerate(retained_chunks, start=1):
            observation = self._start_observation(
                name=(
                    "06 上下文压缩｜保留证据 "
                    f"{index}/{len(retained_chunks)}"
                ),
                as_type="span",
                input={
                    "notebook_fingerprint": fingerprint,
                    "evidence_refs": [item.get("evidence_ref") for item in chunk],
                },
                metadata={"run_id": run_id, "notebook_fingerprint": fingerprint},
            )
            self._finish_observation(observation, output={"retained_results": chunk})

        if omitted:
            observation = self._start_observation(
                name="06 上下文压缩｜省略结果索引",
                as_type="span",
                input={"notebook_fingerprint": fingerprint},
                metadata={"run_id": run_id, "notebook_fingerprint": fingerprint},
            )
            self._finish_observation(
                observation,
                output={"omitted_results": omitted},
            )

        self._flush_completed_observations()
        logger.info(
            "agent_research_notebook_traced run_id=%s fingerprint=%s "
            "retained=%s omitted=%s chunks=%s",
            run_id,
            fingerprint,
            len(retained),
            len(omitted),
            len(retained_chunks),
        )

    async def on_llm_start(
        self,
        context,
        agent,
        system_prompt,
        input_items,
    ) -> None:
        context.context.llm_calls += 1
        correcting = any(
            item.name in _SUBMIT_TOOLS
            and item.result is not None
            and "校验失败" in str(item.result)
            for item in context.context.tool_invocations[-2:]
        )
        notebook = _research_notebook_from_input(input_items)
        if notebook is not None:
            self.record_research_notebook(
                run_id=context.context.run_id,
                notebook=notebook,
            )
        trace_input = {
            "round": context.context.llm_calls,
            "purpose": "修正报告" if correcting else "研究分析",
            "message_count": len(input_items),
        }
        if self._include_sensitive_data:
            trace_input.update(
                {
                    "system_prompt": system_prompt,
                    "conversation": [
                        _simplify_conversation_item(item)
                        for item in input_items
                    ],
                }
            )
        self._llm_observation = self._start_observation(
            name=_llm_trace_name(context.context.llm_calls, correcting=correcting),
            as_type="generation",
            input=trace_input,
            metadata={
                "run_id": context.context.run_id,
                "round": context.context.llm_calls,
                "purpose": trace_input["purpose"],
            },
            model=self._model or str(agent.model),
        )
        logger.info(
            "agent_llm_start run_id=%s agent=%s call=%s items=%s",
            context.context.run_id,
            agent.name,
            context.context.llm_calls,
            len(input_items),
        )

    async def on_llm_end(self, context, agent, response) -> None:
        usage_details = _langfuse_usage_details(getattr(response, "usage", None))
        self._finish_observation(
            self._llm_observation,
            output=_llm_trace_output(getattr(response, "output", response)),
            usage_details=usage_details,
        )
        self._llm_observation = None
        self._flush_completed_observations()
        logger.info(
            "agent_llm_end run_id=%s agent=%s outputs=%s",
            context.context.run_id,
            agent.name,
            len(response.output),
        )

    async def on_tool_start(self, context, agent, tool) -> None:
        research_context = context.context.research_context
        evidence_tool_calls = sum(
            item.name not in _SUBMIT_TOOLS
            for item in context.context.tool_invocations
        )
        if (
            research_context is not None
            and tool.name not in _SUBMIT_TOOLS
            and evidence_tool_calls >= research_context.trigger.max_tool_calls
        ):
            # Tool visibility is calculated before a parallel batch starts, so
            # several calls can legitimately cross the soft limit together.
            # Killing the whole run here would discard successful sibling reads
            # and the complete research state. Hide reads on the next turn and
            # let the Agent submit from what it has already learned.
            logger.warning(
                "research_tool_soft_budget_exceeded run_id=%s tool=%s "
                "completed_evidence_calls=%s configured_limit=%s",
                context.context.run_id,
                tool.name,
                evidence_tool_calls,
                research_context.trigger.max_tool_calls,
            )
        call_id = str(getattr(context, "tool_call_id", "") or "")
        invocation = ToolInvocation(
            name=str(getattr(context, "tool_name", "") or tool.name),
            call_id=call_id,
            arguments=_parse_arguments(getattr(context, "tool_arguments", None)),
        )
        context.context.tool_invocations.append(invocation)
        trace_input: dict[str, Any] = {
            "call_id": call_id,
            "tool": invocation.name,
        }
        if self._include_sensitive_data:
            trace_input["arguments"] = _trace_value(invocation.arguments)
        self._tool_observations[call_id] = self._start_observation(
            name=_tool_trace_name(invocation.name),
            as_type="tool",
            input=trace_input,
            metadata={
                "run_id": context.context.run_id,
                "call_id": call_id,
                "tool": invocation.name,
            },
        )
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
        validation_failed = (
            invocation.name in _SUBMIT_TOOLS
            and "校验失败" in str(result)
        )
        self._finish_observation(
            self._tool_observations.pop(call_id, None),
            output=_decode_json_value(result) if self._include_sensitive_data else {
                "result_chars": len(str(result)),
                "validation_failed": validation_failed,
            },
            level="WARNING" if validation_failed else "DEFAULT",
            status_message=(
                "proposal rejected; agent will revise"
                if validation_failed
                else "completed"
            ),
        )
        self._flush_completed_observations()
        if (
            invocation.name in _SUBMIT_TOOLS
            and "校验失败" in str(result)
        ):
            logger.warning(
                "agent_submit_validation_error run_id=%s call_id=%s error=%s",
                context.context.run_id,
                call_id,
                str(result)[:2000],
            )
        logger.info(
            "agent_tool_end run_id=%s tool=%s call_id=%s result_chars=%s",
            context.context.run_id,
            invocation.name,
            call_id,
            len(str(result)),
        )
