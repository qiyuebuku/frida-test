#!/usr/bin/env python3
"""Atomically migrate ambiguous Langfuse variables to one named project."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path


PROJECT_PREFIXES = {
    "agent": "SMART_FUND_AGENT_LANGFUSE",
    "smart-fund-server": "SMART_FUND_SERVER_LANGFUSE",
}
LEGACY_KEYS = {
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_HOST",
    "KG_LANGFUSE_BASE_URL",
}


def _parse_value(value: str) -> str:
    try:
        parsed = shlex.split(value.strip(), posix=True)
    except ValueError:
        parsed = []
    return parsed[0] if len(parsed) == 1 else value.strip()


def migrate(path: Path, *, project: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw in lines:
        key, separator, value = raw.partition("=")
        if separator:
            values[key.strip()] = _parse_value(value)

    prefix = PROJECT_PREFIXES[project]
    legacy = {
        "PUBLIC_KEY": values.get("LANGFUSE_PUBLIC_KEY", ""),
        "SECRET_KEY": values.get("LANGFUSE_SECRET_KEY", ""),
        "BASE_URL": values.get("LANGFUSE_BASE_URL", "")
        or values.get("LANGFUSE_HOST", "")
        or values.get("KG_LANGFUSE_BASE_URL", ""),
    }
    missing = [name for name, value in legacy.items() if not value]
    if missing:
        raise SystemExit(
            "Legacy Langfuse configuration is incomplete: " + ", ".join(missing)
        )

    updates = {f"{prefix}_{suffix}": value for suffix, value in legacy.items()}
    for key, value in updates.items():
        existing = values.get(key)
        if existing and existing != value:
            raise SystemExit(f"Refusing to overwrite different project value: {key}")

    output: list[str] = []
    written: set[str] = set()
    for raw in lines:
        key, separator, _ = raw.partition("=")
        normalized = key.strip()
        if separator and normalized in LEGACY_KEYS:
            continue
        if separator and normalized in updates:
            if normalized not in written:
                output.append(f"{normalized}={shlex.quote(updates[normalized])}")
                written.add(normalized)
            continue
        output.append(raw)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={shlex.quote(value)}")

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--project", required=True, choices=tuple(PROJECT_PREFIXES))
    args = parser.parse_args()
    migrate(args.env_file, project=args.project)


if __name__ == "__main__":
    main()
