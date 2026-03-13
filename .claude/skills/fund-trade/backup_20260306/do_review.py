#!/usr/bin/env python3
"""
基金决策复盘执行 - 生产版本

这个脚本会：
1. 从数据库获取待复盘决策
2. 加载最新的净值数据（如果有T+1数据则使用，否则使用模拟）
3. 判定决策结果
4. 保存复盘结果和提炼的经验
"""

import sys
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import psycopg2.extras
from fund_db import get_conn, update_review, save_lesson, update_lesson_confidence

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "jettask",
    "user": "jettask",
    "password": "123456",
}

def parse_nav_history(nav_string: str) -> Dict[str, float]:
    """解析净值历史字符串"""
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
            except:
                continue
    return result

def load_nav_data() -> Dict[str, Dict[str, float]]:
    """加载净值历史数据"""
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

def get_nav_on_date(nav_history: Dict[str, float], date_str: str) -> Optional[float]:
    """获取指定日期的净值（如果没有精确日期，返回最接近的前一个交易日）"""
    date_key = date_str.replace('-', '') if '-' in date_str else date_str

    if date_key in nav_history:
        return nav_history[date_key]

    available_dates = sorted(nav_history.keys(), reverse=True)
    for available_date in available_dates:
        if available_date <= date_key:
            return nav_history[available_date]

    return None

def get_next_nav(nav_history: Dict[str, float], date_str: str) -> Optional[float]:
    """获取指定日期之后第一个交易日的净值"""
    date_key = date_str.replace('-', '') if '-' in date_str else date_str

    available_dates = sorted(nav_history.keys(), reverse=True)
    found = False
    for i, date in enumerate(available_dates):
        if date == date_key:
            found = True
        elif found and date < date_key:
            return nav_history[date]

    return None

def judge_outcome(action: str, nav_decision: float, nav_t1: Optional[float]) -> Tuple[str, float]:
    """判定决策结果"""
    if nav_t1 is None:
        return "pending", 0

    change_pct = (nav_t1 - nav_decision) / nav_decision * 100

    threshold_active = 0.3  # buy/sell/clear
    threshold_passive = 1.0  # watch/hold

    if action == "buy":
        if abs(change_pct) <= threshold_active:
            return "neutral", change_pct
        elif change_pct > threshold_active:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    elif action == "sell":
        if abs(change_pct) <= threshold_active:
            return "neutral", change_pct
        elif change_pct < -threshold_active:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    elif action == "watch":
        if abs(change_pct) <= threshold_passive:
            return "neutral", change_pct
        elif change_pct < -0.5:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    elif action == "hold":
        if abs(change_pct) <= threshold_passive:
            return "neutral", change_pct
        elif change_pct > threshold_active:
            return "correct", change_pct
        else:
            return "wrong", change_pct

    return "pending", 0

def do_review():
    """执行复盘"""
    print("="*70)
    print(" "*15 + "基金决策复盘 - 生产执行")
    print("="*70)

    # 加载净值数据
    nav_by_code = load_nav_data()
    print(f"\n✓ 已加载 {len(nav_by_code)} 个基金的净值历史\n")

    # 获取待复盘决策
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT r.id, r.fund_code, d.fund_name, d.action, d.reason,
                   d.confidence, r.decision_date, r.nav_at_decision, r.nav_t1
            FROM ft_reviews r
            LEFT JOIN ft_decisions d ON r.decision_id = d.id
            WHERE r.outcome = 'pending'
            AND r.decision_date >= CURRENT_DATE - 30
            ORDER BY r.decision_date DESC
        """)

        pending = cur.fetchall()
        print(f"待复盘决策: {len(pending)} 条\n")

        stats = {"correct": 0, "wrong": 0, "neutral": 0, "failed": 0}

        for i, review in enumerate(pending, 1):
            rev_id = review['id']
            code = review['fund_code']
            action = review['action']
            reason = review['reason'] or ""
            decision_date = review['decision_date']

            # 跳过没有净值数据的基金
            if code not in nav_by_code:
                print(f"{i:2d}. {code:6s} {action:6s} [ID={rev_id:3d}] ✗ 无净值数据")
                stats["failed"] += 1
                continue

            nav_history = nav_by_code[code]

            # 获取决策日净值
            nav_decision = get_nav_on_date(nav_history, str(decision_date))
            if nav_decision is None:
                print(f"{i:2d}. {code:6s} {action:6s} [ID={rev_id:3d}] ✗ 获取决策日净值失败")
                stats["failed"] += 1
                continue

            # 获取T+1净值
            nav_t1 = get_next_nav(nav_history, str(decision_date))
            if nav_t1 is None:
                print(f"{i:2d}. {code:6s} {action:6s} [ID={rev_id:3d}] ✗ 无T+1数据")
                stats["failed"] += 1
                continue

            # 判定结果
            outcome, change_pct = judge_outcome(action, nav_decision, nav_t1)

            # 更新数据库
            try:
                update_review(
                    rev_id,
                    nav_at_decision=nav_decision,
                    nav_t1=nav_t1,
                    change_t1_pct=change_pct,
                    outcome=outcome,
                    review_notes=f"{action.upper()}后{change_pct:+.2f}%，{outcome}"
                )
                stats[outcome] += 1

                status_icon = "✓" if outcome == "correct" else "✗" if outcome == "wrong" else "="
                reason_short = reason[:40] if len(reason) > 40 else reason

                print(f"{i:2d}. {code:6s} {action:6s} [ID={rev_id:3d}] {status_icon} "
                      f"{outcome:7s} {change_pct:+6.2f}% | {reason_short}")

                # 简单经验提炼：正确的决策中涉及的关键词记录
                if outcome == "correct" and "政策" in reason:
                    # 验证相关经验
                    try:
                        cur.execute("SELECT id FROM ft_lessons WHERE category='policy' LIMIT 1")
                        lesson = cur.fetchone()
                        if lesson:
                            update_lesson_confidence(lesson['id'], True)
                    except:
                        pass

            except Exception as e:
                print(f"{i:2d}. {code:6s} {action:6s} [ID={rev_id:3d}] ✗ 更新失败: {e}")
                stats["failed"] += 1

        conn.close()

    except Exception as e:
        print(f"数据库错误: {e}")
        return

    # 输出统计
    print("\n" + "="*70)
    print("复盘结果统计")
    print("="*70)
    total = stats["correct"] + stats["wrong"] + stats["neutral"]
    print(f"总复盘: {total} 条")
    print(f"  正确 ✓: {stats['correct']:3d} 条  ({stats['correct']/total*100:5.1f}%)" if total > 0 else "  正确 ✓: 0 条")
    print(f"  错误 ✗: {stats['wrong']:3d} 条  ({stats['wrong']/total*100:5.1f}%)" if total > 0 else "  错误 ✗: 0 条")
    print(f"  平局 =: {stats['neutral']:3d} 条  ({stats['neutral']/total*100:5.1f}%)" if total > 0 else "  平局 =: 0 条")
    print(f"  失败:   {stats['failed']:3d} 条")

    if stats['correct'] > 0:
        win_rate = stats['correct'] / total * 100
        print(f"\n胜率: {win_rate:.1f}%")

    print("\n✓ 复盘完成")

if __name__ == "__main__":
    do_review()
