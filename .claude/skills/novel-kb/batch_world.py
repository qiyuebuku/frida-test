#!/usr/bin/env python3
"""
T5 世界观提取 — 批量编排脚本

从 T2 章节摘要 + T3 弧文件 + T4 角色档案中提取世界观设定。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T2 已完成: plot/chapters/ 下有章节摘要（"新增设定" + "重要物品" 字段）
  - T3 已完成: plot/outline/ 下有弧文件（"新世界信息" 字段）
  - T4 已完成: characters/ 下有角色档案（提取势力归属，可选）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 0  preprocess       Python 预处理，合并三个数据源
                           产出: world/.build/raw_world_data.json
                                 包含章节设定、弧世界信息、角色势力
                           耗时: ~10s, 0 次 AI 调用

  阶段 1  segment-classify 分段分类，每段 100 章，Claude 分类为 4 大体系 + misc
                           产出: world/.build/seg_XX.json
                                 包含 power_system, geography, factions, rules, misc
                           耗时: 取决于章节数，每段 1 次 AI 调用
                                 支持 --concurrency M 并发 M 段加速
                                 小规模（≤50 章）跳过分段，直接全量处理

  阶段 2  global-merge     全局融合，合并所有段数据 → 4 个 MD 文件
                           产出: world/power_system.md（力量体系）
                                 world/geography.md（地理空间）
                                 world/factions.md（组织势力）
                                 world/rules.md（规则与限制）
                                 world/index.md（世界观总览）
                           耗时: ~2min, 1 次 AI 调用（需要 Write 工具）

  阶段 3  refine           精修与原文补充，分 4 步自动执行
                           3a-c. 逐类别精修（力量/地理/势力，补充原文细节）
                           3d. 一致性验证（交叉检查 4 个文件的冲突）
                           产出: 更新上述 4 个 MD 文件
                                 world/.build/consistency_validation.json（验证结果）
                           耗时: ~3-5min, 3 次精修 AI + 1 次验证 AI
                                 支持 --concurrency M 并发精修加速

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（从当前进度自动继续）
  python batch_world.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：阶段 1 + 3 并发处理
  python batch_world.py --book-dir ... --concurrency 3

  # 只运行某个阶段
  python batch_world.py --book-dir ... --phase preprocess
  python batch_world.py --book-dir ... --phase segment-classify --concurrency 3
  python batch_world.py --book-dir ... --phase global-merge
  python batch_world.py --book-dir ... --phase refine --concurrency 3

  # 试运行 + 验证产出
  python batch_world.py --book-dir ... --dry-run
  python batch_world.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH         知识库目录（必需）
  --segment-size N        每段章节数（默认 100，影响阶段 1）
  --concurrency M         并发数（默认 1，对 segment-classify 和 refine 阶段生效）
  --phase PHASE           只运行特定阶段（preprocess/segment-classify/global-merge/refine）
  --model MODEL           Claude 模型（默认 sonnet）
  --timeout SEC           单次调用超时秒数（默认 600）
  --dry-run               试运行，不执行 Claude 调用
  --validate              验证产出文件完整性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    world/
      power_system.md       力量体系（修炼等级、功法体系、法术分类、修炼资源）
      geography.md          地理空间（区域划分、重要地点、空间结构、距离尺度）
      factions.md           组织势力（宗门/家族/势力、层级结构、成员构成、势力关系）
      rules.md              规则与限制（世界法则、修炼限制、社会规则、禁忌）
      index.md              世界观总览（统计 + 文件导航）
      .build/
        raw_world_data.json        预处理数据（章节设定 + 弧世界信息 + 角色势力）
        seg_XX.json                段级分类 JSON
        consistency_validation.json 一致性验证结果
      .progress.json        进度文件
"""

import argparse
import fcntl
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

# 分类体系
CATEGORIES = ["power_system", "geography", "factions", "rules"]
CATEGORY_NAMES = {
    "power_system": "力量体系",
    "geography": "地理空间",
    "factions": "组织势力",
    "rules": "规则与限制",
}


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="世界层提取")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--phase",
                        choices=["preprocess", "segment-classify",
                                 "global-merge", "refine"],
                        help="只运行特定阶段")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--segment-size", type=int, default=100,
                        help="每段处理的章节数（默认 100）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="segment-classify 和 refine 阶段并发数（默认 1）")
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
    d = book_dir / "plot" / "chapters"
    if not d.exists():
        print(f"错误：chapters 目录不存在: {d}")
        print("请先运行 T2（章节摘要生成）")
        sys.exit(1)
    return d


def get_outline_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "outline"
    if not d.exists():
        print(f"错误：outline 目录不存在: {d}")
        print("请先运行 T3（剧情层提取）")
        sys.exit(1)
    return d


def get_characters_dir(book_dir: Path) -> Path:
    d = book_dir / "characters"
    if not d.exists():
        print(f"警告：characters 目录不存在: {d}")
        print("T4（角色层）未完成，势力信息将缺失")
    return d


def get_world_dir(book_dir: Path) -> Path:
    d = book_dir / "world"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_build_dir(book_dir: Path) -> Path:
    d = book_dir / "world" / ".build"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(book_dir: Path) -> Path:
    return book_dir / "world" / ".progress.json"


# ============================================================
# 进度管理
# ============================================================

def _default_progress() -> dict:
    return {
        "phase": "preprocess",
        "preprocess": {"status": "pending"},
        "segment_classify": {
            "status": "pending",
            "segments_completed": [],
            "segments_failed": [],
        },
        "global_merge": {"status": "pending"},
        "refine": {
            "power_system": False,
            "geography": False,
            "factions": False,
            "validated": False,
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
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            disk = _read_progress_raw(progress_path)
            # 合并 segment_classify completed/failed
            sc = progress.get("segment_classify", {})
            dsc = disk.get("segment_classify", {})
            merged_completed = sorted(set(dsc.get("segments_completed", [])) | set(sc.get("segments_completed", [])))
            merged_failed = sorted(
                (set(dsc.get("segments_failed", [])) | set(sc.get("segments_failed", []))) - set(merged_completed)
            )
            progress["segment_classify"]["segments_completed"] = merged_completed
            progress["segment_classify"]["segments_failed"] = merged_failed
            # 合并 refine（取 True）
            for key in ("power_system", "geography", "factions", "validated"):
                progress["refine"][key] = progress["refine"].get(key, False) or disk["refine"].get(key, False)
            # stats 取 max
            progress["stats"]["total_calls"] = max(
                disk["stats"].get("total_calls", 0), progress["stats"].get("total_calls", 0))
            progress["stats"]["total_time_seconds"] = max(
                disk["stats"].get("total_time_seconds", 0), progress["stats"].get("total_time_seconds", 0))
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ============================================================
# 数据加载
# ============================================================

def list_summary_chapters(chapters_dir: Path) -> list[int]:
    chapters = []
    for f in chapters_dir.iterdir():
        m = re.match(r"ch(\d+)\.md$", f.name)
        if m:
            chapters.append(int(m.group(1)))
    chapters.sort()
    return chapters


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
# Markdown 解析工具
# ============================================================

def parse_section(content: str, section_name: str) -> str:
    """从 Markdown 内容中提取指定 ## 段落的文本"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_setting_lines(section_text: str) -> list[dict]:
    """解析设定行，返回 [{name, description}, ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        # 匹配: - 设定名：描述 或 - 设定名: 描述
        m = re.match(r"- (.+?)[:：](.+)", line)
        if m:
            results.append({
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            })
    return results


def parse_bold_lines(section_text: str) -> list[dict]:
    """解析加粗格式行（弧文件格式），返回 [{name, description}, ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        # 匹配: - **设定名**: 描述
        m = re.match(r"- \*\*(.+?)\*\*[:：]\s*(.+)", line)
        if m:
            results.append({
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            })
    return results


# ============================================================
# 阶段 0：Python 预处理
# ============================================================

def parse_chapter_settings(filepath: Path) -> dict:
    """解析单章摘要的世界观相关字段"""
    content = filepath.read_text(encoding="utf-8")

    settings_section = parse_section(content, "新增设定")
    settings = parse_setting_lines(settings_section)

    items_section = parse_section(content, "重要物品")
    items = parse_setting_lines(items_section)

    return {"settings": settings, "items": items}


def parse_all_settings(chapters_dir: Path, all_chapters: list[int]) -> dict:
    """遍历全部章节摘要，提取世界观设定"""
    chapter_settings = {}

    for ch in all_chapters:
        filepath = chapters_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue

        ch_str = f"ch{ch:04d}"
        data = parse_chapter_settings(filepath)

        if data["settings"] or data["items"]:
            chapter_settings[ch_str] = data

    return chapter_settings


def parse_arc_world_info(outline_dir: Path) -> list[dict]:
    """解析 T3 弧文件中的新世界信息"""
    arc_info = []

    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")

        # 解析弧标题和章节范围
        title_match = re.match(r"# (.+)", content)
        arc_title = title_match.group(1).strip() if title_match else arc_file.stem

        range_match = re.search(
            r"\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)", content
        )
        ch_start = int(range_match.group(1)) if range_match else 0
        ch_end = int(range_match.group(2)) if range_match else 0

        # 解析新世界信息
        world_section = parse_section(content, "新世界信息")
        entries = parse_bold_lines(world_section)

        if entries:
            arc_info.append({
                "arc": arc_title,
                "arc_file": arc_file.name,
                "ch_range": f"ch{ch_start:04d}-ch{ch_end:04d}",
                "entries": entries,
            })

    return arc_info


def parse_character_factions(characters_dir: Path) -> list[dict]:
    """从 T4 角色档案提取势力归属"""
    factions = {}  # faction_name -> [member_names]

    if not characters_dir.exists():
        return []

    for f in sorted(characters_dir.glob("*.md")):
        if f.name in ("index.md", "relationships.md"):
            continue

        content = f.read_text(encoding="utf-8")

        # 提取角色名
        title_match = re.match(r"# (.+)", content)
        char_name = title_match.group(1).strip() if title_match else f.stem

        # 从身份字段中提取势力名
        # 匹配: - **身份**: XXX宗/门/派/族/帮/会
        identity_match = re.search(r"\*\*身份\*\*[:：]\s*(.+)", content)
        if identity_match:
            identity = identity_match.group(1).strip()
            # 提取组织名
            org_matches = re.findall(r"([\u4e00-\u9fff]+(?:宗|门|派|族|帮|会|盟|教|谷|阁|堡|庄|楼))", identity)
            for org in org_matches:
                if org not in factions:
                    factions[org] = []
                if char_name not in factions[org]:
                    factions[org].append(char_name)

    result = []
    for org_name, members in factions.items():
        result.append({
            "faction": org_name,
            "members": members,
            "source": "characters",
        })

    return result


def build_raw_world_data(book_dir: Path, all_chapters: list[int]) -> dict:
    """合并所有来源 → raw_world_data.json"""
    chapters_dir = get_chapters_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)
    characters_dir = get_characters_dir(book_dir)

    # 1. 解析全部章节摘要
    chapter_settings = parse_all_settings(chapters_dir, all_chapters)

    # 2. 解析 T3 弧文件
    arc_world_info = parse_arc_world_info(outline_dir)

    # 3. 解析 T4 角色势力
    character_factions = parse_character_factions(characters_dir)

    # 统计
    total_settings = sum(
        len(v["settings"]) + len(v["items"])
        for v in chapter_settings.values()
    )
    total_arc_entries = sum(len(a["entries"]) for a in arc_world_info)

    return {
        "chapter_settings": chapter_settings,
        "arc_world_info": arc_world_info,
        "character_factions": character_factions,
        "stats": {
            "total_chapters_with_settings": len(chapter_settings),
            "total_setting_entries": total_settings,
            "total_arc_entries": total_arc_entries,
            "total_factions_from_characters": len(character_factions),
        },
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

    if dry_run:
        print(f"将解析 {len(all_chapters)} 章摘要 + T3 弧文件 + T4 角色档案")
        print("产出: world/.build/raw_world_data.json")
        return

    raw_data = build_raw_world_data(book_dir, all_chapters)

    # 保存
    build_dir = get_build_dir(book_dir)
    output_path = build_dir / "raw_world_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    stats = raw_data["stats"]
    print(f"含设定的章节: {stats['total_chapters_with_settings']} 章")
    print(f"设定条目总数: {stats['total_setting_entries']} 条")
    print(f"弧世界信息: {stats['total_arc_entries']} 条")
    print(f"角色势力: {stats['total_factions_from_characters']} 个")

    # 列出各章设定
    for ch_str, data in sorted(raw_data["chapter_settings"].items()):
        n_s = len(data["settings"])
        n_i = len(data["items"])
        print(f"  {ch_str}: {n_s} 设定 + {n_i} 物品")

    print(f"\n已保存: {output_path}")

    progress["preprocess"]["status"] = "completed"
    progress["phase"] = "segment_classify"
    save_progress(progress_path, progress)


# ============================================================
# 阶段 1：分段提取与分类
# ============================================================

def build_segment_data(raw_data: dict, start_ch: int, end_ch: int) -> str:
    """构建段数据文本"""
    parts = []

    # 章节设定
    for ch_str, data in sorted(raw_data["chapter_settings"].items()):
        ch_num = int(re.match(r"ch(\d+)", ch_str).group(1))
        if ch_num < start_ch or ch_num > end_ch:
            continue

        entries = []
        for s in data["settings"]:
            entries.append(f"  - [设定] {s['name']}：{s['description']}")
        for i in data["items"]:
            entries.append(f"  - [物品] {i['name']}：{i['description']}")
        if entries:
            parts.append(f"### {ch_str}\n" + "\n".join(entries))

    # 弧世界信息（范围内的）
    arc_parts = []
    for arc in raw_data["arc_world_info"]:
        range_match = re.match(r"ch(\d+)-ch(\d+)", arc["ch_range"])
        if range_match:
            arc_start = int(range_match.group(1))
            arc_end = int(range_match.group(2))
            if arc_start <= end_ch and arc_end >= start_ch:
                for e in arc["entries"]:
                    arc_parts.append(f"  - [{arc['arc']}] {e['name']}：{e['description']}")

    if arc_parts:
        parts.append("### 弧汇总信息\n" + "\n".join(arc_parts))

    return "\n\n".join(parts) if parts else "（本段无设定数据）"


def build_segment_classify_prompt(segment_data: str, segment_label: str) -> str:
    """构建分段分类 prompt"""
    template = (PROMPTS_DIR / "world_segment_classify.md").read_text(encoding="utf-8")
    prompt = template.replace("{segment_data}", segment_data)
    prompt = prompt.replace("{segment_label}", segment_label)
    return prompt


def phase_segment_classify(book_dir: Path, all_chapters: list[int],
                           progress: dict, progress_path: Path,
                           model: str, timeout: int, dry_run: bool,
                           segment_size: int, concurrency: int = 1):
    """执行阶段 1：分段提取与分类"""
    print("\n" + "=" * 60)
    print("阶段 1：分段提取与分类")
    print("=" * 60)

    build_dir = get_build_dir(book_dir)
    raw_data_path = build_dir / "raw_world_data.json"
    if not raw_data_path.exists():
        print("错误：raw_world_data.json 不存在，请先运行阶段 0")
        return

    with open(raw_data_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    total_chapters = len(all_chapters)

    # 小规模优化：≤50 章跳过分段，直接全量处理
    if total_chapters <= 50:
        print(f"章节数 ({total_chapters}) ≤ 50，跳过分段，直接进入阶段 2")
        # 生成一个覆盖全部章节的 segment_all.json
        segment_data = build_segment_data(raw_data, all_chapters[0], all_chapters[-1])
        segment_label = f"ch{all_chapters[0]:04d}-ch{all_chapters[-1]:04d} (全部)"

        if dry_run:
            print(f"将一次性分类全部 {total_chapters} 章设定")
            return

        prompt = build_segment_classify_prompt(segment_data, segment_label)
        print(f"分类全部 {total_chapters} 章设定...")
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

                # 统计
                for cat in CATEGORIES + ["misc"]:
                    n = len(result.get(cat, []))
                    if n > 0:
                        label = CATEGORY_NAMES.get(cat, cat)
                        print(f"  {label}: {n} 条")
            else:
                print(f"无法解析 JSON ({elapsed:.0f}s)")
                print(f"调试信息已保存: {debug_path}")
                save_progress(progress_path, progress)
                return
        else:
            print(f"失败 ({elapsed:.0f}s): {output[:200]}")
            save_progress(progress_path, progress)
            return

        progress["segment_classify"]["status"] = "completed"
        progress["segment_classify"]["segments_completed"].append("all")
        progress["phase"] = "global_merge"
        save_progress(progress_path, progress)
        return

    # 正常分段处理
    segments = []
    for i in range(0, total_chapters, segment_size):
        seg_chapters = all_chapters[i:i + segment_size]
        segments.append((seg_chapters[0], seg_chapters[-1], f"seg_{i // segment_size + 1:02d}"))

    completed = set(progress["segment_classify"]["segments_completed"])
    pending = [s for s in segments if s[2] not in completed]

    print(f"总段数: {len(segments)} ({len(pending)} 待处理)")

    if dry_run:
        for start, end, label in pending:
            seg_data = build_segment_data(raw_data, start, end)
            n_lines = len([l for l in seg_data.splitlines() if l.strip().startswith("- [")])
            print(f"  {label} (ch{start:04d}-ch{end:04d}): {n_lines} 条设定")
        return

    concurrency = max(1, concurrency)
    print_lock = threading.Lock()
    done_counter = {"n": 0, "total": len(pending)}

    def process_one_segment(start: int, end: int, seg_label: str,
                            verbose: bool = True) -> dict:
        seg_display = f"ch{start:04d}-ch{end:04d}"
        segment_data = build_segment_data(raw_data, start, end)

        if segment_data == "（本段无设定数据）":
            with print_lock:
                print(f"\n--- {seg_label} ({seg_display}): 无设定，跳过 ---")
            # 带锁更新进度
            lock_path = progress_path.with_suffix(".lock")
            with open(lock_path, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    disk = _read_progress_raw(progress_path)
                    if seg_label not in disk["segment_classify"]["segments_completed"]:
                        disk["segment_classify"]["segments_completed"].append(seg_label)
                    with open(progress_path, "w", encoding="utf-8") as f:
                        json.dump(disk, f, ensure_ascii=False, indent=2)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
            with print_lock:
                done_counter["n"] += 1
            return {"seg": seg_label, "success": True, "skipped": True}

        prompt = build_segment_classify_prompt(segment_data, f"{seg_label} ({seg_display})")

        with print_lock:
            print(f"\n--- {seg_label} ({seg_display}) ---")

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
        elapsed = time.time() - start_time

        # 带锁更新进度
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
                        if seg_label not in disk["segment_classify"]["segments_completed"]:
                            disk["segment_classify"]["segments_completed"].append(seg_label)
                        ok = True
                    else:
                        if seg_label not in disk["segment_classify"]["segments_failed"]:
                            disk["segment_classify"]["segments_failed"].append(seg_label)
                else:
                    if seg_label not in disk["segment_classify"]["segments_failed"]:
                        disk["segment_classify"]["segments_failed"].append(seg_label)
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
                progress.update(disk)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        with print_lock:
            done_counter["n"] += 1
            status = "✓" if ok else "✗"
            msg = f"  [{seg_label}] {status} ({elapsed:.0f}s)  [{done_counter['n']}/{done_counter['total']}]"
            if not success:
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
    all_done = set(s[2] for s in segments) <= set(final["segment_classify"]["segments_completed"])
    if all_done:
        final["segment_classify"]["status"] = "completed"
        final["phase"] = "global_merge"
        save_progress(progress_path, final)
        progress.update(final)
        print("\n分段分类全部完成！")


# ============================================================
# 阶段 2：全局融合
# ============================================================

def merge_segments(build_dir: Path) -> dict:
    """Python 预合并全部段级 JSON"""
    merged = {cat: [] for cat in CATEGORIES}
    merged["misc"] = []

    # 查找所有 segment JSON 文件
    seg_files = sorted(build_dir.glob("segment_*.json")) + \
                sorted(build_dir.glob("seg_*.json"))

    for seg_file in seg_files:
        with open(seg_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for cat in CATEGORIES + ["misc"]:
            entries = data.get(cat, [])
            merged[cat].extend(entries)

    return merged


def build_global_merge_prompt(segment_files: list[Path], raw_world_data_path: Path,
                              world_dir: str) -> str:
    """构建全局融合 prompt（让 Claude 用 Read 工具读取文件，避免 prompt 过大）"""
    template = (PROMPTS_DIR / "world_global_merge.md").read_text(encoding="utf-8")

    # 生成文件列表
    files_list = "\n".join(f"- `{f}`" for f in segment_files)

    prompt = template.replace("{segment_files_list}", files_list)
    prompt = prompt.replace("{raw_world_data_path}", str(raw_world_data_path))
    prompt = prompt.replace("{world_dir}", world_dir)
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
    world_dir = get_world_dir(book_dir)

    # 加载段级数据
    merged_data = merge_segments(build_dir)
    total_entries = sum(len(v) for v in merged_data.values())

    if total_entries == 0:
        print("警告：无分类数据，尝试从 raw_world_data.json 直接处理...")
        # 如果没有段级文件但 raw 数据存在，说明是小规模跳过了分段
        raw_path = build_dir / "raw_world_data.json"
        if raw_path.exists():
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            # 直接用 raw 数据构建分类 prompt
            # 这种情况不应该发生，因为小规模也会先生成 segment_all.json
            print("错误：请先运行阶段 1")
            return
        else:
            print("错误：无任何数据，请从阶段 0 开始")
            return

    print(f"总条目数: {total_entries}")
    for cat in CATEGORIES + ["misc"]:
        n = len(merged_data[cat])
        if n > 0:
            label = CATEGORY_NAMES.get(cat, cat)
            print(f"  {label}: {n} 条")

    # 收集 segment 文件路径
    segment_files = sorted(build_dir.glob("seg_*.json"))
    raw_path = build_dir / "raw_world_data.json"

    if dry_run:
        print(f"将融合 {total_entries} 条设定到 4 个 MD 文件")
        print(f"Segment 文件: {len(segment_files)} 个")
        return

    prompt = build_global_merge_prompt(segment_files, raw_path, str(world_dir))

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
        for cat in CATEGORIES:
            fpath = world_dir / f"{cat}.md"
            if fpath.exists():
                generated.append(f"{cat}.md ({fpath.stat().st_size} bytes)")
        print(f"  生成文件: {', '.join(generated) if generated else '无'}")

        progress["global_merge"]["status"] = "completed"
        progress["phase"] = "refine"
    else:
        print(f"失败 ({elapsed:.0f}s): {output[:200]}")

    save_progress(progress_path, progress)


# ============================================================
# 阶段 3：精修与原文补充
# ============================================================

def build_refine_prompt(category: str, current_content: str,
                        related_chapters: str) -> str:
    """构建精修 prompt"""
    template_name = f"world_refine_{category}.md"
    template_path = PROMPTS_DIR / template_name
    if not template_path.exists():
        return ""

    template = template_path.read_text(encoding="utf-8")
    prompt = template.replace("{current_content}", current_content)
    prompt = prompt.replace("{related_chapters}", related_chapters)
    return prompt


def build_validate_prompt(world_dir: Path) -> str:
    """构建一致性验证 prompt"""
    template = (PROMPTS_DIR / "world_validate_consistency.md").read_text(encoding="utf-8")

    all_content_parts = []
    for cat in CATEGORIES:
        fpath = world_dir / f"{cat}.md"
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            all_content_parts.append(f"### {CATEGORY_NAMES.get(cat, cat)}\n\n{content}")

    prompt = template.replace("{all_world_files}", "\n\n---\n\n".join(all_content_parts))
    return prompt


def get_related_chapter_summaries(book_dir: Path, category: str,
                                  max_chapters: int = 10) -> str:
    """获取与类别相关的章节摘要片段（用于精修时的原文参考）"""
    build_dir = get_build_dir(book_dir)
    chapters_dir = get_chapters_dir(book_dir)

    raw_path = build_dir / "raw_world_data.json"
    if not raw_path.exists():
        return "（无原始数据）"

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # 找出包含该类别设定的章节
    related_chs = []
    for ch_str, data in raw_data["chapter_settings"].items():
        ch_num = int(re.match(r"ch(\d+)", ch_str).group(1))
        for s in data["settings"] + data["items"]:
            text = f"{s['name']} {s['description']}".lower()
            if category == "power_system" and any(
                kw in text for kw in ["修炼", "等级", "功法", "法术", "法宝",
                                       "月华", "灵气", "修仙", "能力", "修为"]
            ):
                related_chs.append(ch_num)
                break
            elif category == "geography" and any(
                kw in text for kw in ["山", "河", "村", "城", "湖", "海",
                                       "洞", "地", "方向", "距离"]
            ):
                related_chs.append(ch_num)
                break
            elif category == "factions" and any(
                kw in text for kw in ["宗", "门", "派", "族", "帮", "势力",
                                       "组织", "仙宗"]
            ):
                related_chs.append(ch_num)
                break

    if not related_chs:
        return "（无相关章节）"

    # 限制数量
    related_chs = sorted(set(related_chs))[:max_chapters]

    parts = []
    for ch in related_chs:
        filepath = chapters_dir / f"ch{ch:04d}.md"
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            parts.append(f"### ch{ch:04d}\n\n{content}")

    return "\n\n---\n\n".join(parts) if parts else "（无相关章节）"


def phase_refine(book_dir: Path, progress: dict, progress_path: Path,
                 model: str, timeout: int, dry_run: bool,
                 concurrency: int = 1):
    """执行阶段 3：精修与原文补充"""
    print("\n" + "=" * 60)
    print("阶段 3：精修与原文补充")
    print("=" * 60)

    world_dir = get_world_dir(book_dir)

    # 精修各类别
    refine_categories = ["power_system", "geography", "factions"]
    pending_refine = []
    for cat in refine_categories:
        if progress["refine"].get(cat, False):
            print(f"{CATEGORY_NAMES[cat]} 精修已完成")
            continue
        cat_file = world_dir / f"{cat}.md"
        if not cat_file.exists():
            print(f"跳过 {CATEGORY_NAMES[cat]}（文件不存在）")
            continue
        current_content = cat_file.read_text(encoding="utf-8")
        if len(current_content) > 2000:
            print(f"{CATEGORY_NAMES[cat]} 内容已足够丰富，跳过精修")
            progress["refine"][cat] = True
            save_progress(progress_path, progress)
            continue
        prompt = build_refine_prompt(cat, current_content, get_related_chapter_summaries(book_dir, cat))
        if not prompt:
            print(f"跳过 {CATEGORY_NAMES[cat]}（无精修模板）")
            progress["refine"][cat] = True
            save_progress(progress_path, progress)
            continue
        pending_refine.append((cat, prompt))

    if dry_run:
        for cat, _ in pending_refine:
            print(f"将精修 {CATEGORY_NAMES[cat]}")
    elif pending_refine:
        concurrency = max(1, concurrency)
        print_lock = threading.Lock()

        def refine_one(cat: str, prompt: str, verbose: bool = True):
            with print_lock:
                print(f"精修 {CATEGORY_NAMES[cat]}...")
            start_time = time.time()
            success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write", verbose=verbose)
            elapsed = time.time() - start_time
            # 带锁更新
            lock_path = progress_path.with_suffix(".lock")
            with open(lock_path, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    disk = _read_progress_raw(progress_path)
                    disk["stats"]["total_calls"] = disk["stats"].get("total_calls", 0) + 1
                    disk["stats"]["total_time_seconds"] = disk["stats"].get("total_time_seconds", 0) + int(elapsed)
                    if success:
                        disk["refine"][cat] = True
                    with open(progress_path, "w", encoding="utf-8") as f:
                        json.dump(disk, f, ensure_ascii=False, indent=2)
                    progress.update(disk)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
            with print_lock:
                status = "✓" if success else "✗"
                msg = f"  [{CATEGORY_NAMES[cat]}] {status} ({elapsed:.0f}s)"
                if not success:
                    msg += f" {output[:100]}"
                print(msg)

        if concurrency == 1:
            for cat, prompt in pending_refine:
                refine_one(cat, prompt)
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(pending_refine))) as executor:
                futures = {executor.submit(refine_one, c, p, verbose=False): c for c, p in pending_refine}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        with print_lock:
                            print(f"  ✗ 精修异常 ({futures[future]}): {e}")

    # 一致性验证
    if not progress["refine"].get("validated", False):
        # 检查是否有足够的文件进行验证
        existing_files = [cat for cat in CATEGORIES
                          if (world_dir / f"{cat}.md").exists()]
        if len(existing_files) < 2:
            print("文件不足，跳过一致性验证")
            progress["refine"]["validated"] = True
            save_progress(progress_path, progress)
        else:
            if dry_run:
                print(f"将验证 {len(existing_files)} 个世界文件的一致性")
                return

            prompt = build_validate_prompt(world_dir)

            print("一致性验证...")
            start_time = time.time()
            success, output = run_claude_prompt(prompt, model, timeout)
            elapsed = time.time() - start_time

            progress["stats"]["total_calls"] += 1
            progress["stats"]["total_time_seconds"] += int(elapsed)

            if success:
                build_dir = get_build_dir(book_dir)
                debug_path = build_dir / "consistency_validation_json_debug.txt"
                result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
                if result:
                    n_issues = len(result.get("issues", []))
                    print(f"  完成 ({elapsed:.0f}s): {n_issues} 个问题")
                    # 保存验证结果
                    val_path = build_dir / "consistency_validation.json"
                    with open(val_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                else:
                    print(f"  完成 ({elapsed:.0f}s): 无结构化输出")
            else:
                print(f"  失败 ({elapsed:.0f}s): {output[:200]}")

            progress["refine"]["validated"] = True
            save_progress(progress_path, progress)

    # 生成 index.md
    build_index_md(world_dir)

    print("\n精修阶段完成！")


# ============================================================
# index.md 生成
# ============================================================

def build_index_md(world_dir: Path):
    """Python 直接生成 index.md"""
    lines = ["# 世界观总览\n"]

    # 统计各文件
    file_stats = []
    for cat in CATEGORIES:
        fpath = world_dir / f"{cat}.md"
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            # 统计 ## 段落数
            sections = re.findall(r"^## .+", content, re.MULTILINE)
            # 统计 - 条目数
            items = re.findall(r"^- .+", content, re.MULTILINE)
            file_stats.append({
                "file": f"{cat}.md",
                "name": CATEGORY_NAMES[cat],
                "sections": len(sections),
                "items": len(items),
                "size": len(content),
            })

    if file_stats:
        lines.append("## 统计\n")
        lines.append("| 文件 | 类别 | 段落数 | 条目数 |")
        lines.append("|------|------|--------|--------|")
        total_sections = 0
        total_items = 0
        for fs in file_stats:
            lines.append(f"| [{fs['name']}]({fs['file']}) | {fs['name']} | {fs['sections']} | {fs['items']} |")
            total_sections += fs["sections"]
            total_items += fs["items"]
        lines.append(f"| **合计** | | **{total_sections}** | **{total_items}** |")
        lines.append("")

    # 文件链接
    lines.append("## 文件导航\n")
    for cat in CATEGORIES:
        fpath = world_dir / f"{cat}.md"
        if fpath.exists():
            lines.append(f"- [{CATEGORY_NAMES[cat]}]({cat}.md)")
        else:
            lines.append(f"- {CATEGORY_NAMES[cat]}（未生成）")

    lines.append("")

    index_path = world_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成: {index_path}")


# ============================================================
# 验证
# ============================================================

def run_validate(book_dir: Path):
    """验证所有产出文件"""
    print("\n验证 T5 产出文件...")

    world_dir = book_dir / "world"
    build_dir = book_dir / "world" / ".build"

    results = []

    # 检查中间产物
    for name, desc in [
        ("raw_world_data.json", "Python 预处理"),
    ]:
        path = build_dir / name
        if path.exists():
            size = path.stat().st_size
            results.append((desc, "OK", f"{size} bytes"))
        else:
            results.append((desc, "缺失", ""))

    # 检查段级 JSON
    seg_files = list(build_dir.glob("segment_*.json")) + list(build_dir.glob("seg_*.json"))
    results.append(("段级分类", "OK" if seg_files else "缺失",
                     f"{len(seg_files)} 个文件"))

    # 检查 4 个 MD 文件
    for cat in CATEGORIES:
        fpath = world_dir / f"{cat}.md"
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            sections = len(re.findall(r"^## .+", content, re.MULTILINE))
            items = len(re.findall(r"^- .+", content, re.MULTILINE))
            results.append((CATEGORY_NAMES[cat], "OK",
                            f"{fpath.stat().st_size} bytes, {sections} 段, {items} 条"))
        else:
            results.append((CATEGORY_NAMES[cat], "缺失", ""))

    # 检查 index.md
    index_path = world_dir / "index.md"
    if index_path.exists():
        results.append(("世界观总览", "OK", f"{index_path.stat().st_size} bytes"))
    else:
        results.append(("世界观总览", "缺失", ""))

    # 检查一致性验证
    val_path = build_dir / "consistency_validation.json"
    if val_path.exists():
        with open(val_path, "r", encoding="utf-8") as f:
            val_data = json.load(f)
        n_issues = len(val_data.get("issues", []))
        results.append(("一致性验证", "OK", f"{n_issues} 个问题"))
    else:
        results.append(("一致性验证", "未运行", ""))

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

    print(f"知识库: {book_dir}")
    print(f"章节摘要: {len(all_chapters)} 章 "
          f"(ch{all_chapters[0]:04d} ~ ch{all_chapters[-1]:04d})")

    # 验证模式
    if args.validate:
        run_validate(book_dir)
        return

    # 加载进度
    progress = load_progress(progress_path)
    print(f"当前阶段: {progress['phase']}")

    # 确保输出目录存在
    get_world_dir(book_dir)
    get_build_dir(book_dir)

    if args.dry_run:
        print("\n=== 试运行模式 ===")

    # 阶段 0: 预处理
    if args.phase == "preprocess" or (not args.phase and progress["phase"] == "preprocess"):
        phase_preprocess(book_dir, all_chapters, progress, progress_path, args.dry_run)

    # 阶段 1: 分段分类
    if args.phase == "segment-classify" or (
        not args.phase and progress["phase"] in ("segment_classify", "preprocess")
    ):
        progress = load_progress(progress_path)
        if progress["phase"] == "segment_classify" or args.phase == "segment-classify":
            phase_segment_classify(book_dir, all_chapters, progress, progress_path,
                                   args.model, args.timeout, args.dry_run,
                                   args.segment_size, args.concurrency)

    # 阶段 2: 全局融合
    if args.phase == "global-merge" or (
        not args.phase and progress["phase"] in ("global_merge", "segment_classify")
    ):
        progress = load_progress(progress_path)
        if progress["phase"] == "global_merge" or args.phase == "global-merge":
            phase_global_merge(book_dir, progress, progress_path,
                               args.model, args.timeout, args.dry_run)

    # 阶段 3: 精修
    if args.phase == "refine" or (
        not args.phase and progress["phase"] in ("refine", "global_merge")
    ):
        progress = load_progress(progress_path)
        if progress["phase"] == "refine" or args.phase == "refine":
            phase_refine(book_dir, progress, progress_path,
                         args.model, args.timeout, args.dry_run,
                         args.concurrency)

    # 最终统计
    progress = load_progress(progress_path)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    print(f"  预处理: {progress['preprocess']['status']}")
    sc = progress["segment_classify"]
    print(f"  分段分类: {sc['status']} ({len(sc['segments_completed'])} 完成/"
          f"{len(sc['segments_failed'])} 失败)")
    print(f"  全局融合: {progress['global_merge']['status']}")
    ref = progress["refine"]
    print(f"  精修: 力量{'✓' if ref.get('power_system') else '✗'} "
          f"地理{'✓' if ref.get('geography') else '✗'} "
          f"势力{'✓' if ref.get('factions') else '✗'} "
          f"验证{'✓' if ref.get('validated') else '✗'}")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    # 检查是否全部完成
    all_done = (
        progress["preprocess"]["status"] == "completed"
        and progress["segment_classify"]["status"] == "completed"
        and progress["global_merge"]["status"] == "completed"
        and ref.get("validated", False)
    )
    if all_done:
        print("\nT5 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
