#!/usr/bin/env python3
"""Build Jettask queue-stream registries for streams created before indexed discovery."""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict

import redis


def parse_priority_stream(prefix: str, stream_key: str) -> tuple[str, str] | None:
    stream_prefix = f"{prefix}:stream:"
    if not stream_key.startswith(stream_prefix):
        return None
    queue_and_priority = stream_key[len(stream_prefix) :]
    match = re.fullmatch(r"(.+):p-?\d+", queue_and_priority)
    if match is None:
        return None
    return match.group(1), stream_key


def registry_key(prefix: str, queue: str) -> str:
    return f"{prefix}:queue:{queue}:streams"


def build_registries(
    client: redis.Redis,
    *,
    prefix: str,
    apply: bool,
    scan_count: int,
) -> tuple[int, int, int]:
    streams_by_queue: dict[str, set[str]] = defaultdict(set)
    scanned = 0
    cursor: int | str = 0
    pattern = f"{prefix}:stream:*:p*"
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=scan_count)
        scanned += len(keys)
        for key in keys:
            parsed = parse_priority_stream(prefix, str(key))
            if parsed is not None:
                queue, stream = parsed
                streams_by_queue[queue].add(stream)
        if int(cursor) == 0:
            break

    indexed = sum(len(streams) for streams in streams_by_queue.values())
    if apply and streams_by_queue:
        pipe = client.pipeline(transaction=False)
        for queue, streams in streams_by_queue.items():
            pipe.sadd(registry_key(prefix, queue), *sorted(streams))
        pipe.execute()
    return scanned, len(streams_by_queue), indexed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default=os.getenv("JETTASK_PREFIX", "fund_aggregator"))
    parser.add_argument("--scan-count", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.scan_count <= 0:
        parser.error("--scan-count must be positive")

    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    scanned, queues, indexed = build_registries(
        client,
        prefix=args.prefix,
        apply=args.apply,
        scan_count=args.scan_count,
    )
    mode = "applied" if args.apply else "dry-run"
    print(f"mode={mode} scanned_candidates={scanned} queues={queues} streams={indexed}")


if __name__ == "__main__":
    main()
