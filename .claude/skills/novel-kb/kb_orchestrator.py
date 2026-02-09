#!/usr/bin/env python3
"""
KB 构建编排脚本

统一入口，管理 T1-T7 知识库构建任务的依赖检查、进度追踪和分发调用。

用法：
    # 查看知识库构建进度
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage status

    # T1：原始数据处理（需要 --input 指定清洗后的 JSON 目录）
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage t1 --input 清洗后JSON目录

    # 运行单个阶段
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage t2
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage t3 --phase segment-scan
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage t7 --dry-run

    # 运行全部阶段（按依赖顺序，T1 除外）
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage all

    # 验证某阶段产出
    python kb_orchestrator.py --book-dir novel_kb/玄鉴仙族 --stage t3 --validate
"""

import argparse
import subprocess
import sys
import time
import threading
from pathlib import Path


def print_flush(msg: str, end: str = "\n"):
    """带 flush 的 print，确保实时输出"""
    print(msg, end=end, flush=True)


def show_waiting_animation(stop_event: threading.Event, message: str = "处理中"):
    """在后台线程显示等待动画（仅在 TTY 中显示）"""
    # 检查是否在交互式终端中运行
    if not sys.stdout.isatty():
        return  # 输出被重定向时不显示动画

    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    idx = 0
    while not stop_event.is_set():
        print_flush(f"\r{chars[idx]} {message}...", end="")
        idx = (idx + 1) % len(chars)
        time.sleep(0.1)
    # 清除动画行
    print_flush("\r" + " " * (len(message) + 10) + "\r", end="")

# 脚本所在目录
KB_DIR = Path(__file__).parent


def fmt_duration(seconds: float) -> str:
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"

# 阶段映射：stage → (脚本名, 中文名, 支持 --phase)
STAGES = {
    "t1": ("process_chapters.py", "原始数据处理", False),
    "t2": ("batch_summary.py", "章节摘要", False),
    "t3": ("batch_plot.py", "剧情层", True),
    "t4": ("batch_character.py", "角色层", True),
    "t5": ("batch_world.py", "世界层", True),
    "t6": ("batch_reader.py", "读者层", True),
    "t7": ("batch_style.py", "风格层", True),
}

# --stage all 的执行顺序（T1 需要额外 --input 参数，不纳入 all）
EXEC_ORDER = ["t2", "t6", "t3", "t4", "t5", "t7"]


# ============================================================
# 依赖检查
# ============================================================

def check_dependency(book_dir: Path, stage: str, input_dir: str | None = None) -> tuple[bool, str]:
    """检查某个 stage 的前置条件是否满足。返回 (ok, message)"""
    if stage == "t1":
        if not input_dir:
            return False, "T1 需要 --input 参数指定清洗后的 JSON 目录"
        input_path = Path(input_dir)
        if not input_path.exists():
            return False, f"输入目录不存在: {input_path}"
        json_files = list(input_path.glob("*.json"))
        if not json_files:
            return False, f"输入目录下没有 .json 文件"
        return True, f"输入目录包含 {len(json_files)} 个 JSON 文件"

    if stage == "t2":
        text_dir = book_dir / "text"
        if not text_dir.exists():
            return False, f"text/ 目录不存在: {text_dir}"
        md_files = list(text_dir.glob("*.md"))
        if not md_files:
            return False, f"text/ 目录下没有 .md 文件"
        return True, f"text/ 目录包含 {len(md_files)} 个 .md 文件"

    if stage == "t3":
        chapters_dir = book_dir / "plot" / "chapters"
        if not chapters_dir.exists():
            return False, f"plot/chapters/ 目录不存在（需要先完成 T2）"
        md_files = list(chapters_dir.glob("ch*.md"))
        if not md_files:
            return False, f"plot/chapters/ 目录下没有章节摘要文件"
        return True, f"plot/chapters/ 包含 {len(md_files)} 个摘要"

    if stage == "t4":
        chapters_dir = book_dir / "plot" / "chapters"
        outline_dir = book_dir / "plot" / "outline"
        if not chapters_dir.exists():
            return False, f"plot/chapters/ 目录不存在（需要先完成 T2）"
        if not outline_dir.exists():
            return False, f"plot/outline/ 目录不存在（需要先完成 T3）"
        arc_files = list(outline_dir.glob("arc_*.md"))
        if not arc_files:
            return False, f"plot/outline/ 下没有 arc_*.md（需要先完成 T3）"
        return True, f"plot/chapters/ 和 plot/outline/ 就绪（{len(arc_files)} 个弧文件）"

    if stage == "t5":
        chapters_dir = book_dir / "plot" / "chapters"
        outline_dir = book_dir / "plot" / "outline"
        if not chapters_dir.exists():
            return False, f"plot/chapters/ 目录不存在（需要先完成 T2）"
        if not outline_dir.exists():
            return False, f"plot/outline/ 目录不存在（需要先完成 T3）"
        arc_files = list(outline_dir.glob("arc_*.md"))
        if not arc_files:
            return False, f"plot/outline/ 下没有 arc_*.md（需要先完成 T3）"
        return True, f"plot/chapters/ 和 plot/outline/ 就绪"

    if stage == "t6":
        comments_dir = book_dir / "reader" / "comments"
        if not comments_dir.exists():
            return False, f"reader/comments/ 目录不存在"
        md_files = list(comments_dir.glob("*.md"))
        if not md_files:
            return False, f"reader/comments/ 目录下没有 .md 文件"
        return True, f"reader/comments/ 包含 {len(md_files)} 个评论文件"

    if stage == "t7":
        text_dir = book_dir / "text"
        if not text_dir.exists():
            return False, f"text/ 目录不存在"
        md_files = list(text_dir.glob("*.md"))
        if not md_files:
            return False, f"text/ 目录下没有 .md 文件"
        return True, f"text/ 目录包含 {len(md_files)} 个 .md 文件"

    return False, f"未知 stage: {stage}"


# ============================================================
# 进度查看
# ============================================================

def show_status(book_dir: Path):
    """扫描知识库目录，显示各层完成度"""
    print_flush(f"\n知识库目录: {book_dir.resolve()}\n")
    print_flush("=" * 60)

    # T1: 原始数据
    text_dir = book_dir / "text"
    comments_dir = book_dir / "reader" / "comments"
    t1_parts = []
    if text_dir.exists():
        text_count = len(list(text_dir.glob("ch*.md")))
        t1_parts.append(f"text/ {text_count} 章")
    if comments_dir.exists():
        comment_count = len(list(comments_dir.glob("ch*.md")))
        t1_parts.append(f"comments/ {comment_count} 章")
    if t1_parts:
        print_flush(f"  T1 原始数据:  已完成（{', '.join(t1_parts)}）")
    else:
        print_flush(f"  T1 原始数据:  未开始")

    # T2: 章节摘要
    chapters_dir = book_dir / "plot" / "chapters"
    text_dir = book_dir / "text"
    if chapters_dir.exists():
        ch_files = sorted(chapters_dir.glob("ch*.md"))
        total_text = len(list(text_dir.glob("ch*.md"))) if text_dir.exists() else "?"
        has_index = (chapters_dir / "index.md").exists()
        idx_mark = " + index.md" if has_index else ""
        print_flush(f"  T2 章节摘要:  {len(ch_files)}/{total_text} 已完成{idx_mark}")
    else:
        print_flush(f"  T2 章节摘要:  未开始")

    # T3: 剧情层
    outline_dir = book_dir / "plot" / "outline"
    plot_dir = book_dir / "plot"
    if outline_dir.exists():
        arc_files = sorted(outline_dir.glob("arc_*.md"))
        has_plot_lines = (outline_dir / "plot_lines.md").exists()
        has_open_loops = (plot_dir / "open_loops.md").exists()
        has_timeline = (plot_dir / "timeline" / "index.md").exists()
        parts = []
        if arc_files:
            parts.append(f"{len(arc_files)} 个弧文件")
        if has_plot_lines:
            parts.append("plot_lines.md")
        if has_open_loops:
            parts.append("open_loops.md")
        if has_timeline:
            parts.append("timeline")
        if parts:
            print_flush(f"  T3 剧情层:    已完成（{', '.join(parts)}）")
        else:
            print_flush(f"  T3 剧情层:    目录存在但无产出")
    else:
        segments_dir = plot_dir / "segments" if plot_dir.exists() else None
        if segments_dir and segments_dir.exists():
            seg_files = list(segments_dir.glob("segment_*.json"))
            print_flush(f"  T3 剧情层:    进行中（{len(seg_files)} 个段结果）")
        else:
            print_flush(f"  T3 剧情层:    未开始")

    # T4: 角色层
    char_dir = book_dir / "characters"
    if char_dir.exists():
        profiles = sorted(char_dir.glob("*.md"))
        profiles = [p for p in profiles if p.name not in ("index.md", "relationships.md")]
        has_index = (char_dir / "index.md").exists()
        has_rel = (char_dir / "relationships.md").exists()
        extras = []
        if has_index:
            extras.append("index.md")
        if has_rel:
            extras.append("relationships.md")
        extra_str = f" + {', '.join(extras)}" if extras else ""
        print_flush(f"  T4 角色层:    已完成（{len(profiles)} 个角色档案{extra_str}）")
    else:
        print_flush(f"  T4 角色层:    未开始")

    # T5: 世界层
    world_dir = book_dir / "world"
    if world_dir.exists():
        world_files = list(world_dir.glob("*.md"))
        names = [f.name for f in world_files]
        print_flush(f"  T5 世界层:    已完成（{len(world_files)} 个文件: {', '.join(sorted(names))}）")
    else:
        print_flush(f"  T5 世界层:    未开始")

    # T6: 读者层
    feedback_dir = book_dir / "reader" / "feedback"
    if feedback_dir.exists():
        fb_files = list(feedback_dir.glob("*.md"))
        names = [f.name for f in fb_files]
        print_flush(f"  T6 读者层:    已完成（{len(fb_files)} 个反馈文件: {', '.join(sorted(names))}）")
    else:
        comments_dir = book_dir / "reader" / "comments"
        if comments_dir and comments_dir.exists():
            print_flush(f"  T6 读者层:    有原始评论数据，尚未分析")
        else:
            print_flush(f"  T6 读者层:    未开始（无评论数据）")

    # T7: 风格层
    style_dir = book_dir / "style"
    if style_dir.exists():
        style_files = list(style_dir.glob("*.md"))
        has_stats = (style_dir / "stats.json").exists()
        names = [f.name for f in style_files]
        stats_str = " + stats.json" if has_stats else ""
        print_flush(f"  T7 风格层:    已完成（{len(style_files)} 个分析文件{stats_str}: {', '.join(sorted(names))}）")
    else:
        print_flush(f"  T7 风格层:    未开始")

    print_flush("=" * 60)
    print_flush("")


# ============================================================
# 分发调用
# ============================================================

def dispatch_stage(book_dir: Path, stage: str, phase: str | None,
                   model: str, timeout: int,
                   dry_run: bool, validate: bool,
                   input_dir: str | None = None,
                   min_agree: int = 15) -> int:
    """构造命令行参数，调用对应的 batch_*.py。返回退出码。"""
    script_name, stage_name, supports_phase = STAGES[stage]
    script_path = KB_DIR / script_name

    # T1 用不同的参数格式
    if stage == "t1":
        cmd = [sys.executable, str(script_path),
               "--mode", "kb",
               "--input", str(input_dir),
               "--output", str(book_dir),
               "--min-agree", str(min_agree)]
    else:
        cmd = [sys.executable, str(script_path), "--book-dir", str(book_dir)]

        if supports_phase and phase:
            cmd.extend(["--phase", phase])

        cmd.extend(["--model", model])
        cmd.extend(["--timeout", str(timeout)])

        if dry_run:
            cmd.append("--dry-run")
        if validate:
            cmd.append("--validate")

    action = "验证" if validate else ("试运行" if dry_run else "执行")
    phase_str = f" --phase {phase}" if phase else ""
    print_flush(f"\n{'='*60}")
    print_flush(f"[{stage.upper()}] {action} {stage_name}{phase_str}")
    print_flush(f"命令: {' '.join(cmd)}")
    print_flush(f"{'='*60}\n")

    result = subprocess.run(cmd)
    return result.returncode


# ============================================================
# 进度汇总
# ============================================================

def _print_all_summary(results: dict, stage_names: dict, all_start: float):
    """打印 --stage all 的汇总表"""
    total_dur = time.time() - all_start
    print_flush(f"\n{'='*60}")
    print_flush(f"  汇总（总耗时 {fmt_duration(total_dur)}）")
    print_flush(f"{'='*60}")
    for stage in EXEC_ORDER:
        name = stage_names.get(stage, "?")
        if stage in results:
            status, dur = results[stage]
            mark = {"OK": "✓", "FAIL": "✗", "SKIP": "-"}.get(status, "?")
            dur_str = fmt_duration(dur) if dur > 0 else ""
            print_flush(f"  [{mark}] {stage.upper()} {name:<8} {status:<6} {dur_str}")
        else:
            print_flush(f"  [ ] {stage.upper()} {name:<8} 未执行")
    print_flush(f"{'='*60}\n")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="KB 构建编排脚本 - 管理 T1-T7 知识库构建任务")
    parser.add_argument("--book-dir", required=True,
                        help="知识库目录路径，如 novel_kb/玄鉴仙族")
    parser.add_argument("--stage", required=True,
                        choices=["t1", "t2", "t3", "t4", "t5", "t6", "t7", "all", "status"],
                        help="要执行的阶段（t1-t7/all/status）")
    parser.add_argument("--input",
                        help="T1 专用：清洗后的 JSON 目录路径")
    parser.add_argument("--min-agree", type=int, default=15,
                        help="T1 专用：评论最低点赞数阈值（默认: 15）")
    parser.add_argument("--phase",
                        help="传递给子脚本的 --phase 参数")
    parser.add_argument("--model", default="sonnet",
                        help="Claude 模型（默认: sonnet）")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认: 600）")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行，不执行 Claude 调用")
    parser.add_argument("--validate", action="store_true",
                        help="验证产出而非执行构建")
    args = parser.parse_args()

    book_dir = Path(args.book_dir).resolve()
    if not book_dir.exists():
        print(f"错误：知识库目录不存在: {book_dir}")
        sys.exit(1)

    # 状态查看
    if args.stage == "status":
        show_status(book_dir)
        return

    # 全部执行
    if args.stage == "all":
        total = len(EXEC_ORDER)
        stage_names = {s: STAGES[s][1] for s in EXEC_ORDER}
        print_flush(f"\n按依赖顺序执行全部阶段（共 {total} 个）:")
        print_flush(f"  {' → '.join(f'{s.upper()}({stage_names[s]})' for s in EXEC_ORDER)}")
        print_flush("")

        all_start = time.time()
        results = {}  # stage → (status, duration)

        for idx, stage in enumerate(EXEC_ORDER, 1):
            # 进度头
            elapsed = time.time() - all_start
            print_flush(f"\n{'#'*60}")
            print_flush(f"  [{idx}/{total}] {stage.upper()} {stage_names[stage]}")
            if idx > 1:
                print_flush(f"  已耗时: {fmt_duration(elapsed)}")
            print_flush(f"{'#'*60}")

            ok, msg = check_dependency(book_dir, stage)
            if not ok:
                print_flush(f"\n  ✗ 依赖检查未通过: {msg}")
                results[stage] = ("SKIP", 0)
                # 打印中间汇总后退出
                _print_all_summary(results, stage_names, all_start)
                sys.exit(1)

            stage_start = time.time()
            rc = dispatch_stage(book_dir, stage, None,
                                args.model, args.timeout,
                                args.dry_run, args.validate)
            stage_dur = time.time() - stage_start

            if rc != 0:
                results[stage] = ("FAIL", stage_dur)
                print_flush(f"\n  ✗ {stage.upper()} 执行失败（退出码 {rc}），耗时 {fmt_duration(stage_dur)}")
                _print_all_summary(results, stage_names, all_start)
                sys.exit(rc)

            results[stage] = ("OK", stage_dur)
            print_flush(f"\n  ✓ {stage.upper()} 完成，耗时 {fmt_duration(stage_dur)}")

        _print_all_summary(results, stage_names, all_start)
        show_status(book_dir)
        return

    # 单个阶段
    stage = args.stage
    ok, msg = check_dependency(book_dir, stage, args.input)
    if not ok:
        print(f"\n依赖检查未通过: {msg}")
        sys.exit(1)
    print(f"依赖检查通过: {msg}")

    stage_start = time.time()
    rc = dispatch_stage(book_dir, stage, args.phase,
                        args.model, args.timeout,
                        args.dry_run, args.validate,
                        input_dir=args.input,
                        min_agree=args.min_agree)
    dur = time.time() - stage_start
    status = "完成" if rc == 0 else f"失败（退出码 {rc}）"
    print(f"\n{stage.upper()} {status}，耗时 {fmt_duration(dur)}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
