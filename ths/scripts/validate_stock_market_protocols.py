#!/usr/bin/env python3
"""Validate THS A-share stock-page protocols without persistence or scheduling."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any


CONFIG_URL = (
    "https://eq.10jqka.com.cn/open/api/dynamic_configuration/v1/"
    "config_list?key=gegufeaturelist"
)

RANKINGS: OrderedDict[str, tuple[str, int, int]] = OrderedDict(
    [
        ("rise", ("zhangfu", 34818, 0)),
        ("fall", ("diefu", 34818, 1)),
        ("quick", ("zhangsu", 48, 0)),
        ("turnover", ("chengjiaoe", 19, 0)),
        ("large_order", ("dadanjingliang", 34370, 0)),
        ("volume_ratio", ("liangbi", 34311, 0)),
        ("turnover_rate", ("huanshoulv", 34312, 0)),
        ("main_net_inflow", ("zhulijingliuru", 34391, 0)),
        ("amplitude", ("zhenfu", 34819, 0)),
    ]
)

FIELD_NAMES = {
    "4": "code",
    "5": "code_alias",
    "10": "latest",
    "19": "turnover",
    "48": "speed",
    "55": "name",
    "34311": "volume_ratio",
    "34312": "turnover_rate",
    "34370": "large_order_ratio",
    "34391": "main_net_inflow",
    "34818": "change_rate",
    "34819": "amplitude",
    "36072": "industry",
    "36103": "market",
}


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    payload = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 THSProtocolValidation/1.0",
    }
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"), strict=False)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON from {url}: {raw[:300]!r}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"expected object from {url}, got {type(decoded).__name__}")
    return decoded


def request_bridge_json(
    url: str,
    body: dict[str, Any],
    *,
    attempts: int = 3,
    timeout: float = 50.0,
) -> dict[str, Any]:
    last_payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            last_payload = request_json(
                url,
                method="POST",
                body=body,
                timeout=timeout,
            )
            if last_payload.get("success"):
                return last_payload
            last_error = RuntimeError(str(last_payload.get("error") or "native failure"))
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1.0 + attempt)
    if last_payload is not None:
        return last_payload
    raise RuntimeError(str(last_error or "native request failed"))


def fetch_dynamic_group_config() -> list[dict[str, Any]]:
    payload = None
    last_error = None
    for attempt in range(3):
        try:
            payload = request_json(CONFIG_URL, timeout=20.0)
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    if payload is None:
        raise RuntimeError(f"unable to load THS dynamic group configuration: {last_error}")
    groups = ((payload.get("data") or {}).get("gegufeaturelist") or [])
    if not isinstance(groups, list) or not groups:
        raise RuntimeError("THS dynamic group configuration is empty")
    return [group for group in groups if isinstance(group, dict)]


def _header_ids(group: dict[str, Any]) -> list[str]:
    result = ["55"]
    for header in group.get("headers") or []:
        indicator_id = str((header or {}).get("indicatorId") or "").strip()
        if indicator_id and indicator_id not in result:
            result.append(indicator_id)
    return result


def _order_name(sort_order: Any) -> str | None:
    value = str(sort_order or "").strip()
    if not value:
        return None
    return "ASCENDING" if value == "1" else "DESCENDING"


def fetch_dynamic_group(
    bridge_url: str,
    group: dict[str, Any],
) -> dict[str, Any]:
    prompt_id = str(group.get("promptId") or "").strip()
    if not prompt_id:
        raise RuntimeError(f"dynamic group has no promptId: {group.get('title')!r}")
    sort_header = group.get("sortHeader") or {}
    query_sort_id = str(sort_header.get("indicatorId") or "").strip() or None
    source_sort_id = query_sort_id
    if source_sort_id is None:
        source_sort_id = next(
            (indicator_id for indicator_id in _header_ids(group) if indicator_id != "55"),
            "34818",
        )
    source_sort_order = _order_name(sort_header.get("sortOrder")) or "DESCENDING"
    securities = group.get("securities") or []
    count = max(1, len(securities) or 4)
    request_body = {
        "frame_id": 2312,
        "start": 0,
        "count": count,
        "hurricane_type": "PROMPT_CODE",
        "hurricane_ids": [prompt_id],
        "hurricane_indicator_ids": [],
        "mobile_indicator_ids": _header_ids(group),
        # Older injected probes only expose source-level sorting. Keep the same
        # sort on both layers; the current probe also places it on QueryParam,
        # which is how the stock page constructs the request.
        "sort_indicator_id": source_sort_id,
        "order": source_sort_order,
        "http_source_id": "securities-ranking-slider",
        "timeout_ms": 30000,
    }
    payload = request_bridge_json(
        f"{bridge_url.rstrip('/')}/native/hurricane",
        request_body,
    )
    if not payload.get("success"):
        raise RuntimeError(
            f"dynamic group {group.get('title')!r} failed: {payload.get('error')}"
        )
    rows = []
    for raw_row in (payload.get("data") or {}).get("rows") or []:
        indicators = raw_row.get("indicators") or {}
        row = {
            "code": raw_row.get("code"),
            "market": raw_row.get("market"),
        }
        for indicator_id, cell in indicators.items():
            field_name = FIELD_NAMES.get(str(indicator_id), f"indicator_{indicator_id}")
            row[field_name] = (cell or {}).get("content")
        rows.append(row)
    return {
        "title": group.get("title"),
        "subtitle": group.get("subtitle"),
        "highlight_tag": group.get("highlightTag"),
        "is_show_ranking": str(group.get("isShowRanking") or "") == "1",
        "jump_url": group.get("jumpUrl"),
        "subtitle_jump_url": group.get("subtitleJumpUrl"),
        "data_code": group.get("data_code"),
        "key": group.get("key"),
        "prompt_id": prompt_id,
        "query": group.get("query"),
        "configured_count": count,
        "total": (payload.get("data") or {}).get("total"),
        "rows": rows,
        "request": request_body,
        "captured_query_sort": {
            "indicator_id": query_sort_id,
            "order": _order_name(sort_header.get("sortOrder")),
        },
    }


def _column_value(columns: dict[str, Any], key: str, index: int) -> Any:
    values = columns.get(key)
    if isinstance(values, list) and index < len(values):
        return values[index]
    return None


def fetch_ranking(
    bridge_url: str,
    ranking: str,
    *,
    start: int,
    count: int,
    market_id: int,
) -> dict[str, Any]:
    online_id, sort_id, sort_order = RANKINGS[ranking]
    request_text = "\r\n".join(
        [
            f"startrow={start}",
            f"rowcount={count}",
            f"marketId={market_id}",
            f"sortorder={sort_order}",
            f"sortid={sort_id}",
        ]
    )
    request_body = {
        "onlineId": online_id,
        "protocolId": 1208,
        "pageId": 2312,
        "requestDic": request_text,
        "requestType": 262144,
        "timeoutSeconds": 40,
    }
    payload = request_bridge_json(
        f"{bridge_url.rstrip('/')}/native/unified",
        request_body,
        timeout=50.0,
    )
    if not payload.get("success"):
        raise RuntimeError(f"ranking {ranking} failed: {payload.get('error')}")
    response = payload.get("response") or {}
    columns = ((response.get("body") or {}).get("dataDict") or {})
    if not isinstance(columns, dict):
        raise RuntimeError(f"ranking {ranking} returned no dataDict")
    row_count = max(
        (len(values) for values in columns.values() if isinstance(values, list)),
        default=0,
    )
    rows = []
    for index in range(row_count):
        rows.append(
            {
                name: _column_value(columns, indicator_id, index)
                for indicator_id, name in FIELD_NAMES.items()
                if _column_value(columns, indicator_id, index) is not None
            }
        )
    return {
        "ranking": ranking,
        "online_id": online_id,
        "sort_id": sort_id,
        "sort_order": sort_order,
        "start": start,
        "requested_count": count,
        "returned_count": len(rows),
        "response_head": response.get("head"),
        "rows": rows,
        "request_text": request_text,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate THS stock dynamic groups and ranking protocols.",
    )
    parser.add_argument(
        "--bridge-url",
        default="http://127.0.0.1:18900",
        help="Injected THS bridge base URL.",
    )
    parser.add_argument(
        "--only",
        choices=("all", "dynamic", "ranking"),
        default="all",
    )
    parser.add_argument(
        "--ranking",
        action="append",
        choices=tuple(RANKINGS),
        help="Ranking to validate; repeatable. Defaults to every ranking.",
    )
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--market-id", type=int, default=0)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start < 0:
        raise SystemExit("--start must be >= 0")
    if not 1 <= args.count <= 50:
        raise SystemExit("--count must be between 1 and 50")

    result: dict[str, Any] = {
        "bridge_url": args.bridge_url,
        "dynamic_groups": [],
        "rankings": [],
    }
    failures: list[dict[str, str]] = []

    if args.only in {"all", "dynamic"}:
        for group in fetch_dynamic_group_config():
            try:
                result["dynamic_groups"].append(
                    fetch_dynamic_group(args.bridge_url, group)
                )
            except Exception as exc:  # Keep the rest of the protocol matrix running.
                failures.append(
                    {"capability": f"dynamic:{group.get('title')}", "error": str(exc)}
                )

    if args.only in {"all", "ranking"}:
        for ranking in args.ranking or list(RANKINGS):
            try:
                result["rankings"].append(
                    fetch_ranking(
                        args.bridge_url,
                        ranking,
                        start=args.start,
                        count=args.count,
                        market_id=args.market_id,
                    )
                )
            except Exception as exc:  # Keep the rest of the protocol matrix running.
                failures.append({"capability": f"ranking:{ranking}", "error": str(exc)})

    result["failures"] = failures
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(args.output.resolve())
    else:
        print(rendered)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
