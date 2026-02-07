#!/usr/bin/env python3
"""
RPC 日志轮询器 — 通过 ADB forward 增量拉取 Hook 日志并写入文件

用法:
    python3 scripts/log_poller.py

日志输出到: <项目>/logs/qdhook.log（固定文件名，每次覆盖）
"""

import socket
import json
import time
import os
from datetime import datetime
from pathlib import Path

# --- 配置 ---
RPC_HOST = "127.0.0.1"
RPC_PORT = 12345
POLL_INTERVAL = 2  # 秒
BATCH_LIMIT = 500  # 每次最多拉取条数

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def rpc_call(cmd, **kwargs):
    """发送 RPC 命令并返回结果"""
    request = json.dumps({"cmd": cmd, **kwargs}) + "\n"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((RPC_HOST, RPC_PORT))
        sock.sendall(request.encode("utf-8"))
        buf = b""
        while True:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            except socket.timeout:
                break
        sock.close()
        return json.loads(buf.decode("utf-8"))
    except Exception:
        return None


def format_log_line(entry):
    """格式化一条日志"""
    ts = entry.get("timestamp", 0)
    dt = datetime.fromtimestamp(ts / 1000)
    tag = entry.get("tag", "")
    thread = entry.get("thread", "")
    msg = entry.get("message", "")
    return f"[{dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [{thread}] [{tag}] {msg}"


def main():
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "qdhook.log"

    since = 0  # 增量时间戳
    total_lines = 0
    log_file = open(log_path, "w", encoding="utf-8")  # 覆盖写入
    log_file.write(f"# QDHook Log Poller started at {datetime.now()}\n")
    log_file.write(f"# RPC: {RPC_HOST}:{RPC_PORT}\n\n")
    log_file.flush()

    try:
        while True:
            try:
                result = rpc_call("getLogs", limit=BATCH_LIMIT, since=since)
            except Exception as e:
                log_file.write(f"# [POLLER] RPC exception: {e}\n")
                log_file.flush()
                time.sleep(POLL_INTERVAL)
                continue

            if result and result.get("success"):
                entries = result.get("data", [])
                new_max = result.get("maxTimestamp", 0)

                if entries:
                    for entry in entries:
                        line = format_log_line(entry)
                        log_file.write(line + "\n")

                    log_file.flush()
                    total_lines += len(entries)

                    if new_max > since:
                        since = new_max

            elif result is None:
                log_file.write(f"# [POLLER] RPC connection failed\n")
                log_file.flush()
            else:
                err = result.get("error", "unknown")
                log_file.write(f"# [POLLER] RPC error: {err}\n")
                log_file.flush()

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        log_file.write(f"# [POLLER] Fatal: {e}\n")
    finally:
        log_file.write(f"\n# Stopped. Total {total_lines} lines.\n")
        log_file.close()


if __name__ == "__main__":
    main()
