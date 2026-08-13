#!/usr/bin/env python3
"""把隔离恢复实例中的缺失向量原样合并进当前 Milvus。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable
from typing import Any

from pymilvus import MilvusClient

LOGGER = logging.getLogger("merge_milvus_collections")
DEFAULT_COLLECTIONS = (
    "kg_evidence_chunks",
    "kg_cognitive_cards",
    "kg_cognitive_card_focus_evidence",
    "kg_card_relations",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--target-uri", required=True)
    parser.add_argument("--collection", action="append", dest="collections")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _target_ids(client: MilvusClient, collection_name: str, batch_size: int) -> set[str]:
    result: set[str] = set()
    iterator = client.query_iterator(
        collection_name=collection_name,
        batch_size=batch_size,
        filter="",
        output_fields=["target_id"],
    )
    try:
        while rows := iterator.next():
            result.update(str(row["target_id"]) for row in rows if row.get("target_id"))
    finally:
        iterator.close()
    return result


def _missing_rows(rows: Iterable[dict[str, Any]], existing_ids: set[str]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for row in rows:
        target_id = str(row.get("target_id") or "")
        if not target_id or target_id in existing_ids:
            continue
        missing.append(dict(row))
        existing_ids.add(target_id)
    return missing


def _merge_collection(
    source: MilvusClient,
    target: MilvusClient,
    collection_name: str,
    *,
    batch_size: int,
    dry_run: bool,
) -> dict[str, int]:
    existing_ids = _target_ids(target, collection_name, batch_size)
    source_rows = 0
    inserted = 0
    skipped = 0
    iterator = source.query_iterator(
        collection_name=collection_name,
        batch_size=batch_size,
        filter="",
        output_fields=["*"],
    )
    try:
        while rows := iterator.next():
            source_rows += len(rows)
            missing = _missing_rows(rows, existing_ids)
            skipped += len(rows) - len(missing)
            if missing and not dry_run:
                target.upsert(collection_name=collection_name, data=missing)
            inserted += len(missing)
            LOGGER.info(
                "collection=%s scanned=%s inserted=%s skipped=%s dry_run=%s",
                collection_name,
                source_rows,
                inserted,
                skipped,
                dry_run,
            )
    finally:
        iterator.close()
    return {"source_rows": source_rows, "inserted": inserted, "skipped": skipped}


def main() -> None:
    args = _parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        raise ValueError("batch-size 必须在 1..1000 之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    source = MilvusClient(uri=args.source_uri)
    target = MilvusClient(uri=args.target_uri)
    try:
        source_names = set(source.list_collections())
        target_names = set(target.list_collections())
        collections = tuple(args.collections or DEFAULT_COLLECTIONS)
        for collection_name in collections:
            if collection_name not in source_names or collection_name not in target_names:
                raise RuntimeError(f"collection 不同时存在于新旧实例: {collection_name}")
            result = _merge_collection(
                source,
                target,
                collection_name,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )
            LOGGER.info("collection=%s completed result=%s", collection_name, result)
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
