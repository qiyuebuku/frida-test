"""Debug tracing helpers for KG retrieval LLM calls.

The trace is intentionally written as compact plain text instead of JSONL.
It is meant for fast human/AI inspection during replay debugging, not as a
machine ingestion format.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from src.infrastructure.llm_proxy.types import LLMProxyRequest


def retrieval_llm_trace_enabled() -> bool:
    return os.getenv("KG_RETRIEVAL_LLM_TRACE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def trace_llm_request(stage: str, request: LLMProxyRequest) -> None:
    if not retrieval_llm_trace_enabled():
        return
    _write_event(
        {
            "type": "request",
            "stage": stage,
            "model": request.model,
            "metadata": request.metadata,
            "use_cache": request.use_cache,
            "has_json_schema": request.json_schema is not None,
            "prompt": _compact_prompt(request.prompt_text()),
        }
    )


def trace_llm_response(stage: str, response: Any, parsed_payload: dict[str, Any] | None) -> None:
    if not retrieval_llm_trace_enabled():
        return
    _write_event(
        {
            "type": "response",
            "stage": stage,
            "duration_ms": getattr(response, "duration_ms", None),
            "cache_hit": getattr(response, "cache_hit", None),
            "usage": getattr(response, "usage", None),
            "parsed_payload": parsed_payload,
            "raw_text": _clip(str(getattr(response, "text", "") or "")),
        }
    )


def _write_event(event: dict[str, Any]) -> None:
    path = _trace_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        **event,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(_format_event(_compact_event(event)))
        file.write("\n")


def _trace_file() -> Path:
    raw = os.getenv("KG_RETRIEVAL_LLM_TRACE_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("data/logs/kg_retrieval_llm_trace.log")


def _format_event(event: dict[str, Any]) -> str:
    kind = str(event.get("type") or "")
    stage = str(event.get("stage") or "")
    lines = [f"===== {event.get('ts')} {stage} {kind} ====="]
    if kind == "request":
        lines.extend(_format_request(event))
    elif kind == "response":
        lines.extend(_format_response(event))
    else:
        lines.append(_yamlish(event))
    return "\n".join(lines) + "\n"


def _format_request(event: dict[str, Any]) -> list[str]:
    prompt = event.get("prompt")
    metadata = event.get("metadata")
    lines = [
        f"model: {event.get('model')}",
        f"metadata: {_inline(metadata)}",
        f"use_cache: {event.get('use_cache')}  has_json_schema: {event.get('has_json_schema')}",
    ]
    if isinstance(prompt, dict):
        for key in ("query", "q", "query_anchor", "anchor", "constraints", "working_set"):
            if key in prompt:
                lines.append(f"{key}:")
                lines.append(_indent(_yamlish(prompt[key]), "  "))
        if "observations" in prompt:
            lines.append(f"observations: total={prompt.get('observations_total', len(prompt.get('observations') or []))}")
            lines.extend(_format_items(prompt.get("observations") or [], item_name="observation"))
        evidence_context = prompt.get("evidence_context")
        if isinstance(evidence_context, list):
            total = prompt.get("evidence_context_total", len(evidence_context))
            lines.append(f"evidence_context: total={total} showing={len(evidence_context)}")
            lines.extend(_format_items(evidence_context, item_name="evidence"))
        candidates = prompt.get("candidates") or prompt.get("c")
        if isinstance(candidates, list):
            total = prompt.get("candidates_total", len(candidates))
            lines.append(f"candidates: total={total} showing={len(candidates)}")
            lines.extend(_format_items(candidates, item_name="candidate"))
        for key in ("decision_rules", "rules", "available_tools", "decision_contract"):
            if key in prompt:
                lines.append(f"{key}: {_inline(prompt[key])}")
    else:
        lines.append(f"prompt: {_inline(prompt)}")
    return lines


def _format_response(event: dict[str, Any]) -> list[str]:
    lines = [
        f"duration_ms: {event.get('duration_ms')}  cache_hit: {event.get('cache_hit')}",
        f"usage: {_inline(event.get('usage'))}",
    ]
    parsed = event.get("parsed_payload")
    if isinstance(parsed, dict):
        if isinstance(parsed.get("judgements"), list):
            lines.append(f"judgements: total={parsed.get('judgements_total', len(parsed['judgements']))}")
            lines.extend(_format_items(parsed["judgements"], item_name="judgement"))
        else:
            lines.append("parsed_payload:")
            lines.append(_indent(_yamlish(parsed), "  "))
    raw_text = str(event.get("raw_text") or "").strip()
    if raw_text:
        lines.append("raw_text:")
        lines.append(_indent(raw_text, "  "))
    return lines


def _format_items(items: list[Any], *, item_name: str) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            title = (
                item.get("title")
                or item.get("candidate_key")
                or item.get("key")
                or item.get("candidate_id")
                or item.get("tool")
                or item.get("decision")
                or ""
            )
            lines.append(f"- {item_name} {index}: {title}")
            for key, value in item.items():
                if key == "title":
                    continue
                if key == "hits" and isinstance(value, list):
                    lines.append(f"  hits: {len(value)}")
                    for hit_index, hit in enumerate(value[: _trace_max_items()], start=1):
                        lines.append(f"    - hit {hit_index}: {_inline(hit)}")
                    continue
                lines.append(f"  {key}: {_inline(value)}")
        else:
            lines.append(f"- {item_name} {index}: {_inline(item)}")
    return lines


def _inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _yamlish(value: Any) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{key}:")
                lines.append(_indent(_yamlish(item), "  "))
            else:
                lines.append(f"{key}: {_inline(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append("-")
                lines.append(_indent(_yamlish(item), "  "))
            else:
                lines.append(f"- {_inline(item)}")
        return "\n".join(lines)
    return _inline(value)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.splitlines())


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    parsed = payload.get("parsed_payload")
    if isinstance(parsed, dict) and isinstance(parsed.get("judgements"), list):
        payload["parsed_payload"] = {
            **parsed,
            "judgements": [
                {
                    "candidate_id": item.get("candidate_id"),
                    "candidate_key": item.get("candidate_key"),
                    "title": item.get("title"),
                    "role": item.get("role"),
                    "decision": item.get("decision"),
                    "score": item.get("score", item.get("relevance_score")),
                    "expand": item.get("expand"),
                    "code": item.get("code", item.get("reason_code")),
                }
                for item in parsed["judgements"][: _trace_max_items()]
                if isinstance(item, dict)
            ],
            "judgements_total": len(parsed["judgements"]),
        }
    return payload


def _compact_prompt(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"text": _clip(text)}
    if not isinstance(payload, dict):
        return {"text": _clip(text)}
    result: dict[str, Any] = {}
    for key in (
        "query",
        "q",
        "constraints",
        "available_tools",
        "decision_contract",
        "query_anchor",
        "anchor",
        "decision_rules",
        "rules",
    ):
        if key in payload:
            result[key] = payload[key]
    working_set = payload.get("working_set")
    if isinstance(working_set, dict):
        result["working_set"] = _compact_working_set(working_set)
    observations = payload.get("observations")
    if isinstance(observations, list):
        result["observations"] = [
            _compact_observation(item)
            for item in observations[: _trace_max_items()]
            if isinstance(item, dict)
        ]
        result["observations_total"] = len(observations)
    candidates = payload.get("candidates")
    if candidates is None:
        candidates = payload.get("c")
    if isinstance(candidates, list):
        result["candidates"] = [
            _compact_candidate(item)
            for item in candidates[: _trace_max_items()]
            if isinstance(item, dict)
        ]
        result["candidates_total"] = len(candidates)
    evidence_context = payload.get("evidence_context")
    if isinstance(evidence_context, list):
        result["evidence_context"] = [
            _compact_evidence_context(item)
            for item in evidence_context[: _trace_max_items()]
            if isinstance(item, dict)
        ]
        result["evidence_context_total"] = len(evidence_context)
    return result


def _compact_observation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": item.get("tool"),
        "hit_count": item.get("hit_count"),
        "summary": item.get("summary"),
        "hits": [
            _compact_candidate(hit)
            for hit in (item.get("hits") or [])[: _trace_max_items()]
            if isinstance(hit, dict)
        ],
    }


def _compact_working_set(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("evidence_refs", "opened_windows", "missing_anchor_items", "tool_call_count", "no_gain_rounds"):
        if key in item:
            value = item[key]
            if isinstance(value, list):
                result[key] = value[: _trace_max_items()]
                result[f"{key}_total"] = len(value)
            else:
                result[key] = value
    for key in ("accepted", "background", "dropped"):
        value = item.get(key)
        if isinstance(value, list):
            result[key] = [
                _compact_candidate(candidate)
                for candidate in value[: _trace_max_items()]
                if isinstance(candidate, dict)
            ]
            result[f"{key}_total"] = len(value)
    return result


def _compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        "candidate_key": item.get("candidate_key") or item.get("key"),
        "candidate_id": item.get("candidate_id") or item.get("hit_id") or item.get("id"),
        "type": item.get("type") or item.get("ty") or item.get("kind"),
        "source": item.get("source") or item.get("src") or item.get("channel"),
        "title": item.get("title") or item.get("t"),
        "score": item.get("score") or item.get("s"),
        "evidence_keys": item.get("evidence_keys") or item.get("evidence_refs") or item.get("ev"),
        "meaning": _clip(
            str(item.get("meaning") or item.get("snippet") or item.get("ctx") or ""),
            limit=_snippet_max_chars(),
        ),
        "support_relations": item.get("support_relations") or item.get("edges"),
        "evidence": item.get("evidence") or item.get("ex"),
        "why": item.get("why_recalled") or item.get("why"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_evidence_context(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item.get("key"),
        "source": item.get("source"),
        "excerpt": _clip(str(item.get("excerpt") or ""), limit=_snippet_max_chars()),
    }


def _clip(value: str, *, limit: int | None = None) -> str:
    limit = _trace_max_chars() if limit is None else limit
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def _trace_max_chars() -> int:
    raw = os.getenv("KG_RETRIEVAL_LLM_TRACE_MAX_CHARS", "5000").strip()
    try:
        return int(raw)
    except ValueError:
        return 5000


def _trace_max_items() -> int:
    raw = os.getenv("KG_RETRIEVAL_LLM_TRACE_MAX_ITEMS", "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


def _snippet_max_chars() -> int:
    raw = os.getenv("KG_RETRIEVAL_LLM_TRACE_SNIPPET_CHARS", "500").strip()
    try:
        return max(80, int(raw))
    except ValueError:
        return 500
