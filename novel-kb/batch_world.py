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
三阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 0  preprocess       Python 预处理，合并三个数据源
                           产出: world/.build/raw_world_data.json
                           耗时: ~10s, 0 次 AI 调用

  阶段 1  segment-classify 分段分类，每段 100 章，Claude 分类为 4 大体系 + misc
                           产出: world/.build/seg_XX.json
                           支持 --concurrency M 并发

  阶段 2  global-merge     全局去重合并 + AI 扩写 + 生成实体文件
                           2a. Python 全局去重合并 → merged_entities.json
                           2b. AI 标注重要性 + 扩写描述（按类别，high 实体一步到位）
                           2c. Python 生成实体文件 + index.md
                           产出: world/{category}/{entity}.md + index.md
                           支持 --concurrency M 并发

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    world/
      power_system/
        index.md              力量体系索引（实体列表 + 简介）
        修仙六境.md            独立实体文件
        ...
      geography/
        index.md
        ...
      factions/
        index.md
        ...
      rules/
        index.md
        ...
      index.md                世界观总览（链接到各类别 index）
      .build/
        raw_world_data.json
        seg_XX.json
        merged_entities.json  全局去重后的实体列表
      .progress.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程
  python batch_world.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：并发处理
  python batch_world.py --book-dir ... --concurrency 3

  # 只运行某个阶段
  python batch_world.py --book-dir ... --phase preprocess
  python batch_world.py --book-dir ... --phase segment-classify --concurrency 3
  python batch_world.py --book-dir ... --phase global-merge --concurrency 3

  # 试运行 + 验证产出
  python batch_world.py --book-dir ... --dry-run
  python batch_world.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH         知识库目录（必需）
  --segment-size N        每段章节数（默认 100，影响阶段 1）
  --concurrency M         并发数（默认 1）
  --phase PHASE           只运行特定阶段
  --model MODEL           Claude 模型（默认 sonnet）
  --timeout SEC           单次调用超时秒数（默认 600）
  --dry-run               试运行，不执行 Claude 调用
  --validate              验证产出文件完整性
"""

import argparse
import fcntl
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from json_fixer import fix_and_parse_json
from kb_common import (
    SKILL_DIR, PROMPTS_DIR,
    resolve_book_dir, load_progress, save_progress, merge_stats,
    run_claude_prompt, list_chapter_files, load_chapter_file, compute_segments,
    parse_section, parse_setting_lines, sanitize_filename,
    print_flush,
)
from kb_preprocess import load_preprocess_cache, get_raw_world_data

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
                                 "global-merge"],
                        help="只运行特定阶段")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--segment-size", type=int, default=100,
                        help="每段处理的章节数（默认 100）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="并发数（默认 1）")
    return parser.parse_args()


# ============================================================
# 路径解析
# ============================================================

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
        "entity_expand": {"status": "pending", "categories_completed": []},
        "stats": {
            "total_calls": 0,
            "total_time_seconds": 0,
        },
    }


def _read_raw(progress_path: Path) -> dict:
    """无锁读取进度文件（供已持锁的上下文使用）"""
    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults = _default_progress()
        for key in defaults:
            if key not in data:
                data[key] = defaults[key]
        return data
    return _default_progress()


def _load_progress(progress_path: Path) -> dict:
    """加载进度（带旧格式迁移）"""
    data = load_progress(progress_path, default_fn=_default_progress)
    defaults = _default_progress()
    for key in defaults:
        if key not in data:
            data[key] = defaults[key]
    return data


def _merge_progress(disk: dict, progress: dict) -> dict:
    """T5 专用合并策略"""
    # 合并 segment_classify completed/failed
    sc = progress.get("segment_classify", {})
    dsc = disk.get("segment_classify", {})
    merged_completed = sorted(set(dsc.get("segments_completed", [])) | set(sc.get("segments_completed", [])))
    merged_failed = sorted(
        (set(dsc.get("segments_failed", [])) | set(sc.get("segments_failed", []))) - set(merged_completed)
    )
    progress["segment_classify"]["segments_completed"] = merged_completed
    progress["segment_classify"]["segments_failed"] = merged_failed
    # 合并 entity_expand categories_completed
    ee = progress.get("entity_expand", {})
    dee = disk.get("entity_expand", {})
    progress["entity_expand"]["categories_completed"] = sorted(
        set(dee.get("categories_completed", [])) | set(ee.get("categories_completed", []))
    )
    # stats
    progress["stats"] = merge_stats(disk.get("stats", {}), progress.get("stats", {}))
    return progress


def _save_progress(progress_path: Path, progress: dict):
    """保存进度（使用 T5 合并策略）"""
    save_progress(progress_path, progress,
                  default_fn=_default_progress, merge_fn=_merge_progress)


# ============================================================
# 数据加载
# ============================================================

def parse_bold_lines(section_text: str) -> list[dict]:
    """解析加粗格式行（弧文件格式），返回 [{name, description}, ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
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

        title_match = re.match(r"# (.+)", content)
        arc_title = title_match.group(1).strip() if title_match else arc_file.stem

        range_match = re.search(
            r"\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)", content
        )
        ch_start = int(range_match.group(1)) if range_match else 0
        ch_end = int(range_match.group(2)) if range_match else 0

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
    factions = {}

    if not characters_dir.exists():
        return []

    for f in sorted(characters_dir.glob("*.md")):
        if f.name in ("index.md", "relationships.md"):
            continue

        content = f.read_text(encoding="utf-8")

        title_match = re.match(r"# (.+)", content)
        char_name = title_match.group(1).strip() if title_match else f.stem

        identity_match = re.search(r"\*\*身份\*\*[:：]\s*(.+)", content)
        if identity_match:
            identity = identity_match.group(1).strip()
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

    chapter_settings = parse_all_settings(chapters_dir, all_chapters)
    arc_world_info = parse_arc_world_info(outline_dir)
    character_factions = parse_character_factions(characters_dir)

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

    # 使用统一预处理缓存
    cache = load_preprocess_cache(book_dir)
    raw_data = get_raw_world_data(cache)

    build_dir = get_build_dir(book_dir)
    output_path = build_dir / "raw_world_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    stats = raw_data["stats"]
    print(f"含设定的章节: {stats['total_chapters_with_settings']} 章")
    print(f"设定条目总数: {stats['total_setting_entries']} 条")
    print(f"弧世界信息: {stats['total_arc_entries']} 条")
    print(f"角色势力: {stats['total_factions_from_characters']} 个")

    for ch_str, data in sorted(raw_data["chapter_settings"].items()):
        n_s = len(data["settings"])
        n_i = len(data["items"])
        print(f"  {ch_str}: {n_s} 设定 + {n_i} 物品")

    print(f"\n已保存: {output_path}")

    progress["preprocess"]["status"] = "completed"
    progress["phase"] = "segment_classify"
    _save_progress(progress_path, progress)


# ============================================================
# 阶段 1：分段提取与分类
# ============================================================

def build_segment_data(raw_data: dict, start_ch: int, end_ch: int) -> str:
    """构建段数据文本"""
    parts = []

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
    """执行阶段 1：从 T3 统一段扫描结果中提取世界观分类数据"""
    print("\n" + "=" * 60)
    print("阶段 1：提取世界观分类数据（从 T3 段扫描）")
    print("=" * 60)

    build_dir = get_build_dir(book_dir)
    t3_segments_dir = book_dir / "plot" / "outline" / ".segments"

    if not t3_segments_dir.exists():
        print(f"错误：T3 段扫描结果不存在: {t3_segments_dir}")
        print("请先运行 T3（batch_plot.py --phase segment-scan）")
        return

    # 扫描 T3 段文件
    t3_seg_files = sorted(t3_segments_dir.glob("segment_*.json"))
    if not t3_seg_files:
        print("错误：T3 段目录中无段文件")
        return

    if dry_run:
        print(f"将从 {len(t3_seg_files)} 个 T3 段文件中提取世界观数据")
        return

    total_extracted = 0
    categories_count = {cat: 0 for cat in CATEGORIES + ["misc"]}

    for seg_file in t3_seg_files:
        with open(seg_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        world_data = data.get("world")
        if not world_data:
            # 旧格式 T3 段文件（无 world 部分），跳过
            print(f"  跳过 {seg_file.name}（无 world 数据，可能是旧格式）")
            continue

        # 保存为 T5 格式的 seg JSON
        seg_name = seg_file.stem  # e.g., "segment_01"
        # 转换为 T5 的命名约定
        seg_match = re.match(r"segment_(\d+)", seg_name)
        if seg_match:
            seg_num = int(seg_match.group(1))
            t5_seg_name = f"seg_{seg_num:02d}"
        else:
            t5_seg_name = seg_name

        output_path = build_dir / f"{t5_seg_name}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(world_data, f, ensure_ascii=False, indent=2)

        # 统计
        for cat in CATEGORIES + ["misc"]:
            n = len(world_data.get(cat, []))
            categories_count[cat] += n
            total_extracted += n

        seg_label = t5_seg_name
        if not progress["segment_classify"]["segments_completed"]:
            progress["segment_classify"]["segments_completed"] = []
        if seg_label not in progress["segment_classify"]["segments_completed"]:
            progress["segment_classify"]["segments_completed"].append(seg_label)

    print(f"已提取 {total_extracted} 条设定（来自 {len(t3_seg_files)} 个段文件）")
    for cat in CATEGORIES + ["misc"]:
        n = categories_count[cat]
        if n > 0:
            label = CATEGORY_NAMES.get(cat, cat)
            print(f"  {label}: {n} 条")

    progress["segment_classify"]["status"] = "completed"
    progress["phase"] = "global_merge"
    _save_progress(progress_path, progress)
    print("\n世界观分类数据提取完成！")


# ============================================================
# 阶段 2：全局去重合并 + AI 扩写 + 生成实体文件
# ============================================================

def merge_segments(build_dir: Path) -> dict:
    """Python 预合并全部段级 JSON"""
    merged = {cat: [] for cat in CATEGORIES}
    merged["misc"] = []

    seg_files = sorted(build_dir.glob("segment_*.json")) + \
                sorted(build_dir.glob("seg_*.json"))

    for seg_file in seg_files:
        with open(seg_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for cat in CATEGORIES + ["misc"]:
            entries = data.get(cat, [])
            merged[cat].extend(entries)

    return merged


def _normalize_name(name: str) -> str:
    """标准化实体名用于去重比较"""
    # 去掉空格、括号内容、标点
    name = re.sub(r'[（(].*?[）)]', '', name)
    name = re.sub(r'[·\s\-_]', '', name)
    return name.strip()


def _names_match(a: str, b: str) -> bool:
    """判断两个名称是否指向同一实体"""
    na = _normalize_name(a)
    nb = _normalize_name(b)
    if na == nb:
        return True
    # 包含关系：短名是长名的子串
    if len(na) >= 2 and len(nb) >= 2:
        if na in nb or nb in na:
            return True
    return False


def merge_and_dedup_entities(build_dir: Path) -> dict:
    """Phase 2a: 合并所有 seg JSON，按 name 相似度去重"""
    merged_raw = merge_segments(build_dir)

    result = {}
    for cat in CATEGORIES:
        entries = merged_raw.get(cat, [])
        # misc 中的条目尝试归入最合适的类别（保持兼容，这里直接丢弃 misc）
        deduped = []
        for entry in entries:
            name = entry.get("name", "").strip()
            if not name:
                continue

            # 查找是否已有匹配的实体
            found = False
            for existing in deduped:
                if _names_match(existing["name"], name):
                    # 合并：保留更长的描述
                    if len(entry.get("description", "")) > len(existing.get("description", "")):
                        existing["description"] = entry["description"]
                    # 保留更长的名称
                    if len(name) > len(existing["name"]):
                        existing["name"] = name
                    # 合并 source_chapters
                    src = set(existing.get("source_chapters", []))
                    src.update(entry.get("source_chapters", []))
                    existing["source_chapters"] = sorted(src)
                    # 合并 evolution
                    if entry.get("evolution") and entry["evolution"] not in existing.get("evolution", ""):
                        existing["evolution"] = (existing.get("evolution", "") + "；" + entry["evolution"]).strip("；")
                    # 更新 first_chapter（取更早的）
                    if entry.get("first_chapter", "z") < existing.get("first_chapter", "z"):
                        existing["first_chapter"] = entry["first_chapter"]
                    found = True
                    break

            if not found:
                deduped.append({
                    "name": name,
                    "description": entry.get("description", ""),
                    "first_chapter": entry.get("first_chapter", ""),
                    "source_chapters": sorted(set(entry.get("source_chapters", []))),
                    "evolution": entry.get("evolution", ""),
                })

        result[cat] = deduped

    # misc 条目分配到最可能的类别（简单启发式）
    for entry in merged_raw.get("misc", []):
        name = entry.get("name", "").strip()
        if not name:
            continue
        # 默认放 rules
        target = "rules"
        desc = (name + " " + entry.get("description", "")).lower()
        if any(kw in desc for kw in ["修炼", "法术", "功法", "法宝", "灵气", "修为"]):
            target = "power_system"
        elif any(kw in desc for kw in ["山", "河", "村", "城", "湖", "地", "天地", "空间"]):
            target = "geography"
        elif any(kw in desc for kw in ["宗", "门", "派", "族", "帮", "会", "势力"]):
            target = "factions"
        result[target].append({
            "name": name,
            "description": entry.get("description", ""),
            "first_chapter": entry.get("first_chapter", ""),
            "source_chapters": sorted(set(entry.get("source_chapters", []))),
            "evolution": entry.get("evolution", ""),
        })

    return result


def build_entity_expand_prompt(category_key: str, entities: list[dict]) -> str:
    """构建 Phase 2b 实体扩写 prompt"""
    template = (PROMPTS_DIR / "world_entity_expand.md").read_text(encoding="utf-8")
    prompt = template.replace("{category_name}", CATEGORY_NAMES[category_key])
    prompt = prompt.replace("{category_key}", category_key)
    prompt = prompt.replace("{entities_json}", json.dumps(entities, ensure_ascii=False, indent=2))
    return prompt


def phase_entity_expand(build_dir: Path, merged_entities: dict,
                        progress: dict, progress_path: Path,
                        model: str, timeout: int, dry_run: bool,
                        concurrency: int = 1):
    """Phase 2b: AI 标注重要性 + 扩写描述"""
    completed_cats = set(progress["entity_expand"].get("categories_completed", []))
    pending_cats = [cat for cat in CATEGORIES if cat not in completed_cats and merged_entities.get(cat)]

    if not pending_cats:
        print("  所有类别扩写已完成")
        return merged_entities  # 返回已有数据

    print(f"  待扩写类别: {len(pending_cats)} ({', '.join(CATEGORY_NAMES[c] for c in pending_cats)})")

    if dry_run:
        for cat in pending_cats:
            print(f"    {CATEGORY_NAMES[cat]}: {len(merged_entities[cat])} 个实体")
        return merged_entities

    # 尝试加载已有的扩写结果
    expanded_path = build_dir / "expanded_entities.json"
    if expanded_path.exists():
        with open(expanded_path, "r", encoding="utf-8") as f:
            expanded = json.load(f)
    else:
        expanded = {}

    print_lock = threading.Lock()

    def expand_one_category(cat: str, verbose: bool = True) -> tuple[str, list[dict] | None]:
        entities = merged_entities[cat]
        with print_lock:
            print(f"    扩写 {CATEGORY_NAMES[cat]}（{len(entities)} 个实体）...")

        prompt = build_entity_expand_prompt(cat, entities)
        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
        elapsed = time.time() - start_time

        # 更新统计
        lock_path = progress_path.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = _read_raw(progress_path)
                disk["stats"]["total_calls"] = disk["stats"].get("total_calls", 0) + 1
                disk["stats"]["total_time_seconds"] = disk["stats"].get("total_time_seconds", 0) + int(elapsed)
                if success:
                    debug_path = build_dir / f"expand_{cat}_json_debug.txt"
                    result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
                    if result and isinstance(result, list):
                        if cat not in disk["entity_expand"].get("categories_completed", []):
                            disk.setdefault("entity_expand", {}).setdefault("categories_completed", []).append(cat)
                        with open(progress_path, "w", encoding="utf-8") as f:
                            json.dump(disk, f, ensure_ascii=False, indent=2)
                        progress.update(disk)
                        with print_lock:
                            print(f"    [{CATEGORY_NAMES[cat]}] ✓ ({elapsed:.0f}s) {len(result)} 个实体")
                        return cat, result
                    else:
                        with print_lock:
                            print(f"    [{CATEGORY_NAMES[cat]}] ✗ JSON 解析失败 ({elapsed:.0f}s)")
                else:
                    with print_lock:
                        print(f"    [{CATEGORY_NAMES[cat]}] ✗ ({elapsed:.0f}s): {output[:100]}")
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
                progress.update(disk)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        return cat, None

    concurrency = max(1, concurrency)
    if concurrency == 1:
        for cat in pending_cats:
            cat_key, result = expand_one_category(cat)
            if result:
                expanded[cat_key] = result
    else:
        print(f"  并发数: {concurrency}")
        with ThreadPoolExecutor(max_workers=min(concurrency, len(pending_cats))) as executor:
            futures = {executor.submit(expand_one_category, c, verbose=False): c for c in pending_cats}
            for future in as_completed(futures):
                try:
                    cat_key, result = future.result()
                    if result:
                        expanded[cat_key] = result
                except Exception as e:
                    with print_lock:
                        print(f"    ✗ 线程异常 ({futures[future]}): {e}")

    # 保存扩写结果
    with open(expanded_path, "w", encoding="utf-8") as f:
        json.dump(expanded, f, ensure_ascii=False, indent=2)

    # 对于已完成但不在 pending 中的类别，从已有数据补充
    for cat in CATEGORIES:
        if cat in completed_cats and cat not in expanded:
            expanded[cat] = merged_entities.get(cat, [])

    return expanded


def generate_entity_files(world_dir: Path, expanded_entities: dict,
                          character_factions: list[dict]):
    """Phase 2c: 为每个实体生成独立 .md 文件 + index.md"""
    print("  生成实体文件...")

    total_files = 0

    for cat in CATEGORIES:
        cat_dir = world_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        entities = expanded_entities.get(cat, [])
        if not entities:
            continue

        # 生成每个实体的独立文件
        index_rows = []
        for entity in entities:
            name = entity.get("name", "unnamed")
            filename = sanitize_filename(name) + ".md"
            filepath = cat_dir / filename

            importance = entity.get("importance", "low")
            first_ch = entity.get("first_chapter", "")
            source_chs = entity.get("source_chapters", [])
            description = entity.get("description", "")
            evolution = entity.get("evolution", "")
            related = entity.get("related_entities", [])

            # 如果是势力类别，补充角色成员
            members_section = ""
            if cat == "factions":
                for cf in character_factions:
                    if _names_match(cf["faction"], name):
                        members = cf.get("members", [])
                        if members:
                            members_section = f"\n\n## 已知成员\n\n" + "\n".join(f"- {m}" for m in members)
                        break

            # 构建实体文件内容
            lines = [f"# {name}\n"]
            lines.append(f"**分类**：{CATEGORY_NAMES[cat]}")
            lines.append(f"**重要性**：{importance}")
            if first_ch:
                lines.append(f"**首次提及**：{first_ch}")
            if source_chs:
                lines.append(f"**相关章节**：{', '.join(source_chs)}")
            if related:
                lines.append(f"**关联实体**：{', '.join(related)}")

            lines.append(f"\n## 描述\n\n{description}")

            if evolution:
                lines.append(f"\n## 演化记录\n\n{evolution}")

            if members_section:
                lines.append(members_section)

            filepath.write_text("\n".join(lines), encoding="utf-8")
            total_files += 1

            # 简介：取描述前 80 字
            brief = description[:80].replace("\n", " ").strip()
            if len(description) > 80:
                brief += "..."
            index_rows.append({
                "name": name,
                "filename": filename,
                "importance": importance,
                "first_chapter": first_ch,
                "brief": brief,
            })

        # 生成类别 index.md
        index_lines = [f"# {CATEGORY_NAMES[cat]}\n"]
        index_lines.append("| 名称 | 重要性 | 首次提及 | 简介 |")
        index_lines.append("|------|--------|----------|------|")

        # 按重要性排序：high > medium > low
        importance_order = {"high": 0, "medium": 1, "low": 2}
        index_rows.sort(key=lambda r: (importance_order.get(r["importance"], 3), r["first_chapter"]))

        for row in index_rows:
            index_lines.append(
                f"| [{row['name']}]({row['filename']}) "
                f"| {row['importance']} "
                f"| {row['first_chapter']} "
                f"| {row['brief']} |"
            )
        index_lines.append("")

        (cat_dir / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
        print(f"    {CATEGORY_NAMES[cat]}: {len(entities)} 个实体文件 + index.md")

    print(f"  共生成 {total_files} 个实体文件")


def phase_global_merge(book_dir: Path, progress: dict, progress_path: Path,
                       model: str, timeout: int, dry_run: bool,
                       concurrency: int = 1):
    """执行阶段 2：全局去重合并 + AI 扩写 + 生成实体文件"""
    print("\n" + "=" * 60)
    print("阶段 2：全局去重合并 + 实体扩写")
    print("=" * 60)

    if progress["global_merge"]["status"] == "completed":
        print("全局融合已完成！")
        return

    build_dir = get_build_dir(book_dir)
    world_dir = get_world_dir(book_dir)

    # --- Phase 2a: Python 全局去重合并 ---
    print("\n--- Phase 2a: Python 全局去重合并 ---")
    merged_path = build_dir / "merged_entities.json"

    if merged_path.exists():
        print("  已有合并数据，加载中...")
        with open(merged_path, "r", encoding="utf-8") as f:
            merged_entities = json.load(f)
    else:
        merged_entities = merge_and_dedup_entities(build_dir)
        with open(merged_path, "w", encoding="utf-8") as f:
            json.dump(merged_entities, f, ensure_ascii=False, indent=2)

    total_entities = sum(len(v) for v in merged_entities.values())
    if total_entities == 0:
        print("  警告：无分类数据，请先运行阶段 1")
        return

    print(f"  去重后总实体数: {total_entities}")
    for cat in CATEGORIES:
        n = len(merged_entities.get(cat, []))
        if n > 0:
            print(f"    {CATEGORY_NAMES[cat]}: {n} 条")

    if dry_run:
        print("  [dry-run] 将为每个类别调用 AI 进行重要性标注和描述扩写")
        return

    # --- Phase 2b: AI 重要性标注 + 描述扩写 ---
    print("\n--- Phase 2b: AI 重要性标注 + 描述扩写 ---")
    expanded_entities = phase_entity_expand(
        build_dir, merged_entities, progress, progress_path,
        model, timeout, dry_run, concurrency
    )

    # --- Phase 2c: Python 生成实体文件 ---
    print("\n--- Phase 2c: 生成实体文件 ---")

    # 加载角色势力信息
    raw_path = build_dir / "raw_world_data.json"
    character_factions = []
    if raw_path.exists():
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        character_factions = raw_data.get("character_factions", [])

    generate_entity_files(world_dir, expanded_entities, character_factions)

    # 生成顶层 index.md
    build_index_md(world_dir, expanded_entities)

    progress["global_merge"]["status"] = "completed"
    progress["phase"] = "completed"
    _save_progress(progress_path, progress)
    print("\n全局融合完成！")


# ============================================================
# index.md 生成
# ============================================================

def build_index_md(world_dir: Path, expanded_entities: dict = None):
    """生成顶层 world/index.md"""
    lines = ["# 世界观总览\n"]

    # 统计各类别
    cat_stats = []
    for cat in CATEGORIES:
        cat_dir = world_dir / cat
        if not cat_dir.exists():
            continue
        # 统计实体文件数（排除 index.md）
        entity_files = [f for f in cat_dir.glob("*.md") if f.name != "index.md"]
        n_entities = len(entity_files)

        # 统计 high/medium/low
        n_high = 0
        n_medium = 0
        n_low = 0
        if expanded_entities and cat in expanded_entities:
            for e in expanded_entities[cat]:
                imp = e.get("importance", "low")
                if imp == "high":
                    n_high += 1
                elif imp == "medium":
                    n_medium += 1
                else:
                    n_low += 1

        cat_stats.append({
            "cat": cat,
            "name": CATEGORY_NAMES[cat],
            "total": n_entities,
            "high": n_high,
            "medium": n_medium,
            "low": n_low,
        })

    if cat_stats:
        lines.append("## 统计\n")
        lines.append("| 类别 | 总数 | high | medium | low |")
        lines.append("|------|------|------|--------|-----|")
        total_all = 0
        total_h = 0
        total_m = 0
        total_l = 0
        for cs in cat_stats:
            lines.append(
                f"| [{cs['name']}]({cs['cat']}/index.md) "
                f"| {cs['total']} | {cs['high']} | {cs['medium']} | {cs['low']} |"
            )
            total_all += cs["total"]
            total_h += cs["high"]
            total_m += cs["medium"]
            total_l += cs["low"]
        lines.append(f"| **合计** | **{total_all}** | **{total_h}** | **{total_m}** | **{total_l}** |")
        lines.append("")

    # 文件导航
    lines.append("## 类别导航\n")
    for cat in CATEGORIES:
        cat_dir = world_dir / cat
        if cat_dir.exists() and (cat_dir / "index.md").exists():
            lines.append(f"- [{CATEGORY_NAMES[cat]}]({cat}/index.md)")
        else:
            lines.append(f"- {CATEGORY_NAMES[cat]}（未生成）")

    lines.append("")

    index_path = world_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  已生成: {index_path}")


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
        ("merged_entities.json", "全局去重合并"),
        ("expanded_entities.json", "AI 扩写结果"),
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

    # 检查各类别实体文件
    total_entity_files = 0
    total_high = 0
    for cat in CATEGORIES:
        cat_dir = world_dir / cat
        if cat_dir.exists():
            entity_files = [f for f in cat_dir.glob("*.md") if f.name != "index.md"]
            n = len(entity_files)
            total_entity_files += n
            # 统计 high
            n_high = 0
            for ef in entity_files:
                content = ef.read_text(encoding="utf-8")
                if "**重要性**：high" in content:
                    n_high += 1
            total_high += n_high
            has_index = (cat_dir / "index.md").exists()
            results.append((
                CATEGORY_NAMES[cat], "OK",
                f"{n} 实体 ({n_high} high), index: {'✓' if has_index else '✗'}"
            ))
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

    print(f"\n总计: {total_entity_files} 个实体文件 ({total_high} 个 high)")

    # 加载进度统计
    progress_path = get_progress_path(book_dir)
    if progress_path.exists():
        progress = _load_progress(progress_path)
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

    all_chapters = list_chapter_files(chapters_dir)
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
    progress = _load_progress(progress_path)
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
        progress = _load_progress(progress_path)
        if progress["phase"] == "segment_classify" or args.phase == "segment-classify":
            phase_segment_classify(book_dir, all_chapters, progress, progress_path,
                                   args.model, args.timeout, args.dry_run,
                                   args.segment_size, args.concurrency)

    # 阶段 2: 全局融合
    if args.phase == "global-merge" or (
        not args.phase and progress["phase"] in ("global_merge", "segment_classify")
    ):
        progress = _load_progress(progress_path)
        if progress["phase"] == "global_merge" or args.phase == "global-merge":
            phase_global_merge(book_dir, progress, progress_path,
                               args.model, args.timeout, args.dry_run,
                               args.concurrency)

    # 最终统计
    progress = _load_progress(progress_path)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    print(f"  预处理: {progress['preprocess']['status']}")
    sc = progress["segment_classify"]
    print(f"  分段分类: {sc['status']} ({len(sc['segments_completed'])} 完成/"
          f"{len(sc['segments_failed'])} 失败)")
    print(f"  全局融合: {progress['global_merge']['status']}")
    ee = progress.get("entity_expand", {})
    print(f"  实体扩写: {ee.get('status', 'pending')} ({len(ee.get('categories_completed', []))} 类别)")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    # 检查是否全部完成
    all_done = (
        progress["preprocess"]["status"] == "completed"
        and progress["segment_classify"]["status"] == "completed"
        and progress["global_merge"]["status"] == "completed"
    )
    if all_done:
        print("\nT5 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
