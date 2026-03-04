#!/usr/bin/env python3
"""
草稿验证脚本 — L1 定量检测 + 反哺 + 验证历史

Layer 1：纯 Python 定量检测（12 项指标 vs stats.json 基线），<5s，0 次 AI 调用
L2/L3 深度分析已迁移到 Reviewer subagent（templates/reviewer_agent.md）

用法：
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --layer 1
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --chapter 4 --layer 1
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --promote --chapter 902
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --history
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# ============================================================
# 复用 batch_style.py 的分析函数和正则
# ============================================================

# 将 batch_style.py 所在目录加入 sys.path
# novel-kb skill 与 novel-write skill 是同级目录
_NOVEL_KB_DIR = Path(__file__).parent.parent.parent / "novel-kb"
sys.path.insert(0, str(_NOVEL_KB_DIR))

from batch_style import (
    COMMENT_COUNT_RE,
    DIALOGUE_LINE_RE,
    DIALOGUE_TAG_RE,
    DIRECT_EMOTION_RE,
    INDIRECT_EMOTION_RE,
    MODERN_TIME_RE,
    SENTENCE_SPLIT_RE,
    TRADITIONAL_TIME_RE,
    analyze_single_chapter,
    classify_sentence,
    count_dialogues,
    load_chapter_text,
    run_claude_prompt,
    split_sentences,
)

# Skill 目录
SKILL_DIR = Path(__file__).parent
PROMPTS_DIR = SKILL_DIR / "prompts"

# ============================================================
# 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description="草稿验证（L1 定量检测）")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--chapter", type=int, help="指定验证章节号")
    parser.add_argument("--layer", type=int, choices=[1],
                        help="验证层级（仅支持 1=定量检测）")
    parser.add_argument("--promote", action="store_true",
                        help="反哺：将验证通过的草稿纳入正式知识库")
    parser.add_argument("--history", action="store_true",
                        help="显示验证历史")
    parser.add_argument("--model", default="sonnet",
                        help="Claude 模型（默认 sonnet，仅 --promote 使用）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="单次 Claude 调用超时秒数（默认 300，仅 --promote 使用）")
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


def get_paths(book_dir: Path) -> dict:
    """获取所有相关路径"""
    text_dir = book_dir / "text"
    drafts_dir = book_dir / "drafts"
    style_dir = book_dir / "style"
    stats_path = style_dir / ".build" / "stats.json"

    if not drafts_dir.exists():
        print(f"错误：drafts 目录不存在: {drafts_dir}")
        print("请先运行 /novel-write 生成草稿")
        sys.exit(1)

    if not stats_path.exists():
        print(f"错误：stats.json 不存在: {stats_path}")
        print("请先运行 /novel-kb --stage t7")
        sys.exit(1)

    return {
        "book_dir": book_dir,
        "text_dir": text_dir,
        "drafts_dir": drafts_dir,
        "style_dir": style_dir,
        "stats_path": stats_path,
        "history_path": drafts_dir / ".verify_history.json",
    }


def list_draft_chapters(drafts_dir: Path) -> list[int]:
    """扫描 drafts/ 中的章节编号"""
    chapters = []
    for f in drafts_dir.iterdir():
        m = re.match(r"ch(\d+)\.md$", f.name)
        if m:
            chapters.append(int(m.group(1)))
    chapters.sort()
    return chapters


def _normalize_quotes(text: str) -> str:
    """将半角引号转换为全角引号（AI 续写可能使用半角引号）"""
    result = []
    in_quote = False
    for ch in text:
        if ch == '"':
            if not in_quote:
                result.append('\u201c')  # 左引号 "
                in_quote = True
            else:
                result.append('\u201d')  # 右引号 "
                in_quote = False
        else:
            result.append(ch)
    return ''.join(result)


def load_draft_text(drafts_dir: Path, ch_num: int) -> str:
    """加载草稿纯正文（去掉标题行，规范化引号）"""
    filepath = drafts_dir / f"ch{ch_num:04d}.md"
    if not filepath.exists():
        return ""
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()
    text_lines = []
    for line in lines:
        if line.startswith("# "):
            continue
        clean = COMMENT_COUNT_RE.sub("", line).rstrip()
        if clean:
            text_lines.append(clean)
    text = "\n".join(text_lines)
    # AI 续写可能使用半角引号，需统一为全角引号以匹配 DIALOGUE_LINE_RE
    return _normalize_quotes(text)


# ============================================================
# Layer 1：定量检测（纯 Python，12 项指标）
# ============================================================

# 指标阈值配置
METRICS_CONFIG = {
    "M1_avg_sentence_len": {
        "name": "平均句长", "weight": 10,
        "pass_range": (30, 52), "warn_range": (25, 58),
    },
    "M2_dialogue_ratio": {
        "name": "对话比例", "weight": 10,
        "pass_range": (0.25, 0.58), "warn_range": (0.20, 0.65),
    },
    "M3_dao_tag_ratio": {
        "name": "\"道\"系标签占比", "weight": 8,
        "pass_min": 0.45, "warn_min": 0.30,
    },
    "M4_opening_type": {
        "name": "章首类型", "weight": 10,
        "pass_types": ["action", "environment"],
        "warn_types": ["thought"],
        "fail_types": ["dialogue"],
    },
    "M5_closing_type": {
        "name": "章尾类型", "weight": 10,
        "pass_types": ["action", "environment"],
        "warn_types": ["thought"],
        "fail_types": ["dialogue"],
    },
    "M6_direct_emotion": {
        "name": "直白情感词", "weight": 12,
        "pass_max": 0, "warn_max": 2,
    },
    "M7_modern_time": {
        "name": "现代时间", "weight": 10,
        "pass_max": 0,
    },
    "M8_traditional_time": {
        "name": "传统时辰", "weight": 5,
    },
    "M9_char_count": {
        "name": "总字数", "weight": 5,
        "pass_range": (3000, 5000), "warn_range": (2500, 6000),
    },
    "M10_avg_para_len": {
        "name": "段落平均长度", "weight": 5,
        "pass_range": (25, 55), "warn_range": (20, 65),
    },
    "M11_short_sentence_ratio": {
        "name": "短句率", "weight": 5,
        "pass_range": (0.12, 0.32), "warn_range": (0.08, 0.38),
    },
    "M12_emotion_ratio": {
        "name": "间接/直白情感比", "weight": 10,
        "pass_min_ratio": 3.0, "warn_min_ratio": 1.0,
    },
}


def evaluate_metric(metric_key: str, value, analysis: dict) -> tuple[str, float]:
    """评估单项指标，返回 (PASS/WARN/FAIL, 得分百分比)"""
    config = METRICS_CONFIG[metric_key]
    weight = config["weight"]

    if metric_key in ("M1_avg_sentence_len", "M2_dialogue_ratio",
                      "M9_char_count", "M10_avg_para_len",
                      "M11_short_sentence_ratio"):
        lo_p, hi_p = config["pass_range"]
        lo_w, hi_w = config["warn_range"]
        if lo_p <= value <= hi_p:
            return "PASS", weight
        elif lo_w <= value <= hi_w:
            return "WARN", weight * 0.6
        else:
            return "FAIL", 0

    elif metric_key == "M3_dao_tag_ratio":
        if value >= config["pass_min"]:
            return "PASS", weight
        elif value >= config["warn_min"]:
            return "WARN", weight * 0.6
        else:
            return "FAIL", 0

    elif metric_key in ("M4_opening_type", "M5_closing_type"):
        if value in config["pass_types"]:
            return "PASS", weight
        elif value in config["warn_types"]:
            return "WARN", weight * 0.6
        else:
            return "FAIL", 0

    elif metric_key == "M6_direct_emotion":
        if value <= config["pass_max"]:
            return "PASS", weight
        elif value <= config["warn_max"]:
            return "WARN", weight * 0.6
        else:
            return "FAIL", 0

    elif metric_key == "M7_modern_time":
        if value <= config["pass_max"]:
            return "PASS", weight
        else:
            return "FAIL", 0

    elif metric_key == "M8_traditional_time":
        modern = analysis.get("modern_time_count", 0)
        if modern > 0 and value == 0:
            return "FAIL", 0
        else:
            return "PASS", weight

    elif metric_key == "M12_emotion_ratio":
        direct = analysis.get("direct_emotion_count", 0)
        indirect = analysis.get("indirect_emotion_count", 0)
        if direct == 0:
            return "PASS", weight
        ratio = indirect / max(direct, 1)
        if ratio >= config["pass_min_ratio"]:
            return "PASS", weight
        elif ratio >= config["warn_min_ratio"]:
            return "WARN", weight * 0.6
        else:
            return "FAIL", 0

    return "PASS", weight


def compute_dao_ratio(analysis: dict) -> float:
    """计算道系标签占比"""
    tags = analysis.get("dialogue_tag_counts", {})
    if not tags:
        return 1.0  # 无对话时默认通过
    total = sum(tags.values())
    if total == 0:
        return 1.0
    dao_count = sum(v for k, v in tags.items() if "道" in k)
    return dao_count / total


def get_dominant_type(types: list[str]) -> str:
    """从前几句的类型列表中取主导类型"""
    if not types:
        return "action"
    counter = Counter(types)
    return counter.most_common(1)[0][0]


def run_layer1(draft_text: str, original_text: str | None,
               stats: dict, ch_num: int) -> dict:
    """运行 Layer 1 定量检测"""
    print("\n" + "=" * 60)
    print(f"  Layer 1：定量检测 — ch{ch_num:04d}")
    print("=" * 60)

    draft_analysis = analyze_single_chapter(draft_text)
    if not draft_analysis:
        print("错误：草稿文本为空或分析失败")
        return {"score": 0, "metrics": {}, "grade": "FAIL"}

    original_analysis = None
    if original_text:
        original_analysis = analyze_single_chapter(original_text)

    # 计算各项指标值
    dao_ratio = compute_dao_ratio(draft_analysis)
    opening_type = get_dominant_type(draft_analysis.get("opening_types", []))
    closing_type = get_dominant_type(draft_analysis.get("closing_types", []))

    metric_values = {
        "M1_avg_sentence_len": draft_analysis.get("sentence_avg_len", 0),
        "M2_dialogue_ratio": draft_analysis.get("dialogue_ratio", 0),
        "M3_dao_tag_ratio": dao_ratio,
        "M4_opening_type": opening_type,
        "M5_closing_type": closing_type,
        "M6_direct_emotion": draft_analysis.get("direct_emotion_count", 0),
        "M7_modern_time": draft_analysis.get("modern_time_count", 0),
        "M8_traditional_time": draft_analysis.get("traditional_time_count", 0),
        "M9_char_count": draft_analysis.get("char_count", 0),
        "M10_avg_para_len": draft_analysis.get("paragraph_avg_len", 0),
        "M11_short_sentence_ratio": draft_analysis.get("sentence_short_ratio", 0),
        "M12_emotion_ratio": None,  # 特殊处理
    }

    # 基准值
    baseline_values = {
        "M1_avg_sentence_len": stats.get("sentence", {}).get("avg_length", 40.8),
        "M2_dialogue_ratio": stats.get("dialogue", {}).get("ratio", 0.415),
        "M3_dao_tag_ratio": 0.84,
        "M4_opening_type": "action (68%)",
        "M5_closing_type": "action (82%)",
        "M6_direct_emotion": f"{stats.get('vocabulary', {}).get('time_expressions', {}).get('traditional', 39) / max(stats.get('total_chapters', 901), 1):.3f}/章",
        "M7_modern_time": f"{27 / max(stats.get('total_chapters', 901), 1):.3f}/章",
        "M8_traditional_time": f"{39 / max(stats.get('total_chapters', 901), 1):.3f}/章",
        "M9_char_count": f"{stats.get('total_chars', 0) / max(stats.get('total_chapters', 901), 1):.0f}/章",
        "M10_avg_para_len": stats.get("paragraph", {}).get("avg_length", 36.7),
        "M11_short_sentence_ratio": stats.get("sentence", {}).get("distribution", {}).get("short", 0.206),
        "M12_emotion_ratio": "8.9:1",
    }

    # 评估各项
    results = {}
    total_score = 0
    max_score = 0

    for key, config in METRICS_CONFIG.items():
        value = metric_values[key]
        status, score = evaluate_metric(key, value, draft_analysis)
        results[key] = {
            "name": config["name"],
            "value": value,
            "status": status,
            "score": score,
            "weight": config["weight"],
            "baseline": baseline_values.get(key, "—"),
        }
        if original_analysis and key not in ("M4_opening_type", "M5_closing_type",
                                              "M8_traditional_time", "M12_emotion_ratio"):
            orig_val = {
                "M1_avg_sentence_len": original_analysis.get("sentence_avg_len", 0),
                "M2_dialogue_ratio": original_analysis.get("dialogue_ratio", 0),
                "M3_dao_tag_ratio": compute_dao_ratio(original_analysis),
                "M6_direct_emotion": original_analysis.get("direct_emotion_count", 0),
                "M7_modern_time": original_analysis.get("modern_time_count", 0),
                "M9_char_count": original_analysis.get("char_count", 0),
                "M10_avg_para_len": original_analysis.get("paragraph_avg_len", 0),
                "M11_short_sentence_ratio": original_analysis.get("sentence_short_ratio", 0),
            }.get(key)
            if orig_val is not None:
                results[key]["original"] = orig_val
        elif original_analysis and key == "M4_opening_type":
            results[key]["original"] = get_dominant_type(
                original_analysis.get("opening_types", []))
        elif original_analysis and key == "M5_closing_type":
            results[key]["original"] = get_dominant_type(
                original_analysis.get("closing_types", []))

        total_score += score
        max_score += config["weight"]

    l1_score = (total_score / max_score * 100) if max_score > 0 else 0

    # 打印结果表格
    print(f"\n{'指标':<20} {'草稿值':<14} {'原文值':<14} {'基准线':<14} {'状态':<6} {'得分'}")
    print("-" * 82)
    for key, r in results.items():
        draft_val = _fmt(r["value"])
        orig_val = _fmt(r.get("original", "—"))
        baseline = _fmt(r["baseline"])
        status_mark = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}[r["status"]]
        print(f"{r['name']:<18} {draft_val:<14} {orig_val:<14} {baseline:<14} {status_mark:<6} {r['score']:.1f}/{r['weight']}")

    print(f"\nLayer 1 得分：{l1_score:.1f}/100")

    # 生成改进建议
    suggestions = []
    for key, r in results.items():
        if r["status"] in ("WARN", "FAIL"):
            suggestions.append(_gen_suggestion(key, r))

    return {
        "score": round(l1_score, 1),
        "metrics": results,
        "suggestions": suggestions,
        "draft_analysis": draft_analysis,
        "original_analysis": original_analysis,
    }


def _fmt(val) -> str:
    """格式化显示值"""
    if val is None or val == "—":
        return "—"
    if isinstance(val, float):
        if val < 1:
            return f"{val:.3f}"
        return f"{val:.1f}"
    return str(val)


def _gen_suggestion(metric_key: str, result: dict) -> str:
    """为 WARN/FAIL 指标生成改进建议"""
    name = result["name"]
    value = result["value"]
    status = result["status"]

    suggestions_map = {
        "M1_avg_sentence_len": f"[{status}] {name}={_fmt(value)}：建议调整句子长度到 30-52 字范围",
        "M2_dialogue_ratio": f"[{status}] {name}={_fmt(value)}：对话比例偏{'高' if value > 0.58 else '低'}，建议调整到 25-58%",
        "M3_dao_tag_ratio": f"[{status}] {name}={_fmt(value)}：对话标签应以\"道\"系为主（>45%），当前偏低",
        "M4_opening_type": f"[{status}] {name}={value}：章首应以动作或环境开篇，避免{value}开篇",
        "M5_closing_type": f"[{status}] {name}={value}：章尾应以动作或环境收尾，避免{value}收尾",
        "M6_direct_emotion": f"[{status}] {name}={value}个：禁止使用直白情感词，用动作/细节/生理反应替代",
        "M7_modern_time": f"[{status}] {name}={value}个：禁止使用现代时间表达，改用传统时辰（寅时、卯时等）",
        "M8_traditional_time": f"[{status}] {name}：使用了现代时间但无传统时辰",
        "M9_char_count": f"[{status}] {name}={value}：建议控制在 3000-5000 字",
        "M10_avg_para_len": f"[{status}] {name}={_fmt(value)}：段落长度偏{'长' if value > 55 else '短'}，建议 25-55 字",
        "M11_short_sentence_ratio": f"[{status}] {name}={_fmt(value)}：短句率偏{'高' if value > 0.32 else '低'}，建议 12-32%",
        "M12_emotion_ratio": f"[{status}] {name}：直白情感过多，间接/直白比应 > 3:1",
    }
    return suggestions_map.get(metric_key, f"[{status}] {name}：指标异常")


# ============================================================
# 报告生成（仅 L1）
# ============================================================

def generate_report(ch_num: int, l1_result: dict, drafts_dir: Path) -> str:
    """生成 L1 验证报告"""
    l1_score = l1_result["score"]
    grade = "A" if l1_score >= 90 else "B" if l1_score >= 80 else "C" if l1_score >= 70 else "D"

    lines = [
        f"# ch{ch_num:04d} L1 验证报告",
        "",
        f"**验证时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**L1 得分**：{l1_score}/100",
        "",
        "> L2/L3 深度分析由 Reviewer subagent 独立完成，结果将合并到最终评级。",
        "",
    ]

    # Layer 1 结果
    lines.append("## Layer 1：定量检测")
    lines.append(f"\n得分：{l1_score}/100\n")
    lines.append("| 指标 | 草稿值 | 原文值 | 基准线 | 状态 | 得分 |")
    lines.append("|------|--------|--------|--------|------|------|")
    for key, r in l1_result["metrics"].items():
        draft_val = _fmt(r["value"])
        orig_val = _fmt(r.get("original", "—"))
        baseline = _fmt(r["baseline"])
        lines.append(f"| {r['name']} | {draft_val} | {orig_val} | {baseline} | {r['status']} | {r['score']:.1f}/{r['weight']} |")

    if l1_result.get("suggestions"):
        lines.append("\n### 改进建议\n")
        for s in l1_result["suggestions"]:
            lines.append(f"- {s}")

    # FAIL/WARN 汇总
    fail_count = sum(1 for r in l1_result["metrics"].values() if r["status"] == "FAIL")
    warn_count = sum(1 for r in l1_result["metrics"].values() if r["status"] == "WARN")
    pass_count = sum(1 for r in l1_result["metrics"].values() if r["status"] == "PASS")
    lines.append(f"\n**汇总**：PASS={pass_count} WARN={warn_count} FAIL={fail_count}")

    report_text = "\n".join(lines)

    # 保存报告
    report_path = drafts_dir / f"ch{ch_num:04d}_l1_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\nL1 报告已保存: {report_path}")

    return report_text


# ============================================================
# 验证历史
# ============================================================

def load_history(history_path: Path) -> dict:
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"records": [], "recurring_issues": {}}


def save_history(history_path: Path, history: dict):
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_history(history_path: Path, ch_num: int, l1_score: float,
                   l1_result: dict):
    """更新验证历史"""
    history = load_history(history_path)

    record = {
        "chapter": ch_num,
        "timestamp": datetime.now().isoformat(),
        "l1_score": l1_score,
        "fail_metrics": [
            k for k, v in l1_result["metrics"].items() if v["status"] == "FAIL"
        ],
        "warn_metrics": [
            k for k, v in l1_result["metrics"].items() if v["status"] == "WARN"
        ],
    }
    history["records"].append(record)

    # 追踪 recurring issues
    for metric in record["fail_metrics"] + record["warn_metrics"]:
        history["recurring_issues"][metric] = \
            history["recurring_issues"].get(metric, 0) + 1

    save_history(history_path, history)
    print(f"验证历史已更新: {history_path}")


def show_history(history_path: Path):
    """显示验证历史"""
    history = load_history(history_path)
    records = history.get("records", [])

    if not records:
        print("暂无验证记录。")
        return

    print(f"\n{'章节':<10} {'时间':<22} {'L1分数':<10} {'FAIL 项'}")
    print("-" * 70)
    for r in records:
        ch = f"ch{r['chapter']:04d}"
        ts = r["timestamp"][:19]
        # 兼容旧格式（score 字段）和新格式（l1_score 字段）
        score = r.get("l1_score", r.get("score", 0))
        score_str = f"{score:.1f}"
        fails = ", ".join(r.get("fail_metrics", [])) or "—"
        print(f"{ch:<10} {ts:<22} {score_str:<10} {fails}")

    recurring = history.get("recurring_issues", {})
    if recurring:
        print("\n反复出现的问题：")
        for metric, count in sorted(recurring.items(), key=lambda x: -x[1]):
            if count >= 2:
                print(f"  {metric}: {count} 次")


# ============================================================
# 反哺流程
# ============================================================

def promote_chapter(book_dir: Path, ch_num: int, model: str, timeout: int):
    """将验证通过的草稿反哺到正式知识库"""
    paths = get_paths(book_dir)
    drafts_dir = paths["drafts_dir"]
    text_dir = paths["text_dir"]

    draft_path = drafts_dir / f"ch{ch_num:04d}.md"
    target_path = text_dir / f"ch{ch_num:04d}.md"

    if not draft_path.exists():
        print(f"错误：草稿不存在: {draft_path}")
        sys.exit(1)

    if target_path.exists():
        print(f"错误：text/{target_path.name} 已存在，不覆盖原文。")
        print("反哺仅对新章节号（text/ 中不存在的）执行。")
        sys.exit(1)

    # 检查验证报告是否存在（兼容新旧格式）
    report_path = drafts_dir / f"ch{ch_num:04d}_verification.md"
    l1_report_path = drafts_dir / f"ch{ch_num:04d}_l1_report.md"
    found_report = None
    for rp in [report_path, l1_report_path]:
        if rp.exists():
            found_report = rp
            break

    if not found_report:
        print(f"错误：验证报告不存在")
        print(f"请先运行验证：python batch_verify.py --book-dir ... --chapter {ch_num}")
        sys.exit(1)

    # 检查评级（从 verification.md 读取；L1 报告不含综合评级，跳过检查）
    if found_report == report_path:
        report_text = report_path.read_text(encoding="utf-8")
        grade_match = re.search(r"评级\s+([ABCD])", report_text)
        if grade_match:
            grade = grade_match.group(1)
            if grade in ("C", "D"):
                print(f"错误：评级 {grade}，不建议反哺。请修改后重新验证。")
                sys.exit(1)

    # 1. 复制正文到 text/
    print(f"正文 → {target_path}")
    draft_content = draft_path.read_text(encoding="utf-8")
    target_path.write_text(draft_content, encoding="utf-8")

    # 2. 生成章节摘要（调用 Claude）
    plot_chapters_dir = book_dir / "plot" / "chapters"
    summary_path = plot_chapters_dir / f"ch{ch_num:04d}.md"
    if not summary_path.exists() and plot_chapters_dir.exists():
        print(f"生成章节摘要 → {summary_path}")
        prompt = f"""请为以下章节生成结构化摘要。

章节内容：
{draft_content}

输出格式（纯 markdown，不要 JSON）：

# ch{ch_num:04d} 摘要

## 关键事件
- 事件1
- 事件2

## 出场角色
- 角色1：做了什么
- 角色2：做了什么

## 一句话摘要
一句话概括本章内容。
"""
        ok, output = run_claude_prompt(prompt, model, timeout)
        if ok:
            summary_path.write_text(output, encoding="utf-8")
            print("  章节摘要已生成")
        else:
            print(f"  章节摘要生成失败: {output[:200]}")

    # 3. 更新 index.md
    index_path = plot_chapters_dir / "index.md"
    if index_path.exists():
        index_content = index_path.read_text(encoding="utf-8")
        entry = f"\n| ch{ch_num:04d} | [AI 续写] | [摘要](ch{ch_num:04d}.md) |"
        if f"ch{ch_num:04d}" not in index_content:
            with open(index_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
            print(f"索引已更新: {index_path}")

    print(f"\n反哺完成！ch{ch_num:04d} 已纳入正式知识库。")


# ============================================================
# 主流程
# ============================================================

def verify_chapter(ch_num: int, paths: dict) -> dict:
    """验证单个章节（仅 L1 定量检测）"""
    drafts_dir = paths["drafts_dir"]
    text_dir = paths["text_dir"]
    stats_path = paths["stats_path"]

    # 加载草稿
    draft_text = load_draft_text(drafts_dir, ch_num)
    if not draft_text:
        print(f"错误：草稿 ch{ch_num:04d}.md 为空或不存在")
        sys.exit(1)

    # 加载原文（如果存在，用于 L1 对比显示）
    original_text = load_chapter_text(text_dir, ch_num) if text_dir.exists() else None
    if original_text:
        print(f"原文已加载: text/ch{ch_num:04d}.md")
    else:
        print(f"无原文 text/ch{ch_num:04d}.md")

    # 加载 stats.json
    with open(stats_path, "r", encoding="utf-8") as f:
        stats = json.load(f)

    # Layer 1：定量检测
    l1_result = run_layer1(draft_text, original_text, stats, ch_num)

    # 生成 L1 报告
    generate_report(ch_num, l1_result, drafts_dir)

    # 更新历史
    update_history(paths["history_path"], ch_num, l1_result["score"], l1_result)

    return l1_result


def main():
    args = parse_args()
    book_dir = resolve_book_dir(args.book_dir)
    paths = get_paths(book_dir)

    # 查看历史
    if args.history:
        show_history(paths["history_path"])
        return

    # 反哺
    if args.promote:
        if not args.chapter:
            print("错误：--promote 需要指定 --chapter")
            sys.exit(1)
        promote_chapter(book_dir, args.chapter, args.model, args.timeout)
        return

    # 确定要验证的章节
    if args.chapter:
        chapters = [args.chapter]
    else:
        chapters = list_draft_chapters(paths["drafts_dir"])

    if not chapters:
        print("错误：drafts/ 中没有找到草稿文件（chNNNN.md）")
        sys.exit(1)

    print(f"知识库: {book_dir}")
    print(f"待验证章节: {', '.join(f'ch{c:04d}' for c in chapters)}")
    print(f"验证层级: Layer 1（定量检测）")
    print(f"L2/L3 深度分析请通过 Reviewer subagent 执行")

    start_time = time.time()

    for ch_num in chapters:
        print(f"\n{'#' * 60}")
        print(f"  验证 ch{ch_num:04d}")
        print(f"{'#' * 60}")
        verify_chapter(ch_num, paths)

    elapsed = time.time() - start_time
    print(f"\n总耗时: {elapsed:.1f}s")
    print("完成。")


if __name__ == "__main__":
    main()
