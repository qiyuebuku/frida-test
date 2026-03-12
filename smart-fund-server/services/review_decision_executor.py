#!/usr/bin/env python3
"""
基金决策复盘执行器

功能：
1. 读取待复盘决策
2. 获取决策日和T+1/T+2的净值
3. 按照复盘标准判定结果（correct/wrong/neutral/early/late）
4. 保存复盘结果到数据库
5. 提炼经验并保存或验证
"""

import sys
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from services.fund_db import (
    get_conn, update_review, save_lesson, update_lesson_confidence,
    revise_lesson, get_lessons, find_similar_lessons, mark_lesson_extracted
)

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "jettask",
    "user": "jettask",
    "password": "123456",
}

# ==================== 数据获取 ====================

def get_pending_reviews(days_back=7):
    """获取待复盘的决策记录"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT r.id, r.fund_code, d.fund_name, d.action, d.reason,
                       d.confidence, r.decision_date, d.id as decision_id,
                       r.nav_at_decision, r.nav_t1, r.nav_t2
                FROM ft_reviews r
                LEFT JOIN ft_decisions d ON r.decision_id = d.id
                WHERE r.outcome = 'pending'
                AND r.decision_date >= CURRENT_DATE - %s
                ORDER BY r.decision_date DESC
            """, (days_back,))
            return cur.fetchall()


def parse_nav_history(nav_string: str) -> Dict[str, float]:
    """解析净值历史字符串，返回 {date: nav}"""
    result = {}
    if not nav_string:
        return result
    lines = nav_string.split('|')
    for line in lines:
        parts = line.split(';')
        if len(parts) >= 3:
            try:
                date = parts[0]
                net = float(parts[2])
                result[date] = net
            except (ValueError, IndexError):
                continue
    return result


def load_nav_data() -> Dict[str, Dict[str, float]]:
    """从 ft_nav_data.json 加载净值历史"""
    nav_by_code = {}

    try:
        with open('/tmp/ft_nav_data.json', 'r') as f:
            nav_data_raw = json.load(f)

        for code, fund_data in nav_data_raw.items():
            if 'nav' in fund_data and fund_data['nav'].get('data'):
                nav_by_code[code] = parse_nav_history(fund_data['nav']['data'])
    except Exception as e:
        print(f"警告: 无法加载净值数据 - {e}")

    return nav_by_code


def get_nav_for_date(nav_history: Dict[str, float], date_str: str) -> Optional[float]:
    """
    获取指定日期的净值（格式: YYYY-MM-DD或YYYYMMDD）
    如果指定日期无数据，返回最接近的前一个交易日净值
    """
    # 统一日期格式
    if '-' in date_str:
        date_key = date_str.replace('-', '')
    else:
        date_key = date_str

    # 精确匹配
    if date_key in nav_history:
        return nav_history[date_key]

    # 查找最接近的前一个日期
    available_dates = sorted(nav_history.keys(), reverse=True)
    for available_date in available_dates:
        if available_date <= date_key:
            return nav_history[available_date]

    return None


def get_next_trading_day_nav(nav_history: Dict[str, float], date_str: str) -> Optional[float]:
    """获取指定日期之后的第一个交易日的净值"""
    if '-' in date_str:
        date_key = date_str.replace('-', '')
    else:
        date_key = date_str

    available_dates = sorted(nav_history.keys(), reverse=True)
    for i, available_date in enumerate(available_dates):
        if available_date == date_key and i > 0:
            # 返回下一个交易日（历史中更早的日期）
            return nav_history[available_dates[i-1]]

    return None


# ==================== 决策结果判定 ====================

def judge_decision_outcome(
    action: str,
    nav_at_decision: float,
    nav_t1: Optional[float] = None,
    nav_t2: Optional[float] = None
) -> Tuple[str, float]:
    """
    判定决策的结果

    返回: (outcome, change_pct)
    outcome: correct/wrong/neutral/early/late
    change_pct: T+2或T+1的百分比变化
    """

    if nav_t2 is not None:
        change_pct = (nav_t2 - nav_at_decision) / nav_at_decision * 100
        base_nav = nav_t2
    elif nav_t1 is not None:
        change_pct = (nav_t1 - nav_at_decision) / nav_at_decision * 100
        base_nav = nav_t1
    else:
        return "pending", 0

    # buy/sell/clear 的判定标准
    if action == "buy":
        if abs(change_pct) <= 0.3:
            return "neutral", change_pct
        elif change_pct > 0.3:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    elif action == "sell":
        if abs(change_pct) <= 0.3:
            return "neutral", change_pct
        elif change_pct < -0.3:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    elif action == "clear":
        if abs(change_pct) <= 0.5:
            return "neutral", change_pct
        elif change_pct < -0.5:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    # watch/hold 的判定标准更宽松
    elif action == "watch":
        if abs(change_pct) <= 1.0:
            return "neutral", change_pct
        elif change_pct < -0.5:  # watch时下跌是正确的
            return "correct", change_pct
        else:
            return "wrong", change_pct  # 错失上涨机会

    elif action == "hold":
        if abs(change_pct) <= 1.0:
            return "neutral", change_pct
        elif change_pct > 0.3:
            return "correct", change_pct
        else:
            return "wrong", change_pct  # 应该减仓

    return "pending", 0


# ==================== 经验提炼 ====================

def extract_lessons_from_review(
    review_id: int,
    fund_code: str,
    action: str,
    reason: str,
    outcome: str,
    change_pct: float,
    confidence: str
) -> List[Dict]:
    """
    从复盘结果中提炼经验

    返回: [{category, trigger_pattern, expected_outcome, actual_outcome, lesson_text, ...}]
    """
    lessons = []

    if outcome == "pending":
        return lessons

    # 分析决策理由中的关键词，识别经验类型
    reason_lower = reason.lower() if reason else ""

    category = None
    if any(keyword in reason_lower for keyword in ['政策', '央行', '降准', '降息', '公告', '利好', '利空']):
        category = "policy"
    elif any(keyword in reason_lower for keyword in ['rsi', 'ma', '布林', '技术', '超卖', '超买']):
        category = "technical"
    elif any(keyword in reason_lower for keyword in ['情绪', '极端', '恐慌', '乐观', '悲观']):
        category = "sentiment"
    elif any(keyword in reason_lower for keyword in ['板块', '行业', '轮动', '从', '流向', '切换']):
        category = "sector"
    elif any(keyword in reason_lower for keyword in ['时机', '择时', '周期', '周一', '周五']):
        category = "timing"
    elif any(keyword in reason_lower for keyword in ['止损', '止盈', '回撤', '风险']):
        category = "risk"

    if category:
        lesson = {
            "category": category,
            "trigger_pattern": reason[:100],  # 截取前100个字符
            "expected_outcome": f"{action.upper()} 后应该 {'上涨' if action in ['buy', 'hold'] else '下跌'}",
            "actual_outcome": f"实际{'+' if change_pct > 0 else ''}{change_pct:.2f}%，{outcome}",
            "lesson_text": f"'{reason[:50]}' 触发的 {action} 决策最终 {outcome}",
            "confidence": "low",  # 单一案例默认信心低
            "source_review_ids": [review_id]
        }
        lessons.append(lesson)

    return lessons


def validate_lessons_against_review(
    review_id: int,
    outcome: str,
    change_pct: float,
    category: Optional[str] = None
) -> List[Dict]:
    """
    检查是否有已有经验被本次复盘验证或否定

    返回: [{lesson_id, success, note}]
    """
    validations = []

    # 查询所有相关类别的经验
    conditions = ["status = 'active'"]
    if category:
        conditions.append("category = %s")

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            where = " AND ".join(conditions) if conditions else ""
            params = [category] if category else []

            cur.execute(
                f"SELECT id FROM ft_lessons WHERE {where} LIMIT 10",
                params
            )
            lessons = cur.fetchall()

    # 简单的验证逻辑：outcome=correct 则验证成功，outcome=wrong 则验证失败
    success = (outcome == "correct")

    for lesson in lessons:
        validations.append({
            "lesson_id": lesson['id'],
            "success": success,
            "note": f"复盘 {outcome}，{'验证' if success else '否定'} 该经验"
        })

    return validations


# ==================== 主复盘流程 ====================

def execute_decision_review(limit: int = 30, days_back: int = 7) -> Dict:
    """
    执行待复盘决策的复盘，返回复盘统计结果

    返回格式:
    {
        "review_date": "YYYY-MM-DD",
        "total_reviewed": N,
        "correct": N,
        "wrong": N,
        "neutral": N,
        "new_lessons": N,
        "lesson_validations": N,
        "lesson_revisions": N,
        "details": [...]
    }
    """

    print("="*60)
    print("基金决策复盘执行器")
    print("="*60)

    # 加载净值数据
    nav_by_code = load_nav_data()
    print(f"\n已加载 {len(nav_by_code)} 个基金的净值历史数据")

    # 获取待复盘的决策
    pending_reviews = get_pending_reviews(days_back=days_back)
    print(f"待复盘决策数量: {len(pending_reviews)}\n")

    if not pending_reviews:
        print("没有待复盘的决策")
        return {
            "review_date": datetime.now().strftime("%Y-%m-%d"),
            "total_reviewed": 0,
            "correct": 0,
            "wrong": 0,
            "neutral": 0,
            "new_lessons": 0,
            "lesson_validations": 0,
            "lesson_revisions": 0
        }

    # 进行复盘
    stats = {
        "correct": 0,
        "wrong": 0,
        "neutral": 0,
        "pending": 0,
        "new_lessons": [],
        "lesson_validations": [],
        "lesson_revisions": []
    }

    reviewed_count = 0

    for review in pending_reviews[:limit]:
        review_id = review['id']
        fund_code = review['fund_code']
        action = review['action']
        reason = review['reason']
        decision_date = review['decision_date']

        print(f"\n复盘 {fund_code} {action} [ID={review_id}]")
        print(f"  决策理由: {reason[:60]}")
        print(f"  决策日期: {decision_date}")

        # 获取净值数据
        if fund_code not in nav_by_code:
            print(f"  ✗ 未找到净值数据")
            stats["pending"] += 1
            continue

        nav_history = nav_by_code[fund_code]

        # 决策日净值
        nav_at_decision = get_nav_for_date(nav_history, str(decision_date))
        if nav_at_decision is None:
            print(f"  ✗ 无法获取决策日净值")
            stats["pending"] += 1
            continue

        # T+1 净值
        nav_t1 = get_next_trading_day_nav(nav_history, str(decision_date))

        # 判定结果
        outcome, change_pct = judge_decision_outcome(action, nav_at_decision, nav_t1)

        # 如果无法获取T+1净值，尝试从现有的复盘记录中提取
        if nav_t1 is None and review['nav_t1'] is not None:
            nav_t1 = review['nav_t1']

        if nav_t1 is not None:
            stats[outcome] = stats.get(outcome, 0) + 1
            reviewed_count += 1

            # 更新复盘记录
            try:
                update_review(
                    review_id,
                    nav_at_decision=nav_at_decision,
                    nav_t1=nav_t1,
                    change_t1_pct=change_pct,
                    outcome=outcome,
                    review_notes=f"决策方向: {action}, 实际变化: {change_pct:+.2f}%, 判定: {outcome}"
                )
                print(f"  ✓ {outcome:8s} | 净值: {nav_at_decision:.4f} → {nav_t1:.4f} | 变化: {change_pct:+.2f}%")
            except Exception as e:
                print(f"  ✗ 更新失败: {e}")
                continue
        else:
            print(f"  ✗ 无法获取T+1净值，跳过此条决策")
            stats["pending"] += 1
            continue

        # 尝试提炼经验
        if outcome != "pending":
            lessons = extract_lessons_from_review(
                review_id, fund_code, action, reason, outcome, change_pct,
                review['confidence']
            )
            if lessons:
                for lesson in lessons:
                    try:
                        lesson_id = save_lesson(
                            category=lesson['category'],
                            trigger_pattern=lesson['trigger_pattern'],
                            expected_outcome=lesson['expected_outcome'],
                            actual_outcome=lesson['actual_outcome'],
                            lesson_text=lesson['lesson_text'],
                            confidence=lesson['confidence'],
                            source_review_ids=lesson['source_review_ids']
                        )
                        stats["new_lessons"].append({
                            "lesson_id": lesson_id,
                            "category": lesson['category'],
                            "text": lesson['lesson_text']
                        })
                        mark_lesson_extracted(review_id)
                        print(f"    → 新经验 #{lesson_id}: {lesson['category']}")
                    except Exception as e:
                        print(f"    → 保存经验失败: {e}")

            # 验证已有经验
            validations = validate_lessons_against_review(review_id, outcome, change_pct)
            if validations:
                for val in validations:
                    try:
                        update_lesson_confidence(val['lesson_id'], val['success'])
                        stats["lesson_validations"].append(val)
                        print(f"    → 验证经验 #{val['lesson_id']}: {'✓' if val['success'] else '✗'}")
                    except Exception as e:
                        print(f"    → 验证失败: {e}")

    # 生成统计报告
    print("\n" + "="*60)
    print("复盘结果统计")
    print("="*60)
    print(f"总已复盘: {reviewed_count} 条")
    print(f"  正确: {stats['correct']} 条")
    print(f"  错误: {stats['wrong']} 条")
    print(f"  平局: {stats['neutral']} 条")
    print(f"  待数据: {stats['pending']} 条")

    if reviewed_count > 0:
        correct_rate = stats['correct'] / reviewed_count * 100
        print(f"\n正确率: {correct_rate:.1f}%")

    print(f"\n新增经验: {len(stats['new_lessons'])} 条")
    for lesson in stats['new_lessons']:
        print(f"  #{lesson['lesson_id']} [{lesson['category']}] {lesson['text']}")

    print(f"\n验证经验: {len(stats['lesson_validations'])} 条")

    print(f"\n已完成的复盘任务 ✓")

    return {
        "review_date": datetime.now().strftime("%Y-%m-%d"),
        "total_reviewed": reviewed_count,
        "correct": stats['correct'],
        "wrong": stats['wrong'],
        "neutral": stats['neutral'],
        "new_lessons": len(stats['new_lessons']),
        "lesson_validations": len(stats['lesson_validations']),
        "lesson_revisions": len(stats.get('lesson_revisions', []))
    }


if __name__ == "__main__":
    result = execute_decision_review(limit=30, days_back=30)
    print(json.dumps(result, indent=2, ensure_ascii=False))
