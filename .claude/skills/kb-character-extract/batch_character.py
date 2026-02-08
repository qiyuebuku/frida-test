#!/usr/bin/env python3
"""
角色层提取批量编排脚本

从章节摘要 + 剧情层产出中提取角色信息，构建完整人物库。
五阶段 pipeline：Python 预处理 → 别名合并 → 角色深度分析 → 关系网构建 → 状态精修。

用法：
    python batch_character.py --book-dir qidian/novel_kb/玄鉴仙族
    python batch_character.py --book-dir ... --phase preprocess
    python batch_character.py --book-dir ... --phase alias-merge
    python batch_character.py --book-dir ... --phase deep-dive
    python batch_character.py --book-dir ... --phase deep-dive --character "李木田"
    python batch_character.py --book-dir ... --phase relationship
    python batch_character.py --book-dir ... --phase status-update
    python batch_character.py --book-dir ... --dry-run
    python batch_character.py --book-dir ... --validate
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

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

def load_progress(progress_path: Path) -> dict:
    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
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


def save_progress(progress_path: Path, progress: dict):
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


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
                      allow_tools: str = "") -> tuple[bool, str]:
    cmd = [
        "claude",
        "-p", prompt,
        "--model", model,
        "--permission-mode", "bypassPermissions",
    ]
    if allow_tools:
        cmd.extend(["--allowedTools", allow_tools])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, f"退出码 {result.returncode}\nstderr: {result.stderr[:1000]}"
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, f"超时（{timeout}秒）"
    except Exception as e:
        return False, f"异常: {e}"


def extract_json_from_output(output: str) -> dict | None:
    text = output.strip()
    if "```json" in text:
        m = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    elif "```" in text:
        m = re.search(r"```\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_str = text[first_brace:last_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    return None


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

def build_alias_merge_prompt(raw_census: dict, plot_lines_content: str) -> str:
    """构建别名合并 prompt"""
    template = (PROMPTS_DIR / "alias_merge.md").read_text(encoding="utf-8")

    prompt = template.replace("{raw_census_json}",
                              json.dumps(raw_census, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{plot_lines_content}", plot_lines_content)
    return prompt


def phase_alias_merge(book_dir: Path, progress: dict, progress_path: Path,
                      model: str, timeout: int, dry_run: bool):
    """执行阶段 1：别名合并与分级"""
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

    if dry_run:
        print(f"将分析 {raw_census['stats']['total_characters']} 个角色")
        print("产出: census.json + alias_mapping.json")
        return

    prompt = build_alias_merge_prompt(raw_census, plot_lines_content)

    print("调用 Claude 进行别名合并与分级...")
    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout)
    elapsed = time.time() - start_time

    progress["stats"]["total_calls"] += 1
    progress["stats"]["total_time_seconds"] += int(elapsed)

    if not success:
        print(f"失败 ({elapsed:.0f}s): {output}")
        save_progress(progress_path, progress)
        return

    census = extract_json_from_output(output)
    if census is None:
        print("无法解析 JSON 输出")
        debug_path = build_dir / "alias_merge_raw.txt"
        debug_path.write_text(output, encoding="utf-8")
        print(f"原始输出已保存: {debug_path}")
        save_progress(progress_path, progress)
        return

    # 如果 Claude 没有生成 file_name，用 pypinyin 补充
    for char in census.get("characters", []):
        if not char.get("file_name"):
            char["file_name"] = name_to_pinyin(char["canonical_name"])

    # 保存 census.json
    census_path = build_dir / "census.json"
    with open(census_path, "w", encoding="utf-8") as f:
        json.dump(census, f, ensure_ascii=False, indent=2)

    # 保存 alias_mapping.json
    alias_mapping = census.get("alias_mapping", {})
    alias_path = build_dir / "alias_mapping.json"
    with open(alias_path, "w", encoding="utf-8") as f:
        json.dump(alias_mapping, f, ensure_ascii=False, indent=2)

    # 统计
    classification = census.get("classification_summary", {})
    core_count = len(classification.get("core", []))
    important_count = len(classification.get("important", []))
    minor_count = len(classification.get("minor", []))

    print(f"完成 ({elapsed:.0f}s)")
    print(f"  核心角色: {core_count}")
    print(f"  重要角色: {important_count}")
    print(f"  次要角色: {minor_count}")
    print(f"  别名映射: {len(alias_mapping)} 条")
    print(f"\n已保存: {census_path}")
    print(f"别名映射: {alias_path}（可手动审查修正）")

    progress["alias_merge"]["status"] = "completed"
    progress["alias_merge"]["total_characters_merged"] = len(census.get("characters", []))
    progress["alias_merge"]["core_count"] = core_count
    progress["alias_merge"]["important_count"] = important_count
    progress["alias_merge"]["minor_count"] = minor_count
    progress["phase"] = "deep_dive"
    save_progress(progress_path, progress)


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


def build_core_character_prompt(char_data: dict, summaries: str,
                                arc_context: str, plotline_context: str,
                                related_chars: str, output_path: str) -> str:
    template = (PROMPTS_DIR / "character_deep_core.md").read_text(encoding="utf-8")
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
    template = (PROMPTS_DIR / "character_deep_important.md").read_text(encoding="utf-8")
    prompt = template.replace("{character_name}", char_data["canonical_name"])
    prompt = prompt.replace("{character_data_json}",
                            json.dumps(char_data, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{chapter_summaries}", summaries)
    prompt = prompt.replace("{output_path}", output_path)
    return prompt


def phase_deep_dive(book_dir: Path, progress: dict, progress_path: Path,
                    model: str, timeout: int, dry_run: bool,
                    target_character: str | None = None):
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

    # 处理核心角色
    for name in pending_core:
        char_data = chars_by_name.get(name, {"canonical_name": name})
        ch_list = get_character_chapters(name, raw_census, alias_mapping)
        output_path = str(characters_dir / f"{char_data.get('file_name', name_to_pinyin(name))}.md")

        print(f"\n--- [核心] {name} ({len(ch_list)} 章) ---")

        summaries = load_summaries_for_chapters(chapters_dir, ch_list)
        arc_context = get_character_arc_context(name, outline_dir)
        related_chars = get_related_characters(name, raw_census)

        prompt = build_core_character_prompt(
            char_data, summaries, arc_context, plotline_context,
            related_chars, output_path,
        )

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write")
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            print(f"  完成 ({elapsed:.0f}s)")
            progress["deep_dive"]["core_completed"].append(name)
            if name in progress["deep_dive"]["core_failed"]:
                progress["deep_dive"]["core_failed"].remove(name)
        else:
            print(f"  失败 ({elapsed:.0f}s): {output[:200]}")
            if name not in progress["deep_dive"]["core_failed"]:
                progress["deep_dive"]["core_failed"].append(name)

        save_progress(progress_path, progress)

    # 处理重要角色
    for name in pending_important:
        char_data = chars_by_name.get(name, {"canonical_name": name})
        ch_list = get_character_chapters(name, raw_census, alias_mapping)
        output_path = str(characters_dir / f"{char_data.get('file_name', name_to_pinyin(name))}.md")

        print(f"\n--- [重要] {name} ({len(ch_list)} 章) ---")

        summaries = load_summaries_for_chapters(chapters_dir, ch_list)

        prompt = build_important_character_prompt(char_data, summaries, output_path)

        start_time = time.time()
        success, output = run_claude_prompt(prompt, model, timeout, allow_tools="Write")
        elapsed = time.time() - start_time

        progress["stats"]["total_calls"] += 1
        progress["stats"]["total_time_seconds"] += int(elapsed)

        if success:
            print(f"  完成 ({elapsed:.0f}s)")
            progress["deep_dive"]["important_completed"].append(name)
            if name in progress["deep_dive"]["important_failed"]:
                progress["deep_dive"]["important_failed"].remove(name)
        else:
            print(f"  失败 ({elapsed:.0f}s): {output[:200]}")
            if name not in progress["deep_dive"]["important_failed"]:
                progress["deep_dive"]["important_failed"].append(name)

        save_progress(progress_path, progress)

    # 检查是否全部完成
    all_core_done = set(progress["deep_dive"]["core_completed"]) >= set(core_chars)
    all_imp_done = set(progress["deep_dive"]["important_completed"]) >= set(important_chars)
    if all_core_done and all_imp_done:
        progress["phase"] = "relationship"
        save_progress(progress_path, progress)
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
    template = (PROMPTS_DIR / "relationship_build.md").read_text(encoding="utf-8")
    prompt = template.replace("{relationship_timeline_json}",
                              json.dumps(raw_census.get("relationship_timeline", []),
                                        ensure_ascii=False, indent=2))
    prompt = prompt.replace("{character_profiles_summary}", character_profiles)
    prompt = prompt.replace("{arc_summary}", arc_summary)
    prompt = prompt.replace("{output_path}", output_path)
    return prompt


def build_relationship_validate_prompt(current_relationships: str,
                                       character_profiles: str) -> str:
    template = (PROMPTS_DIR / "relationship_validate.md").read_text(encoding="utf-8")
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
                corrections = extract_json_from_output(output)
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
    template = (PROMPTS_DIR / "status_update.md").read_text(encoding="utf-8")
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
                            args.character)

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
