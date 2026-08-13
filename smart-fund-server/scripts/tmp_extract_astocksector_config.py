#!/usr/bin/env python3
import json
import sys


def walk(value):
    if isinstance(value, dict):
        if value.get("type") in {"PlateCard", "SecuritiesListCard"}:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        for child in value.values():
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)


walk(json.load(sys.stdin))
