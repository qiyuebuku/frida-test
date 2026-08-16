from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_jettask_priority_stream_registry.py"
SPEC = importlib.util.spec_from_file_location("migrate_jettask_priority_stream_registry", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_priority_stream_preserves_queue_colons() -> None:
    assert MODULE.parse_priority_stream(
        "fund_aggregator_prod",
        "fund_aggregator_prod:stream:card:relation:p0",
    ) == ("card:relation", "fund_aggregator_prod:stream:card:relation:p0")


def test_parse_priority_stream_rejects_non_numeric_suffix() -> None:
    assert MODULE.parse_priority_stream("app", "app:stream:orders:pending") is None
    assert MODULE.parse_priority_stream("app", "other:stream:orders:p0") is None
