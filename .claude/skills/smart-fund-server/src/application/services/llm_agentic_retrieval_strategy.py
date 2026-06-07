"""LLM-backed retrieval controller strategy for agentic KG retrieval."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.application.services.retrieval_llm_trace import (
    trace_llm_request,
    trace_llm_response,
)
from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalStrategy,
    RetrievalControllerDecision,
    RetrievalSearchPlan,
    RetrievalWorkingSet,
)
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.retrieval_tools import RetrievalToolResult
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest


class LLMAgenticRetrievalStrategy(AgenticRetrievalStrategy):
    """Ask the LLM to choose the next high-level retrieval action."""

    def __init__(self, llm_service=None, llm_model: str | None = None) -> None:
        self._llm = llm_service
        self._llm_model = llm_model

    async def next_decision(
        self,
        *,
        query: str,
        working_set: RetrievalWorkingSet,
        observations: list[RetrievalToolResult],
        constraints: AgenticRetrievalConstraints,
    ) -> RetrievalControllerDecision:
        service = self._llm or get_llm_gateway_service()
        model = self._llm_model or resolve_kg_llm_model("kg_retrieval_controller")
        request = LLMProxyRequest(
            messages=_messages(query, working_set, observations, constraints),
            model=model,
            json_schema=_DECISION_SCHEMA,
            metadata={"task": "kg_retrieval_controller", "observations": len(observations)},
            use_cache=_retrieval_llm_cache_enabled(),
        )
        trace_llm_request("retrieval_controller", request)
        with profile_span(
            "llm.retrieval_controller.generate",
            model=model,
            observations=len(observations),
            tool_calls=working_set.tool_call_count,
        ):
            response = await service.generate(request)
        payload = response.structured_output if isinstance(response.structured_output, dict) else None
        if payload is None:
            payload = _parse_json_object(response.text)
        trace_llm_response("retrieval_controller", response, payload)
        return _decision_from_payload(payload or {}, query=query)


def _retrieval_llm_cache_enabled() -> bool:
    raw = os.getenv("KG_RETRIEVAL_LLM_USE_CACHE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


_SYSTEM_PROMPT = """你是知识图谱检索控制器。你只能选择 search、scoped_search、find、open、expand、summarize、stop。
你不能回答用户问题，不能生成事实，不能绕过候选裁判。每次只返回一个 JSON 决策。
你的价值不是把 query 改写成一句相似搜索词，而是产出可执行的 Search Plan：
- answer_targets: 本轮要补齐或回答的对象。
- query_rewrites: 2-3 条互补搜索表达，覆盖实体、代码、关系意图、证据类型；不要输出同义句堆砌。
- negative_boundaries: 应避免混入的主题漂移方向。
- expected_evidence: 希望找到的证据类型或关系形态。
- relation_intents: 本轮关注的关系意图。
如果已有证据足够，选择 stop；如果需要精读已知 evidence，选择 open/find，不要重复 search。
如果已有候选或证据方向正确，但全局搜索太宽、需要在局部范围内继续补齐主体/行业/资产影响，选择 scoped_search，并指定 target_candidate_ids 或 target_evidence_refs。
如果命中的 node/edge/evidence 暗示存在相关主体、行业、资产或影响链但证据不足，选择 expand 并指定 target_candidate_ids。
如果 pending_expand_candidates 非空，除非已有答案显然覆盖 stop_condition，否则不要直接 stop；优先 expand 这些候选的 target_candidate_ids，或 open 其 evidence_refs 精读证据。
时间约束：
- 不要凭模型记忆或训练截止时间生成任何年份、月份、日期。
- 只有用户原 query 明确包含年份/日期，query_rewrites 才能保留该时间。
- 对“最近/最新/近期”这类相对时间，不要追加数字年份；使用“最近/最新/近期”等原始语义即可。
- 如果确实需要当前日期，只能使用输入里的 runtime_time.current_date，不得猜测。"""


_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "next_tool": {
            "type": "string",
            "enum": ["search", "scoped_search", "find", "open", "expand", "summarize", "stop"],
        },
        "reason": {"type": "string"},
        "target_candidate_ids": {"type": "array", "items": {"type": "string"}},
        "target_evidence_refs": {"type": "array", "items": {"type": "string"}},
        "query_rewrites": {"type": "array", "items": {"type": "string"}},
        "search_plan": {
            "type": "object",
            "properties": {
                "answer_targets": {"type": "array", "items": {"type": "string"}},
                "negative_boundaries": {"type": "array", "items": {"type": "string"}},
                "expected_evidence": {"type": "array", "items": {"type": "string"}},
                "relation_intents": {"type": "array", "items": {"type": "string"}},
                "stop_condition": {"type": "string"},
            },
        },
        "expected_gain": {
            "type": "string",
            "enum": ["evidence_coverage", "disambiguation", "context_window", "compression", "none"],
        },
        "confidence": {"type": "number"},
        "stop_reason": {"type": "string"},
    },
    "required": ["next_tool", "reason", "confidence"],
}


def _prompt(
    query: str,
    working_set: RetrievalWorkingSet,
    observations: list[RetrievalToolResult],
    constraints: AgenticRetrievalConstraints,
) -> str:
    return json.dumps(
        {
            **_stable_prompt_context(query, working_set, constraints),
            **_dynamic_prompt_context(working_set, observations),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _messages(
    query: str,
    working_set: RetrievalWorkingSet,
    observations: list[RetrievalToolResult],
    constraints: AgenticRetrievalConstraints,
) -> list[dict[str, str]]:
    """Build a chat-shaped request with stable prefix for provider prompt cache.

    Do not move dynamic observations into the first user message. DeepSeek's
    prompt cache only helps when the message prefix is byte-for-byte stable.
    """

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                _stable_prompt_context(query, working_set, constraints),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                _dynamic_prompt_context(working_set, observations),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def _stable_prompt_context(
    query: str,
    working_set: RetrievalWorkingSet,
    constraints: AgenticRetrievalConstraints,
) -> dict[str, Any]:
    return {
        "constraints": constraints.model_dump(),
        "available_tools": {
            "search": "重新组织查询并多通道召回。",
            "scoped_search": "在已知 candidate/evidence 的局部范围内继续检索，必须指定 target_candidate_ids 或 target_evidence_refs。",
            "find": "在已知 evidence 内定位关键词或语义线索，必须指定 target_evidence_refs。",
            "open": "打开已知 evidence 或候选的父证据/窗口，必须指定 target_evidence_refs 或 target_candidate_ids。",
            "expand": "对已知 node/edge/evidence 候选执行局部图谱关系展开，必须指定 target_candidate_ids。",
            "summarize": "压缩已验证 trace 和 evidence，不生成新事实。",
            "stop": "证据足够或继续无收益时停止。",
        },
        "decision_contract": {
            "search": {
                "next_tool": "search",
                "query_rewrites": ["query expression 1", "query expression 2"],
                "search_plan": {
                    "answer_targets": ["objects to answer"],
                    "negative_boundaries": ["topics to avoid"],
                    "expected_evidence": ["evidence or relation type expected"],
                    "relation_intents": ["impact/beneficiary/cause/risk/etc"],
                    "stop_condition": "what evidence is enough",
                },
                "expected_gain": "evidence_coverage",
            },
            "find": {
                "next_tool": "find",
                "target_evidence_refs": ["kg_ev:..."],
                "query_rewrites": ["term"],
                "expected_gain": "disambiguation",
            },
            "scoped_search": {
                "next_tool": "scoped_search",
                "target_candidate_ids": ["kg:..."],
                "target_evidence_refs": ["kg_ev:..."],
                "query_rewrites": ["scoped query expression"],
                "expected_gain": "evidence_coverage",
            },
            "open": {
                "next_tool": "open",
                "target_evidence_refs": ["kg_ev:..."],
                "expected_gain": "context_window",
            },
            "expand": {
                "next_tool": "expand",
                "target_candidate_ids": ["kg:..."],
                "expected_gain": "evidence_coverage",
            },
            "stop": {"next_tool": "stop", "stop_reason": "evidence_sufficient"},
        },
        "runtime_time": _runtime_time_context(),
        "query": query,
        "query_anchor": working_set.query_anchor.model_dump(mode="json"),
        "cache_policy": {
            "message_shape": "system + stable_task_context + dynamic_observation_context",
            "stable_prefix_rule": "历史 system 和 stable_task_context 不允许在后续轮次改写；只能追加或替换尾部 dynamic_observation_context。",
        },
    }


def _dynamic_prompt_context(
    working_set: RetrievalWorkingSet,
    observations: list[RetrievalToolResult],
) -> dict[str, Any]:
    return {
            "working_set": {
                "evidence_refs": working_set.evidence_refs,
                "accepted": [_judgement_payload(item) for item in working_set.accepted_candidates[-8:]],
                "background": [_judgement_payload(item) for item in working_set.background_candidates[-6:]],
                "dropped": [_judgement_payload(item) for item in working_set.dropped_candidates[-6:]],
                "pending_expand_candidates": _pending_expand_payload(working_set, observations),
                "opened_windows": working_set.opened_windows[-8:],
                "missing_anchor_items": working_set.missing_anchor_items,
                "tool_call_count": working_set.tool_call_count,
                "no_gain_rounds": working_set.no_gain_rounds,
            },
            "observations": [_observation_payload(item) for item in observations[-4:]],
    }


def _judgement_payload(item) -> dict[str, Any]:
    return {
        "candidate_id": item.candidate_id,
        "role": item.role,
        "decision": item.decision,
        "score": round(float(item.relevance_score or 0.0), 3),
        "expand": item.can_expand_graph,
        "reason": item.reason_code or item.reason,
    }


def _observation_payload(result: RetrievalToolResult) -> dict[str, Any]:
    return {
        "tool": result.tool,
        "hit_count": len(result.hits),
        "summary": result.summary,
        "hits": [
            {
                "hit_id": hit.hit_id,
                "type": hit.hit_type,
                "title": hit.title,
                "score": hit.score,
                "node_refs": hit.node_refs[:3],
                "edge_refs": hit.edge_refs[:3],
                "evidence_refs": hit.evidence_refs[:3],
                "snippet": hit.snippet[:220],
            }
            for hit in result.hits[:5]
        ],
    }


def _pending_expand_payload(
    working_set: RetrievalWorkingSet,
    observations: list[RetrievalToolResult],
) -> list[dict[str, Any]]:
    hits_by_id = {
        hit.hit_id: hit
        for result in observations
        for hit in result.hits
    }
    opened = set(working_set.opened_windows)
    items: list[dict[str, Any]] = []
    for judgement in working_set.accepted_candidates[-10:]:
        if judgement.decision != "keep" or not judgement.can_expand_graph:
            continue
        if _expand_marker(judgement.candidate_id) in opened:
            continue
        hit = hits_by_id.get(judgement.candidate_id)
        if hit is not None and hit.hit_type == "evidence":
            continue
        evidence_refs = hit.evidence_refs if hit is not None else []
        items.append(
            {
                "candidate_id": judgement.candidate_id,
                "title": hit.title if hit is not None else judgement.candidate_id,
                "type": hit.hit_type if hit is not None else "",
                "role": judgement.role,
                "score": judgement.relevance_score,
                "reason": judgement.reason_code or judgement.reason,
                "evidence_refs": evidence_refs[:3],
                "open_hint": {
                    "next_tool": "open",
                    "target_candidate_ids": [judgement.candidate_id],
                    "target_evidence_refs": evidence_refs[:3],
                },
            }
        )
    return items[:5]


def _expand_marker(candidate_id: str) -> str:
    return f"candidate_expand:{candidate_id}"


def _decision_from_payload(payload: dict[str, Any], *, query: str = "") -> RetrievalControllerDecision:
    next_tool = str(payload.get("next_tool") or "").strip()
    if next_tool not in {"search", "scoped_search", "find", "open", "expand", "summarize", "stop"}:
        next_tool = "stop"
    expected_gain = str(payload.get("expected_gain") or "none")
    if expected_gain not in {"evidence_coverage", "disambiguation", "context_window", "compression", "none"}:
        expected_gain = "none"
    return RetrievalControllerDecision(
        next_tool=next_tool,
        reason=str(payload.get("reason") or "llm_controller_decision"),
        target_candidate_ids=_string_list(payload.get("target_candidate_ids")),
        target_evidence_refs=_string_list(payload.get("target_evidence_refs")),
        query_rewrites=_sanitize_query_rewrites(_string_list(payload.get("query_rewrites")), query=query),
        search_plan=_search_plan_from_payload(payload.get("search_plan")),
        expected_gain=expected_gain,
        confidence=float(payload.get("confidence") or 0.5),
        stop_reason=str(payload.get("stop_reason") or "") or None,
    )


def _search_plan_from_payload(value: Any) -> RetrievalSearchPlan:
    if not isinstance(value, dict):
        return RetrievalSearchPlan()
    return RetrievalSearchPlan(
        answer_targets=_string_list(value.get("answer_targets")),
        negative_boundaries=_string_list(value.get("negative_boundaries")),
        expected_evidence=_string_list(value.get("expected_evidence")),
        relation_intents=_string_list(value.get("relation_intents")),
        stop_condition=str(value.get("stop_condition") or "").strip(),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _runtime_time_context() -> dict[str, Any]:
    current = date.today()
    return {
        "current_date": current.isoformat(),
        "current_year": current.year,
        "time_rewrite_policy": "Do not invent numeric dates. Keep only explicit user dates; relative recency should remain relative.",
    }


_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:年)?(?!\d)")


def _sanitize_query_rewrites(rewrites: list[str], *, query: str) -> list[str]:
    explicit_years = set(_YEAR_RE.findall(query or ""))
    sanitized: list[str] = []
    for item in rewrites:
        text = item
        for year in set(_YEAR_RE.findall(text)):
            if year in explicit_years:
                continue
            text = re.sub(rf"(?<!\d){re.escape(year)}年?(?!\d)", " ", text)
        text = " ".join(text.split())
        if text:
            sanitized.append(text)
    return _dedupe_strings(sanitized)


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
