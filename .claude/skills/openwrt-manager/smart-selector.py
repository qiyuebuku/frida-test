#!/usr/bin/env python3
"""Smart Node Selector for mihomo (OpenClash)

每分钟测速一轮，3轮后决策：
1. 计算每个节点3轮平均延迟
2. 过滤不稳定节点（标准差 > 均值50%）
3. 新节点必须比当前节点平均延迟低20%才切换

使用 asyncio 原生 TCP 协程并发测速，零外部依赖。
"""

import asyncio
import json
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from math import sqrt

API_HOST = "127.0.0.1"
API_PORT = 9090
API_KEY = "k1FuD3ty"
GROUP = "能用就行"
TEST_URL = "http://www.gstatic.com/generate_204"
TIMEOUT_MS = 5000

DATA_DIR = Path("/tmp/smart-selector")
HISTORY_FILE = DATA_DIR / "history.json"
LOG_FILE = DATA_DIR / "selector.log"

WINDOW_SIZE = 3
IMPROVE_THRESHOLD = 0.20
STABILITY_THRESHOLD = 0.50
MAX_CONCURRENT = 20
# auto-select blacklist (matched by region prefix)
BLACKLIST = ["香港", "马来西亚", "俄罗斯"]


# ── async HTTP client (raw TCP, zero deps) ──

def _decode_chunked(data):
    result = b""
    while data:
        end = data.find(b"\r\n")
        if end == -1:
            break
        size_str = data[:end].decode().split(";")[0].strip()
        if not size_str:
            data = data[2:]
            continue
        size = int(size_str, 16)
        if size == 0:
            break
        data = data[end + 2:]
        result += data[:size]
        data = data[size + 2:]
    return result


async def _http(method, path, body=None, timeout=10):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(API_HOST, API_PORT),
        timeout=timeout,
    )
    headers = (
        f"{method} {path} HTTP/1.1\r\n"
        f"Host: {API_HOST}:{API_PORT}\r\n"
        f"Authorization: Bearer {API_KEY}\r\n"
    )
    if body is not None:
        payload = json.dumps(body).encode()
        headers += (
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
        )
    headers += "Connection: close\r\n\r\n"
    writer.write(headers.encode())
    if body is not None:
        writer.write(payload)
    await writer.drain()

    data = b""
    while True:
        chunk = await reader.read(8192)
        if not chunk:
            break
        data += chunk
    writer.close()

    sep = data.find(b"\r\n\r\n")
    if sep == -1:
        return {}
    resp_headers = data[:sep].decode().lower()
    resp_body = data[sep + 4:]
    if "transfer-encoding: chunked" in resp_headers:
        resp_body = _decode_chunked(resp_body)
    if not resp_body:
        return {}
    return json.loads(resp_body)


async def api_get(path):
    return await _http("GET", path)


async def api_put(path, data):
    return await _http("PUT", path, body=data)


# ── core logic ──

async def get_group_info():
    encoded = urllib.parse.quote(GROUP, safe="")
    return await api_get(f"/proxies/{encoded}")


async def test_node(sem, node_name):
    async with sem:
        encoded = urllib.parse.quote(node_name, safe="")
        path = (
            f"/proxies/{encoded}/delay"
            f"?timeout={TIMEOUT_MS}"
            f"&url={urllib.parse.quote(TEST_URL, safe='')}"
        )
        try:
            result = await asyncio.wait_for(api_get(path), timeout=TIMEOUT_MS / 1000 + 5)
            delay = result.get("delay", 0)
            return node_name, delay if delay > 0 else None
        except Exception:
            return node_name, None


async def test_all_nodes(nodes):
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [test_node(sem, n) for n in nodes]
    results = await asyncio.gather(*tasks)
    return dict(results)


async def switch_node(node_name):
    encoded = urllib.parse.quote(GROUP, safe="")
    await api_put(f"/proxies/{encoded}", {"name": node_name})


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"round": 0, "results": {}}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False)


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    # also write to syslog (visible in LuCI System Log)
    try:
        subprocess.Popen(
            ["logger", "-t", "smart-selector", msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    # keep log under 200KB
    try:
        if LOG_FILE.stat().st_size > 200 * 1024:
            lines = LOG_FILE.read_text().splitlines()
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n")
    except Exception:
        pass


def calc_avg(delays):
    return sum(delays) / len(delays) if delays else None


def calc_std(delays):
    if len(delays) < 2:
        return 0
    avg = sum(delays) / len(delays)
    return sqrt(sum((d - avg) ** 2 for d in delays) / len(delays))


async def run():
    DATA_DIR.mkdir(exist_ok=True)

    try:
        group = await get_group_info()
    except Exception as e:
        log(f"mihomo API unreachable: {e}")
        return

    current_node = group.get("now", "")
    all_nodes = group.get("all", [])
    if not all_nodes:
        log("No nodes found in group")
        return

    history = load_history()
    round_num = history.get("round", 0) + 1
    results = history.get("results", {})

    log(f"=== Round {round_num}, current: {current_node}, window: {WINDOW_SIZE} ===")

    # concurrent test
    t0 = time.monotonic()
    delays_map = await test_all_nodes(all_nodes)
    elapsed = time.monotonic() - t0

    # append new results, keep only last WINDOW_SIZE per node
    for node, delay in delays_map.items():
        if delay is None or delay == 0:
            delay = 9999
        buf = results.setdefault(node, [])
        buf.append(delay)
        if len(buf) > WINDOW_SIZE:
            del buf[0]
        log(f"  {node}: {delay}ms" if delay < 9999 else f"  {node}: FAIL")

    # show current node's running average
    cur_delays = results.get(current_node, [])
    if cur_delays:
        cur_avg = calc_avg(cur_delays)
        cur_std = calc_std(cur_delays)
        cur_str = f"{cur_avg:.0f}ms" if cur_avg < 9999 else "FAIL"
        log(f"  Done in {elapsed:.1f}s | current({current_node}) avg={cur_str}, std={cur_std:.0f}ms [{len(cur_delays)}/{WINDOW_SIZE}]")

    # decision every round using sliding window
    cur_avg_val = calc_avg(cur_delays) if cur_delays else 9999
    cur_failed = cur_avg_val is None or cur_avg_val >= 9999
    cur_blacklisted = any(bl in current_node for bl in BLACKLIST)

    if cur_failed or cur_blacklisted or len(cur_delays) >= 2:
        log("--- Decision ---")
        candidates = {}
        for node, delays in results.items():
            avg = calc_avg(delays)
            if avg is None or avg >= 9999:
                continue
            if any(bl in node for bl in BLACKLIST):
                log(f"  {node}: avg={avg:.0f}ms, std=0ms [blacklist]")
                continue
            # skip unstable nodes (but not when current is failed - any working node is fine)
            if not cur_failed and len(delays) >= 2:
                std = calc_std(delays)
                if std > avg * STABILITY_THRESHOLD:
                    log(f"  {node}: avg={avg:.0f}ms, std={std:.0f}ms [unstable]")
                    continue
            candidates[node] = avg

        if not candidates:
            log("No stable candidates, keeping current")
        else:
            best_node = min(candidates, key=candidates.get)
            best_avg = candidates[best_node]
            current_avg = candidates.get(current_node)

            if cur_failed:
                log(f"Current node FAILED, emergency switch to {best_node} ({best_avg:.0f}ms)")
                try:
                    await switch_node(best_node)
                except Exception as e:
                    log(f"Switch failed: {e}")
            elif cur_blacklisted:
                log(f"Current node FAILED, emergency switch to {best_node} ({best_avg:.0f}ms)")
                try:
                    await switch_node(best_node)
                except Exception as e:
                    log(f"Switch failed: {e}")
            elif best_node == current_node:
                log(f"Already on best: {best_node} ({best_avg:.0f}ms)")
            else:
                improvement = (current_avg - best_avg) / current_avg
                if improvement >= IMPROVE_THRESHOLD:
                    log(f"Switch: {current_node}({current_avg:.0f}ms) -> {best_node}({best_avg:.0f}ms) [{improvement:.1%}]")
                    try:
                        await switch_node(best_node)
                    except Exception as e:
                        log(f"Switch failed: {e}")
                else:
                    log(f"Keeping {current_node}({current_avg:.0f}ms), gain={improvement:.1%} < {IMPROVE_THRESHOLD:.0%}")

    history["round"] = round_num
    history["results"] = results
    save_history(history)


if __name__ == "__main__":
    asyncio.run(run())
