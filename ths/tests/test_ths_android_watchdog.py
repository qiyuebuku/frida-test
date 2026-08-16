from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = (
    Path(__file__).parents[1]
    / "deployment"
    / "android-emulator"
    / "ths-android-watchdog.py"
)
SPEC = importlib.util.spec_from_file_location("ths_android_watchdog", MODULE_PATH)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def test_probe_requires_guest_shell_round_trip(monkeypatch) -> None:
    adb = Mock(return_value=subprocess.CompletedProcess([], 0, "ths-watchdog-ok\r\n", ""))
    monkeypatch.setattr(
        watchdog,
        "_adb",
        adb,
    )

    assert watchdog.probe_android_shell() == (True, "ok")
    adb.assert_called_once_with(
        "shell",
        "echo",
        "ths-watchdog-ok",
        timeout=watchdog.PROBE_TIMEOUT_SECONDS,
    )


def test_probe_treats_adb_shell_timeout_as_unhealthy(monkeypatch) -> None:
    monkeypatch.setattr(
        watchdog,
        "_adb",
        Mock(side_effect=subprocess.TimeoutExpired(["adb", "shell"], 15)),
    )

    healthy, detail = watchdog.probe_android_shell()

    assert healthy is False
    assert "TimeoutExpired" in detail


def test_recovery_restarts_emulator_before_bridges(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        watchdog,
        "_systemctl",
        lambda *args, **kwargs: calls.append(tuple(args)),
    )
    monkeypatch.setattr(watchdog, "_wait_for_boot", Mock())
    monkeypatch.setattr(watchdog, "_wait_for_bridge", Mock())
    sleep_guest_display = Mock()
    monkeypatch.setattr(watchdog, "sleep_guest_display", sleep_guest_display)

    watchdog.recover_android_stack()

    restart_index = calls.index(("restart", "ths-android-emulator.service"))
    first_bridge_index = calls.index(("start", watchdog.BRIDGES[0][0]))
    assert restart_index < first_bridge_index
    assert calls[-2:] == [
        ("start", "ths-app-load-balancer.service"),
        ("start", "smart-fund-ths-realtime-stream.service"),
    ] or calls[-3:-1] == [
        ("start", "ths-app-load-balancer.service"),
        ("start", "smart-fund-ths-realtime-stream.service"),
    ]
    sleep_guest_display.assert_called_once_with()


def test_sleep_guest_display_verifies_power_state(monkeypatch) -> None:
    adb = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "  mWakefulness=Asleep\n", ""),
        ]
    )
    monkeypatch.setattr(watchdog, "_adb", adb)
    monkeypatch.setattr(watchdog.time, "sleep", Mock())

    watchdog.sleep_guest_display()

    assert adb.call_args_list[0].args == (
        "shell",
        "input",
        "keyevent",
        "KEYCODE_SLEEP",
    )
    assert adb.call_args_list[1].args == ("shell", "dumpsys", "power")


def test_awake_display_is_returned_to_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        watchdog,
        "_adb",
        Mock(return_value=subprocess.CompletedProcess([], 0, "mWakefulness=Awake\n", "")),
    )
    sleep = Mock()
    monkeypatch.setattr(watchdog, "sleep_guest_display", sleep)

    watchdog.ensure_guest_display_asleep()

    sleep.assert_called_once_with()
