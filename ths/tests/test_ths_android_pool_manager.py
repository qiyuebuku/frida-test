from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = (
    Path(__file__).parents[1]
    / "deployment"
    / "android-emulator"
    / "ths-android-pool-manager.py"
)
SPEC = importlib.util.spec_from_file_location("ths_android_pool_manager", MODULE_PATH)
assert SPEC and SPEC.loader
manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


def test_waiter_wakes_one_inactive_lane(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path)
    monkeypatch.setattr(manager, "STATE_FILE", tmp_path / "desired.json")
    monkeypatch.setattr(
        manager,
        "_load_status",
        lambda: {"native_waiters": 1, "http_waiters": 0, "backends": []},
    )
    monkeypatch.setattr(
        manager,
        "_unit_active",
        lambda unit: unit == manager.LANES[0].unit,
    )
    wake = Mock()
    monkeypatch.setattr(manager, "wake_lane", wake)

    scaled_at = manager.reconcile_once({}, now=100.0, last_scale_out=0.0)

    assert scaled_at == 100.0
    wake.assert_called_once_with(manager.LANES[1])


def test_idle_lane_scales_in_but_owner_never_does(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path)
    monkeypatch.setattr(manager, "STATE_FILE", tmp_path / "desired.json")
    monkeypatch.setattr(manager, "IDLE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(
        manager,
        "_load_status",
        lambda: {
            "native_waiters": 0,
            "http_waiters": 0,
            "backends": [
                {"name": lane.name, "healthy": True, "active": 0, "stream_sessions": 0}
                for lane in manager.LANES
            ],
        },
    )
    monkeypatch.setattr(manager, "_unit_active", lambda unit: True)
    sleep = Mock()
    monkeypatch.setattr(manager, "sleep_lane", sleep)
    last_busy = {lane.name: 0.0 for lane in manager.LANES}

    manager.reconcile_once(last_busy, now=20.0, last_scale_out=0.0)

    sleep.assert_called_once_with(manager.LANES[-1])
    assert sleep.call_args.args[0].minimum is False
