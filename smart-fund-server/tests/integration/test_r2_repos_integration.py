"""T-R2.x 集成测试: 6 个 collection repository CRUD (用 jettask_test 库)"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.infrastructure.connections import get_session, set_target


@pytest.fixture(autouse=True)
def use_test_db():
    """所有测试切换到 jettask_test"""
    set_target("test")
    yield
    set_target("prod")


@pytest.fixture
def clean_test_tables():
    """每个测试前清空相关表"""
    with get_session() as s:
        s.execute(text("TRUNCATE ft_news, ft_market_flow, ft_market_cache, "
                       "ft_sentiment, ft_macro_indicators, ft_collection_state "
                       "RESTART IDENTITY CASCADE"))
    yield


@pytest.mark.integration
def test_r2_2_news_upsert_and_find(clean_test_tables):
    """T-R2.2-news: NewsRepositoryImpl upsert + find_unextracted + mark_extracted"""
    from src.infrastructure.persistence.repositories import NewsRepositoryImpl

    repo = NewsRepositoryImpl()
    items = [
        {
            "title": "央行降准 0.5 个百分点",
            "source": "test",
            "fingerprint": "fp_test_1",
            "published_at": datetime.now(timezone.utc),
            "tags": ["政策", "货币"],
            "related_stocks": ["sh600036"],
            "category": "policy",
            "url": "https://example.com/1",
            "content": "正文...",
            "summary": "摘要",
            "source_name": "测试源",
            "source_reliability": 0.8,
        },
        {
            "title": "美联储加息 25bp",
            "source": "test",
            "fingerprint": "fp_test_2",
            "published_at": datetime.now(timezone.utc),
            "tags": [],
            "related_stocks": [],
        },
    ]

    n = repo.upsert_batch(items)
    assert n == 2

    n2 = repo.upsert_batch(items)
    assert n2 == 0

    rows = repo.find_unextracted(limit=10)
    assert len(rows) == 2
    assert all(r["title"] for r in rows)

    ids = [r["id"] for r in rows]
    n3 = repo.mark_extracted(ids)
    assert n3 == 2

    assert len(repo.find_unextracted()) == 0
    assert repo.count() == 2


@pytest.mark.integration
def test_r2_2_market_flow_upsert_dedupe(clean_test_tables):
    """T-R2.2-market_flow: 4 个 partial unique 自动按 data_type 去重"""
    from src.infrastructure.persistence.repositories import MarketFlowRepositoryImpl

    repo = MarketFlowRepositoryImpl()
    today = date.today()
    items = [
        {"data_type": "stock_flow", "trade_date": today,
         "data": {"code": "sh600036", "today": {"mainNetIn": 1000}}},
        {"data_type": "sector_flow", "trade_date": today,
         "data": {"name": "银行", "net_amount": 5000}},
        {"data_type": "northbound", "trade_date": today,
         "data": {"mutual_type": "005", "net_flow": 1234.5}},
        {"data_type": "dragon_tiger", "trade_date": today,
         "data": {"source": "ths", "code": "sh600036", "name": "招行"}},
    ]
    n = repo.upsert_batch(items)
    assert n == 4

    n2 = repo.upsert_batch(items)
    assert n2 == 0

    counts = repo.count_by_type()
    assert counts == {"stock_flow": 1, "sector_flow": 1, "northbound": 1, "dragon_tiger": 1}

    assert repo.max_trade_date("stock_flow") == today
    assert repo.max_trade_date("not_exist") is None


@pytest.mark.integration
def test_r2_2_market_cache_upsert_overwrite(clean_test_tables):
    """T-R2.2-market_cache: 覆盖式 upsert (unique on data_type)"""
    from src.infrastructure.persistence.repositories import MarketCacheRepositoryImpl

    repo = MarketCacheRepositoryImpl()
    expires = datetime.now() + timedelta(hours=1)

    repo.upsert("sector_ranking", {"v": 1}, expires)
    assert repo.find_by_type("sector_ranking") == {"v": 1}

    repo.upsert("sector_ranking", {"v": 2}, expires)
    assert repo.find_by_type("sector_ranking") == {"v": 2}

    types = repo.all_types()
    assert types == ["sector_ranking"]


@pytest.mark.integration
def test_r2_2_sentiment_upsert(clean_test_tables):
    """T-R2.2-sentiment: 简单 INSERT"""
    from src.infrastructure.persistence.repositories import SentimentRepositoryImpl

    repo = SentimentRepositoryImpl()
    today = date.today()
    items = [
        {"data_type": "guba_popularity", "trade_date": today,
         "data": {"sh600036": 1000, "sh000001": 800}},
        {"data_type": "xueqiu_hot", "trade_date": today,
         "data": [{"code": "sh600036", "rank": 1}]},
    ]
    n = repo.upsert_batch(items)
    assert n == 2
    assert repo.count_by_type() == {"guba_popularity": 1, "xueqiu_hot": 1}


@pytest.mark.integration
def test_r2_2_macro_upsert(clean_test_tables):
    """T-R2.2-macro: ON CONFLICT (indicator, period, source) DO NOTHING"""
    from src.infrastructure.persistence.repositories import MacroRepositoryImpl

    repo = MacroRepositoryImpl()
    items = [
        {"indicator": "cpi", "period": "2026-03", "value": 2.1, "unit": "%",
         "source": "eastmoney", "published_at": date(2026, 4, 1)},
        {"indicator": "ppi", "period": "2026-03", "value": -1.5, "unit": "%",
         "source": "eastmoney", "published_at": date(2026, 4, 1)},
    ]
    assert repo.upsert_batch(items) == 2
    assert repo.upsert_batch(items) == 0

    latest = repo.latest_per_indicator()
    assert len(latest) == 2
    assert {x["indicator"] for x in latest} == {"cpi", "ppi"}


@pytest.mark.integration
def test_r2_2_collection_state_full_lifecycle(clean_test_tables):
    """T-R2.2-collection_state: get / update_success / update_failure / reset / disable"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()

    assert repo.get("news", "cls") is None
    assert repo.get_checkpoint("news", "cls") == {}
    assert repo.is_enabled("news", "cls") is True

    repo.update_success("news", "cls", {"mode": "backfill", "newest_time": "2026-04-11"}, saved_count=5)
    state = repo.get("news", "cls")
    assert state is not None
    assert state["mode"] == "backfill"
    assert state["newest_time"] == "2026-04-11"
    assert state["total_runs"] == 1
    assert state["total_saved"] == 5
    assert state["consecutive_failures"] == 0

    repo.update_success("news", "cls", {"newest_time": "2026-04-12"}, saved_count=3)
    state = repo.get("news", "cls")
    assert state["newest_time"] == "2026-04-12"
    assert state["total_runs"] == 2
    assert state["total_saved"] == 8

    repo.update_failure("news", "cls", "test error")
    state = repo.get("news", "cls")
    assert state["consecutive_failures"] == 1
    assert state["last_error"] == "test error"
    assert state["total_runs"] == 3

    assert repo.reset("news", "cls") is True
    cp = repo.get_checkpoint("news", "cls")
    assert cp["mode"] == "backfill"  # reset 后回到 backfill

    repo.disable("news", "cls")
    assert repo.is_enabled("news", "cls") is False
    repo.enable("news", "cls")
    assert repo.is_enabled("news", "cls") is True

    rows = repo.list_all("news")
    assert len(rows) == 1
    assert rows[0]["source_name"] == "cls"
