"""T-R3.5/R3.6 单元测试: application/services + dto"""
import inspect

import pytest


@pytest.mark.unit
def test_r3_5_dto_imports():
    """T-R3.5-1: active dto files can import + dataclass works"""
    from src.application.dto import (
        CollectionResult,
    )
    cr = CollectionResult(aggregator="news", sources_run=1, total_saved=5)
    assert cr.to_dict() == {"aggregator": "news", "sources_run": 1, "total_saved": 5}


@pytest.mark.unit
def test_r3_5_app_services_importable():
    """T-R3.5-2: active app service can import"""
    from src.application.services import (
        CollectionAppService,
    )
    assert CollectionAppService() is not None


@pytest.mark.unit
def test_r3_5_app_services_method_signatures():
    """T-R3.5-3: collection app service methods are async"""
    from src.application.services import (
        CollectionAppService,
    )

    for name in [
        "run_news_collection", "run_fund_flow_collection",
        "run_market_collection", "run_sentiment_collection", "run_macro_collection",
    ]:
        m = getattr(CollectionAppService, name, None)
        assert m is not None, f"CollectionAppService 缺 {name}"
        assert inspect.iscoroutinefunction(m), f"{name} 应是 async"
