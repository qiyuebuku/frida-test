#!/usr/bin/env python3
"""
novel-kb 公共基础设施 — 6 个 batch 脚本的共享代码

提供：
  - 路径解析（resolve_book_dir）
  - 进度管理（load_progress / save_progress，带 fcntl 文件锁）
  - Claude CLI 调用（run_claude_prompt）
  - 章节文件扫描（list_chapter_files）
  - 分段计算（compute_segments）
  - Markdown 解析工具（parse_section, parse_setting_lines）
  - 文件名工具（sanitize_filename）
  - 输出工具（print_flush, show_waiting_animation）
"""

import fcntl
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Skill 目录（所有脚本共享）
SKILL_DIR = Path(__file__).parent
PROMPTS_DIR = SKILL_DIR / "prompts"


# ============================================================
# 路径解析
# ============================================================

def resolve_book_dir(book_dir_arg: str) -> Path:
    """解析 book-dir 参数为绝对路径"""
    p = Path(book_dir_arg)
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if not p.exists():
        print(f"错误：目录不存在: {p}")
        sys.exit(1)
    return p


# ============================================================
# 进度管理（带 fcntl 文件锁，多进程安全）
# ============================================================

def load_progress(progress_path: Path,
                  default_fn: Callable[[], dict] = dict) -> dict:
    """加载进度文件（带共享文件锁）

    Args:
        progress_path: 进度文件路径
        default_fn: 文件不存在时返回的默认值工厂函数
    """
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        try:
            if progress_path.exists():
                with open(progress_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return default_fn()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def save_progress(progress_path: Path, progress: dict,
                  default_fn: Callable[[], dict] = dict,
                  merge_fn: Optional[Callable[[dict, dict], dict]] = None) -> None:
    """保存进度文件（带排他文件锁 + 可选合并策略）

    流程：lock → 读磁盘最新 → 合并 → 写入 → unlock → 同步回 progress

    Args:
        progress_path: 进度文件路径
        progress: 本地进度状态（会被合并结果同步更新）
        default_fn: 文件不存在时返回的默认值工厂函数
        merge_fn: 自定义合并函数 merge_fn(disk, local) -> merged
                  如果为 None，直接用 local 覆盖
    """
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if progress_path.exists():
                with open(progress_path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            else:
                disk = default_fn()

            if merge_fn:
                merged = merge_fn(disk, progress)
            else:
                merged = progress

            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)

            # 同步回本地变量
            progress.update(merged)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def merge_stats(disk_stats: dict, local_stats: dict) -> dict:
    """合并统计信息（取较大值）— 各脚本的 stats 合并逻辑完全一致"""
    return {
        "total_calls": max(
            disk_stats.get("total_calls", 0),
            local_stats.get("total_calls", 0)),
        "total_time_seconds": max(
            disk_stats.get("total_time_seconds", 0),
            local_stats.get("total_time_seconds", 0)),
    }


def merge_list_fields(disk: dict, local: dict,
                      completed_key: str,
                      failed_key: Optional[str] = None) -> None:
    """合并进度中的列表字段（并集策略，failed 自动排除 completed）

    直接修改 local dict。

    Args:
        disk: 磁盘上的进度数据
        local: 本地进度数据（会被修改）
        completed_key: completed 列表的键名（用 . 分隔嵌套路径，如 "segment_scan.completed"）
        failed_key: failed 列表的键名（可选）
    """
    def _get_nested(d, key_path):
        parts = key_path.split(".")
        for p in parts:
            d = d.get(p, {}) if isinstance(d, dict) else {}
        return d if isinstance(d, list) else []

    def _set_nested(d, key_path, value):
        parts = key_path.split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = value

    merged_completed = sorted(
        set(_get_nested(disk, completed_key)) |
        set(_get_nested(local, completed_key))
    )
    _set_nested(local, completed_key, merged_completed)

    if failed_key:
        merged_failed = sorted(
            (set(_get_nested(disk, failed_key)) |
             set(_get_nested(local, failed_key)))
            - set(merged_completed)
        )
        _set_nested(local, failed_key, merged_failed)


# ============================================================
# Claude CLI 调用
# ============================================================

def run_claude_prompt(prompt: str, model: str, timeout: int,
                      allow_tools: str = "",
                      verbose: bool = True,
                      agent: str = "") -> tuple[bool, str]:
    """调用 claude -p 并返回 (成功, stdout 输出)

    prompt 通过 stdin 管道传入，避免命令行参数长度限制。

    Args:
        prompt: 任务 prompt 文本
        model: Claude 模型名称（如 "sonnet"）
        timeout: 超时秒数
        allow_tools: 允许的工具列表（如 "Read,Write"）
        verbose: True 时 stderr 直接输出到终端；False 时捕获全部（适合并发）
        agent: 使用的 subagent 名称（如 "world-extractor"），可选

    Returns:
        (success, output) 元组
    """
    cmd = [
        "claude",
        "-p", "-",
        "--model", model,
        "--permission-mode", "bypassPermissions",
    ]
    if agent:
        cmd.extend(["--agent", agent])
    if allow_tools:
        cmd.extend(["--allowedTools", allow_tools])

    try:
        if verbose:
            result = subprocess.run(
                cmd,
                input=prompt,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                return False, f"退出码 {result.returncode}\n{result.stdout[:1000]}"
        else:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                err_detail = result.stderr[:1000] if result.stderr.strip() else result.stdout[:1000]
                return False, f"退出码 {result.returncode}\n{err_detail}"
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, f"超时（{timeout}秒）"
    except Exception as e:
        return False, f"异常: {e}"


# ============================================================
# 章节文件扫描
# ============================================================

def list_chapter_files(directory: Path, pattern: str = r"ch(\d+)\.md$") -> list[int]:
    """扫描目录中的章节文件编号，返回排序后的列表

    适用于 text/chXXXX.md、plot/chapters/chXXXX.md 等目录。

    Args:
        directory: 要扫描的目录
        pattern: 匹配文件名的正则表达式，必须有一个捕获组（章节号）
    """
    chapters = []
    compiled = re.compile(pattern)
    for f in directory.iterdir():
        m = compiled.match(f.name)
        if m:
            chapters.append(int(m.group(1)))
    chapters.sort()
    return chapters


def load_chapter_file(directory: Path, ch_num: int,
                      fmt: str = "ch{:04d}.md",
                      missing_msg: str = "[ch{:04d} 缺失]") -> str:
    """加载单章文件内容

    Args:
        directory: 章节目录
        ch_num: 章节编号
        fmt: 文件名格式
        missing_msg: 文件不存在时的占位文本
    """
    filepath = directory / fmt.format(ch_num)
    if not filepath.exists():
        return missing_msg.format(ch_num)
    return filepath.read_text(encoding="utf-8")


# ============================================================
# 分段计算
# ============================================================

def compute_segments(all_chapters: list[int], segment_size: int) -> list[dict]:
    """将章节列表切分为若干段

    Returns:
        [{"num": 1, "chapters": [1,2,...], "start_ch": 1, "end_ch": 100,
          "range": "ch0001-ch0100"}, ...]
    """
    segments = []
    for i in range(0, len(all_chapters), segment_size):
        chunk = all_chapters[i:i + segment_size]
        seg_num = len(segments) + 1
        segments.append({
            "num": seg_num,
            "chapters": chunk,
            "start_ch": chunk[0],
            "end_ch": chunk[-1],
            "range": f"ch{chunk[0]:04d}-ch{chunk[-1]:04d}",
        })
    return segments


# ============================================================
# Markdown 解析工具
# ============================================================

def parse_section(content: str, section_name: str) -> str:
    """从 Markdown 内容中提取指定 ## 段落的文本

    Args:
        content: Markdown 文本
        section_name: 二级标题名（如 "关键事件"）

    Returns:
        段落内容（去除首尾空白），找不到返回空字符串
    """
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_setting_lines(section_text: str) -> list[dict]:
    """解析设定行列表，返回 [{"name": ..., "description": ...}, ...]

    格式：- 名称：描述  或  - 名称：描述
    跳过 "（无）" 标记的行。
    """
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        m = re.match(r"- (.+?)[:：](.+)", line)
        if m:
            results.append({
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            })
    return results


# ============================================================
# 文件名工具
# ============================================================

def sanitize_filename(name: str) -> str:
    """将实体名转为安全的文件名（保留中文、字母、数字）"""
    name = name.strip()
    name = re.sub(r'[/\\:*?"<>|]', '_', name)
    name = name.replace(' ', '_')
    if len(name.encode('utf-8')) > 200:
        while len(name.encode('utf-8')) > 200:
            name = name[:-1]
    return name or "unnamed"


# ============================================================
# 输出工具
# ============================================================

def print_flush(msg: str, end: str = "\n"):
    """带 flush 的 print，确保实时输出"""
    print(msg, end=end, flush=True)


def show_waiting_animation(stop_event: threading.Event,
                           message: str = "处理中"):
    """在后台线程显示等待动画（仅在 TTY 中显示）"""
    if not sys.stdout.isatty():
        return
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    idx = 0
    while not stop_event.is_set():
        print_flush(f"\r{chars[idx]} {message}...", end="")
        idx = (idx + 1) % len(chars)
        time.sleep(0.1)
    print_flush("\r" + " " * (len(message) + 10) + "\r", end="")
