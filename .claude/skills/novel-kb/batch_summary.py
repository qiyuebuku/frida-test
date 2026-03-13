#!/usr/bin/env python3
"""
T2 章节摘要批量生成 — 批量编排脚本

从 T1 原文 (text/) 自动生成结构化章节摘要，累积角色名册。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T1 已完成: text/ 下有原文章节 (chXXXX.md)
  - book_detail.md 存在（提取小说背景和初始角色榜）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pipeline（单阶段批量处理，支持断点续传 + 并发）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  批量摘要生成  每批 N 章同时调用 Claude（默认每批 5 章）
                产出: plot/chapters/chXXXX.md（8 段结构化摘要）
                      .progress.json（已完成章节 + 累积角色名册）
                耗时: 取决于章节数，每批 1 次 AI 调用
                      支持 --concurrency M 并发 M 批加速
                      断点续跑：失败章节自动记录，可 --retry-failed 重试

  后处理        --validate 验证已生成摘要的格式完整性
                --gen-index 生成 plot/chapters/index.md 索引表

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（自动从当前进度继续）
  python batch_summary.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：并发 3 批 + 每批 5 章（适合大规模章节）
  python batch_summary.py --book-dir ... --concurrency 3 --batch-size 5

  # 指定范围处理（如调试前 10 章）
  python batch_summary.py --book-dir ... --range 1-10

  # 重试失败章节
  python batch_summary.py --book-dir ... --retry-failed

  # 验证 + 生成索引
  python batch_summary.py --book-dir ... --validate
  python batch_summary.py --book-dir ... --gen-index

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH       知识库目录（必需）
  --batch-size N        每批处理章节数（默认 5）
  --concurrency M       并发批次数（默认 1，建议 2-4）
  --range START-END     处理章节范围，如 1-50
  --retry-failed        只重试失败章节
  --validate            验证已生成摘要的格式
  --gen-index           生成 index.md 索引表
  --model MODEL         Claude 模型（默认 sonnet）
  --timeout SEC         单批超时秒数（默认 600）
  --dry-run             试运行，不执行 Claude 调用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    plot/
      chapters/
        ch0001.md         8 段结构化摘要（关键事件、角色、关系、物品、设定、伏笔）
        ch0002.md
        ...
        index.md          章节索引（编号、标题、一句话摘要）
        .progress.json    进度文件（已完成章节、累积角色名册、统计信息）
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kb_common import (
    SKILL_DIR, PROMPTS_DIR,
    resolve_book_dir, load_progress, save_progress, merge_stats,
    list_chapter_files,
    print_flush, show_waiting_animation,
)

PROMPT_TEMPLATE_PATH = PROMPTS_DIR / "summary_prompt.md"

# 摘要必需的 8 个段落标题
REQUIRED_SECTIONS = [
    "## 关键事件",
    "## 新登场角色",
    "## 已有角色出场",
    "## 关系变化",
    "## 重要物品",
    "## 新增设定",
    "## 伏笔/悬念",
    "## 一句话摘要",
]


def parse_args():
    parser = argparse.ArgumentParser(description="章节摘要批量生成")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径，如 novel_kb/玄鉴仙族")
    parser.add_argument("--batch-size", type=int, default=5, help="每批处理章节数（默认 5）")
    parser.add_argument("--range", dest="chapter_range", help="处理范围，如 1-50")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不执行")
    parser.add_argument("--validate", action="store_true", help="验证已生成的摘要格式")
    parser.add_argument("--retry-failed", action="store_true", help="重试失败项")
    parser.add_argument("--gen-index", action="store_true", help="生成/重新生成 index.md")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--timeout", type=int, default=600, help="单批超时秒数（默认 600）")
    parser.add_argument("--concurrency", type=int, default=1, help="并发 Claude 调用数（默认 1）")
    return parser.parse_args()


def get_text_dir(book_dir: Path) -> Path:
    d = book_dir / "text"
    if not d.exists():
        print(f"错误：text 目录不存在: {d}")
        sys.exit(1)
    return d


def get_output_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "chapters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(output_dir: Path) -> Path:
    return output_dir / ".progress.json"


def _default_progress() -> dict:
    return {
        "completed": [],
        "failed": [],
        "characters": {},
        "stats": {"total_calls": 0, "total_time_seconds": 0},
    }


def _merge_progress(disk: dict, local: dict) -> dict:
    """T2 专用合并策略"""
    merged = json.loads(json.dumps(disk))  # deep copy
    merged["completed"] = sorted(set(disk.get("completed", [])) | set(local.get("completed", [])))
    merged["failed"] = sorted(
        (set(disk.get("failed", [])) | set(local.get("failed", []))) - set(merged["completed"])
    )
    base_chars = disk.get("characters", {})
    local_chars = local.get("characters", {})
    merged["characters"] = {**base_chars, **local_chars}
    merged["stats"] = merge_stats(disk.get("stats", {}), local.get("stats", {}))
    return merged


def extract_book_background(book_dir: Path) -> str:
    """从 book_detail.md 提取小说背景信息"""
    detail_path = book_dir / "book_detail.md"
    if not detail_path.exists():
        print(f"警告：book_detail.md 不存在: {detail_path}")
        return "（无小说背景信息）"

    content = detail_path.read_text(encoding="utf-8")
    # 提取基本信息、简介、标签三个段落
    sections = []
    current_section = None
    current_lines = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_section and current_section in ("基本信息", "简介", "标签"):
                sections.append((current_section, "\n".join(current_lines)))
            current_section = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 最后一个段落
    if current_section and current_section in ("基本信息", "简介", "标签"):
        sections.append((current_section, "\n".join(current_lines)))

    # 组合背景文本
    parts = []
    for name, text in sections:
        text = text.strip()
        if text:
            parts.append(f"### {name}\n{text}")

    return "\n\n".join(parts) if parts else "（无小说背景信息）"


def extract_initial_characters(book_dir: Path) -> dict[str, str]:
    """从 book_detail.md 角色人气榜提取初始角色名册"""
    detail_path = book_dir / "book_detail.md"
    if not detail_path.exists():
        return {}

    content = detail_path.read_text(encoding="utf-8")
    characters = {}
    in_characters = False

    for line in content.splitlines():
        if "角色人气榜" in line:
            in_characters = True
            continue
        if in_characters:
            if line.startswith("## "):
                break
            # 格式：- **角色名**（身份）👍数字 — 描述
            m = re.match(r"- \*\*(.+?)\*\*（(.+?)）.*?— (.+)", line)
            if m:
                name = m.group(1)
                role = m.group(2)
                desc = m.group(3).strip()
                characters[name] = f"{role}，{desc}"

    return characters


def format_characters_context(characters: dict[str, str]) -> str:
    """格式化角色名册为 prompt 文本"""
    if not characters:
        return "（暂无已知角色）"

    lines = []
    for name, desc in characters.items():
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


def format_chapter_list(
    chapters: list[int], text_dir: Path, output_dir: Path
) -> str:
    """格式化章节列表为 prompt 文本"""
    lines = []
    for ch in chapters:
        src = text_dir / f"ch{ch:04d}.md"
        dst = output_dir / f"ch{ch:04d}.md"
        lines.append(f"- 读取: `{src}` → 写入: `{dst}`")
    return "\n".join(lines)


def build_prompt(
    book_background: str,
    characters_context: str,
    chapter_list_text: str,
) -> str:
    """构建完整 prompt"""
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt = template.replace("{book_background}", book_background)
    prompt = prompt.replace("{characters_context}", characters_context)
    prompt = prompt.replace("{chapter_list}", chapter_list_text)
    return prompt


def validate_summary(filepath: Path) -> tuple[bool, list[str]]:
    """验证单个摘要文件的格式

    返回 (通过, 错误列表)
    """
    errors = []

    if not filepath.exists():
        return False, ["文件不存在"]

    content = filepath.read_text(encoding="utf-8")
    if not content.strip():
        return False, ["文件为空"]

    # 检查 8 个必需段落
    for section in REQUIRED_SECTIONS:
        if section not in content:
            errors.append(f"缺少段落: {section}")

    # 检查一句话摘要长度
    summary_match = re.search(r"## 一句话摘要\s*\n(.+?)(?:\n|$)", content)
    if summary_match:
        summary = summary_match.group(1).strip()
        if len(summary) > 50:
            errors.append(f"一句话摘要超过50字: {len(summary)}字")
    else:
        errors.append("无法提取一句话摘要")

    return len(errors) == 0, errors


def extract_new_characters(filepath: Path) -> dict[str, str]:
    """从摘要文件的"新登场角色"段落提取新角色"""
    if not filepath.exists():
        return {}

    content = filepath.read_text(encoding="utf-8")
    characters = {}

    # 找到 "## 新登场角色" 到下一个 "##" 之间的内容
    match = re.search(
        r"## 新登场角色\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if not match:
        return {}

    section = match.group(1)
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and "（无）" not in line:
            # 格式：- 角色名：描述  或  - 角色名（身份）：描述
            m = re.match(r"- (.+?)[:：](.+)", line)
            if m:
                name = m.group(1).strip()
                desc = m.group(2).strip()
                characters[name] = desc

    return characters


def run_claude_batch(
    prompt: str,
    model: str,
    timeout: int,
    batch_chapters: list[int],
    verbose: bool = True,
) -> tuple[bool, str]:
    """调用 claude -p 处理一批章节

    返回 (成功, 输出/错误信息)
    verbose=True 时 stderr 直接输出到终端；False 时捕获全部输出（适合并发）。
    """
    cmd = [
        "claude",
        "-p", "-",
        "--model", model,
        "--allowedTools", "Read,Write",
        "--permission-mode", "bypassPermissions",
    ]

    # 启动等待动画线程
    stop_event = threading.Event()
    ch_range = f"ch{batch_chapters[0]:04d}~ch{batch_chapters[-1]:04d}"
    anim_thread = threading.Thread(
        target=show_waiting_animation,
        args=(stop_event, f"Claude 处理 {ch_range}")
    )
    anim_thread.daemon = True
    anim_thread.start()

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
            stop_event.set()
            anim_thread.join(timeout=0.5)

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
            stop_event.set()
            anim_thread.join(timeout=0.5)

            if result.returncode != 0:
                err_detail = result.stderr[:1000] if result.stderr.strip() else result.stdout[:1000]
                return False, f"退出码 {result.returncode}\n{err_detail}"
        return True, result.stdout[:500]
    except subprocess.TimeoutExpired:
        stop_event.set()
        anim_thread.join(timeout=0.5)
        return False, f"超时（{timeout}秒）"
    except Exception as e:
        stop_event.set()
        anim_thread.join(timeout=0.5)
        return False, f"异常: {e}"


def run_validate(output_dir: Path, all_chapters: list[int]):
    """验证所有已生成的摘要"""
    total = 0
    passed = 0
    failed_list = []

    for ch in all_chapters:
        filepath = output_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue
        total += 1
        ok, errors = validate_summary(filepath)
        if ok:
            passed += 1
        else:
            failed_list.append((ch, errors))

    print(f"\n验证结果: {passed}/{total} 通过")
    if failed_list:
        print(f"\n失败章节 ({len(failed_list)}):")
        for ch, errors in failed_list:
            print(f"  ch{ch:04d}: {'; '.join(errors)}")

    missing = [ch for ch in all_chapters if not (output_dir / f"ch{ch:04d}.md").exists()]
    if missing:
        print(f"\n未生成 ({len(missing)}): ch{missing[0]:04d} ~ ch{missing[-1]:04d}")


def run_gen_index(output_dir: Path, all_chapters: list[int]):
    """生成 index.md 索引"""
    lines = ["# 章节摘要索引\n"]
    lines.append("| 编号 | 标题 | 一句话摘要 |")
    lines.append("|------|------|-----------|")

    count = 0
    for ch in all_chapters:
        filepath = output_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue

        content = filepath.read_text(encoding="utf-8")

        # 提取标题
        title_match = re.match(r"# (.+)", content)
        title = title_match.group(1).strip() if title_match else f"第{ch}章"

        # 提取一句话摘要
        summary_match = re.search(r"## 一句话摘要\s*\n(.+?)(?:\n|$)", content)
        summary = summary_match.group(1).strip() if summary_match else ""

        lines.append(f"| ch{ch:04d} | {title} | {summary} |")
        count += 1

    index_path = output_dir / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"索引已生成: {index_path} ({count} 条)")


def determine_pending_chapters(
    all_chapters: list[int],
    progress: dict,
    output_dir: Path,
    chapter_range: str | None,
    retry_failed: bool,
) -> list[int]:
    """确定待处理的章节列表"""
    completed = set(progress.get("completed", []))
    failed = set(progress.get("failed", []))

    if retry_failed:
        # 重试模式：只处理失败项
        pending = sorted(failed)
        if not pending:
            print("没有失败项需要重试")
        return pending

    # 应用范围过滤
    candidates = all_chapters
    if chapter_range:
        m = re.match(r"(\d+)-(\d+)$", chapter_range)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            candidates = [ch for ch in all_chapters if start <= ch <= end]
        else:
            print(f"错误：范围格式不正确: {chapter_range}（应为 start-end）")
            sys.exit(1)

    # 过滤已完成的（双重确认：progress + 文件存在且有效）
    pending = []
    for ch in candidates:
        filepath = output_dir / f"ch{ch:04d}.md"
        if ch in completed and filepath.exists():
            ok, _ = validate_summary(filepath)
            if ok:
                continue
        pending.append(ch)

    return pending


def main():
    args = parse_args()

    # 解析路径
    book_dir = resolve_book_dir(args.book_dir)
    text_dir = get_text_dir(book_dir)
    output_dir = get_output_dir(book_dir)
    progress_path = get_progress_path(output_dir)

    # 扫描章节
    all_chapters = list_chapter_files(text_dir)
    if not all_chapters:
        print(f"错误：text 目录中没有章节文件: {text_dir}")
        sys.exit(1)
    print_flush(f"知识库目录: {book_dir}")
    print_flush(f"总章节数: {len(all_chapters)} (ch{all_chapters[0]:04d} ~ ch{all_chapters[-1]:04d})")

    # 特殊模式
    if args.validate:
        run_validate(output_dir, all_chapters)
        return

    if args.gen_index:
        run_gen_index(output_dir, all_chapters)
        return

    # 加载进度
    progress = load_progress(progress_path, default_fn=_default_progress)
    completed_count = len(progress.get("completed", []))
    print_flush(f"已完成: {completed_count}, 失败: {len(progress.get('failed', []))}")

    # 提取小说背景
    book_background = extract_book_background(book_dir)

    # 确定待处理章节
    pending = determine_pending_chapters(
        all_chapters, progress, output_dir, args.chapter_range, args.retry_failed
    )

    if not pending:
        print("所有章节已完成！")
        return

    print_flush(f"待处理: {len(pending)} 章")

    # 计算批次
    batches = []
    for i in range(0, len(pending), args.batch_size):
        batches.append(pending[i : i + args.batch_size])

    print_flush(f"批次数: {len(batches)} (每批 {args.batch_size} 章)")

    if args.dry_run:
        print("\n=== 试运行模式 ===")
        for i, batch in enumerate(batches):
            ch_range = f"ch{batch[0]:04d}~ch{batch[-1]:04d}"
            print(f"  批次 {i + 1}/{len(batches)}: {ch_range} ({len(batch)} 章)")
        print(f"\n模型: {args.model}")
        print(f"超时: {args.timeout}s/批")
        print(f"已知角色: {len(progress['characters'])} 个")
        print(f"\n背景信息预览:\n{book_background[:200]}...")
        return

    # 开始批量处理
    concurrency = max(1, args.concurrency)
    print_flush(f"\n开始处理（模型: {args.model}, 并发: {concurrency}）...")

    # 用于线程安全的打印和进度更新
    print_lock = threading.Lock()
    completed_counter = {"done": 0, "total": len(batches)}

    def process_one_batch(batch_idx: int, batch_chapters: list[int],
                          verbose: bool = True) -> dict:
        """处理单个批次，返回结果摘要"""
        ch_range = f"ch{batch_chapters[0]:04d}~ch{batch_chapters[-1]:04d}"

        with print_lock:
            # 读取最新进度获取角色列表
            latest = load_progress(progress_path, default_fn=_default_progress)
            chars_count = len(latest.get("characters", {}))
            characters_text = format_characters_context(latest.get("characters", {}))
            print_flush(f"\n{'='*60}")
            print_flush(f"批次 {batch_idx + 1}/{len(batches)}: {ch_range} ({len(batch_chapters)} 章)")
            print_flush(f"已知角色: {chars_count} 个")

        # 构建 prompt（读文件操作，线程安全）
        chapter_list_text = format_chapter_list(batch_chapters, text_dir, output_dir)
        prompt = build_prompt(book_background, characters_text, chapter_list_text)

        # 调用 Claude（耗时操作，并发执行）
        start_time = time.time()
        success, output = run_claude_batch(
            prompt, args.model, args.timeout, batch_chapters, verbose=verbose
        )
        elapsed = time.time() - start_time

        # 构建本批次的局部进度变更
        local_completed = []
        local_failed = []
        local_characters = {}
        batch_ok = 0
        batch_fail = 0

        if not success:
            with print_lock:
                print_flush(f"  [{ch_range}] ✗ 失败 ({elapsed:.0f}s): {output[:100]}")
            local_failed = list(batch_chapters)
        else:
            # 验证输出 + 提取角色
            fail_msgs = []
            for ch in batch_chapters:
                filepath = output_dir / f"ch{ch:04d}.md"
                ok, errors = validate_summary(filepath)
                if ok:
                    batch_ok += 1
                    local_completed.append(ch)
                    new_chars = extract_new_characters(filepath)
                    local_characters.update(new_chars)
                else:
                    batch_fail += 1
                    local_failed.append(ch)
                    fail_msgs.append(f"    ✗ ch{ch:04d} 验证失败: {'; '.join(errors)}")

            with print_lock:
                print_flush(f"  [{ch_range}] ✓ Claude 调用完成 ({elapsed:.0f}s)")
                for msg in fail_msgs:
                    print_flush(msg)
                print_flush(f"  [{ch_range}] 结果: {batch_ok} 通过, {batch_fail} 失败")

        # 构建本批次局部进度，通过 save_progress 合并到磁盘
        local_progress = _default_progress()
        local_progress["completed"] = local_completed
        local_progress["failed"] = local_failed
        local_progress["characters"] = local_characters
        # stats 使用增量：merge 时需要叠加到磁盘值上
        local_progress["stats"]["total_calls"] = 1
        local_progress["stats"]["total_time_seconds"] = int(elapsed)

        def _batch_merge(disk: dict, local: dict) -> dict:
            """单批次合并：completed/failed/characters 走 _merge_progress，stats 用增量"""
            merged = _merge_progress(disk, local)
            # stats 用增量叠加（而非 max）
            merged["stats"]["total_calls"] = disk.get("stats", {}).get("total_calls", 0) + local["stats"]["total_calls"]
            merged["stats"]["total_time_seconds"] = disk.get("stats", {}).get("total_time_seconds", 0) + local["stats"]["total_time_seconds"]
            return merged

        save_progress(progress_path, local_progress, default_fn=_default_progress, merge_fn=_batch_merge)

        with print_lock:
            completed_counter["done"] += 1
            # 重新读取合并后的进度
            latest = load_progress(progress_path, default_fn=_default_progress)
            total_done = len(latest.get("completed", []))
            print_flush(f"  总进度: {total_done}/{len(all_chapters)} ({total_done*100//len(all_chapters)}%)  [{completed_counter['done']}/{completed_counter['total']} 批]")

        return {"ok": batch_ok, "fail": batch_fail, "elapsed": elapsed}

    # 分发到线程池
    all_results = []
    if concurrency == 1:
        # 串行模式，直接循环
        for batch_idx, batch_chapters in enumerate(batches):
            result = process_one_batch(batch_idx, batch_chapters)
            all_results.append(result)
    else:
        # 并发模式
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(process_one_batch, i, batch, verbose=False): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    all_results.append(result)
                except Exception as e:
                    with print_lock:
                        print_flush(f"  ✗ 线程异常: {e}")

    # 最终统计（从磁盘读取最终状态）
    final = load_progress(progress_path, default_fn=_default_progress)
    print_flush(f"\n{'='*60}")
    print_flush("处理完成！")
    print_flush(f"  已完成: {len(final['completed'])}/{len(all_chapters)}")
    print_flush(f"  失败: {len(final['failed'])}")
    print_flush(f"  总调用: {final['stats']['total_calls']} 次")
    total_seconds = final["stats"]["total_time_seconds"]
    print_flush(f"  总耗时: {total_seconds // 3600}h{(total_seconds % 3600) // 60}m")

    if final["failed"]:
        print(f"\n失败章节: {final['failed']}")
        print("可用 --retry-failed 重试")

    if len(final["completed"]) == len(all_chapters):
        print("\n全部章节已完成！建议运行:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")
        print(f"  python {__file__} --book-dir {args.book_dir} --gen-index")


if __name__ == "__main__":
    main()
