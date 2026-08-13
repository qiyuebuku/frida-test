import pytest

from src.domain.collection.services.news import (
    NewsAggregator,
    normalize_cls_hot_articles,
)
from src.infrastructure import clients


class _FakeCLSClient:
    def __init__(self, ranked_items: list[dict], details: dict[int, dict | Exception]):
        self.ranked_items = ranked_items
        self.details = details
        self.detail_calls: list[int] = []

    async def get_hot_article_list(self) -> list[dict]:
        return self.ranked_items

    async def get_article_detail(self, article_id: int) -> dict:
        self.detail_calls.append(article_id)
        result = self.details[article_id]
        if isinstance(result, Exception):
            raise result
        return result


def _detail(article_id: int, title: str = "热门文章") -> dict:
    return {
        "id": article_id,
        "status": 1,
        "title": title,
        "brief": "文章摘要",
        "content": "<p>第一段&nbsp;正文</p><p><strong>第二段</strong></p>",
        "ctime": 1785279600,
        "readingNum": 100,
        "author": {"name": "财联社"},
        "visibleTags": [{"id": 8, "name": "原创"}],
        "subject": [{"id": 1151, "name": "有声早报"}],
        "stocks": [{"stockId": "000001"}],
    }


def test_normalize_cls_hot_articles_uses_full_detail_content() -> None:
    items = normalize_cls_hot_articles([_detail(2439549, "热门文章一")])

    assert len(items) == 1
    item = items[0]
    assert item["source"] == "cls_hot_article"
    assert item["source_name"] == "财联社热门文章"
    assert item["content"] == "第一段 正文\n第二段"
    assert item["summary"] == "文章摘要"
    assert item["url"] == "https://www.cls.cn/detail/2439549"
    assert item["tags"] == ["原创", "有声早报"]
    assert item["related_stocks"] == ["000001"]
    assert item["published_at"] == "2026-07-29T07:00:00+08:00"


def test_save_preserves_hot_article_when_same_title_exists_from_another_source(monkeypatch) -> None:
    class _FakeNewsRepository:
        def __init__(self):
            self.records = []

        def upsert_batch_returning_ids(self, records: list[dict]) -> list[int]:
            self.records = records
            return [101] if records else []

        def find_existing_content_fingerprints(
            self,
            _fingerprints: list[str],
        ) -> set[str]:
            return set()

    repository = _FakeNewsRepository()
    monkeypatch.setattr(NewsAggregator, "_query_today_titles", lambda _self: ["热门文章一"])
    monkeypatch.setattr(NewsAggregator, "_news_repo", staticmethod(lambda: repository))
    aggregator = NewsAggregator.__new__(NewsAggregator)
    aggregator.last_saved_ids = []

    saved = aggregator._save(normalize_cls_hot_articles([_detail(2439549, "热门文章一")]))

    assert saved == 1
    assert repository.records[0]["source"] == "cls_hot_article"
    assert repository.records[0]["news_kind"] == "news"
    assert aggregator.last_saved_ids == [101]


def test_news_kind_classifies_market_recap_and_preview() -> None:
    assert NewsAggregator._classify_news_kind(
        {"title": "A股收评：三大指数集体下跌", "source": "cls"}
    ) == "market_recap"
    assert NewsAggregator._classify_news_kind(
        {"title": "盘前必读：今日重要消息", "source": "cls"}
    ) == "market_preview"


def test_news_dedup_key_is_cross_source_but_date_scoped() -> None:
    left = NewsAggregator._dedup_key(
        {
            "title": "A股 收盘！",
            "published_at": "2026-07-31T15:00:00+08:00",
            "source": "cls",
        }
    )
    right = NewsAggregator._dedup_key(
        {
            "title": "A股收盘",
            "published_at": "2026-07-31T15:01:00+08:00",
            "source": "sina",
        }
    )
    next_day = NewsAggregator._dedup_key(
        {
            "title": "A股收盘",
            "published_at": "2026-08-01T15:00:00+08:00",
            "source": "sina",
        }
    )

    assert left == right
    assert left != next_day


@pytest.mark.asyncio
async def test_fetch_cls_hot_articles_skips_details_when_ranking_has_no_new_ids(monkeypatch) -> None:
    fake_client = _FakeCLSClient(
        ranked_items=[{"id": 2}, {"id": 1}],
        details={},
    )
    monkeypatch.setattr(clients, "cls", fake_client)
    aggregator = NewsAggregator.__new__(NewsAggregator)
    checkpoint = {
        "cursor": {"seen_ids": [1, 2]},
        "_config": {"seen_ids_limit": 1000, "detail_concurrency": 5},
    }

    result = await aggregator._fetch_cls_hot_articles(checkpoint)

    assert result == []
    assert fake_client.detail_calls == []
    assert checkpoint["cursor"] == {"seen_ids": [1, 2]}


@pytest.mark.asyncio
async def test_fetch_cls_hot_articles_fetches_only_new_ids_and_updates_checkpoint(monkeypatch) -> None:
    fake_client = _FakeCLSClient(
        ranked_items=[{"id": 3}, {"id": 2}, {"id": 1}],
        details={3: _detail(3)},
    )
    monkeypatch.setattr(clients, "cls", fake_client)
    aggregator = NewsAggregator.__new__(NewsAggregator)
    checkpoint = {
        "cursor": {"seen_ids": [1, 2]},
        "_config": {"seen_ids_limit": 1000, "detail_concurrency": 5},
    }

    result = await aggregator._fetch_cls_hot_articles(checkpoint)

    assert [item["id"] for item in result] == [3]
    assert fake_client.detail_calls == [3]
    assert checkpoint["cursor"] == {"seen_ids": [3, 1, 2]}


def test_hot_article_seen_ids_are_preserved_by_checkpoint_computation() -> None:
    aggregator = NewsAggregator.__new__(NewsAggregator)
    checkpoint = {
        "mode": "incremental",
        "cursor": {"seen_ids": [3, 1, 2]},
    }
    items = normalize_cls_hot_articles([_detail(3)])

    updated = aggregator._compute_checkpoint("cls_hot_article", items, checkpoint)

    assert updated["cursor"] == {"seen_ids": [3, 1, 2]}
    assert updated["newest_time"] == "2026-07-29T07:00:00+08:00"


@pytest.mark.asyncio
async def test_fetch_cls_hot_articles_retries_failed_details_on_next_run(monkeypatch) -> None:
    fake_client = _FakeCLSClient(
        ranked_items=[{"id": 4}, {"id": 3}, {"id": 2}],
        details={4: RuntimeError("temporary failure"), 3: _detail(3)},
    )
    monkeypatch.setattr(clients, "cls", fake_client)
    aggregator = NewsAggregator.__new__(NewsAggregator)
    checkpoint = {
        "cursor": {"seen_ids": [2]},
        "_config": {"seen_ids_limit": 1000, "detail_concurrency": 5},
    }

    result = await aggregator._fetch_cls_hot_articles(checkpoint)

    assert [item["id"] for item in result] == [3]
    assert fake_client.detail_calls == [4, 3]
    assert checkpoint["cursor"] == {"seen_ids": [3, 2]}
