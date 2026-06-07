#!/usr/bin/env python3
"""Smart Selector Web Log Viewer

轻量 HTTP 服务，显示 smart-selector 的测速日志和决策历史。
访问: http://10.168.1.3:9091/
数据接口: http://10.168.1.3:9091/api  (JSON)
"""

import json
import time
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOG_FILE = Path("/tmp/smart-selector/selector.log")
HISTORY_FILE = Path("/tmp/smart-selector/history.json")
PORT = 9091

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Selector</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'SF Mono', 'Menlo', 'Consolas', monospace; background:#0d1117; color:#c9d1d9; padding:20px; }
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; padding-bottom:12px; border-bottom:1px solid #21262d; }
.header h1 { font-size:20px; color:#58a6ff; }
.header .meta { font-size:12px; color:#484f58; }
.status { display:flex; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
.card { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:12px 16px; min-width:120px; }
.card .label { font-size:11px; color:#484f58; text-transform:uppercase; letter-spacing:0.5px; }
.card .value { font-size:18px; color:#58a6ff; margin-top:4px; font-weight:600; }
.card .value.fail { color:#f85149; }
.btn { background:#21262d; border:1px solid #30363d; border-radius:6px; color:#58a6ff; padding:6px 14px; cursor:pointer; font-size:12px; font-family:inherit; }
.btn:hover { background:#30363d; }
.btn:active { background:#484f58; }
.log-container { background:#161b22; border:1px solid #21262d; border-radius:8px; overflow:hidden; }
.log-header { padding:10px 16px; border-bottom:1px solid #21262d; font-size:13px; color:#484f58; display:flex; justify-content:space-between; }
.log-body { padding:12px 16px; max-height:70vh; overflow-y:auto; font-size:13px; line-height:1.8; }
.log-line { white-space:pre-wrap; word-break:break-all; }
.round { color:#3fb950; font-weight:bold; }
.decision { color:#d29922; font-weight:bold; }
.switch { color:#f85149; font-weight:bold; }
.best { color:#3fb950; }
.keeping { color:#8b949e; }
.fail { color:#484f58; }
.unstable { color:#d29922; }
.blink { animation: blink 1s ease-in-out 3; }
@keyframes blink { 50% { opacity:0.3; } }
</style>
</head>
<body>
<div class="header">
  <h1>Smart Selector</h1>
  <div class="meta"><span id="meta">loading...</span> <button class="btn" id="btn" onclick="doRefresh()">Refresh</button></div>
</div>
<div class="status" id="status"></div>
<div class="log-container">
  <div class="log-header">
    <span>Log</span>
    <span id="log_size">-</span>
  </div>
  <div class="log-body" id="log">loading...</div>
</div>
<script>
function hl(line) {
  let h = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  let cls = 'log-line';
  if (h.includes('=== Round')) cls += ' round';
  else if (h.includes('--- Decision')) cls += ' decision';
  else if (h.includes('Switch:') || h.includes('emergency switch')) cls += ' switch';
  else if (h.includes('Already on best')) cls += ' best';
  else if (h.includes('Keeping')) cls += ' keeping';
  else if (h.includes('[unstable]')) cls += ' unstable';
  else if (h.includes('FAIL')) cls += ' fail';
  return '<div class="' + cls + '">' + h + '</div>';
}

let lastLogLen = 0;
function refresh(onDone) {
  fetch('/api').then(r => r.json()).then(d => {
    document.getElementById('meta').textContent = 'auto-refresh 10s | ' + d.time;
    document.getElementById('status').innerHTML = d.cards;
    document.getElementById('log_size').textContent = d.log_size;

    let el = document.getElementById('log');
    if (d.log_count !== lastLogLen) {
      el.innerHTML = d.log_lines.map(hl).join('\\n');
      lastLogLen = d.log_count;
      el.classList.add('blink');
      setTimeout(() => el.classList.remove('blink'), 3000);
    }
    if (onDone) onDone();
  }).catch(() => { if (onDone) onDone(); });
}
function doRefresh() {
  let btn = document.getElementById('btn');
  btn.textContent = '...';
  btn.disabled = true;
  refresh(function() { btn.textContent = 'Refresh'; btn.disabled = false; });
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


def build_cards():
    cards = ""
    current_node = None

    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:9090/proxies/%E8%83%BD%E7%94%A8%E5%B0%B1%E8%A1%8C",
            headers={"Authorization": "Bearer k1FuD3ty"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            g = json.loads(resp.read())
        current_node = g.get("now", "?")
        cards += f'<div class="card"><div class="label">Current Node</div><div class="value" style="font-size:14px">{html.escape(current_node)}</div></div>'
    except Exception:
        cards += '<div class="card"><div class="label">Current Node</div><div class="value">?</div></div>'

    if HISTORY_FILE.exists() and current_node:
        try:
            h = json.loads(HISTORY_FILE.read_text())
            delays = h.get("results", {}).get(current_node, [])
            if delays:
                avg = sum(delays) / len(delays)
                std = (sum((d - avg) ** 2 for d in delays) / len(delays)) ** 0.5 if len(delays) >= 2 else 0
                if avg >= 9999:
                    cards += '<div class="card"><div class="label">Avg Delay</div><div class="value fail">FAIL</div></div>'
                else:
                    cards += f'<div class="card"><div class="label">Avg Delay</div><div class="value">{avg:.0f}ms</div></div>'
                    cards += f'<div class="card"><div class="label">Std Dev</div><div class="value">{std:.0f}ms</div></div>'
                last = delays[-1]
                last_str = f"{last}ms" if last < 9999 else "FAIL"
                last_cls = " fail" if last >= 9999 else ""
                cards += f'<div class="card"><div class="label">Last</div><div class="value{last_cls}">{last_str}</div></div>'
            else:
                cards += '<div class="card"><div class="label">Avg Delay</div><div class="value">-</div></div>'
        except Exception:
            pass

    if HISTORY_FILE.exists():
        try:
            h = json.loads(HISTORY_FILE.read_text())
            rnd = h.get("round", 0)
            n_nodes = len(h.get("results", {}))
            cards += f'<div class="card"><div class="label">Round</div><div class="value">{rnd}</div></div>'
            cards += f'<div class="card"><div class="label">Nodes</div><div class="value">{n_nodes}</div></div>'
        except Exception:
            pass

    return cards


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            self._serve_api()
        else:
            self._serve_page()

    def _serve_page(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def _serve_api(self):
        log_text = LOG_FILE.read_text() if LOG_FILE.exists() else ""
        log_lines = log_text.strip().splitlines() if log_text.strip() else []
        recent = log_lines[-200:] if log_lines else []

        data = {
            "time": time.strftime("%H:%M:%S"),
            "cards": build_cards(),
            "log_lines": list(reversed(recent)),
            "log_count": len(log_lines),
            "log_size": f"{len(log_lines)} lines",
        }
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Smart Selector Log Viewer running on port {PORT}")
    server.serve_forever()
