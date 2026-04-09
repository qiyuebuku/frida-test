#!/usr/bin/env python3
"""量化信号计算模块 - 从 PG 缓存计算指标，作为 LLM 决策参考"""

import json
import os
import sys

from src.infrastructure.db.fund_db import get_cache, save_signal, get_config


def _load_config():
    return get_config()


# ==================== 净值解析 ====================

def _parse_nav_list(nav_data):
    """从 nav 缓存中提取净值列表（时间正序，最早在前）

    nav 数据格式: {"data": "日期;x;净值;涨幅|日期;x;净值;涨幅|..."}
    数据从新到旧排列，需反转为正序。
    """
    if not nav_data:
        return []
    # data 字段可能是字符串或嵌套结构
    data_str = nav_data.get("data", "")
    if not isinstance(data_str, str) or not data_str:
        return []

    records = []
    for line in data_str.split("|"):
        parts = line.split(";")
        if len(parts) >= 3:
            try:
                records.append(float(parts[2]))
            except (ValueError, IndexError):
                continue
    records.reverse()
    return records


# ==================== 均线计算 ====================

def _calc_ma(navs, n):
    """计算 MA(n)，数据不足返回 None"""
    if len(navs) < n:
        return None
    return sum(navs[-n:]) / n


def _calc_ma_arrangement(navs):
    """判断均线排列：多头排列 / 空头排列 / 震荡"""
    ma5 = _calc_ma(navs, 5)
    ma10 = _calc_ma(navs, 10)
    ma20 = _calc_ma(navs, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return None
    if ma5 > ma10 > ma20:
        return "多头排列"
    elif ma5 < ma10 < ma20:
        return "空头排列"
    else:
        return "震荡"


def _calc_ma5_cross_ma20(navs):
    """检查最近5日内 MA5 与 MA20 是否发生金叉/死叉

    逐日回溯，检查 MA5 和 MA20 是否从一侧穿越到另一侧。
    """
    if len(navs) < 25:  # 至少需要 20+5 个数据点
        return "none"

    # 计算最近 6 天的 MA5 和 MA20（需要 [today, today-1, ..., today-5] 共 6 个点）
    for days_ago in range(1, 6):
        idx_today = len(navs) - days_ago
        idx_yesterday = idx_today - 1
        if idx_yesterday < 19:  # MA20 至少需要 20 个点
            break

        ma5_today = sum(navs[idx_today - 4:idx_today + 1]) / 5
        ma20_today = sum(navs[idx_today - 19:idx_today + 1]) / 20
        ma5_yesterday = sum(navs[idx_yesterday - 4:idx_yesterday + 1]) / 5
        ma20_yesterday = sum(navs[idx_yesterday - 19:idx_yesterday + 1]) / 20

        # 金叉: 前一天 MA5 <= MA20，当天 MA5 > MA20
        if ma5_yesterday <= ma20_yesterday and ma5_today > ma20_today:
            return f"golden_cross_{days_ago}d_ago"
        # 死叉: 前一天 MA5 >= MA20，当天 MA5 < MA20
        if ma5_yesterday >= ma20_yesterday and ma5_today < ma20_today:
            return f"death_cross_{days_ago}d_ago"

    return "none"


# ==================== RSI ====================

def _extract_rsi14(rsi_data):
    """从 rsi 缓存中提取 RSI14 数值

    RSI API 返回格式可能是:
    {"data": {"rsiBestLimitDown": ..., "rsiBestLimitUp": ..., ...}}
    或含有 rsi 数值的其他结构。尝试多种路径提取。
    """
    if not rsi_data:
        return None
    try:
        data = rsi_data.get("data", rsi_data)
        if isinstance(data, dict):
            # 尝试直接取 rsi 值
            for key in ("rsi", "rsi14", "RSI", "RSI14", "value"):
                if key in data and data[key] is not None:
                    return float(data[key])
            # 尝试从列表结构中提取
            for key in ("indicList", "list", "items"):
                items = data.get(key, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            for vk in ("rsi", "value", "rsi14"):
                                if vk in item and item[vk] is not None:
                                    return float(item[vk])
            # 尝试从 rsiBestLimitDown/Up 区间推断中间值
            down = data.get("rsiBestLimitDown")
            up = data.get("rsiBestLimitUp")
            if down is not None and up is not None:
                # 只有上下限没有当前值，返回 None
                return None
        # 如果 data 直接是数字
        if isinstance(data, (int, float)):
            return float(data)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _rsi_status(rsi):
    """RSI 状态判断"""
    if rsi is None:
        return None
    if rsi < 30:
        return "oversold"
    elif rsi > 70:
        return "overbought"
    else:
        return "neutral"


# ==================== PE 百分位 ====================

def _extract_pe_percentile(pe_data):
    """从 pe_percentile 缓存提取加权估值百分位

    格式: {"data": {"summary": {"weightedPePct": 35.2, ...}, "stocks": [...]}}
    """
    if not pe_data:
        return None
    try:
        data = pe_data.get("data", pe_data)
        if isinstance(data, dict):
            summary = data.get("summary", {})
            if isinstance(summary, dict):
                val = summary.get("weightedPePct")
                if val is not None:
                    return round(float(val), 1)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _pe_rating(pct):
    """PE 百分位评级"""
    if pct is None:
        return None
    if pct < 20:
        return "极低"
    elif pct < 40:
        return "偏低"
    elif pct < 60:
        return "中等"
    elif pct < 80:
        return "偏高"
    else:
        return "极高"


# ==================== 60日回撤 ====================

def _extract_drawdown_60d(drawdown_data):
    """从 drawdown 缓存中提取 60 日（近半年）最大回撤

    API 返回阶段回撤数据，尝试提取近半年的回撤值。
    """
    if not drawdown_data:
        return None
    try:
        data = drawdown_data.get("data", drawdown_data)
        if isinstance(data, dict):
            # 常见结构: {"data": {"list": [{"key": "hyear", "value": -12.5}, ...]}}
            items = data.get("list", data.get("items", data.get("drawdownList", [])))
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        key = item.get("key", item.get("name", item.get("type", "")))
                        if key in ("hyear", "halfYear", "近半年", "half_year", "sixMonth"):
                            val = item.get("value", item.get("maxDrawDown", item.get("drawdown")))
                            if val is not None:
                                return round(float(val), 2)
            # 尝试直接取字段
            for key in ("hyear", "halfYear", "maxDrawDown", "drawdown_60d"):
                if key in data and data[key] is not None:
                    return round(float(data[key]), 2)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


# ==================== 资金流向趋势 ====================

def _extract_fund_flow_trend(scale_data):
    """从 scale_change 缓存判断资金流向趋势（净申购/净赎回/稳定）

    规模变动数据，检查最近一两个季度的份额变动方向。
    """
    if not scale_data:
        return None
    try:
        data = scale_data.get("data", scale_data)
        if isinstance(data, dict):
            # 常见结构: 列表包含季度数据，有申购/赎回/份额变动字段
            items = data.get("list", data.get("items", data.get("scaleList", [])))
            if isinstance(items, list) and len(items) > 0:
                # 取最新一条记录
                latest = items[0] if isinstance(items[0], dict) else None
                if latest:
                    # 尝试获取净申购额或份额变动
                    for key in ("netPurchase", "net_purchase", "shareChange",
                                "share_change", "netSubscribe"):
                        val = latest.get(key)
                        if val is not None:
                            val = float(val)
                            if val > 0:
                                return "净申购"
                            elif val < 0:
                                return "净赎回"
                            else:
                                return "稳定"
        # 如果是字符串类型的简化数据
        if isinstance(data, str):
            if "申购" in data or "流入" in data:
                return "净申购"
            elif "赎回" in data or "流出" in data:
                return "净赎回"
    except (ValueError, TypeError, AttributeError):
        pass
    return None


# ==================== 机构持仓占比趋势 ====================

def _extract_inst_ratio_trend(holder_data):
    """从 holder_ratio 缓存判断机构持仓占比趋势（上升/下降/稳定）

    检查最近两期机构持仓比例的变化方向。
    """
    if not holder_data:
        return None
    try:
        data = holder_data.get("data", holder_data)
        if isinstance(data, dict):
            items = data.get("list", data.get("items", data.get("holderList",
                        data.get("constList", []))))
            if isinstance(items, list) and len(items) >= 2:
                def _get_inst_ratio(item):
                    for key in ("instRatio", "inst_ratio", "institutionRatio",
                                "jgcybl", "机构占比"):
                        if key in item and item[key] is not None:
                            return float(str(item[key]).rstrip("%"))
                    return None

                r0 = _get_inst_ratio(items[0])
                r1 = _get_inst_ratio(items[1])
                if r0 is not None and r1 is not None:
                    diff = r0 - r1
                    if diff > 1:
                        return "上升"
                    elif diff < -1:
                        return "下降"
                    else:
                        return "稳定"
    except (ValueError, TypeError, AttributeError):
        pass
    return None


# ==================== 近一年排名百分位 ====================

def _extract_rank_1y_pct(rank_data):
    """从 rank 缓存提取近一年排名百分位

    阶段涨幅排名数据，找到近一年的排名和同类总数，计算百分位。
    """
    if not rank_data:
        return None
    try:
        data = rank_data.get("data", rank_data)
        if isinstance(data, dict):
            items = data.get("list", data.get("items", data.get("rateList", [])))
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        key = item.get("key", item.get("name", item.get("type", "")))
                        if key in ("year", "近一年", "1year", "oneYear"):
                            rank_val = item.get("rank", item.get("ranking"))
                            total = item.get("total", item.get("totalCount", item.get("count")))
                            if rank_val is not None and total is not None:
                                rank_val = int(rank_val)
                                total = int(total)
                                if total > 0:
                                    # 排名越小越好，百分位 = rank/total * 100
                                    return round(rank_val / total * 100, 1)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


# ==================== 基金名称 ====================

def _extract_fund_name(detail_data):
    """从 detail 缓存提取基金名称"""
    if not detail_data:
        return None
    try:
        data = detail_data.get("data", detail_data)
        if isinstance(data, dict):
            for key in ("name", "fundName", "simpleName", "fund_name", "shortName"):
                val = data.get(key)
                if val:
                    return str(val)
            # 嵌套结构
            for sub_key in ("fundBase", "base", "info", "detail"):
                sub = data.get(sub_key, {})
                if isinstance(sub, dict):
                    for key in ("name", "fundName", "simpleName", "fund_name", "shortName"):
                        val = sub.get(key)
                        if val:
                            return str(val)
    except (TypeError, AttributeError):
        pass
    return None


# ==================== 规则建议 ====================

def _rule_suggestion(signals):
    """基于简单组合规则给出建议"""
    rsi = signals.get("rsi14")
    pe = signals.get("pe_percentile")
    ma_arr = signals.get("ma_arrangement")
    cross = signals.get("ma5_cross_ma20", "none")

    # 任一关键指标缺失则降级为 hold
    if rsi is None or pe is None:
        return "hold", "关键指标缺失，默认持有观望"

    # buy: RSI < 40 且 PE百分位 < 50 且 (多头排列 或 金叉)
    has_bullish_ma = (ma_arr == "多头排列") or (cross and "golden_cross" in str(cross))
    if rsi < 40 and pe < 50 and has_bullish_ma:
        reasons = []
        rsi_status = signals.get("rsi_status", "")
        if rsi_status == "oversold":
            reasons.append("RSI超卖")
        else:
            reasons.append(f"RSI偏低({rsi})")
        reasons.append(f"估值{signals.get('pe_rating', '偏低')}")
        if ma_arr == "多头排列":
            reasons.append("多头排列")
        if cross and "golden_cross" in str(cross):
            reasons.append("金叉信号")
        # 附加正面因素
        if signals.get("inst_ratio_trend") == "上升":
            reasons.append("机构加仓")
        if signals.get("fund_flow_trend") == "净申购":
            reasons.append("资金流入")
        return "buy", "+".join(reasons)

    # sell: RSI > 70 且 PE百分位 > 70
    if rsi > 70 and pe > 70:
        reasons = []
        if signals.get("rsi_status") == "overbought":
            reasons.append("RSI超买")
        else:
            reasons.append(f"RSI偏高({rsi})")
        reasons.append(f"估值{signals.get('pe_rating', '偏高')}")
        if ma_arr == "空头排列":
            reasons.append("空头排列")
        if cross and "death_cross" in str(cross):
            reasons.append("死叉信号")
        if signals.get("inst_ratio_trend") == "下降":
            reasons.append("机构减仓")
        return "sell", "+".join(reasons)

    # hold: 其他
    return "hold", "指标中性，继续持有观望"


# ==================== 单基金评估 ====================

def evaluate_fund(fund_code):
    """对单只基金计算全部量化信号"""
    # 读取各类缓存数据
    rsi_data = get_cache(fund_code, "rsi")
    nav_data = get_cache(fund_code, "nav")
    pe_data = get_cache(fund_code, "pe_percentile")
    drawdown_data = get_cache(fund_code, "drawdown")
    scale_data = get_cache(fund_code, "scale_change")
    holder_data = get_cache(fund_code, "holder_ratio")
    rank_data = get_cache(fund_code, "rank")
    detail_data = get_cache(fund_code, "detail")

    # 提取基金名称
    name = _extract_fund_name(detail_data) or fund_code

    # 计算各指标
    # 1. RSI14
    rsi14 = _extract_rsi14(rsi_data)

    # 如果 RSI API 没有返回当前值，尝试从 nav 数据自行计算
    if rsi14 is None:
        navs = _parse_nav_list(nav_data)
        if len(navs) >= 15:
            rsi14 = _calc_rsi14_from_navs(navs)

    # 2. RSI 状态
    rsi_st = _rsi_status(rsi14)

    # 3-4. 均线排列 & MA5穿越MA20
    navs = _parse_nav_list(nav_data)
    ma_arr = _calc_ma_arrangement(navs) if len(navs) >= 20 else None
    ma_cross = _calc_ma5_cross_ma20(navs) if len(navs) >= 25 else "none"

    # 5-6. PE 百分位 & 评级
    pe_pct = _extract_pe_percentile(pe_data)
    pe_rate = _pe_rating(pe_pct)

    # 7. 60日回撤
    dd_60d = _extract_drawdown_60d(drawdown_data)

    # 8. 资金流向趋势
    flow_trend = _extract_fund_flow_trend(scale_data)

    # 9. 机构持仓占比趋势
    inst_trend = _extract_inst_ratio_trend(holder_data)

    # 10. 近一年排名百分位
    rank_pct = _extract_rank_1y_pct(rank_data)

    signals = {
        "rsi14": rsi14,
        "rsi_status": rsi_st,
        "ma_arrangement": ma_arr,
        "ma5_cross_ma20": ma_cross,
        "pe_percentile": pe_pct,
        "pe_rating": pe_rate,
        "drawdown_60d": dd_60d,
        "fund_flow_trend": flow_trend,
        "inst_ratio_trend": inst_trend,
        "rank_1y_pct": rank_pct,
    }

    action, reason = _rule_suggestion(signals)

    return {
        "name": name,
        "signals": signals,
        "rule_suggestion": action,
        "rule_reason": reason,
    }


def _calc_rsi14_from_navs(navs):
    """从净值序列计算 RSI(14) - Wilder 平滑"""
    if len(navs) < 15:
        return None
    changes = [navs[i] - navs[i - 1] for i in range(1, len(navs))]
    period = 14
    gains = [max(c, 0) for c in changes[:period]]
    losses = [abs(min(c, 0)) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for c in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period
        avg_loss = (avg_loss * (period - 1) + abs(min(c, 0))) / period
    if (avg_gain + avg_loss) == 0:
        return 50.0
    return round(100 * avg_gain / (avg_gain + avg_loss), 1)


# ==================== evaluate 子命令 ====================

def cmd_evaluate():
    """对基金池中每只基金计算量化信号"""
    config = _load_config()
    raw_pool = config.get("fund_pool", [])
    fund_pool = [item["code"] if isinstance(item, dict) else item for item in raw_pool]

    if not fund_pool:
        print(json.dumps({"message": "基金池为空，请先在 ft_config 中配置 fund_pool"}, ensure_ascii=False))
        return

    result = {}
    for code in fund_pool:
        try:
            fund_result = evaluate_fund(code)
            result[code] = fund_result

            # 保存信号到 ft_signals 表
            save_signal(
                fund_code=code,
                strategy="default",
                action=fund_result["rule_suggestion"],
                confidence="medium",
                indicators=fund_result["signals"],
            )
        except Exception as e:
            result[code] = {
                "name": code,
                "signals": {},
                "rule_suggestion": "hold",
                "rule_reason": f"计算异常: {e}",
            }

    print(json.dumps(result, ensure_ascii=False))


# ==================== CLI ====================

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "evaluate":
        print(json.dumps({
            "usage": "python indicators.py evaluate",
            "description": "对基金池每只基金计算量化信号并保存到 ft_signals",
        }, ensure_ascii=False))
        sys.exit(1)

    cmd_evaluate()
