"""Bootstrap seed community topics into PG and Milvus.

This script is intentionally narrow: it only ensures seed communities exist as
real kg_graph_communities rows and community semantic documents. It does not
compile news, clean demo data, or create synthetic assignments.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from pprint import pprint


def _project_root() -> Path:
    root = Path(__file__).resolve()
    while root.name != "smart-fund-server" and root.parent != root:
        root = root.parent
    if root.name != "smart-fund-server":
        raise RuntimeError("cannot locate smart-fund-server project root")
    return root


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402


TARGET = "prod"
ADAPTER = "financial"


async def main() -> None:
    service = create_knowledge_service(target=TARGET)
    result = await service.bootstrap_seed_communities(adapter_name=ADAPTER)
    pprint(
        {
            "target": TARGET,
            "adapter": ADAPTER,
            "status": result.get("status"),
            "communities": result.get("communities"),
            "documents_written": result.get("documents_written"),
            "community_ids": result.get("community_ids"),
            "persistence": result.get("persistence"),
        },
        sort_dicts=False,
    )


if __name__ == "__main__":
    asyncio.run(main())
