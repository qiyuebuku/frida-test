#!/usr/bin/env python3
"""
草稿验证脚本 — T9 验证测试

三层验证：Layer 1 定量检测 → Layer 2 AI 深度分析 → Layer 3 跨章一致性
产出 verification_report.md + 综合评分（A/B/C/D）

用法：
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir qidian/novel_kb/书名
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --layer 1
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --chapter 4
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --promote --chapter 902
    python .claude/skills/novel-write/verify/batch_verify.py --book-dir ... --history
"""

import argparse
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# ============================================================
# 复用 batch_style.py 的分析函数和正则
# ============================================================

# 将 batch_style.py 所在目录加入 sys.path
# 从 novel-write/verify/ 向上到 skills/，再进入 kb-style-analyze/
_STYLE_SKILL_DIR = Path(__file__).parent.parent.parent / "kb-style-analyze"
sys.path.insert(0, str(_STYLE_SKILL_DIR))

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
    extract_json_from_output,
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
    parser = argparse.ArgumentParser(description="草稿验证（T9）")
    parser.add_argument("--book-dir", required=True, help="知识库目录路径")
    parser.add_argument("--chapter", type=int, help="指定验证章节号")
    parser.add_argument("--layer", type=int, choices=[1, 2, 3],
                        help="只运行特定层（1=定量, 2=AI分析, 3=跨章）")
    parser.add_argument("--promote", action="store_true",
                        help="反哺：将验证通过的草稿纳入正式知识库")
    parser.add_argument("--history", action="store_true",
                        help="显示验证历史")
    parser.add_argument("--model", default="sonnet",
                        help="Claude 模型（默认 sonnet）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="单次 Claude 调用超时秒数（默认 300）")
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
        print("请先运行 T8（/novel-write）生成草稿")
        sys.exit(1)

    if not stats_path.exists():
        print(f"错误：stats.json 不存在: {stats_path}")
        print("请先运行 T7（kb-style-analyze）")
        sys.exit(1)

    return {
        "book_dir": book_dir,
        "text_dir": text_dir,
        "drafts_dir": drafts_dir,
        "style_dir": style_dir,
        "stats_path": stats_path,
        "history_path": drafts_dir / ".verify_history.json",
        "narrative_path": style_dir / "narrative.md",
        "vocabulary_path": style_dir / "vocabulary.md",
        "guide_path": book_dir / "guide.md",
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
    # 配对替换：找到成对的半角双引号 "..." 转为 "\u201c...\u201d"
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
# Layer 2：AI 深度分析
# ============================================================

def run_layer2(draft_text: str, original_text: str | None,
               paths: dict, ch_num: int, model: str, timeout: int) -> dict:
    """运行 Layer 2 AI 深度分析"""
    print("\n" + "=" * 60)
    print(f"  Layer 2：AI 深度分析 — ch{ch_num:04d}")
    print("=" * 60)

    blind_result = None
    style_result = None

    # --- 盲测对比（仅当原文存在时）---
    if original_text:
        print("\n[2a] 盲测对比...")
        blind_result = _run_blind_compare(draft_text, original_text, model, timeout)
    else:
        print("\n[2a] 盲测对比：跳过（无原文 text/chNNNN.md）")

    # --- 深度风格分析 ---
    print("\n[2b] 深度风格分析...")
    style_result = _run_style_analysis(draft_text, paths, model, timeout)

    # 计算 Layer 2 得分
    l2_score = _compute_l2_score(blind_result, style_result)
    print(f"\nLayer 2 得分：{l2_score:.1f}/100")

    return {
        "score": round(l2_score, 1),
        "blind_compare": blind_result,
        "style_analysis": style_result,
    }


def _run_blind_compare(draft_text: str, original_text: str,
                       model: str, timeout: int) -> dict | None:
    """盲测对比：draft + original 匿名标记 A/B"""
    prompt_template = (PROMPTS_DIR / "blind_compare.md").read_text(encoding="utf-8")

    # 随机分配 A/B
    is_draft_a = random.choice([True, False])
    if is_draft_a:
        text_a, text_b = draft_text, original_text
        draft_label = "A"
    else:
        text_a, text_b = original_text, draft_text
        draft_label = "B"

    prompt = prompt_template.replace("{TEXT_A}", text_a).replace("{TEXT_B}", text_b)

    ok, output = run_claude_prompt(prompt, model, timeout)
    if not ok:
        print(f"  盲测调用失败: {output[:200]}")
        return None

    data = extract_json_from_output(output)
    if not data:
        print(f"  盲测 JSON 解析失败")
        print(f"  原始输出: {output[:500]}")
        return None

    # 检查 Claude 是否正确识别了 AI
    ai_guess = data.get("ai_is", "")
    correct = (ai_guess == draft_label)
    similarity = data.get("similarity_score", 50)

    print(f"  草稿标记为: {draft_label}")
    print(f"  Claude 判断 AI 是: {ai_guess} ({'正确' if correct else '错误'})")
    print(f"  相似度评分: {similarity}/100")
    print(f"  置信度: {data.get('confidence', '?')}")

    return {
        "draft_label": draft_label,
        "ai_guess": ai_guess,
        "correct_identification": correct,
        "similarity_score": similarity,
        "confidence": data.get("confidence", "unknown"),
        "reasoning": data.get("reasoning", ""),
        "analysis": data.get("analysis", {}),
    }


def _run_style_analysis(draft_text: str, paths: dict,
                        model: str, timeout: int) -> dict | None:
    """深度风格分析"""
    prompt_template = (PROMPTS_DIR / "style_deep_analysis.md").read_text(encoding="utf-8")

    # 加载风格基准（截取关键部分，控制 prompt 大小）
    narrative_excerpt = ""
    vocabulary_excerpt = ""
    book_rules = ""
    if paths["narrative_path"].exists():
        narrative_full = paths["narrative_path"].read_text(encoding="utf-8")
        narrative_excerpt = _extract_key_sections(narrative_full, max_chars=3000)
    if paths["vocabulary_path"].exists():
        vocabulary_full = paths["vocabulary_path"].read_text(encoding="utf-8")
        vocabulary_excerpt = _extract_key_sections(vocabulary_full, max_chars=2000)
    # 从 guide.md 提取书籍特定规则（特殊角色规则、续写铁则等）
    if paths["guide_path"].exists():
        guide_full = paths["guide_path"].read_text(encoding="utf-8")
        book_rules = _extract_book_rules(guide_full)

    prompt = (prompt_template
              .replace("{NARRATIVE_EXCERPT}", narrative_excerpt)
              .replace("{VOCABULARY_EXCERPT}", vocabulary_excerpt)
              .replace("{BOOK_RULES}", book_rules or "（无特殊规则）")
              .replace("{DRAFT_TEXT}", draft_text))

    ok, output = run_claude_prompt(prompt, model, timeout)
    if not ok:
        print(f"  风格分析调用失败: {output[:200]}")
        return None

    data = extract_json_from_output(output)
    if not data:
        print(f"  风格分析 JSON 解析失败")
        print(f"  原始输出: {output[:500]}")
        return None

    overall = data.get("overall", {})
    total_avg = overall.get("total_avg", 0)
    print(f"  情感表达: {overall.get('emotion_avg', 0):.1f}/5")
    print(f"  对话自然度: {overall.get('dialogue_avg', 0):.1f}/5")
    print(f"  动作描写: {overall.get('action_avg', 0):.1f}/5")
    print(f"  信息密度: {overall.get('density_avg', 0):.1f}/5")
    print(f"  节奏感: {overall.get('rhythm_avg', 0):.1f}/5")
    print(f"  综合平均: {total_avg:.2f}/5")

    strengths = data.get("strengths", [])
    weaknesses = data.get("weaknesses", [])
    if strengths:
        print(f"  优点: {', '.join(strengths[:3])}")
    if weaknesses:
        print(f"  不足: {', '.join(weaknesses[:3])}")

    return data


def _extract_key_sections(text: str, max_chars: int) -> str:
    """从 markdown 文件中截取关键部分"""
    if len(text) <= max_chars:
        return text
    # 保留前 max_chars 字符
    return text[:max_chars] + "\n\n[...截断...]"


def _extract_book_rules(guide_text: str) -> str:
    """从 guide.md 中提取书籍特定规则（续写铁则部分）"""
    rules_sections = []
    in_rules = False
    for line in guide_text.splitlines():
        # 匹配 "续写铁则" 相关的标题
        if re.match(r"^##\s+续写铁则", line):
            in_rules = True
            rules_sections.append(line)
            continue
        # 遇到下一个同级标题则停止
        if in_rules and re.match(r"^##\s+[^#]", line) and "铁则" not in line:
            break
        if in_rules:
            rules_sections.append(line)
    return "\n".join(rules_sections) if rules_sections else ""


def _compute_l2_score(blind_result: dict | None, style_result: dict | None) -> float:
    """计算 Layer 2 综合得分

    有盲测时：盲测 40% + 风格分析 60%
    无盲测时：风格分析独占 100%（不降权）
    """
    if style_result:
        overall = style_result.get("overall", {})
        total_avg = overall.get("total_avg", 0)
        style_score = total_avg * 20  # 1-5 分映射到 0-100
    else:
        style_score = 50.0  # 无数据时给中间分

    if blind_result:
        similarity = blind_result.get("similarity_score", 50)
        return similarity * 0.4 + style_score * 0.6
    else:
        # 无原文时，风格分析独占 100%
        return style_score


# ============================================================
# Layer 3：跨章一致性
# ============================================================

def run_layer3(drafts_dir: Path, text_dir: Path, draft_chapters: list[int],
               model: str, timeout: int) -> dict:
    """运行 Layer 3 跨章一致性检测"""
    print("\n" + "=" * 60)
    print("  Layer 3：跨章一致性")
    print("=" * 60)

    if len(draft_chapters) < 2:
        print("  跳过：drafts/ 中只有 1 个章节，需 2+ 章才触发跨章检测")
        return {"score": None, "skipped": True, "reason": "single_chapter"}

    # Python 自动检测
    print("\n[3a] 重复表达检测...")
    repetition = _detect_repetition(drafts_dir, draft_chapters)

    print(f"  检测到 {len(repetition)} 处重复表达")

    # 计算得分
    repetition_penalty = min(len(repetition) * 5, 30)  # 每处扣 5 分，最多扣 30
    l3_score = max(100 - repetition_penalty, 0)
    print(f"\nLayer 3 得分：{l3_score:.1f}/100")

    return {
        "score": round(l3_score, 1),
        "skipped": False,
        "repetition_issues": repetition,
    }


def _detect_repetition(drafts_dir: Path, chapters: list[int],
                       threshold: float = 0.7) -> list[dict]:
    """检测相邻章节间的重复表达"""
    issues = []
    texts = {}
    for ch in chapters:
        texts[ch] = load_draft_text(drafts_dir, ch)

    for i in range(len(chapters) - 1):
        ch_a, ch_b = chapters[i], chapters[i + 1]
        sents_a = split_sentences(texts[ch_a])
        sents_b = split_sentences(texts[ch_b])

        # 检测高度相似的句子对
        for sa in sents_a:
            if len(sa) < 10:
                continue
            for sb in sents_b:
                if len(sb) < 10:
                    continue
                sim = _simple_similarity(sa, sb)
                if sim >= threshold:
                    issues.append({
                        "chapter_pair": f"ch{ch_a:04d}-ch{ch_b:04d}",
                        "text_a": sa[:50],
                        "text_b": sb[:50],
                        "similarity": round(sim, 2),
                    })
    return issues


def _simple_similarity(a: str, b: str) -> float:
    """简单的字符级 Jaccard 相似度"""
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# ============================================================
# 综合评分
# ============================================================

def compute_final_score(l1_result: dict, l2_result: dict | None,
                        l3_result: dict | None) -> tuple[float, str]:
    """计算综合评分和评级"""
    l1_score = l1_result.get("score", 0)

    if l3_result and not l3_result.get("skipped", True):
        # 多章：L1 × 40% + L2 × 45% + L3 × 15%
        l2_score = l2_result.get("score", 50) if l2_result else 50
        l3_score = l3_result.get("score", 50)
        final = l1_score * 0.40 + l2_score * 0.45 + l3_score * 0.15
    else:
        # 单章：L1 × 45% + L2 × 55%
        l2_score = l2_result.get("score", 50) if l2_result else 50
        final = l1_score * 0.45 + l2_score * 0.55

    # 评级
    if final >= 90:
        grade = "A"
    elif final >= 80:
        grade = "B"
    elif final >= 70:
        grade = "C"
    else:
        grade = "D"

    return round(final, 1), grade


# ============================================================
# 报告生成
# ============================================================

def generate_report(ch_num: int, l1_result: dict, l2_result: dict | None,
                    l3_result: dict | None, final_score: float,
                    grade: str, drafts_dir: Path) -> str:
    """生成 verification_report.md"""
    lines = [
        f"# ch{ch_num:04d} 验证报告",
        "",
        f"**验证时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**综合评分**：{final_score}/100（评级 {grade}）",
        "",
    ]

    # Layer 1 结果
    lines.append("## Layer 1：定量检测")
    lines.append(f"\n得分：{l1_result['score']}/100\n")
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

    # Layer 2 结果
    if l2_result:
        lines.append(f"\n## Layer 2：AI 深度分析")
        lines.append(f"\n得分：{l2_result['score']}/100\n")

        if l2_result.get("blind_compare"):
            bc = l2_result["blind_compare"]
            lines.append("### 盲测对比\n")
            lines.append(f"- 相似度：{bc.get('similarity_score', '?')}/100")
            lines.append(f"- Claude 识别正确：{'是' if bc.get('correct_identification') else '否'}")
            lines.append(f"- 置信度：{bc.get('confidence', '?')}")
            lines.append(f"- 依据：{bc.get('reasoning', '无')}")

        if l2_result.get("style_analysis"):
            sa = l2_result["style_analysis"]
            overall = sa.get("overall", {})
            lines.append("\n### 深度风格分析\n")
            lines.append("| 维度 | 评分 |")
            lines.append("|------|------|")
            for dim, key in [("情感表达", "emotion_avg"), ("对话自然度", "dialogue_avg"),
                             ("动作描写", "action_avg"), ("信息密度", "density_avg"),
                             ("节奏感", "rhythm_avg")]:
                lines.append(f"| {dim} | {overall.get(key, 0):.1f}/5 |")
            lines.append(f"| **综合** | **{overall.get('total_avg', 0):.2f}/5** |")

            if sa.get("strengths"):
                lines.append(f"\n**优点**：{', '.join(sa['strengths'][:3])}")
            if sa.get("weaknesses"):
                lines.append(f"\n**不足**：{', '.join(sa['weaknesses'][:3])}")

            if sa.get("improvement_suggestions"):
                lines.append("\n### 具体改进建议\n")
                for sugg in sa["improvement_suggestions"][:5]:
                    lines.append(f"- **{sugg.get('location', '?')}**：{sugg.get('issue', '')} → {sugg.get('suggestion', '')}")

    # Layer 3 结果
    if l3_result and not l3_result.get("skipped", True):
        lines.append(f"\n## Layer 3：跨章一致性")
        lines.append(f"\n得分：{l3_result['score']}/100\n")
        if l3_result.get("repetition_issues"):
            lines.append("### 重复表达\n")
            for issue in l3_result["repetition_issues"][:5]:
                lines.append(f"- {issue['chapter_pair']}：\"{issue['text_a']}\" ↔ \"{issue['text_b']}\" (相似度 {issue['similarity']})")
    elif l3_result and l3_result.get("skipped"):
        lines.append("\n## Layer 3：跨章一致性\n")
        lines.append("跳过（单章验证，不触发跨章检测）\n")

    # 综合评价
    lines.append(f"\n## 综合评价")
    lines.append(f"\n**评级 {grade}**（{final_score}/100）")
    if grade in ("A", "B"):
        quality = "优秀" if grade == "A" else "良好"
        lines.append(f"\n草稿质量{quality}，可考虑反哺到正式知识库。")
    elif grade == "C":
        lines.append("\n草稿质量及格，建议重点改进上述 WARN/FAIL 项后重新验证。")
    else:
        lines.append("\n草稿质量不合格，建议全面重写。")

    report_text = "\n".join(lines)

    # 保存报告
    report_path = drafts_dir / f"ch{ch_num:04d}_verification.md"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\n报告已保存: {report_path}")

    # 评级 C/D 时生成 feedback 文件
    if grade in ("C", "D"):
        feedback_lines = [
            f"# ch{ch_num:04d} 修改反馈",
            "",
            f"评级 {grade}（{final_score}/100），需要修改后重新验证。",
            "",
            "## 必须修改的问题\n",
        ]
        for key, r in l1_result["metrics"].items():
            if r["status"] == "FAIL":
                feedback_lines.append(f"- **{r['name']}**：值={_fmt(r['value'])}，状态=FAIL")
        feedback_lines.append("\n## 建议改进\n")
        for s in l1_result.get("suggestions", []):
            feedback_lines.append(f"- {s}")
        if l2_result and l2_result.get("style_analysis", {}).get("improvement_suggestions"):
            feedback_lines.append("\n## AI 建议\n")
            for sugg in l2_result["style_analysis"]["improvement_suggestions"][:5]:
                feedback_lines.append(f"- {sugg.get('location', '?')}：{sugg.get('suggestion', '')}")

        feedback_path = drafts_dir / f"ch{ch_num:04d}_feedback.md"
        feedback_path.write_text("\n".join(feedback_lines), encoding="utf-8")
        print(f"反馈文件已保存: {feedback_path}")

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


def update_history(history_path: Path, ch_num: int, final_score: float,
                   grade: str, l1_result: dict):
    """更新验证历史"""
    history = load_history(history_path)

    record = {
        "chapter": ch_num,
        "timestamp": datetime.now().isoformat(),
        "score": final_score,
        "grade": grade,
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

    print(f"\n{'章节':<10} {'时间':<22} {'评分':<8} {'评级':<6} {'FAIL 项'}")
    print("-" * 70)
    for r in records:
        ch = f"ch{r['chapter']:04d}"
        ts = r["timestamp"][:19]
        score = f"{r['score']:.1f}"
        grade = r["grade"]
        fails = ", ".join(r.get("fail_metrics", [])) or "—"
        print(f"{ch:<10} {ts:<22} {score:<8} {grade:<6} {fails}")

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

    # 检查验证报告是否存在
    report_path = drafts_dir / f"ch{ch_num:04d}_verification.md"
    if not report_path.exists():
        print(f"错误：验证报告不存在: {report_path}")
        print("请先运行验证：python batch_verify.py --book-dir ... --chapter {ch_num}")
        sys.exit(1)

    # 检查评级
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

def verify_chapter(ch_num: int, paths: dict, layer: int | None,
                   model: str, timeout: int) -> tuple[float, str]:
    """验证单个章节"""
    drafts_dir = paths["drafts_dir"]
    text_dir = paths["text_dir"]
    stats_path = paths["stats_path"]

    # 加载草稿
    draft_text = load_draft_text(drafts_dir, ch_num)
    if not draft_text:
        print(f"错误：草稿 ch{ch_num:04d}.md 为空或不存在")
        sys.exit(1)

    # 加载原文（如果存在）
    original_text = load_chapter_text(text_dir, ch_num) if text_dir.exists() else None
    if original_text:
        print(f"原文已加载: text/ch{ch_num:04d}.md")
    else:
        print(f"无原文 text/ch{ch_num:04d}.md（跳过盲测对比）")

    # 加载 stats.json
    with open(stats_path, "r", encoding="utf-8") as f:
        stats = json.load(f)

    # Layer 1：定量检测（总是运行）
    l1_result = run_layer1(draft_text, original_text, stats, ch_num)

    if layer == 1:
        final_score = l1_result["score"]
        grade = "A" if final_score >= 90 else "B" if final_score >= 80 else "C" if final_score >= 70 else "D"
        generate_report(ch_num, l1_result, None, None, final_score, grade, drafts_dir)
        update_history(paths["history_path"], ch_num, final_score, grade, l1_result)
        return final_score, grade

    # Layer 2：AI 深度分析
    l2_result = None
    if layer is None or layer == 2:
        l2_result = run_layer2(draft_text, original_text, paths, ch_num, model, timeout)

    # Layer 3：跨章一致性
    l3_result = None
    if layer is None or layer == 3:
        draft_chapters = list_draft_chapters(drafts_dir)
        l3_result = run_layer3(drafts_dir, text_dir, draft_chapters, model, timeout)

    # 综合评分
    final_score, grade = compute_final_score(l1_result, l2_result, l3_result)

    print("\n" + "=" * 60)
    print(f"  综合评分：{final_score}/100（评级 {grade}）")
    print("=" * 60)

    # 生成报告
    generate_report(ch_num, l1_result, l2_result, l3_result,
                    final_score, grade, drafts_dir)

    # 更新历史
    update_history(paths["history_path"], ch_num, final_score, grade, l1_result)

    return final_score, grade


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
    print(f"验证层级: {'全部' if args.layer is None else f'Layer {args.layer}'}")

    start_time = time.time()

    for ch_num in chapters:
        print(f"\n{'#' * 60}")
        print(f"  验证 ch{ch_num:04d}")
        print(f"{'#' * 60}")
        final_score, grade = verify_chapter(
            ch_num, paths, args.layer, args.model, args.timeout)

    elapsed = time.time() - start_time
    print(f"\n总耗时: {elapsed:.1f}s")
    print("完成。")


if __name__ == "__main__":
    main()
