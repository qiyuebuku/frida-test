"""策略路由：风控/量化/决策复盘/决策管理/持仓管理/限额/数据同步/账户/基金扫描"""

import json
import sys
from io import StringIO

from fastapi import APIRouter, HTTPException, Query, Body

from routers._utils import safe_call, client
from services import fund_db

router = APIRouter()


# ==================== 风控模块 ====================

@router.get("/api/risk/snapshot", summary="风控快照", tags=["风控模块"])
async def risk_snapshot():
    """输出当前持仓/仓位/可用资金/各基金风控状态"""
    from services import risk_manager

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.snapshot()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"风控快照失败: {e}")


@router.post("/api/risk/check", summary="校验决策", tags=["风控模块"])
async def risk_check(decisions: dict = Body(...)):
    """校验 LLM 决策是否违反硬约束"""
    from services import risk_manager

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.check(decisions)
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return json.loads(output)
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"决策校验失败: {e}")


@router.get("/api/risk/preflight", summary="交易前置检查", tags=["风控模块"])
async def risk_preflight():
    """交易前置检查：交易时间、交易日、熔断机制"""
    from services import risk_manager

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        risk_manager.preflight()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"前置检查失败: {e}")


# ==================== 量化信号模块 ====================

@router.post("/api/indicators/evaluate", summary="计算量化信号", tags=["量化信号"])
async def evaluate_indicators():
    """对基金池中每只基金计算量化信号"""
    from services import indicators

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        indicators.cmd_evaluate()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        raw_result = json.loads(output)

        return {
            "status": "success",
            "data": raw_result
        }
    except Exception as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=500, detail=f"量化信号计算失败: {e}")


# ==================== 决策复盘模块 ====================

@router.post("/api/review/execute", summary="执行决策复盘", tags=["决策复盘"])
async def execute_review(limit: int = Query(30, description="复盘数量"), days_back: int = Query(7, description="回溯天数")):
    """执行待复盘决策的复盘，返回复盘统计结果"""
    from services import review_decision_executor

    try:
        result = review_decision_executor.execute_decision_review(limit, days_back)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"决策复盘失败: {e}")


@router.post("/api/review/create", summary="创建待复盘记录", tags=["决策复盘"])
async def create_reviews(decision_date: str = Query(None, description="决策日期 YYYY-MM-DD")):
    """从 ft_decisions 创建待复盘记录到 ft_reviews"""
    try:
        count = fund_db.create_reviews_from_decisions(decision_date)
        return {
            "status": "success",
            "message": f"创建了 {count} 条待复盘记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建待复盘记录失败: {e}")


@router.get("/api/review/pending", summary="获取待复盘决策", tags=["决策复盘"])
async def get_pending_reviews(days_back: int = Query(3, description="回溯天数")):
    """获取待复盘的决策列表"""
    try:
        reviews = fund_db.get_pending_reviews(days_back)
        return {
            "status": "success",
            "data": reviews,
            "count": len(reviews)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取待复盘决策失败: {e}")


@router.get("/api/review/stats", summary="获取复盘统计", tags=["决策复盘"])
async def get_review_statistics(days: int = Query(30, description="统计天数")):
    """获取复盘统计数据"""
    try:
        stats = fund_db.get_review_stats(days)
        return {
            "status": "success",
            "data": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取复盘统计失败: {e}")


@router.get("/api/lessons", summary="获取经验知识库", tags=["决策复盘"])
async def get_lessons(
    category: str = Query(None, description="经验分类"),
    min_confidence: str = Query(None, description="最低可信度"),
    include_deprecated: bool = Query(False, description="包含已废弃的经验"),
    limit: int = Query(20, description="返回数量限制")
):
    """获取经验知识库"""
    try:
        lessons = fund_db.get_lessons(category, min_confidence, include_deprecated, limit)
        return {
            "status": "success",
            "data": lessons,
            "count": len(lessons)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取经验知识库失败: {e}")


@router.post("/api/lessons/save", summary="保存经验教训", tags=["决策复盘"])
async def save_lesson(lesson: dict):
    """保存一条经验教训到 ft_lessons 表"""
    try:
        lesson_id = fund_db.save_lesson(
            category=lesson["category"],
            trigger_pattern=lesson["trigger_pattern"],
            expected_outcome=lesson["expected_outcome"],
            actual_outcome=lesson["actual_outcome"],
            lesson_text=lesson["lesson_text"],
            confidence=lesson.get("confidence", "low"),
            related_sectors=lesson.get("related_sectors"),
            tags=lesson.get("tags"),
            source_review_ids=lesson.get("source_review_ids")
        )
        return {
            "status": "success",
            "message": "经验教训保存成功",
            "lesson_id": lesson_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存经验教训失败: {e}")


@router.post("/api/lessons/update-confidence/{lesson_id}", summary="更新经验可信度", tags=["决策复盘"])
async def update_lesson_confidence(lesson_id: int, success: bool):
    """更新经验教训的可信度"""
    try:
        fund_db.update_lesson_confidence(lesson_id, success)
        return {
            "status": "success",
            "message": f"经验 {lesson_id} 可信度已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新经验可信度失败: {e}")


@router.post("/api/review/update/{review_id}", summary="更新复盘结果", tags=["决策复盘"])
async def update_review(review_id: int, review_data: dict):
    """更新复盘结果到 ft_reviews 表"""
    try:
        fund_db.update_review(
            review_id=review_id,
            nav_at_decision=review_data.get("nav_at_decision"),
            nav_t1=review_data.get("nav_t1"),
            nav_t2=review_data.get("nav_t2"),
            change_t1_pct=review_data.get("change_t1_pct"),
            change_t2_pct=review_data.get("change_t2_pct"),
            outcome=review_data.get("outcome"),
            review_notes=review_data.get("review_notes")
        )
        return {
            "status": "success",
            "message": f"复盘结果 {review_id} 已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新复盘结果失败: {e}")


@router.post("/api/review/mark-extracted/{review_id}", summary="标记经验已提取", tags=["决策复盘"])
async def mark_lesson_extracted(review_id: int):
    """标记复盘记录的经验已提取"""
    try:
        fund_db.mark_lesson_extracted(review_id)
        return {
            "status": "success",
            "message": f"复盘 {review_id} 已标记为经验已提取"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"标记经验已提取失败: {e}")


# ==================== 决策管理模块 ====================

@router.post("/api/decisions/save", summary="保存决策", tags=["决策管理"])
async def save_decision(decision: dict):
    """保存决策到 ft_decisions 表"""
    try:
        fund_db.save_decision(
            fund_code=decision["fund_code"],
            fund_name=decision["fund_name"],
            action=decision["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
            market_view=decision.get("market_view"),
            amount=decision.get("amount"),
            sell_pct=decision.get("sell_pct"),
            risk_notes=decision.get("risk_notes"),
            referenced_lesson_ids=decision.get("referenced_lesson_ids", [])
        )
        return {
            "status": "success",
            "message": "决策保存成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存决策失败: {e}")


@router.post("/api/decisions/save-pending", summary="保存待确认决策", tags=["决策管理"])
async def save_pending_decision(decision: dict):
    """保存待确认的决策到 ft_pending_decisions 表"""
    try:
        fund_db.save_pending_decision(
            fund_code=decision["fund_code"],
            fund_name=decision["fund_name"],
            action=decision["action"],
            reason=decision["reason"],
            confidence=decision["confidence"],
            market_view=decision.get("market_view"),
            market_phase=decision.get("market_phase"),
            amount=decision.get("amount"),
            sell_pct=decision.get("sell_pct"),
            risk_notes=decision.get("risk_notes")
        )
        return {
            "status": "success",
            "message": "待确认决策保存成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存待确认决策失败: {e}")


@router.post("/api/decisions/execute-pending/{pending_id}", summary="执行待确认决策", tags=["决策管理"])
async def execute_pending_decision(pending_id: int):
    """标记待确认决策为已执行"""
    try:
        fund_db.execute_pending_decision(pending_id)
        return {
            "status": "success",
            "message": f"待确认决策 {pending_id} 已标记为执行"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行待确认决策失败: {e}")


@router.get("/api/decisions/today", summary="获取今日决策", tags=["决策管理"])
async def get_today_decisions():
    """获取今日所有决策记录"""
    try:
        decisions = fund_db.get_today_decisions()
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取今日决策失败: {e}")


@router.get("/api/decisions/recent", summary="获取最近决策", tags=["决策管理"])
async def get_recent_decisions(
    days: int = Query(5, description="回溯天数"),
    exclude_today: bool = Query(False, description="排除今日决策")
):
    """获取最近几天的决策记录"""
    try:
        decisions = fund_db.get_recent_decisions(days, exclude_today)
        return {
            "status": "success",
            "data": decisions,
            "count": len(decisions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最近决策失败: {e}")


@router.get("/api/decisions/watch-streaks", summary="获取连续观望天数", tags=["决策管理"])
async def get_watch_streaks():
    """获取各基金的连续观望天数"""
    try:
        streaks = fund_db.get_watch_streaks()
        return {
            "status": "success",
            "data": streaks
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取观望天数失败: {e}")


# ==================== 持仓查询模块 ====================

@router.get("/api/position", summary="查询所有持仓", tags=["持仓管理"])
async def get_all_positions():
    """从 ft_positions 表查询所有持仓"""
    try:
        positions = fund_db.get_positions()
        return {
            "status": "success",
            "data": positions,
            "count": len(positions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


@router.get("/api/position/{fund_code}", summary="查询指定基金持仓", tags=["持仓管理"])
async def get_single_position(fund_code: str):
    """从 ft_positions 表查询指定基金持仓"""
    try:
        positions = fund_db.get_positions()
        for p in positions:
            if p.get("fund_code") == fund_code:
                return {
                    "status": "success",
                    "data": p
                }
        return {
            "status": "error",
            "message": f"未找到基金 {fund_code} 的持仓记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


# ==================== 本地订单查询 ====================

@router.get("/api/trades", summary="查询本地交易记录", tags=["持仓管理"])
async def get_local_trades(
    days: int = Query(30, description="查询天数"),
    limit: int = Query(100, description="返回条数")
):
    """从 ft_trades 表查询本地交易记录（不需要同花顺登录）"""
    try:
        trades = fund_db.get_trades(days=days, limit=limit)
        return {
            "status": "success",
            "data": trades,
            "count": len(trades)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询交易记录失败: {e}")


# ==================== 基金限额模块 ====================

@router.get("/api/limits/summary", summary="限额统计摘要", tags=["基金限额"])
async def get_limits_summary():
    """获取限额统计信息"""
    try:
        summary = fund_db.get_fund_limits_summary()
        return {
            "status": "ok",
            "data": {
                "total": summary["total"],
                "suspended": summary["suspended"],
                "min_buy_10": summary["min_buy_10"],
                "min_buy_100": summary["min_buy_100"],
                "max_buy_1000": summary["max_buy_1000"],
                "total_available": float(summary["total_available"]) if summary["total_available"] else 0,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询统计失败: {e}")


@router.get("/api/limits", summary="批量查询限额", tags=["基金限额"])
async def get_fund_limits(
    codes: str = Query(None, description="基金代码列表，逗号分隔"),
    min_buy_lte: float = Query(None, description="最小买入金额上限"),
    include_suspended: bool = Query(False, description="是否包含暂停申购的基金")
):
    """批量查询基金限额信息"""
    try:
        fund_codes = codes.split(",") if codes else None
        limits = fund_db.get_fund_limits(
            fund_codes=fund_codes,
            min_buy_lte=min_buy_lte,
            include_suspended=include_suspended
        )
        return {
            "status": "ok",
            "count": len(limits),
            "data": [
                {
                    "fund_code": l["fund_code"],
                    "fund_name": l["fund_name"],
                    "min_buy": float(l["min_buy"]) if l["min_buy"] else 0,
                    "max_buy": float(l["max_buy"]) if l["max_buy"] else 0,
                    "is_suspended": l["is_suspended"],
                    "last_checked_at": str(l["last_checked_at"]) if l["last_checked_at"] else None,
                }
                for l in limits
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询限额失败: {e}")


@router.get("/api/limits/{fund_code}", summary="查询单个基金限额", tags=["基金限额"])
async def get_fund_limit(fund_code: str):
    """查询单个基金的买入限额信息"""
    try:
        limit_info = fund_db.get_fund_limit(fund_code)
        if not limit_info:
            return {"status": "not_found", "fund_code": fund_code, "message": "暂无限额信息，需要先尝试买入获取"}
        return {
            "status": "ok",
            "data": {
                "fund_code": limit_info["fund_code"],
                "fund_name": limit_info["fund_name"],
                "min_buy": float(limit_info["min_buy"]) if limit_info["min_buy"] else 0,
                "max_buy": float(limit_info["max_buy"]) if limit_info["max_buy"] else 0,
                "daily_limit": float(limit_info["daily_limit"]) if limit_info["daily_limit"] else 0,
                "is_suspended": limit_info["is_suspended"],
                "suspend_reason": limit_info["suspend_reason"],
                "last_checked_at": str(limit_info["last_checked_at"]) if limit_info["last_checked_at"] else None,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询限额失败: {e}")


@router.post("/api/limits/plan", summary="智能分配购买计划", tags=["基金限额"])
async def plan_buy(
    target_amount: float = Body(..., description="目标总金额"),
    exclude_codes: list = Body(default=[], description="排除的基金代码"),
    fund_codes: list = Body(default=None, description="限定的基金代码范围")
):
    """根据已知限额智能分配购买计划"""
    try:
        result, remaining = fund_db.get_buyable_funds(
            target_amount=target_amount,
            exclude_codes=exclude_codes
        )

        # 如果指定了基金代码范围，只保留范围内的
        if fund_codes:
            result = [r for r in result if r["fund_code"] in fund_codes]
            # 重新计算剩余
            allocated = sum(r["suggested_amount"] for r in result)
            remaining = target_amount - allocated

        return {
            "status": "ok",
            "target_amount": target_amount,
            "allocated_amount": target_amount - remaining,
            "remaining_amount": remaining,
            "plan": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成购买计划失败: {e}")


# ==================== 数据采集和同步模块 ====================

@router.post("/api/sync/positions", summary="同步持仓", tags=["数据同步"])
async def sync_positions():
    """从同花顺同步持仓到本地数据库"""
    try:
        positions_data = await client.get_fund_positions()

        synced_count = 0
        result = positions_data.get("result", {})
        single_data = result.get("singleData", {})
        fund_general = single_data.get("fundGeneral", {})
        fund_list = fund_general.get("fundPositonCombinedList", [])

        for pos in fund_list:
            fund_db.update_position(
                fund_code=pos["fundCode"],
                fund_name=pos["fundName"],
                shares=float(pos.get("holdVol") or 0),
                total_cost=float(pos.get("totalAmount") or 0),
                market_value=float(pos.get("shareValue") or 0),
                profit_pct=float(pos.get("holdIncomeRate") or 0)
            )
            synced_count += 1

        return {
            "status": "success",
            "message": f"同步了 {synced_count} 条持仓记录"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步持仓失败: {e}")


@router.get("/api/account/overview", summary="账户总览", tags=["账户信息"])
async def get_account_overview():
    """获取账户总览（总资产、收益等）"""
    try:
        overview = await client.get_account_overview()
        return {
            "status": "success",
            "data": overview
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取账户总览失败: {e}")


@router.get("/api/wallet/info", summary="钱包信息", tags=["账户信息"])
async def get_wallet_info():
    """获取钱包余额信息"""
    try:
        wallet = await client.get_wallet_info()
        return {
            "status": "success",
            "data": wallet
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取钱包信息失败: {e}")


@router.get("/api/wallet/home", summary="钱包首页", tags=["账户信息"])
async def get_wallet_home():
    """获取钱包首页完整信息"""
    try:
        home = await client.get_wallet_home()
        return {
            "status": "success",
            "data": home
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取钱包首页失败: {e}")


@router.get("/api/funds/scan", summary="基金扫描（完整版）", tags=["基金数据"])
async def scan_all_funds():
    """扫描基金池中所有基金的详细数据（完整版，数据量大）"""
    try:
        positions = fund_db.get_positions()
        fund_codes = [p["fund_code"] for p in positions]

        funds_data = []
        for code in fund_codes:
            try:
                detail = await client.get_fund_detail(code)
                funds_data.append(detail)
            except:
                continue

        return {
            "status": "success",
            "data": {"funds": funds_data},
            "count": len(funds_data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"基金扫描失败: {e}")


@router.get("/api/funds/scan-summary", summary="基金扫描（精简版）", tags=["基金数据"])
async def scan_funds_summary():
    """扫描基金池中所有基金的关键数据（精简版，约2KB）"""
    try:
        positions = fund_db.get_positions()
        fund_codes = [p["fund_code"] for p in positions]

        funds_summary = []
        for code in fund_codes:
            try:
                base = await client.get_fund_base(code)
                info = await client.get_fund_info(code)

                base_data = base.get("data", {})
                info_data = info.get("data", {})

                nav = info_data.get("net")
                if nav:
                    try:
                        nav = float(nav)
                    except (ValueError, TypeError):
                        nav = None

                rate = info_data.get("week")
                if rate:
                    try:
                        rate = float(rate)
                    except (ValueError, TypeError):
                        rate = None

                yield_month = info_data.get("month")
                if yield_month:
                    try:
                        yield_month = float(yield_month)
                    except (ValueError, TypeError):
                        yield_month = None

                funds_summary.append({
                    "code": code,
                    "name": base_data.get("simpleName") or info_data.get("name"),
                    "type": base_data.get("fundType"),
                    "nav": nav,
                    "rate": rate,
                    "yield_month": yield_month,
                    "risk": base_data.get("riskLevel")
                })
            except:
                continue

        return {
            "status": "success",
            "data": {"funds": funds_summary},
            "count": len(funds_summary)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"基金扫描失败: {e}")
