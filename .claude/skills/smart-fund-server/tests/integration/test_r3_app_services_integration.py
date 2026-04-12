"""T-R3.6 集成测试: application services 实际跑通"""
import asyncio

import pytest


@pytest.mark.integration
def test_r3_6_collection_app_service_runs():
    """T-R3.6-1: CollectionAppService.run_market_collection() 实际跑通"""
    from src.application.services import CollectionAppService
    from src.application.dto import CollectionResult

    svc = CollectionAppService()
    result = asyncio.run(svc.run_market_collection())
    assert isinstance(result, CollectionResult)
    assert result.aggregator == "market"
    assert result.sources_run >= 0
    assert result.total_saved >= 0


@pytest.mark.integration
def test_r3_6_trading_app_service_dry_run():
    """T-R3.6-2: TradingAppService.execute_pending_decisions() 实际 dry_run 跑通"""
    from src.application.services import TradingAppService
    from src.application.dto import ExecutionResult

    svc = TradingAppService()
    result = asyncio.run(svc.execute_pending_decisions())
    assert isinstance(result, ExecutionResult)
    assert result.live == 0


@pytest.mark.integration
def test_r3_6_reflection_app_service_runs():
    """T-R3.6-3: ReflectionAppService.run_review() 实际跑通"""
    from src.application.services import ReflectionAppService
    from src.application.dto import ReviewResult

    svc = ReflectionAppService()
    result = asyncio.run(svc.run_review())
    assert isinstance(result, ReviewResult)
    assert result.trades >= 0
