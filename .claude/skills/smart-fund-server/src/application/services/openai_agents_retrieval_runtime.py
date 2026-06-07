"""OpenAI Agents SDK route adapter for KG retrieval."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import traceback
from contextlib import ExitStack, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalResult,
    AgenticToolTrace,
    AgenticRetrievalStrategy,
    RetrievalWorkingSet,
)
from src.domain.knowledge.retrieval import RetrievalHit, RetrievalTrace, dedupe_hits
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_judge import CandidateJudge
from src.domain.knowledge.retrieval_rerank import prepare_rerank_candidates
from src.domain.knowledge.retrieval_stop_verifier import verify_stop_condition
from src.domain.knowledge.retrieval_trace_log import trace_agentic_event
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolResult
from src.domain.knowledge.retrieval_tools import RetrievalToolRegistry
from src.infrastructure.config import settings

logger = logging.getLogger(__name__)


_SYSTEM_INSTRUCTIONS = """你是基于工具的知识图谱 Agentic RAG 检索 Agent。
你不能凭模型记忆回答用户问题，必须通过工具获得证据。

工作方式：
1. 系统已经先用原始 query 做了一次 bootstrap 召回，并经过候选清洗和 reranker 重排，你必须先阅读 bootstrap_observation。
2. bootstrap 中的候选已经统一追溯为 evidence chunk；如果标题或片段与用户 query 精确匹配，优先 kg_open 该 evidence。
3. 打开直连证据后，如果证据已经能回答“涉及哪些主体、行业或资产影响”，必须直接返回最终 JSON；不要继续搜索同一主题。
4. 只有在 bootstrap 和已打开证据都缺少关键覆盖项时，才调用 kg_search 改写查询或补充召回。
5. 当已有候选方向正确但范围过大时，调用 kg_scoped_search 在这些 candidate/evidence 的局部范围内继续检索。
6. 只有当已命中的 evidence chunk 暗示存在相关主体、行业、资产或影响链但证据不足时，才调用 kg_expand_graph；工具会通过 PG 图关系展开并返回新的 evidence chunk。
7. kg_search 返回的 evidence chunk 如果数量多、主题混杂、或你需要重新比较相关性，应调用 kg_rerank_candidates 后再打开证据或最终选择。
8. kg_find 只用于在已打开证据中定位具体词句；不要用 kg_find 代替最终收敛。
9. 停止前如果不确定，可以调用一次 kg_stop_check；如果已经确定，直接最终回答，不需要 stop_check。
10. 最终只返回 JSON 对象，字段包含 stop_reason、selected_candidate_ids、evidence_ids、reason。

硬预算和去重：
- 不要重复打开同一个 evidence_id。
- 不要重复搜索同一表达或同义改写。
- 常规查询最多额外 kg_search 2 次、kg_scoped_search 2 次、kg_expand_graph 2 次、kg_open 2 次、kg_find 2 次；达到预算后必须返回最终 JSON。
- 最终 JSON 应选择 bootstrap/search/open/expand 中的 evidence chunk 或 evidence id；不要把裸 node/edge 当最终证据。

选择标准：
- 需要覆盖 query 中的主体、行业、资产影响、关系意图和证据来源。
- 对“这条新闻涉及哪些主体、行业或资产影响”类问题，优先选择原始新闻 evidence chunk；node/edge 只作为命中线索和展开依据，不作为最终证据。
- 如果同一 evidence 支撑多个主体/行业/资产，可以一次性选中这些候选，不要反复打开同一 evidence。
- kg_search 的首轮来源是向量语义检索；只有 query 包含强标识时才会附加 PG exact resolver。graph 不会默认全局展开，必须由你基于候选质量显式调用 kg_expand_graph。
- 不要生成知识图谱中没有的事实。
- 如果只有泛词命中或主题漂移，不要把它当答案。
- 最终输出必须是裸 JSON，不要 Markdown 代码块，不要前置解释，不要在 JSON 字符串里使用未转义的双引号。
"""

_TRANSCRIPT_FILE_LOCK = threading.Lock()
_LANGFUSE_LOCK = threading.Lock()
_LANGFUSE_INSTRUMENTED = False
_LANGFUSE_AUTH_CHECKED = False


class OpenAIAgentsRetrievalRuntime:
    """Run KG retrieval through OpenAI Agents SDK when available.

    The SDK route treats the Agent final JSON as the handoff contract. Python
    only validates selected ids, materializes RetrievalHit objects, and records
    trace data; it does not run a second semantic judge after the Agent.
    When the `agents` package, provider credentials, or SDK run are unavailable,
    this route fails directly. It never falls back to the hand-written baseline.
    """

    trace_mode = "openai_agents_arag"

    def __init__(
        self,
        registry: RetrievalToolRegistry,
        strategy: AgenticRetrievalStrategy,
        candidate_judge: CandidateJudge,
        constraints: AgenticRetrievalConstraints,
    ) -> None:
        self.registry = registry
        self.strategy = strategy
        self.candidate_judge = candidate_judge
        self.constraints = constraints

    async def run(self, query: str) -> AgenticRetrievalResult:
        if _sdk_disabled():
            raise RuntimeError("openai_agents_arag is disabled by KG_OPENAI_AGENTS_SDK_ENABLED")
        sdk = _load_agents_sdk()
        if sdk is None:
            raise RuntimeError(
                "openai-agents is not installed; install dependency openai-agents>=0.17,<1"
            )
        return await self._run_with_sdk(query, sdk=sdk)

    async def _run_with_sdk(self, query: str, *, sdk: Any) -> AgenticRetrievalResult:
        langfuse_client = _configure_langfuse_observability()
        anchor = build_guarded_query_anchor(
            query,
            known_nodes=self.registry.runtime.repository.list_nodes(self.registry.options.adapter_name),
        )
        working_set = RetrievalWorkingSet(query_anchor=anchor)
        observations: list[RetrievalToolResult] = []
        tool_traces: list[AgenticToolTrace] = []
        raw_hits: list[RetrievalHit] = []

        langfuse_context = ExitStack()
        langfuse_context.enter_context(
            _langfuse_run_context(query, adapter=self.registry.options.adapter_name)
        )
        langfuse_context.enter_context(
            _langfuse_agent_observation(
                langfuse_client,
                query=query,
                adapter=self.registry.options.adapter_name,
                bootstrap_hits=0,
                max_turns=self.constraints.max_turns,
            )
        )
        bootstrap_started = time.perf_counter()
        bootstrap_call = RetrievalToolCall(
            tool="search",
            query=query,
            limit=self.constraints.recall_pool_max_hits,
        )
        try:
            await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=None,
                call=bootstrap_call,
                reason="bootstrap_raw_query_before_agent",
                auto_action="bootstrap_search",
            )
        except BaseException:
            langfuse_context.close()
            raise
        bootstrap_ms = (time.perf_counter() - bootstrap_started) * 1000
        bootstrap_result = observations[-1]
        trace_agentic_event(
            "openai_agents_bootstrap_search",
            {
                "event_meaning": "raw query bootstrap recall ran before the OpenAI Agents SDK loop.",
                "_human_lines": [
                    f"查询: {query}",
                    "首轮: 原始 query bootstrap 召回，未让 Agent 先改写。",
                    f"候选数: {len(bootstrap_result.hits)}",
                ],
                "query": query,
                "hit_samples": _hit_summary_payloads(bootstrap_result.hits[:8]),
                "search_diagnostics": _search_diagnostics_summary(
                    bootstrap_result.step.input.get("search_diagnostics", {})
                ),
                "tool_ms": round(bootstrap_ms, 1),
            },
        )
        try:
            await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=None,
                call=RetrievalToolCall(
                    tool="rerank",
                    query=query,
                    candidate_pool=bootstrap_result.hits,
                    limit=self.constraints.recall_pool_max_hits,
                ),
                reason="bootstrap_search_before_agent",
                auto_action="system_bootstrap",
            )
        except BaseException:
            langfuse_context.close()
            raise
        bootstrap_result_for_agent = observations[-1]
        transcript: _AgentTranscript | None = _start_agent_transcript(query, bootstrap_result_for_agent)

        async def kg_search(query: str, limit: int = 0) -> str:
            """Search KG facts through PG deterministic search and vector semantic search."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="search",
                    query=query,
                    limit=limit or self.constraints.recall_pool_max_hits,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_scoped_search(
            query: str,
            candidate_ids: list[str] | None = None,
            evidence_ids: list[str] | None = None,
            limit: int = 0,
        ) -> str:
            """Search within the local scope of known candidates or evidence."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="scoped_search",
                    query=query,
                    candidate_ids=candidate_ids or [],
                    evidence_ids=evidence_ids or [],
                    limit=limit or self.constraints.recall_pool_max_hits,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_expand_graph(candidate_ids: list[str], limit: int = 0) -> str:
            """Expand selected evidence-backed candidates through local KG relations and return evidence chunks."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="expand",
                    candidate_ids=candidate_ids,
                    limit=limit or self.registry.options.graph_limit,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_expand_graph_scoped(
            candidate_ids: list[str] | None = None,
            seed_node_ids: list[str] | None = None,
            seed_edge_ids: list[str] | None = None,
            seed_chunk_ids: list[str] | None = None,
            evidence_ids: list[str] | None = None,
            relation_filters: list[str] | None = None,
            hop_limit: int = 0,
            limit: int = 0,
        ) -> str:
            """Expand PG graph from explicit node/edge/chunk/evidence seeds and return evidence chunks."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="expand",
                    candidate_ids=candidate_ids or [],
                    seed_node_ids=seed_node_ids or [],
                    seed_edge_ids=seed_edge_ids or [],
                    seed_chunk_ids=seed_chunk_ids or [],
                    evidence_ids=evidence_ids or [],
                    relation_filters=relation_filters or [],
                    hop_limit=hop_limit or None,
                    limit=limit or self.registry.options.graph_limit,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_open(
            evidence_ids: list[str] | None = None,
            chunk_ids: list[str] | None = None,
            candidate_ids: list[str] | None = None,
            include_neighbors: str = "none",
            limit: int = 0,
        ) -> str:
            """Open evidence chunk text by evidence ids, chunk ids, or candidate ids."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="open",
                    evidence_ids=evidence_ids or [],
                    chunk_ids=chunk_ids or [],
                    candidate_ids=candidate_ids or [],
                    include_neighbors=_normalize_include_neighbors(include_neighbors),
                    limit=limit or None,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_find(
            query: str,
            evidence_ids: list[str] | None = None,
            chunk_ids: list[str] | None = None,
            limit: int = 0,
        ) -> str:
            """Find local snippets inside opened or known evidence ids."""

            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="find",
                    query=query,
                    evidence_ids=evidence_ids or [],
                    chunk_ids=chunk_ids or [],
                    limit=limit or None,
                ),
                reason="agent_sdk_tool_call",
            )

        async def kg_rerank_candidates(
            candidate_ids: list[str] | None = None,
            top_n: int = 0,
            reason: str = "",
        ) -> str:
            """Rerank currently known evidence chunks with the external semantic reranker."""

            pool = dedupe_hits(raw_hits)
            ids = set(candidate_ids or [])
            if ids:
                pool = [hit for hit in pool if hit.hit_id in ids]
                if not pool:
                    raise ValueError("kg_rerank_candidates received no known candidate_ids")
            return await _execute_sdk_tool(
                registry=self.registry,
                observations=observations,
                raw_hits=raw_hits,
                tool_traces=tool_traces,
                working_set=working_set,
                transcript=transcript,
                call=RetrievalToolCall(
                    tool="rerank",
                    query=query,
                    candidate_pool=pool,
                    limit=top_n or self.constraints.recall_pool_max_hits,
                ),
                reason=reason or "agent_requested_rerank",
                auto_action="agent_requested",
            )

        def kg_stop_check() -> str:
            """Check whether current evidence is enough to stop."""

            stop_call = {"tool": "stop_check"}
            _append_tool_call(transcript, stop_call)
            started = time.perf_counter()
            result = verify_stop_condition(
                working_set,
                self.constraints,
                hits=dedupe_hits(raw_hits),
            )
            payload = result.model_dump(mode="json")
            _append_tool_result(
                transcript,
                stop_call,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                payload=payload,
            )
            return json.dumps(payload, ensure_ascii=False)

        agent = sdk.Agent(
            name="KG OpenAI Agents RAG",
            instructions=_SYSTEM_INSTRUCTIONS,
            model=_sdk_model(sdk),
            model_settings=sdk.ModelSettings(
                temperature=0.0,
                parallel_tool_calls=False,
                max_tokens=_agent_max_tokens(),
            ),
            tools=[
                sdk.function_tool(kg_search, strict_mode=False),
                sdk.function_tool(kg_open, strict_mode=False),
                sdk.function_tool(kg_find, strict_mode=False),
                sdk.function_tool(kg_scoped_search, strict_mode=False),
                sdk.function_tool(kg_expand_graph, strict_mode=False),
                sdk.function_tool(kg_expand_graph_scoped, strict_mode=False),
                sdk.function_tool(kg_rerank_candidates, strict_mode=False),
                sdk.function_tool(kg_stop_check, strict_mode=False),
            ],
        )
        run_input = _agent_input(query, bootstrap_result_for_agent)
        trace_agentic_event(
            "openai_agents_runner_start",
            {
                "event_meaning": "OpenAI Agents SDK runner starts; subsequent search/open/find choices are model-driven tool calls.",
                "model": _agent_model_name(),
                "base_url": _agent_base_url_for_trace(),
                "max_turns": self.constraints.max_turns,
                "bootstrap_hits": len(bootstrap_result_for_agent.hits),
            },
        )
        run_started = time.perf_counter()
        try:
            try:
                run_result = await sdk.Runner.run(
                    agent,
                    run_input,
                    max_turns=max(1, self.constraints.max_turns),
                )
            except BaseException as exc:
                _update_langfuse_agent_observation(
                    langfuse_client,
                    level="ERROR",
                    status_message=f"{type(exc).__name__}: {exc}",
                    output={
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "tool_calls": len(observations),
                        "raw_hits": len(dedupe_hits(raw_hits)),
                    },
                )
                raise
            else:
                _update_langfuse_agent_observation(
                    langfuse_client,
                    level="DEFAULT",
                    status_message="OpenAI Agents SDK retrieval completed",
                    output={
                        "status": "completed",
                        "bootstrap_hits": len(bootstrap_result_for_agent.hits),
                        "tool_calls": len(observations),
                        "raw_hits": len(dedupe_hits(raw_hits)),
                        "final_output_preview": _clip(
                            str(getattr(run_result, "final_output", "")),
                            1200,
                        ),
                    },
                )
        except BaseException as exc:
            runner_ms = (time.perf_counter() - run_started) * 1000
            langfuse_context.close()
            _flush_sdk_traces(sdk)
            _flush_langfuse(langfuse_client)
            _abort_agent_transcript(
                transcript,
                duration_ms=round(runner_ms, 1),
                exc=exc,
                raw_hits=dedupe_hits(raw_hits),
                observations=observations,
            )
            trace_agentic_event(
                "openai_agents_runner_failed",
                {
                    "event_meaning": "OpenAI Agents SDK runner failed before final output.",
                    "duration_ms": round(runner_ms, 1),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "tool_calls": len(observations),
                },
            )
            raise
        runner_ms = (time.perf_counter() - run_started) * 1000
        langfuse_context.close()
        _flush_sdk_traces(sdk)
        _flush_langfuse(langfuse_client)
        _append_agent_run_items(transcript, run_result)
        trace_agentic_event(
            "openai_agents_runner_done",
            {
                "event_meaning": "OpenAI Agents SDK runner finished.",
                "duration_ms": round(runner_ms, 1),
                "final_output": str(getattr(run_result, "final_output", "") or "")[:4000],
                "tool_calls": len(observations),
            },
        )

        warnings: list[str] = []
        agent_output = _parse_agent_final_output(getattr(run_result, "final_output", ""))
        selected_hits = _select_agent_hits(
            dedupe_hits(raw_hits),
            selected_candidate_ids=agent_output["selected_candidate_ids"],
            evidence_ids=agent_output["evidence_ids"],
            max_hits=self.constraints.max_hits,
        )
        if not selected_hits:
            warnings.append("openai_agents_final_output_selected_no_known_hits")
        evidence_refs = _ordered_unique(
            [
                *agent_output["evidence_ids"],
                *(evidence_id for hit in selected_hits for evidence_id in hit.evidence_refs),
            ]
        )
        working_set.evidence_refs = _ordered_unique(
            [
                *working_set.evidence_refs,
                *evidence_refs,
            ]
        )
        working_set.stop_reason = str(agent_output.get("stop_reason") or "openai_agents_done")
        tool_traces.append(
            AgenticToolTrace(
                tool_name="stop",
                tool_input={
                    "agent_final_output": agent_output,
                    "selected_candidate_ids": [hit.hit_id for hit in selected_hits],
                    "evidence_ids": evidence_refs,
                },
                raw_candidate_count=len(raw_hits),
                package_count=len(selected_hits),
                keep_count=len(selected_hits),
                decision_reason=str(agent_output.get("reason") or "agent_final_selection"),
                auto_action="agent_final",
                auto_action_reason="openai_agents_final_output",
                stop_reason=working_set.stop_reason,
            )
        )
        trace_agentic_event(
            "openai_agents_final_selection",
            {
                "event_meaning": (
                    "OpenAI Agents SDK final JSON selected the retrieval result; "
                    "Python only validated ids and materialized hits."
                ),
                "stop_reason": working_set.stop_reason,
                "reason": agent_output.get("reason", ""),
                "selected_candidate_ids": agent_output["selected_candidate_ids"],
                "evidence_ids": evidence_refs,
                "selected_hits": _hit_payloads(selected_hits),
                "warnings": warnings,
            },
        )
        _finish_agent_transcript(
            transcript,
            duration_ms=round(runner_ms, 1),
            final_output=getattr(run_result, "final_output", ""),
            parsed_output=agent_output,
            selected_hits=selected_hits,
            evidence_refs=evidence_refs,
            warnings=warnings,
        )
        semantic_enabled = bool(getattr(self.registry.runtime.semantic_retriever, "enabled", False))
        backend_name = str(getattr(self.registry.runtime.semantic_retriever, "backend_name", "none"))
        return AgenticRetrievalResult(
            query=query,
            hits=selected_hits,
            evidence_refs=evidence_refs,
            working_set=working_set,
            stop_reason=working_set.stop_reason or "openai_agents_done",
            trace=RetrievalTrace(
                mode=self.trace_mode,
                channels_enabled=_ordered_unique([*self.registry.available_tools, "rerank"]),
                channels_used=_ordered_unique(item.tool for item in observations),
                semantic_enabled=semantic_enabled,
                milvus_enabled=semantic_enabled and backend_name == "milvus",
                agentic_enabled=True,
                planner_enabled=True,
                steps=[item.step for item in observations],
                warnings=warnings,
                query_anchor=anchor.model_dump(mode="json"),
                candidate_judgements=[],
                working_set=working_set.model_dump(mode="json"),
                controller_decisions=[item.model_dump(mode="json") for item in tool_traces],
            ),
        )


async def _execute_sdk_tool(
    *,
    registry: RetrievalToolRegistry,
    observations: list[RetrievalToolResult],
    raw_hits: list[RetrievalHit],
    tool_traces: list[AgenticToolTrace],
    working_set: RetrievalWorkingSet,
    transcript: _AgentTranscript | None,
    call: RetrievalToolCall,
    reason: str,
    auto_action: str = "agent_tool",
) -> str:
    call_input = call.model_dump(mode="json")
    if call.tool == "rerank":
        call_input["input_hit_count"] = len(call.candidate_pool)
        call_input["documents"] = _rerank_document_samples(call, registry)
    _append_tool_call(transcript, call_input)
    langfuse_client = _langfuse_client_or_none()
    with _langfuse_tool_observation(langfuse_client, call_input):
        started = time.perf_counter()
        try:
            result = await registry.execute(call)
        except BaseException as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            _update_langfuse_agent_observation(
                langfuse_client,
                level="ERROR",
                status_message=f"{type(exc).__name__}: {exc}",
                output={
                    "status": "failed",
                    "tool": call.tool,
                    "duration_ms": round(duration_ms, 1),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        langfuse_tool_output = {
            "status": "completed",
            "tool": call.tool,
            "duration_ms": round(duration_ms, 1),
            "hit_count": len(result.hits),
            "search_diagnostics": _search_diagnostics_summary(
                result.step.input.get("search_diagnostics", {})
            ),
            "rerank_diagnostics": result.step.input.get("rerank_diagnostics", {}),
            "hit_samples": _hit_summary_payloads(result.hits[:8]),
        }
        _update_langfuse_agent_observation(
            langfuse_client,
            level="DEFAULT",
            status_message="KG retrieval tool completed",
            output=langfuse_tool_output,
        )
    observations.append(result)
    raw_hits.extend(result.hits)
    working_set.tool_call_count += 1
    if call.tool == "open":
        working_set.opened_windows = _ordered_unique(
            [
                *working_set.opened_windows,
                *(hit.hit_id for hit in result.hits),
                *(ref for hit in result.hits for ref in hit.evidence_refs),
            ]
        )
        working_set.evidence_refs = _ordered_unique(
            [
                *working_set.evidence_refs,
                *(ref for hit in result.hits for ref in hit.evidence_refs),
            ]
        )
    tool_traces.append(
        AgenticToolTrace(
            tool_name=call.tool,
            tool_input=call_input,
            raw_candidate_count=len(result.hits),
            package_count=len(result.hits) if call.tool == "rerank" else 0,
            decision_reason=reason,
            tool_duration_ms=round(duration_ms, 1),
            auto_action=auto_action,
            auto_action_reason="openai_agents_sdk",
        )
    )
    payload = {
        "tool": call.tool,
        "hit_count": len(result.hits),
        "search_diagnostics": result.step.input.get("search_diagnostics", {}),
        "rerank_diagnostics": result.step.input.get("rerank_diagnostics", {}),
        "hits": _hit_payloads(result.hits[:12]),
    }
    _append_tool_result(transcript, call_input, duration_ms=round(duration_ms, 1), payload=payload)
    trace_payload = _compact_trace_tool_payload(payload)
    trace_agentic_event(
        "openai_agents_tool_result",
        {
            "event_meaning": "OpenAI Agents SDK called a KG retrieval tool.",
            "tool": call.tool,
            "input": call_input,
            "duration_ms": round(duration_ms, 1),
            **trace_payload,
        },
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _load_agents_sdk() -> Any | None:
    try:
        import agents  # type: ignore
        from openai import AsyncOpenAI
        from agents import OpenAIChatCompletionsModel
    except Exception:
        return None
    return _SDK(
        Agent=agents.Agent,
        Runner=agents.Runner,
        ModelSettings=agents.ModelSettings,
        function_tool=agents.function_tool,
        flush_traces=agents.flush_traces,
        set_tracing_disabled=agents.set_tracing_disabled,
        set_trace_processors=agents.set_trace_processors,
        AsyncOpenAI=AsyncOpenAI,
        OpenAIChatCompletionsModel=OpenAIChatCompletionsModel,
    )


class _SDK:
    def __init__(self, **items: Any) -> None:
        self.__dict__.update(items)


def _sdk_model(sdk: Any) -> Any:
    _configure_sdk_tracing(sdk)
    client = sdk.AsyncOpenAI(
        api_key=_agent_api_key(),
        base_url=_agent_base_url(),
    )
    return sdk.OpenAIChatCompletionsModel(
        model=_agent_model_name(),
        openai_client=client,
    )


def _agent_input(query: str, bootstrap_result: RetrievalToolResult) -> str:
    return json.dumps(_agent_input_payload(query, bootstrap_result), ensure_ascii=False, separators=(",", ":"))


def _agent_input_payload(query: str, bootstrap_result: RetrievalToolResult) -> dict[str, Any]:
    return {
        "query": query,
        "bootstrap_observation": {
            "tool": bootstrap_result.tool,
            "hit_count": len(bootstrap_result.hits),
            "search_diagnostics": bootstrap_result.step.input.get("search_diagnostics", {}),
            "rerank_diagnostics": bootstrap_result.step.input.get("rerank_diagnostics", {}),
            "hits": _hit_payloads(bootstrap_result.hits[:12]),
        },
        "instruction": "基于 bootstrap_observation 决定是否继续调用工具；最终返回 JSON。",
    }


def _hit_payloads(hits: list[RetrievalHit]) -> list[dict[str, Any]]:
    return [
        {
            "id": hit.hit_id,
            "type": hit.hit_type,
            "title": hit.title,
            "channels": hit.source_channels or [hit.source],
            "raw_scores": _rounded_score_map(hit.raw_scores),
            "channel_ranks": dict(hit.channel_ranks or {}),
            "evidence_refs": hit.evidence_refs[:5],
            "matched_terms": hit.matched_terms[:8],
            "matched_fields": hit.matched_fields[:8],
            "snippet": (hit.snippet or "")[:360],
        }
        for hit in hits
    ]


def _rerank_document_samples(call: RetrievalToolCall, registry: RetrievalToolRegistry, *, limit: int = 8) -> list[str]:
    if call.tool != "rerank" or not call.candidate_pool or not call.query:
        return []
    try:
        preparation = prepare_rerank_candidates(
            call.query,
            call.candidate_pool,
            max_documents=registry.reranker_max_documents,
        )
    except Exception:
        return []
    return [candidate.document for candidate in preparation.candidates[:limit]]


def _hit_summary_payloads(hits: list[RetrievalHit]) -> list[dict[str, Any]]:
    return [
        {
            "id": hit.hit_id,
            "type": hit.hit_type,
            "title": hit.title,
            "channels": hit.source_channels or [hit.source],
            "raw_scores": _rounded_score_map(hit.raw_scores),
            "channel_ranks": dict(hit.channel_ranks or {}),
            "evidence_refs": hit.evidence_refs[:3],
        }
        for hit in hits
    ]


def _rounded_score_map(value: dict[str, float] | None) -> dict[str, float]:
    return {str(key): round(float(score or 0.0), 4) for key, score in (value or {}).items()}


def _normalize_include_neighbors(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"one_hop", "window"}:
        return normalized
    return "none"


def _search_diagnostics_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep_keys = [
        "pre_dedupe_counts",
        "merged_channel_counts",
        "selected_merged_channel_counts",
        "query_parser",
        "vector_semantic",
        "semantic_hybrid",
        "graph_seed_nodes",
        "max_hits",
    ]
    return {key: value[key] for key in keep_keys if key in value}


def _compact_trace_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    hits = payload.get("hits")
    return {
        "tool": payload.get("tool"),
        "hit_count": payload.get("hit_count"),
        "search_diagnostics": _search_diagnostics_summary(payload.get("search_diagnostics")),
        "rerank_diagnostics": payload.get("rerank_diagnostics") or {},
        "hit_samples": _compact_hit_payloads_for_trace(hits if isinstance(hits, list) else []),
    }


def _compact_hit_payloads_for_trace(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for hit in hits[:8]:
        if not isinstance(hit, dict):
            continue
        result.append(
            {
                "id": hit.get("id"),
                "type": hit.get("type"),
                "title": hit.get("title"),
                "channels": hit.get("channels"),
                "raw_scores": hit.get("raw_scores"),
                "channel_ranks": hit.get("channel_ranks"),
                "evidence_refs": (hit.get("evidence_refs") or [])[:3],
            }
        )
    return result


def _parse_agent_final_output(output: Any) -> dict[str, Any]:
    text = str(output or "").strip()
    if not text:
        return {"stop_reason": "openai_agents_done", "selected_candidate_ids": [], "evidence_ids": [], "reason": ""}
    json_text = _extract_agent_json_text(text)
    try:
        parsed = json.loads(json_text)
    except Exception:
        recovered = _recover_agent_final_fields(text)
        recovered["raw_text"] = text[:4000]
        return recovered
    if not isinstance(parsed, dict):
        return {"stop_reason": "openai_agents_done", "selected_candidate_ids": [], "evidence_ids": [], "reason": ""}
    return {
        "stop_reason": str(parsed.get("stop_reason") or "openai_agents_done"),
        "selected_candidate_ids": _string_list(parsed.get("selected_candidate_ids")),
        "evidence_ids": _string_list(parsed.get("evidence_ids")),
        "reason": str(parsed.get("reason") or ""),
    }


def _extract_agent_json_text(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return text


def _recover_agent_final_fields(text: str) -> dict[str, Any]:
    return {
        "stop_reason": _recover_json_string_field(text, "stop_reason") or "openai_agents_done",
        "selected_candidate_ids": _recover_json_string_array(text, "selected_candidate_ids"),
        "evidence_ids": _recover_json_string_array(text, "evidence_ids"),
        "reason": _recover_json_string_field(text, "reason") or "",
    }


def _recover_json_string_field(text: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"(.+?)"\s*(?:,|\n\s*\}})', text, flags=re.DOTALL)
    if not match:
        return ""
    return match.group(1).replace('\\"', '"').strip()


def _recover_json_string_array(text: str, field: str) -> list[str]:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*\[(.*?)\]', text, flags=re.DOTALL)
    if not match:
        return []
    return [item.group(1) for item in re.finditer(r'"([^"]+)"', match.group(1))]


def _select_agent_hits(
    hits: list[RetrievalHit],
    *,
    selected_candidate_ids: list[str],
    evidence_ids: list[str],
    max_hits: int,
) -> list[RetrievalHit]:
    selected_ids = set(selected_candidate_ids)
    selected_evidence = set(evidence_ids)
    selected: list[RetrievalHit] = []
    selected_hit_ids: set[str] = set()
    for hit in hits:
        if hit.hit_id in selected_ids or selected_evidence.intersection(hit.evidence_refs):
            selected.append(hit)
            selected_hit_ids.add(hit.hit_id)
        if len(selected) >= max_hits:
            break
    if selected_evidence and len(selected) < max_hits:
        for hit in hits:
            if hit.hit_id in selected_hit_ids:
                continue
            if hit.hit_id in selected_evidence:
                selected.append(hit)
                selected_hit_ids.add(hit.hit_id)
            if len(selected) >= max_hits:
                break
    return dedupe_hits(selected)[:max_hits]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _ordered_unique(str(item).strip() for item in value if str(item or "").strip())


def _ordered_unique(values) -> list:
    result: list = []
    seen: set = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


class _AgentTranscript(list[str]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, item: str) -> None:
        super().append(item)
        _write_transcript_lines(self.path, [item])

    def extend(self, items) -> None:
        lines = [str(item) for item in items]
        super().extend(lines)
        _write_transcript_lines(self.path, lines)


def _write_transcript_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    with _TRANSCRIPT_FILE_LOCK:
        with path.open("a", encoding="utf-8") as file:
            file.write("\n".join(lines))
            file.write("\n")
            file.flush()


def _start_agent_transcript(query: str, bootstrap_result: RetrievalToolResult) -> _AgentTranscript | None:
    if not _agent_transcript_enabled():
        return None
    payload = _agent_input_payload(query, bootstrap_result)
    transcript = _AgentTranscript(_agent_transcript_file())
    transcript.extend([
        "",
        f"# OpenAI Agent A-RAG Transcript {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## User Question",
        "",
        query,
        "",
        "## Agent System Instructions",
        "",
        _SYSTEM_INSTRUCTIONS.strip(),
        "",
        "## Input Sent To Agent",
        "",
        _json_block(_compact_agent_input_payload(payload)),
    ])
    return transcript


def _append_tool_call(transcript: _AgentTranscript | None, call: Any) -> None:
    if transcript is None:
        return
    transcript.extend(
        [
            "",
            f"## Tool Call: {_call_tool_name(call)}",
            "",
            "Arguments:",
            "",
            _json_block(_call_payload(call)),
        ]
    )


def _append_tool_result(
    transcript: _AgentTranscript | None,
    call: Any,
    *,
    duration_ms: float,
    payload: dict[str, Any],
) -> None:
    if transcript is None:
        return
    transcript.extend(
        [
            "",
            f"## Tool Result: {_call_tool_name(call)}",
            "",
            f"duration_ms: {duration_ms}",
            "",
            _json_block(_compact_tool_payload(payload)),
        ]
    )


def _append_agent_run_items(transcript: _AgentTranscript | None, run_result: Any) -> None:
    if transcript is None:
        return
    items = list(getattr(run_result, "new_items", []) or [])
    if not items:
        transcript.extend(["", "## Agent SDK Run Items", "", "(no SDK run items exposed)"])
        return
    transcript.extend(["", "## Agent SDK Run Items"])
    for index, item in enumerate(items[:60], start=1):
        kind = getattr(item, "type", None) or item.__class__.__name__
        raw = _dumpable(getattr(item, "raw_item", item))
        transcript.extend(["", f"### {index}. {kind}", ""])
        summary = _summarize_sdk_item(kind, raw)
        if summary:
            transcript.append(summary)
        else:
            transcript.append(_json_block(_compact_unknown_payload(raw)))
    if len(items) > 60:
        transcript.append(f"\n... omitted {len(items) - 60} SDK item(s)")


def _finish_agent_transcript(
    transcript: _AgentTranscript | None,
    *,
    duration_ms: float,
    final_output: Any,
    parsed_output: dict[str, Any],
    selected_hits: list[RetrievalHit],
    evidence_refs: list[str],
    warnings: list[str],
) -> None:
    if transcript is None:
        return
    transcript.extend(
        [
            "",
            "## Agent Final Answer",
            "",
            f"runner_duration_ms: {duration_ms}",
            "",
            "Raw final_output:",
            "",
            _text_block(str(final_output or "")),
            "",
            "Parsed final_output:",
            "",
            _json_block(parsed_output),
            "",
            "Selected hits:",
            "",
            _json_block(_hit_payloads(selected_hits)),
            "",
            "Evidence refs:",
            "",
            _json_block(evidence_refs),
            "",
            "Warnings:",
            "",
            _json_block(warnings),
            "",
            "---",
            "",
        ]
    )
def _abort_agent_transcript(
    transcript: _AgentTranscript | None,
    *,
    duration_ms: float,
    exc: BaseException,
    raw_hits: list[RetrievalHit],
    observations: list[RetrievalToolResult],
) -> None:
    if transcript is None:
        return
    transcript.extend(
        [
            "",
            "## Agent Run Failed",
            "",
            f"runner_duration_ms: {duration_ms}",
            f"error_type: {type(exc).__name__}",
            f"error: {exc}",
            f"tool_observations: {len(observations)}",
            "",
            "Partial known hits:",
            "",
            _json_block(_hit_payloads(raw_hits[:12])),
            "",
            "Traceback:",
            "",
            _text_block("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))),
            "",
            "---",
            "",
        ]
    )
def _agent_transcript_enabled() -> bool:
    return os.getenv("KG_OPENAI_AGENTS_TRANSCRIPT", "").strip().lower() in {"1", "true", "yes", "on"}


def _agent_transcript_file() -> Path:
    raw = os.getenv("KG_OPENAI_AGENTS_TRANSCRIPT_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    trace_file = os.getenv("KG_RETRIEVAL_LLM_TRACE_FILE", "").strip()
    if trace_file:
        return Path(trace_file).expanduser().with_name("generated_openai_agent_arag_transcript.md")
    return Path("generated_openai_agent_arag_transcript.md")


def _compact_agent_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    observation = dict(payload.get("bootstrap_observation") or {})
    hits = observation.get("hits")
    if isinstance(hits, list):
        observation["hits"] = hits[:8]
        observation["omitted_hits"] = max(0, len(hits) - 8)
    return {
        "query": payload.get("query"),
        "bootstrap_observation": observation,
        "instruction": payload.get("instruction"),
    }


def _compact_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    hits = compact.get("hits")
    if isinstance(hits, list):
        compact["hits"] = hits[:8]
        compact["omitted_hits"] = max(0, len(hits) - 8)
    return compact


def _compact_unknown_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact_unknown_payload(item) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact_unknown_payload(item) for item in value[:20]]
    if isinstance(value, str):
        return _clip(value, 2000)
    return value


def _append_generation_span_to_transcript(event: str, exported: Any) -> None:
    if event != "span_end" or not _agent_transcript_enabled() or not isinstance(exported, dict):
        return
    span_data = exported.get("span_data")
    if not isinstance(span_data, dict) or span_data.get("type") != "generation":
        return
    path = _agent_transcript_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "",
        "## LLM Request / Response",
        "",
        f"model: {span_data.get('model') or '-'}",
        f"span_id: {exported.get('id') or '-'}",
        f"started_at: {exported.get('started_at') or '-'}",
        f"ended_at: {exported.get('ended_at') or '-'}",
        "",
        "Request messages/context:",
        "",
        _json_block(_compact_llm_messages(span_data.get("input"))),
        "",
        "Model output:",
        "",
        _json_block(_compact_llm_messages(span_data.get("output"))),
        "",
        "Usage:",
        "",
        _json_block(span_data.get("usage") or {}),
    ]
    _write_transcript_lines(path, lines)


def _compact_llm_messages(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    result: list[Any] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            result.append(_clip(str(item), 4000))
            continue
        compact: dict[str, Any] = {}
        for key in ("role", "type", "name", "id", "call_id", "status"):
            if key in item:
                compact[key] = item[key]
        if "content" in item:
            compact["content"] = _compact_llm_content(item.get("content"))
        for key in ("tool_calls", "function_call", "arguments", "output", "text"):
            if key in item:
                compact[key] = _compact_llm_content(item.get(key))
        remaining = {
            key: value
            for key, value in item.items()
            if key not in {*compact.keys(), "content", "tool_calls", "function_call", "arguments", "output", "text"}
        }
        if remaining:
            compact["extra"] = _compact_unknown_payload(remaining)
        result.append(compact)
    if len(value) > 20:
        result.append({"omitted_messages": len(value) - 20})
    return result


def _compact_llm_content(value: Any) -> Any:
    if isinstance(value, str):
        return _clip(value, 12000)
    if isinstance(value, list):
        return [_compact_llm_content(item) for item in value[:20]]
    if isinstance(value, dict):
        return {key: _compact_llm_content(item) for key, item in value.items()}
    return value


def _summarize_sdk_item(kind: str, raw: Any) -> str:
    payload = raw if isinstance(raw, dict) else {}
    text = _extract_message_text(payload)
    if text:
        return _text_block(text)
    tool_name = payload.get("name") or payload.get("tool_name") or payload.get("type")
    arguments = payload.get("arguments") or payload.get("args")
    if tool_name or arguments:
        return _json_block({"tool": tool_name, "arguments": arguments, "call_id": payload.get("call_id")})
    output = payload.get("output")
    if output is not None:
        return _text_block(_clip(str(output), 4000))
    return ""


def _extract_message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    texts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content") or item.get("output_text")
            if text:
                texts.append(str(text))
    elif isinstance(content, str):
        texts.append(content)
    text = payload.get("text") or payload.get("output_text")
    if text:
        texts.append(str(text))
    return "\n".join(_ordered_unique(item for item in texts if item))


def _call_tool_name(call: Any) -> str:
    if isinstance(call, dict):
        return str(call.get("tool") or call.get("name") or "unknown")
    return str(getattr(call, "tool", None) or getattr(call, "name", None) or "unknown")


def _call_payload(call: Any) -> dict[str, Any]:
    if isinstance(call, dict):
        return call
    if hasattr(call, "model_dump"):
        return call.model_dump(mode="json")
    return _dumpable(call)


def _dumpable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dumpable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_dumpable(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _json_block(value: Any) -> str:
    return "```json\n" + _clip(json.dumps(value, ensure_ascii=False, indent=2, default=str), 12000) + "\n```"


def _text_block(value: str) -> str:
    return "```text\n" + _clip(value, 12000) + "\n```"


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


class _LocalJsonlTraceProcessor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def on_trace_start(self, trace: Any) -> None:
        self._write("trace_start", trace)

    def on_trace_end(self, trace: Any) -> None:
        self._write("trace_end", trace)

    def on_span_start(self, span: Any) -> None:
        self._write("span_start", span)

    def on_span_end(self, span: Any) -> None:
        self._write("span_end", span)

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def _write(self, event: str, item: Any) -> None:
        try:
            exported = item.export() if hasattr(item, "export") else _dumpable(item)
            record = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "event": event,
                "payload": exported,
            }
            with self._lock:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            _append_generation_span_to_transcript(event, exported)
        except Exception:
            return None


def _configure_sdk_tracing(sdk: Any) -> None:
    local_trace_file = _agent_local_trace_file()
    if local_trace_file is not None:
        sdk.set_tracing_disabled(False)
        if hasattr(sdk, "set_trace_processors"):
            sdk.set_trace_processors([_LocalJsonlTraceProcessor(local_trace_file)])
        return
    if _langfuse_enabled():
        sdk.set_tracing_disabled(False)
        return
    if _agent_trace_to_openai_enabled():
        sdk.set_tracing_disabled(False)
        return
    sdk.set_tracing_disabled(True)


def _flush_sdk_traces(sdk: Any) -> None:
    if not hasattr(sdk, "flush_traces"):
        return None
    try:
        sdk.flush_traces()
    except Exception:
        return None


def _agent_local_trace_file() -> Path | None:
    raw = os.getenv("KG_OPENAI_AGENTS_LOCAL_TRACE_FILE", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _agent_trace_to_openai_enabled() -> bool:
    return os.getenv("KG_OPENAI_AGENTS_TRACE_TO_OPENAI", "").strip().lower() in {"1", "true", "yes", "on"}


def _configure_langfuse_observability() -> Any | None:
    if not _langfuse_enabled():
        return None
    try:
        from langfuse import get_client
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
    except Exception as exc:
        raise RuntimeError(
            "Langfuse observability is enabled but dependencies are missing. "
            "Install: pip install langfuse openinference-instrumentation-openai-agents"
        ) from exc

    global _LANGFUSE_INSTRUMENTED, _LANGFUSE_AUTH_CHECKED
    with _LANGFUSE_LOCK:
        if not _LANGFUSE_INSTRUMENTED:
            OpenAIAgentsInstrumentor().instrument()
            _LANGFUSE_INSTRUMENTED = True
            logger.info(
                "Langfuse OpenAI Agents instrumentation enabled: host=%s auth_check=%s",
                os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "<unset>",
                _langfuse_auth_check_enabled(),
            )

    client = get_client()
    if _langfuse_auth_check_enabled() and not _LANGFUSE_AUTH_CHECKED:
        try:
            authenticated = client.auth_check()
        except Exception as exc:
            raise RuntimeError(
                "Langfuse authentication failed. Check LANGFUSE_PUBLIC_KEY, "
                "LANGFUSE_SECRET_KEY and LANGFUSE_HOST. "
                f"Current host={os.getenv('LANGFUSE_HOST') or os.getenv('LANGFUSE_BASE_URL') or '<unset>'}."
            ) from exc
        if not authenticated:
            raise RuntimeError(
                "Langfuse authentication failed. Check LANGFUSE_PUBLIC_KEY, "
                "LANGFUSE_SECRET_KEY and LANGFUSE_HOST."
            )
        _LANGFUSE_AUTH_CHECKED = True
    return client


def _langfuse_run_context(query: str, *, adapter: str):
    if not _langfuse_enabled():
        return nullcontext()
    try:
        from langfuse import propagate_attributes
    except Exception:
        return nullcontext()
    session_id = _langfuse_session_id()
    return propagate_attributes(
        session_id=session_id,
        tags=_langfuse_tags(),
        metadata={
            "component": "kg_openai_agents_arag",
            "adapter": adapter,
            "query": query,
            "session_id": session_id,
            "base_url": _agent_base_url_for_trace(),
            "model": _agent_model_name(),
        },
        version=os.getenv("KG_LANGFUSE_VERSION", "").strip() or None,
        trace_name=f"kg-openai-agent-arag:{adapter}",
    )


def _langfuse_agent_observation(
    client: Any | None,
    *,
    query: str,
    adapter: str,
    bootstrap_hits: int,
    max_turns: int,
):
    if client is None:
        return nullcontext()
    try:
        return client.start_as_current_observation(
            name="kg-openai-agent-arag-run",
            as_type="agent",
            input={"query": query},
            metadata={
                "component": "kg_openai_agents_arag",
                "adapter": adapter,
                "session_id": _langfuse_session_id(),
                "bootstrap_hits": bootstrap_hits,
                "max_turns": max_turns,
                "base_url": _agent_base_url_for_trace(),
                "model": _agent_model_name(),
            },
            version=os.getenv("KG_LANGFUSE_VERSION", "").strip() or None,
        )
    except Exception as exc:
        logger.warning("Langfuse agent observation start failed: %s", exc)
        return nullcontext()


def _update_langfuse_agent_observation(
    client: Any | None,
    *,
    level: str,
    status_message: str,
    output: dict[str, Any],
) -> None:
    if client is None:
        return
    try:
        client.update_current_span(
            output=output,
            level=level,
            status_message=status_message,
        )
    except Exception as exc:
        logger.warning("Langfuse agent observation update failed: %s", exc)


def _langfuse_client_or_none() -> Any | None:
    if not _langfuse_enabled():
        return None
    try:
        from langfuse import get_client
    except Exception:
        return None
    return get_client()


def _langfuse_tool_observation(client: Any | None, call: Any):
    if client is None:
        return nullcontext()
    if isinstance(call, dict):
        payload = call
        tool_name = str(call.get("tool") or call.get("name") or "<unknown>")
    else:
        tool_name = str(getattr(call, "tool", "<unknown>"))
        try:
            payload = call.model_dump(mode="json")
        except Exception:
            payload = {"tool": tool_name}
    try:
        return client.start_as_current_observation(
            name=f"kg_tool:{tool_name}",
            as_type="tool",
            input=payload,
            metadata={"component": "kg_openai_agents_arag_tool"},
            version=os.getenv("KG_LANGFUSE_VERSION", "").strip() or None,
        )
    except Exception as exc:
        logger.warning("Langfuse tool observation start failed: %s", exc)
        return nullcontext()


def _flush_langfuse(client: Any | None) -> None:
    if client is None:
        return
    try:
        client.flush()
        logger.info("Langfuse trace flush requested")
    except Exception as exc:
        logger.warning("Langfuse trace flush failed: %s", exc)
        return


def _langfuse_enabled() -> bool:
    return os.getenv("KG_LANGFUSE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _langfuse_auth_check_enabled() -> bool:
    return os.getenv("KG_LANGFUSE_AUTH_CHECK", "1").strip().lower() not in {"0", "false", "no", "off"}


def _langfuse_tags() -> list[str]:
    raw = os.getenv("KG_LANGFUSE_TAGS", "").strip()
    tags = [item.strip() for item in raw.split(",") if item.strip()]
    return _ordered_unique([*tags, "kg", "openai-agents", "agentic-rag"])


def _langfuse_session_id() -> str | None:
    raw = (
        os.getenv("KG_LANGFUSE_SESSION_ID", "").strip()
        or os.getenv("LANGFUSE_SESSION_ID", "").strip()
    )
    return _normalize_langfuse_session_id(raw)


def _normalize_langfuse_session_id(value: str) -> str | None:
    if not value:
        return None
    normalized = "".join(char if 32 <= ord(char) <= 126 else "-" for char in value)
    normalized = re.sub(r"\s+", "-", normalized).strip("-")
    if not normalized:
        return None
    return normalized[:199]


def _sdk_disabled() -> bool:
    return os.getenv("KG_OPENAI_AGENTS_SDK_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}


def _agent_model_name() -> str:
    return (
        os.getenv("KG_OPENAI_AGENTS_MODEL", "").strip()
        or resolve_kg_llm_model("kg_agentic_retrieval")
    )


def _agent_base_url() -> str:
    return os.getenv("KG_OPENAI_AGENTS_BASE_URL", "").strip() or settings.DEEPSEEK_BASE_URL


def _agent_base_url_for_trace() -> str:
    return _agent_base_url().rstrip("/")


def _agent_api_key() -> str:
    return os.getenv("KG_OPENAI_AGENTS_API_KEY", "").strip() or settings.DEEPSEEK_API_KEY or "unused"


def _agent_max_tokens() -> int:
    raw = os.getenv("KG_OPENAI_AGENTS_MAX_TOKENS", "").strip()
    if raw.isdigit():
        return max(256, int(raw))
    return 2048
