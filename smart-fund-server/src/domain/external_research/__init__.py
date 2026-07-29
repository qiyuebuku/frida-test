"""Provider-neutral external research contracts."""

from src.domain.external_research.models import (
    ExternalContent,
    ExternalSearchItem,
)
from src.domain.external_research.provider import ExternalResearchProvider
from src.domain.external_research.store import ExternalContentStore

__all__ = [
    "ExternalContent",
    "ExternalContentStore",
    "ExternalResearchProvider",
    "ExternalSearchItem",
]
