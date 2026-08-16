#!/usr/bin/env python3
"""Configure the explicit smart-fund-server project on local Langfuse."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError:
            parsed = []
        values[key.strip()] = parsed[0] if len(parsed) == 1 else value
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for raw in lines:
        key, separator, _ = raw.partition("=")
        normalized_key = key.strip()
        if separator and normalized_key in remaining:
            output.append(f"{normalized_key}={shlex.quote(remaining.pop(normalized_key))}")
        else:
            output.append(raw)
    output.extend(f"{key}={shlex.quote(value)}" for key, value in remaining.items())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--langfuse-env", required=True, type=Path)
    parser.add_argument("--client-env", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    source = parse_env(args.langfuse_env)
    required = (
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY",
    )
    missing = [key for key in required if not source.get(key)]
    if missing:
        raise SystemExit(f"Langfuse environment is missing: {', '.join(missing)}")
    update_env(
        args.client_env,
        {
            "SMART_FUND_SERVER_LANGFUSE_PUBLIC_KEY": source["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"],
            "SMART_FUND_SERVER_LANGFUSE_SECRET_KEY": source["LANGFUSE_INIT_PROJECT_SECRET_KEY"],
            "SMART_FUND_SERVER_LANGFUSE_BASE_URL": args.base_url.rstrip("/"),
            "KG_LANGFUSE_ENABLED": "true",
        },
    )


if __name__ == "__main__":
    main()
