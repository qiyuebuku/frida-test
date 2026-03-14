"""终端镜像：服务端累积 ANSI → HTML，通过 SSE 实时推送，持久化到数据库"""

import asyncio
import logging
import subprocess
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import StreamingResponse

from services import task_db

router = APIRouter()
logger = logging.getLogger(__name__)

SESSION_PREFIX = "sa_claude_"


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


def _capture_pane(session_name: str) -> str:
    """捕获 tmux 全部 scrollback（-S - 从头开始）"""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-e", "-S", "-"],
            capture_output=True, timeout=5
        )
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
    """检测是否发生了屏幕清除：新内容大幅缩短且首行不同"""
    if not old_lines:
        return False
    if len(new_lines) < len(old_lines) * 0.5 and len(old_lines) > 10:
        # 内容大幅缩短，且首行不同 → 屏幕被清除
        if new_lines and old_lines and new_lines[0] != old_lines[0]:
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


# ==================== SSE 流（累积模式） ====================

async def _sse_generator(task_id: int, request: Request):
    """SSE 生成器：显示全量 scrollback + 屏幕清除时保留旧内容，持久化到数据库"""
    last_html = ""
    no_session_count = 0
    saved_prefix: list[str] = []  # 屏幕清除前保存的内容
    last_scrollback: list[str] = []  # 上次捕获的 scrollback
    last_db_save = time.time()

    # 从数据库加载已有历史
    try:
        existing = task_db.get_terminal_log(task_id)
        if existing:
            saved_prefix = existing.split('\n')
            html = _ansi_to_html(existing)
            encoded = html.replace('\n', '⏎')
            yield f"data: {encoded}\n\n"
            last_html = html
    except Exception as e:
        logger.warning(f"加载终端历史失败: {e}")

    try:
        while True:
            if await request.is_disconnected():
                break

            session_name = _find_tmux_session(task_id)
            if not session_name:
                no_session_count += 1
                if not saved_prefix:
                    html = '<span style="color:#d4a54e">等待终端启动...</span>'
                    if html != last_html:
                        yield f"data: {html}\n\n"
                        last_html = html
                if no_session_count > 60:
                    await asyncio.sleep(3)
                else:
                    await asyncio.sleep(1)
                continue

            no_session_count = 0
            raw = await asyncio.to_thread(_capture_pane, session_name)

            if not raw.strip():
                await asyncio.sleep(0.5)
                continue

            scrollback = _strip_lines(raw)

            if scrollback != last_scrollback:
                # 检测屏幕清除
                if _detect_screen_clear(last_scrollback, scrollback):
                    # 保存旧的 scrollback 到前缀
                    if saved_prefix:
                        saved_prefix.append('─' * 60)
                    saved_prefix.extend(last_scrollback)
                    saved_prefix.append('─' * 60)

                last_scrollback = scrollback[:]

                # 显示内容 = 保存的前缀 + 当前 scrollback
                if saved_prefix:
                    all_lines = saved_prefix + scrollback
                else:
                    all_lines = scrollback

                all_raw = '\n'.join(all_lines)
                html = _ansi_to_html(all_raw)

                if html != last_html:
                    encoded = html.replace('\n', '⏎')
                    yield f"data: {encoded}\n\n"
                    last_html = html

            # 每 10 秒持久化到数据库
            now = time.time()
            if now - last_db_save > 10 and (saved_prefix or last_scrollback):
                try:
                    if saved_prefix:
                        full = saved_prefix + last_scrollback
                    else:
                        full = last_scrollback
                    await asyncio.to_thread(
                        task_db.save_terminal_log,
                        task_id, '\n'.join(full)
                    )
                except Exception as e:
                    logger.warning(f"保存终端日志失败: {e}")
                last_db_save = now

            await asyncio.sleep(0.5)
    finally:
        # 断开时最终保存
        if saved_prefix or last_scrollback:
            try:
                if saved_prefix:
                    full = saved_prefix + last_scrollback
                else:
                    full = last_scrollback
                task_db.save_terminal_log(task_id, '\n'.join(full))
            except Exception as e:
                logger.warning(f"最终保存终端日志失败: {e}")


@router.get("/terminal/{task_id}/stream")
async def terminal_stream(task_id: int, request: Request):
    """SSE 终端内容流"""
    return StreamingResponse(
        _sse_generator(task_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ==================== 页面 ====================

PAGE_TEMPLATE = """<!DOCTYPE html>
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
</style>
</head>
<body>
<div id="content"><span style="color:#d4a54e">connecting...</span></div>
<script>
(function() {{
  var contentEl = document.getElementById('content');
  var taskId = '{task_id}';
  var streamUrl = location.origin + '/terminal/' + taskId + '/stream';
  var userScrolled = false;
  var lastScrollHeight = 0;

  document.body.style.minHeight = window.innerHeight + 'px';

  window.addEventListener('touchstart', function() {{
    userScrolled = true;
  }});
  window.addEventListener('scrollend', function() {{
    if ((window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 50)) {{
      userScrolled = false;
    }}
  }});

  function connect() {{
    var es = new EventSource(streamUrl);

    es.onmessage = function(event) {{
      var html = event.data.replace(/⏎/g, '\\n');
      contentEl.innerHTML = html;
      var newHeight = document.body.scrollHeight;
      if (!userScrolled && newHeight > lastScrollHeight) {{
        window.scrollTo(0, newHeight);
      }}
      lastScrollHeight = newHeight;
    }};

    es.onerror = function() {{
      es.close();
      setTimeout(connect, 2000);
    }};
  }}

  connect();
}})();
</script>
</body>
</html>"""


@router.get("/terminal/{task_id}", response_class=HTMLResponse)
async def terminal_page(task_id: int):
    """返回终端镜像页面（SSE 实时更新）"""
    return HTMLResponse(PAGE_TEMPLATE.format(task_id=task_id))
