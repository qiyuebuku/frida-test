"""T-R3.5/R3.6 单元测试: application/services + dto"""
import inspect

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
    assert CollectionAppService() is not None
    assert ExtractionAppService() is not None
    assert TradingAppService() is not None
    assert ReflectionAppService() is not None


@pytest.mark.unit
def test_r3_5_app_services_method_signatures():
    """T-R3.5-3: 每个 app service 的方法是 async 且返回 dto"""
    from src.application.services import (
        CollectionAppService,
        ExtractionAppService,
        ReflectionAppService,
        TradingAppService,
    )

    for name in [
        "run_news_collection", "run_fund_flow_collection",
        "run_market_collection", "run_sentiment_collection", "run_macro_collection",
    ]:
        m = getattr(CollectionAppService, name, None)
        assert m is not None, f"CollectionAppService 缺 {name}"
        assert inspect.iscoroutinefunction(m), f"{name} 应是 async"

    for name in ["extract_events_from_news", "aggregate_event_streams", "backfill_market_reaction"]:
        assert inspect.iscoroutinefunction(getattr(ExtractionAppService, name))

    for name in ["score_event_streams", "execute_pending_decisions", "monitor_positions"]:
        assert inspect.iscoroutinefunction(getattr(TradingAppService, name))

    assert inspect.iscoroutinefunction(getattr(ReflectionAppService, "run_review"))
