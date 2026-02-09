#!/usr/bin/env python3
"""
T4 角色层提取 — 批量编排脚本

从 T2 章节摘要 + T3 剧情层产出中提取角色信息，构建完整人物库。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T2 已完成: plot/chapters/ 下有章节摘要 (chXXXX.md)
  - T3 已完成: plot/outline/ 下有弧文件 (arc_XX.md) 和 plot_lines.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
五阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 0  preprocess    Python 预处理，从摘要中提取角色名册
                        产出: characters/.build/raw_census.json
                        耗时: ~10s, 0 次 AI 调用

  阶段 1  alias-merge   别名合并与分级（分批调 Claude）
                        产出: characters/.build/census.json, alias_mapping.json
                        耗时: ~5min, 每 150 角色 1 次 AI + 1 次跨批次检测

  阶段 2  deep-dive     逐角色生成详细档案（支持并发）
                        产出: characters/{name}.md（核心 5 模块 / 重要 3 模块）
                        耗时: 取决于角色数，每角色 1 次 AI（高章节数分批提取）

  阶段 3  relationship  关系网构建 + 交叉验证
                        产出: characters/relationships.md
                        耗时: ~2min, 2 次 AI 调用

  阶段 4  status-update 活跃角色当前状态精修 + 生成 index.md
                        产出: characters/index.md + 更新各角色档案的当前状态
                        耗时: ~2min, 1 次 AI 调用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（从当前进度自动继续，直到全部完成）
  python batch_character.py --book-dir qidian/novel_kb/玄鉴仙族

  # 全流程 + deep-dive 阶段 10 并发加速
  python batch_character.py --book-dir ... --concurrency 10

  # 只运行某个阶段（跳过前面已完成的，不执行后面的）
  python batch_character.py --book-dir ... --phase preprocess
  python batch_character.py --book-dir ... --phase alias-merge
  python batch_character.py --book-dir ... --phase deep-dive --concurrency 10
  python batch_character.py --book-dir ... --phase relationship
  python batch_character.py --book-dir ... --phase status-update

  # deep-dive 只处理单个角色（调试用）
  python batch_character.py --book-dir ... --phase deep-dive --character "李木田"

  # 试运行（不实际调用 AI，只显示将要做什么）
  python batch_character.py --book-dir ... --dry-run

  # 验证产出完整性
  python batch_character.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  --book-dir PATH      知识库目录（必须）
  --phase PHASE        只运行指定阶段（可选，默认从当前进度继续）
  --character NAME     只处理指定角色，仅 deep-dive 阶段有效（可选）
  --concurrency N      deep-dive 阶段并发数（默认 1，推荐 5-10）
  --model MODEL        Claude 模型（默认 sonnet）
  --timeout SECONDS    单次 AI 调用超时（默认 600s）
  --dry-run            试运行，不实际调用 AI
  --validate           验证所有产出文件的完整性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {book-dir}/characters/
  ├── index.md                  # 人物索引（阶段 4 生成）
  ├── relationships.md          # 关系网（阶段 3 生成）
  ├── {name}.md                 # 各角色详细档案（阶段 2 生成）
  ├── .progress.json            # 进度文件（断点续传）
  └── .build/                   # 中间产物
      ├── raw_census.json       # 阶段 0: 原始角色名册
      ├── census.json           # 阶段 1: 合并后角色名册
      ├── alias_mapping.json    # 阶段 1: 别名映射表
      └── alias_batch_*.json    # 阶段 1: 各批次结果
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

try:
    from pypinyin import lazy_pinyin, Style
    HAS_PYPINYIN = True
except ImportError:
    HAS_PYPINYIN = False

# Skill 目录
SKILL_DIR = Path(__file__).parent
PROMPTS_DIR = SKILL_DIR / "prompts"


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="角色层提取")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--phase",
                        choices=["preprocess", "alias-merge", "deep-dive",
                                 "relationship", "status-update"],
                        help="只运行特定阶段")
    parser.add_argument("--character", help="只处理特定角色（阶段 2 调试用）")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="deep-dive 阶段并发数（默认 1）")
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
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_build_dir(book_dir: Path) -> Path:
    d = book_dir / "characters" / ".build"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(book_dir: Path) -> Path:
    return book_dir / "characters" / ".progress.json"


# ============================================================
# 进度管理
# ============================================================

def _default_progress() -> dict:
    return {
        "phase": "preprocess",
        "preprocess": {
            "status": "pending",
            "total_characters_found": 0,
            "total_chapters_parsed": 0,
            "total_relationships_found": 0,
        },
        "alias_merge": {
            "status": "pending",
            "total_characters_merged": None,
            "core_count": None,
            "important_count": None,
            "minor_count": None,
        },
        "deep_dive": {
            "core_completed": [],
            "core_failed": [],
            "important_completed": [],
            "important_failed": [],
        },
        "relationship": {
            "built": False,
            "validated": False,
        },
        "status_update": {
            "completed": False,
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
            # 合并 deep_dive completed/failed（并集）
            for key in ("core_completed", "important_completed"):
                merged = sorted(set(disk["deep_dive"].get(key, [])) | set(progress["deep_dive"].get(key, [])))
                progress["deep_dive"][key] = merged
            for key in ("core_failed", "important_failed"):
                completed_key = key.replace("failed", "completed")
                merged = sorted(
                    (set(disk["deep_dive"].get(key, [])) | set(progress["deep_dive"].get(key, [])))
                    - set(progress["deep_dive"].get(completed_key, []))
                )
                progress["deep_dive"][key] = merged
            # stats 取 max
            progress["stats"]["total_calls"] = max(
                disk["stats"].get("total_calls", 0),
                progress["stats"].get("total_calls", 0))
            progress["stats"]["total_time_seconds"] = max(
                disk["stats"].get("total_time_seconds", 0),
                progress["stats"].get("total_time_seconds", 0))
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


def load_chapter_summary(chapters_dir: Path, ch_num: int) -> str:
    filepath = chapters_dir / f"ch{ch_num:04d}.md"
    if not filepath.exists():
        return f"[ch{ch_num:04d} 摘要缺失]"
    return filepath.read_text(encoding="utf-8")


def load_summaries_for_chapters(chapters_dir: Path, ch_list: list[int]) -> str:
    """加载指定章节列表的全部摘要，拼接为字符串"""
    parts = []
    for ch in sorted(ch_list):
        content = load_chapter_summary(chapters_dir, ch)
        parts.append(f"### ch{ch:04d}\n\n{content}")
    return "\n\n---\n\n".join(parts)


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
            # stderr 直接输出到终端（显示 Claude CLI 进度）
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
            # 静默模式（并发时避免输出交错）
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
# 拼音转换
# ============================================================

def name_to_pinyin(name: str) -> str:
    """中文名转拼音文件名"""
    if HAS_PYPINYIN:
        parts = lazy_pinyin(name, style=Style.NORMAL)
        return "_".join(parts)
    # fallback: 用 unicode 编码
    return "".join(f"{ord(c):x}" for c in name if c.strip())


# ============================================================
# 阶段 0：Python 预处理
# ============================================================

def parse_section(content: str, section_name: str) -> str:
    """从 Markdown 内容中提取指定 ## 段落的文本"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_character_lines(section_text: str) -> list[tuple[str, str]]:
    """解析角色行列表，返回 [(角色名, 描述), ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        m = re.match(r"- (.+?)[:：](.+)", line)
        if m:
            name = m.group(1).strip()
            desc = m.group(2).strip()
            results.append((name, desc))
    return results


def parse_relationship_lines(section_text: str) -> list[dict]:
    """解析关系变化行，返回 [{char_a, char_b, description}, ...]"""
    results = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "（无）" in line:
            continue
        # 尝试匹配: - A与B：描述 / - A和B：描述 / - A、B：描述
        m = re.match(r"- (.+?)(?:与|和|、)(.+?)[:：](.+)", line)
        if m:
            results.append({
                "char_a": m.group(1).strip(),
                "char_b": m.group(2).strip(),
                "description": m.group(3).strip(),
            })
    return results


def parse_chapter_summary(filepath: Path) -> dict:
    """解析单章摘要的角色相关字段"""
    content = filepath.read_text(encoding="utf-8")

    new_chars_section = parse_section(content, "新登场角色")
    new_characters = parse_character_lines(new_chars_section)

    existing_chars_section = parse_section(content, "已有角色出场")
    existing_characters = parse_character_lines(existing_chars_section)

    rel_section = parse_section(content, "关系变化")
    relationships = parse_relationship_lines(rel_section)

    return {
        "new_characters": new_characters,
        "existing_characters": existing_characters,
        "relationships": relationships,
    }


def parse_all_chapters(chapters_dir: Path, all_chapters: list[int]) -> dict:
    """遍历全部章节摘要，汇总角色信息"""
    characters = {}  # name -> {identity, first_appearance, appearance_chapters, key_actions}
    relationship_timeline = []

    for ch in all_chapters:
        filepath = chapters_dir / f"ch{ch:04d}.md"
        if not filepath.exists():
            continue

        ch_str = f"ch{ch:04d}"
        data = parse_chapter_summary(filepath)

        # 新登场角色
        for name, desc in data["new_characters"]:
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
        for name, desc in data["existing_characters"]:
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
        for rel in data["relationships"]:
            relationship_timeline.append({
                "ch": ch_str,
                "char_a": rel["char_a"],
                "char_b": rel["char_b"],
                "description": rel["description"],
            })

    return {
        "characters": characters,
        "relationship_timeline": relationship_timeline,
    }


def parse_arc_characters(outline_dir: Path) -> dict:
    """从 T3 弧文件中提取主要角色"""
    arc_roles = {}  # name -> [{arc, role}]

    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")

        # 解析弧标题
        title_match = re.match(r"# 弧 \d+:\s*(.+)", content)
        arc_name = title_match.group(1).strip() if title_match else arc_file.stem

        # 解析 ## 主要角色 段落
        chars_section = parse_section(content, "主要角色")
        for line in chars_section.splitlines():
            line = line.strip()
            m = re.match(r"- \*\*(.+?)\*\*[:：]\s*(.+)", line)
            if m:
                name = m.group(1).strip()
                role = m.group(2).strip()
                if name not in arc_roles:
                    arc_roles[name] = []
                arc_roles[name].append({"arc": arc_name, "role": role})

    return arc_roles


def parse_progress_characters(progress_path: Path) -> dict:
    """读取 T2 进度文件中的累积角色名册"""
    if not progress_path.exists():
        return {}
    with open(progress_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("characters", {})


def build_raw_census(book_dir: Path, all_chapters: list[int]) -> dict:
    """合并所有来源 → raw_census.json"""
    chapters_dir = get_chapters_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)

    # 1. 解析全部章节摘要
    chapter_data = parse_all_chapters(chapters_dir, all_chapters)

    # 2. 解析 T3 弧文件
    arc_roles = parse_arc_characters(outline_dir)

    # 3. 读取 T2 进度文件角色名册
    t2_progress_path = chapters_dir / ".progress.json"
    t2_characters = parse_progress_characters(t2_progress_path)

    # 4. 合并
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
        # 用 T2 进度文件的描述补充（通常更丰富）
        if desc and len(desc) > len(characters[name].get("identity", "")):
            characters[name]["t2_identity"] = desc

    # 添加弧角色信息
    for name, roles in arc_roles.items():
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

    return {
        "characters": characters,
        "relationship_timeline": chapter_data["relationship_timeline"],
        "stats": {
            "total_characters": len(characters),
            "total_chapters_parsed": len(all_chapters),
            "total_relationships": len(chapter_data["relationship_timeline"]),
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
        print(f"将解析 {len(all_chapters)} 章摘要 + T3 弧文件 + T2 进度文件")
        print(f"产出: characters/.build/raw_census.json")
        return

    raw_census = build_raw_census(book_dir, all_chapters)

    # 保存
    build_dir = get_build_dir(book_dir)
    output_path = build_dir / "raw_census.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_census, f, ensure_ascii=False, indent=2)

    stats = raw_census["stats"]
    print(f"找到 {stats['total_characters']} 个角色")
    print(f"解析了 {stats['total_chapters_parsed']} 章")
    print(f"提取了 {stats['total_relationships']} 条关系变化")

    # 列出角色
    for name, data in sorted(raw_census["characters"].items(),
                              key=lambda x: -x[1]["appearance_count"]):
        print(f"  {name}: 出场 {data['appearance_count']} 次 "
              f"(首次 {data['first_appearance']})")

    print(f"\n已保存: {output_path}")

    progress["preprocess"]["status"] = "completed"
    progress["preprocess"]["total_characters_found"] = stats["total_characters"]
    progress["preprocess"]["total_chapters_parsed"] = stats["total_chapters_parsed"]
    progress["preprocess"]["total_relationships_found"] = stats["total_relationships"]
    progress["phase"] = "alias_merge"
    save_progress(progress_path, progress)


# ============================================================
# 阶段 1：别名合并与分级
# ============================================================

MIN_APPEARANCE_FOR_ALIAS = 3  # 出场 < 3 次的角色自动归为 minor，不发给 Claude


def _compact_census_for_alias(raw_census: dict) -> dict:
    """精简 raw_census 以适配别名合并 prompt。

    1. 过滤：出场 < MIN_APPEARANCE_FOR_ALIAS 次的角色不发给 Claude（自动 minor）
    2. 精简：去掉 appearance_chapters、key_actions、raw_relationships 等大字段
    """
    compact_chars = {}
    skipped = 0
    for name, info in raw_census.get("characters", {}).items():
        if info.get("appearance_count", 0) < MIN_APPEARANCE_FOR_ALIAS:
            skipped += 1
            continue
        compact = {
            "identity": info.get("identity", ""),
            "first_appearance": info.get("first_appearance", "unknown"),
            "appearance_count": info.get("appearance_count", 0),
        }
        if info.get("t2_identity"):
            compact["t2_identity"] = info["t2_identity"]
        # arc_roles 只保留弧名和角色简述（截断过长的）
        if info.get("arc_roles"):
            compact["arc_roles"] = [
                {"arc": ar["arc"], "role": ar["role"][:80]}
                for ar in info["arc_roles"][:5]
            ]
        compact_chars[name] = compact
    return {
        "characters": compact_chars,
        "stats": raw_census.get("stats", {}),
        "note": f"已过滤 {skipped} 个出场<{MIN_APPEARANCE_FOR_ALIAS}次的角色（自动归为 minor）",
    }


ALIAS_BATCH_SIZE = 150  # 每批发给 Claude 的角色数


def build_alias_merge_prompt(char_batch: dict, plot_lines_content: str,
                             batch_label: str) -> str:
    """构建别名合并 prompt（单批）"""
    template = (PROMPTS_DIR / "char_alias_merge.md").read_text(encoding="utf-8")

    prompt = template.replace("{raw_census_json}",
                              json.dumps(char_batch, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{plot_lines_content}", plot_lines_content)
    return prompt


def _split_characters_into_batches(raw_census: dict) -> list[dict]:
    """将角色分批，每批 ALIAS_BATCH_SIZE 个（只含出场>=MIN_APPEARANCE_FOR_ALIAS的）。

    按出场次数降序排列，高频角色优先处理。
    """
    chars = raw_census.get("characters", {})
    # 过滤低频角色
    eligible = {k: v for k, v in chars.items()
                if v.get("appearance_count", 0) >= MIN_APPEARANCE_FOR_ALIAS}
    # 按出场次数降序
    sorted_names = sorted(eligible.keys(),
                          key=lambda n: eligible[n].get("appearance_count", 0),
                          reverse=True)

    batches = []
    for i in range(0, len(sorted_names), ALIAS_BATCH_SIZE):
        batch_names = sorted_names[i:i + ALIAS_BATCH_SIZE]
        batch_chars = {n: eligible[n] for n in batch_names}
        batches.append(batch_chars)
    return batches


def _build_cross_batch_summary(batch_results: list[dict]) -> list[dict]:
    """提取紧凑的跨批次角色摘要，用于别名检测。"""
    summary = []
    for i, result in enumerate(batch_results):
        for char in result.get("characters", []):
            entry = {
                "batch": i + 1,
                "name": char["canonical_name"],
                "identity": char.get("identity", ""),
                "count": char.get("appearance_count", 0),
            }
            if char.get("aliases"):
                entry["aliases"] = char["aliases"]
            summary.append(entry)
    return summary


def _apply_cross_batch_merges(batch_results: list[dict],
                               merges: list[dict]) -> list[dict]:
    """将跨批次别名合并应用到 batch_results 上。"""
    if not merges:
        return batch_results

    # 建立 name -> (batch_idx, char_idx) 索引
    name_to_loc = {}
    for bi, result in enumerate(batch_results):
        for ci, char in enumerate(result.get("characters", [])):
            name_to_loc[char["canonical_name"]] = (bi, ci)

    removed = set()
    applied = 0

    for m in merges:
        keep = m.get("keep", "")
        drop = m.get("merge", "")

        if keep not in name_to_loc or drop not in name_to_loc:
            continue
        keep_bi, keep_ci = name_to_loc[keep]
        drop_bi, drop_ci = name_to_loc[drop]

        if (keep_bi, keep_ci) == (drop_bi, drop_ci):
            continue
        if (drop_bi, drop_ci) in removed:
            continue

        keep_char = batch_results[keep_bi]["characters"][keep_ci]
        drop_char = batch_results[drop_bi]["characters"][drop_ci]

        # 合并别名
        existing = set(keep_char.get("aliases", []))
        existing.add(drop)
        for alias in drop_char.get("aliases", []):
            if alias != keep:
                existing.add(alias)
        keep_char["aliases"] = sorted(existing)

        # 合并出场次数
        keep_char["appearance_count"] = (
            keep_char.get("appearance_count", 0) +
            drop_char.get("appearance_count", 0)
        )

        # 保留更高分级
        cls_rank = {"core": 3, "important": 2, "minor": 1}
        if cls_rank.get(drop_char.get("classification"), 0) > \
           cls_rank.get(keep_char.get("classification"), 0):
            keep_char["classification"] = drop_char["classification"]

        # 更新 alias_mapping
        batch_results[keep_bi].setdefault("alias_mapping", {})[drop] = keep
        for alias in drop_char.get("aliases", []):
            batch_results[keep_bi]["alias_mapping"][alias] = keep

        removed.add((drop_bi, drop_ci))
        applied += 1

    # 删除被合并的条目
    for bi in range(len(batch_results)):
        batch_results[bi]["characters"] = [
            c for ci, c in enumerate(batch_results[bi].get("characters", []))
            if (bi, ci) not in removed
        ]

    print(f"  应用了 {applied} 对跨批次合并，移除 {len(removed)} 个重复条目")
    return batch_results


def _merge_batch_results(batch_results: list[dict], raw_census: dict) -> dict:
    """合并多批 Claude 结果 + 低频角色 → 最终 census。

    去重策略：同名角色保留分级最高的条目，合并别名。
    """
    cls_rank = {"core": 3, "important": 2, "minor": 1}
    all_alias_mapping = {}
    # name -> best char entry（去重）
    best_by_name: dict[str, dict] = {}

    for result in batch_results:
        for char in result.get("characters", []):
            # 强制用 Python 生成 file_name，不信任 Claude 的拼音
            char["file_name"] = name_to_pinyin(char["canonical_name"])
            name = char["canonical_name"]
            if name in best_by_name:
                existing = best_by_name[name]
                # 保留分级更高的
                if cls_rank.get(char.get("classification"), 0) > \
                   cls_rank.get(existing.get("classification"), 0):
                    # 合并别名后替换
                    merged_aliases = sorted(
                        set(existing.get("aliases", [])) | set(char.get("aliases", []))
                    )
                    char["aliases"] = merged_aliases
                    best_by_name[name] = char
                else:
                    # 只合并别名
                    merged_aliases = sorted(
                        set(existing.get("aliases", [])) | set(char.get("aliases", []))
                    )
                    existing["aliases"] = merged_aliases
            else:
                best_by_name[name] = char
        all_alias_mapping.update(result.get("alias_mapping", {}))

    # 追加低频角色为 minor
    for name, info in raw_census.get("characters", {}).items():
        if info.get("appearance_count", 0) < MIN_APPEARANCE_FOR_ALIAS and name not in best_by_name:
            best_by_name[name] = {
                "canonical_name": name,
                "aliases": [],
                "classification": "minor",
                "identity": info.get("identity", ""),
                "first_appearance": info.get("first_appearance", "unknown"),
                "appearance_count": info.get("appearance_count", 0),
                "file_name": name_to_pinyin(name),
            }

    # 按分级汇总
    all_core, all_important, all_minor = [], [], []
    for name, char in best_by_name.items():
        cls = char.get("classification", "minor")
        if cls == "core":
            all_core.append(name)
        elif cls == "important":
            all_important.append(name)
        else:
            all_minor.append(name)

    return {
        "characters": list(best_by_name.values()),
        "alias_mapping": all_alias_mapping,
        "classification_summary": {
            "core": sorted(all_core),
            "important": sorted(all_important),
            "minor": sorted(all_minor),
        },
    }


def phase_alias_merge(book_dir: Path, progress: dict, progress_path: Path,
                      model: str, timeout: int, dry_run: bool):
    """执行阶段 1：别名合并与分级（分批处理）"""
    print("\n" + "=" * 60)
    print("阶段 1：别名合并与分级")
    print("=" * 60)

    if progress["alias_merge"]["status"] == "completed":
        print("别名合并已完成！")
        return

    build_dir = get_build_dir(book_dir)
    raw_census_path = build_dir / "raw_census.json"
    if not raw_census_path.exists():
        print("错误：raw_census.json 不存在，请先运行阶段 0")
        return

    with open(raw_census_path, "r", encoding="utf-8") as f:
        raw_census = json.load(f)

    # 加载 plot_lines.md
    plot_lines_path = book_dir / "plot" / "outline" / "plot_lines.md"
    plot_lines_content = ""
    if plot_lines_path.exists():
        plot_lines_content = plot_lines_path.read_text(encoding="utf-8")

    # 分批
    char_batches = _split_characters_into_batches(raw_census)
    total_eligible = sum(len(b) for b in char_batches)
    total_chars = len(raw_census.get("characters", {}))
    auto_minor = total_chars - total_eligible

    print(f"总角色: {total_chars}，需 Claude 分析: {total_eligible}（出场>={MIN_APPEARANCE_FOR_ALIAS}）")
    print(f"自动归为 minor: {auto_minor}（出场<{MIN_APPEARANCE_FOR_ALIAS}）")
    print(f"分 {len(char_batches)} 批处理（每批 {ALIAS_BATCH_SIZE} 个）")

    if dry_run:
        for i, batch in enumerate(char_batches):
            print(f"  批 {i+1}: {len(batch)} 个角色")
        return

    # 恢复已完成的批次
    completed_batches = progress["alias_merge"].get("batches_completed", [])
    batch_results = []

    # 加载已完成批次的结果
    for label in completed_batches:
        result_path = build_dir / f"alias_{label}.json"
        if result_path.exists():
            with open(result_path, "r", encoding="utf-8") as f:
                batch_results.append(json.load(f))

    for i, batch_chars in enumerate(char_batches):
        batch_label = f"batch_{i+1:02d}"
        if batch_label in completed_batches:
            continue

        compact = _compact_census_for_alias({"characters": batch_chars, "stats": {}})
        prompt = build_alias_merge_prompt(compact, plot_lines_content, batch_label)
        print(f"\n--- {batch_label} ({len(batch_chars)} 角色, "
              f"prompt {len(prompt.encode('utf-8')) // 1024} KB) ---")

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout)
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if not success:
            print(f"  失败 ({elapsed:.0f}s): {output[:200]}")
            save_progress(progress_path, progress)
            continue

        debug_path = build_dir / f"alias_{batch_label}_json_debug.txt"
        result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
        if result is None:
            print(f"  无法解析 JSON ({elapsed:.0f}s)")
            print(f"  调试信息已保存: {debug_path}")
            save_progress(progress_path, progress)
            continue

        # 保存单批结果
        result_path = build_dir / f"alias_{batch_label}.json"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        batch_results.append(result)
        n_chars = len(result.get("characters", []))
        n_aliases = len(result.get("alias_mapping", {}))
        print(f"  完成 ({elapsed:.0f}s): {n_chars} 角色, {n_aliases} 别名")

        progress["alias_merge"].setdefault("batches_completed", []).append(batch_label)
        save_progress(progress_path, progress)

    # 检查是否全部完成
    expected_labels = [f"batch_{i+1:02d}" for i in range(len(char_batches))]
    if set(expected_labels) <= set(progress["alias_merge"].get("batches_completed", [])):
        # === 跨批次别名检测 ===
        cross_path = build_dir / "cross_batch_aliases.json"
        cross_merges = []

        if len(char_batches) > 1 and not progress["alias_merge"].get("cross_batch_done"):
            print("\n--- 跨批次别名检测 ---")
            summary = _build_cross_batch_summary(batch_results)
            template = (PROMPTS_DIR / "char_cross_batch_alias.md").read_text(encoding="utf-8")
            prompt = template.replace("{characters_summary_json}",
                                      json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"  {len(summary)} 个角色, prompt {len(prompt.encode('utf-8')) // 1024} KB")

            start_time = time.time()
            success, output = run_claude_prompt(prompt, model, timeout)
            elapsed = time.time() - start_time

            progress["stats"]["total_calls"] += 1
            progress["stats"]["total_time_seconds"] += int(elapsed)

            if success:
                debug_path = build_dir / "cross_batch_json_debug.txt"
                result = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
                if result:
                    cross_merges = result.get("cross_batch_aliases", [])
                    with open(cross_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    if cross_merges:
                        print(f"  发现 {len(cross_merges)} 对跨批次别名 ({elapsed:.0f}s)")
                    else:
                        print(f"  未发现跨批次重复 ({elapsed:.0f}s)")
                else:
                    print(f"  无法解析结果 ({elapsed:.0f}s)")
                    print(f"  调试信息已保存: {debug_path}")
            else:
                print(f"  失败 ({elapsed:.0f}s): {output[:200]}")

            progress["alias_merge"]["cross_batch_done"] = True
            save_progress(progress_path, progress)
        elif cross_path.exists():
            # 恢复已有的跨批次结果
            with open(cross_path, "r", encoding="utf-8") as f:
                cross_data = json.load(f)
            cross_merges = cross_data.get("cross_batch_aliases", [])

        # 应用跨批次合并
        if cross_merges:
            batch_results = _apply_cross_batch_merges(batch_results, cross_merges)

        # === 最终合并 ===
        census = _merge_batch_results(batch_results, raw_census)

        # 保存 census.json
        census_path = build_dir / "census.json"
        with open(census_path, "w", encoding="utf-8") as f:
            json.dump(census, f, ensure_ascii=False, indent=2)

        # 保存 alias_mapping.json
        alias_mapping = census.get("alias_mapping", {})
        alias_path = build_dir / "alias_mapping.json"
        with open(alias_path, "w", encoding="utf-8") as f:
            json.dump(alias_mapping, f, ensure_ascii=False, indent=2)

        classification = census["classification_summary"]
        core_count = len(classification["core"])
        important_count = len(classification["important"])
        minor_count = len(classification["minor"])

        print(f"\n合并完成！")
        print(f"  核心角色: {core_count}")
        print(f"  重要角色: {important_count}")
        print(f"  次要角色: {minor_count}")
        print(f"  别名映射: {len(alias_mapping)} 条")
        print(f"\n已保存: {census_path}")

        progress["alias_merge"]["status"] = "completed"
        progress["alias_merge"]["total_characters_merged"] = len(census["characters"])
        progress["alias_merge"]["core_count"] = core_count
        progress["alias_merge"]["important_count"] = important_count
        progress["alias_merge"]["minor_count"] = minor_count
        progress["phase"] = "deep_dive"
        save_progress(progress_path, progress)
    else:
        done = len(progress["alias_merge"].get("batches_completed", []))
        print(f"\n部分完成: {done}/{len(char_batches)} 批，请重新运行继续")


# ============================================================
# 阶段 2：角色深度分析
# ============================================================

def load_census(build_dir: Path) -> dict:
    census_path = build_dir / "census.json"
    if not census_path.exists():
        print("错误：census.json 不存在，请先运行阶段 1")
        sys.exit(1)
    with open(census_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_raw_census(build_dir: Path) -> dict:
    path = build_dir / "raw_census.json"
    if not path.exists():
        print("错误：raw_census.json 不存在，请先运行阶段 0")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_character_chapters(char_name: str, raw_census: dict,
                           alias_mapping: dict) -> list[int]:
    """获取角色（含别名）的全部出场章节编号"""
    chars = raw_census.get("characters", {})
    chapters = set()

    # 正名
    names_to_check = [char_name]
    # 别名
    for alias, canonical in alias_mapping.items():
        if canonical == char_name:
            names_to_check.append(alias)

    for name in names_to_check:
        if name in chars:
            for ch_str in chars[name].get("appearance_chapters", []):
                m = re.match(r"ch(\d+)", ch_str)
                if m:
                    chapters.add(int(m.group(1)))

    return sorted(chapters)


def get_character_arc_context(char_name: str, outline_dir: Path) -> str:
    """从弧文件中提取该角色相关段落"""
    parts = []
    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")
        # 检查角色名是否在文件中
        if char_name not in content:
            continue
        # 提取弧标题和角色相关内容
        title_match = re.match(r"# (.+)", content)
        arc_title = title_match.group(1) if title_match else arc_file.stem

        # 提取主要角色中该角色的描述
        chars_section = parse_section(content, "主要角色")
        for line in chars_section.splitlines():
            if char_name in line:
                parts.append(f"**{arc_title}**: {line.strip()}")

        # 提取关键转折点中涉及该角色的
        turns_section = parse_section(content, "关键转折点")
        for line in turns_section.splitlines():
            if char_name in line:
                parts.append(f"  转折: {line.strip()}")

    return "\n".join(parts) if parts else "（无弧内信息）"


def get_related_characters(char_name: str, raw_census: dict) -> str:
    """获取与指定角色有关系记录的其他角色列表"""
    chars = raw_census.get("characters", {})
    related = set()

    # 从 relationship_timeline 中查找
    for rel in raw_census.get("relationship_timeline", []):
        if rel["char_a"] == char_name:
            related.add(rel["char_b"])
        elif rel["char_b"] == char_name:
            related.add(rel["char_a"])

    # 从 raw_relationships 中查找
    if char_name in chars:
        for rel in chars[char_name].get("raw_relationships", []):
            related.add(rel["target"])

    parts = []
    for name in sorted(related):
        if name in chars:
            parts.append(f"- {name}: {chars[name].get('identity', '未知')}")
        else:
            parts.append(f"- {name}: 未知身份")

    return "\n".join(parts) if parts else "（无相关角色）"


DEEP_DIVE_BATCH_SIZE = 100  # 每批加载的章节数


def _extract_character_notes(char_name: str, ch_batch: list[int],
                              chapters_dir: Path, batch_label: str,
                              model: str, timeout: int,
                              verbose: bool = False) -> tuple[bool, str, float]:
    """从一批章节摘要中提取指定角色的关键笔记。

    返回 (success, notes_text, elapsed_seconds)。
    """
    summaries = load_summaries_for_chapters(chapters_dir, ch_batch)
    prompt = f"""从以下章节摘要中提取关于「{char_name}」的关键信息。只关注该角色相关内容，忽略无关内容。

## 章节摘要（ch{ch_batch[0]:04d} ~ ch{ch_batch[-1]:04d}）

{summaries}

## 提取要求

简洁列出以下信息，每项一行，格式 `- chXXXX: 描述`。无内容的分类可省略。

### 关键事件
该角色的重要行动、决策、遭遇。

### 关系变化
与其他角色之间关系的建立、变化、破裂。

### 状态变化
实力、修为、地位、处境等方面的变化。

### 性格展现
体现性格特征的具体行为、对话、选择。

不要输出 JSON，直接输出 Markdown 文本。"""

    start = time.time()
    success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
    elapsed = time.time() - start
    if success:
        notes = f"## {batch_label}（ch{ch_batch[0]:04d} ~ ch{ch_batch[-1]:04d}）\n\n{output.strip()}"
    else:
        notes = f"## {batch_label}（失败: {output[:100]}）"
    return success, notes, elapsed


def _batched_extract_for_character(char_name: str, ch_list: list[int],
                                    chapters_dir: Path,
                                    model: str, timeout: int,
                                    verbose: bool = False,
                                    print_lock=None) -> tuple[str, int, float]:
    """对高章节数角色分批提取笔记。

    返回 (merged_notes, call_count, total_elapsed)。
    """
    batches = []
    for i in range(0, len(ch_list), DEEP_DIVE_BATCH_SIZE):
        batches.append(ch_list[i:i + DEEP_DIVE_BATCH_SIZE])

    all_notes = []
    total_calls = 0
    total_elapsed = 0.0

    for idx, batch_chs in enumerate(batches):
        label = f"提取批次 {idx + 1}/{len(batches)}"
        if print_lock:
            with print_lock:
                print(f"    {label} ({len(batch_chs)} 章)...")
        success, notes, elapsed = _extract_character_notes(
            char_name, batch_chs, chapters_dir, label, model, timeout, verbose)
        total_calls += 1
        total_elapsed += elapsed
        all_notes.append(notes)
        if print_lock:
            with print_lock:
                status = "✓" if success else "✗"
                print(f"    {label} {status} ({elapsed:.0f}s)")

    return "\n\n---\n\n".join(all_notes), total_calls, total_elapsed


def build_core_character_prompt(char_data: dict, summaries: str,
                                arc_context: str, plotline_context: str,
                                related_chars: str, output_path: str) -> str:
    template = (PROMPTS_DIR / "char_deep_core.md").read_text(encoding="utf-8")
    prompt = template.replace("{character_name}", char_data["canonical_name"])
    prompt = prompt.replace("{character_data_json}",
                            json.dumps(char_data, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{chapter_summaries}", summaries)
    prompt = prompt.replace("{arc_context}", arc_context)
    prompt = prompt.replace("{plotline_context}", plotline_context)
    prompt = prompt.replace("{related_characters}", related_chars)
    prompt = prompt.replace("{output_path}", output_path)
    return prompt


def build_important_character_prompt(char_data: dict, summaries: str,
                                     output_path: str) -> str:
    template = (PROMPTS_DIR / "char_deep_important.md").read_text(encoding="utf-8")
    prompt = template.replace("{character_name}", char_data["canonical_name"])
    prompt = prompt.replace("{character_data_json}",
                            json.dumps(char_data, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{chapter_summaries}", summaries)
    prompt = prompt.replace("{output_path}", output_path)
    return prompt


def phase_deep_dive(book_dir: Path, progress: dict, progress_path: Path,
                    model: str, timeout: int, dry_run: bool,
                    target_character: str | None = None,
                    concurrency: int = 1):
    """执行阶段 2：角色深度分析"""
    print("\n" + "=" * 60)
    print("阶段 2：角色深度分析")
    print("=" * 60)

    build_dir = get_build_dir(book_dir)
    characters_dir = get_characters_dir(book_dir)
    chapters_dir = get_chapters_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)

    census = load_census(build_dir)
    raw_census = load_raw_census(build_dir)
    alias_mapping = census.get("alias_mapping", {})
    classification = census.get("classification_summary", {})

    # 加载 plot_lines.md
    plot_lines_path = book_dir / "plot" / "outline" / "plot_lines.md"
    plotline_context = ""
    if plot_lines_path.exists():
        plotline_context = plot_lines_path.read_text(encoding="utf-8")

    # 确定要处理的角色
    core_chars = classification.get("core", [])
    important_chars = classification.get("important", [])

    if target_character:
        # 只处理指定角色
        all_chars_in_census = {c["canonical_name"]: c for c in census.get("characters", [])}
        if target_character in all_chars_in_census:
            char_data = all_chars_in_census[target_character]
            cls = char_data.get("classification", "important")
            if cls == "core":
                core_chars = [target_character]
                important_chars = []
            else:
                core_chars = []
                important_chars = [target_character]
        else:
            print(f"错误：角色 '{target_character}' 不在 census.json 中")
            return

    completed_core = set(progress["deep_dive"]["core_completed"])
    completed_important = set(progress["deep_dive"]["important_completed"])

    pending_core = [n for n in core_chars if n not in completed_core]
    pending_important = [n for n in important_chars if n not in completed_important]

    print(f"核心角色: {len(core_chars)} ({len(pending_core)} 待处理)")
    print(f"重要角色: {len(important_chars)} ({len(pending_important)} 待处理)")

    if dry_run:
        for name in pending_core:
            chs = get_character_chapters(name, raw_census, alias_mapping)
            print(f"  [核心] {name}: {len(chs)} 章")
        for name in pending_important:
            chs = get_character_chapters(name, raw_census, alias_mapping)
            print(f"  [重要] {name}: {len(chs)} 章")
        return

    # 查找角色数据的辅助函数
    chars_by_name = {c["canonical_name"]: c for c in census.get("characters", [])}

    concurrency = max(1, concurrency)
    print_lock = threading.Lock()
    done_counter = {"n": 0, "total": len(pending_core) + len(pending_important)}

    def process_one_character(name: str, cls: str) -> dict:
        """处理单个角色，cls 为 'core' 或 'important'"""
        char_data = chars_by_name.get(name, {"canonical_name": name})
        ch_list = get_character_chapters(name, raw_census, alias_mapping)
        output_path = str(characters_dir / f"{char_data.get('file_name', name_to_pinyin(name))}.md")
        label = f"[{'核心' if cls == 'core' else '重要'}] {name}"

        needs_batch = len(ch_list) > DEEP_DIVE_BATCH_SIZE
        with print_lock:
            extra = f", 分 {(len(ch_list) - 1) // DEEP_DIVE_BATCH_SIZE + 1} 批提取" if needs_batch else ""
            print(f"\n--- {label} ({len(ch_list)} 章{extra}) ---")

        extra_calls = 0
        extra_time = 0.0

        if needs_batch:
            # 分批提取笔记，再合成
            summaries, extra_calls, extra_time = _batched_extract_for_character(
                name, ch_list, chapters_dir, model, timeout,
                verbose=False, print_lock=print_lock)
        else:
            summaries = load_summaries_for_chapters(chapters_dir, ch_list)

        if cls == "core":
            arc_context = get_character_arc_context(name, outline_dir)
            related_chars = get_related_characters(name, raw_census)
            prompt = build_core_character_prompt(
                char_data, summaries, arc_context, plotline_context,
                related_chars, output_path,
            )
        else:
            prompt = build_important_character_prompt(char_data, summaries, output_path)

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout,
                                           allow_tools="Write", verbose=False)
        elapsed = time.time() - start_time + extra_time
        extra_calls += 1  # 合成调用本身

        # 验证文件是否真的被写入
        if success and not Path(output_path).exists():
            success = False
            output = f"Claude 返回成功但文件未创建: {output_path}"

        # 带锁写入进度
        completed_key = f"{cls}_completed"
        failed_key = f"{cls}_failed"
        lock_path = progress_path.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = _read_progress_raw(progress_path)
                disk["stats"]["total_calls"] = disk["stats"].get("total_calls", 0) + extra_calls
                disk["stats"]["total_time_seconds"] = disk["stats"].get("total_time_seconds", 0) + int(elapsed)
                if success:
                    if name not in disk["deep_dive"][completed_key]:
                        disk["deep_dive"][completed_key].append(name)
                    if name in disk["deep_dive"][failed_key]:
                        disk["deep_dive"][failed_key].remove(name)
                else:
                    if name not in disk["deep_dive"][failed_key]:
                        disk["deep_dive"][failed_key].append(name)
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
                progress.update(disk)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        with print_lock:
            done_counter["n"] += 1
            status = "✓" if success else "✗"
            msg = f"  [{label}] {status} ({elapsed:.0f}s)  [{done_counter['n']}/{done_counter['total']}]"
            if not success:
                msg += f" {output[:100]}"
            print(msg)

        return {"name": name, "cls": cls, "success": success}

    # 构建任务列表：核心角色优先
    tasks = [(n, "core") for n in pending_core] + [(n, "important") for n in pending_important]

    if concurrency == 1:
        for name, cls in tasks:
            process_one_character(name, cls)
    else:
        print(f"并发数: {concurrency}")
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process_one_character, n, c): n for n, c in tasks}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    with print_lock:
                        print(f"  ✗ 线程异常 ({futures[future]}): {e}")

    # 检查是否全部完成
    final = load_progress(progress_path)
    all_core_done = set(final["deep_dive"]["core_completed"]) >= set(core_chars)
    all_imp_done = set(final["deep_dive"]["important_completed"]) >= set(important_chars)
    if all_core_done and all_imp_done:
        final["phase"] = "relationship"
        save_progress(progress_path, final)
        progress.update(final)
        print("\n角色深度分析全部完成！")


# ============================================================
# 阶段 3：关系网构建
# ============================================================

def extract_relationship_sections(characters_dir: Path) -> str:
    """从角色档案中提取关系段落"""
    parts = []
    for f in sorted(characters_dir.glob("*.md")):
        if f.name in ("index.md", "relationships.md"):
            continue
        content = f.read_text(encoding="utf-8")
        # 提取标题
        title_match = re.match(r"# (.+)", content)
        char_name = title_match.group(1) if title_match else f.stem

        # 提取关系相关段落
        rel_section = parse_section(content, "3. 关系演化（动态）")
        if not rel_section:
            rel_section = parse_section(content, "3. 当前状态")
        if rel_section:
            parts.append(f"### {char_name}\n\n{rel_section}")
    return "\n\n---\n\n".join(parts) if parts else "（无关系段落）"


def build_relationship_prompt(raw_census: dict, character_profiles: str,
                              arc_summary: str, output_path: str) -> str:
    template = (PROMPTS_DIR / "char_relationship_build.md").read_text(encoding="utf-8")
    prompt = template.replace("{relationship_timeline_json}",
                              json.dumps(raw_census.get("relationship_timeline", []),
                                        ensure_ascii=False, indent=2))
    prompt = prompt.replace("{character_profiles_summary}", character_profiles)
    prompt = prompt.replace("{arc_summary}", arc_summary)
    prompt = prompt.replace("{output_path}", output_path)
    return prompt


def build_relationship_validate_prompt(current_relationships: str,
                                       character_profiles: str) -> str:
    template = (PROMPTS_DIR / "char_relationship_validate.md").read_text(encoding="utf-8")
    prompt = template.replace("{current_relationships}", current_relationships)
    prompt = prompt.replace("{character_profiles_relationships}", character_profiles)
    return prompt


def phase_relationship(book_dir: Path, progress: dict, progress_path: Path,
                       model: str, timeout: int, dry_run: bool):
    """执行阶段 3：关系网构建"""
    print("\n" + "=" * 60)
    print("阶段 3：关系网构建")
    print("=" * 60)

    if progress["relationship"]["built"] and progress["relationship"]["validated"]:
        print("关系网已完成！")
        return

    build_dir = get_build_dir(book_dir)
    characters_dir = get_characters_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)
    raw_census = load_raw_census(build_dir)
    output_path = str(characters_dir / "relationships.md")

    # 加载弧概览
    arc_summary_parts = []
    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")
        title_match = re.match(r"# (.+)", content)
        arc_title = title_match.group(1) if title_match else arc_file.stem
        range_match = re.search(
            r"\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)", content
        )
        range_str = f"ch{range_match.group(1)}-ch{range_match.group(2)}" if range_match else ""
        arc_summary_parts.append(f"- {arc_title} ({range_str})")
    arc_summary = "\n".join(arc_summary_parts) if arc_summary_parts else "（无弧信息）"

    if dry_run:
        print(f"将从 {len(raw_census.get('relationship_timeline', []))} 条关系变化构建关系网")
        print(f"产出: {output_path}")
        return

    # 步骤 1：构建关系网
    if not progress["relationship"]["built"]:
        character_profiles = extract_relationship_sections(characters_dir)
        prompt = build_relationship_prompt(raw_census, character_profiles,
                                           arc_summary, output_path)

        print("构建关系网...")
        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write")
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            print(f"完成 ({elapsed:.0f}s)")
            progress["relationship"]["built"] = True
        else:
            print(f"失败 ({elapsed:.0f}s): {output[:200]}")

        save_progress(progress_path, progress)

    # 步骤 2：交叉验证
    if progress["relationship"]["built"] and not progress["relationship"]["validated"]:
        rel_path = characters_dir / "relationships.md"
        if rel_path.exists():
            current_relationships = rel_path.read_text(encoding="utf-8")
            character_profiles = extract_relationship_sections(characters_dir)

            prompt = build_relationship_validate_prompt(
                current_relationships, character_profiles
            )

            print("交叉验证关系...")
            start_time = time.time()
            success, output = run_claude_prompt(prompt, model, timeout)
            elapsed = time.time() - start_time

            progress["stats"]["total_calls"] += 1
            progress["stats"]["total_time_seconds"] += int(elapsed)

            if success:
                debug_path = build_dir / "relationship_validation_json_debug.txt"
                corrections = fix_and_parse_json(output, verbose=False, save_debug_to=debug_path)
                if corrections:
                    n_issues = len(corrections.get("issues", []))
                    print(f"完成 ({elapsed:.0f}s): 发现 {n_issues} 个问题")
                    # 保存验证结果
                    val_path = build_dir / "relationship_validation.json"
                    with open(val_path, "w", encoding="utf-8") as f:
                        json.dump(corrections, f, ensure_ascii=False, indent=2)
                else:
                    print(f"完成 ({elapsed:.0f}s): 无问题")
            else:
                print(f"失败 ({elapsed:.0f}s): {output[:200]}")

            progress["relationship"]["validated"] = True
            save_progress(progress_path, progress)

    # 检查阶段完成
    if progress["relationship"]["built"] and progress["relationship"]["validated"]:
        progress["phase"] = "status_update"
        save_progress(progress_path, progress)


# ============================================================
# 阶段 4：状态精修与索引
# ============================================================

def build_status_update_prompt(active_chars: list[dict], recent_summaries: str,
                               char_file_paths: dict) -> str:
    template = (PROMPTS_DIR / "char_status_update.md").read_text(encoding="utf-8")
    prompt = template.replace("{active_characters_json}",
                              json.dumps(active_chars, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{recent_chapter_summaries}", recent_summaries)
    prompt = prompt.replace("{character_file_paths}",
                            json.dumps(char_file_paths, ensure_ascii=False, indent=2))
    return prompt


def build_index_md(census: dict, characters_dir: Path) -> str:
    """Python 直接生成 index.md"""
    chars = census.get("characters", [])
    classification = census.get("classification_summary", {})

    core = classification.get("core", [])
    important = classification.get("important", [])
    minor = classification.get("minor", [])

    chars_by_name = {c["canonical_name"]: c for c in chars}

    lines = [
        "# 人物索引\n",
        "## 统计",
        f"- 核心角色: {len(core)} 人",
        f"- 重要角色: {len(important)} 人",
        f"- 次要角色: {len(minor)} 人",
        f"- 总计: {len(chars)} 人\n",
    ]

    def make_table(names: list[str], with_link: bool = True):
        if with_link:
            table_lines = [
                "| 角色名 | 身份 | 出场次数 | 首次出场 | 档案 |",
                "|--------|------|---------|---------|------|",
            ]
        else:
            table_lines = [
                "| 角色名 | 身份 | 出场次数 | 首次出场 |",
                "|--------|------|---------|---------|",
            ]

        for name in names:
            c = chars_by_name.get(name, {})
            identity = c.get("identity", "")
            # 截断过长的身份描述
            if len(identity) > 30:
                identity = identity[:27] + "..."
            count = c.get("appearance_count", 0)
            first = c.get("first_appearance", "?")
            file_name = c.get("file_name", name_to_pinyin(name))
            if with_link:
                table_lines.append(
                    f"| {name} | {identity} | {count} | {first} | [{name}]({file_name}.md) |"
                )
            else:
                table_lines.append(
                    f"| {name} | {identity} | {count} | {first} |"
                )
        return "\n".join(table_lines)

    if core:
        lines.append("## 核心角色\n")
        lines.append(make_table(core, with_link=True))
        lines.append("")

    if important:
        lines.append("## 重要角色\n")
        lines.append(make_table(important, with_link=True))
        lines.append("")

    if minor:
        lines.append("## 次要角色\n")
        lines.append(make_table(minor, with_link=False))
        lines.append("")

    return "\n".join(lines)


def phase_status_update(book_dir: Path, all_chapters: list[int],
                        progress: dict, progress_path: Path,
                        model: str, timeout: int, dry_run: bool):
    """执行阶段 4：状态精修与索引"""
    print("\n" + "=" * 60)
    print("阶段 4：状态精修与索引")
    print("=" * 60)

    if progress["status_update"]["completed"]:
        print("状态精修已完成！")
        return

    build_dir = get_build_dir(book_dir)
    characters_dir = get_characters_dir(book_dir)
    chapters_dir = get_chapters_dir(book_dir)

    census = load_census(build_dir)
    raw_census = load_raw_census(build_dir)
    alias_mapping = census.get("alias_mapping", {})

    # 找出活跃角色（在最后 20 章有出场的核心/重要角色）
    recent_n = min(20, len(all_chapters))
    recent_chapters = all_chapters[-recent_n:]

    chars_by_name = {c["canonical_name"]: c for c in census.get("characters", [])}
    classification = census.get("classification_summary", {})
    active_names = set()

    for name in classification.get("core", []) + classification.get("important", []):
        ch_list = get_character_chapters(name, raw_census, alias_mapping)
        if any(ch in recent_chapters for ch in ch_list):
            active_names.add(name)

    active_chars = []
    char_file_paths = {}
    for name in sorted(active_names):
        c = chars_by_name.get(name, {})
        file_name = c.get("file_name", name_to_pinyin(name))
        file_path = str(characters_dir / f"{file_name}.md")
        active_chars.append({
            "name": name,
            "classification": c.get("classification", "important"),
            "file_path": file_path,
        })
        char_file_paths[name] = file_path

    if dry_run:
        print(f"最后 {recent_n} 章中的活跃角色: {len(active_chars)} 人")
        for ac in active_chars:
            print(f"  [{ac['classification']}] {ac['name']}")
        print("还将生成 index.md")
        return

    # 步骤 1：更新活跃角色的当前状态
    if active_chars:
        recent_summaries = load_summaries_for_chapters(chapters_dir, recent_chapters)

        prompt = build_status_update_prompt(active_chars, recent_summaries,
                                            char_file_paths)

        print(f"更新 {len(active_chars)} 个活跃角色的当前状态...")
        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout * 2,
                                            allow_tools="Write,Read")
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            print(f"完成 ({elapsed:.0f}s)")
        else:
            print(f"失败 ({elapsed:.0f}s): {output[:200]}")
    else:
        print("无活跃角色需要更新")

    # 步骤 2：生成 index.md（Python 直接生成）
    index_content = build_index_md(census, characters_dir)
    index_path = characters_dir / "index.md"
    index_path.write_text(index_content, encoding="utf-8")
    print(f"已生成: {index_path}")

    progress["status_update"]["completed"] = True
    save_progress(progress_path, progress)


# ============================================================
# 验证
# ============================================================

def run_validate(book_dir: Path):
    """验证所有产出文件"""
    print("\n验证 T4 产出文件...")

    characters_dir = book_dir / "characters"
    build_dir = book_dir / "characters" / ".build"

    results = []

    # 检查中间产物
    for name, desc in [
        ("raw_census.json", "Python 预处理"),
        ("census.json", "别名合并"),
        ("alias_mapping.json", "别名映射"),
    ]:
        path = build_dir / name
        if path.exists():
            size = path.stat().st_size
            results.append((desc, "OK", f"{size} bytes"))
        else:
            results.append((desc, "缺失", ""))

    # 检查 index.md
    index_path = characters_dir / "index.md"
    if index_path.exists():
        results.append(("人物索引", "OK", f"{index_path.stat().st_size} bytes"))
    else:
        results.append(("人物索引", "缺失", ""))

    # 检查 relationships.md
    rel_path = characters_dir / "relationships.md"
    if rel_path.exists():
        results.append(("关系网", "OK", f"{rel_path.stat().st_size} bytes"))
    else:
        results.append(("关系网", "缺失", ""))

    # 检查角色档案
    profile_files = [f for f in characters_dir.glob("*.md")
                     if f.name not in ("index.md", "relationships.md")]
    results.append(("角色档案", "OK" if profile_files else "缺失",
                     f"{len(profile_files)} 个文件"))

    # 检查核心角色档案的段落完整性
    if build_dir.exists() and (build_dir / "census.json").exists():
        with open(build_dir / "census.json", "r", encoding="utf-8") as f:
            census = json.load(f)
        classification = census.get("classification_summary", {})
        chars_by_name = {c["canonical_name"]: c for c in census.get("characters", [])}

        for name in classification.get("core", []):
            c = chars_by_name.get(name, {})
            file_name = c.get("file_name", name_to_pinyin(name))
            profile_path = characters_dir / f"{file_name}.md"
            if profile_path.exists():
                content = profile_path.read_text(encoding="utf-8")
                required_sections = [
                    "## 1. 基本设定",
                    "## 2. 角色弧光",
                    "## 3. 关系演化",
                    "## 4. 个人时间轴",
                    "## 5. 当前状态",
                ]
                missing = [s for s in required_sections if s not in content]
                if missing:
                    results.append((f"核心-{name}", "警告",
                                    f"缺少段落: {', '.join(missing)}"))
                else:
                    results.append((f"核心-{name}", "OK", "5 模块完整"))
            else:
                results.append((f"核心-{name}", "缺失", f"文件不存在: {file_name}.md"))

        for name in classification.get("important", []):
            c = chars_by_name.get(name, {})
            file_name = c.get("file_name", name_to_pinyin(name))
            profile_path = characters_dir / f"{file_name}.md"
            if profile_path.exists():
                content = profile_path.read_text(encoding="utf-8")
                required_sections = [
                    "## 1. 基本设定",
                    "## 2. 关键事件",
                    "## 3. 当前状态",
                ]
                missing = [s for s in required_sections if s not in content]
                if missing:
                    results.append((f"重要-{name}", "警告",
                                    f"缺少段落: {', '.join(missing)}"))
                else:
                    results.append((f"重要-{name}", "OK", "3 模块完整"))
            else:
                results.append((f"重要-{name}", "缺失", f"文件不存在: {file_name}.md"))

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
    get_characters_dir(book_dir)
    get_build_dir(book_dir)

    if args.dry_run:
        print("\n=== 试运行模式 ===")

    # 阶段 0: 预处理
    if args.phase == "preprocess" or (not args.phase and progress["phase"] == "preprocess"):
        phase_preprocess(book_dir, all_chapters, progress, progress_path, args.dry_run)

    # 阶段 1: 别名合并
    if args.phase == "alias-merge" or (not args.phase and progress["phase"] in ("alias_merge", "preprocess")):
        progress = load_progress(progress_path)
        if progress["phase"] == "alias_merge" or args.phase == "alias-merge":
            phase_alias_merge(book_dir, progress, progress_path,
                              args.model, args.timeout, args.dry_run)

    # 阶段 2: 角色深度分析
    if args.phase == "deep-dive" or (not args.phase and progress["phase"] in ("deep_dive", "alias_merge")):
        progress = load_progress(progress_path)
        if progress["phase"] == "deep_dive" or args.phase == "deep-dive":
            phase_deep_dive(book_dir, progress, progress_path,
                            args.model, args.timeout, args.dry_run,
                            args.character, args.concurrency)

    # 阶段 3: 关系网构建
    if args.phase == "relationship" or (not args.phase and progress["phase"] in ("relationship", "deep_dive")):
        progress = load_progress(progress_path)
        if progress["phase"] == "relationship" or args.phase == "relationship":
            phase_relationship(book_dir, progress, progress_path,
                               args.model, args.timeout, args.dry_run)

    # 阶段 4: 状态精修
    if args.phase == "status-update" or (not args.phase and progress["phase"] in ("status_update", "relationship")):
        progress = load_progress(progress_path)
        if progress["phase"] == "status_update" or args.phase == "status-update":
            phase_status_update(book_dir, all_chapters, progress, progress_path,
                                args.model, args.timeout, args.dry_run)

    # 最终统计
    progress = load_progress(progress_path)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    print(f"  预处理: {progress['preprocess']['status']}")
    print(f"  别名合并: {progress['alias_merge']['status']}")
    dd = progress["deep_dive"]
    print(f"  角色深度: 核心 {len(dd['core_completed'])} 完成/{len(dd['core_failed'])} 失败, "
          f"重要 {len(dd['important_completed'])} 完成/{len(dd['important_failed'])} 失败")
    rel = progress["relationship"]
    print(f"  关系网: 构建{'✓' if rel['built'] else '✗'} 验证{'✓' if rel['validated'] else '✗'}")
    print(f"  状态精修: {'✓' if progress['status_update']['completed'] else '✗'}")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    if progress["status_update"]["completed"]:
        print("\nT4 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
