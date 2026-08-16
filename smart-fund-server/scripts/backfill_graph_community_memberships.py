#!/usr/bin/env python3
"""Backfill normalized Graph Community memberships from current state."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.connections import get_engine, get_session  # noqa: E402
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeGraphCommunity,
    KnowledgeGraphCommunityMembership,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", default="prod", choices=("prod", "test"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    KnowledgeGraphCommunityMembership.__table__.create(
        bind=get_engine(args.target),
        checkfirst=True,
    )
    with get_session(args.target) as session:
        communities = list(
            session.scalars(
                select(KnowledgeGraphCommunity).where(
                    KnowledgeGraphCommunity.adapter_name == args.adapter,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
            ).all()
        )
        rows = [
            {
                "adapter_name": args.adapter,
                "card_id": card_id,
                "community_id": community.community_id,
            }
            for community in communities
            for card_id in community.member_card_ids or []
        ]
        duplicate_count = len(rows) - len(
            {(row["adapter_name"], row["card_id"]) for row in rows}
        )
        if duplicate_count:
            raise RuntimeError(f"发现重复 Community Membership: {duplicate_count}")
        if args.apply:
            session.execute(
                delete(KnowledgeGraphCommunityMembership).where(
                    KnowledgeGraphCommunityMembership.adapter_name == args.adapter
                )
            )
            for offset in range(0, len(rows), 2_000):
                session.execute(
                    pg_insert(KnowledgeGraphCommunityMembership).values(
                        rows[offset : offset + 2_000]
                    )
                )
        print(
            {
                "adapter": args.adapter,
                "communities": len(communities),
                "memberships": len(rows),
                "duplicates": duplicate_count,
                "applied": args.apply,
            }
        )


if __name__ == "__main__":
    main()
