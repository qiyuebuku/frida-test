"""交易决策应用服务

3 个 use case:
- score_event_streams: ft_event_streams → 打分 → ft_pending_decisions
- execute_pending_decisions: ft_pending_decisions → ft_trades (默认 dry_run)
- monitor_positions: 持仓监控 → 触发卖出/加仓决策

⚠️ execute_pending_decisions 默认 dry_run=true。
LIVE 模式需要环境变量 EXEC_DRY_RUN=false + LIVE_FUND_WHITELIST + 单笔/单日上限。

R4: 加 prometheus metrics 上报
"""
from src.application.dto.trading_dto import (
    DecisionResult,
    ExecutionResult,
    MonitorResult,
)
from src.infrastructure.observability import (
    get_logger,
    record_decision,
    record_trade,
)

logger = get_logger(__name__)


class TradingAppService:
    """交易决策 use case 入口"""

    async def score_event_streams(self) -> DecisionResult:
        """事件驱动决策打分 (trade_decision task)"""
        from src.domain.trading.services.event_driven_decider import EventDrivenDecider
        result = await EventDrivenDecider().decide_all() or {}
        decisions = result.get("decisions", 0)
        record_decision("buy", decisions)  # 简化: 大部分是 buy
        return DecisionResult(
            streams=result.get("streams", 0),
            decisions=decisions,
            skipped=result.get("skipped", 0),
        )

    async def execute_pending_decisions(self) -> ExecutionResult:
        """执行 pending 决策 (trade_execution task)

        ⚠️ 安全闸: 默认 dry_run, LIVE 需要多个环境变量同时满足。
        """
        from src.domain.trading.services.trade_executor import TradeExecutor
        result = await TradeExecutor().execute_pending() or {}
        dry = result.get("dry_run", 0)
        live = result.get("live", 0)
        record_trade("buy", dry_run=True, count=dry)
        record_trade("buy", dry_run=False, count=live)
        return ExecutionResult(
            total=result.get("total", 0),
            executed=result.get("executed", 0),
            dry_run=dry,
            live=live,
            failed=result.get("failed", 0),
        )

    async def monitor_positions(self) -> MonitorResult:
        """持仓监控 (trade_monitor task)"""
        from src.domain.trading.services.trade_monitor import TradeMonitor
        result = await TradeMonitor().monitor_all() or {}
        return MonitorResult(
            positions=result.get("positions", 0),
            stop_loss=result.get("stop_loss", 0),
            decay_sell=result.get("decay_sell", 0),
            add_position=result.get("add_position", 0),
        )
