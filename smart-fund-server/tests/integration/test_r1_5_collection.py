"""T-R1.5 测试: 6 个采集层 ORM 模型只读 smoke + JSONB + 字段类型对账"""
from datetime import date, datetime

import pytest
from sqlalchemy import select


@pytest.mark.integration
def test_r1_5_1_six_models_select_one():
    """T-R1.5-1: 对 6 个采集模型每个跑 select(M).limit(1)(连真实生产库只读)"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import (
        CollectionState, MacroIndicator, MarketCache, MarketFlow, News, Sentiment,
    )

    models = [News, MarketFlow, MarketCache, Sentiment, MacroIndicator, CollectionState]
    with get_session("prod") as s:
        for M in models:
            row = s.scalars(select(M).limit(1)).first()
            # 不强制有数据,但只要不抛异常就算 OK(字段类型映射正确)
            if row is not None:
                # 能 repr 出来 → __repr__ 字段访问全部成功
                assert repr(row)


@pytest.mark.integration
def test_r1_5_2_jsonb_auto_deserialize():
    """T-R1.5-2: JSONB 字段直接当 list/dict 用,不需要 json.loads"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import (
        CollectionState, MarketFlow, News,
    )

    with get_session("prod") as s:
        # News.tags 应该是 list (即使为空)
        news = s.scalars(select(News).where(News.tags != None).limit(1)).first()  # noqa: E711
        if news:
            assert isinstance(news.tags, list), f"News.tags 类型 {type(news.tags)},应为 list"
            assert isinstance(news.related_stocks, list)

        # MarketFlow.data 应该是 dict
        mf = s.scalars(select(MarketFlow).limit(1)).first()
        if mf:
            assert isinstance(mf.data, dict), f"MarketFlow.data 类型 {type(mf.data)}"

        # CollectionState.config 应该是 dict, mode 应该是 str
        cs = s.scalars(select(CollectionState).limit(1)).first()
        if cs:
            assert isinstance(cs.config, dict), f"config 类型 {type(cs.config)}"
            assert cs.mode in ("backfill", "incremental", None), f"mode={cs.mode}"


@pytest.mark.integration
def test_r1_5_3_field_types_match():
    """T-R1.5-3: 字段类型与 PG schema 对应

    News.published_at → datetime (tz-aware)
    Sentiment.trade_date → date
    MacroIndicator.value → float
    """
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import (
        MacroIndicator, News, Sentiment,
    )

    with get_session("prod") as s:
        news = s.scalars(select(News).limit(1)).first()
        if news:
            assert isinstance(news.published_at, datetime), \
                f"News.published_at 类型 {type(news.published_at)}"
            assert news.published_at.tzinfo is not None, "published_at 应是 tz-aware"

        sent = s.scalars(select(Sentiment).limit(1)).first()
        if sent:
            assert isinstance(sent.trade_date, date) and not isinstance(sent.trade_date, datetime), \
                f"Sentiment.trade_date 类型 {type(sent.trade_date)},应是纯 date"

        macro = s.scalars(select(MacroIndicator).limit(1)).first()
        if macro:
            assert isinstance(macro.value, float), f"MacroIndicator.value 类型 {type(macro.value)}"
