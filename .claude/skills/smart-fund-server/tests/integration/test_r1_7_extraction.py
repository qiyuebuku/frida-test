"""T-R1.7 测试: 2 个事件抽取层 ORM 模型只读 smoke"""
import pytest
from sqlalchemy import select


@pytest.mark.integration
def test_r1_7_1_extraction_models_select_one():
    """T-R1.7-1: Event / EventStream 只读 smoke + bytea + ARRAY 字段"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import Event, EventStream

    with get_session("prod") as s:
        # Event
        event = s.scalars(select(Event).limit(1)).first()
        if event:
            assert repr(event)
            assert isinstance(event.industries, list)
            assert isinstance(event.companies, list)
            # source_news_ids 是 ARRAY[Integer] → list[int]
            if event.source_news_ids:
                assert isinstance(event.source_news_ids, list)
                assert all(isinstance(x, int) for x in event.source_news_ids)
            # embedding 可能是 None 或 bytes(R1 阶段只验证类型)
            if event.embedding is not None:
                assert isinstance(event.embedding, (bytes, memoryview))

        # EventStream
        stream = s.scalars(select(EventStream).limit(1)).first()
        if stream:
            assert repr(stream)
            if stream.event_ids:
                assert isinstance(stream.event_ids, list)


@pytest.mark.integration
def test_r1_7_2_event_with_embedding_filter():
    """T-R1.7-2: 能用 ORM 过滤有 embedding 的事件"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import Event

    with get_session("prod") as s:
        rows = s.scalars(
            select(Event).where(Event.embedding.isnot(None)).limit(3)
        ).all()
        # 不强制必须有数据
        for r in rows:
            assert r.embedding is not None
            # bytea 长度应该是 4096 (1024 维 fp32)
            assert len(r.embedding) > 0
