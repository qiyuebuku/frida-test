"""Plain text trace hooks for retrieval decisions.

This module is domain-local so agentic retrieval can record non-LLM actions
without importing application services.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def retrieval_trace_enabled() -> bool:
    return os.getenv("KG_RETRIEVAL_LLM_TRACE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def trace_agentic_event(stage: str, payload: dict[str, Any]) -> None:
    if not retrieval_trace_enabled():
        return
    path = _trace_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"===== {datetime.now().isoformat(timespec='seconds')} {stage} event ====="]
    human_lines = payload.get("_human_lines") or payload.get("human_lines")
    if isinstance(human_lines, list) and human_lines:
        lines.append("human_summary:")
        for line in human_lines:
            lines.append(f"  {str(line)}")
    for key, value in payload.items():
        if key in {"_human_lines", "human_lines"}:
            continue
        lines.append(f"{key}: {_inline(value)}")
    with path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))
        file.write("\n\n")


def _trace_file() -> Path:
    raw = os.getenv("KG_RETRIEVAL_LLM_TRACE_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("data/logs/kg_retrieval_llm_trace.log")


def _inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
