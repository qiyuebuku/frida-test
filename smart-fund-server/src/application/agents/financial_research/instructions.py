"""Research Agent instruction loading and bounded run-input construction."""

from __future__ import annotations

import json
from datetime import datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

from src.application.agents.financial_research.schemas import ResearchContextPack


_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def load_financial_research_instructions() -> str:
    prompt = files("src.application.agents.financial_research").joinpath(
        "prompts/financial_research.md"
    )
    return prompt.read_text(encoding="utf-8").strip()


def build_run_input(
    *,
    context_pack: ResearchContextPack,
) -> str:
    compact_pack = _compact_context_pack(context_pack)
    payload = {
        "research_task": {
            "question": context_pack.research_question,
            "trigger_scene": context_pack.trigger.trigger_slot.value,
            "data_time_policy": (
                "historical_replay_boundary"
                if context_pack.trigger.run_mode.value == "replay"
                else "latest_available_at_each_tool_call"
            ),
        },
        "initial_context": compact_pack,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact_context_pack(context_pack: ResearchContextPack) -> dict:
    """Project trusted state into a smaller model-facing representation."""

    payload = context_pack.model_dump(mode="json")
    payload.pop("trigger", None)
    payload.pop("research_question", None)
    market_state = payload["market_state"]
    market_state["frame_id"] = "frame_ref:F1"
    market_state.pop("cutoff_at", None)
    market_state["drilldown_handles"] = [
        f"dimension:{str(handle).split(':', 2)[1]}"
        if str(handle).startswith("market-dimension:")
        else handle
        for handle in market_state.get("drilldown_handles", [])
    ]
    for dimension in market_state.get("dimensions", []):
        name = str(dimension.get("dimension") or "")
        if name:
            dimension["evidence_handles"] = [f"dimension:{name}"]
        if dimension.get("state") == "unknown":
            dimension.pop("state", None)
    return _compact_timestamps(payload)


def _compact_timestamps(value):
    if isinstance(value, str):
        if "T" not in value:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return _compact_datetime(parsed)
    if isinstance(value, list):
        return [_compact_timestamps(item) for item in value]
    if isinstance(value, dict):
        return {key: _compact_timestamps(item) for key, item in value.items()}
    return value


def _compact_datetime(value: datetime) -> str:
    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone(_CHINA_TIMEZONE)
    return value.strftime("%Y-%m-%d %H:%M")
