#!/usr/bin/env python3
"""One-command build, test, record and replay workflow for the THS Hook."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any


THS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = THS_ROOT.parent
SERVER_ROOT = WORKSPACE_ROOT / "smart-fund-server"
HOOK_PACKAGE = "com.yuyang.thshook"
TARGET_PACKAGE = "com.hexin.plat.android"
DEVICE_PORT = 18900
PROTOCOL = "THSSTREAM/1"
ZYGISK_MODULE_DEX = "/data/adb/modules/thshook_zygisk/dex/classes.dex"


def _run(
    command: list[str],
    *,
    cwd: Path = THS_ROOT,
    timeout: float = 120,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        check=True,
        timeout=timeout,
        capture_output=capture,
    )


def _adb() -> str:
    configured = os.getenv("ADB")
    if configured:
        return configured
    for candidate in ("adb", "adb.exe"):
        if path := shutil.which(candidate):
            return path
    raise RuntimeError("ADB not found; set the ADB environment variable")


def _adb_command(serial: str | None, *args: str) -> list[str]:
    command = [_adb()]
    if serial:
        command.extend(("-s", serial))
    command.extend(args)
    return command


def _adb_su_shell_command(serial: str | None, command: str) -> list[str]:
    """Keep compound root commands intact across Linux and Windows ADB."""

    return _adb_command(serial, "shell", f"su -c {shlex.quote(command)}")


def _select_serial(explicit: str | None) -> str:
    if explicit:
        return explicit
    result = _run([_adb(), "devices"], capture=True)
    devices = [
        line.split("\t", 1)[0]
        for line in result.stdout.splitlines()
        if line.endswith("\tdevice")
    ]
    if len(devices) != 1:
        raise RuntimeError(
            f"expected exactly one connected device, found {devices}; use --serial"
        )
    return devices[0]


def _apksigner() -> str:
    configured = os.getenv("APKSIGNER")
    if configured:
        return configured
    build_tools = Path(os.getenv("ANDROID_HOME", "/home/yuyang/android-sdk")) / "build-tools"
    candidates = sorted(build_tools.glob("*/apksigner"), reverse=True)
    if not candidates:
        raise RuntimeError("apksigner not found; set APKSIGNER or ANDROID_HOME")
    return str(candidates[0])


def _certificate_digest(apk: Path) -> str:
    result = _run(
        [_apksigner(), "verify", "--print-certs", str(apk)],
        capture=True,
    )
    match = re.search(
        r"certificate SHA-256 digest:\s*([0-9a-fA-F]+)",
        result.stdout,
    )
    if not match:
        raise RuntimeError(f"unable to read certificate digest from {apk}")
    return match.group(1).lower()


def build_hook() -> Path:
    _run(["./gradlew", ":app:assembleDebug"], timeout=120)
    apk = THS_ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.exists():
        raise RuntimeError(f"build did not produce {apk}")
    print(apk)
    return apk


def _extract_main_hook_dex(apk: Path, destination: Path) -> None:
    marker = b"Lcom/yuyang/thshook/MainHook;"
    with zipfile.ZipFile(apk) as archive:
        candidates = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"classes\d*\.dex", name)
        )
        for name in candidates:
            content = archive.read(name)
            if marker in content:
                destination.write_bytes(content)
                return
    raise RuntimeError("Hook APK does not contain MainHook DEX")


def _deploy_zygisk_runtime_dex(serial: str, apk: Path) -> bool:
    probe = _run(
        _adb_su_shell_command(
            serial,
            f"if test -f {ZYGISK_MODULE_DEX}; then echo present; else echo absent; fi",
        ),
        capture=True,
    )
    if "present" not in probe.stdout:
        return False
    with tempfile.TemporaryDirectory(prefix="ths-zygisk-dex-") as directory:
        main_hook_dex = Path(directory) / "classes.dex"
        _extract_main_hook_dex(apk, main_hook_dex)
        local_runtime_dex = THS_ROOT / "zygisk/magisk/dex/classes.dex"
        shutil.copyfile(main_hook_dex, local_runtime_dex)
        remote_staging = "/data/local/tmp/thshook-classes.dex"
        _run(_adb_command(serial, "push", str(main_hook_dex), remote_staging))
        _run(
            _adb_su_shell_command(
                serial,
                f"cp {remote_staging} {ZYGISK_MODULE_DEX}",
            )
        )
        _run(
            _adb_su_shell_command(
                serial,
                f"chmod 0644 {ZYGISK_MODULE_DEX}",
            )
        )
    return True


def deploy_device(serial: str | None) -> None:
    run_predeploy()
    selected = _select_serial(serial)
    apk = THS_ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.exists():
        raise RuntimeError("predeploy did not produce the Hook APK")
    path_result = _run(
        _adb_command(selected, "shell", "pm", "path", HOOK_PACKAGE),
        capture=True,
    )
    remote_apk = path_result.stdout.strip().replace("\r", "")
    if not remote_apk.startswith("package:"):
        raise RuntimeError(f"Hook package is not installed: {remote_apk}")
    with tempfile.TemporaryDirectory(prefix="ths-hook-") as directory:
        installed_apk = Path(directory) / "installed-hook.apk"
        _run(
            _adb_command(
                selected,
                "pull",
                remote_apk.removeprefix("package:"),
                str(installed_apk),
            )
        )
        installed_digest = _certificate_digest(installed_apk)
        candidate_digest = _certificate_digest(apk)
        if installed_digest != candidate_digest:
            raise RuntimeError(
                "Hook APK signature mismatch; refusing to uninstall or overwrite. "
                f"installed={installed_digest} candidate={candidate_digest}"
            )
    _run(_adb_command(selected, "install", "-r", str(apk)))
    zygisk_updated = _deploy_zygisk_runtime_dex(selected, apk)
    _run(_adb_command(selected, "shell", "am", "force-stop", TARGET_PACKAGE))
    _run(
        _adb_command(
            selected,
            "shell",
            "monkey",
            "-p",
            TARGET_PACKAGE,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )
    )
    mode = "APK and Zygisk runtime DEX" if zygisk_updated else "APK"
    print(
        f"Hook {mode} updated without uninstalling; LSPosed scope was preserved."
    )


async def _stream_request(
    *,
    host: str,
    port: int,
    route: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, limit=8 * 1024 * 1024),
        timeout=timeout,
    )
    try:
        writer.write(f"{PROTOCOL}\n".encode())
        await writer.drain()
        hello = json.loads(await asyncio.wait_for(reader.readline(), timeout))
        if hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL:
            raise RuntimeError(f"invalid stream handshake: {hello}")
        request_id = uuid.uuid4().hex
        writer.write(
            json.dumps(
                {
                    "op": "request",
                    "request_id": request_id,
                    "route": route,
                    "payload": payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        await writer.drain()
        while True:
            raw = await asyncio.wait_for(reader.readline(), timeout)
            if not raw:
                raise ConnectionError("THS stream closed before response")
            message = json.loads(raw)
            if message.get("request_id") != request_id:
                continue
            if message.get("type") == "error":
                raise RuntimeError(str(message.get("error") or "request failed"))
            if message.get("type") == "response":
                response = message.get("payload")
                if not isinstance(response, dict):
                    raise RuntimeError("response payload must be an object")
                return response
    finally:
        writer.close()
        await writer.wait_closed()


def _read_payload(value: str | None, path: Path | None) -> dict[str, Any]:
    if value and path:
        raise ValueError("use either --payload or --payload-file")
    raw = path.read_text(encoding="utf-8") if path else (value or "{}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def _request_command(args: argparse.Namespace) -> dict[str, Any]:
    payload = _read_payload(args.payload, args.payload_file)
    return asyncio.run(
        _stream_request(
            host=args.host,
            port=args.port,
            route=args.route,
            payload=payload,
            timeout=args.timeout,
        )
    )


def record_fixture(args: argparse.Namespace) -> None:
    payload = _read_payload(args.payload, args.payload_file)
    response = _request_command(args)
    scenario: dict[str, Any] = {"protocol": PROTOCOL, "routes": {}}
    if args.output.exists():
        scenario = json.loads(args.output.read_text(encoding="utf-8"))
    routes = scenario.setdefault("routes", {})
    routes.setdefault(args.route, []).append(
        {"match": payload, "response": response}
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output.resolve())


def wait_device_stream(
    serial: str | None,
    *,
    local_port: int,
    timeout: float,
) -> None:
    selected = _select_serial(serial)
    _run(
        _adb_command(
            selected,
            "forward",
            f"tcp:{local_port}",
            f"tcp:{DEVICE_PORT}",
        )
    )
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1) as sock:
                sock.sendall(f"{PROTOCOL}\n".encode())
                raw = sock.recv(1024)
                if b'"type":"hello"' in raw and PROTOCOL.encode() in raw:
                    print(f"THS stream ready on 127.0.0.1:{local_port}")
                    return
        except OSError as exc:
            last_error = exc
        time.sleep(0.25)
    raise TimeoutError(f"THS stream did not become ready: {last_error}")


def run_tests() -> None:
    python = sys.executable
    _run(
        [
            python,
            "-m",
            "pytest",
            "tests",
            "-q",
        ],
        timeout=120,
    )
    _run(
        [
            python,
            "-m",
            "pytest",
            "tests/unit/test_ths_native_stream.py",
            "tests/unit/test_ths_bridge_routing.py",
            "-q",
        ],
        cwd=SERVER_ROOT,
        timeout=120,
    )


def run_predeploy() -> None:
    """Mandatory local gate before any Hook installation."""

    run_tests()
    build_hook()
    print("THS predeploy gate passed: tests and incremental build succeeded.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("test", help="Run local protocol and routing tests")
    subparsers.add_parser("build", help="Incrementally build the Hook APK")
    subparsers.add_parser(
        "predeploy",
        help="Run the mandatory tests and incremental build deployment gate",
    )

    serve = subparsers.add_parser("serve", help="Run a local fixture simulator")
    serve.add_argument("--scenario", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=49310)

    for name in ("request", "record"):
        command = subparsers.add_parser(name)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=49300)
        command.add_argument("--route", required=True)
        command.add_argument("--payload")
        command.add_argument("--payload-file", type=Path)
        command.add_argument("--timeout", type=float, default=75)
        if name == "record":
            command.add_argument("--output", type=Path, required=True)

    deploy = subparsers.add_parser("deploy-device")
    deploy.add_argument("--serial")

    ready = subparsers.add_parser("wait-device")
    ready.add_argument("--serial")
    ready.add_argument("--local-port", type=int, default=18900)
    ready.add_argument("--timeout", type=float, default=45)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "test":
        run_tests()
    elif args.command == "build":
        build_hook()
    elif args.command == "predeploy":
        run_predeploy()
    elif args.command == "serve":
        from ths_stream_simulator import THSStreamSimulator, _load_scenario

        simulator = THSStreamSimulator(
            _load_scenario(args.scenario), host=args.host, port=args.port
        )
        try:
            asyncio.run(simulator.serve_forever())
        except KeyboardInterrupt:
            return 130
    elif args.command == "request":
        print(json.dumps(_request_command(args), ensure_ascii=False, indent=2))
    elif args.command == "record":
        record_fixture(args)
    elif args.command == "deploy-device":
        deploy_device(args.serial)
    elif args.command == "wait-device":
        wait_device_stream(
            args.serial,
            local_port=args.local_port,
            timeout=args.timeout,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
