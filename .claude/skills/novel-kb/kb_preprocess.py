#!/usr/bin/env python3
"""
统一预处理 — 合并 T4 phase 0 + T5 phase 0

一次遍历所有 T2 章节摘要 + T3 弧文件，同时提取：
  - 角色数据（原 T4 phase_preprocess / build_raw_census）
  - 世界观数据（原 T5 phase_preprocess / build_raw_world_data）

产出: {book_dir}/.cache/preprocess.json

效果：
  - 旧方案：T4 + T5 各遍历 1200 文件 = 2400 次文件读取
  - 新方案：统一 1 次遍历 = 1200 次文件读取（减少 50%）
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from kb_common import (
    resolve_book_dir, parse_section, parse_setting_lines,
    list_chapter_files, print_flush,
)


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="统一预处理（T4+T5 phase 0 合并）")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--force", action="store_true", help="强制重新生成缓存")
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
        print(f"警告：outline 目录不存在: {d}")
        print("T3（剧情层）未完成，弧信息将缺失")
    return d


def get_characters_dir(book_dir: Path) -> Path:
    d = book_dir / "characters"
    if not d.exists():
        print(f"警告：characters 目录不存在: {d}")
        print("T4（角色层）未完成，势力信息将缺失")
    return d


def get_cache_dir(book_dir: Path) -> Path:
    d = book_dir / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cache_path(book_dir: Path) -> Path:
    return get_cache_dir(book_dir) / "preprocess.json"


# ============================================================
# 解析工具（原 T4）
# ============================================================

def parse_character_lines(section_text: str) -> list[tuple[str, str]]:
    """解析角色行列表，返回 [(角色名, 描述), ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        m = re.match(r"- (.+?)[:：](.+)", line)
        if m:
            results.append((m.group(1).strip(), m.group(2).strip()))
    return results


def parse_relationship_lines(section_text: str) -> list[dict]:
    """解析关系变化行，返回 [{char_a, char_b, description}, ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        m = re.match(r"- (.+?)(?:与|和|、)(.+?)[:：](.+)", line)
        if m:
            results.append({
                "char_a": m.group(1).strip(),
                "char_b": m.group(2).strip(),
                "description": m.group(3).strip(),
            })
    return results


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
# 核心：统一遍历章节摘要
# ============================================================

def parse_all_chapters_unified(
    chapters_dir: Path, all_chapters: list[int]
) -> dict:
    """一次遍历全部章节摘要，同时提取角色 + 世界观数据

    Returns:
        {
            "characters": {name: {identity, first_appearance, appearance_chapters, key_actions}},
            "relationship_timeline": [{ch, char_a, char_b, description}],
            "chapter_settings": {ch_str: {settings: [...], items: [...]}},
        }
    """
    characters = {}
    relationship_timeline = []
    chapter_settings = {}

    for ch in all_chapters:
        filepath = chapters_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue

        ch_str = f"ch{ch:04d}"
        content = filepath.read_text(encoding="utf-8")

        # ---- 角色信息（原 T4 parse_chapter_summary） ----
        new_chars = parse_character_lines(parse_section(content, "新登场角色"))
        existing_chars = parse_character_lines(parse_section(content, "已有角色出场"))
        relationships = parse_relationship_lines(parse_section(content, "关系变化"))

        # 新登场角色
        for name, desc in new_chars:
            if name not in characters:
                characters[name] = {
                    "identity": desc,
                    "first_appearance": ch_str,
                    "appearance_chapters": [],
                    "key_actions": [],
                }
            characters[name]["appearance_chapters"].append(ch_str)
            characters[name]["key_actions"].append({
                "ch": ch_str, "action": f"[首次出场] {desc}",
            })

        # 已有角色出场
        for name, desc in existing_chars:
            if name not in characters:
                characters[name] = {
                    "identity": desc,
                    "first_appearance": ch_str,
                    "appearance_chapters": [],
                    "key_actions": [],
                }
            if ch_str not in characters[name]["appearance_chapters"]:
                characters[name]["appearance_chapters"].append(ch_str)
            characters[name]["key_actions"].append({
                "ch": ch_str, "action": desc,
            })

        # 关系变化
        for rel in relationships:
            relationship_timeline.append({
                "ch": ch_str,
                "char_a": rel["char_a"],
                "char_b": rel["char_b"],
                "description": rel["description"],
            })

        # ---- 世界观设定（原 T5 parse_chapter_settings） ----
        settings = parse_setting_lines(parse_section(content, "新增设定"))
        items = parse_setting_lines(parse_section(content, "重要物品"))

        if settings or items:
            chapter_settings[ch_str] = {"settings": settings, "items": items}

    return {
        "characters": characters,
        "relationship_timeline": relationship_timeline,
        "chapter_settings": chapter_settings,
    }


# ============================================================
# 弧文件解析（一次读取，同时提取角色和世界信息）
# ============================================================

def parse_arcs_unified(outline_dir: Path) -> dict:
    """一次遍历 T3 弧文件，同时提取角色和世界信息

    Returns:
        {
            "arc_roles": {name: [{arc, role}]},
            "arc_world_info": [{arc, arc_file, ch_range, entries: [{name, description}]}],
        }
    """
    arc_roles = {}
    arc_world_info = []

    if not outline_dir.exists():
        return {"arc_roles": arc_roles, "arc_world_info": arc_world_info}

    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")

        # 弧标题
        title_match = re.match(r"# (?:弧 \d+:\s*)?(.+)", content)
        arc_name = title_match.group(1).strip() if title_match else arc_file.stem

        # 章节范围
        range_match = re.search(
            r"\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)", content
        )
        ch_start = int(range_match.group(1)) if range_match else 0
        ch_end = int(range_match.group(2)) if range_match else 0

        # ---- 角色信息（原 T4 parse_arc_characters） ----
        chars_section = parse_section(content, "主要角色")
        for entry in parse_bold_lines(chars_section):
            name = entry["name"]
            role = entry["description"]
            if name not in arc_roles:
                arc_roles[name] = []
            arc_roles[name].append({"arc": arc_name, "role": role})

        # ---- 世界信息（原 T5 parse_arc_world_info） ----
        world_section = parse_section(content, "新世界信息")
        entries = parse_bold_lines(world_section)
        if entries:
            arc_world_info.append({
                "arc": arc_name,
                "arc_file": arc_file.name,
                "ch_range": f"ch{ch_start:04d}-ch{ch_end:04d}",
                "entries": entries,
            })

    return {"arc_roles": arc_roles, "arc_world_info": arc_world_info}


# ============================================================
# 辅助数据源
# ============================================================

def parse_progress_characters(chapters_dir: Path) -> dict:
    """读取 T2 进度文件中的累积角色名册"""
    progress_path = chapters_dir / ".progress.json"
    if not progress_path.exists():
        return {}
    with open(progress_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("characters", {})


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
            org_matches = re.findall(
                r"([\u4e00-\u9fff]+(?:宗|门|派|族|帮|会|盟|教|谷|阁|堡|庄|楼))",
                identity,
            )
            for org in org_matches:
                if org not in factions:
                    factions[org] = []
                if char_name not in factions[org]:
                    factions[org].append(char_name)

    return [
        {"faction": org_name, "members": members, "source": "characters"}
        for org_name, members in factions.items()
    ]


# ============================================================
# 主函数：统一预处理
# ============================================================

def preprocess_all(book_dir: Path) -> dict:
    """一次性读取所有 T2 摘要 + T3 弧文件，提取全部结构化数据

    Returns:
        {
            # 原 T4 phase 0 产出（raw_census）
            "characters": {name: {...}},
            "relationship_timeline": [{...}],
            "arc_roles": {name: [{arc, role}]},

            # 原 T5 phase 0 产出（raw_world_data）
            "chapter_settings": {ch_str: {settings, items}},
            "arc_world_info": [{arc, entries}],
            "character_factions": [{faction, members, source}],

            # 元数据
            "stats": {...}
        }
    """
    chapters_dir = get_chapters_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)
    characters_dir = get_characters_dir(book_dir)

    all_chapters = list_chapter_files(chapters_dir)
    if not all_chapters:
        print(f"错误：chapters 目录中没有摘要文件: {chapters_dir}")
        sys.exit(1)

    print_flush(f"统一预处理: {len(all_chapters)} 章摘要")

    # 1. 一次遍历所有章节摘要（核心优化点）
    t0 = time.time()
    chapter_data = parse_all_chapters_unified(chapters_dir, all_chapters)
    t_chapters = time.time() - t0
    print_flush(f"  章节遍历: {t_chapters:.1f}s "
                f"({len(chapter_data['characters'])} 角色, "
                f"{len(chapter_data['chapter_settings'])} 章含设定)")

    # 2. 一次遍历弧文件（同时提取角色+世界信息）
    t0 = time.time()
    arc_data = parse_arcs_unified(outline_dir)
    t_arcs = time.time() - t0
    print_flush(f"  弧文件: {t_arcs:.1f}s "
                f"({len(arc_data['arc_roles'])} 弧角色, "
                f"{len(arc_data['arc_world_info'])} 弧世界条目)")

    # 3. T2 进度文件角色名册
    t2_characters = parse_progress_characters(chapters_dir)
    print_flush(f"  T2 角色名册: {len(t2_characters)} 个")

    # 4. T4 角色档案势力归属
    character_factions = parse_character_factions(characters_dir)
    print_flush(f"  角色势力: {len(character_factions)} 个")

    # 5. 合并角色数据（原 build_raw_census 的合并逻辑）
    characters = chapter_data["characters"]

    # 补充 T2 进度文件中有但摘要解析中没有的角色
    for name, desc in t2_characters.items():
        if name not in characters:
            characters[name] = {
                "identity": desc,
                "first_appearance": "unknown",
                "appearance_chapters": [],
                "key_actions": [],
            }
        if desc and len(desc) > len(characters[name].get("identity", "")):
            characters[name]["t2_identity"] = desc

    # 添加弧角色信息
    for name, roles in arc_data["arc_roles"].items():
        if name in characters:
            characters[name]["arc_roles"] = roles
        else:
            characters[name] = {
                "identity": "",
                "first_appearance": "unknown",
                "appearance_chapters": [],
                "key_actions": [],
                "arc_roles": roles,
            }

    # 添加统计信息
    for name, data in characters.items():
        data["appearance_count"] = len(data["appearance_chapters"])

    # 生成 raw_relationships（按角色汇总）
    for rel in chapter_data["relationship_timeline"]:
        for role_name in [rel["char_a"], rel["char_b"]]:
            if role_name in characters:
                if "raw_relationships" not in characters[role_name]:
                    characters[role_name]["raw_relationships"] = []
                target = rel["char_b"] if role_name == rel["char_a"] else rel["char_a"]
                characters[role_name]["raw_relationships"].append({
                    "ch": rel["ch"],
                    "target": target,
                    "description": rel["description"],
                })

    # 6. 汇总统计
    total_settings = sum(
        len(v["settings"]) + len(v["items"])
        for v in chapter_data["chapter_settings"].values()
    )
    total_arc_entries = sum(
        len(a["entries"]) for a in arc_data["arc_world_info"]
    )

    result = {
        # 角色数据（兼容 T4 raw_census 格式）
        "characters": characters,
        "relationship_timeline": chapter_data["relationship_timeline"],

        # 世界观数据（兼容 T5 raw_world_data 格式）
        "chapter_settings": chapter_data["chapter_settings"],
        "arc_world_info": arc_data["arc_world_info"],
        "character_factions": character_factions,

        # 统计
        "stats": {
            # 角色统计
            "total_characters": len(characters),
            "total_relationships": len(chapter_data["relationship_timeline"]),
            # 世界观统计
            "total_chapters_with_settings": len(chapter_data["chapter_settings"]),
            "total_setting_entries": total_settings,
            "total_arc_entries": total_arc_entries,
            "total_factions_from_characters": len(character_factions),
            # 共享统计
            "total_chapters_parsed": len(all_chapters),
        },
    }

    return result


def run_preprocess(book_dir: Path, dry_run: bool = False,
                   force: bool = False) -> Path:
    """执行统一预处理并保存缓存

    Args:
        book_dir: 知识库目录
        dry_run: 试运行
        force: 强制重新生成

    Returns:
        缓存文件路径
    """
    cache_path = get_cache_path(book_dir)

    if cache_path.exists() and not force:
        print_flush(f"预处理缓存已存在: {cache_path}")
        print_flush("使用 --force 强制重新生成")
        return cache_path

    if dry_run:
        chapters_dir = get_chapters_dir(book_dir)
        all_chapters = list_chapter_files(chapters_dir)
        print_flush(f"[试运行] 将解析 {len(all_chapters)} 章摘要 + T3 弧文件")
        print_flush(f"[试运行] 产出: {cache_path}")
        return cache_path

    t0 = time.time()
    result = preprocess_all(book_dir)
    elapsed = time.time() - t0

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    stats = result["stats"]
    print_flush(f"\n统一预处理完成 ({elapsed:.1f}s)")
    print_flush(f"  角色: {stats['total_characters']} 个")
    print_flush(f"  关系: {stats['total_relationships']} 条")
    print_flush(f"  含设定章节: {stats['total_chapters_with_settings']} 章")
    print_flush(f"  设定条目: {stats['total_setting_entries']} 条")
    print_flush(f"  弧世界信息: {stats['total_arc_entries']} 条")
    print_flush(f"  角色势力: {stats['total_factions_from_characters']} 个")
    print_flush(f"  缓存: {cache_path}")

    return cache_path


def load_preprocess_cache(book_dir: Path) -> dict:
    """加载预处理缓存

    如果缓存不存在，自动执行预处理。
    """
    cache_path = get_cache_path(book_dir)
    if not cache_path.exists():
        print_flush("预处理缓存不存在，开始自动预处理...")
        run_preprocess(book_dir)

    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 兼容层：为 T4/T5 提供与原格式一致的数据
# ============================================================

def get_raw_census(cache: dict) -> dict:
    """从统一缓存中提取 T4 raw_census 格式的数据"""
    return {
        "characters": cache["characters"],
        "relationship_timeline": cache["relationship_timeline"],
        "stats": {
            "total_characters": cache["stats"]["total_characters"],
            "total_chapters_parsed": cache["stats"]["total_chapters_parsed"],
            "total_relationships": cache["stats"]["total_relationships"],
        },
    }


def get_raw_world_data(cache: dict) -> dict:
    """从统一缓存中提取 T5 raw_world_data 格式的数据"""
    return {
        "chapter_settings": cache["chapter_settings"],
        "arc_world_info": cache["arc_world_info"],
        "character_factions": cache["character_factions"],
        "stats": {
            "total_chapters_with_settings": cache["stats"]["total_chapters_with_settings"],
            "total_setting_entries": cache["stats"]["total_setting_entries"],
            "total_arc_entries": cache["stats"]["total_arc_entries"],
            "total_factions_from_characters": cache["stats"]["total_factions_from_characters"],
        },
    }


# ============================================================
# CLI 入口
# ============================================================

def main():
    args = parse_args()
    book_dir = resolve_book_dir(args.book_dir)

    print_flush(f"知识库目录: {book_dir}")
    run_preprocess(book_dir, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
