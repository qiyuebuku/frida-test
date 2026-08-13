#!/usr/bin/env python3
import json
import re
import sys


payload = json.loads(sys.stdin.read(), strict=False)
for record in payload.get("records") or []:
    if record.get("phase") != "query":
        continue
    detail = record.get("detail") or ""
    interesting = []
    for pattern in (
        r"QueryParam[^\n]{0,6000}",
        r"MobileHqSecuritiesSource[^\n]{0,2500}",
        r"HurricaneSecuritiesSource[^\n]{0,2500}",
    ):
        match = re.search(pattern, detail)
        if match:
            interesting.append(match.group(0))
    print(json.dumps({
        "at_ms": record.get("at_ms"),
        "query_id": record.get("query_id"),
        "detail": "\n".join(interesting) or detail[:8000],
    }, ensure_ascii=False, indent=2))
