"""终端镜像：服务端累积 ANSI → HTML，通过 WebSocket 实时推送，持久化到数据库"""

import asyncio
import logging
import re
import subprocess
import time
import zlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from services.db import task_db

router = APIRouter()
logger = logging.getLogger(__name__)

SESSION_PREFIX = "sa_claude_"

# 正在展开折叠块的任务集合，推送期间跳过避免闪现 thinking 内容
_expanding_tasks: set[int] = set()

# 已尝试恢复会话的任务集合（全局，防止多个 WS 连接重复触发）
_resume_attempted_tasks: set[int] = set()


def _find_tmux_session(task_id: int) -> str | None:
    for suffix in ["", "_sonnet", "_haiku", "_opus"]:
        name = f"{SESSION_PREFIX}{task_id}{suffix}"
        try:
            r = subprocess.run(
                ["tmux", "has-session", "-t", name],
                capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return name
        except Exception:
            pass
    return None


def _capture_pane(session_name: str, ansi: bool = True) -> str:
    """捕获 tmux 全部 scrollback（-S - 从头开始）"""
    try:
        cmd = ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"]
        if ansi:
            cmd.insert(-2, "-e")  # -e 输出 ANSI escape
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_lines(raw: str) -> list[str]:
    """去掉首尾空行，返回行列表"""
    lines = raw.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _detect_screen_clear(old_lines: list[str], new_lines: list[str]) -> bool:
    """检测是否发生了屏幕清除：新内容大幅缩短且首行不同

    排除 TUI 重新渲染的情况：如果新内容包含大量旧内容行，说明是重绘而非清屏。
    """
    if not old_lines:
        return False
    if len(new_lines) < len(old_lines) * 0.5 and len(old_lines) > 10:
        if new_lines and old_lines and new_lines[0] != old_lines[0]:
            old_set = set(l.strip() for l in old_lines if l.strip())
            new_set = set(l.strip() for l in new_lines if l.strip())
            if old_set:
                overlap = len(old_set & new_set) / len(old_set)
                if overlap > 0.3:
                    return False
            return True
    return False


# ANSI 256色 → CSS 颜色
_ANSI_256 = {
    174: "#d68787", 246: "#949494", 81: "#5fd7ff", 215: "#ffaf5f",
    243: "#767676", 250: "#bcbcbc", 245: "#8a8a8a", 244: "#808080",
    178: "#d7af00", 214: "#ffaf00", 208: "#ff8700", 203: "#ff5f5f",
    71: "#5faf5f", 108: "#87af87", 109: "#87afaf", 110: "#87afd7",
    139: "#af87af", 140: "#af87d7", 141: "#af87ff", 167: "#d75f5f",
    168: "#d75f87", 173: "#d7875f", 175: "#d787af", 176: "#d787d7",
    180: "#d7af87", 182: "#d7afd7", 183: "#d7afff", 186: "#d7d787",
    187: "#d7d7af", 188: "#d7d7d7", 189: "#d7d7ff",
}

_ANSI_BASIC_FG = {
    30: "#1a1a1a", 31: "#e05252", 32: "#4ec86c", 33: "#d4a54e",
    34: "#5b9cf5", 35: "#c678dd", 36: "#56b6c2", 37: "#d4d4d4",
    90: "#5c6370", 91: "#e06c75", 92: "#98c379", 93: "#e5c07b",
    94: "#61afef", 95: "#c678dd", 96: "#56b6c2", 97: "#ffffff",
}

# 匹配所有常见 ANSI 转义序列
_ANSI_RE = re.compile(r'''
    \x1b\[[0-9;]*[a-zA-Z]  |
    \x1b\][^\x07]*\x07      |
    \x1b\][^\x1b]*\x1b\\    |
    \x1b[()][0-9A-Za-z]     |
    \x1b[#=>\[\]^_]         |
    \x1b[A-Za-z]
''', re.VERBOSE)

def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列，返回纯文本"""
    return _ANSI_RE.sub('', text)


def _ansi_to_html(text: str) -> str:
    """将 ANSI 转义序列转换为内联 style 的 HTML span"""
    result = []
    open_spans = 0
    i = 0
    n = len(text)

    while i < n:
        if text[i] == '\x1b' and i + 1 < n and text[i + 1] == '[':
            j = i + 2
            while j < n and not text[j].isalpha() and j - i < 30:
                j += 1

            if j < n:
                cmd = text[j]
                param_str = text[i + 2:j]

                if cmd == 'm':
                    while open_spans > 0:
                        result.append('</span>')
                        open_spans -= 1

                    if param_str and param_str != '0':
                        codes = param_str.split(';')
                        styles = []
                        k = 0
                        while k < len(codes):
                            try:
                                c = int(codes[k])
                            except ValueError:
                                k += 1
                                continue

                            if c == 1:
                                styles.append('font-weight:bold')
                            elif c == 2:
                                styles.append('opacity:0.6')
                            elif c == 3:
                                styles.append('font-style:italic')
                            elif c == 4:
                                styles.append('text-decoration:underline')
                            elif c in _ANSI_BASIC_FG:
                                styles.append(f'color:{_ANSI_BASIC_FG[c]}')
                            elif c == 38 and k + 1 < len(codes) and codes[k + 1] == '5':
                                if k + 2 < len(codes):
                                    try:
                                        ci = int(codes[k + 2])
                                        color = _ANSI_256.get(ci)
                                        if not color:
                                            if ci < 8:
                                                color = _ANSI_BASIC_FG.get([30,31,32,33,34,35,36,37][ci])
                                            elif ci < 16:
                                                color = _ANSI_BASIC_FG.get([90,91,92,93,94,95,96,97][ci-8])
                                            elif ci < 232:
                                                idx = ci - 16
                                                r, g, b = (idx//36)*51, ((idx//6)%6)*51, (idx%6)*51
                                                color = f'#{r:02x}{g:02x}{b:02x}'
                                            else:
                                                gray = 8 + (ci - 232) * 10
                                                color = f'#{gray:02x}{gray:02x}{gray:02x}'
                                        if color:
                                            styles.append(f'color:{color}')
                                    except (ValueError, IndexError):
                                        pass
                                k += 2
                            k += 1

                        if styles:
                            result.append(f'<span style="{";".join(styles)}">')
                            open_spans += 1
                i = j + 1
            else:
                i = j
        elif text[i] == '<':
            result.append('&lt;')
            i += 1
        elif text[i] == '>':
            result.append('&gt;')
            i += 1
        elif text[i] == '&':
            result.append('&amp;')
            i += 1
        elif text[i] == '\r':
            i += 1
        else:
            result.append(text[i])
            i += 1

    while open_spans > 0:
        result.append('</span>')
        open_spans -= 1

    return ''.join(result)


# ==================== WebSocket 推送 ====================

async def _ws_loop(task_id: int, ws: WebSocket):
    """WebSocket 推送循环：行级增量，zlib 压缩二进制帧

    协议（二进制帧，zlib 压缩后发送）：
      F<html>          — 全量替换
      D<start>-<end>⏎<html>  — 替换 lines[start:end]，保留 end 之后的行
    """
    no_session_count = 0
    saved_prefix: list[str] = []
    last_scrollback: list[str] = []
    last_db_save = time.time()
    has_db_history = False
    waiting_sent = False
    _cached_session_id = None
    last_change_time = time.time()
    last_raw_snapshot = ""
    last_html_lines: list[str] = []

    db_history_lines: list[str] = []

    try:
        existing = task_db.get_terminal_log(task_id)
        if existing:
            db_history_lines = existing.split('\n')
            has_db_history = True
    except Exception as e:
        logger.warning(f"加载终端历史失败: {e}")

    try:
        while True:
            session_name = _find_tmux_session(task_id)
            if not session_name:
                no_session_count += 1
                if not waiting_sent:
                    if has_db_history:
                        hist_html = '\n'.join(_ansi_to_html(l) for l in db_history_lines)
                        await _ws_send(ws, f"F{hist_html}")
                    else:
                        await _ws_send(ws, 'F<span style="color:#d4a54e">等待终端启动...</span>')
                    waiting_sent = True

                # 首次找不到 tmux 时检查任务状态
                if no_session_count == 1:
                    task = task_db.get_task(task_id)
                    task_status = task.get("status", "") if task else ""
                    _cached_session_id = task.get("session_id") if task else None
                    # 已完成/失败的任务：显示 DB 历史，不自动恢复
                    if task_status not in ("processing", "pending"):
                        if not has_db_history:
                            if _cached_session_id:
                                await _ws_send(ws, 'F<span style="color:#8b8b8b">会话已结束，发送消息可恢复对话</span>')
                            else:
                                await _ws_send(ws, 'F<span style="color:#8b8b8b">终端日志不可用（会话已结束）</span>')
                        break

                # 仅 processing/pending 且有 session_id 但无 tmux，3 秒后自动 resume（全局仅尝试一次）
                if no_session_count == 3 and _cached_session_id and task_id not in _resume_attempted_tasks:
                    _resume_attempted_tasks.add(task_id)
                    # 在 DB 历史末尾追加恢复提示，不覆盖历史内容
                    if has_db_history:
                        resume_hint = '\n<span style="color:#d4a54e">正在恢复会话...</span>'
                        hist_html = '\n'.join(_ansi_to_html(l) for l in db_history_lines)
                        await _ws_send(ws, f"F{hist_html}{resume_hint}")
                    else:
                        await _ws_send(ws, 'F<span style="color:#d4a54e">正在恢复会话...</span>')

                    # 后台执行恢复，不阻塞 WS 推送循环
                    async def _do_resume(_tid=task_id, _sid=_cached_session_id):
                        try:
                            from services.task_executor import executor as _executor
                            await asyncio.to_thread(
                                _executor._get_or_create_session,
                                _tid, resume_session_id=_sid
                            )
                            logger.info(f"[terminal] 自动恢复会话成功 task_id={_tid}")
                        except Exception as e:
                            logger.warning(f"[terminal] 自动恢复会话失败 task_id={_tid}: {e}")

                    asyncio.create_task(_do_resume())

                await asyncio.sleep(3 if no_session_count > 10 else 1)
                continue

            no_session_count = 0

            if task_id in _expanding_tasks:
                await asyncio.sleep(0.5)
                continue

            raw_ansi = await asyncio.to_thread(_capture_pane, session_name, True)
            if not raw_ansi.strip():
                await asyncio.sleep(0.5)
                continue

            ansi_lines = _strip_lines(raw_ansi)
            now = time.time()

            if raw_ansi != last_raw_snapshot:
                last_change_time = now
                if last_scrollback and _detect_screen_clear(last_scrollback, ansi_lines):
                    if saved_prefix:
                        saved_prefix.append('─' * 60)
                    saved_prefix.extend(last_scrollback)
                    saved_prefix.append('─' * 60)

                new_html_lines = [_ansi_to_html(l) for l in ansi_lines]

                # 双端比较
                diff_start = 0
                min_len = min(len(last_html_lines), len(new_html_lines))
                while diff_start < min_len and last_html_lines[diff_start] == new_html_lines[diff_start]:
                    diff_start += 1

                diff_end_old = len(last_html_lines)
                diff_end_new = len(new_html_lines)
                while diff_end_old > diff_start and diff_end_new > diff_start \
                        and last_html_lines[diff_end_old - 1] == new_html_lines[diff_end_new - 1]:
                    diff_end_old -= 1
                    diff_end_new -= 1

                if diff_start > 0 and diff_start >= len(last_html_lines) * 0.5:
                    delta_lines = new_html_lines[diff_start:diff_end_new]
                    delta_html = '\n'.join(delta_lines)
                    msg = f"D{diff_start}-{diff_end_old}\n{delta_html}"
                    label = f"D{diff_start}-{diff_end_old} ({len(delta_lines)}行/{len(new_html_lines)}行)"
                    await _ws_send(ws, msg, task_id, label)
                else:
                    full_html = '\n'.join(new_html_lines)
                    msg = f"F{full_html}"
                    await _ws_send(ws, msg, task_id, f"F ({len(ansi_lines)}行)")

                last_html_lines = new_html_lines
                last_scrollback = ansi_lines[:]
                last_raw_snapshot = raw_ansi
                changed = True
            else:
                changed = False

            # 每 10 秒持久化到数据库
            if now - last_db_save > 10 and (saved_prefix or last_scrollback):
                try:
                    full = (saved_prefix + last_scrollback) if saved_prefix else last_scrollback
                    await asyncio.to_thread(
                        task_db.save_terminal_log,
                        task_id, '\n'.join(full)
                    )
                except Exception as e:
                    logger.warning(f"保存终端日志失败: {e}")
                last_db_save = now

            # 有变化时立即再次检查，无变化时动态间隔
            if changed:
                continue
            idle = now - last_change_time
            if idle < 2:
                await asyncio.sleep(0.02)
            elif idle < 10:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(2.0)
    finally:
        if saved_prefix or last_scrollback:
            try:
                full = (saved_prefix + last_scrollback) if saved_prefix else last_scrollback
                task_db.save_terminal_log(task_id, '\n'.join(full))
            except Exception as e:
                logger.warning(f"最终保存终端日志失败: {e}")


async def _ws_send(ws: WebSocket, text: str, task_id: int = 0, label: str = ""):
    """小于 512B 直接文本发送，大于则 zlib 压缩二进制发送"""
    raw = text.encode('utf-8')
    if len(raw) < 512:
        await ws.send_text(text)
        if label:
            logger.info(f"[WS] task={task_id} {label} {len(raw)}B (text)")
    else:
        compressed = zlib.compress(raw, level=1)
        await ws.send_bytes(compressed)
        if label:
            logger.info(f"[WS] task={task_id} {label} {len(raw)/1024:.1f}KB→{len(compressed)/1024:.1f}KB (zlib)")


def _resize_tmux(task_id: int, cols: int):
    """调整 tmux pane 宽度"""
    session_name = _find_tmux_session(task_id)
    if session_name:
        try:
            subprocess.run(
                ["tmux", "resize-window", "-t", session_name, "-x", str(cols)],
                capture_output=True, timeout=5
            )
            logger.info(f"[WS] task={task_id} resize tmux → {cols} cols")
        except Exception as e:
            logger.warning(f"[WS] task={task_id} resize 失败: {e}")


async def _ws_recv_loop(task_id: int, ws: WebSocket):
    """监听客户端消息（屏幕旋转时发送新列数）"""
    import json
    try:
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
                if "cols" in data:
                    cols = max(40, min(int(data["cols"]), 300))
                    await asyncio.to_thread(_resize_tmux, task_id, cols)
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@router.websocket("/terminal/{task_id}/ws")
async def terminal_ws(task_id: int, ws: WebSocket):
    """WebSocket 终端内容流

    客户端随时可发送 {"cols": N} 调整终端宽度（连接时 + 屏幕旋转时）
    """
    await ws.accept()
    try:
        # 推送循环和接收循环并发运行，任一结束则全部取消
        push_task = asyncio.create_task(_ws_loop(task_id, ws))
        recv_task = asyncio.create_task(_ws_recv_loop(task_id, ws))
        done, pending = await asyncio.wait(
            [push_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WS] task={task_id} 异常: {e}")


# ==================== 页面 ====================

PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
* {{ margin: 0; padding: 0; }}
body {{
  background: #1a1a1a;
  font-family: "Courier New", "DejaVu Sans Mono", monospace;
  font-size: 10px; line-height: 1.25;
  overflow-x: auto; overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  display: flex;
  flex-direction: column;
}}
#content {{
  padding: 4px 6px;
  color: #d4d4d4;
  white-space: pre;
  margin-top: auto;
  overflow-x: auto;
}}
.L {{
  contain: content;
  min-height: 12px;
}}
</style>
</head>
<body>
<script src="https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js"></script>
<div id="content"><span style="color:#d4a54e">connecting...</span></div>
<script>
(function() {{
  var C = document.getElementById('content');
  var taskId = '{task_id}';
  var wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsUrl = wsProto + '//' + location.host + '/terminal/' + taskId + '/ws';
  var autoScroll = true;  // 是否自动跟随底部
  var userScrolling = false; // 用户正在手动滚动（触摸中）
  var touchY = 0;         // 触摸起始 Y
  var els = [];           // DOM 行元素数组
  var ready = false;      // 首次消息前为 false
  var queue = [];         // 消息队列
  var rafId = 0;

  document.body.style.minHeight = window.innerHeight + 'px';

  function isAtBottom() {{
    return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 60);
  }}

  // 记录触摸起始位置
  window.addEventListener('touchstart', function(e) {{
    touchY = e.touches[0].clientY;
    userScrolling = true;
  }}, {{ passive: true }});

  // 用户向上滑动超过 10px → 禁用自动滚动
  window.addEventListener('touchmove', function(e) {{
    var dy = e.touches[0].clientY - touchY;
    if (dy > 10) {{
      autoScroll = false;
    }}
  }}, {{ passive: true }});

  // 触摸结束后延迟检查（等惯性滚动停止）
  window.addEventListener('touchend', function() {{
    setTimeout(function() {{
      userScrolling = false;
      if (isAtBottom()) autoScroll = true;
    }}, 500);
  }}, {{ passive: true }});

  function scroll() {{
    if (autoScroll && !userScrolling) window.scrollTo(0, document.body.scrollHeight);
  }}

  // ---- 全量替换 ----
  function applyFull(html) {{
    if (!ready) {{ C.innerHTML = ''; els = []; ready = true; }}
    var lines = html.split('\\n');
    var n = lines.length;
    // 复用已有 DOM 节点
    var i = 0;
    for (; i < n && i < els.length; i++) {{
      if (els[i]._h !== lines[i]) {{ els[i].innerHTML = lines[i]; els[i]._h = lines[i]; }}
    }}
    // 多余的删除
    if (els.length > n) {{
      for (var r = els.length - 1; r >= n; r--) C.removeChild(els[r]);
      els.length = n;
    }}
    // 不够的追加
    if (i < n) {{
      var frag = document.createDocumentFragment();
      for (; i < n; i++) {{
        var el = document.createElement('div');
        el.className = 'L';
        el.innerHTML = lines[i];
        el._h = lines[i];
        frag.appendChild(el);
        els.push(el);
      }}
      C.appendChild(frag);
    }}
  }}

  // ---- 增量替换 lines[start:end] ----
  function applyDelta(start, end, html) {{
    var lines = html.split('\\n');
    var removeCount = end - start;
    var addCount = lines.length;

    if (removeCount === addCount) {{
      // 同数量：直接原地更新 innerHTML，零 DOM 增删
      for (var i = 0; i < addCount; i++) {{
        var el = els[start + i];
        if (el._h !== lines[i]) {{ el.innerHTML = lines[i]; el._h = lines[i]; }}
      }}
    }} else {{
      // 数量不同：删旧插新
      var removed = els.splice(start, removeCount);
      for (var r = 0; r < removed.length; r++) C.removeChild(removed[r]);
      var beforeEl = els[start] || null;
      var frag = document.createDocumentFragment();
      var inserted = [];
      for (var j = 0; j < addCount; j++) {{
        var el = document.createElement('div');
        el.className = 'L';
        el.innerHTML = lines[j];
        el._h = lines[j];
        frag.appendChild(el);
        inserted.push(el);
      }}
      C.insertBefore(frag, beforeEl);
      Array.prototype.splice.apply(els, [start, 0].concat(inserted));
    }}
  }}

  // ---- RAF 批量渲染 ----
  function flush() {{
    rafId = 0;
    var q = queue;
    queue = [];
    for (var i = 0; i < q.length; i++) {{
      var m = q[i];
      if (m.t === 'F') {{
        applyFull(m.d);
      }} else {{
        applyDelta(m.s, m.e, m.d);
      }}
    }}
    scroll();
  }}

  function enqueue(msg) {{
    queue.push(msg);
    if (!rafId) rafId = requestAnimationFrame(flush);
  }}

  // ---- 测量字符宽度，计算列数 ----
  function measureCols() {{
    var span = document.createElement('span');
    span.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:10px "Courier New","DejaVu Sans Mono",monospace';
    span.textContent = 'MMMMMMMMMM';
    document.body.appendChild(span);
    var charW = span.offsetWidth / 10;
    document.body.removeChild(span);
    var viewW = window.innerWidth - 12; // 减去 padding
    return Math.floor(viewW / charW);
  }}

  // ---- WebSocket ----
  function connect() {{
    var ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';

    ws.onopen = function() {{
      var cols = measureCols();
      ws.send(JSON.stringify({{ cols: cols }}));
      // 监听屏幕旋转/窗口大小变化，重新发送列数
      var lastCols = cols;
      window.addEventListener('resize', function() {{
        var newCols = measureCols();
        if (newCols !== lastCols && ws.readyState === 1) {{
          ws.send(JSON.stringify({{ cols: newCols }}));
          lastCols = newCols;
        }}
      }});
    }};

    ws.onmessage = function(ev) {{
      var text;
      if (typeof ev.data === 'string') {{
        text = ev.data;
      }} else {{
        text = pako.inflate(new Uint8Array(ev.data), {{ to: 'string' }});
      }}
      var type = text.charAt(0);
      if (type === 'D') {{
        var nl = text.indexOf('\\n');
        var parts = text.substring(1, nl).split('-');
        enqueue({{ t: 'D', s: parseInt(parts[0]), e: parseInt(parts[1]), d: text.substring(nl + 1) }});
      }} else {{
        enqueue({{ t: 'F', d: text.substring(1) }});
      }}
    }};

    ws.onclose = function() {{ setTimeout(connect, 2000); }};
    ws.onerror = function() {{ ws.close(); }};
  }}

  connect();
}})();
</script>
</body>
</html>"""


@router.get("/terminal/{task_id}/expanded", response_class=HTMLResponse)
async def terminal_expanded(task_id: int):
    """返回展开后的终端内容（静态 HTML，从 terminal_log_expanded 字段读取）"""
    task = task_db.get_task(task_id)
    if not task or not task.get("terminal_log_expanded"):
        return HTMLResponse(
            STATIC_PAGE_TEMPLATE.format(content='<span style="color:#d4a54e">暂无展开内容</span>'),
            status_code=200
        )
    html_content = _ansi_to_html(task["terminal_log_expanded"])
    return HTMLResponse(STATIC_PAGE_TEMPLATE.format(content=html_content))


STATIC_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
body {{
  margin: 0; padding: 0;
  background: #1a1a1a;
  font-family: "Courier New", "DejaVu Sans Mono", monospace;
  font-size: 10px; line-height: 1.25;
  overflow-x: auto; overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}}
#content {{
  padding: 4px 6px;
  color: #d4d4d4;
  white-space: pre;
  overflow-x: auto;
}}
</style>
</head>
<body>
<div id="content">{content}</div>
</body>
</html>"""


@router.get("/terminal/{task_id}", response_class=HTMLResponse)
async def terminal_page(task_id: int):
    """返回终端镜像页面（WebSocket 实时更新）"""
    return HTMLResponse(PAGE_TEMPLATE.format(task_id=task_id))
