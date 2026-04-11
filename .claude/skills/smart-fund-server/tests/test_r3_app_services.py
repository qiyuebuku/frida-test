"""T-R3.5/R3.6 测试: application/services + dto"""
import asyncio

import pytest


@pytest.mark.unit
def test_r3_5_dto_imports():
    """T-R3.5-1: 4 个 dto 文件可 import + dataclass 工作"""
    from src.application.dto import (
        CollectionResult,
        DecisionResult,
        EventExtractionResult,
        EventStreamResult,
        ExecutionResult,
        FeedbackResult,
        MonitorResult,
        ReviewResult,
    )
    # to_dict 工作
    cr = CollectionResult(aggregator="news", sources_run=1, total_saved=5)
    assert cr.to_dict() == {"aggregator": "news", "sources_run": 1, "total_saved": 5}

    dr = DecisionResult(streams=10, decisions=3)
    assert dr.to_dict()["decisions"] == 3


@pytest.mark.unit
def test_r3_5_app_services_importable():
    """T-R3.5-2: 4 个 app service 可 import"""
    from src.application.services import (
        CollectionAppService,
        ExtractionAppService,
        ReflectionAppService,
        TradingAppService,
    )
    # 实例化
    assert CollectionAppService() is not None
    assert ExtractionAppService() is not None
    assert TradingAppService() is not None
    assert ReflectionAppService() is not None


@pytest.mark.unit
def test_r3_5_app_services_method_signatures():
    """T-R3.5-3: 每个 app service 的方法是 async 且返回 dto"""
    import inspect

    from src.application.services import (
        CollectionAppService,
        ExtractionAppService,
        ReflectionAppService,
        TradingAppService,
    )

    # CollectionAppService 5 个 use case
    csvc = CollectionAppService
    for name in [
        "run_news_collection", "run_fund_flow_collection",
        "run_market_collection", "run_sentiment_collection", "run_macro_collection",
    ]:
        m = getattr(csvc, name, None)
        assert m is not None, f"CollectionAppService 缺 {name}"
        assert inspect.iscoroutinefunction(m), f"{name} 应是 async"

    # ExtractionAppService 3 个
    for name in ["extract_events_from_news", "aggregate_event_streams", "backfill_market_reaction"]:
        assert inspect.iscoroutinefunction(getattr(ExtractionAppService, name))

    # TradingAppService 3 个
    for name in ["score_event_streams", "execute_pending_decisions", "monitor_positions"]:
        assert inspect.iscoroutinefunction(getattr(TradingAppService, name))

    # ReflectionAppService 1 个
    assert inspect.iscoroutinefunction(getattr(ReflectionAppService, "run_review"))


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
    # live 必须为 0(全局 dry_run 默认开)
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
