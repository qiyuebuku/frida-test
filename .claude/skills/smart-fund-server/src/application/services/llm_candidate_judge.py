"""LLM-backed semantic candidate judge for KG retrieval."""

from __future__ import annotations

import json
import os
from typing import Any

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.application.services.retrieval_llm_trace import (
    trace_llm_request,
    trace_llm_response,
)
from src.domain.knowledge.retrieval_anchor import QueryAnchor
from src.domain.knowledge.retrieval_judge import CandidateJudgement
from src.domain.knowledge.retrieval_profile import profile_span
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest


class LLMCandidateJudge:
    """Judge whether retrieved candidates semantically answer the query.

    This is intentionally not a string guard. Exact ids, codes, and titles are
    passed as context, but the model must decide whether the candidate explains
    the query intent before it can enter Query Context.
    """

    def __init__(self, llm_service=None, llm_model: str | None = None) -> None:
        self._llm = llm_service
        self._llm_model = llm_model

    async def judge(
        self,
        *,
        query: str,
        anchor: QueryAnchor,
        hits: list[Any],
    ) -> list[CandidateJudgement]:
        if not hits:
            return []
        service = self._llm or get_llm_gateway_service()
        model = self._llm_model or resolve_kg_llm_model("kg_candidate_judge")
        request = LLMProxyRequest(
            system_prompt=_SYSTEM_PROMPT,
            prompt=_prompt(query, anchor, hits),
            model=model,
            json_schema=_JUDGE_SCHEMA,
            metadata={"task": "kg_candidate_judge", "candidate_count": len(hits)},
            use_cache=_retrieval_llm_cache_enabled(),
        )
        trace_llm_request("candidate_judge", request)
        with profile_span("llm.candidate_judge.generate", model=model, candidates=len(hits)):
            response = await service.generate(request)
        payload = response.structured_output if isinstance(response.structured_output, dict) else None
        if payload is None:
            payload = _parse_json_object(response.text)
        trace_llm_response("candidate_judge", response, _payload_for_trace(payload or {}, hits))
        return _judgements_from_payload(payload or {}, hits)


def _retrieval_llm_cache_enabled() -> bool:
    raw = os.getenv("KG_RETRIEVAL_LLM_USE_CACHE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


_SYSTEM_PROMPT = """你是知识图谱检索候选裁判。
你只判断候选在 Query Context 中的角色，不生成新事实，不补全知识图谱。
字符串重合、向量相似、同领域主题都不足以判定相关；必须根据候选上下文和证据判断是否覆盖 query 意图。
角色定义：
- answer: 可以直接回答 query 的候选。
- support: 不能单独回答，但能作为答案证据、关系路径或核心 anchor。
- background: 只适合作为背景，不能触发图扩展。
- drop: 主题漂移、证据不足或仅泛泛相似。
如果 query 在问“哪些事件/哪些因素/受哪些影响”，具备明确含义的事件、信号、新闻、政策、行业、资产或概念实体候选应优先标为 answer；纯实体 anchor 才标为 support。
kg_edge/edge 候选只是关系证据或路径，默认只能是 support/background/drop，不能作为 answer；它连接出的实体或事件才可以作为 answer。
只能返回紧凑 JSON，不要输出长解释。"""


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_key": {"type": "string"},
                    "role": {"type": "string", "enum": ["answer", "support", "background", "drop"]},
                    "score": {"type": "number"},
                    "expand": {"type": "boolean"},
                    "code": {
                        "type": "string",
                        "enum": [
                            "direct_answer",
                            "evidence_support",
                            "anchor_support",
                            "background_only",
                            "insufficient_evidence",
                            "topic_drift",
                            "generic_similarity",
                        ],
                    },
                },
                "required": ["candidate_key", "role", "score", "expand", "code"],
            },
        }
    },
    "required": ["judgements"],
}


def _prompt(query: str, anchor: QueryAnchor, hits: list[Any]) -> str:
    evidence_context, evidence_key_by_id = _shared_evidence_context(hits)
    return json.dumps(
        {
            "rules": [
                "answer/support may enter context; only answer/support can expand graph.",
                "background may enter context but must not expand.",
                "drop must not enter context.",
                "Return candidate_key such as C1/C2; do not return database ids.",
                "score must be a semantic relevance number from 0 to 1; never copy retrieval scores.",
                "Prefer support over drop for exact query anchor nodes with evidence.",
                "Use drop for topic drift even when evidence/vector score is high.",
                "Edge candidates are relation evidence; use support for useful edges, never answer.",
                "Evidence candidates may be answer only when the evidence text directly answers the query.",
            ],
            "plan": _candidate_search_plan(hits),
            "q": query,
            "anchor": _compact_anchor(anchor),
            "evidence_context": evidence_context,
            "c": [
                _candidate_payload(index, hit, evidence_key_by_id=evidence_key_by_id)
                for index, hit in enumerate(hits, start=1)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact_anchor(anchor: QueryAnchor) -> dict[str, Any]:
    return {
        "entities": [item.text for item in anchor.core_entities],
        "topics": [item.text for item in anchor.core_topics],
        "intent": list(anchor.relation_intents),
        "time": [item.text for item in anchor.time_hints],
        "source": [item.text for item in anchor.source_hints],
        "negative": list(anchor.negative_boundaries),
        "inferred": [item.text for item in anchor.inferred_hints],
        "must": [item.value for item in anchor.guard_constraints if item.must_preserve],
    }


def _candidate_search_plan(hits: list[Any]) -> dict[str, Any]:
    for hit in hits:
        plan = getattr(hit, "search_plan", None)
        if isinstance(plan, dict) and plan:
            return {
                "answer_targets": list(plan.get("answer_targets") or []),
                "negative_boundaries": list(plan.get("negative_boundaries") or []),
                "expected_evidence": list(plan.get("expected_evidence") or []),
                "relation_intents": list(plan.get("relation_intents") or []),
                "stop_condition": str(plan.get("stop_condition") or ""),
            }
    return {}


def _candidate_payload(index: int, hit: Any, *, evidence_key_by_id: dict[str, str] | None = None) -> dict[str, Any]:
    package = hit if hasattr(hit, "candidate") else None
    candidate = getattr(hit, "candidate", hit)
    edges = getattr(package, "supporting_edges", []) if package is not None else []
    evidence = getattr(package, "supporting_evidence_excerpt", []) if package is not None else []
    evidence_key_by_id = evidence_key_by_id or {}
    evidence_keys = _candidate_evidence_keys(candidate, evidence, evidence_key_by_id)
    payload = {
        "key": f"C{index}",
        "title": str(getattr(candidate, "title", "")),
        "kind": str(getattr(candidate, "hit_type", "")),
    }
    meaning = _candidate_meaning(candidate)
    if meaning and meaning != payload["title"]:
        payload["meaning"] = meaning
    support_relations = [_edge_payload(item) for item in edges[:2]]
    if support_relations:
        payload["support_relations"] = support_relations
    if evidence_keys:
        payload["evidence_keys"] = evidence_keys[:3]
    return payload


def _candidate_meaning(candidate: Any) -> str:
    title = str(getattr(candidate, "title", "") or "").strip()
    kind = str(getattr(candidate, "hit_type", "") or "")
    raw_snippet = str(getattr(candidate, "snippet", "") or "")
    snippet = _readable_snippet(raw_snippet)
    if kind == "evidence" and list(getattr(candidate, "evidence_refs", []) or []):
        return title or snippet[:120]
    if kind in {"node", "wiki", "edge"} and _looks_like_repeated_evidence(snippet, title):
        return title or snippet[:120]
    limit = 220 if kind == "evidence" else 160
    if title and snippet and title not in snippet:
        return f"{title}。{snippet}"[:limit]
    return (snippet or title)[:limit]


def _looks_like_repeated_evidence(snippet: str, title: str) -> bool:
    text = str(snippet or "").strip()
    if not text:
        return False
    if len(text) >= 160:
        return True
    title = str(title or "").strip()
    if title and "\n" in text and title not in text[:80]:
        return True
    return any(marker in text for marker in ("数据显示", "Wind资讯", "据悉", "报道称"))


def _shared_evidence_context(hits: list[Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    contexts: list[dict[str, Any]] = []
    key_by_id: dict[str, str] = {}
    for hit in hits:
        package = hit if hasattr(hit, "candidate") else None
        candidate = getattr(hit, "candidate", hit)
        evidence_items = list(getattr(package, "supporting_evidence_excerpt", []) or [])
        if not evidence_items:
            evidence_items = [
                _InlineEvidence(evidence_id=evidence_id, excerpt=str(getattr(candidate, "snippet", "") or ""))
                for evidence_id in list(getattr(candidate, "evidence_refs", []) or [])[:2]
            ]
        for item in evidence_items:
            evidence_id = str(getattr(item, "evidence_id", "") or "").strip()
            if not evidence_id or evidence_id in key_by_id:
                continue
            key = f"E{len(contexts) + 1}"
            key_by_id[evidence_id] = key
            contexts.append(
                {
                    "key": key,
                    "source": str(getattr(item, "source_id", "") or evidence_id),
                    "excerpt": str(getattr(item, "excerpt", "") or "")[:260],
                }
            )
    return contexts, key_by_id


class _InlineEvidence:
    def __init__(self, *, evidence_id: str, excerpt: str) -> None:
        self.evidence_id = evidence_id
        self.source_id = evidence_id
        self.excerpt = excerpt


def _candidate_evidence_keys(
    candidate: Any,
    evidence_items: list[Any],
    evidence_key_by_id: dict[str, str],
) -> list[str]:
    refs: list[str] = []
    for item in evidence_items:
        evidence_id = str(getattr(item, "evidence_id", "") or "").strip()
        key = evidence_key_by_id.get(evidence_id)
        if key:
            refs.append(key)
    for evidence_id in list(getattr(candidate, "evidence_refs", []) or []):
        key = evidence_key_by_id.get(str(evidence_id))
        if key:
            refs.append(key)
    return _ordered_unique(refs)


def _readable_snippet(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:360]
    if not isinstance(payload, dict):
        return text[:360]
    parts: list[str] = []
    name = payload.get("name")
    node_type = payload.get("type")
    if name:
        parts.append(f"名称：{name}")
    if node_type:
        parts.append(f"类型：{node_type}")
    properties = payload.get("properties")
    if isinstance(properties, dict):
        for key in (
            "summary",
            "event_type",
            "signal_type",
            "source_name",
            "published_at",
            "code",
            "exchange",
            "taxonomy",
        ):
            value = properties.get(key)
            if value not in (None, "", []):
                parts.append(f"{key}：{value}")
    return "；".join(parts)[:360] if parts else text[:360]


def _edge_payload(item: Any) -> str:
    rel = getattr(item, "relation_type", "")
    src = getattr(item, "source_name", "") or getattr(item, "source_node_id", "")
    dst = getattr(item, "target_name", "") or getattr(item, "target_node_id", "")
    if src and dst:
        return f"{src} --{rel}--> {dst}"
    return str(rel)


def _evidence_payload(item: Any) -> dict[str, Any]:
    return {
        "source": getattr(item, "source_id", ""),
        "excerpt": str(getattr(item, "excerpt", "") or "")[:220],
    }


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _judgements_from_payload(payload: dict[str, Any], hits: list[Any]) -> list[CandidateJudgement]:
    by_id = {str(getattr(hit, "hit_id", "")): hit for hit in hits}
    by_key = {f"C{index}": str(getattr(hit, "hit_id", "")) for index, hit in enumerate(hits, start=1)}
    raw_items = payload.get("judgements") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return [_drop_judgement(hit, "llm_judge_payload_missing") for hit in hits]
    judgements: list[CandidateJudgement] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_ref = str(item.get("candidate_key") or item.get("candidate_id") or "").strip()
        candidate_id = by_key.get(candidate_ref, candidate_ref)
        if candidate_id not in by_id:
            continue
        seen.add(candidate_id)
        role = str(item.get("role") or "").strip()
        decision = str(item.get("decision") or "").strip()
        if not decision:
            decision = _decision_from_role(role)
        if decision not in {"keep", "weak_keep", "drop"}:
            decision = "drop"
        if role not in {"answer", "support", "background", "drop"}:
            role = _role_from_decision(decision)
        relevance_score = _clamp_float(item.get("score", item.get("relevance_score")), default=0.0)
        hit = by_id[candidate_id]
        role, decision, reason_code = _normalize_role_for_candidate(
            hit,
            role=role,
            decision=decision,
            reason_code=str(item.get("code") or item.get("reason_code") or "llm_semantic_judge").strip(),
        )
        can_expand_graph = _bool(item.get("expand", item.get("can_expand_graph"))) and decision == "keep"
        judgements.append(
            CandidateJudgement(
                candidate_id=candidate_id,
                decision=decision,
                role=role,
                relevance_score=relevance_score,
                can_expand_graph=can_expand_graph,
                anchor_coverage={"overall": relevance_score},
                topic_drift=reason_code == "topic_drift" or decision == "drop",
                reason=reason_code,
                reason_code=reason_code,
                judge_source="llm",
            )
        )
    for candidate_id, hit in by_id.items():
        if candidate_id not in seen:
            judgements.append(_drop_judgement(hit, "llm_judge_missing_candidate"))
    return judgements


def _payload_for_trace(payload: dict[str, Any], hits: list[Any]) -> dict[str, Any]:
    raw_items = payload.get("judgements") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return payload
    by_key = {f"C{index}": getattr(hit, "candidate", hit) for index, hit in enumerate(hits, start=1)}
    by_id = {str(getattr(getattr(hit, "candidate", hit), "hit_id", "")): getattr(hit, "candidate", hit) for hit in hits}
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_key = str(item.get("candidate_key") or item.get("candidate_id") or "").strip()
        candidate = by_key.get(candidate_key) or by_id.get(candidate_key)
        role = str(item.get("role") or "").strip()
        decision = str(item.get("decision") or "").strip() or _decision_from_role(role)
        reason_code = str(item.get("code", item.get("reason_code")) or "")
        if candidate is not None:
            role, decision, reason_code = _normalize_role_for_candidate(
                candidate,
                role=role,
                decision=decision,
                reason_code=reason_code,
            )
        items.append(
            {
                "candidate_key": candidate_key if candidate_key.startswith("C") else "",
                "candidate_id": str(getattr(candidate, "hit_id", "")) if candidate is not None else candidate_key,
                "title": str(getattr(candidate, "title", "")) if candidate is not None else candidate_key,
                "role": role,
                "decision": decision if decision in {"keep", "weak_keep", "drop"} else "drop",
                "score": _clamp_float(item.get("score", item.get("relevance_score")), default=0.0),
                "expand": item.get("expand"),
                "code": reason_code,
            }
        )
    return {"judgements": items}


def _drop_judgement(hit: Any, reason: str) -> CandidateJudgement:
    return CandidateJudgement(
        candidate_id=str(getattr(hit, "hit_id", "")),
        decision="drop",
        role="drop",
        relevance_score=0.0,
        can_expand_graph=False,
        anchor_coverage={"overall": 0.0},
        topic_drift=True,
        reason=reason,
        reason_code=reason,
        judge_source="fallback",
    )


def _decision_from_role(role: str) -> str:
    if role in {"answer", "support"}:
        return "keep"
    if role == "background":
        return "weak_keep"
    return "drop"


def _normalize_role_for_candidate(
    candidate: Any,
    *,
    role: str,
    decision: str,
    reason_code: str,
) -> tuple[str, str, str]:
    candidate_id = str(getattr(candidate, "hit_id", ""))
    hit_type = str(getattr(candidate, "hit_type", ""))
    if role == "answer" and (candidate_id.startswith("kg_edge:") or hit_type == "edge"):
        return "support", "keep" if decision != "drop" else "drop", (
            "evidence_support" if reason_code == "direct_answer" else reason_code
        )
    return role, decision, reason_code


def _role_from_decision(decision: str) -> str:
    if decision == "keep":
        return "answer"
    if decision == "weak_keep":
        return "background"
    return "drop"


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


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
