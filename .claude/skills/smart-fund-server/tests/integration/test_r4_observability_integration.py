"""T-R4.2 集成测试: CollectionAppService 运行后 metrics 有数据"""
import asyncio

import pytest


@pytest.mark.integration
def test_r4_2_app_service_emits_metrics():
    """T-R4.2-int: CollectionAppService 真实运行后 metrics 有数据"""
    from src.application.services import CollectionAppService
    from src.infrastructure.observability import metrics_text

    asyncio.run(CollectionAppService().run_market_collection())
    body, _ = metrics_text()
    text = body.decode()
    assert 'aggregator="market"' in text
