"""Canonical values returned by external research providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalSearchItem:
    title: str
    url: str
    snippet: str
    source: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalContent:
    content: str
    title: str = ""
    url: str = ""
    media_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)
