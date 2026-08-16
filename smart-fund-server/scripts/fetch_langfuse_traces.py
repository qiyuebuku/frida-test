#!/usr/bin/env python3
"""从自建 Langfuse v4 下载完整观测记录并按 Trace（链路）重建 JSON。

Langfuse v4 不再提供旧版 ``/api/public/traces`` 导出接口。本脚本使用
``/api/public/v2/observations``，按 ``traceId`` 聚合 Observation（观测记录）。
为避免扫描整个 ClickHouse，按名称或 Session（会话）查询默认只看最近 7 天；
可以通过 ``--from-timestamp`` 和 ``--to-timestamp`` 调整时间范围。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_TRACE_NAME = "kg.write_path_demo"
DEFAULT_FULL_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
DEFAULT_LIST_FIELDS = "core,basic,time,trace_context"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_REQUEST_ATTEMPTS = 4
PROJECT_ENV_PREFIXES = {
    "agent": "SMART_FUND_AGENT_LANGFUSE",
    "smart-fund-server": "SMART_FUND_SERVER_LANGFUSE",
}

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
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def list_observations(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        fields: str = DEFAULT_LIST_FIELDS,
        trace_id: str | None = None,
        from_start_time: str | None = None,
        to_start_time: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "fields": fields}
        if cursor:
            params["cursor"] = cursor
        if trace_id:
            params["traceId"] = trace_id
        if from_start_time:
            params["fromStartTime"] = from_start_time
        if to_start_time:
            params["toStartTime"] = to_start_time
        return self._get_json("/api/public/v2/observations", params)

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{self._host}{path}?{query}"
        request = urllib.request.Request(url, headers=self._headers)
        last_error: Exception | None = None
        for attempt in range(1, DEFAULT_REQUEST_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    payload = response.read()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == DEFAULT_REQUEST_ATTEMPTS:
                    raise LangfuseApiError(
                        f"Langfuse request failed after {attempt} attempts: {url}: {exc}"
                    ) from exc
                time.sleep(0.5 * (2 ** (attempt - 1)))
        else:  # pragma: no cover - loop always breaks or raises
            raise LangfuseApiError(f"Langfuse request failed: {url}: {last_error}")
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def client_from_env(
    *,
    env_file: Path,
    project: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> LangfuseClient:
    load_env_file(env_file)
    prefix = PROJECT_ENV_PREFIXES[project]
    host = os.environ.get(f"{prefix}_BASE_URL")
    public_key = os.environ.get(f"{prefix}_PUBLIC_KEY")
    secret_key = os.environ.get(f"{prefix}_SECRET_KEY")
    missing = [
        name
        for name, value in {
            f"{prefix}_BASE_URL": host,
            f"{prefix}_PUBLIC_KEY": public_key,
            f"{prefix}_SECRET_KEY": secret_key,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing Langfuse config: {', '.join(missing)}")
    return LangfuseClient(host=str(host), public_key=str(public_key), secret_key=str(secret_key), timeout=timeout)


def _bounded_time_range(
    from_timestamp: str | None,
    to_timestamp: str | None,
) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    upper = to_timestamp or now.isoformat()
    if from_timestamp:
        lower = from_timestamp
    else:
        try:
            upper_dt = datetime.fromisoformat(upper.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SystemExit(f"Invalid --to-timestamp: {upper}") from exc
        lower = (upper_dt - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    return lower, upper


def _all_observations(
    client: Any,
    *,
    fields: str,
    page_size: int,
    trace_id: str | None = None,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        response = client.list_observations(
            cursor=cursor,
            limit=page_size,
            fields=fields,
            trace_id=trace_id,
            from_start_time=from_timestamp,
            to_start_time=to_timestamp,
        )
        observations.extend(_items(response))
        meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
        next_cursor = meta.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)
    return observations


def _matches(observation: dict[str, Any], *, name: str | None, session_id: str | None, tags: list[str] | None) -> bool:
    if name and observation.get("traceName") != name:
        return False
    if session_id and observation.get("sessionId") != session_id:
        return False
    actual_tags = set(observation.get("tags") or [])
    return not tags or set(tags).issubset(actual_tags)


def _group_traces(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        trace_id = str(observation.get("traceId") or "")
        if trace_id:
            grouped.setdefault(trace_id, []).append(observation)
    traces = [_build_trace(trace_id, items) for trace_id, items in grouped.items()]
    return sorted(traces, key=lambda item: str(item.get("timestamp") or ""))


def _build_trace(trace_id: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(observations, key=lambda item: str(item.get("startTime") or ""))
    root = next((item for item in ordered if item.get("isRootObservation")), ordered[0])
    tags = sorted({str(tag) for item in ordered for tag in (item.get("tags") or [])})
    return {
        "id": trace_id,
        "name": root.get("traceName"),
        "timestamp": min((str(item.get("startTime")) for item in ordered if item.get("startTime")), default=""),
        "sessionId": root.get("sessionId"),
        "tags": tags,
        "observations": ordered,
    }


def latest_trace(
    client: Any,
    *,
    name: str | None,
    tags: list[str] | None = None,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    lower, upper = _bounded_time_range(from_timestamp, to_timestamp)
    observations = _all_observations(
        client, fields=DEFAULT_LIST_FIELDS, page_size=page_size,
        from_timestamp=lower, to_timestamp=upper,
    )
    matches = [item for item in observations if _matches(item, name=name, session_id=None, tags=tags)]
    traces = _group_traces(matches)
    if not traces:
        raise SystemExit(f"No Langfuse traces found name={name!r} between {lower} and {upper}")
    return traces[-1]


def list_session_traces(
    client: Any,
    *,
    session_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: str = DEFAULT_LIST_FIELDS,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> list[dict[str, Any]]:
    lower, upper = _bounded_time_range(from_timestamp, to_timestamp)
    observations = _all_observations(
        client, fields=fields, page_size=page_size,
        from_timestamp=lower, to_timestamp=upper,
    )
    return _group_traces([
        item for item in observations
        if _matches(item, name=None, session_id=session_id, tags=None)
    ])


def _get_full_trace(client: Any, *, trace_id: str, fields: str, page_size: int) -> dict[str, Any]:
    observations = _all_observations(client, fields=fields, page_size=page_size, trace_id=trace_id)
    if not observations:
        raise SystemExit(f"No Langfuse observations found for traceId={trace_id!r}")
    return _build_trace(trace_id, observations)


def download_latest_session(
    client: Any, *, out_dir: Path, name: str | None = DEFAULT_TRACE_NAME,
    fields: str = DEFAULT_FULL_FIELDS, page_size: int = DEFAULT_PAGE_SIZE,
    tags: list[str] | None = None, from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> DownloadResult:
    trace = latest_trace(
        client, name=name, tags=tags, from_timestamp=from_timestamp,
        to_timestamp=to_timestamp, page_size=page_size,
    )
    session_id = str(trace.get("sessionId") or "")
    if not session_id:
        raise SystemExit(f"Latest trace has no sessionId: {trace.get('id')}")
    return download_session(
        client, session_id=session_id, out_dir=out_dir, fields=fields,
        page_size=page_size, latest_trace_id=str(trace.get("id") or ""),
        from_timestamp=from_timestamp, to_timestamp=to_timestamp,
    )


def download_session(
    client: Any, *, session_id: str, out_dir: Path,
    fields: str = DEFAULT_FULL_FIELDS, page_size: int = DEFAULT_PAGE_SIZE,
    latest_trace_id: str = "", from_timestamp: str | None = None,
    to_timestamp: str | None = None,
) -> DownloadResult:
    traces = list_session_traces(
        client, session_id=session_id, page_size=page_size,
        from_timestamp=from_timestamp, to_timestamp=to_timestamp,
    )
    if not traces:
        raise SystemExit(f"No Langfuse traces found for sessionId={session_id!r}")
    latest = latest_trace_id or str(traces[-1].get("id") or "")
    session_dir = out_dir / f"session-{_safe_filename(session_id)}"
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest_traces: list[dict[str, Any]] = []
    for trace in traces:
        trace_id = str(trace.get("id") or "")
        if not trace_id:
            continue
        full_trace = _get_full_trace(client, trace_id=trace_id, fields=fields, page_size=page_size)
        trace_file = session_dir / f"trace-{_safe_filename(trace_id)}-full.json"
        trace_file.write_text(json.dumps(full_trace, ensure_ascii=False, indent=2))
        manifest_traces.append({
            "trace_id": trace_id, "name": trace.get("name"),
            "timestamp": trace.get("timestamp"), "file": trace_file.name,
            "summary": summarize_trace(full_trace),
        })
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id, "latest_trace_id": latest,
        "trace_count": len(manifest_traces), "fields": fields,
        "traces": manifest_traces,
    }
    manifest_path = session_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return DownloadResult(session_id, latest, session_dir, len(manifest_traces), manifest_path)


def download_trace(
    client: Any, *, trace_id: str, out_dir: Path,
    fields: str = DEFAULT_FULL_FIELDS, page_size: int = DEFAULT_PAGE_SIZE,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    full_trace = _get_full_trace(client, trace_id=trace_id, fields=fields, page_size=page_size)
    trace_file = out_dir / f"trace-{_safe_filename(trace_id)}-full.json"
    trace_file.write_text(json.dumps(full_trace, ensure_ascii=False, indent=2))
    return trace_file


def summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    observations = trace.get("observations") if isinstance(trace.get("observations"), list) else []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    observations_with_io = 0
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if any(key in observation for key in ("input", "output", "metadata")):
            observations_with_io += 1
        details = observation.get("usageDetails") or observation.get("providedUsageDetails")
        if not isinstance(details, dict):
            continue
        usage["input_tokens"] += int(details.get("input") or details.get("input_tokens") or 0)
        usage["output_tokens"] += int(details.get("output") or details.get("output_tokens") or 0)
        usage["total_tokens"] += int(details.get("total") or details.get("total_tokens") or 0)
    return {
        "name": trace.get("name"), "timestamp": trace.get("timestamp"),
        "observation_count": len(observations),
        "observations_with_io_metadata": observations_with_io, "usage": usage,
    }


def _items(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value).strip("_") or "unknown"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从自建 Langfuse v4 下载观测记录并重建 Trace（链路）。")
    parser.add_argument(
        "--project",
        required=True,
        choices=tuple(PROJECT_ENV_PREFIXES),
        help="必须明确选择 agent 或 smart-fund-server 项目，禁止使用模糊默认项目。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--trace-id", help="按 ID 下载一条完整 Trace（链路）。")
    mode.add_argument("--session-id", help="下载一个 Session（会话）中的全部 Trace（链路）。")
    parser.add_argument("--name", default=DEFAULT_TRACE_NAME, help="查找最新 Session（会话）时使用的 Trace 名称。")
    parser.add_argument("--tag", dest="tags", action="append", help="筛选最新 Trace 时必须包含的标签；可以重复。")
    parser.add_argument("--from-timestamp", help="ISO 起始时间；不传时默认最近 7 天。")
    parser.add_argument("--to-timestamp", help="ISO 结束时间；不传时默认当前时间。")
    parser.add_argument("--fields", default=DEFAULT_FULL_FIELDS, help="Observations API v2 字段组。")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    client = client_from_env(
        env_file=args.env_file,
        project=args.project,
        timeout=args.timeout,
    )
    if args.trace_id:
        trace_file = download_trace(
            client, trace_id=args.trace_id, out_dir=args.out_dir,
            fields=args.fields, page_size=args.page_size,
        )
        print(f"[langfuse] downloaded trace: {trace_file}")
        return 0
    if args.session_id:
        result = download_session(
            client, session_id=args.session_id, out_dir=args.out_dir,
            fields=args.fields, page_size=args.page_size,
            from_timestamp=args.from_timestamp, to_timestamp=args.to_timestamp,
        )
    else:
        result = download_latest_session(
            client, out_dir=args.out_dir, name=args.name, fields=args.fields,
            page_size=args.page_size, tags=args.tags,
            from_timestamp=args.from_timestamp, to_timestamp=args.to_timestamp,
        )
    print(f"[langfuse] session_id: {result.session_id}")
    print(f"[langfuse] latest_trace_id: {result.latest_trace_id}")
    print(f"[langfuse] traces: {result.trace_count}")
    print(f"[langfuse] output_dir: {result.output_dir}")
    print(f"[langfuse] manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
