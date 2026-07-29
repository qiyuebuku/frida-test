"""T-R4.1 + T-R4.2 单元测试: observability (logging + metrics)"""
import logging
import time

import pytest


# ==================== R4.1 logging ====================


@pytest.mark.unit
def test_r4_1_get_logger_inherits_root():
    """T-R4.1-1: get_logger 返回 logger,自动继承 root 配置"""
    from src.infrastructure.observability import configure_logging, get_logger

    configure_logging(level="INFO")
    log = get_logger("test.r4.1")
    log.info("hello")
    assert log.name == "test.r4.1"
    assert log.isEnabledFor(logging.INFO)


@pytest.mark.unit
def test_r4_1_configure_idempotent():
    """T-R4.1-2: configure_logging 幂等(重复调不重复添加 handler)"""
    from src.infrastructure.observability import configure_logging

    configure_logging(level="INFO")
    n1 = len(logging.getLogger().handlers)
    configure_logging(level="DEBUG")
    n2 = len(logging.getLogger().handlers)
    configure_logging(level="WARNING")
    n3 = len(logging.getLogger().handlers)
    assert n1 == n2 == n3, f"handler 数变了: {n1}/{n2}/{n3}"


@pytest.mark.unit
def test_r4_1_third_party_quieted():
    """T-R4.1-3: quiet_third_party 把 httpx 等降到 WARNING"""
    from src.infrastructure.observability import configure_logging

    configure_logging(level="INFO", quiet_third_party=True)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


# ==================== R4.2 metrics ====================


@pytest.mark.unit
def test_r4_2_record_collection_increments_counter():
    """T-R4.2-1: record_collection 上报后 metrics_text 包含数据"""
    from src.infrastructure.observability import (
        COLLECTION_SAVED,
        metrics_text,
        record_collection,
    )

    record_collection("test_agg", duration_s=1.5, saved=10)
    record_collection("test_agg", duration_s=2.0, saved=5)
    body, ctype = metrics_text()
    text = body.decode()
    assert "collection_duration_seconds" in text
    assert "collection_saved_total" in text
    assert 'aggregator="test_agg"' in text


@pytest.mark.unit
def test_r4_2_record_decision_buy():
    """T-R4.2-2: record_decision 累加"""
    from src.infrastructure.observability import metrics_text, record_decision

    record_decision("buy", count=3)
    body, _ = metrics_text()
    assert "decision_count_total" in body.decode()


@pytest.mark.unit
def test_r4_2_record_trade_dry_vs_live():
    """T-R4.2-3: record_trade 区分 dry_run / live"""
    from src.infrastructure.observability import metrics_text, record_trade

    record_trade("buy", dry_run=True, count=5)
    record_trade("buy", dry_run=False, count=0)  # 0 不上报
    body, _ = metrics_text()
    text = body.decode()
    assert "dry_run_trade_count_total" in text


@pytest.mark.unit
def test_r4_2_collection_timer_context():
    """T-R4.2-4: CollectionTimer 上下文管理器"""
    from src.infrastructure.observability.metrics import CollectionTimer

    with CollectionTimer("test_timer") as t:
        time.sleep(0.01)
        t.saved = 7


@pytest.mark.unit
def test_r4_2_metrics_text_format():
    """T-R4.2-5: metrics_text 返回 prometheus text 格式"""
    from src.infrastructure.observability import metrics_text

    body, ctype = metrics_text()
    assert isinstance(body, bytes)
    assert "text/plain" in ctype or "openmetrics" in ctype.lower()
    assert b"# HELP" in body or b"# TYPE" in body


@pytest.mark.unit
def test_langfuse_propagated_metadata_clips_long_values(monkeypatch):
    """Langfuse propagated attributes have a short value limit; metadata must not trigger SDK drops."""
    from src.infrastructure.observability.langfuse_tracing import _attribute_metadata

    monkeypatch.setenv("KG_LANGFUSE_PROPAGATED_ATTRIBUTE_MAX_CHARS", "80")
    metadata = _attribute_metadata(
        {
            "source_samples": [
                {
                    "source_type": "news_articles",
                    "source_id": f"ft_news:{index}",
                    "title": "A股并购重组市场呈现三方面新变化" * 3,
                }
                for index in range(5)
            ],
            "query": "半导体产业链整合影响" * 20,
        }
    )

    assert set(metadata) == {"source_samples", "query"}
    assert all(len(value) <= 80 for value in metadata.values())
    assert all("[truncated sha256=" in value for value in metadata.values())
