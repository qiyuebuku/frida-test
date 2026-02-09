#!/usr/bin/env python3
"""
T3 剧情层提取 — 批量编排脚本

从 T2 章节摘要提取全局剧情结构（故事弧、主线追踪、伏笔汇总、时间线）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T2 已完成: plot/chapters/ 下有章节摘要 (chXXXX.md)
  - plot/chapters/index.md 存在（用于快速浏览全书结构）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 1  segment-scan   分段扫描，每段 100 章，提取重要事件 + 主线进度 + 弧候选
                         产出: plot/outline/.segments/segment_XX.json
                         耗时: 取决于章节数，每段 1 次 AI 调用
                               支持 --concurrency M 并发 M 段加速

  阶段 2  global-merge   全局融合，合并所有段数据 → 完整剧情文件
                         产出: plot/outline/plot_lines.md（主线追踪）
                               plot/outline/arc_XX.md（故事弧，N 个）
                               plot/open_loops.md（伏笔汇总）
                               plot/timeline/index.md（时间线）
                               plot/outline/index.md（大纲概览）
                         耗时: ~2-3min, 1 次 AI 调用（需要 Write 工具）

  阶段 3  refine         精修验证，分 4 步自动执行
                         3a. 弧边界验证 + 调整（检查弧起止章节是否准确）
                         3b. 弧内容充实（逐弧补充详细信息，支持串行处理）
                         3c. 伏笔交叉验证（验证已埋伏笔与回收的一致性）
                         3d. 最终更新（应用验证结果，更新所有文件）
                         产出: 更新上述全部文件 + plot/index.md（导航页）
                         耗时: ~5-10min, 每个弧 1 次 AI + 验证 2-3 次 AI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（从当前进度自动继续）
  python batch_plot.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：阶段 1 分段扫描并发 3 段
  python batch_plot.py --book-dir ... --concurrency 3

  # 清除旧产出，从头重跑（慎用）
  python batch_plot.py --book-dir ... --reset --concurrency 3

  # 只运行某个阶段
  python batch_plot.py --book-dir ... --phase segment-scan --concurrency 3
  python batch_plot.py --book-dir ... --phase global-merge
  python batch_plot.py --book-dir ... --phase refine

  # 试运行 + 验证产出
  python batch_plot.py --book-dir ... --dry-run
  python batch_plot.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH        知识库目录（必需）
  --segment-size N       每段章节数（默认 100，影响阶段 1）
  --concurrency M        并发数（默认 1，仅对 segment-scan 生效）
  --phase PHASE          只运行特定阶段（segment-scan/global-merge/refine）
  --reset                清除旧产出和进度，从头重跑（慎用）
  --model MODEL          Claude 模型（默认 sonnet）
  --timeout SEC          单次调用超时秒数（默认 600）
  --dry-run              试运行，不执行 Claude 调用
  --validate             验证产出文件完整性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    plot/
      outline/
        plot_lines.md             主线追踪（主线名称、状态、关键章节）
        arc_01.md, arc_02.md...   故事弧（章节范围、核心冲突、转折点、新世界信息）
        index.md                  大纲概览（全书结构概述）
        .segments/                中间产物（段级分析 JSON）
        .progress.json            进度文件
      open_loops.md               伏笔汇总（已回收/未回收，按重要性分类）
      timeline/
        index.md                  时间线（时间标记事件按时序排列）
      index.md                    剧情层导航页（链接上述全部文件）
"""

import argparse
import fcntl
import json
import os
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


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="剧情层提取")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--phase", choices=["segment-scan", "global-merge", "refine"],
                        help="只运行特定阶段")
    parser.add_argument("--segment-size", type=int, default=100,
                        help="每段章数（默认 100）")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="segment-scan 阶段并发数（默认 1）")
    parser.add_argument("--reset", action="store_true",
                        help="清除进度和产出，从头重跑")
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


def get_chapters_dir(book_dir: Path) -> Path:
    """T2 产出的章节摘要目录"""
    d = book_dir / "plot" / "chapters"
    if not d.exists():
        print(f"错误：chapters 目录不存在: {d}")
        print("请先运行 T2（章节摘要生成）")
        sys.exit(1)
    return d


def get_outline_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "outline"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_segments_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "outline" / ".segments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_timeline_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "timeline"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(book_dir: Path) -> Path:
    return book_dir / "plot" / "outline" / ".progress.json"


# ============================================================
# 进度管理
# ============================================================

def _default_progress() -> dict:
    return {
        "phase": "segment_scan",
        "segment_scan": {
            "total_segments": 0,
            "completed": [],
            "failed": [],
            "segment_size": 100,
        },
        "global_merge": {
            "status": "pending",
            "arc_count": None,
        },
        "refine": {
            "arc_boundary_validated": False,
            "arc_details_completed": [],
            "arc_details_failed": [],
            "foreshadow_validated": False,
            "final_updated": False,
        },
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
    """带文件锁的保存（合并 segment_scan、refine、stats）"""
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            disk = _read_progress_raw(progress_path)
            # 合并 segment_scan 的 completed/failed
            disk_ss = disk.get("segment_scan", {})
            local_ss = progress.get("segment_scan", {})
            merged_completed = sorted(set(disk_ss.get("completed", [])) | set(local_ss.get("completed", [])))
            merged_failed = sorted(
                (set(disk_ss.get("failed", [])) | set(local_ss.get("failed", []))) - set(merged_completed)
            )
            progress["segment_scan"]["completed"] = merged_completed
            progress["segment_scan"]["failed"] = merged_failed
            # 合并 refine（布尔值取 or，列表取并集）
            disk_ref = disk.get("refine", {})
            local_ref = progress.get("refine", {})
            for bkey in ("arc_boundary_validated", "foreshadow_validated", "final_updated"):
                progress["refine"][bkey] = disk_ref.get(bkey, False) or local_ref.get(bkey, False)
            for lkey in ("arc_details_completed", "arc_details_failed"):
                merged = sorted(set(disk_ref.get(lkey, [])) | set(local_ref.get(lkey, [])))
                progress["refine"][lkey] = merged
            # stats 累加
            progress["stats"]["total_calls"] = max(
                disk["stats"].get("total_calls", 0),
                progress["stats"].get("total_calls", 0))
            progress["stats"]["total_time_seconds"] = max(
                disk["stats"].get("total_time_seconds", 0),
                progress["stats"].get("total_time_seconds", 0))
            # phase 取更靠后的
            phase_order = ["segment_scan", "global_merge", "refine"]
            disk_phase = disk.get("phase", "segment_scan")
            local_phase = progress.get("phase", "segment_scan")
            if phase_order.index(disk_phase) > phase_order.index(local_phase):
                progress["phase"] = disk_phase
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ============================================================
# 数据加载
# ============================================================

def list_summary_chapters(chapters_dir: Path) -> list[int]:
    """扫描已生成的章节摘要文件编号"""
    chapters = []
    for f in chapters_dir.iterdir():
        m = re.match(r"ch(\d+)\.md$", f.name)
        if m:
            chapters.append(int(m.group(1)))
    chapters.sort()
    return chapters


def load_full_index(chapters_dir: Path) -> str:
    """加载 index.md 全文"""
    index_path = chapters_dir / "index.md"
    if not index_path.exists():
        print(f"错误：index.md 不存在: {index_path}")
        print("请先运行: python batch_summary.py --book-dir ... --gen-index")
        sys.exit(1)
    return index_path.read_text(encoding="utf-8")


def load_chapter_summary(chapters_dir: Path, ch_num: int) -> str:
    """加载单章完整摘要"""
    filepath = chapters_dir / f"ch{ch_num:04d}.md"
    if not filepath.exists():
        return f"[ch{ch_num:04d} 摘要缺失]"
    return filepath.read_text(encoding="utf-8")


def load_summaries_for_range(chapters_dir: Path, ch_start: int, ch_end: int,
                             all_chapters: list[int]) -> str:
    """加载一个范围内的全部章节摘要，拼接为字符串"""
    parts = []
    for ch in all_chapters:
        if ch_start <= ch <= ch_end:
            content = load_chapter_summary(chapters_dir, ch)
            parts.append(f"### ch{ch:04d}\n\n{content}")
    return "\n\n---\n\n".join(parts)


# ============================================================
# 分段计算
# ============================================================

def compute_segments(all_chapters: list[int], segment_size: int) -> list[dict]:
    """将章节列表切分为若干段"""
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
# Claude 调用
# ============================================================

def run_claude_prompt(prompt: str, model: str, timeout: int,
                      allow_tools: str = "",
                      verbose: bool = True) -> tuple[bool, str]:
    """调用 claude -p 并返回 (成功, stdout 输出)

    prompt 通过 stdin 管道传入，避免命令行参数长度限制。
    verbose=True 时 stderr 直接输出到终端；False 时捕获全部输出（适合并发）。
    """
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
# 阶段 1：分段扫描
# ============================================================

def build_segment_prompt(segment: dict, full_index: str,
                         segment_summaries: str) -> str:
    """构建分段扫描的 prompt"""
    template = (PROMPTS_DIR / "plot_segment_scan.md").read_text(encoding="utf-8")
    prompt = template.replace("{segment_range}", segment["range"])
    prompt = prompt.replace("{start_ch}", f"ch{segment['start_ch']:04d}")
    prompt = prompt.replace("{end_ch}", f"ch{segment['end_ch']:04d}")
    prompt = prompt.replace("{full_index}", full_index)
    prompt = prompt.replace("{segment_summaries}", segment_summaries)
    prompt = prompt.replace("{segment_num}", str(segment["num"]))
    return prompt


def run_segment_scan(segment: dict, chapters_dir: Path, full_index: str,
                     segments_dir: Path, all_chapters: list[int],
                     model: str, timeout: int,
                     verbose: bool = True) -> tuple[bool, str]:
    """运行单段扫描"""
    seg_num = segment["num"]
    output_path = segments_dir / f"segment_{seg_num:02d}.json"

    # 加载本段摘要
    summaries = load_summaries_for_range(
        chapters_dir, segment["start_ch"], segment["end_ch"], all_chapters
    )

    # 构建 prompt
    prompt = build_segment_prompt(segment, full_index, summaries)

    # 调用 Claude（不需要工具，直接输出 JSON）
    success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
    if not success:
        return False, output

    # 解析 JSON
    debug_path = segments_dir / f"segment_{seg_num:02d}_json_debug.txt"
    data = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
    if data is None:
        return False, f"无法解析 JSON 输出，调试信息已保存到: {debug_path}"

    # 保存
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return True, f"已保存: {output_path}"


def phase_segment_scan(book_dir: Path, chapters_dir: Path, all_chapters: list[int],
                       progress: dict, progress_path: Path,
                       segment_size: int, model: str, timeout: int,
                       dry_run: bool, concurrency: int = 1):
    """执行阶段 1：分段扫描（支持并发）"""
    print("\n" + "=" * 60)
    print("阶段 1：分段扫描")
    print("=" * 60)

    segments = compute_segments(all_chapters, segment_size)
    segments_dir = get_segments_dir(book_dir)
    full_index = load_full_index(chapters_dir)

    progress["segment_scan"]["total_segments"] = len(segments)
    progress["segment_scan"]["segment_size"] = segment_size

    completed = set(progress["segment_scan"]["completed"])
    pending = [s for s in segments if s["num"] not in completed]

    print(f"总段数: {len(segments)} (每段 {segment_size} 章)")
    print(f"已完成: {len(completed)}, 待处理: {len(pending)}")

    if not pending:
        print("所有段已完成！")
        return

    if dry_run:
        for seg in pending:
            print(f"  段 {seg['num']}/{len(segments)}: {seg['range']} ({len(seg['chapters'])} 章)")
        return

    concurrency = max(1, concurrency)
    print_lock = threading.Lock()
    done_counter = {"n": 0, "total": len(pending)}

    def process_one_segment(seg: dict, verbose: bool = True) -> dict:
        seg_label = f"段 {seg['num']}/{len(segments)}: {seg['range']}"
        with print_lock:
            print(f"\n--- {seg_label} 开始 ---")

        start_time = time.time()
        success, msg = run_segment_scan(
            seg, chapters_dir, full_index, segments_dir,
            all_chapters, model, timeout, verbose=verbose
        )
        elapsed = time.time() - start_time

        # 带锁更新进度到磁盘
        lock_path = progress_path.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = _read_progress_raw(progress_path)
                disk["stats"]["total_calls"] = disk["stats"].get("total_calls", 0) + 1
                disk["stats"]["total_time_seconds"] = disk["stats"].get("total_time_seconds", 0) + int(elapsed)
                if success:
                    if seg["num"] not in disk["segment_scan"]["completed"]:
                        disk["segment_scan"]["completed"].append(seg["num"])
                    if seg["num"] in disk["segment_scan"]["failed"]:
                        disk["segment_scan"]["failed"].remove(seg["num"])
                else:
                    if seg["num"] not in disk["segment_scan"]["failed"]:
                        disk["segment_scan"]["failed"].append(seg["num"])
                # 检查是否全部完成
                if len(disk["segment_scan"]["completed"]) == len(segments):
                    disk["phase"] = "global_merge"
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
                # 同步回内存
                progress.update(disk)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        with print_lock:
            done_counter["n"] += 1
            status = "✓" if success else "✗"
            print(f"  [{seg_label}] {status} ({elapsed:.0f}s): {msg}  [{done_counter['n']}/{done_counter['total']}]")

        return {"seg": seg["num"], "success": success, "elapsed": elapsed}

    if concurrency == 1:
        for seg in pending:
            process_one_segment(seg)
    else:
        print(f"并发数: {concurrency}")
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process_one_segment, seg, verbose=False): seg for seg in pending}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    with print_lock:
                        print(f"  ✗ 线程异常: {e}")

    # 重新读取最终状态
    final = load_progress(progress_path)
    completed_count = len(final["segment_scan"]["completed"])
    if completed_count == len(segments):
        print("\n分段扫描全部完成！进入全局融合阶段。")
    else:
        failed_count = len(final["segment_scan"].get("failed", []))
        print(f"\n分段扫描: {completed_count}/{len(segments)} 完成, {failed_count} 失败")


# ============================================================
# 阶段 2：全局融合
# ============================================================

def load_all_segments(segments_dir: Path) -> list[dict]:
    """加载所有段级 JSON"""
    segments = []
    for f in sorted(segments_dir.glob("segment_*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            segments.append(json.load(fh))
    return segments


def compute_aux_signals(segments: list[dict]) -> str:
    """从段级数据中预计算辅助信号"""
    lines = []

    # 汇总所有 high significance 事件
    lines.append("### 高重要性事件汇总")
    for seg in segments:
        for evt in seg.get("major_events", []):
            if evt.get("significance") == "high":
                lines.append(f"- {evt['ch']}: {evt['event']}")

    # 汇总主线状态变化点
    lines.append("\n### 主线状态变化点")
    all_plot_lines = {}
    for seg in segments:
        for pl in seg.get("plot_lines_progress", []):
            name = pl["name"]
            if name not in all_plot_lines:
                all_plot_lines[name] = []
            all_plot_lines[name].append({
                "segment": seg["segment"],
                "range": seg["range"],
                "status": pl["status"],
                "progress": pl.get("progress", ""),
            })
    for name, entries in all_plot_lines.items():
        lines.append(f"\n**{name}**:")
        for e in entries:
            lines.append(f"- 段{e['segment']} ({e['range']}): [{e['status']}] {e['progress']}")

    # 汇总弧候选
    lines.append("\n### 弧候选汇总")
    for seg in segments:
        for arc in seg.get("arc_candidates", []):
            lines.append(
                f"- 段{seg['segment']}: {arc['name']} ({arc['start_ch']}-{arc['end_ch']}) "
                f"— {arc.get('conflict', '')}"
            )

    return "\n".join(lines)


def build_merge_prompt(full_index: str, all_segments: list[dict],
                       aux_signals: str, book_dir: Path,
                       last_ch: int) -> str:
    """构建全局融合的 prompt"""
    template = (PROMPTS_DIR / "plot_global_merge.md").read_text(encoding="utf-8")

    # 生成路径占位符
    outline_dir = book_dir / "plot" / "outline"
    plot_dir = book_dir / "plot"
    timeline_dir = book_dir / "plot" / "timeline"

    prompt = template.replace("{full_index}", full_index)
    prompt = prompt.replace("{all_segments_json}",
                            json.dumps(all_segments, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{aux_signals}", aux_signals)
    prompt = prompt.replace("{plot_lines_path}", str(outline_dir / "plot_lines.md"))
    prompt = prompt.replace("{arc_dir}", str(outline_dir))
    prompt = prompt.replace("{open_loops_path}", str(plot_dir / "open_loops.md"))
    prompt = prompt.replace("{timeline_path}", str(timeline_dir / "index.md"))
    prompt = prompt.replace("{outline_index_path}", str(outline_dir / "index.md"))
    prompt = prompt.replace("{plot_index_path}", str(plot_dir / "index.md"))
    prompt = prompt.replace("{last_ch}", f"{last_ch:04d}")

    return prompt


def phase_global_merge(book_dir: Path, chapters_dir: Path, all_chapters: list[int],
                       progress: dict, progress_path: Path,
                       model: str, timeout: int, dry_run: bool):
    """执行阶段 2：全局融合"""
    print("\n" + "=" * 60)
    print("阶段 2：全局融合")
    print("=" * 60)

    if progress["global_merge"]["status"] == "completed":
        print("全局融合已完成！")
        return

    segments_dir = get_segments_dir(book_dir)
    all_segments = load_all_segments(segments_dir)

    if not all_segments:
        print("错误：没有段级分析结果。请先运行阶段 1。")
        return

    print(f"加载了 {len(all_segments)} 个段级分析结果")

    full_index = load_full_index(chapters_dir)
    aux_signals = compute_aux_signals(all_segments)
    last_ch = all_chapters[-1]

    if dry_run:
        print(f"将生成: plot_lines.md, arc_XX.md, open_loops.md, timeline/index.md, outline/index.md, plot/index.md")
        print(f"辅助信号预览:\n{aux_signals[:500]}...")
        return

    prompt = build_merge_prompt(full_index, all_segments, aux_signals, book_dir, last_ch)

    print("调用 Claude 生成 6 类产出文件...")
    start_time = time.time()
    # 需要 Write 工具来生成文件
    success, output = run_claude_prompt(prompt, model, timeout * 2, allow_tools="Write")
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    if success:
        print(f"完成 ({elapsed:.0f}s)")

        # 检查产出文件
        outline_dir = get_outline_dir(book_dir)
        arc_files = sorted(outline_dir.glob("arc_*.md"))
        arc_count = len(arc_files)

        progress["global_merge"]["status"] = "completed"
        progress["global_merge"]["arc_count"] = arc_count
        progress["phase"] = "refine"
        save_progress(progress_path, progress)

        print(f"生成了 {arc_count} 个弧文件")

        # 列出生成的文件
        expected_files = [
            outline_dir / "plot_lines.md",
            outline_dir / "index.md",
            book_dir / "plot" / "open_loops.md",
            book_dir / "plot" / "timeline" / "index.md",
            book_dir / "plot" / "index.md",
        ]
        for f in expected_files:
            status = "OK" if f.exists() else "缺失"
            print(f"  [{status}] {f.relative_to(book_dir)}")
        for f in arc_files:
            print(f"  [OK] {f.relative_to(book_dir)}")
    else:
        print(f"失败 ({elapsed:.0f}s): {output}")

    save_progress(progress_path, progress)


# ============================================================
# 阶段 3：精修验证
# ============================================================

def load_arc_boundaries(outline_dir: Path) -> list[dict]:
    """从弧文件中解析边界信息"""
    arcs = []
    for f in sorted(outline_dir.glob("arc_*.md")):
        m = re.match(r"arc_(\d+)\.md$", f.name)
        if not m:
            continue
        arc_num = int(m.group(1))
        content = f.read_text(encoding="utf-8")

        # 解析标题
        title_match = re.match(r"# 弧 \d+:\s*(.+)", content)
        name = title_match.group(1).strip() if title_match else f"弧 {arc_num}"

        # 解析章节范围
        range_match = re.search(
            r"\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)", content
        )
        if range_match:
            start_ch = int(range_match.group(1))
            end_ch = int(range_match.group(2))
        else:
            start_ch = 0
            end_ch = 0

        arcs.append({
            "num": arc_num,
            "name": name,
            "start_ch": start_ch,
            "end_ch": end_ch,
            "file": str(f),
        })

    return arcs


def phase_refine_boundary(book_dir: Path, chapters_dir: Path,
                          all_chapters: list[int],
                          progress: dict, progress_path: Path,
                          model: str, timeout: int):
    """阶段 3a：弧边界验证"""
    print("\n--- 3a: 弧边界验证 ---")

    if progress["refine"]["arc_boundary_validated"]:
        print("弧边界已验证！")
        return

    outline_dir = get_outline_dir(book_dir)
    arcs = load_arc_boundaries(outline_dir)
    if not arcs:
        print("错误：未找到弧文件")
        return

    print(f"找到 {len(arcs)} 个弧")

    # 精简版索引：只保留章节号+标题，不含摘要（减少 token 用量）
    full_index_raw = load_full_index(chapters_dir)
    compact_lines = []
    for line in full_index_raw.splitlines():
        if line.startswith("| ch"):
            parts = line.split("|")
            if len(parts) >= 4:
                compact_lines.append(f"| {parts[1].strip()} | {parts[2].strip()} |")
        elif line.startswith("| 编号"):
            compact_lines.append("| 编号 | 标题 |")
        elif line.startswith("|---"):
            compact_lines.append("|------|------|")
        else:
            compact_lines.append(line)
    full_index = "\n".join(compact_lines)

    # 加载边界附近的章节摘要（±2 章，避免 prompt 过大）
    boundary_chapters = set()
    for arc in arcs:
        for ch in all_chapters:
            if abs(ch - arc["start_ch"]) <= 2 or abs(ch - arc["end_ch"]) <= 2:
                boundary_chapters.add(ch)
    boundary_chapters = sorted(boundary_chapters)

    boundary_summaries_parts = []
    for ch in boundary_chapters:
        content = load_chapter_summary(chapters_dir, ch)
        boundary_summaries_parts.append(f"### ch{ch:04d}\n\n{content}")
    boundary_summaries = "\n\n---\n\n".join(boundary_summaries_parts)

    # 格式化弧边界
    arc_boundaries_text = "\n".join(
        f"- 弧 {a['num']}: {a['name']} (ch{a['start_ch']:04d} - ch{a['end_ch']:04d})"
        for a in arcs
    )

    # 构建 prompt
    template = (PROMPTS_DIR / "plot_arc_boundary_validate.md").read_text(encoding="utf-8")
    prompt = template.replace("{arc_boundaries}", arc_boundaries_text)
    prompt = prompt.replace("{full_index}", full_index)
    prompt = prompt.replace("{boundary_summaries}", boundary_summaries)
    print(f"prompt 大小: {len(prompt)} 字符 ({len(prompt.encode('utf-8')) / 1024:.0f} KB)")

    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout)
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    debug_path = get_segments_dir(book_dir) / "boundary_validation_raw.txt"

    if not success:
        print(f"失败 ({elapsed:.0f}s): {output}")
        # 尝试从已有的 raw output 恢复
        if debug_path.exists():
            print("尝试从已有的 raw output 恢复...")
            output = debug_path.read_text(encoding="utf-8")
        else:
            save_progress(progress_path, progress)
            return

    # 解析验证结果
    validation = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
    if validation is None:
        print(f"无法解析验证结果 JSON")
        print(f"调试信息已保存: {debug_path}")
        save_progress(progress_path, progress)
        return

    # 保存验证结果
    result_path = get_segments_dir(book_dir) / "boundary_validation.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(validation, f, ensure_ascii=False, indent=2)

    # 应用边界调整
    adjustments = 0
    for v in validation.get("validations", []):
        if v.get("start_adjusted") or v.get("end_adjusted"):
            arc_num = v["arc"]
            arc_file = outline_dir / f"arc_{arc_num:02d}.md"
            if arc_file.exists():
                content = arc_file.read_text(encoding="utf-8")
                old_start = v["original_start"]
                new_start = v["validated_start"]
                old_end = v["original_end"]
                new_end = v["validated_end"]

                if v.get("start_adjusted"):
                    content = content.replace(old_start, new_start, 1)
                    adjustments += 1
                if v.get("end_adjusted"):
                    content = content.replace(old_end, new_end, 1)
                    adjustments += 1

                arc_file.write_text(content, encoding="utf-8")

    # 应用弧名称调整
    for sug in validation.get("suggested_arc_names", []):
        arc_num = sug["arc"]
        arc_file = outline_dir / f"arc_{arc_num:02d}.md"
        if arc_file.exists() and sug.get("suggested_name"):
            content = arc_file.read_text(encoding="utf-8")
            content = content.replace(sug["original_name"], sug["suggested_name"], 1)
            arc_file.write_text(content, encoding="utf-8")

    issues = validation.get("issues", [])
    print(f"完成 ({elapsed:.0f}s): {adjustments} 处边界调整, {len(issues)} 个问题")
    if issues:
        for issue in issues:
            print(f"  问题: {issue}")

    progress["refine"]["arc_boundary_validated"] = True
    save_progress(progress_path, progress)


def phase_refine_arc_details(book_dir: Path, chapters_dir: Path,
                             all_chapters: list[int],
                             progress: dict, progress_path: Path,
                             model: str, timeout: int):
    """阶段 3b：弧内容充实"""
    print("\n--- 3b: 弧内容充实 ---")

    outline_dir = get_outline_dir(book_dir)
    arcs = load_arc_boundaries(outline_dir)
    if not arcs:
        print("错误：未找到弧文件")
        return

    completed = set(progress["refine"]["arc_details_completed"])
    pending = [a for a in arcs if a["num"] not in completed]

    print(f"总弧数: {len(arcs)}, 已完成: {len(completed)}, 待处理: {len(pending)}")

    if not pending:
        print("所有弧已充实完成！")
        return

    # 加载主线摘要
    plot_lines_path = outline_dir / "plot_lines.md"
    plot_lines_summary = ""
    if plot_lines_path.exists():
        plot_lines_summary = plot_lines_path.read_text(encoding="utf-8")

    # 构建其他弧简述
    other_arcs_parts = []
    for a in arcs:
        other_arcs_parts.append(
            f"- 弧 {a['num']}: {a['name']} (ch{a['start_ch']:04d}-ch{a['end_ch']:04d})"
        )
    other_arcs_summary = "\n".join(other_arcs_parts)

    template = (PROMPTS_DIR / "plot_arc_detail.md").read_text(encoding="utf-8")

    for arc in pending:
        print(f"\n  弧 {arc['num']}: {arc['name']} (ch{arc['start_ch']:04d}-ch{arc['end_ch']:04d})")

        # 加载弧内全部章节摘要
        arc_summaries = load_summaries_for_range(
            chapters_dir, arc["start_ch"], arc["end_ch"], all_chapters
        )

        arc_chapter_count = len([
            ch for ch in all_chapters
            if arc["start_ch"] <= ch <= arc["end_ch"]
        ])

        # 构建 prompt
        prompt = template.replace("{arc_num}", str(arc["num"]))
        prompt = prompt.replace("{arc_num_padded}", f"{arc['num']:02d}")
        prompt = prompt.replace("{arc_name}", arc["name"])
        prompt = prompt.replace("{arc_start}", f"ch{arc['start_ch']:04d}")
        prompt = prompt.replace("{arc_end}", f"ch{arc['end_ch']:04d}")
        prompt = prompt.replace("{arc_chapter_count}", str(arc_chapter_count))
        prompt = prompt.replace("{plot_lines_summary}", plot_lines_summary)
        prompt = prompt.replace("{other_arcs_summary}", other_arcs_summary)
        prompt = prompt.replace("{arc_chapter_summaries}", arc_summaries)
        prompt = prompt.replace("{arc_file_path}", arc["file"])

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write,Read")
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            print(f"    完成 ({elapsed:.0f}s)")
            progress["refine"]["arc_details_completed"].append(arc["num"])
            if arc["num"] in progress["refine"]["arc_details_failed"]:
                progress["refine"]["arc_details_failed"].remove(arc["num"])
        else:
            print(f"    失败 ({elapsed:.0f}s): {output[:200]}")
            if arc["num"] not in progress["refine"]["arc_details_failed"]:
                progress["refine"]["arc_details_failed"].append(arc["num"])

        save_progress(progress_path, progress)


def phase_refine_foreshadow(book_dir: Path, chapters_dir: Path,
                            all_chapters: list[int],
                            progress: dict, progress_path: Path,
                            model: str, timeout: int):
    """阶段 3c：伏笔交叉验证"""
    print("\n--- 3c: 伏笔交叉验证 ---")

    if progress["refine"]["foreshadow_validated"]:
        print("伏笔已验证！")
        return

    # 加载当前伏笔文件
    open_loops_path = book_dir / "plot" / "open_loops.md"
    if not open_loops_path.exists():
        print("错误：open_loops.md 不存在，跳过伏笔验证")
        progress["refine"]["foreshadow_validated"] = True
        save_progress(progress_path, progress)
        return

    open_loops_content = open_loops_path.read_text(encoding="utf-8")

    # 从中提取分类内容
    resolved = ""
    unresolved_high = ""
    unresolved_low = ""

    sections = re.split(r"\n## \d+\.\s+", open_loops_content)
    for section in sections:
        if "已回收" in section[:20]:
            resolved = section
        elif "未回收" in section[:20] and "高" in section[:30]:
            unresolved_high = section
        elif "未回收" in section[:20]:
            unresolved_low = section

    # 加载段扫描的原始伏笔数据
    segments_dir = get_segments_dir(book_dir)
    all_segments = load_all_segments(segments_dir)
    segment_foreshadowing_parts = []
    for seg in all_segments:
        fs = seg.get("foreshadowing", {})
        if fs.get("planted") or fs.get("resolved"):
            segment_foreshadowing_parts.append(
                f"### 段 {seg['segment']} ({seg['range']})\n"
                f"埋设: {json.dumps(fs.get('planted', []), ensure_ascii=False)}\n"
                f"回收: {json.dumps(fs.get('resolved', []), ensure_ascii=False)}"
            )
    segment_foreshadowing = "\n\n".join(segment_foreshadowing_parts)

    # 收集需要验证的章节
    foreshadow_chapters = set()
    for seg in all_segments:
        fs = seg.get("foreshadowing", {})
        for item in fs.get("planted", []):
            ch_str = item.get("ch", "")
            m = re.match(r"ch(\d+)", ch_str)
            if m:
                foreshadow_chapters.add(int(m.group(1)))
        for item in fs.get("resolved", []):
            for key in ("planted_ch", "resolved_ch"):
                ch_str = item.get(key, "")
                m = re.match(r"ch(\d+)", ch_str)
                if m:
                    foreshadow_chapters.add(int(m.group(1)))

    # 限制验证章节数量（太多会超出上下文）
    foreshadow_chapters = sorted(foreshadow_chapters)
    if len(foreshadow_chapters) > 100:
        # 只验证 high importance 的
        high_chapters = set()
        for seg in all_segments:
            for item in seg.get("foreshadowing", {}).get("planted", []):
                if item.get("importance") == "high":
                    m = re.match(r"ch(\d+)", item.get("ch", ""))
                    if m:
                        high_chapters.add(int(m.group(1)))
        foreshadow_chapters = sorted(high_chapters)[:80]

    validation_parts = []
    for ch in foreshadow_chapters:
        content = load_chapter_summary(chapters_dir, ch)
        validation_parts.append(f"### ch{ch:04d}\n\n{content}")
    validation_summaries = "\n\n---\n\n".join(validation_parts)

    # 构建 prompt
    template = (PROMPTS_DIR / "plot_foreshadow_validate.md").read_text(encoding="utf-8")
    prompt = template.replace("{resolved_foreshadowing}", resolved)
    prompt = prompt.replace("{unresolved_high}", unresolved_high)
    prompt = template.replace("{unresolved_low}", unresolved_low)

    # 重新构建 —— 上面逻辑有误，用完整替换
    prompt = template
    prompt = prompt.replace("{resolved_foreshadowing}", resolved)
    prompt = prompt.replace("{unresolved_high}", unresolved_high)
    prompt = prompt.replace("{unresolved_low}", unresolved_low)
    prompt = prompt.replace("{segment_foreshadowing}", segment_foreshadowing)
    prompt = prompt.replace("{validation_summaries}", validation_summaries)

    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout)
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    if not success:
        print(f"失败 ({elapsed:.0f}s): {output[:200]}")
        save_progress(progress_path, progress)
        return

    # 解析结果
    debug_path = segments_dir / "foreshadow_validation_json_debug.txt"
    corrections = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
    if corrections is None:
        print("无法解析验证结果")
        print(f"调试信息已保存: {debug_path}")
    else:
        result_path = segments_dir / "foreshadow_validation.json"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(corrections, f, ensure_ascii=False, indent=2)

        n_corrections = len(corrections.get("corrections", []))
        n_additions = len(corrections.get("additions", []))
        n_removals = len(corrections.get("removals", []))
        print(f"完成 ({elapsed:.0f}s): {n_corrections} 重分类, {n_additions} 补充, {n_removals} 移除")

    progress["refine"]["foreshadow_validated"] = True
    save_progress(progress_path, progress)


def phase_refine_final_update(book_dir: Path, chapters_dir: Path,
                              all_chapters: list[int],
                              progress: dict, progress_path: Path,
                              model: str, timeout: int):
    """阶段 3d：最终更新"""
    print("\n--- 3d: 最终更新 ---")

    if progress["refine"]["final_updated"]:
        print("最终更新已完成！")
        return

    outline_dir = get_outline_dir(book_dir)
    segments_dir = get_segments_dir(book_dir)

    # 加载精修后的弧信息
    arcs = load_arc_boundaries(outline_dir)
    validated_arcs = "\n".join(
        f"- 弧 {a['num']}: {a['name']} (ch{a['start_ch']:04d}-ch{a['end_ch']:04d})"
        for a in arcs
    )

    # 加载伏笔验证结果
    foreshadow_path = segments_dir / "foreshadow_validation.json"
    validated_foreshadowing = ""
    if foreshadow_path.exists():
        with open(foreshadow_path, "r", encoding="utf-8") as f:
            validated_foreshadowing = json.dumps(
                json.load(f), ensure_ascii=False, indent=2
            )
    else:
        validated_foreshadowing = "（无伏笔验证数据）"

    # 加载当前主线文件
    plot_lines_path = outline_dir / "plot_lines.md"
    current_plot_lines = ""
    if plot_lines_path.exists():
        current_plot_lines = plot_lines_path.read_text(encoding="utf-8")

    # 汇总时间线事件
    all_segments = load_all_segments(segments_dir)
    timeline_parts = []
    for seg in all_segments:
        for evt in seg.get("timeline_events", []):
            timeline_parts.append(
                f"- {evt.get('ch', '?')}: [{evt.get('time_marker', '?')}] {evt.get('event', '')}"
            )
    timeline_events = "\n".join(timeline_parts) if timeline_parts else "（无时间标记事件）"

    # 构建 prompt
    template = (PROMPTS_DIR / "plot_final_update.md").read_text(encoding="utf-8")
    prompt = template.replace("{validated_arcs}", validated_arcs)
    prompt = prompt.replace("{validated_foreshadowing}", validated_foreshadowing)
    prompt = prompt.replace("{current_plot_lines}", current_plot_lines)
    prompt = prompt.replace("{timeline_events}", timeline_events)
    prompt = prompt.replace("{plot_lines_path}", str(outline_dir / "plot_lines.md"))
    prompt = prompt.replace("{open_loops_path}", str(book_dir / "plot" / "open_loops.md"))
    prompt = prompt.replace("{timeline_path}", str(book_dir / "plot" / "timeline" / "index.md"))
    prompt = prompt.replace("{outline_index_path}", str(outline_dir / "index.md"))
    prompt = prompt.replace("{plot_index_path}", str(book_dir / "plot" / "index.md"))

    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout * 2, allow_tools="Write,Read")
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    if success:
        print(f"完成 ({elapsed:.0f}s)")
        progress["refine"]["final_updated"] = True
    else:
        print(f"失败 ({elapsed:.0f}s): {output[:200]}")

    save_progress(progress_path, progress)


def phase_refine(book_dir: Path, chapters_dir: Path, all_chapters: list[int],
                 progress: dict, progress_path: Path,
                 model: str, timeout: int, dry_run: bool):
    """执行阶段 3：精修验证"""
    print("\n" + "=" * 60)
    print("阶段 3：精修验证")
    print("=" * 60)

    outline_dir = get_outline_dir(book_dir)
    arcs = load_arc_boundaries(outline_dir)

    if dry_run:
        print(f"3a: 弧边界验证 — {'已完成' if progress['refine']['arc_boundary_validated'] else '待处理'}")
        completed_details = len(progress["refine"]["arc_details_completed"])
        print(f"3b: 弧内容充实 — {completed_details}/{len(arcs)} 完成")
        print(f"3c: 伏笔验证 — {'已完成' if progress['refine']['foreshadow_validated'] else '待处理'}")
        print(f"3d: 最终更新 — {'已完成' if progress['refine']['final_updated'] else '待处理'}")
        return

    # 3a: 弧边界验证
    phase_refine_boundary(book_dir, chapters_dir, all_chapters,
                          progress, progress_path, model, timeout)

    # 3b: 弧内容充实
    phase_refine_arc_details(book_dir, chapters_dir, all_chapters,
                             progress, progress_path, model, timeout)

    # 3c: 伏笔交叉验证
    phase_refine_foreshadow(book_dir, chapters_dir, all_chapters,
                            progress, progress_path, model, timeout)

    # 3d: 最终更新
    phase_refine_final_update(book_dir, chapters_dir, all_chapters,
                              progress, progress_path, model, timeout)


# ============================================================
# 验证
# ============================================================

def run_validate(book_dir: Path):
    """验证所有产出文件"""
    print("\n验证 T3 产出文件...")

    outline_dir = book_dir / "plot" / "outline"
    plot_dir = book_dir / "plot"
    timeline_dir = book_dir / "plot" / "timeline"

    results = []

    # 检查必要文件
    required_files = [
        (outline_dir / "plot_lines.md", "主线追踪"),
        (outline_dir / "index.md", "大纲概览"),
        (plot_dir / "open_loops.md", "伏笔汇总"),
        (timeline_dir / "index.md", "时间线"),
        (plot_dir / "index.md", "剧情层导航"),
    ]

    for filepath, desc in required_files:
        if filepath.exists():
            size = filepath.stat().st_size
            results.append((desc, "OK", f"{size} bytes"))
        else:
            results.append((desc, "缺失", ""))

    # 检查弧文件
    arc_files = sorted(outline_dir.glob("arc_*.md"))
    if arc_files:
        # 验证弧覆盖
        arcs = load_arc_boundaries(outline_dir)
        if arcs:
            # 检查首尾相接
            gaps = []
            overlaps = []
            for i in range(len(arcs) - 1):
                curr_end = arcs[i]["end_ch"]
                next_start = arcs[i + 1]["start_ch"]
                if next_start > curr_end + 1:
                    gaps.append(f"ch{curr_end + 1:04d}-ch{next_start - 1:04d}")
                elif next_start <= curr_end:
                    overlaps.append(f"弧{arcs[i]['num']}和弧{arcs[i + 1]['num']}")

            results.append(("弧文件", "OK", f"{len(arc_files)} 个弧"))
            if gaps:
                results.append(("弧覆盖-间隙", "警告", f"遗漏: {', '.join(gaps)}"))
            if overlaps:
                results.append(("弧覆盖-重叠", "警告", f"重叠: {', '.join(overlaps)}"))
        else:
            results.append(("弧文件", "警告", "无法解析弧边界"))
    else:
        results.append(("弧文件", "缺失", ""))

    # 输出结果
    print(f"\n{'文件':<20} {'状态':<6} {'详情'}")
    print("-" * 60)
    for name, status, detail in results:
        print(f"{name:<20} {status:<6} {detail}")

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
    chapters_dir = get_chapters_dir(book_dir)
    progress_path = get_progress_path(book_dir)

    # 扫描已有章节摘要
    all_chapters = list_summary_chapters(chapters_dir)
    if not all_chapters:
        print(f"错误：chapters 目录中没有摘要文件: {chapters_dir}")
        sys.exit(1)

    # 检查 index.md
    index_path = chapters_dir / "index.md"
    if not index_path.exists():
        print(f"错误：index.md 不存在: {index_path}")
        print("请先运行: python batch_summary.py --book-dir ... --gen-index")
        sys.exit(1)

    print(f"知识库: {book_dir}")
    print(f"章节摘要: {len(all_chapters)} 章 "
          f"(ch{all_chapters[0]:04d} ~ ch{all_chapters[-1]:04d})")

    # 验证模式
    if args.validate:
        run_validate(book_dir)
        return

    # --reset: 清除旧进度和产出
    if args.reset:
        import shutil
        outline_dir = book_dir / "plot" / "outline"
        segments_dir = outline_dir / ".segments"
        for f in outline_dir.glob("*.md"):
            f.unlink()
            print(f"  删除: {f.name}")
        if segments_dir.exists():
            shutil.rmtree(segments_dir)
            print(f"  删除: .segments/")
        if progress_path.exists():
            progress_path.unlink()
            print(f"  删除: .progress.json")
        lock_path = progress_path.with_suffix(".lock")
        if lock_path.exists():
            lock_path.unlink()
        print("已清除 T3 所有产出，从头开始。\n")

    # 加载进度
    progress = load_progress(progress_path)
    print(f"当前阶段: {progress['phase']}")

    # 确保输出目录存在
    get_outline_dir(book_dir)
    get_segments_dir(book_dir)
    get_timeline_dir(book_dir)

    if args.dry_run:
        print("\n=== 试运行模式 ===")

    # 根据 --phase 或自动确定要运行的阶段
    if args.phase == "segment-scan" or (not args.phase and progress["phase"] == "segment_scan"):
        phase_segment_scan(
            book_dir, chapters_dir, all_chapters,
            progress, progress_path,
            args.segment_size, args.model, args.timeout,
            args.dry_run, args.concurrency,
        )

    if args.phase == "global-merge" or (not args.phase and progress["phase"] in ("global_merge", "segment_scan")):
        # 如果是自动模式且刚完成 segment_scan，重新加载进度
        progress = load_progress(progress_path)
        if progress["phase"] == "global_merge" or args.phase == "global-merge":
            phase_global_merge(
                book_dir, chapters_dir, all_chapters,
                progress, progress_path,
                args.model, args.timeout, args.dry_run,
            )

    if args.phase == "refine" or (not args.phase and progress["phase"] in ("refine", "global_merge")):
        progress = load_progress(progress_path)
        if progress["phase"] == "refine" or args.phase == "refine":
            phase_refine(
                book_dir, chapters_dir, all_chapters,
                progress, progress_path,
                args.model, args.timeout, args.dry_run,
            )

    # 最终统计
    progress = load_progress(progress_path)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    seg = progress["segment_scan"]
    print(f"  分段扫描: {len(seg['completed'])}/{seg['total_segments']} 完成")
    print(f"  全局融合: {progress['global_merge']['status']}")
    ref = progress["refine"]
    print(f"  精修: 边界{'✓' if ref['arc_boundary_validated'] else '✗'} "
          f"弧详情 {len(ref['arc_details_completed'])}/{progress['global_merge'].get('arc_count', '?')} "
          f"伏笔{'✓' if ref['foreshadow_validated'] else '✗'} "
          f"最终{'✓' if ref['final_updated'] else '✗'}")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    if progress["refine"]["final_updated"]:
        print("\nT3 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
