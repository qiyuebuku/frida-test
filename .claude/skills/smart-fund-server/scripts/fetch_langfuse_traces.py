#!/usr/bin/env python3
"""Download full Langfuse traces without the UI export truncation.

Default behavior:
  1. Load Langfuse credentials from .env.
  2. Find the latest trace matching --name.
  3. Use its sessionId to fetch every trace in that session.
  4. Fetch each trace with full IO/metadata/observations and write JSON files.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TRACE_NAME = "kg.write_path_demo"
DEFAULT_FULL_FIELDS = "core,io,scores,observations,metrics"
DEFAULT_LIST_FIELDS = "core,metrics"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 60

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_OUT_DIR = PROJECT_ROOT / "langfuse-full-traces"


@dataclass(frozen=True)
class DownloadResult:
    session_id: str
    latest_trace_id: str
    output_dir: Path
    trace_count: int
    manifest_path: Path


class LangfuseApiError(RuntimeError):
    pass


class LangfuseClient:
    def __init__(self, *, host: str, public_key: str, secret_key: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self._host = host.rstrip("/")
        self._timeout = timeout
        token = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }

    def list_traces(
        self,
        *,
        page: int,
        limit: int,
        order_by: str = "timestamp.desc",
        fields: str = DEFAULT_LIST_FIELDS,
        name: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        from_timestamp: str | None = None,
        to_timestamp: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "orderBy": order_by,
            "fields": fields,
        }
        if name:
            params["name"] = name
        if session_id:
            params["sessionId"] = session_id
        if tags:
            params["tags"] = tags
        if from_timestamp:
            params["fromTimestamp"] = from_timestamp
        if to_timestamp:
            params["toTimestamp"] = to_timestamp
        return self._get_json("/api/public/traces", params)

    def get_trace(self, trace_id: str, *, fields: str = DEFAULT_FULL_FIELDS) -> dict[str, Any]:
        return self._get_json(f"/api/public/traces/{urllib.parse.quote(trace_id)}", {"fields": fields})

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{self._host}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = response.read()
        except Exception as exc:  # noqa: BLE001 - CLI should surface the original request failure.
            raise LangfuseApiError(f"Langfuse request failed: {url}: {exc}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LangfuseApiError(f"Langfuse response is not JSON: {url}") from exc
        if not isinstance(data, dict):
            raise LangfuseApiError(f"Langfuse response must be JSON object: {url}")
        return data


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def client_from_env(*, env_file: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> LangfuseClient:
    load_env_file(env_file)
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    missing = [
        name
        for name, value in {
            "LANGFUSE_HOST or LANGFUSE_BASE_URL": host,
            "LANGFUSE_PUBLIC_KEY": public_key,
            "LANGFUSE_SECRET_KEY": secret_key,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing Langfuse config: {', '.join(missing)}")
    return LangfuseClient(host=str(host), public_key=str(public_key), secret_key=str(secret_key), timeout=timeout)


def latest_trace(
    client: Any,
    *,
    name: str | None,
    tags: list[str] | None = None,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> dict[str, Any]:
    page = client.list_traces(
        page=1,
        limit=10,
        order_by="timestamp.desc",
        fields=DEFAULT_LIST_FIELDS,
        name=name,
        tags=tags,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )
    traces = _trace_items(page)
    if not traces:
        suffix = f" name={name!r}" if name else ""
        raise SystemExit(f"No Langfuse traces found{suffix}")
    return traces[0]


def list_session_traces(
    client: Any,
    *,
    session_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: str = DEFAULT_LIST_FIELDS,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.list_traces(
            page=page,
            limit=page_size,
            order_by="timestamp.asc",
            fields=fields,
            session_id=session_id,
        )
        traces.extend(_trace_items(response))
        meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
        total_pages = int(meta.get("totalPages") or page)
        if page >= total_pages:
            break
        page += 1
    return traces


def download_latest_session(
    client: Any,
    *,
    out_dir: Path,
    name: str | None = DEFAULT_TRACE_NAME,
    fields: str = DEFAULT_FULL_FIELDS,
    page_size: int = DEFAULT_PAGE_SIZE,
    tags: list[str] | None = None,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> DownloadResult:
    trace = latest_trace(
        client,
        name=name,
        tags=tags,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )
    session_id = str(trace.get("sessionId") or "")
    if not session_id:
        raise SystemExit(f"Latest trace has no sessionId: {trace.get('id')}")
    return download_session(
        client,
        session_id=session_id,
        out_dir=out_dir,
        fields=fields,
        page_size=page_size,
        latest_trace_id=str(trace.get("id") or ""),
    )


def download_session(
    client: Any,
    *,
    session_id: str,
    out_dir: Path,
    fields: str = DEFAULT_FULL_FIELDS,
    page_size: int = DEFAULT_PAGE_SIZE,
    latest_trace_id: str = "",
) -> DownloadResult:
    traces = list_session_traces(client, session_id=session_id, page_size=page_size)
    if not traces:
        raise SystemExit(f"No Langfuse traces found for sessionId={session_id!r}")
    latest = latest_trace_id or str(max(traces, key=lambda item: str(item.get("timestamp") or "")).get("id") or "")
    session_dir = out_dir / f"session-{_safe_filename(session_id)}"
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest_traces: list[dict[str, Any]] = []
    for trace in traces:
        trace_id = str(trace.get("id") or "")
        if not trace_id:
            continue
        full_trace = client.get_trace(trace_id, fields=fields)
        trace_file = session_dir / f"trace-{_safe_filename(trace_id)}-full.json"
        trace_file.write_text(json.dumps(full_trace, ensure_ascii=False, indent=2))
        manifest_traces.append(
            {
                "trace_id": trace_id,
                "name": trace.get("name"),
                "timestamp": trace.get("timestamp"),
                "file": trace_file.name,
                "summary": summarize_trace(full_trace),
            }
        )
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "latest_trace_id": latest,
        "trace_count": len(manifest_traces),
        "fields": fields,
        "traces": manifest_traces,
    }
    manifest_path = session_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return DownloadResult(
        session_id=session_id,
        latest_trace_id=latest,
        output_dir=session_dir,
        trace_count=len(manifest_traces),
        manifest_path=manifest_path,
    )


def download_trace(client: Any, *, trace_id: str, out_dir: Path, fields: str = DEFAULT_FULL_FIELDS) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    full_trace = client.get_trace(trace_id, fields=fields)
    trace_file = out_dir / f"trace-{_safe_filename(trace_id)}-full.json"
    trace_file.write_text(json.dumps(full_trace, ensure_ascii=False, indent=2))
    return trace_file


def summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    observations = trace.get("observations") if isinstance(trace.get("observations"), list) else []
    usage = {
        "input_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    observations_with_io = 0
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if "input" in observation or "output" in observation or "metadata" in observation:
            observations_with_io += 1
        details = observation.get("providedUsageDetails") or observation.get("usageDetails")
        if not isinstance(details, dict):
            continue
        for key in usage:
            usage[key] += int(details.get(key) or 0)
    usage["non_cached_tokens"] = (
        usage["prompt_cache_miss_tokens"] + usage["output_tokens"] + usage["reasoning_tokens"]
    )
    return {
        "name": trace.get("name"),
        "timestamp": trace.get("timestamp"),
        "observation_count": len(observations),
        "observations_with_io_metadata": observations_with_io,
        "usage": usage,
    }


def _trace_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value).strip("_") or "unknown"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download full Langfuse traces by trace id or latest session.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--trace-id", help="Download one full trace by id.")
    mode.add_argument("--session-id", help="Download all traces in a session.")
    parser.add_argument("--name", default=DEFAULT_TRACE_NAME, help=f"Trace name for latest-session lookup. Default: {DEFAULT_TRACE_NAME}")
    parser.add_argument("--tag", dest="tags", action="append", help="Require tag when selecting the latest trace. Can be repeated.")
    parser.add_argument("--from-timestamp", help="ISO timestamp lower bound for latest trace lookup.")
    parser.add_argument("--to-timestamp", help="ISO timestamp upper bound for latest trace lookup.")
    parser.add_argument("--fields", default=DEFAULT_FULL_FIELDS, help=f"Fields passed to trace get. Default: {DEFAULT_FULL_FIELDS}")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Trace list page size.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="Path to .env containing Langfuse credentials.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    client = client_from_env(env_file=args.env_file, timeout=args.timeout)
    if args.trace_id:
        trace_file = download_trace(client, trace_id=args.trace_id, out_dir=args.out_dir, fields=args.fields)
        print(f"[langfuse] downloaded trace: {trace_file}")
        return 0
    if args.session_id:
        result = download_session(
            client,
            session_id=args.session_id,
            out_dir=args.out_dir,
            fields=args.fields,
            page_size=args.page_size,
        )
    else:
        result = download_latest_session(
            client,
            out_dir=args.out_dir,
            name=args.name,
            fields=args.fields,
            page_size=args.page_size,
            tags=args.tags,
            from_timestamp=args.from_timestamp,
            to_timestamp=args.to_timestamp,
        )
    print(f"[langfuse] session_id: {result.session_id}")
    print(f"[langfuse] latest_trace_id: {result.latest_trace_id}")
    print(f"[langfuse] traces: {result.trace_count}")
    print(f"[langfuse] output_dir: {result.output_dir}")
    print(f"[langfuse] manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
