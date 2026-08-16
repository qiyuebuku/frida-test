#!/usr/bin/env python3
"""Scale THS Android user lanes from load-balancer demand."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


LOGGER = logging.getLogger("ths-android-pool-manager")
ADB = "/home/yuyangruan/android-sdk/platform-tools/adb"
SERIAL = "emulator-5554"
STATUS_URL = "http://127.0.0.1:49350/lb/status"
STATE_DIR = Path("/run/ths-android-pool")
STATE_FILE = STATE_DIR / "desired.json"
POLL_SECONDS = 2
IDLE_TIMEOUT_SECONDS = int(os.getenv("THS_POOL_IDLE_TIMEOUT_SECONDS", "600"))
SCALE_OUT_COOLDOWN_SECONDS = 10


@dataclass(frozen=True)
class Lane:
    name: str
    unit: str
    user_id: int
    port: int
    minimum: bool = False


LANES = (
    Lane("owner", "ths-collector-bridge.service", 0, 49301, minimum=True),
    Lane("futures", "ths-collector-bridge@futures.service", 12, 49311),
    Lane("us-ranking", "ths-collector-bridge@us-ranking.service", 13, 49321),
    Lane("us-etf", "ths-collector-bridge@us-etf.service", 14, 49331),
    Lane("pool5", "ths-collector-bridge@pool5.service", 10, 49341),
    Lane("pool6", "ths-collector-bridge@pool6.service", 11, 49361),
    Lane("pool7", "ths-collector-bridge@pool7.service", 15, 49371),
    Lane("pool8", "ths-collector-bridge@pool8.service", 16, 49381),
)


def _run(command: list[str], *, timeout: int = 360, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _adb(*args: str, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "/usr/sbin/runuser", "-u", "yuyangruan", "--", "/usr/bin/env",
            "HOME=/home/yuyangruan", ADB, "-s", SERIAL, *args,
        ],
        timeout=timeout,
        check=check,
    )


def _unit_active(unit: str) -> bool:
    result = _run(["/usr/bin/systemctl", "is-active", unit], timeout=15, check=False)
    return result.stdout.strip() in {"active", "activating"}


def _load_status() -> dict:
    with urllib.request.urlopen(STATUS_URL, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("invalid load-balancer status payload")
    return payload


def _set_draining(lane: Lane, draining: bool) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:49350/admin/backend/{lane.name}",
        data=json.dumps({"draining": draining}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"backend drain HTTP {response.status}")


def _wait_lane_health(lane: Lane) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{lane.port}/health", timeout=4
            ) as response:
                payload = json.load(response)
            if response.status == 200 and payload.get("ok") is True:
                return
        except Exception:  # noqa: BLE001 - expected during cold start
            pass
        time.sleep(2)
    raise RuntimeError(f"THS lane did not become healthy: {lane.name}")


def _save_desired(active_names: set[str]) -> None:
    STATE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"active": sorted(active_names), "updated_at": time.time()},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(STATE_FILE)


def wake_lane(lane: Lane) -> None:
    LOGGER.warning("Waking THS lane=%s user=%s", lane.name, lane.user_id)
    _run(["/usr/bin/systemctl", "start", lane.unit], timeout=360)
    _wait_lane_health(lane)
    _set_draining(lane, False)


def sleep_lane(lane: Lane) -> None:
    if lane.minimum:
        return
    LOGGER.warning("Sleeping THS lane=%s user=%s", lane.name, lane.user_id)
    _set_draining(lane, True)
    time.sleep(3)
    _run(["/usr/bin/systemctl", "stop", lane.unit], timeout=120)
    _adb("shell", "am", "force-stop", "--user", str(lane.user_id), "com.hexin.plat.android")
    _adb("shell", "am", "stop-user", "-f", str(lane.user_id), timeout=90)


def reconcile_once(last_busy: dict[str, float], now: float, last_scale_out: float) -> float:
    status = _load_status()
    states = {
        str(item.get("name")): item
        for item in status.get("backends", [])
        if isinstance(item, dict)
    }
    active_lanes = {lane.name for lane in LANES if _unit_active(lane.unit)}
    active_lanes.add("owner")
    _save_desired(active_lanes)

    waiters = int(status.get("native_waiters") or 0) + int(status.get("http_waiters") or 0)
    if waiters and now - last_scale_out >= SCALE_OUT_COOLDOWN_SECONDS:
        lane = next((item for item in LANES if item.name not in active_lanes), None)
        if lane is not None:
            wake_lane(lane)
            active_lanes.add(lane.name)
            _save_desired(active_lanes)
            return now

    for lane in reversed(LANES):
        if lane.minimum or lane.name not in active_lanes:
            continue
        state = states.get(lane.name, {})
        busy = any(
            int(state.get(key) or 0) > 0
            for key in ("active", "stream_sessions")
        )
        if busy or not state.get("healthy"):
            last_busy[lane.name] = now
            continue
        idle_since = last_busy.setdefault(lane.name, now)
        if now - idle_since >= IDLE_TIMEOUT_SECONDS:
            sleep_lane(lane)
            active_lanes.discard(lane.name)
            last_busy.pop(lane.name, None)
            _save_desired(active_lanes)
            break
    return last_scale_out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    last_busy: dict[str, float] = {}
    last_scale_out = 0.0
    while True:
        try:
            last_scale_out = reconcile_once(last_busy, time.monotonic(), last_scale_out)
        except Exception:  # noqa: BLE001 - supervisor must survive transient failures
            LOGGER.exception("THS pool reconciliation failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
