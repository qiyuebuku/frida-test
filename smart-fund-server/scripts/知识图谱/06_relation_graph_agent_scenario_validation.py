#!/usr/bin/env python3
"""Run a real tool-calling Agent against production relationship-graph APIs."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.llm_proxy import (  # noqa: E402
    LLMProxyRequest,
    get_llm_gateway_service,
)
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)


DEFAULT_DATASET = (
    PROJECT_ROOT
    / "scripts"
    / "知识图谱"
    / "datasets"
    / "relation_graph_agent_production_cases.json"
)

AGENT_SYSTEM_PROMPT = """\
你是金融关系图检索 Agent。你只能依据工具返回的 Cognitive Card、Edge 和 Community 回答。

工作规则：
1. 必须先用 kg_relation_graph_search 找到语义种子，不能凭记忆回答。
2. Search 只证明文本相似，不证明事实之间存在关系。需要回答因果、冲突、印证、进展、共同驱动或市场联动时，先用 expand 找到 Edge，再用 kg_edge_open 核验关系依据。
3. 需要陈述某个事实时，用 kg_card_open 或 kg_edge_open 取得焦点原文；不能把未打开的摘要包装成已核验事实。
4. 面对跨领域或全局问题，可以打开或展开 Community；不要为了形式机械调用所有工具。
5. 相同事实的重复报道只作为热度或印证，不要重复罗列为多个独立结论。
6. 如果图中没有足够关系证据，明确说明证据不足，不得自行补全关系。
7. 工具可多轮调用。优先小范围检索和一跳展开，不足时再扩大。
8. 第一次展开使用 hop_limit=1、node_limit 不超过20、edge_limit 不超过30，
   并按问题指定 relation_kinds；只有结果不足时才扩大。不要并行发起含义重复的搜索。
9. 一到两条已打开的 observed Edge 已能直接回答问题时立即停止；用户未要求时，
   不要继续穷举重复报道、盘中每个报价点或反向案例。
10. Community 成员和 Search/Expand Card 只有摘要。最终回答前逐条检查 Card 引用：
    未经 kg_card_open 或 kg_edge_open 打开的 Card 不得作为事实引用，直接删掉即可。

完成检索后直接用中文回答。关键事实后标注 [Card:完整ID]，关系结论后标注
[Edge:完整ID]，使用社区时标注 [Community:完整ID]。ID 必须来自本轮工具返回。
如果没有关系证据，回答中必须明确写“证据不足”，且不能引用不存在的 Edge。\
"""


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "kg_relation_graph_search",
            "description": (
                "按自然语言语义检索关系图中的 Card 种子。只用于找入口，"
                "搜索命中本身不能证明 Card 之间存在关系。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "seed_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                    },
                    "candidate_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 60,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_card_expand",
            "description": (
                "从一个或多个 Card 沿已核验 Edge 展开一至两跳，返回相关 "
                "Card、Edge 和所属 Community。用于发现关系，不能替代打开证据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 12,
                    },
                    "hop_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                    },
                    "node_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                    },
                    "edge_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 40,
                    },
                    "relation_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "decision_classes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["observed", "inferred"],
                        },
                    },
                    "min_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["card_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_card_open",
            "description": (
                "按 ID 打开 Card，取得原子事实摘要、焦点原文、来源、时间和"
                "相邻关系 ID。用于核验单个事实。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                    },
                    "incident_edge_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["card_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_edge_open",
            "description": (
                "按 ID 打开已核验 Edge，取得关系类型、方向、置信度、关系依据"
                "以及两端 Card 的焦点原文。任何关系性结论都应优先用此工具核验。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 30,
                    }
                },
                "required": ["edge_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_community_expand",
            "description": (
                "从 Community 沿跨社区关系扩展，用于发现相邻事件群和跨社区"
                "传导；返回概览，不返回成员原文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "community_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                    },
                    "hop_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                    },
                    "community_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 40,
                    },
                    "relation_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "relation_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["community_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_community_open",
            "description": (
                "打开 Community 的成员 Card 摘要和内部 Edge。适合回答一个"
                "事件群的全貌，不适合只涉及两个事件的简单关系问题；若要核验"
                "具体事实或关系，继续打开 Card 或 Edge。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "community_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 6,
                    },
                    "member_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                    },
                    "edge_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["community_ids"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_PATHS = {
    "kg_relation_graph_search": "/api/kg/agent/relation-graph/search",
    "kg_card_expand": "/api/kg/agent/relation-graph/cards/expand",
    "kg_card_open": "/api/kg/agent/relation-graph/cards/open",
    "kg_edge_open": "/api/kg/agent/relation-graph/edges/open",
    "kg_community_expand": "/api/kg/agent/relation-graph/communities/expand",
    "kg_community_open": "/api/kg/agent/relation-graph/communities/open",
}


@dataclass(frozen=True)
class AgentConfig:
    api_base_url: str
    target: str
    adapter_name: str
    session_id: str
    model: str
    provider: str | None
    max_tool_rounds: int
    llm_timeout: float
    tool_timeout: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="让真实 LLM Agent 自主调用生产关系图 Tool，并按金标验收"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--api-base-url",
        default="http://119.23.227.187:8900",
    )
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--provider", default="")
    parser.add_argument("--max-tool-rounds", type=int, default=8)
    parser.add_argument("--llm-timeout", type=float, default=120.0)
    parser.add_argument("--tool-timeout", type=float, default=60.0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("dataset 必须是 JSON 数组或包含 cases 数组")
    selected_ids = {
        str(case_id).strip()
        for case_id in args.case_id
        if str(case_id).strip()
    }
    result = [
        dict(case)
        for case in cases
        if isinstance(case, dict)
        and (
            not selected_ids
            or str(case.get("case_id") or "") in selected_ids
        )
    ]
    if args.limit > 0:
        result = result[: args.limit]
    if not result:
        raise ValueError("没有可执行的测试用例")
    for index, case in enumerate(result, start=1):
        if not str(case.get("query") or "").strip():
            raise ValueError(f"第 {index} 个 case 缺少 query")
        case.setdefault("case_id", f"case-{index}")
    return result


async def run_agent_case(
    case: dict[str, Any],
    *,
    config: AgentConfig,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": str(case["query"]).strip()},
    ]
    transcript: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    final_text = ""
    failure = ""

    with langfuse_observation(
        name="kg.relation_graph_agent.scenario_case",
        as_type="span",
        input={
            "case_id": case["case_id"],
            "query": case["query"],
            "expectations": {
                key: case.get(key)
                for key in (
                    "expected_card_ids",
                    "expected_edge_ids",
                    "expected_relation_kinds",
                    "forbidden_card_ids",
                    "required_tools",
                )
            },
        },
        metadata={"case_id": case["case_id"], "model": config.model},
    ):
        for round_index in range(1, config.max_tool_rounds + 2):
            try:
                async with asyncio.timeout(config.llm_timeout):
                    response = await get_llm_gateway_service().generate(
                        LLMProxyRequest(
                            model=config.model,
                            provider=config.provider,
                            messages=messages,
                            tools=TOOL_DEFINITIONS,
                            tool_choice="auto",
                            temperature=0.0,
                            max_tokens=4096,
                            metadata={
                                "task": "kg_relation_graph_agent_scenario",
                                "case_id": case["case_id"],
                                "round": round_index,
                            },
                            use_cache=False,
                        )
                    )
            except TimeoutError:
                failure = f"LLM 第 {round_index} 轮超过 {config.llm_timeout}s"
                break
            _merge_usage(total_usage, response.usage)
            raw_message = dict(
                (response.raw_payload or {}).get("message") or {}
            )
            tool_calls = raw_message.get("tool_calls") or []
            if not tool_calls:
                final_text = str(response.text or raw_message.get("content") or "")
                break
            if round_index > config.max_tool_rounds:
                failure = f"超过最大工具调用轮数 {config.max_tool_rounds}"
                break

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": raw_message.get("content"),
                "tool_calls": tool_calls,
            }
            if raw_message.get("reasoning_content"):
                assistant_message["reasoning_content"] = raw_message[
                    "reasoning_content"
                ]
            messages.append(assistant_message)

            for tool_call in tool_calls:
                call_id = str(tool_call.get("id") or "").strip()
                function = tool_call.get("function") or {}
                tool_name = str(function.get("name") or "").strip()
                arguments, argument_error = _parse_tool_arguments(
                    function.get("arguments")
                )
                if argument_error:
                    output = {"error": argument_error}
                else:
                    output = await _call_tool(
                        client,
                        tool_name=tool_name,
                        arguments=arguments,
                        config=config,
                    )
                transcript.append(
                    {
                        "round": round_index,
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "output": output,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": json.dumps(
                            output,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )

        final_payload = _parse_final_payload(final_text)
        final_json_only = _is_json_object_text(final_text)
        evaluation = evaluate_case(
            case,
            transcript=transcript,
            final_payload=final_payload,
            failure=failure,
        )
        result = {
            "case_id": case["case_id"],
            "query": case["query"],
            "passed": evaluation["passed"],
            "failure": failure,
            "evaluation": evaluation,
            "final": final_payload,
            "raw_final_text": final_text if final_payload is None else "",
            "final_response_json_only": final_json_only,
            "tool_calls": [
                {
                    "round": item["round"],
                    "tool_name": item["tool_name"],
                    "arguments": item["arguments"],
                    "output_summary": _tool_output_summary(item["output"]),
                }
                for item in transcript
            ],
            "usage": total_usage,
        }
        langfuse_update_span(
            output=result,
            level="DEFAULT" if evaluation["passed"] else "WARNING",
            status_message="passed" if evaluation["passed"] else "failed",
        )
        return result


async def _call_tool(
    client: httpx.AsyncClient,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    config: AgentConfig,
) -> dict[str, Any]:
    path = TOOL_PATHS.get(tool_name)
    if not path:
        return {"error": f"未知工具: {tool_name}"}
    payload = dict(arguments)
    payload.update(
        {
            "target": config.target,
            "adapter_name": config.adapter_name,
            "session_id": config.session_id,
        }
    )
    _apply_tool_defaults(tool_name, payload)
    try:
        response = await client.post(
            path,
            json=payload,
            timeout=config.tool_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"result": data}
    except httpx.HTTPStatusError as exc:
        return {
            "error": f"HTTP {exc.response.status_code}",
            "detail": exc.response.text[:1000],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:1000]}"}


def _apply_tool_defaults(tool_name: str, payload: dict[str, Any]) -> None:
    if tool_name == "kg_relation_graph_search":
        payload.setdefault("seed_limit", 8)
        payload.setdefault("candidate_limit", 40)
    elif tool_name == "kg_card_expand":
        payload.setdefault("hop_limit", 1)
        payload.setdefault("node_limit", 20)
        payload.setdefault("edge_limit", 30)
        payload.setdefault("decision_classes", ["observed", "inferred"])
        payload.setdefault("min_confidence", 0.0)
    elif tool_name == "kg_card_open":
        payload.setdefault("incident_edge_limit", 40)
    elif tool_name == "kg_community_expand":
        payload.setdefault("hop_limit", 1)
        payload.setdefault("community_limit", 20)
        payload.setdefault("relation_limit", 40)
    elif tool_name == "kg_community_open":
        payload.setdefault("member_limit", 20)
        payload.setdefault("edge_limit", 30)


def evaluate_case(
    case: dict[str, Any],
    *,
    transcript: list[dict[str, Any]],
    final_payload: dict[str, Any] | None,
    failure: str,
) -> dict[str, Any]:
    expected_cards = _string_set(case.get("expected_card_ids"))
    expected_cited_cards = _string_set(
        case.get("expected_cited_card_ids")
    )
    expected_edges = _string_set(case.get("expected_edge_ids"))
    expected_kinds = _string_set(case.get("expected_relation_kinds"))
    forbidden_cards = _string_set(case.get("forbidden_card_ids"))
    required_tools = _string_set(case.get("required_tools"))
    required_any_tool_groups = [
        _string_set(group)
        for group in case.get("required_any_tool_groups", [])
        if isinstance(group, list)
    ]
    called_tools = {
        item["tool_name"]
        for item in transcript
        if item["tool_name"]
    }
    outputs = [item["output"] for item in transcript]
    available_cards = _collect_ids(outputs, "card")
    available_edges = _collect_ids(outputs, "edge")
    available_communities = _collect_ids(outputs, "community")
    available_kinds = _collect_relation_kinds(outputs)
    opened_cards = _opened_ids(transcript, "kg_card_open", "card")
    opened_edges = _opened_ids(transcript, "kg_edge_open", "edge")
    for item in transcript:
        if item["tool_name"] != "kg_edge_open":
            continue
        opened_cards.update(_collect_ids([item["output"]], "card"))

    cited_cards = _string_set(
        final_payload.get("card_ids") if final_payload else []
    )
    cited_edges = _string_set(
        final_payload.get("edge_ids") if final_payload else []
    )
    cited_communities = _string_set(
        final_payload.get("community_ids") if final_payload else []
    )
    expected_insufficient = case.get("expected_insufficient_evidence")
    checks = {
        "completed_without_runtime_failure": not failure,
        "final_answer_parsed": final_payload is not None,
        "final_answer_non_empty": bool(
            str(final_payload.get("answer") or "").strip()
            if final_payload
            else ""
        ),
        "search_was_used": "kg_relation_graph_search" in called_tools,
        "required_tools_used": required_tools.issubset(called_tools),
        "required_any_tool_groups_used": all(
            bool(group.intersection(called_tools))
            for group in required_any_tool_groups
        ),
        "expected_cards_retrieved": expected_cards.issubset(
            available_cards
        ),
        "expected_edges_retrieved": expected_edges.issubset(
            available_edges
        ),
        "expected_edges_opened": expected_edges.issubset(opened_edges),
        "expected_relation_kinds_retrieved": expected_kinds.issubset(
            available_kinds
        ),
        "cited_cards_are_grounded": cited_cards.issubset(
            available_cards
        ),
        "cited_edges_are_grounded": cited_edges.issubset(
            available_edges
        ),
        "cited_communities_are_grounded": cited_communities.issubset(
            available_communities
        ),
        "cited_cards_have_open_evidence": cited_cards.issubset(
            opened_cards
        ),
        "cited_edges_have_open_evidence": cited_edges.issubset(
            opened_edges
        ),
        "expected_cards_cited": expected_cited_cards.issubset(
            cited_cards
        ),
        "expected_edges_cited": expected_edges.issubset(cited_edges),
        "forbidden_cards_not_cited": not forbidden_cards.intersection(
            cited_cards
        ),
        "tool_calls_succeeded": not any(
            item["output"].get("error")
            for item in transcript
            if isinstance(item["output"], dict)
        ),
        "insufficient_evidence_decision_correct": (
            expected_insufficient is None
            or (
                final_payload is not None
                and final_payload.get("insufficient_evidence")
                is bool(expected_insufficient)
            )
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "called_tools": sorted(called_tools),
        "available_card_count": len(available_cards),
        "available_edge_count": len(available_edges),
        "available_community_count": len(available_communities),
        "opened_card_count": len(opened_cards),
        "opened_edge_count": len(opened_edges),
        "missing_expected_card_ids": sorted(
            expected_cards - available_cards
        ),
        "missing_expected_edge_ids": sorted(
            expected_edges - available_edges
        ),
        "missing_expected_relation_kinds": sorted(
            expected_kinds - available_kinds
        ),
        "uncited_expected_card_ids": sorted(
            expected_cited_cards - cited_cards
        ),
        "uncited_expected_edge_ids": sorted(
            expected_edges - cited_edges
        ),
        "hallucinated_card_ids": sorted(cited_cards - available_cards),
        "hallucinated_edge_ids": sorted(cited_edges - available_edges),
        "hallucinated_community_ids": sorted(
            cited_communities - available_communities
        ),
        "cited_card_ids_without_open_evidence": sorted(
            cited_cards - opened_cards
        ),
        "cited_edge_ids_without_open_evidence": sorted(
            cited_edges - opened_edges
        ),
        "forbidden_card_hits": sorted(
            forbidden_cards.intersection(cited_cards)
        ),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args)
    session_id = (
        args.session_id.strip()
        or f"kg-relation-graph-agent-scenarios-{uuid4().hex}"
    )
    config = AgentConfig(
        api_base_url=args.api_base_url.rstrip("/"),
        target=args.target,
        adapter_name=args.adapter,
        session_id=session_id,
        model=args.model,
        provider=args.provider.strip() or None,
        max_tool_rounds=args.max_tool_rounds,
        llm_timeout=args.llm_timeout,
        tool_timeout=args.tool_timeout,
    )
    results: list[dict[str, Any]] = []
    try:
        with langfuse_propagation_context(
            trace_name="kg.relation_graph_agent.scenario_validation",
            session_id=session_id,
            tags=[
                "kg",
                "agent-tool",
                "relation-graph",
                "production-scenario",
            ],
            metadata={
                "target": args.target,
                "adapter_name": args.adapter,
                "model": args.model,
                "case_count": len(cases),
                "api_base_url": config.api_base_url,
            },
        ):
            async with httpx.AsyncClient(
                base_url=config.api_base_url
            ) as client:
                for case in cases:
                    results.append(
                        await run_agent_case(
                            case,
                            config=config,
                            client=client,
                        )
                    )
    finally:
        langfuse_flush()
    return {
        "session_id": session_id,
        "target": args.target,
        "adapter_name": args.adapter,
        "model": args.model,
        "api_base_url": config.api_base_url,
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "pass_rate": round(
            sum(1 for item in results if item["passed"])
            / max(1, len(results)),
            4,
        ),
        "total_tool_calls": sum(
            len(item["tool_calls"])
            for item in results
        ),
        "usage": _sum_result_usage(results),
        "results": results,
    }


def _parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str]:
    if isinstance(raw, dict):
        return dict(raw), ""
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        return {}, f"工具参数不是合法 JSON: {exc}"
    if not isinstance(value, dict):
        return {}, "工具参数必须是 JSON object"
    return value, ""


def _parse_final_payload(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        )
    )
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(json.dumps(value, ensure_ascii=False))

    payload: dict[str, Any] | None = None
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if isinstance(payload, dict):
        required = {
            "answer",
            "card_ids",
            "edge_ids",
            "community_ids",
            "insufficient_evidence",
        }
        if (
            required.issubset(payload)
            and isinstance(payload.get("answer"), str)
            and all(
                isinstance(payload.get(key), list)
                for key in ("card_ids", "edge_ids", "community_ids")
            )
            and isinstance(payload.get("insufficient_evidence"), bool)
        ):
            return payload
    return {
        "answer": raw,
        "card_ids": sorted(
            _extract_cited_ids(
                raw,
                label="Card",
                prefix="kg_cognitive_card:",
            )
        ),
        "edge_ids": sorted(
            _extract_cited_ids(
                raw,
                label="Edge",
                prefix="kg_card_relation:",
            )
        ),
        "community_ids": sorted(
            _extract_cited_ids(
                raw,
                label="Community",
                prefix="kgc:",
            )
        ),
        "insufficient_evidence": "证据不足" in raw,
    }


def _is_json_object_text(text: str) -> bool:
    try:
        return isinstance(json.loads(str(text or "").strip()), dict)
    except json.JSONDecodeError:
        return False


def _extract_cited_ids(
    text: str,
    *,
    label: str,
    prefix: str,
) -> set[str]:
    found = set(
        re.findall(
            rf"{re.escape(prefix)}[A-Za-z0-9_.:-]+",
            text,
        )
    )
    for value in re.findall(
        rf"\[{re.escape(label)}:([A-Za-z0-9_.:-]+)\]",
        text,
    ):
        found.add(value if value.startswith(prefix) else f"{prefix}{value}")
    return found


def _collect_ids(outputs: list[Any], kind: str) -> set[str]:
    prefix_by_kind = {
        "card": "kg_cognitive_card:",
        "edge": "kg_card_relation:",
        "community": "kgc:",
    }
    prefix = prefix_by_kind[kind]
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if (
            isinstance(value, str)
            and value.startswith(prefix)
            and re.fullmatch(r"[A-Za-z0-9_.:-]+", value)
        ):
            found.add(value)

    for output in outputs:
        visit(output)
    return found


def _collect_relation_kinds(outputs: list[Any]) -> set[str]:
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            relation_kind = value.get("relation_kind")
            if isinstance(relation_kind, str) and relation_kind:
                found.add(relation_kind)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for output in outputs:
        visit(output)
    return found


def _opened_ids(
    transcript: list[dict[str, Any]],
    tool_name: str,
    kind: str,
) -> set[str]:
    return _collect_ids(
        [
            item["output"]
            for item in transcript
            if item["tool_name"] == tool_name
        ],
        kind,
    )


def _string_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {
        str(value).strip()
        for value in values
        if str(value).strip()
    }


def _merge_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)


def _sum_result_usage(results: list[dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for result in results:
        _merge_usage(total, result.get("usage") or {})
    return total


def _tool_output_summary(output: dict[str, Any]) -> dict[str, Any]:
    if output.get("error"):
        return {
            "error": output["error"],
            "detail": output.get("detail", ""),
        }
    return {
        "operation": output.get("operation"),
        "card_ids": sorted(_collect_ids([output], "card")),
        "edge_ids": sorted(_collect_ids([output], "edge")),
        "community_ids": sorted(
            _collect_ids([output], "community")
        ),
        "relation_kinds": sorted(
            _collect_relation_kinds([output])
        ),
        "truncated": bool(output.get("truncated")),
    }


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
