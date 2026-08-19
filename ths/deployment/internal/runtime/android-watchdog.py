#!/usr/bin/env python3
# Installed runtime implementation; not a public deployment entrypoint.
"""Detect a half-dead Android guest and recover the complete THS stack."""

from __future__ import annotations

import logging
import json
import subprocess
import time
import urllib.request
from pathlib import Path


LOGGER = logging.getLogger("ths-android-watchdog")
ADB = "/home/yuyangruan/android-sdk/platform-tools/adb"
SERIAL = "emulator-5556"
PROBE_TIMEOUT_SECONDS = 15
BUSINESS_ADB_TIMEOUT_SECONDS = 60
PROBE_INTERVAL_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 3
RECOVERY_COOLDOWN_SECONDS = 900
BOOT_TIMEOUT_SECONDS = 300
BRIDGE_TIMEOUT_SECONDS = 180
DISPLAY_SLEEP_SETTLE_SECONDS = 2
BRIDGES = (
    ("ths-collector-bridge@primary.service", 49301),
    ("ths-collector-bridge@futures.service", 49311),
    ("ths-collector-bridge@us-ranking.service", 49321),
    ("ths-collector-bridge@us-etf.service", 49331),
    ("ths-collector-bridge@pool5.service", 49341),
    ("ths-collector-bridge@pool6.service", 49361),
    ("ths-collector-bridge@pool7.service", 49371),
    ("ths-collector-bridge@pool8.service", 49381),
)
BRIDGE_NAMES = {
    "ths-collector-bridge@primary.service": "primary",
    "ths-collector-bridge@futures.service": "futures",
    "ths-collector-bridge@us-ranking.service": "us-ranking",
    "ths-collector-bridge@us-etf.service": "us-etf",
    "ths-collector-bridge@pool5.service": "pool5",
    "ths-collector-bridge@pool6.service": "pool6",
    "ths-collector-bridge@pool7.service": "pool7",
    "ths-collector-bridge@pool8.service": "pool8",
}
POOL_STATE_FILE = Path("/run/ths-android-pool/desired.json")


def _run(command: list[str], *, timeout: int, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _adb(*args: str, timeout: int = BUSINESS_ADB_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "/usr/sbin/runuser",
            "-u",
            "yuyangruan",
            "--",
            "/usr/bin/env",
            "HOME=/home/yuyangruan",
            ADB,
            "-s",
            SERIAL,
            *args,
        ],
        timeout=timeout,
    )


def probe_android_shell() -> tuple[bool, str]:
    """Probe guest command execution, not merely the ADB transport state."""

    try:
        result = _adb(
            "shell",
            "echo",
            "ths-watchdog-ok",
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = result.stdout.replace("\r", "").strip()
    if output != "ths-watchdog-ok":
        return False, f"unexpected adb shell response: {output[:200]!r}"
    return True, "ok"


def _systemctl(*args: str, timeout: int = 120) -> None:
    _run(["/usr/bin/systemctl", *args], timeout=timeout)


def _wait_for_boot() -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            result = _adb(
                "shell",
                "getprop",
                "sys.boot_completed",
                timeout=PROBE_TIMEOUT_SECONDS,
            )
            if result.stdout.replace("\r", "").strip() == "1":
                healthy, detail = probe_android_shell()
                if healthy:
                    return
                last_error = detail
            else:
                last_error = f"boot_completed={result.stdout.strip()!r}"
        except (subprocess.SubprocessError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(5)
    raise RuntimeError(f"Android boot timed out: {last_error}")


def _wait_for_bridge(port: int) -> None:
    deadline = time.monotonic() + BRIDGE_TIMEOUT_SECONDS
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health",
                timeout=5,
            ) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # noqa: BLE001 - health boundary
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise RuntimeError(f"bridge port={port} did not recover: {last_error}")


def sleep_guest_display() -> None:
    """Stop unnecessary SwiftShader rendering after all collectors are healthy."""

    _adb("shell", "input", "keyevent", "KEYCODE_SLEEP", timeout=PROBE_TIMEOUT_SECONDS)
    time.sleep(DISPLAY_SLEEP_SETTLE_SECONDS)
    result = _adb("shell", "dumpsys", "power", timeout=PROBE_TIMEOUT_SECONDS)
    if "mWakefulness=Asleep" not in result.stdout:
        raise RuntimeError("Android display did not enter the asleep state")


def ensure_guest_display_asleep() -> None:
    """Repair display wakeups caused by an individual lane bootstrap."""

    result = _adb("shell", "dumpsys", "power", timeout=PROBE_TIMEOUT_SECONDS)
    if "mWakefulness=Awake" not in result.stdout:
        return
    LOGGER.warning("Android display woke unexpectedly; returning it to sleep")
    sleep_guest_display()


def _desired_bridge_names() -> set[str]:
    try:
        result = _run(
            ["/usr/bin/systemctl", "is-active", "ths-android-pool-manager.service"],
            timeout=15,
            check=False,
        )
        if result.stdout.strip() != "active":
            return set(BRIDGE_NAMES.values())
    except (subprocess.SubprocessError, OSError):
        return set(BRIDGE_NAMES.values())
    try:
        payload = json.loads(POOL_STATE_FILE.read_text(encoding="utf-8"))
        active = {str(item) for item in payload.get("active", [])}
        if active:
            active.add("primary")
            return active
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return set(BRIDGE_NAMES.values())


def recover_android_stack() -> None:
    """Restart emulator and rebuild all user-specific bridge processes."""

    bridge_units = [unit for unit, _ in BRIDGES]
    desired_names = _desired_bridge_names()
    LOGGER.warning("Stopping THS services before Android guest recovery")
    _systemctl("stop", "ths-android-pool-manager.service", timeout=60)
    _systemctl("stop", "smart-fund-ths-realtime-stream.service", timeout=90)
    _systemctl("stop", "ths-app-load-balancer.service", timeout=60)
    _systemctl("stop", *reversed(bridge_units), timeout=120)
    _systemctl("restart", "ths-android-emulator.service", timeout=180)
    _wait_for_boot()
    for unit, port in BRIDGES:
        if BRIDGE_NAMES[unit] not in desired_names:
            continue
        LOGGER.info("Starting %s", unit)
        _systemctl("start", unit, timeout=120)
        _wait_for_bridge(port)
    _systemctl("start", "ths-app-load-balancer.service", timeout=60)
    _systemctl("start", "smart-fund-ths-realtime-stream.service", timeout=60)
    sleep_guest_display()
    LOGGER.warning("Android guest and THS collection stack recovered")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    failures = 0
    last_recovery_at = 0.0
    while True:
        healthy, detail = probe_android_shell()
        if healthy:
            try:
                ensure_guest_display_asleep()
            except Exception:  # noqa: BLE001 - display policy must not kill watchdog
                LOGGER.exception("Failed to enforce Android display sleep")
            if failures:
                LOGGER.info("Android shell recovered after %s failed probe(s)", failures)
            failures = 0
        else:
            failures += 1
            LOGGER.warning(
                "Android shell probe failed (%s/%s): %s",
                failures,
                MAX_CONSECUTIVE_FAILURES,
                detail,
            )
            cooldown_elapsed = time.monotonic() - last_recovery_at
            if (
                failures >= MAX_CONSECUTIVE_FAILURES
                and cooldown_elapsed >= RECOVERY_COOLDOWN_SECONDS
            ):
                try:
                    recover_android_stack()
                except Exception:  # noqa: BLE001 - watchdog must remain alive
                    LOGGER.exception("Android stack recovery failed")
                finally:
                    last_recovery_at = time.monotonic()
                    failures = 0
        time.sleep(PROBE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
