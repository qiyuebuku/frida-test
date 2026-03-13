#!/usr/bin/env python3
"""
T7 风格层分析 — 批量编排脚本

从 T1 原文 + T3 弧文件 + T6 读者反馈中提取写作风格特征。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
前置条件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - T1 已完成: text/ 下有原文章节 (chXXXX.md)
  - T3 已完成: plot/outline/ 下有弧文件（用于智能抽样）
  - T6 已完成: reader/feedback/emotions.md（用于验证高赞段落风格）
  - 需要 jieba（pip install jieba）用于词频统计

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三阶段 Pipeline（按顺序自动执行，支持断点续传）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  阶段 0  preprocess     Python 全量定量统计 + 智能抽样计划
                         全量统计：句长分布、对话占比、段落长度、时间表达、情感表达
                         智能抽样：首章/末章 + 高赞章 + 转折章 + 各弧代表章
                         产出: style/.build/stats.json（全量统计数据）
                               style/.build/sampling_plan.json（抽样计划，默认 20 章）
                         耗时: ~1-2min（取决于章节数 + 字数），0 次 AI 调用

  阶段 1  sample         Claude 抽样精析，每批 4 章，提取 7 维定性特征
                         产出: style/.build/batch_XX.json
                               包含叙事视角、场景描写、情感表达、意象、用词、写作技巧、读者验证
                         耗时: 取决于抽样章数，每批 1 次 AI 调用
                               支持 --concurrency M 并发 M 批加速

  阶段 2  merge          全局融合，合并定量统计 + 定性分析 → 3 个 MD 文件
                         产出: style/narrative.md（叙事特征：视角、节奏、章节结构）
                               style/vocabulary.md（用词特征：高频词、时间表达、情感词）
                               style/rhythm.md（节奏特征：句长分布、对话占比、段落长度）
                               style/index.md（风格概览）
                         耗时: ~2min, 1 次 AI 调用（需要 Write 工具）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
典型用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 一键全流程（从当前进度自动继续）
  python batch_style.py --book-dir qidian/novel_kb/玄鉴仙族

  # 加速：阶段 1 抽样精析并发 3 批
  python batch_style.py --book-dir ... --concurrency 3

  # 自定义抽样参数
  python batch_style.py --book-dir ... --sample-size 30 --chapters-per-call 5

  # 只运行某个阶段
  python batch_style.py --book-dir ... --phase preprocess
  python batch_style.py --book-dir ... --phase sample --concurrency 3
  python batch_style.py --book-dir ... --phase merge

  # 试运行 + 验证产出
  python batch_style.py --book-dir ... --dry-run
  python batch_style.py --book-dir ... --validate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
参数说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --book-dir PATH          知识库目录（必需）
  --sample-size N          抽样章节数（默认 20，影响阶段 0）
  --chapters-per-call M    每批分析章数（默认 4，影响阶段 1）
  --concurrency K          并发数（默认 1，仅对 sample 阶段生效，建议 2-4）
  --phase PHASE            只运行特定阶段（preprocess/sample/merge）
  --model MODEL            Claude 模型（默认 sonnet）
  --timeout SEC            单次调用超时秒数（默认 600）
  --dry-run                试运行，不执行 Claude 调用
  --validate               验证产出文件完整性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
产出目录结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {book_dir}/
    style/
      narrative.md          叙事特征（叙事视角、节奏控制、章节开头/结尾模式）
      vocabulary.md         用词特征（高频词 TOP 100、时间表达、情感表达方式）
      rhythm.md             节奏特征（句长分布、对话占比、段落长度统计）
      index.md              风格概览（统计摘要 + 文件导航）
      .build/
        stats.json          全量统计数据（按章、按弧、按三段统计）
        sampling_plan.json  抽样计划（抽样章节 + 原因标注 + 弧信息）
        batch_XX.json       抽样分析 JSON
      .progress.json        进度文件
"""

import argparse
import fcntl
import json
import math
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from json_fixer import fix_and_parse_json
from kb_common import (
    SKILL_DIR, PROMPTS_DIR,
    resolve_book_dir, load_progress, save_progress, merge_stats,
    run_claude_prompt, list_chapter_files, print_flush,
)

# ============================================================
# 硬依赖检查
# ============================================================
try:
    import jieba
    jieba.setLogLevel(jieba.logging.WARNING)
except ImportError:
    print("ERROR: 请先安装 jieba（pip install jieba）")
    print("jieba 是必须依赖，无法降级。")
    sys.exit(1)

# 产出文件
STYLE_FILES = ["narrative", "vocabulary", "rhythm"]
STYLE_NAMES = {
    "narrative": "叙事特征",
    "vocabulary": "用词特征",
    "rhythm": "节奏特征",
}

# 分句正则（句号、问号、叹号、省略号）
SENTENCE_SPLIT_RE = re.compile(r'[。！？…\u2026]+')
# 对话检测正则（中文全角引号 "" 和 「」）
DIALOGUE_LINE_RE = re.compile(
    r'(?:\u201c[^\u201d]*\u201d|「[^」]*」)'  # "..." 或 「...」
)
# 评论数标注
COMMENT_COUNT_RE = re.compile(r'\((\d+)评\)\s*$')
# 对话标签
DIALOGUE_TAG_RE = re.compile(r'(说|道|笑道|喝道|叫道|冷道|怒道|淡淡道|沉声道|低声道|轻声道|喊道|答道|问道|叹道|吼道|骂道)')
# 时间表达
TRADITIONAL_TIME_RE = re.compile(r'(寅时|卯时|辰时|巳时|午时|未时|申时|酉时|戌时|亥时|子时|丑时)')
MODERN_TIME_RE = re.compile(r'(\d+[点时](?:\d+分)?|凌晨|早上|上午|下午|傍晚|晚上)')
# 直白情感词
DIRECT_EMOTION_RE = re.compile(
    r'(心中涌起|心头一震|不由得感到|感到一阵|心里一暖|心中大喜|'
    r'心中一凛|内心深处|心中暗想|暗自欢喜|心中暗喜|怒火中烧|'
    r'悲从中来|喜不自禁|心如刀割|心花怒放)'
)
# 间接情感表达（动作/细节暗示）
INDIRECT_EMOTION_RE = re.compile(
    r'(叹了口气|攥紧了拳|咬了咬牙|微微皱眉|嘴角微扬|'
    r'沉默不语|转过身去|垂下了眼|握紧了手|深吸一口气|'
    r'抿了抿嘴|眉头一挑|嘴角一勾|眼神一暗|目光一闪)'
)


# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="风格层分析")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--phase",
                        choices=["preprocess", "sample", "merge"],
                        help="只运行特定阶段")
    parser.add_argument("--model", default="sonnet", help="Claude 模型（默认 sonnet）")
    parser.add_argument("--dry-run", action="store_true", help="试运行")
    parser.add_argument("--validate", action="store_true", help="验证产出")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次 Claude 调用超时秒数（默认 600）")
    parser.add_argument("--sample-size", type=int, default=20,
                        help="抽样章节数（默认 20）")
    parser.add_argument("--chapters-per-call", type=int, default=4,
                        help="每次 Claude 调用分析的章节数（默认 4）")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="并发 Claude 调用数（默认 1，建议 2-4）")
    return parser.parse_args()


# ============================================================
# 路径解析
# ============================================================

def get_text_dir(book_dir: Path) -> Path:
    d = book_dir / "text"
    if not d.exists():
        print(f"错误：text 目录不存在: {d}")
        print("请先运行 T1（数据提取）")
        sys.exit(1)
    return d


def get_outline_dir(book_dir: Path) -> Path:
    d = book_dir / "plot" / "outline"
    if not d.exists():
        print(f"ERROR: T3 弧文件不存在，请先运行 kb-plot-extract")
        print(f"路径: {d}")
        sys.exit(1)
    arc_files = list(d.glob("arc_*.md"))
    if not arc_files:
        print(f"ERROR: T3 弧文件不存在（{d} 中无 arc_*.md），请先运行 kb-plot-extract")
        sys.exit(1)
    return d


def get_emotions_path(book_dir: Path) -> Path:
    p = book_dir / "reader" / "feedback" / "emotions.md"
    if not p.exists():
        print(f"ERROR: T6 读者反馈不存在，请先运行 kb-reader-feedback")
        print(f"路径: {p}")
        sys.exit(1)
    return p


def get_style_dir(book_dir: Path) -> Path:
    d = book_dir / "style"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_build_dir(book_dir: Path) -> Path:
    d = book_dir / "style" / ".build"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_progress_path(book_dir: Path) -> Path:
    return book_dir / "style" / ".progress.json"


# ============================================================
# 进度管理
# ============================================================

print_lock = threading.Lock()


def _default_progress() -> dict:
    return {
        "phase": "preprocess",
        "preprocess": {"status": "pending"},
        "sample_analyze": {
            "status": "pending",
            "batches_completed": [],
            "batches_failed": [],
        },
        "global_merge": {"status": "pending"},
        "stats": {
            "total_calls": 0,
            "total_time_seconds": 0,
        },
    }


def _merge_progress(disk: dict, progress: dict) -> dict:
    """T7 专用合并策略"""
    # 合并 sample_analyze 列表（并集）
    for key in ("batches_completed", "batches_failed"):
        merged = list(dict.fromkeys(
            disk.get("sample_analyze", {}).get(key, []) +
            progress.get("sample_analyze", {}).get(key, [])
        ))
        disk.setdefault("sample_analyze", {})[key] = merged
        progress.setdefault("sample_analyze", {})[key] = merged
    # stats
    for sk in ("total_calls", "total_time_seconds"):
        disk["stats"][sk] = max(
            disk.get("stats", {}).get(sk, 0),
            progress.get("stats", {}).get(sk, 0),
        )
    # phase / status（取进度较前的）
    phase_order = ["preprocess", "sample_analyze", "global_merge", "done"]
    for field in ("phase",):
        di = phase_order.index(disk.get(field, "preprocess")) if disk.get(field, "preprocess") in phase_order else 0
        pi = phase_order.index(progress.get(field, "preprocess")) if progress.get(field, "preprocess") in phase_order else 0
        disk[field] = phase_order[max(di, pi)]
    for section in ("preprocess", "sample_analyze", "global_merge"):
        ds = disk.get(section, {}).get("status", "pending")
        ps = progress.get(section, {}).get("status", "pending")
        status_order = ["pending", "completed"]
        dsi = status_order.index(ds) if ds in status_order else 0
        psi = status_order.index(ps) if ps in status_order else 0
        disk[section]["status"] = status_order[max(dsi, psi)]
    progress.update(disk)
    return progress


# ============================================================
# 文本解析工具
# ============================================================

def load_chapter_text(text_dir: Path, ch_num: int) -> str:
    """加载单章纯正文（去掉标题行和评论数标注）"""
    filepath = text_dir / f"ch{ch_num:04d}.md"
    if not filepath.exists():
        return ""
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()
    text_lines = []
    for line in lines:
        # 跳过标题行
        if line.startswith("# "):
            continue
        # 去掉评论数标注
        clean = COMMENT_COUNT_RE.sub("", line).rstrip()
        if clean:
            text_lines.append(clean)
    return "\n".join(text_lines)


def get_chapter_title(text_dir: Path, ch_num: int) -> str:
    """获取章节标题"""
    filepath = text_dir / f"ch{ch_num:04d}.md"
    if not filepath.exists():
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
    if first_line.startswith("# "):
        return first_line[2:]
    return ""


# ============================================================
# 阶段 0A：全量定量统计
# ============================================================

def split_sentences(text: str) -> list[str]:
    """分句"""
    parts = SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in parts if s.strip()]


def count_dialogues(text: str) -> tuple[int, int, list[str]]:
    """统计对话行数、总行数、对话内容列表"""
    lines = text.splitlines()
    total_lines = len(lines)
    dialogue_lines = 0
    dialogues = []
    for line in lines:
        # 检测中文全角引号 "" 或 「」
        if '\u201c' in line or '「' in line:
            matches = DIALOGUE_LINE_RE.findall(line)
            if matches:
                dialogue_lines += 1
                dialogues.extend(matches)
    return dialogue_lines, total_lines, dialogues


def classify_sentence(sentence: str) -> str:
    """粗分类句子类型：environment/action/dialogue/thought"""
    if DIALOGUE_LINE_RE.search(sentence):
        return "dialogue"
    if re.search(r'(想着|暗想|心道|心中|默默|念头|思索|回忆|感觉)', sentence):
        return "thought"
    if re.search(r'(天空|月光|阳光|树|山|河|风|雨|云|雾|夜|晨|黄昏|日落)', sentence):
        return "environment"
    return "action"


def analyze_single_chapter(text: str) -> dict:
    """分析单章的定量指标"""
    if not text:
        return {}

    sentences = split_sentences(text)
    if not sentences:
        return {}

    # 句长统计
    lengths = [len(s) for s in sentences]
    avg_len = sum(lengths) / len(lengths) if lengths else 0
    sorted_lens = sorted(lengths)
    median_len = sorted_lens[len(sorted_lens) // 2] if sorted_lens else 0

    short = sum(1 for l in lengths if l < 10)
    medium = sum(1 for l in lengths if 10 <= l <= 30)
    long = sum(1 for l in lengths if l > 30)
    total = len(lengths) or 1

    # 段落统计
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    para_lengths = [len(p) for p in paragraphs]
    avg_para_len = sum(para_lengths) / len(para_lengths) if para_lengths else 0

    # 对话统计
    dialogue_lines, total_lines, dialogues = count_dialogues(text)
    dialogue_ratio = dialogue_lines / max(total_lines, 1)

    # 对话标签
    tags = DIALOGUE_TAG_RE.findall(text)
    tag_counter = Counter(tags)

    # 时间表达
    traditional_times = TRADITIONAL_TIME_RE.findall(text)
    modern_times = MODERN_TIME_RE.findall(text)

    # 情感表达
    direct_emotions = DIRECT_EMOTION_RE.findall(text)
    indirect_emotions = INDIRECT_EMOTION_RE.findall(text)

    # 章首/章尾分类
    opening_sentences = sentences[:3]
    closing_sentences = sentences[-3:] if len(sentences) >= 3 else sentences
    opening_types = [classify_sentence(s) for s in opening_sentences]
    closing_types = [classify_sentence(s) for s in closing_sentences]

    # 总字数
    char_count = len(text.replace("\n", "").replace(" ", ""))

    return {
        "char_count": char_count,
        "sentence_count": len(sentences),
        "sentence_avg_len": round(avg_len, 1),
        "sentence_median_len": median_len,
        "sentence_short_ratio": round(short / total, 3),
        "sentence_medium_ratio": round(medium / total, 3),
        "sentence_long_ratio": round(long / total, 3),
        "paragraph_count": len(paragraphs),
        "paragraph_avg_len": round(avg_para_len, 1),
        "dialogue_ratio": round(dialogue_ratio, 3),
        "dialogue_tag_counts": dict(tag_counter.most_common(20)),
        "traditional_time_count": len(traditional_times),
        "modern_time_count": len(modern_times),
        "direct_emotion_count": len(direct_emotions),
        "indirect_emotion_count": len(indirect_emotions),
        "opening_types": opening_types,
        "closing_types": closing_types,
    }


def compute_word_freq(all_text: str, top_n: int = 100) -> list[tuple[str, int]]:
    """使用 jieba 分词计算高频词"""
    # 过滤停用词
    stopwords = set("的了在是我他她它们这那个一不有人也就都"
                    "要和你着到说上下来去过把被从中为与")
    words = jieba.lcut(all_text)
    # 只保留长度 >= 2 的词
    filtered = [w for w in words if len(w) >= 2 and w not in stopwords
                and not re.match(r'^[\d\s\W]+$', w)]
    counter = Counter(filtered)
    return counter.most_common(top_n)


# ============================================================
# 阶段 0B：T3 弧文件解析
# ============================================================

def parse_arc_files(outline_dir: Path) -> list[dict]:
    """解析 T3 弧文件，提取弧信息"""
    arcs = []
    for arc_file in sorted(outline_dir.glob("arc_*.md")):
        content = arc_file.read_text(encoding="utf-8")

        # 解析弧 ID
        arc_id = arc_file.stem  # e.g., "arc_01"

        # 解析章节范围
        range_match = re.search(
            r'\*\*章节范围\*\*:\s*ch(\d+)\s*-\s*ch(\d+)',
            content
        )
        if not range_match:
            continue
        start_ch = int(range_match.group(1))
        end_ch = int(range_match.group(2))

        # 解析核心冲突
        conflict_match = re.search(
            r'## 核心冲突\s*\n(.+?)(?=\n## |\Z)',
            content, re.DOTALL
        )
        core_conflict = conflict_match.group(1).strip() if conflict_match else ""

        # 解析关键转折点中的章节
        turning_points = []
        tp_match = re.search(
            r'## 关键转折点\s*\n(.+?)(?=\n## |\Z)',
            content, re.DOTALL
        )
        if tp_match:
            tp_text = tp_match.group(1)
            for ch_m in re.finditer(r'ch(\d+)', tp_text):
                ch_num = int(ch_m.group(1))
                if ch_num not in turning_points:
                    turning_points.append(ch_num)

        # 从核心冲突推断 mood
        mood = "综合"
        conflict_lower = core_conflict.lower()
        if any(w in conflict_lower for w in ["战", "斗", "杀", "攻", "守", "敌"]):
            mood = "战斗"
        elif any(w in conflict_lower for w in ["日常", "生活", "修炼", "平静"]):
            mood = "日常"
        elif any(w in conflict_lower for w in ["悬", "秘", "谜", "探", "密"]):
            mood = "悬疑"
        elif any(w in conflict_lower for w in ["冒险", "旅途", "探索", "历"]):
            mood = "冒险"

        arcs.append({
            "arc_id": arc_id,
            "start_ch": start_ch,
            "end_ch": end_ch,
            "core_conflict": core_conflict[:200],
            "mood": mood,
            "turning_points": turning_points,
        })

    return arcs


# ============================================================
# 阶段 0C：T6 读者反馈解析
# ============================================================

def parse_emotions_file(emotions_path: Path) -> dict:
    """解析 T6 emotions.md，提取高赞段落和情绪映射"""
    content = emotions_path.read_text(encoding="utf-8")

    # 提取 TOP 高赞段落
    top_paragraphs = []
    # 匹配 "### N. chXXXX 第N段（👍 XXXXX）"
    para_re = re.compile(
        r'### \d+\.\s+ch(\d+)\s+第(\d+)段（👍\s*(\d+)）\s*\n'
        r'> 原文：(.+?)(?:\n)',
        re.MULTILINE
    )
    for m in para_re.finditer(content):
        ch_num = int(m.group(1))
        para_id = int(m.group(2))
        likes = int(m.group(3))
        text = m.group(4).strip()

        # 查找后续的情绪类型和写作启示
        block_start = m.end()
        block_end = content.find("\n### ", block_start)
        if block_end == -1:
            block_end = len(content)
        block = content[block_start:block_end]

        emotion_m = re.search(r'\*\*情绪类型\*\*：(.+)', block)
        emotion_type = emotion_m.group(1).strip() if emotion_m else ""

        insight_m = re.search(r'\*\*写作启示\*\*：(.+)', block)
        writing_insight = insight_m.group(1).strip() if insight_m else ""

        top_paragraphs.append({
            "chapter": f"ch{ch_num:04d}",
            "chapter_num": ch_num,
            "paragraph": para_id,
            "text": text[:200],
            "likes": likes,
            "emotion_type": emotion_type,
            "writing_insight": writing_insight,
        })

    # 高赞章节去重
    high_chapters = sorted(set(p["chapter_num"] for p in top_paragraphs))

    # 提取场景类型 → 写作启示映射
    emotion_technique_map = {}
    scene_re = re.compile(
        r'### (.+?)（出现 \d+ 次.*?）\s*\n'
        r'(?:.*?\n)*?'
        r'- \*\*写作启示\*\*：(.+?)(?:\n)',
        re.MULTILINE
    )
    for m in scene_re.finditer(content):
        scene_type = m.group(1).strip()
        insight = m.group(2).strip()
        # 提取大类
        category = scene_type.split("-")[0] if "-" in scene_type else scene_type
        if category not in emotion_technique_map:
            emotion_technique_map[category] = []
        emotion_technique_map[category].append(insight[:100])

    return {
        "top_paragraphs": top_paragraphs,
        "high_engagement_chapters": high_chapters,
        "emotion_technique_map": emotion_technique_map,
    }


# ============================================================
# 阶段 0：统合执行
# ============================================================

def build_sampling_plan(arcs: list[dict], reader_data: dict,
                        all_chapters: list[int],
                        sample_size: int) -> list[int]:
    """构建智能抽样计划"""
    selected = set()

    # 优先级 1：必选章
    if all_chapters:
        selected.add(all_chapters[0])   # 第 1 章
        selected.add(all_chapters[-1])  # 最后一章

    # T6 高赞章节（TOP 3）
    for ch in reader_data.get("high_engagement_chapters", [])[:3]:
        if ch in all_chapters:
            selected.add(ch)

    # 优先级 2：转折章（来自 T3 弧文件）
    for arc in arcs:
        for tp in arc["turning_points"]:
            if tp in all_chapters and len(selected) < sample_size:
                selected.add(tp)

    # 优先级 3：均匀补充（确保每个弧至少 1 章）
    for arc in arcs:
        arc_chapters = [c for c in all_chapters
                        if arc["start_ch"] <= c <= arc["end_ch"]]
        if not arc_chapters:
            continue
        # 检查弧内是否已有选中章节
        arc_selected = [c for c in arc_chapters if c in selected]
        if not arc_selected and len(selected) < sample_size:
            # 选弧的中间章节
            mid = arc_chapters[len(arc_chapters) // 2]
            selected.add(mid)

    # 如果还不够，均匀补充
    remaining = sample_size - len(selected)
    if remaining > 0:
        unselected = [c for c in all_chapters if c not in selected]
        if unselected:
            step = max(1, len(unselected) // remaining)
            for i in range(0, len(unselected), step):
                if len(selected) >= sample_size:
                    break
                selected.add(unselected[i])

    return sorted(selected)[:sample_size]


def phase_preprocess(book_dir: Path, all_chapters: list[int],
                     progress: dict, progress_path: Path, dry_run: bool,
                     sample_size: int):
    """执行阶段 0：Python 全量统计 + 辅助数据解析"""
    print("\n" + "=" * 60)
    print("阶段 0：Python 全量统计 + 辅助数据解析")
    print("=" * 60)

    if progress["preprocess"]["status"] == "completed":
        print("预处理已完成！")
        return

    text_dir = get_text_dir(book_dir)
    outline_dir = get_outline_dir(book_dir)
    emotions_path = get_emotions_path(book_dir)

    if dry_run:
        print(f"将分析 {len(all_chapters)} 章正文")
        print(f"将解析 T3 弧文件: {outline_dir}")
        print(f"将解析 T6 读者反馈: {emotions_path}")
        print(f"产出: style/.build/stats.json + sampling_plan.json")
        return

    start_time = time.time()

    # ---- A. 全量定量统计 ----
    print("  A. 全量定量统计...")

    chapter_stats = {}
    all_text_for_freq = []
    total_chars = 0
    errors = []

    # 按弧分段的统计容器
    arcs = parse_arc_files(outline_dir)
    arc_stats = {arc["arc_id"]: {
        "chapter_range": f"ch{arc['start_ch']:04d}-ch{arc['end_ch']:04d}",
        "mood": arc["mood"],
        "sentence_lengths": [],
        "dialogue_ratios": [],
        "paragraph_lengths": [],
        "char_count": 0,
    } for arc in arcs}

    # 三段统计容器
    period_stats = {
        "early": {"sentence_lengths": [], "dialogue_ratios": [], "paragraph_lengths": [], "char_count": 0},
        "middle": {"sentence_lengths": [], "dialogue_ratios": [], "paragraph_lengths": [], "char_count": 0},
        "late": {"sentence_lengths": [], "dialogue_ratios": [], "paragraph_lengths": [], "char_count": 0},
    }

    for idx, ch in enumerate(all_chapters):
        try:
            text = load_chapter_text(text_dir, ch)
            if not text:
                continue

            stats = analyze_single_chapter(text)
            if not stats:
                continue

            ch_str = f"ch{ch:04d}"
            chapter_stats[ch_str] = stats
            total_chars += stats["char_count"]

            # 收集全文用于词频
            all_text_for_freq.append(text)

            # 归入弧统计
            for arc in arcs:
                if arc["start_ch"] <= ch <= arc["end_ch"]:
                    aid = arc["arc_id"]
                    arc_stats[aid]["sentence_lengths"].append(stats["sentence_avg_len"])
                    arc_stats[aid]["dialogue_ratios"].append(stats["dialogue_ratio"])
                    arc_stats[aid]["paragraph_lengths"].append(stats["paragraph_avg_len"])
                    arc_stats[aid]["char_count"] += stats["char_count"]
                    break

            # 归入三段统计
            third = len(all_chapters) // 3
            if ch <= all_chapters[third - 1]:
                period = "early"
            elif ch <= all_chapters[2 * third - 1]:
                period = "middle"
            else:
                period = "late"
            period_stats[period]["sentence_lengths"].append(stats["sentence_avg_len"])
            period_stats[period]["dialogue_ratios"].append(stats["dialogue_ratio"])
            period_stats[period]["paragraph_lengths"].append(stats["paragraph_avg_len"])
            period_stats[period]["char_count"] += stats["char_count"]

        except Exception as e:
            errors.append(f"ch{ch:04d}: {e}")

        if (idx + 1) % 100 == 0 or idx + 1 == len(all_chapters):
            elapsed = time.time() - start_time
            print(f"    已统计 {idx + 1}/{len(all_chapters)} 章 ({elapsed:.0f}s)")

    # 全局聚合统计
    all_sentence_lens = []
    all_dialogue_ratios = []
    all_para_lens = []
    all_opening_types = Counter()
    all_closing_types = Counter()
    all_dialogue_tags = Counter()
    total_traditional_time = 0
    total_modern_time = 0
    total_direct_emotion = 0
    total_indirect_emotion = 0

    for ch_str, s in chapter_stats.items():
        all_sentence_lens.append(s["sentence_avg_len"])
        all_dialogue_ratios.append(s["dialogue_ratio"])
        all_para_lens.append(s["paragraph_avg_len"])
        for t in s["opening_types"]:
            all_opening_types[t] += 1
        for t in s["closing_types"]:
            all_closing_types[t] += 1
        for tag, cnt in s["dialogue_tag_counts"].items():
            all_dialogue_tags[tag] += cnt
        total_traditional_time += s["traditional_time_count"]
        total_modern_time += s["modern_time_count"]
        total_direct_emotion += s["direct_emotion_count"]
        total_indirect_emotion += s["indirect_emotion_count"]

    # 词频统计
    print("  词频统计（jieba）...")
    combined_text = "\n".join(all_text_for_freq)
    word_freq = compute_word_freq(combined_text, top_n=100)

    # 聚合弧统计
    arc_summary = {}
    for aid, data in arc_stats.items():
        if not data["sentence_lengths"]:
            continue
        arc_summary[aid] = {
            "chapter_range": data["chapter_range"],
            "mood": data["mood"],
            "avg_sentence_len": round(sum(data["sentence_lengths"]) / len(data["sentence_lengths"]), 1),
            "avg_dialogue_ratio": round(sum(data["dialogue_ratios"]) / len(data["dialogue_ratios"]), 3),
            "avg_paragraph_len": round(sum(data["paragraph_lengths"]) / len(data["paragraph_lengths"]), 1),
            "total_chars": data["char_count"],
            "chapter_count": len(data["sentence_lengths"]),
        }

    # 聚合三段统计
    period_summary = {}
    for period, data in period_stats.items():
        if not data["sentence_lengths"]:
            continue
        period_summary[period] = {
            "avg_sentence_len": round(sum(data["sentence_lengths"]) / len(data["sentence_lengths"]), 1),
            "avg_dialogue_ratio": round(sum(data["dialogue_ratios"]) / len(data["dialogue_ratios"]), 3),
            "avg_paragraph_len": round(sum(data["paragraph_lengths"]) / len(data["paragraph_lengths"]), 1),
            "total_chars": data["char_count"],
            "chapter_count": len(data["sentence_lengths"]),
        }

    # 开篇/结尾类型占比
    total_opening = sum(all_opening_types.values()) or 1
    total_closing = sum(all_closing_types.values()) or 1
    opening_dist = {t: round(c / total_opening, 3) for t, c in all_opening_types.most_common()}
    closing_dist = {t: round(c / total_closing, 3) for t, c in all_closing_types.most_common()}

    # std_dev
    def std_dev(values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return round(math.sqrt(variance), 1)

    global_avg_sentence = round(sum(all_sentence_lens) / max(len(all_sentence_lens), 1), 1)
    global_median_sentence = sorted(all_sentence_lens)[len(all_sentence_lens) // 2] if all_sentence_lens else 0

    # ---- B. T3 弧信息解析 ----
    print("  B. T3 弧信息解析...")
    # arcs 已在上面解析过
    print(f"    解析到 {len(arcs)} 个弧")

    # ---- C. T6 读者反馈解析 ----
    print("  C. T6 读者反馈解析...")
    reader_data = parse_emotions_file(emotions_path)
    print(f"    高赞段落: {len(reader_data['top_paragraphs'])} 个")
    print(f"    高赞章节: {reader_data['high_engagement_chapters']}")

    # ---- 构建抽样计划 ----
    print(f"  构建抽样计划（目标 {sample_size} 章）...")
    sampled = build_sampling_plan(arcs, reader_data, all_chapters, sample_size)
    print(f"    实际抽样: {len(sampled)} 章")
    print(f"    章节: {[f'ch{c:04d}' for c in sampled]}")

    # 标注每章的来源
    sampling_reasons = {}
    for ch in sampled:
        reasons = []
        if ch == all_chapters[0]:
            reasons.append("首章")
        if ch == all_chapters[-1]:
            reasons.append("末章")
        if ch in reader_data.get("high_engagement_chapters", []):
            reasons.append("高赞章")
        for arc in arcs:
            if ch in arc["turning_points"]:
                reasons.append(f"转折章({arc['arc_id']})")
            elif arc["start_ch"] <= ch <= arc["end_ch"]:
                reasons.append(f"弧覆盖({arc['arc_id']})")
        sampling_reasons[f"ch{ch:04d}"] = reasons if reasons else ["均匀补充"]

    elapsed = time.time() - start_time

    # ---- 保存 stats.json ----
    stats_data = {
        "total_chapters": len(chapter_stats),
        "total_chars": total_chars,
        "sentence": {
            "avg_length": global_avg_sentence,
            "median_length": global_median_sentence,
            "std_dev": std_dev(all_sentence_lens),
            "distribution": {
                "short": round(sum(s["sentence_short_ratio"] for s in chapter_stats.values()) / max(len(chapter_stats), 1), 3),
                "medium": round(sum(s["sentence_medium_ratio"] for s in chapter_stats.values()) / max(len(chapter_stats), 1), 3),
                "long": round(sum(s["sentence_long_ratio"] for s in chapter_stats.values()) / max(len(chapter_stats), 1), 3),
            },
            "by_period": period_summary,
            "by_arc": arc_summary,
        },
        "paragraph": {
            "avg_length": round(sum(all_para_lens) / max(len(all_para_lens), 1), 1),
        },
        "dialogue": {
            "ratio": round(sum(all_dialogue_ratios) / max(len(all_dialogue_ratios), 1), 3),
            "tags": dict(all_dialogue_tags.most_common(20)),
        },
        "chapter_patterns": {
            "opening": opening_dist,
            "closing": closing_dist,
        },
        "vocabulary": {
            "top_100_words": word_freq,
            "time_expressions": {
                "traditional": total_traditional_time,
                "modern": total_modern_time,
            },
            "emotion_expressions": {
                "indirect": total_indirect_emotion,
                "direct": total_direct_emotion,
            },
        },
        "reader_benchmarks": {
            "top_paragraphs_count": len(reader_data["top_paragraphs"]),
            "high_engagement_chapters": [f"ch{c:04d}" for c in reader_data["high_engagement_chapters"]],
            "emotion_technique_map": reader_data["emotion_technique_map"],
        },
    }

    build_dir = get_build_dir(book_dir)

    stats_path = build_dir / "stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats_data, f, ensure_ascii=False, indent=2)

    # ---- 保存 sampling_plan.json ----
    sampling_plan = {
        "sample_size": len(sampled),
        "sampled_chapters": [f"ch{c:04d}" for c in sampled],
        "sampling_reasons": sampling_reasons,
        "arcs": [{
            "arc_id": a["arc_id"],
            "chapter_range": f"ch{a['start_ch']:04d}-ch{a['end_ch']:04d}",
            "mood": a["mood"],
            "turning_points": [f"ch{tp:04d}" for tp in a["turning_points"]],
        } for a in arcs],
        "reader_top_paragraphs": reader_data["top_paragraphs"],
    }

    plan_path = build_dir / "sampling_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(sampling_plan, f, ensure_ascii=False, indent=2)

    # 输出统计
    print(f"\n统计完成 ({elapsed:.0f}s)")
    print(f"  总章数: {len(chapter_stats)}")
    print(f"  总字数: {total_chars} ({total_chars / 10000:.1f} 万)")
    print(f"  平均句长: {global_avg_sentence} 字")
    print(f"  对话占比: {stats_data['dialogue']['ratio']:.1%}")
    print(f"  传统时辰/现代时间: {total_traditional_time}/{total_modern_time}")
    print(f"  间接/直接情感: {total_indirect_emotion}/{total_direct_emotion}")
    print(f"  TOP 5 高频词: {[w for w, _ in word_freq[:5]]}")
    print(f"  弧数: {len(arcs)}")
    if errors:
        print(f"  错误: {len(errors)} 个")
        for e in errors[:3]:
            print(f"    {e}")

    print(f"\n已保存: {stats_path} ({stats_path.stat().st_size / 1024:.1f} KB)")
    print(f"已保存: {plan_path} ({plan_path.stat().st_size / 1024:.1f} KB)")

    progress["preprocess"]["status"] = "completed"
    progress["phase"] = "sample_analyze"
    save_progress(progress_path, progress, default_fn=_default_progress, merge_fn=_merge_progress)


# ============================================================
# 阶段 1：Claude 抽样精析
# ============================================================

def build_sample_batch_text(text_dir: Path, batch_chapters: list[int],
                            reader_paragraphs: list[dict]) -> str:
    """构建单批抽样文本"""
    parts = []
    for ch in batch_chapters:
        text = load_chapter_text(text_dir, ch)
        title = get_chapter_title(text_dir, ch)
        if not text:
            continue

        # 截取前 8000 字（避免过长）
        if len(text) > 8000:
            text = text[:8000] + "\n[...截断...]"

        ch_str = f"ch{ch:04d}"
        header = f"### {ch_str} · {title}" if title else f"### {ch_str}"

        # 检查此章是否有高赞段落
        ch_reader = [p for p in reader_paragraphs if p["chapter_num"] == ch]
        reader_note = ""
        if ch_reader:
            notes = []
            for p in ch_reader:
                notes.append(f"第{p['paragraph']}段（👍{p['likes']}，{p['emotion_type']}）")
            reader_note = f"\n**高赞段落**: {'; '.join(notes)}"

        parts.append(f"{header}{reader_note}\n\n{text}")

    return "\n\n---\n\n".join(parts)


def build_sample_analyze_prompt(batch_text: str, batch_label: str,
                                arc_info: str, output_file: str) -> str:
    """构建抽样分析 prompt"""
    template = (PROMPTS_DIR / "style_sample_analyze.md").read_text(encoding="utf-8")
    prompt = template.replace("{batch_text}", batch_text)
    prompt = template.replace("{batch_label}", batch_label)
    prompt = prompt.replace("{arc_info}", arc_info)
    prompt = prompt.replace("{output_file}", output_file)
    return prompt


def _process_one_sample_batch(batch_chs: list[int], batch_label: str,
                              arcs: list, reader_paragraphs: list,
                              text_dir: Path, build_dir: Path,
                              progress_path: Path,
                              model: str, timeout: int,
                              verbose: bool = True):
    """处理单个抽样分析批次（线程安全）"""
    # 构建弧信息
    arc_notes = []
    for ch in batch_chs:
        for arc in arcs:
            start = int(arc["chapter_range"].split("-")[0].replace("ch", ""))
            end = int(arc["chapter_range"].split("-")[1].replace("ch", ""))
            if start <= ch <= end:
                arc_notes.append(f"ch{ch:04d} 属于 {arc['arc_id']}（{arc['mood']}弧）")
                break
    arc_info = "\n".join(arc_notes) if arc_notes else "无弧信息"

    batch_text = build_sample_batch_text(text_dir, batch_chs, reader_paragraphs)
    if not batch_text:
        with print_lock:
            print(f"\n--- {batch_label}: 无有效文本，跳过 ---")
        # 原子更新进度
        lock_path = progress_path.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = json.load(open(progress_path, "r", encoding="utf-8")) if progress_path.exists() else _default_progress()
                if batch_label not in disk["sample_analyze"]["batches_completed"]:
                    disk["sample_analyze"]["batches_completed"].append(batch_label)
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(disk, f, ensure_ascii=False, indent=2)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        return batch_label, True

    # 输出文件路径（让 Claude agent 直接写入）
    output_path = build_dir / f"{batch_label}.json"
    prompt = build_sample_analyze_prompt(batch_text, batch_label, arc_info, str(output_path))

    ch_strs = [f"ch{c:04d}" for c in batch_chs]
    with print_lock:
        print(f"\n--- {batch_label} ({', '.join(ch_strs)}) ---")

    start_time = time.time()
    success, output = run_claude_prompt(prompt, model, timeout, verbose=verbose)
    elapsed = time.time() - start_time

    # 原子更新进度
    lock_path = progress_path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            disk = json.load(open(progress_path, "r", encoding="utf-8")) if progress_path.exists() else _default_progress()
            disk["stats"]["total_calls"] += 1
            disk["stats"]["total_time_seconds"] += int(elapsed)

            # 检查文件是否被 Claude 成功创建
            if success and output_path.exists():
                # 验证 JSON 格式
                try:
                    with open(output_path, 'r', encoding='utf-8') as f:
                        result = json.load(f)
                    with print_lock:
                        print(f"  {batch_label}: 完成 ({elapsed:.0f}s)")
                        dims = ["narrative_voice", "scene_description",
                                "emotion_expression", "imagery", "vocabulary",
                                "writing_techniques", "reader_validated"]
                        found = [d for d in dims if result.get(d)]
                        print(f"  维度: {', '.join(found)}")
                    if batch_label not in disk["sample_analyze"]["batches_completed"]:
                        disk["sample_analyze"]["batches_completed"].append(batch_label)
                except json.JSONDecodeError as e:
                    with print_lock:
                        print(f"  {batch_label}: JSON 格式错误 ({elapsed:.0f}s): {e}")
                    if batch_label not in disk["sample_analyze"]["batches_failed"]:
                        disk["sample_analyze"]["batches_failed"].append(batch_label)
            else:
                with print_lock:
                    if not output_path.exists():
                        print(f"  {batch_label}: 文件未创建 ({elapsed:.0f}s)")
                    else:
                        print(f"  {batch_label}: 失败 ({elapsed:.0f}s): {output[:200]}")
                # 保存 Claude 的原始输出供调试
                debug_path = build_dir / f"{batch_label}_debug.txt"
                debug_path.write_text(output, encoding="utf-8")
                if batch_label not in disk["sample_analyze"]["batches_failed"]:
                    disk["sample_analyze"]["batches_failed"].append(batch_label)

            with open(progress_path, "w", encoding="utf-8") as f_out:
                json.dump(disk, f_out, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    return batch_label, success


def phase_sample_analyze(book_dir: Path, all_chapters: list[int],
                         progress: dict, progress_path: Path,
                         model: str, timeout: int, dry_run: bool,
                         chapters_per_call: int, concurrency: int = 1):
    """执行阶段 1：Claude 抽样精析"""
    print("\n" + "=" * 60)
    print("阶段 1：Claude 抽样精析")
    print("=" * 60)

    build_dir = get_build_dir(book_dir)
    text_dir = get_text_dir(book_dir)

    # 加载抽样计划
    plan_path = build_dir / "sampling_plan.json"
    if not plan_path.exists():
        print("错误：sampling_plan.json 不存在，请先运行阶段 0")
        return

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    sampled = [int(ch.replace("ch", "")) for ch in plan["sampled_chapters"]]
    arcs = plan.get("arcs", [])
    reader_paragraphs = plan.get("reader_top_paragraphs", [])

    # 分批
    batches = []
    for i in range(0, len(sampled), chapters_per_call):
        batch_chs = sampled[i:i + chapters_per_call]
        batch_label = f"batch_{i // chapters_per_call + 1:02d}"
        batches.append((batch_chs, batch_label))

    completed = set(progress["sample_analyze"]["batches_completed"])
    pending = [(chs, label) for chs, label in batches if label not in completed]

    print(f"抽样章节: {len(sampled)} 章，分 {len(batches)} 批（每批 {chapters_per_call} 章）")
    print(f"已完成: {len(completed)}，待处理: {len(pending)}")
    if concurrency > 1:
        print(f"并发数: {concurrency}")

    if dry_run:
        for chs, label in pending:
            ch_strs = [f"ch{c:04d}" for c in chs]
            print(f"  {label}: {ch_strs}")
        return

    if not pending:
        print("无待处理批次")
    elif concurrency <= 1:
        # 串行执行
        for batch_chs, batch_label in pending:
            _process_one_sample_batch(
                batch_chs, batch_label, arcs, reader_paragraphs,
                text_dir, build_dir, progress_path, model, timeout,
            )
    else:
        # 并发执行
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(
                    _process_one_sample_batch,
                    batch_chs, batch_label, arcs, reader_paragraphs,
                    text_dir, build_dir, progress_path, model, timeout,
                    verbose=False,
                ): batch_label
                for batch_chs, batch_label in pending
            }
            for future in as_completed(futures):
                label = futures[future]
                try:
                    future.result()
                except Exception as e:
                    with print_lock:
                        print(f"  {label}: 异常 - {e}")

    # 重新加载进度，检查是否全部完成
    progress = load_progress(progress_path, default_fn=_default_progress)
    all_done = set(label for _, label in batches) <= set(
        progress["sample_analyze"]["batches_completed"]
    )
    if all_done:
        progress["sample_analyze"]["status"] = "completed"
        progress["phase"] = "global_merge"
        save_progress(progress_path, progress, default_fn=_default_progress, merge_fn=_merge_progress)
        print("\n抽样分析全部完成！")


# ============================================================
# 阶段 2：全局融合
# ============================================================

def merge_samples(build_dir: Path) -> list[dict]:
    """合并全部抽样分析 JSON"""
    samples = []
    for f in sorted(build_dir.glob("batch_*.json")):
        with open(f, "r", encoding="utf-8") as fp:
            samples.append(json.load(fp))
    return samples


def build_global_merge_prompt(stats_json: str, samples_json: str,
                               reader_benchmarks_json: str,
                               style_dir: str) -> str:
    """构建全局融合 prompt"""
    template = (PROMPTS_DIR / "style_global_merge.md").read_text(encoding="utf-8")
    prompt = template.replace("{stats_json}", stats_json)
    prompt = prompt.replace("{samples_json}", samples_json)
    prompt = prompt.replace("{reader_benchmarks_json}", reader_benchmarks_json)
    prompt = prompt.replace("{style_dir}", style_dir)
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
    style_dir = get_style_dir(book_dir)

    # 加载 stats
    stats_path = build_dir / "stats.json"
    if not stats_path.exists():
        print("错误：stats.json 不存在，请先运行阶段 0")
        return

    with open(stats_path, "r", encoding="utf-8") as f:
        stats = json.load(f)

    # 加载 samples
    samples = merge_samples(build_dir)
    if not samples:
        print("错误：无抽样分析数据，请先运行阶段 1")
        return

    print(f"统计数据: {stats_path.stat().st_size / 1024:.1f} KB")
    print(f"抽样分析: {len(samples)} 批")

    # 读者反馈 benchmarks
    plan_path = build_dir / "sampling_plan.json"
    reader_paragraphs = []
    if plan_path.exists():
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        reader_paragraphs = plan.get("reader_top_paragraphs", [])

    if dry_run:
        print(f"将融合统计 + {len(samples)} 批分析 + {len(reader_paragraphs)} 个高赞段落 → 4 个 MD 文件")
        return

    # 精简 stats 避免 prompt 过长（去掉 per-chapter 数据，只保留聚合）
    stats_compact = {k: v for k, v in stats.items()
                     if k not in ("chapter_stats",)}
    # 精简词频到 top 30
    if "vocabulary" in stats_compact:
        stats_compact["vocabulary"]["top_100_words"] = stats_compact["vocabulary"]["top_100_words"][:30]

    prompt = build_global_merge_prompt(
        json.dumps(stats_compact, ensure_ascii=False, indent=2),
        json.dumps(samples, ensure_ascii=False, indent=2),
        json.dumps(reader_paragraphs[:20], ensure_ascii=False, indent=2),
        str(style_dir),
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
        for name in STYLE_FILES:
            fpath = style_dir / f"{name}.md"
            if fpath.exists():
                generated.append(f"{name}.md ({fpath.stat().st_size} bytes)")
        print(f"  生成文件: {', '.join(generated) if generated else '无'}")

        progress["global_merge"]["status"] = "completed"
    else:
        print(f"失败 ({elapsed:.0f}s): {output[:200]}")

    save_progress(progress_path, progress, default_fn=_default_progress, merge_fn=_merge_progress)

    # 生成 index.md
    if progress["global_merge"]["status"] == "completed":
        build_index_md(book_dir)


# ============================================================
# index.md 生成
# ============================================================

def build_index_md(book_dir: Path):
    """Python 直接生成 style/index.md"""
    style_dir = get_style_dir(book_dir)
    build_dir = get_build_dir(book_dir)

    lines = ["# 风格概览\n"]

    # 从 stats.json 读统计
    stats_path = build_dir / "stats.json"
    if stats_path.exists():
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)

        # 从 sampling_plan.json 读抽样数
        plan_path = build_dir / "sampling_plan.json"
        sample_count = "?"
        if plan_path.exists():
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
            sample_count = plan.get("sample_size", "?")

        lines.append("## 统计\n")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 分析章数（全量统计） | {stats.get('total_chapters', '?')} |")
        lines.append(f"| 抽样精析章数 | {sample_count} |")
        total_chars = stats.get("total_chars", 0)
        lines.append(f"| 总字数 | {total_chars / 10000:.1f} 万 |")
        lines.append("")

    # 产出文件导航
    lines.append("## 文件导航\n")
    for name in STYLE_FILES:
        fpath = style_dir / f"{name}.md"
        label = STYLE_NAMES[name]
        if fpath.exists():
            size = fpath.stat().st_size
            lines.append(f"- [{label}]({name}.md)（{size} bytes）")
        else:
            lines.append(f"- {label}（未生成）")

    lines.append("")

    index_path = style_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成: {index_path}")


# ============================================================
# 验证
# ============================================================

def run_validate(book_dir: Path):
    """验证所有产出文件"""
    print("\n验证 T7 产出文件...")

    style_dir = book_dir / "style"
    build_dir = book_dir / "style" / ".build"

    results = []

    # 检查中间产物
    stats_path = build_dir / "stats.json"
    if stats_path.exists():
        size = stats_path.stat().st_size
        with open(stats_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        results.append(("统计数据", "OK",
                        f"{size / 1024:.1f} KB, "
                        f"{data.get('total_chapters', '?')} 章, "
                        f"{data.get('total_chars', 0) / 10000:.1f} 万字"))
    else:
        results.append(("统计数据", "缺失", ""))

    plan_path = build_dir / "sampling_plan.json"
    if plan_path.exists():
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        results.append(("抽样计划", "OK",
                        f"{plan.get('sample_size', '?')} 章"))
    else:
        results.append(("抽样计划", "缺失", ""))

    # 检查抽样 JSON
    batch_files = list(build_dir.glob("batch_*.json"))
    results.append(("抽样分析", "OK" if batch_files else "缺失",
                     f"{len(batch_files)} 个文件"))

    # 检查 3 个 style 文件
    for name in STYLE_FILES:
        fpath = style_dir / f"{name}.md"
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            sections = len(re.findall(r"^## .+", content, re.MULTILINE))
            has_guidance = "写作指导" in content or "写作指南" in content or "写作建议" in content
            results.append((STYLE_NAMES[name], "OK",
                            f"{fpath.stat().st_size} bytes, "
                            f"{sections} 段, "
                            f"{'含' if has_guidance else '缺'}写作指导"))
        else:
            results.append((STYLE_NAMES[name], "缺失", ""))

    # 检查 index.md
    index_path = style_dir / "index.md"
    if index_path.exists():
        results.append(("风格概览", "OK",
                        f"{index_path.stat().st_size} bytes"))
    else:
        results.append(("风格概览", "缺失", ""))

    # 输出结果
    print(f"\n{'文件':<16} {'状态':<6} {'详情'}")
    print("-" * 60)
    for name, status, detail in results:
        print(f"{name:<16} {status:<6} {detail}")

    # 加载进度统计
    progress_path = get_progress_path(book_dir)
    if progress_path.exists():
        progress = load_progress(progress_path, default_fn=_default_progress)
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
    text_dir = get_text_dir(book_dir)
    progress_path = get_progress_path(book_dir)

    # 硬依赖检查（T3、T6 在 get_outline_dir / get_emotions_path 中检查）
    get_outline_dir(book_dir)
    get_emotions_path(book_dir)

    # 扫描正文文件
    all_chapters = list_chapter_files(text_dir)
    if not all_chapters:
        print(f"错误：text 目录中没有正文文件: {text_dir}")
        sys.exit(1)

    print(f"知识库: {book_dir}")
    print(f"正文文件: {len(all_chapters)} 章 "
          f"(ch{all_chapters[0]:04d} ~ ch{all_chapters[-1]:04d})")

    # 验证模式
    if args.validate:
        run_validate(book_dir)
        return

    # 加载进度
    progress = load_progress(progress_path, default_fn=_default_progress)
    print(f"当前阶段: {progress['phase']}")

    # 确保输出目录存在
    get_style_dir(book_dir)
    get_build_dir(book_dir)

    if args.dry_run:
        print("\n=== 试运行模式 ===")

    # 阶段 0: 预处理
    if args.phase == "preprocess" or (
        not args.phase and progress["phase"] == "preprocess"
    ):
        phase_preprocess(book_dir, all_chapters, progress, progress_path,
                         args.dry_run, args.sample_size)

    # 阶段 1: 抽样分析
    if args.phase == "sample" or (
        not args.phase and progress["phase"] in ("sample_analyze", "preprocess")
    ):
        progress = load_progress(progress_path, default_fn=_default_progress)
        if progress["phase"] == "sample_analyze" or args.phase == "sample":
            phase_sample_analyze(
                book_dir, all_chapters, progress, progress_path,
                args.model, args.timeout, args.dry_run,
                args.chapters_per_call, args.concurrency
            )

    # 阶段 2: 全局融合
    if args.phase == "merge" or (
        not args.phase and progress["phase"] in ("global_merge", "sample_analyze")
    ):
        progress = load_progress(progress_path, default_fn=_default_progress)
        if progress["phase"] == "global_merge" or args.phase == "merge":
            phase_global_merge(book_dir, progress, progress_path,
                               args.model, args.timeout, args.dry_run)

    # 最终统计
    progress = load_progress(progress_path, default_fn=_default_progress)
    print(f"\n{'=' * 60}")
    print("当前状态:")
    print(f"  阶段: {progress['phase']}")
    print(f"  预处理: {progress['preprocess']['status']}")
    sa = progress["sample_analyze"]
    print(f"  抽样分析: {sa['status']} ({len(sa['batches_completed'])} 完成/"
          f"{len(sa['batches_failed'])} 失败)")
    print(f"  全局融合: {progress['global_merge']['status']}")
    print(f"  总调用: {progress['stats']['total_calls']} 次")
    total_s = progress["stats"]["total_time_seconds"]
    print(f"  总耗时: {total_s // 3600}h{(total_s % 3600) // 60}m{total_s % 60}s")

    # 检查是否全部完成
    all_done = (
        progress["preprocess"]["status"] == "completed"
        and progress["sample_analyze"]["status"] == "completed"
        and progress["global_merge"]["status"] == "completed"
    )
    if all_done:
        print("\nT7 全流程完成！建议运行验证:")
        print(f"  python {__file__} --book-dir {args.book_dir} --validate")


if __name__ == "__main__":
    main()
