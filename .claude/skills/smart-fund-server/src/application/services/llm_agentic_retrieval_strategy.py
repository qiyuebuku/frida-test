"""LLM-backed strategy for agentic KG retrieval."""

from __future__ import annotations

import json
from typing import Any

from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalDecision,
    AgenticRetrievalStrategy,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolResult
from src.infrastructure.llm_proxy.service import ClaudeProxyRequest, get_claude_proxy_service


class LLMAgenticRetrievalStrategy(AgenticRetrievalStrategy):
    """Ask the LLM to choose the next whitelisted retrieval tool."""

    async def next_decision(
        self,
        *,
        query: str,
        observations: list[RetrievalToolResult],
        constraints: AgenticRetrievalConstraints,
    ) -> AgenticRetrievalDecision:
        response = await get_claude_proxy_service().generate(
            ClaudeProxyRequest(
                system_prompt=_SYSTEM_PROMPT,
                prompt=_prompt(query, observations, constraints),
                json_schema=_DECISION_SCHEMA,
                metadata={"task": "kg_agentic_retrieval", "observations": len(observations)},
                use_cache=False,
            )
        )
        payload = response.structured_output if isinstance(response.structured_output, dict) else None
        if payload is None:
            payload = _parse_json_object(response.text)
        return _decision_from_payload(payload or {}, query=query)


_SYSTEM_PROMPT = """你是金融知识图谱检索规划器。你只能选择白名单工具，不能回答用户问题。
目标是用尽量少的步骤找到可追溯 evidence。每次只返回一个 JSON 决策。"""


_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "stop": {"type": "boolean"},
        "stop_reason": {"type": "string"},
        "tool": {
            "type": "string",
            "enum": [
                "entity_resolve",
                "semantic_hybrid_search",
                "graph_search",
                "wiki_search",
                "chunk_read",
            ],
        },
        "args": {"type": "object"},
    },
    "required": ["stop"],
}


def _prompt(
    query: str,
    observations: list[RetrievalToolResult],
    constraints: AgenticRetrievalConstraints,
) -> str:
    return json.dumps(
        {
            "query": query,
            "constraints": constraints.model_dump(),
            "available_tools": {
                "entity_resolve": {"args": {"query": "string", "limit": "int optional"}},
                "semantic_hybrid_search": {"args": {"query": "string", "limit": "int optional"}},
                "graph_search": {
                    "args": {
                        "seed_node_ids": "list[string]",
                        "depth": "int optional",
                        "limit": "int optional",
                        "direction": "incoming|outgoing|undirected|path optional",
                        "relation_filters": "list[string] optional",
                    }
                },
                "wiki_search": {"args": {"query": "string", "limit": "int optional"}},
                "chunk_read": {"args": {"evidence_ids": "list[string]", "limit": "int optional"}},
            },
            "observations": [_observation_payload(item) for item in observations],
            "decision_contract": {
                "continue": {"stop": False, "tool": "tool_name", "args": {}},
                "stop": {"stop": True, "stop_reason": "evidence_sufficient|no_more_useful_tools"},
            },
        },
        ensure_ascii=False,
    )


def _observation_payload(result: RetrievalToolResult) -> dict[str, Any]:
    return {
        "tool": result.tool,
        "hit_count": len(result.hits),
        "hits": [
            {
                "hit_id": hit.hit_id,
                "type": hit.hit_type,
                "title": hit.title,
                "score": hit.score,
                "node_refs": hit.node_refs,
                "edge_refs": hit.edge_refs,
                "evidence_refs": hit.evidence_refs,
                "snippet": hit.snippet[:500],
            }
            for hit in result.hits[:8]
        ],
    }


def _decision_from_payload(payload: dict[str, Any], *, query: str) -> AgenticRetrievalDecision:
    if payload.get("stop"):
        return AgenticRetrievalDecision(
            stop=True,
            stop_reason=str(payload.get("stop_reason") or "strategy_stop"),
        )
    tool = str(payload.get("tool") or "").strip()
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    if tool in {"entity_resolve", "semantic_hybrid_search", "wiki_search"}:
        args.setdefault("query", query)
    if tool == "graph_search":
        args.setdefault("seed_node_ids", [])
    if tool == "chunk_read":
        args.setdefault("evidence_ids", [])
    return AgenticRetrievalDecision(tool_call=RetrievalToolCall(tool=tool, **args))


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None
