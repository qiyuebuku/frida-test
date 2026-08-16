#!/usr/bin/env python3
"""Loopback proxy that recovers the THS App process after native channel timeouts."""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import subprocess
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOGGER = logging.getLogger("ths-native-proxy")


class AndroidNativeBridge:
    UPSTREAM_TIMEOUT_SECONDS = 75
    ADB_COMMAND_TIMEOUT_SECONDS = 60
    RANKING_CALLBACK_COOLDOWN_SECONDS = 0.9

    def __init__(
        self,
        *,
        adb: str,
        serial: str,
        package: str,
        android_user_id: int,
        activate_foreground_user: bool,
        return_foreground_user: int | None,
        upstream_port: int,
        device_port: int,
        automatic_recovery: bool,
        gateway_managed: bool,
    ) -> None:
        self._adb = adb
        self._serial = serial
        self._package = package
        self._android_user_id = android_user_id
        self._activate_foreground_user = activate_foreground_user
        self._return_foreground_user = return_foreground_user
        self._upstream_port = upstream_port
        self._device_port = device_port
        self._automatic_recovery = automatic_recovery
        self._gateway_managed = gateway_managed
        self._native_request_lock = threading.Lock()
        self._recovery_lock = threading.Lock()
        self._generation = 0

    def forward(self, method: str, path: str, body: bytes) -> tuple[int, bytes, str]:
        managed_request = (
            method == "POST"
            and (path.startswith("/native/") or path == "/jsbridge")
        )
        if not managed_request:
            return self._request_upstream(method, path, body)

        # The App exposes several Java request families, but they ultimately
        # share one native transport and process lifecycle. Production overlap
        # tests showed that cross-family concurrency can terminate the App even
        # when frame IDs differ. Keep the safety boundary in the proxy so every
        # caller, not only THSClient, observes the same single-flight contract.
        request_guard = (
            nullcontext()
            if getattr(self, "_gateway_managed", False)
            else self._native_request_lock
        )
        with request_guard:
            generation = self._generation
            try:
                status, payload, content_type = self._request_upstream(
                    method,
                    path,
                    body,
                )
            except (OSError, http.client.HTTPException) as exc:
                LOGGER.warning("Native channel transport failed: %s", exc)
                if not getattr(self, "_automatic_recovery", True):
                    raise
                if not self._recover_if_needed(generation):
                    raise
                return self._request_upstream(
                    method,
                    path,
                    body,
                )
            if not self._is_connection_timeout(status, payload):
                if path.startswith("/native/ranking-debug"):
                    # Ranking's builder reuses the fixed 2312 frame route. The
                    # callback is fast, but the App unregisters it
                    # asynchronously; releasing the family slot immediately
                    # makes the next request intermittently lose its callback.
                    time.sleep(self.RANKING_CALLBACK_COOLDOWN_SECONDS)
                return status, payload, content_type

            if self._is_callback_timeout(status, payload):
                # A missing one-shot callback does not prove that the App
                # transport is dead. Return it for a bounded client retry;
                # restarting here adds 20+ seconds and interrupts every
                # healthy subscription in the process.
                time.sleep(self.RANKING_CALLBACK_COOLDOWN_SECONDS)
                return status, payload, content_type

            if not getattr(self, "_automatic_recovery", True):
                return status, payload, content_type
            if not self._recover_if_needed(generation):
                return status, payload, content_type
            recovered = self._request_upstream(
                method,
                path,
                body,
            )
            if path.startswith("/native/ranking-debug"):
                time.sleep(self.RANKING_CALLBACK_COOLDOWN_SECONDS)
            return recovered

    def recover(self) -> tuple[int, bytes, str]:
        # Explicit recovery is coordinated by the gateway. It is allowed to
        # terminate the affected persistent stream; the stream client will
        # reconnect after the App process becomes healthy again.
        recovered = self._recover_if_needed(
            self._generation,
            ignore_stream_sessions=True,
        )
        payload = json.dumps(
            {"success": recovered, "generation": self._generation},
            ensure_ascii=False,
        ).encode("utf-8")
        return (200 if recovered else 409, payload, "application/json")

    def _request_upstream(
        self,
        method: str,
        path: str,
        body: bytes,
    ) -> tuple[int, bytes, str]:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self._upstream_port,
            timeout=self.UPSTREAM_TIMEOUT_SECONDS,
        )
        try:
            headers = {"Connection": "close"}
            if body:
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            connection.request(method, path, body=body or None, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return (
                response.status,
                payload,
                response.getheader("Content-Type", "application/json"),
            )
        finally:
            connection.close()

    @staticmethod
    def _is_connection_timeout(status: int, payload: bytes) -> bool:
        if status >= 500:
            return True
        try:
            value = json.loads(payload)
            for _ in range(3):
                if not isinstance(value, str):
                    break
                value = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(value, dict):
            return False
        data = value.get("data") or {}
        if isinstance(data, dict) and str(data.get("errorNo")) == "-1000":
            return True
        if value.get("success") is not False:
            return False
        response = value.get("response") or {}
        head = response.get("head") or {}
        if head.get("errorCode") == -131:
            return True
        error = str(value.get("error") or "").lower()
        return (
            "timed out" in error
            or "timeout" in error
            or "no webview available" in error
        )

    @staticmethod
    def _is_callback_timeout(status: int, payload: bytes) -> bool:
        if status >= 500:
            return False
        try:
            value = json.loads(payload)
            for _ in range(3):
                if not isinstance(value, str):
                    break
                value = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(value, dict) or value.get("success") is not False:
            return False
        error = str(value.get("error") or "").lower()
        return "timed out" in error or "timeout" in error

    def _recover_if_needed(
        self,
        failed_generation: int,
        *,
        ignore_stream_sessions: bool = False,
    ) -> bool:
        with self._recovery_lock:
            if self._generation != failed_generation:
                return True
            stream_sessions = self._realtime_stream_session_count()
            if stream_sessions > 0 and not ignore_stream_sessions:
                LOGGER.warning(
                    "Native channel timed out; skipping App restart because %s "
                    "realtime stream session(s) are active",
                    stream_sessions,
                )
                return False
            LOGGER.warning(
                "Native channel timed out; restarting %s user=%s",
                self._package,
                self._android_user_id,
            )
            self._prepare_android_user()
            self._adb_run(
                "shell",
                "am",
                "force-stop",
                "--user",
                str(self._android_user_id),
                self._package,
                check=False,
            )
            time.sleep(0.5)
            self._adb_run(
                "shell",
                "am",
                "start",
                "--user",
                str(self._android_user_id),
                "-n",
                f"{self._package}/.Hexin",
            )
            try:
                self._wait_until_healthy()
                self._open_market_page()
            finally:
                self._restore_android_user()
            self._generation += 1
            LOGGER.info("Native channel recovery completed generation=%s", self._generation)
            return True

    def _realtime_stream_session_count(self) -> int:
        try:
            status, payload, _ = self._request_upstream("GET", "/health", b"")
            if status != 200:
                return 0
            health = json.loads(payload)
            return max(0, int(health.get("realtime_stream_sessions") or 0))
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            http.client.HTTPException,
        ) as exc:
            LOGGER.warning("Unable to inspect realtime stream health: %s", exc)
            return 0

    def _prepare_android_user(self) -> None:
        if self._android_user_id != 0:
            self._adb_run(
                "shell",
                "am",
                "start-user",
                "-w",
                str(self._android_user_id),
            )
        if not self._activate_foreground_user:
            return
        self._adb_run(
            "shell",
            "am",
            "switch-user",
            str(self._android_user_id),
        )
        self._wait_for_current_user(self._android_user_id)

    def _restore_android_user(self) -> None:
        if self._return_foreground_user is None:
            return
        self._adb_run(
            "shell",
            "am",
            "switch-user",
            str(self._return_foreground_user),
            check=False,
        )
        self._wait_for_current_user(self._return_foreground_user)

    def _wait_for_current_user(self, user_id: int) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    self._adb,
                    "-s",
                    self._serial,
                    "shell",
                    "am",
                    "get-current-user",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.ADB_COMMAND_TIMEOUT_SECONDS,
            )
            if result.stdout.strip() == str(user_id):
                return
            time.sleep(1)
        raise RuntimeError(f"Android user {user_id} did not become foreground")

    def _open_market_page(self) -> None:
        # /health only proves that the Hook is listening. Ranking protocol 1208
        # is registered by the market WebView, so recovery must restore that UI
        # state before replaying the request.
        self._adb_run(
            "shell",
            "am",
            "start",
            "--user",
            str(self._android_user_id),
            "-n",
            f"{self._package}/.Hexin",
            check=False,
        )
        time.sleep(8)
        self._adb_run(
            "shell",
            "input",
            "tap",
            "245",
            "2090",
            check=False,
        )
        time.sleep(12)

    def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._adb_run(
                    "forward",
                    f"tcp:{self._upstream_port}",
                    f"tcp:{self._device_port}",
                )
                status, payload, _ = self._request_upstream("GET", "/health", b"")
                health = json.loads(payload)
                if (
                    status == 200
                    and health.get("ok") is True
                    and health.get("android_user_id") == self._android_user_id
                ):
                    return
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
                http.client.HTTPException,
                subprocess.SubprocessError,
            ) as exc:
                last_error = exc
            time.sleep(1)
        raise RuntimeError(f"THS Hook did not recover: {last_error}")

    def _adb_run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._adb, "-s", self._serial, *args],
            check=check,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.ADB_COMMAND_TIMEOUT_SECONDS,
        )


class NativeProxyHandler(BaseHTTPRequestHandler):
    bridge: AndroidNativeBridge

    def do_GET(self) -> None:  # noqa: N802
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/admin/recover":
            try:
                status, payload, content_type = self.bridge.recover()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:  # noqa: BLE001 - administrative boundary
                LOGGER.exception("Explicit bridge recovery failed")
                self.send_error(500, str(exc))
            return
        self._proxy("POST")

    def _proxy(self, method: str) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 2 * 1024 * 1024:
                self.send_error(413, "request body too large")
                return
            body = self.rfile.read(content_length) if content_length else b""
            status, payload, content_type = self.bridge.forward(method, self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001 - proxy boundary must return a response
            LOGGER.exception("Bridge request failed method=%s path=%s", method, self.path)
            payload = json.dumps(
                {"success": False, "error": f"bridge recovery failed: {exc}"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=49301)
    parser.add_argument("--upstream-port", type=int, default=49300)
    parser.add_argument("--device-port", type=int, default=18900)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--package", default="com.hexin.plat.android")
    parser.add_argument("--android-user-id", type=int, default=0)
    parser.add_argument("--activate-foreground-user", action="store_true")
    parser.add_argument("--return-foreground-user", type=int)
    parser.add_argument("--disable-automatic-recovery", action="store_true")
    parser.add_argument("--gateway-managed", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    NativeProxyHandler.bridge = AndroidNativeBridge(
        adb=args.adb,
        serial=args.serial,
        package=args.package,
        android_user_id=args.android_user_id,
        activate_foreground_user=args.activate_foreground_user,
        return_foreground_user=args.return_foreground_user,
        upstream_port=args.upstream_port,
        device_port=args.device_port,
        automatic_recovery=not args.disable_automatic_recovery,
        gateway_managed=args.gateway_managed,
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.listen_port), NativeProxyHandler)
    LOGGER.info(
        "THS native proxy listening on 127.0.0.1:%s upstream=127.0.0.1:%s user=%s",
        args.listen_port,
        args.upstream_port,
        args.android_user_id,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
