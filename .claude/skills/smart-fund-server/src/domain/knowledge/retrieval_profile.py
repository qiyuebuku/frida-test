"""Lightweight timing logs for KG retrieval debugging."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("src.domain.knowledge.retrieval.profile")


def retrieval_profile_enabled() -> bool:
    return os.getenv("KG_RETRIEVAL_PROFILE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@contextmanager
def profile_span(name: str, **fields) -> Iterator[None]:
    if not retrieval_profile_enabled():
        yield
        return
    started = time.perf_counter()
    if _profile_verbose():
        logger.log(_profile_log_level(), "[kg_profile] START %s %s", name, _fields_text(fields))
    try:
        yield
    finally:
        duration = time.perf_counter() - started
        if _profile_verbose() or duration >= _profile_min_seconds():
            logger.log(
                _profile_log_level(),
                "[kg_profile] DONE %s duration=%.3fs %s",
                name,
                duration,
                _fields_text(fields),
            )


def profile_event(name: str, **fields) -> None:
    if retrieval_profile_enabled() and _profile_verbose():
        logger.log(_profile_log_level(), "[kg_profile] %s %s", name, _fields_text(fields))


def _fields_text(fields: dict) -> str:
    cleaned = {key: value for key, value in fields.items() if value is not None}
    if not cleaned:
        return ""
    return " ".join(f"{key}={value!r}" for key, value in cleaned.items())


def _profile_verbose() -> bool:
    return os.getenv("KG_RETRIEVAL_PROFILE_VERBOSE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _profile_min_seconds() -> float:
    raw = os.getenv("KG_RETRIEVAL_PROFILE_MIN_MS", "1000").strip()
    try:
        return max(0.0, float(raw) / 1000.0)
    except ValueError:
        return 1.0


def _profile_log_level() -> int:
    raw = os.getenv("KG_RETRIEVAL_PROFILE_LOG_LEVEL", "DEBUG").strip().upper()
    return getattr(logging, raw, logging.DEBUG)
