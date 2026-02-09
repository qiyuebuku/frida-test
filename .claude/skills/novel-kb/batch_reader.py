#!/usr/bin/env python3
"""
T6 读者反馈分析 — 批量编排脚本

从 T1 段落评论中提炼读者反馈，构建情绪触发点 + 角色人气 + 期待分析。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T1 已完成: reader/comments/ 下有评论文件 (chXXXX.md)
    格式：章评 + 段评（每段含原文引用、用户、地区、点赞数、评论内容）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 0  preprocess     Python 预处理，去重去噪 + 统计 + 精选
                         产出: reader/.build/processed_data.json
                               包含全局 TOP 评论、热度段落、章节热度排名
                         耗时: ~30s（取决于评论量），0 次 AI 调用

  阶段 1  segment        分段分析，每段 100 章，Claude 提取 4 类反馈
                         产出: reader/.build/seg_XX.json
                               包含情绪触发点、角色提及、不满点、期待点
                         耗时: 取决于章节数，每段 1 次 AI 调用
                               支持 --concurrency M 并发 M 段加速
                               小规模（≤100 章）跳过分段，直接全量处理

  阶段 2  merge          全局融合，合并所有段数据 → 4 个 MD 文件
                         产出: reader/feedback/emotions.md（情绪触发点）
                               reader/feedback/popular_characters.md（角色人气）
                               reader/feedback/complaints.md（读者不满点）
                               reader/feedback/expectations.md（读者期待）
                               reader/index.md（读者层总览）
                         耗时: ~2min, 1 次 AI 调用（需要 Write 工具）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（从当前进度自动继续）
  python batch_reader.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：阶段 1 分段分析并发 3 段
  python batch_reader.py --book-dir ... --concurrency 3

  # 只运行某个阶段
  python batch_reader.py --book-dir ... --phase preprocess
  python batch_reader.py --book-dir ... --phase segment --concurrency 3
  python batch_reader.py --book-dir ... --phase merge

  # 试运行 + 验证产出
  python batch_reader.py --book-dir ... --dry-run
  python batch_reader.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH         知识库目录（必需）
  --segment-size N        每段章节数（默认 100，影响阶段 1）
  --top-per-chapter M     每章精选高赞评论数（默认 5，影响阶段 1 输入）
  --concurrency K         并发数（默认 1，仅对 segment 阶段生效）
  --phase PHASE           只运行特定阶段（preprocess/segment/merge）
  --model MODEL           Claude 模型（默认 sonnet）
  --timeout SEC           单次调用超时秒数（默认 600）
  --dry-run               试运行，不执行 Claude 调用
  --validate              验证产出文件完整性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    reader/
      feedback/
        emotions.md              情绪触发点（场景分类、TOP 高赞段落、写作启示）
        popular_characters.md    角色人气（提及次数、高赞评论、读者态度）
        complaints.md            读者不满点（槽点分类、改进建议）
        expectations.md          读者期待（期待剧情、角色发展预测）
      index.md                   读者反馈总览（统计 + 文件导航）
      .build/
        processed_data.json      预处理数据（全局统计、TOP 评论、热度章节）
        seg_XX.json              段级分析 JSON
      .progress.json             进度文件
"""

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from json_fixer import fix_and_parse_json

# Skill 目录
SKILL_DIR = Path(__file__).parent
PROMPTS_DIR = SKILL_DIR / "prompts"

# 产出文件
FEEDBACK_FILES = ["emotions", "popular_characters", "complaints", "expectations"]
FEEDBACK_NAMES = {
    "emotions": "情绪触发点",
    "popular_characters": "角色人气",
    "complaints": "读者不满点",
    "expectations": "读者期待",
}

# 噪音正则（编译一次）
NOISE_PATTERNS = [
    re.compile(r"^$"),                                    # 空
    re.compile(r"^[\[fn=\d+\]\s]*$"),                     # 纯表情代码
    re.compile(r"^.{0,3}$"),                              # ≤3 字符
    re.compile(r"^(前排|打卡|签到|[Mm]ark|沙发|板凳)"),    # 占楼类
    re.compile(r"^[^\u4e00-\u9fff\w]{1,10}$"),            # 纯符号/emoji
]


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="读者层分析")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--phase",
                        choices=["preprocess", "segment", "merge"],
                        help="只运行特定阶段")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--segment-size", type=int, default=100,
                        help="每段处理的章节数（默认 100）")
    parser.add_argument("--top-per-chapter", type=int, default=5,
                        help="每章精选的高赞评论数（默认 5）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="segment 阶段并发数（默认 1）")
    return parser.parse_args()


# ============================================================
# 路径解析
# ============================================================

def resolve_book_dir(book_dir_arg: str) -> Path:
    p = Path(book_dir_arg)
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if not p.exists():
        print(f"错误：目录不存在: {p}")
        sys.exit(1)
    return p


def get_comments_dir(book_dir: Path) -> Path:
    d = book_dir / "reader" / "comments"
    if not d.exists():
        print(f"错误：comments 目录不存在: {d}")
        print("请先运行 T1（数据提取）")
        sys.exit(1)
    return d


def get_reader_dir(book_dir: Path) -> Path:
    d = book_dir / "reader"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_feedback_dir(book_dir: Path) -> Path:
    d = book_dir / "reader" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_build_dir(book_dir: Path) -> Path:
    d = book_dir / "reader" / ".build"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(book_dir: Path) -> Path:
    return book_dir / "reader" / ".progress.json"


# ============================================================
# 进度管理
# ============================================================

def _default_progress() -> dict:
    return {
        "phase": "preprocess",
        "preprocess": {"status": "pending"},
        "segment_analyze": {
            "status": "pending",
            "segments_completed": [],
            "segments_failed": [],
        },
        "global_merge": {"status": "pending"},
        "stats": {
            "total_calls": 0,
            "total_time_seconds": 0,
        },
    }


def _read_progress_raw(progress_path: Path) -> dict:
    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return _default_progress()


def load_progress(progress_path: Path) -> dict:
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        try:
            return _read_progress_raw(progress_path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def save_progress(progress_path: Path, progress: dict):
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            disk = _read_progress_raw(progress_path)
            sa = progress.get("segment_analyze", {})
            dsa = disk.get("segment_analyze", {})
            merged_completed = sorted(set(dsa.get("segments_completed", [])) | set(sa.get("segments_completed", [])))
            merged_failed = sorted(
                (set(dsa.get("segments_failed", [])) | set(sa.get("segments_failed", []))) - set(merged_completed)
            )
            progress["segment_analyze"]["segments_completed"] = merged_completed
            progress["segment_analyze"]["segments_failed"] = merged_failed
            progress["stats"]["total_calls"] = max(
                disk["stats"].get("total_calls", 0), progress["stats"].get("total_calls", 0))
            progress["stats"]["total_time_seconds"] = max(
                disk["stats"].get("total_time_seconds", 0), progress["stats"].get("total_time_seconds", 0))
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ============================================================
# Claude 调用
# ============================================================

def run_claude_prompt(prompt: str, model: str, timeout: int,
                      allow_tools: str = "",
                      verbose: bool = True) -> tuple[bool, str]:
    cmd = [
        "claude",
        "-p", "-",
        "--model", model,
        "--permission-mode", "bypassPermissions",
    ]
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
# 评论解析
# ============================================================

# 评论头部正则：**用户名**（地区）· 时间 · 👍 数字 [可选回复部分]
COMMENT_HEADER_RE = re.compile(
    r"\*\*(.+?)\*\*（(.+?)）\s*·\s*"           # 用户名 + 地区
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*·\s*"  # 时间
    r"👍\s*(\d+)"                               # 点赞数
    r"(?:\s*💬\s*回复\s*\*\*(.+?)\*\*：「(.+?)(?:\.\.\.)?」)?"  # 可选回复
)

# 段评段落头：## 第N段评论（M条）
SEGMENT_HEADER_RE = re.compile(r"^## 第(\d+)段评论（(\d+)条）$")

# 章评头：## 章评（M条）
CHAPTER_REVIEW_RE = re.compile(r"^## 章评（(\d+)条）$")

# 原文引用：> 原文：内容 或 > 原文："内容"
ORIGINAL_TEXT_RE = re.compile(r'^> 原文：["\u201c\u201d]?(.+?)["\u201c\u201d]?\s*$')


def parse_comment_file(filepath: Path) -> dict:
    """解析单章评论文件，返回结构化数据"""
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()

    # 提取标题
    title = ""
    if lines and lines[0].startswith("# "):
        title_match = re.match(r"# (.+?) · 评论", lines[0])
        if title_match:
            title = title_match.group(1).strip()

    chapter_comments = []
    segment_comments = []

    current_section = None  # "chapter_review" or "segment"
    current_segment_id = None
    current_original_text = ""
    current_comments = []

    def flush_section():
        nonlocal current_comments
        if current_section == "chapter_review" and current_comments:
            chapter_comments.extend(current_comments)
        elif current_section == "segment" and current_segment_id is not None:
            segment_comments.append({
                "segment_id": current_segment_id,
                "original_text": current_original_text,
                "comments": list(current_comments),
            })
        current_comments = []

    i = 1  # skip title
    while i < len(lines):
        line = lines[i]

        # 检查章评头
        m = CHAPTER_REVIEW_RE.match(line)
        if m:
            flush_section()
            current_section = "chapter_review"
            current_original_text = ""
            i += 1
            continue

        # 检查段评头
        m = SEGMENT_HEADER_RE.match(line)
        if m:
            flush_section()
            current_section = "segment"
            current_segment_id = int(m.group(1))
            current_original_text = ""
            # 下一行可能是原文引用
            if i + 1 < len(lines):
                orig_m = ORIGINAL_TEXT_RE.match(lines[i + 1])
                if orig_m:
                    current_original_text = orig_m.group(1).strip()
                    i += 2
                    continue
            i += 1
            continue

        # 检查评论头
        m = COMMENT_HEADER_RE.search(line)
        if m:
            user = m.group(1)
            region = m.group(2)
            timestamp = m.group(3)
            likes = int(m.group(4))
            reply_to = m.group(5) or ""
            reply_quote = m.group(6) or ""

            # 收集评论内容（后续行直到下一个评论头或段头）
            content_lines = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                # 如果是新评论头、段头、章评头，停止
                if COMMENT_HEADER_RE.search(next_line):
                    break
                if SEGMENT_HEADER_RE.match(next_line):
                    break
                if CHAPTER_REVIEW_RE.match(next_line):
                    break
                # 评论内容行（以 > 开头或缩进或图片）
                if next_line.startswith("> ") and not ORIGINAL_TEXT_RE.match(next_line):
                    content_lines.append(next_line[2:])
                elif next_line.strip().startswith("🖼️"):
                    content_lines.append("[图片]")
                elif next_line.strip():
                    content_lines.append(next_line.strip())
                j += 1

            comment_text = "\n".join(content_lines).strip()

            current_comments.append({
                "user": user,
                "region": region,
                "timestamp": timestamp,
                "likes": likes,
                "content": comment_text,
                "reply_to": reply_to,
                "is_reply": bool(reply_to),
            })

            i = j
            continue

        i += 1

    # flush 最后一个 section
    flush_section()

    return {
        "title": title,
        "chapter_comments": chapter_comments,
        "segment_comments": segment_comments,
    }


def deduplicate_comments(comments: list) -> list:
    """去重：(user, content_hash, likes) 相同 → 只保留一条"""
    seen = set()
    result = []
    for c in comments:
        key = (c["user"], hashlib.md5(c["content"].encode()).hexdigest()[:8], c["likes"])
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


def is_noise(content: str) -> bool:
    """判断是否为噪音评论"""
    clean = content.strip()
    # 去掉图片标记后判断
    clean = clean.replace("[图片]", "").strip()
    if not clean:
        return True
    for pattern in NOISE_PATTERNS:
        if pattern.match(clean):
            return True
    return False


def filter_and_dedup(comments: list) -> list:
    """去重 + 去噪"""
    deduped = deduplicate_comments(comments)
    return [c for c in deduped if not is_noise(c["content"])]


# ============================================================
# 阶段 0：Python 预处理
# ============================================================

def list_comment_chapters(comments_dir: Path) -> list[int]:
    """扫描评论目录中的章节编号"""
    chapters = []
    for f in comments_dir.iterdir():
        m = re.match(r"ch(\d+)\.md$", f.name)
        if m:
            chapters.append(int(m.group(1)))
    chapters.sort()
    return chapters


def process_single_chapter(filepath: Path) -> dict:
    """处理单章：解析 → 去重去噪 → 统计 → 精选"""
    raw = parse_comment_file(filepath)

    # 去重去噪
    chapter_comments_clean = filter_and_dedup(raw["chapter_comments"])
    raw_chapter_count = len(raw["chapter_comments"])
    dedup_chapter_count = len(chapter_comments_clean)

    segment_data = []
    raw_segment_total = 0
    clean_segment_total = 0
    for seg in raw["segment_comments"]:
        raw_segment_total += len(seg["comments"])
        clean_comments = filter_and_dedup(seg["comments"])
        clean_segment_total += len(clean_comments)
        if clean_comments:
            segment_data.append({
                "segment_id": seg["segment_id"],
                "original_text": seg["original_text"][:150],  # 截断长原文
                "comments": clean_comments,
                "comment_count": len(clean_comments),
                "total_likes": sum(c["likes"] for c in clean_comments),
            })

    # 所有评论合并用于统计
    all_clean = chapter_comments_clean + [
        c for seg in segment_data for c in seg["comments"]
    ]

    # 统计地区分布
    regions = {}
    for c in all_clean:
        r = c["region"]
        regions[r] = regions.get(r, 0) + 1

    # 按赞数排序所有评论，取 TOP
    all_with_context = []
    for c in chapter_comments_clean:
        all_with_context.append({**c, "segment_id": -1, "original_text": ""})
    for seg in segment_data:
        for c in seg["comments"]:
            all_with_context.append({
                **c,
                "segment_id": seg["segment_id"],
                "original_text": seg["original_text"],
            })
    all_with_context.sort(key=lambda x: x["likes"], reverse=True)

    # 热度段落
    hot_segments = sorted(segment_data, key=lambda x: x["total_likes"], reverse=True)

    return {
        "title": raw["title"],
        "stats": {
            "raw_total": raw_chapter_count + raw_segment_total,
            "clean_total": len(all_clean),
            "duplicates_removed": (raw_chapter_count + raw_segment_total) - len(all_clean),
            "chapter_comments": dedup_chapter_count,
            "segment_comments": clean_segment_total,
            "total_likes": sum(c["likes"] for c in all_clean),
            "max_likes": max((c["likes"] for c in all_clean), default=0),
            "unique_users": len(set(c["user"] for c in all_clean)),
            "regions": regions,
        },
        "top_comments": [
            {
                "user": c["user"],
                "region": c["region"],
                "likes": c["likes"],
                "timestamp": c["timestamp"],
                "content": c["content"][:200],
                "is_reply": c["is_reply"],
                "segment_id": c["segment_id"],
                "original_text": c["original_text"][:100],
            }
            for c in all_with_context[:10]  # 保留 TOP 10 备用
        ],
        "hot_segments": [
            {
                "segment_id": seg["segment_id"],
                "original_text": seg["original_text"],
                "comment_count": seg["comment_count"],
                "total_likes": seg["total_likes"],
            }
            for seg in hot_segments[:5]  # TOP 5 热度段落
        ],
    }


def phase_preprocess(book_dir: Path, all_chapters: list[int],
                     progress: dict, progress_path: Path, dry_run: bool):
    """执行阶段 0：Python 预处理"""
    print("\n" + "=" * 60)
    print("阶段 0：Python 预处理")
    print("=" * 60)

    if progress["preprocess"]["status"] == "completed":
        print("预处理已完成！")
        return

    comments_dir = get_comments_dir(book_dir)

    if dry_run:
        print(f"将解析 {len(all_chapters)} 个评论文件")
        print("产出: reader/.build/processed_data.json")
        return

    # 逐章处理
    chapters_data = {}
    total_raw = 0
    total_clean = 0
    total_likes = 0
    all_regions = {}
    errors = []

    start_time = time.time()

    for idx, ch in enumerate(all_chapters):
        filepath = comments_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue

        try:
            ch_result = process_single_chapter(filepath)
            ch_str = f"ch{ch:04d}"
            chapters_data[ch_str] = ch_result

            total_raw += ch_result["stats"]["raw_total"]
            total_clean += ch_result["stats"]["clean_total"]
            total_likes += ch_result["stats"]["total_likes"]
            for r, cnt in ch_result["stats"]["regions"].items():
                all_regions[r] = all_regions.get(r, 0) + cnt

        except Exception as e:
            errors.append(f"ch{ch:04d}: {e}")

        # 进度显示
        if (idx + 1) % 100 == 0 or idx + 1 == len(all_chapters):
            elapsed = time.time() - start_time
            print(f"  已处理 {idx + 1}/{len(all_chapters)} 章 ({elapsed:.0f}s)")

    elapsed = time.time() - start_time

    # 全局 TOP 评论
    global_top = []
    for ch_str, data in chapters_data.items():
        for c in data["top_comments"]:
            global_top.append({**c, "chapter": ch_str})
    global_top.sort(key=lambda x: x["likes"], reverse=True)

    # 全局热度段落
    global_hot_segments = []
    for ch_str, data in chapters_data.items():
        for seg in data["hot_segments"]:
            global_hot_segments.append({**seg, "chapter": ch_str})
    global_hot_segments.sort(key=lambda x: x["total_likes"], reverse=True)

    # 章节热度排名
    chapter_heat = []
    for ch_str, data in chapters_data.items():
        chapter_heat.append({
            "chapter": ch_str,
            "title": data["title"],
            "total_likes": data["stats"]["total_likes"],
            "clean_total": data["stats"]["clean_total"],
        })
    chapter_heat.sort(key=lambda x: x["total_likes"], reverse=True)

    # 地区排名
    region_ranking = sorted(all_regions.items(), key=lambda x: x[1], reverse=True)

    # 汇总
    processed = {
        "global_stats": {
            "total_chapters": len(chapters_data),
            "total_comments_raw": total_raw,
            "total_comments_clean": total_clean,
            "duplicates_and_noise_removed": total_raw - total_clean,
            "filter_rate": round((total_raw - total_clean) / max(total_raw, 1) * 100, 1),
            "total_likes": total_likes,
            "total_unique_regions": len(all_regions),
            "region_ranking": region_ranking[:30],
            "processing_time_seconds": round(elapsed, 1),
            "errors": len(errors),
        },
        "global_top_comments": global_top[:50],
        "global_hot_segments": global_hot_segments[:50],
        "chapter_heat_ranking": chapter_heat[:50],
        "chapters": chapters_data,
    }

    # 保存
    build_dir = get_build_dir(book_dir)
    output_path = build_dir / "processed_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)

    # 输出统计
    gs = processed["global_stats"]
    print(f"\n解析完成 ({elapsed:.0f}s)")
    print(f"  处理章节: {gs['total_chapters']}")
    print(f"  原始评论: {gs['total_comments_raw']}")
    print(f"  有效评论: {gs['total_comments_clean']} (过滤率 {gs['filter_rate']}%)")
    print(f"  总赞数: {gs['total_likes']}")
    print(f"  地区数: {gs['total_unique_regions']}")
    if region_ranking:
        top3 = [f"{r}({n})" for r, n in region_ranking[:3]]
        print(f"  TOP 3 地区: {', '.join(top3)}")
    if errors:
        print(f"  错误: {len(errors)} 个")
        for e in errors[:5]:
            print(f"    {e}")

    if global_top[:3]:
        print(f"\n全局 TOP 3 评论:")
        for i, c in enumerate(global_top[:3]):
            print(f"  {i+1}. 👍{c['likes']} [{c['chapter']}] "
                  f"{c['content'][:50]}...")

    print(f"\n已保存: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")

    progress["preprocess"]["status"] = "completed"
    progress["phase"] = "segment_analyze"
    save_progress(progress_path, progress)


# ============================================================
# 阶段 1：分段分析
# ============================================================

def build_segment_text(processed: dict, start_ch: int, end_ch: int,
                       top_per_chapter: int = 5) -> str:
    """构建段级输入文本（精选评论）"""
    parts = []
    seg_total_comments = 0
    seg_total_likes = 0

    for ch_num in range(start_ch, end_ch + 1):
        ch_str = f"ch{ch_num:04d}"
        ch_data = processed["chapters"].get(ch_str)
        if not ch_data:
            continue

        stats = ch_data["stats"]
        seg_total_comments += stats["clean_total"]
        seg_total_likes += stats["total_likes"]

        # 每章精选 TOP N 评论
        top_comments = ch_data["top_comments"][:top_per_chapter]
        if not top_comments:
            continue

        ch_lines = [f"### {ch_str} · {ch_data['title']} "
                    f"({stats['clean_total']}评/{stats['total_likes']}赞)"]
        for c in top_comments:
            seg_info = ""
            if c["segment_id"] > 0 and c["original_text"]:
                seg_info = f" | 第{c['segment_id']}段"
                if c["original_text"]:
                    seg_info += f" | 原文：\"{c['original_text'][:60]}...\""
            elif c["segment_id"] == -1:
                seg_info = " | 章评"
            reply_info = " [回复]" if c["is_reply"] else ""
            ch_lines.append(
                f"- 👍{c['likes']} ({c['region']}){reply_info}{seg_info}\n"
                f"  \"{c['content'][:150]}\""
            )

        # 热度段落（补充未被 top_comments 覆盖的）
        hot_segs = ch_data["hot_segments"][:3]
        if hot_segs:
            ch_lines.append("热度段落:")
            for seg in hot_segs:
                ch_lines.append(
                    f"- 第{seg['segment_id']}段 "
                    f"({seg['comment_count']}评/{seg['total_likes']}赞): "
                    f"\"{seg['original_text'][:80]}...\""
                )

        parts.append("\n".join(ch_lines))

    # 头部统计
    header = (
        f"## 统计\n"
        f"- 章节范围: ch{start_ch:04d} ~ ch{end_ch:04d}\n"
        f"- 有效评论数: {seg_total_comments}\n"
        f"- 总赞数: {seg_total_likes}\n"
    )

    return header + "\n\n" + "\n\n".join(parts) if parts else ""


def build_segment_analyze_prompt(segment_text: str, segment_label: str) -> str:
    """构建分段分析 prompt"""
    template = (PROMPTS_DIR / "reader_segment_analyze.md").read_text(encoding="utf-8")
    prompt = template.replace("{segment_data}", segment_text)
    prompt = prompt.replace("{segment_label}", segment_label)
    return prompt


def phase_segment_analyze(book_dir: Path, all_chapters: list[int],
                          progress: dict, progress_path: Path,
                          model: str, timeout: int, dry_run: bool,
                          segment_size: int, top_per_chapter: int,
                          concurrency: int = 1):
    """执行阶段 1：分段分析"""
    print("\n" + "=" * 60)
    print("阶段 1：分段分析")
    print("=" * 60)

    build_dir = get_build_dir(book_dir)
    processed_path = build_dir / "processed_data.json"
    if not processed_path.exists():
        print("错误：processed_data.json 不存在，请先运行阶段 0")
        return

    with open(processed_path, "r", encoding="utf-8") as f:
        processed = json.load(f)

    total_chapters = len(all_chapters)

    # 小规模优化：≤100 章跳过分段
    if total_chapters <= segment_size:
        print(f"章节数 ({total_chapters}) ≤ {segment_size}，单段处理")
        segment_text = build_segment_text(
            processed, all_chapters[0], all_chapters[-1], top_per_chapter
        )
        segment_label = f"ch{all_chapters[0]:04d}-ch{all_chapters[-1]:04d} (全部)"

        if not segment_text:
            print("无有效评论数据")
            progress["segment_analyze"]["status"] = "completed"
            progress["phase"] = "global_merge"
            save_progress(progress_path, progress)
            return

        if dry_run:
            print(f"将一次性分析全部 {total_chapters} 章评论")
            print(f"输入长度: ~{len(segment_text)} 字符")
            return

        prompt = build_segment_analyze_prompt(segment_text, segment_label)
        print(f"分析全部 {total_chapters} 章评论...")
        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout)
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            debug_path = build_dir / "segment_all_json_debug.txt"
            result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
            if result:
                output_path = build_dir / "segment_all.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"完成 ({elapsed:.0f}s)")
                _print_segment_summary(result)
            else:
                print(f"无法解析 JSON ({elapsed:.0f}s)")
                print(f"调试信息已保存: {debug_path}")
                save_progress(progress_path, progress)
                return
        else:
            print(f"失败 ({elapsed:.0f}s): {output[:200]}")
            save_progress(progress_path, progress)
            return

        progress["segment_analyze"]["status"] = "completed"
        progress["segment_analyze"]["segments_completed"].append("all")
        progress["phase"] = "global_merge"
        save_progress(progress_path, progress)
        return

    # 正常分段处理
    segments = []
    for i in range(0, total_chapters, segment_size):
        seg_chapters = all_chapters[i:i + segment_size]
        segments.append((
            seg_chapters[0], seg_chapters[-1],
            f"seg_{i // segment_size + 1:02d}"
        ))

    completed = set(progress["segment_analyze"]["segments_completed"])
    pending = [s for s in segments if s[2] not in completed]

    print(f"总段数: {len(segments)} ({len(pending)} 待处理)")

    if dry_run:
        for start, end, label in pending:
            seg_text = build_segment_text(
                processed, start, end, top_per_chapter
            )
            print(f"  {label} (ch{start:04d}-ch{end:04d}): ~{len(seg_text)} 字符")
        return

    concurrency = max(1, concurrency)
    print_lock = threading.Lock()
    done_counter = {"n": 0, "total": len(pending)}

    def process_one_segment(start: int, end: int, seg_label: str,
                            verbose: bool = True) -> dict:
        seg_display = f"ch{start:04d}-ch{end:04d}"
        segment_text = build_segment_text(processed, start, end, top_per_chapter)

        if not segment_text:
            with print_lock:
                print(f"\n--- {seg_label} ({seg_display}): 无评论，跳过 ---")
            lock_path = progress_path.with_suffix(".lock")
            with open(lock_path, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    disk = _read_progress_raw(progress_path)
                    if seg_label not in disk["segment_analyze"]["segments_completed"]:
                        disk["segment_analyze"]["segments_completed"].append(seg_label)
                    with open(progress_path, "w", encoding="utf-8") as f:
                        json.dump(disk, f, ensure_ascii=False, indent=2)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
            with print_lock:
                done_counter["n"] += 1
            return {"seg": seg_label, "success": True, "skipped": True}

        prompt = build_segment_analyze_prompt(segment_text, f"{seg_label} ({seg_display})")

        with print_lock:
            print(f"\n--- {seg_label} ({seg_display}) ---")

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
        elapsed = time.time() - start_time

        lock_path = progress_path.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = _read_progress_raw(progress_path)
                disk["stats"]["total_calls"] = disk["stats"].get("total_calls", 0) + 1
                disk["stats"]["total_time_seconds"] = disk["stats"].get("total_time_seconds", 0) + int(elapsed)
                ok = False
                if success:
                    debug_path = build_dir / f"{seg_label}_json_debug.txt"
                    result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
                    if result:
                        output_path = build_dir / f"{seg_label}.json"
                        with open(output_path, "w", encoding="utf-8") as fo:
                            json.dump(result, fo, ensure_ascii=False, indent=2)
                        if seg_label not in disk["segment_analyze"]["segments_completed"]:
                            disk["segment_analyze"]["segments_completed"].append(seg_label)
                        ok = True
                    else:
                        if seg_label not in disk["segment_analyze"]["segments_failed"]:
                            disk["segment_analyze"]["segments_failed"].append(seg_label)
                else:
                    if seg_label not in disk["segment_analyze"]["segments_failed"]:
                        disk["segment_analyze"]["segments_failed"].append(seg_label)
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
                progress.update(disk)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        with print_lock:
            done_counter["n"] += 1
            status = "✓" if ok else "✗"
            msg = f"  [{seg_label}] {status} ({elapsed:.0f}s)  [{done_counter['n']}/{done_counter['total']}]"
            if ok:
                _print_segment_summary(result)
            elif not success:
                msg += f" {output[:100]}"
            print(msg)

        return {"seg": seg_label, "success": ok}

    if concurrency == 1:
        for start, end, seg_label in pending:
            process_one_segment(start, end, seg_label)
    else:
        print(f"并发数: {concurrency}")
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process_one_segment, s, e, l, verbose=False): l for s, e, l in pending}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    with print_lock:
                        print(f"  ✗ 线程异常 ({futures[future]}): {e}")

    # 检查是否全部完成
    final = load_progress(progress_path)
    all_done = set(s[2] for s in segments) <= set(final["segment_analyze"]["segments_completed"])
    if all_done:
        final["segment_analyze"]["status"] = "completed"
        final["phase"] = "global_merge"
        save_progress(progress_path, final)
        progress.update(final)
        print("\n分段分析全部完成！")


def _print_segment_summary(result: dict):
    """打印段级分析结果摘要"""
    n_emotions = len(result.get("emotions", []))
    n_chars = len(result.get("character_mentions", []))
    n_complaints = len(result.get("complaints", []))
    n_expects = len(result.get("expectations", []))
    print(f"  情绪: {n_emotions} | 角色: {n_chars} | "
          f"不满: {n_complaints} | 期待: {n_expects}")


# ============================================================
# 阶段 2：全局融合
# ============================================================

def merge_segments(build_dir: Path) -> dict:
    """Python 预合并全部段级 JSON"""
    merged = {
        "emotions": [],
        "character_mentions": [],
        "complaints": [],
        "expectations": [],
    }

    seg_files = sorted(build_dir.glob("segment_*.json")) + \
                sorted(build_dir.glob("seg_*.json"))

    for seg_file in seg_files:
        with open(seg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in merged:
            merged[key].extend(data.get(key, []))

    return merged


def build_global_merge_prompt(merged_data: dict, global_stats: dict,
                              global_top_comments: list,
                              chapter_heat_ranking: list,
                              feedback_dir: str) -> str:
    """构建全局融合 prompt"""
    template = (PROMPTS_DIR / "reader_global_merge.md").read_text(encoding="utf-8")
    prompt = template.replace(
        "{merged_data_json}",
        json.dumps(merged_data, ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace(
        "{global_stats_json}",
        json.dumps(global_stats, ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace(
        "{global_top_comments_json}",
        json.dumps(global_top_comments[:30], ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace(
        "{chapter_heat_json}",
        json.dumps(chapter_heat_ranking[:20], ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace("{feedback_dir}", feedback_dir)
    return prompt


def phase_global_merge(book_dir: Path, progress: dict, progress_path: Path,
                       model: str, timeout: int, dry_run: bool):
    """执行阶段 2：全局融合"""
    print("\n" + "=" * 60)
    print("阶段 2：全局融合")
    print("=" * 60)

    if progress["global_merge"]["status"] == "completed":
        print("全局融合已完成！")
        return

    build_dir = get_build_dir(book_dir)
    feedback_dir = get_feedback_dir(book_dir)

    # 加载段级数据
    merged_data = merge_segments(build_dir)
    total_entries = sum(len(v) for v in merged_data.values())

    if total_entries == 0:
        print("错误：无段级分析数据，请先运行阶段 1")
        return

    print(f"总条目数: {total_entries}")
    for key, items in merged_data.items():
        if items:
            label = FEEDBACK_NAMES.get(key, key)
            print(f"  {label}: {len(items)} 条")

    # 加载全局统计
    processed_path = build_dir / "processed_data.json"
    global_stats = {}
    global_top_comments = []
    chapter_heat_ranking = []
    if processed_path.exists():
        with open(processed_path, "r", encoding="utf-8") as f:
            processed = json.load(f)
        global_stats = processed.get("global_stats", {})
        global_top_comments = processed.get("global_top_comments", [])
        chapter_heat_ranking = processed.get("chapter_heat_ranking", [])

    if dry_run:
        print(f"将融合 {total_entries} 条分析到 4 个 MD 文件")
        return

    prompt = build_global_merge_prompt(
        merged_data, global_stats,
        global_top_comments, chapter_heat_ranking,
        str(feedback_dir)
    )

    print("调用 Claude 进行全局融合...")
    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write")
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    if success:
        print(f"完成 ({elapsed:.0f}s)")

        # 验证文件是否已生成
        generated = []
        for name in FEEDBACK_FILES:
            fpath = feedback_dir / f"{name}.md"
            if fpath.exists():
                generated.append(f"{name}.md ({fpath.stat().st_size} bytes)")
        print(f"  生成文件: {', '.join(generated) if generated else '无'}")

        progress["global_merge"]["status"] = "completed"
    else:
        print(f"失败 ({elapsed:.0f}s): {output[:200]}")

    save_progress(progress_path, progress)

    # 生成 index.md
    if progress["global_merge"]["status"] == "completed":
        build_index_md(book_dir)


# ============================================================
# index.md 生成
# ============================================================

def build_index_md(book_dir: Path):
    """Python 直接生成 reader/index.md"""
    reader_dir = get_reader_dir(book_dir)
    feedback_dir = get_feedback_dir(book_dir)
    build_dir = get_build_dir(book_dir)

    lines = ["# 读者反馈总览\n"]

    # 从 processed_data.json 读统计
    processed_path = build_dir / "processed_data.json"
    if processed_path.exists():
        with open(processed_path, "r", encoding="utf-8") as f:
            processed = json.load(f)
        gs = processed.get("global_stats", {})

        lines.append("## 统计\n")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 分析章数 | {gs.get('total_chapters', '?')} |")
        lines.append(f"| 有效评论数 | {gs.get('total_comments_clean', '?')}（去重去噪后） |")
        lines.append(f"| 过滤率 | {gs.get('filter_rate', '?')}% |")
        lines.append(f"| 总赞数 | {gs.get('total_likes', '?')} |")
        lines.append(f"| 地区覆盖 | {gs.get('total_unique_regions', '?')} 个 |")
        lines.append("")

    # 产出文件导航
    lines.append("## 文件导航\n")
    for name in FEEDBACK_FILES:
        fpath = feedback_dir / f"{name}.md"
        label = FEEDBACK_NAMES[name]
        if fpath.exists():
            size = fpath.stat().st_size
            lines.append(f"- [{label}](feedback/{name}.md)（{size} bytes）")
        else:
            lines.append(f"- {label}（未生成）")

    lines.append("")

    index_path = reader_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成: {index_path}")


# ============================================================
# 验证
# ============================================================

def run_validate(book_dir: Path):
    """验证所有产出文件"""
    print("\n验证 T6 产出文件...")

    reader_dir = book_dir / "reader"
    feedback_dir = book_dir / "reader" / "feedback"
    build_dir = book_dir / "reader" / ".build"

    results = []

    # 检查中间产物
    processed_path = build_dir / "processed_data.json"
    if processed_path.exists():
        size = processed_path.stat().st_size
        with open(processed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        gs = data.get("global_stats", {})
        results.append(("预处理数据", "OK",
                        f"{size / 1024 / 1024:.1f} MB, "
                        f"{gs.get('total_chapters', '?')} 章, "
                        f"{gs.get('total_comments_clean', '?')} 评论"))
    else:
        results.append(("预处理数据", "缺失", ""))

    # 检查段级 JSON
    seg_files = list(build_dir.glob("segment_*.json")) + \
                list(build_dir.glob("seg_*.json"))
    results.append(("段级分析", "OK" if seg_files else "缺失",
                     f"{len(seg_files)} 个文件"))

    # 检查 4 个 feedback 文件
    for name in FEEDBACK_FILES:
        fpath = feedback_dir / f"{name}.md"
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            sections = len(re.findall(r"^## .+", content, re.MULTILINE))
            items = len(re.findall(r"^- .+", content, re.MULTILINE))
            results.append((FEEDBACK_NAMES[name], "OK",
                            f"{fpath.stat().st_size} bytes, "
                            f"{sections} 段, {items} 条"))
        else:
            results.append((FEEDBACK_NAMES[name], "缺失", ""))

    # 检查 index.md
    index_path = reader_dir / "index.md"
    if index_path.exists():
        results.append(("读者层总览", "OK",
                        f"{index_path.stat().st_size} bytes"))
    else:
        results.append(("读者层总览", "缺失", ""))

    # 输出结果
    print(f"\n{'文件':<16} {'状态':<6} {'详情'}")
    print("-" * 60)
    for name, status, detail in results:
        print(f"{name:<16} {status:<6} {detail}")

    # 加载进度统计
    progress_path = get_progress_path(book_dir)
    if progress_path.exists():
        progress = load_progress(progress_path)
        print(f"\n统计:")
        print(f"  总调用: {progress['stats']['total_calls']} 次")
        total_s = progress["stats"]["total_time_seconds"]
        print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")


# ============================================================
# 主流程
# ============================================================

def main():
    args = parse_args()

    book_dir = resolve_book_dir(args.book_dir)
    comments_dir = get_comments_dir(book_dir)
    progress_path = get_progress_path(book_dir)

    # 扫描评论文件
    all_chapters = list_comment_chapters(comments_dir)
    if not all_chapters:
        print(f"错误：comments 目录中没有评论文件: {comments_dir}")
        sys.exit(1)

    print(f"知识库: {book_dir}")
    print(f"评论文件: {len(all_chapters)} 章 "
          f"(ch{all_chapters[0]:04d} ~ ch{all_chapters[-1]:04d})")

    # 验证模式
    if args.validate:
        run_validate(book_dir)
        return

    # 加载进度
    progress = load_progress(progress_path)
    print(f"当前阶段: {progress['phase']}")

    # 确保输出目录存在
    get_reader_dir(book_dir)
    get_feedback_dir(book_dir)
    get_build_dir(book_dir)

    if args.dry_run:
        print("\n=== 试运行模式 ===")

    # 阶段 0: 预处理
    if args.phase == "preprocess" or (
        not args.phase and progress["phase"] == "preprocess"
    ):
        phase_preprocess(book_dir, all_chapters, progress, progress_path,
                         args.dry_run)

    # 阶段 1: 分段分析
    if args.phase == "segment" or (
        not args.phase and progress["phase"] in ("segment_analyze", "preprocess")
    ):
        progress = load_progress(progress_path)
        if progress["phase"] == "segment_analyze" or args.phase == "segment":
            phase_segment_analyze(
                book_dir, all_chapters, progress, progress_path,
                args.model, args.timeout, args.dry_run,
                args.segment_size, args.top_per_chapter,
                args.concurrency
            )

    # 阶段 2: 全局融合
    if args.phase == "merge" or (
        not args.phase and progress["phase"] in ("global_merge", "segment_analyze")
    ):
        progress = load_progress(progress_path)
        if progress["phase"] == "global_merge" or args.phase == "merge":
            phase_global_merge(book_dir, progress, progress_path,
                               args.model, args.timeout, args.dry_run)

    # 最终统计
    progress = load_progress(progress_path)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    print(f"  预处理: {progress['preprocess']['status']}")
    sa = progress["segment_analyze"]
    print(f"  分段分析: {sa['status']} ({len(sa['segments_completed'])} 完成/"
          f"{len(sa['segments_failed'])} 失败)")
    print(f"  全局融合: {progress['global_merge']['status']}")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    # 检查是否全部完成
    all_done = (
        progress["preprocess"]["status"] == "completed"
        and progress["segment_analyze"]["status"] == "completed"
        and progress["global_merge"]["status"] == "completed"
    )
    if all_done:
        print("\nT6 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
