#!/usr/bin/env python3
"""Print sanitized Redis CPU diagnostics without exposing values or auth."""

from __future__ import annotations

import argparse
import collections
import os

import redis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-slowlog", action="store_true")
    args = parser.parse_args()
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    key_stats = client.info("commandstats").get("cmdstat_keys", {})
    print(f"KEYS_CALLS {int(key_stats.get('calls', 0))}")
    print(f"OPS_PER_SEC {int(client.info().get('instantaneous_ops_per_sec', 0))}")
    if args.reset_slowlog:
        client.slowlog_reset()
        print("SLOWLOG_RESET ok")
        return
    slow_counts: collections.Counter[str] = collections.Counter()
    slow_total_us: collections.Counter[str] = collections.Counter()
    slow_max_us: dict[str, int] = {}
    key_patterns: collections.Counter[str] = collections.Counter()
    for item in client.slowlog_get(128):
        command_line = item.get("command") or ""
        if isinstance(command_line, bytes):
            command_line = command_line.decode("utf-8", errors="replace")
        parts = str(command_line).split(maxsplit=1)
        command = parts[0].upper() if parts else "UNKNOWN"
        duration = int(item.get("duration") or 0)
        slow_counts[command] += 1
        slow_total_us[command] += duration
        slow_max_us[command] = max(slow_max_us.get(command, 0), duration)
        if command == "KEYS" and len(parts) > 1:
            key_patterns[parts[1][:160]] += 1

    print("SLOW_COMMANDS")
    for command, count in slow_counts.most_common():
        print(
            command,
            f"count={count}",
            f"total_ms={slow_total_us[command] / 1000:.1f}",
            f"max_ms={slow_max_us[command] / 1000:.1f}",
        )
    print("KEYS_PATTERNS")
    for pattern, count in key_patterns.most_common(30):
        print(f"count={count} pattern={pattern}")

    command_counts: collections.Counter[str] = collections.Counter()
    client_names: collections.Counter[str] = collections.Counter()
    client_addresses: collections.Counter[str] = collections.Counter()
    for item in client.client_list():
        command_counts[str(item.get("cmd") or "unknown")] += 1
        client_names[str(item.get("name") or "unnamed")] += 1
        address = str(item.get("addr") or "unknown")
        client_addresses[address.rsplit(":", 1)[0]] += 1
    print("CLIENT_COMMANDS")
    for command, count in command_counts.most_common(30):
        print(f"count={count} command={command}")
    print("CLIENT_NAMES")
    for name, count in client_names.most_common(30):
        print(f"count={count} name={name}")
    print("CLIENT_HOSTS")
    for address, count in client_addresses.most_common(30):
        print(f"count={count} host={address}")


if __name__ == "__main__":
    main()
