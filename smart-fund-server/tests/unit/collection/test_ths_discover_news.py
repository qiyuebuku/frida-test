import asyncio

from src.domain.collection.services.news import normalize_ths_discover
from src.domain.collection.services.news import NewsAggregator


def test_normalize_ths_discover_recommendation_extracts_content_and_stock() -> None:
    rows = normalize_ths_discover(
        [{
            "_discover_section": "recommend",
            "info": {"itemId": "2388064839", "jumpUrl": "https://example.test/post"},
            "combination": [
                {"author": {"name": "测试作者", "time": 1786026444000}},
                {"title": {"content": "推荐标题"}},
                {"largeAbstract": {"content": "<hx_stock>stockName:测试股,stockCode:300088,market:33</hx_stock> 推荐正文"}},
                {"stockLine": {"stockTags": [{"stockCode": "300088"}]}},
            ],
        }]
    )

    assert rows[0]["title"] == "推荐标题"
    assert "推荐正文" in rows[0]["content"]
    assert rows[0]["source"] == "ths_discover_recommend"
    assert rows[0]["related_stocks"] == ["300088"]


def test_normalize_ths_discover_hot_topic_preserves_description() -> None:
    rows = normalize_ths_discover(
        [{
            "_discover_section": "hot_topic",
            "code": "Tj4k0yi",
            "title": "宇树科技确定发行价！",
            "description": "热点说明",
            "jump_url": "//t.10jqka.com.cn/topic",
            "attach_info": {"att_stock": [{"code": "603799"}]},
        }]
    )

    assert rows[0]["content"] == "热点说明"
    assert rows[0]["source"] == "ths_discover_hot_topic"
    assert rows[0]["related_stocks"] == ["603799"]


def test_fetch_ths_discover_only_returns_unseen_ids(monkeypatch) -> None:
    class FakeTHS:
        async def get_headlines(self):
            return {"data": [{"seq": "100", "title": "头条"}]}

        async def get_discover_recommendations(self):
            return {"data": [{"info": {"itemId": "200"}, "title": "推荐"}]}

        async def get_discover_hot_topics(self):
            return {"data": {"topic_list": [{"code": "300", "title": "话题"}]}}

        async def get_discover_hot_posts(self, **_kwargs):
            return {"data": {"feed": [{"pid": 400, "title": "热文"}]}}

    from src.infrastructure import clients

    monkeypatch.setattr(clients, "ths", FakeTHS())
    aggregator = NewsAggregator.__new__(NewsAggregator)
    checkpoint = {"cursor": {"seen_ids": ["headline:100", "hot_topic:300"]}}

    rows = asyncio.run(aggregator._fetch_ths_discover(checkpoint))

    assert [row["_discover_section"] for row in rows] == ["recommend", "hot_post"]
    assert checkpoint["cursor"]["seen_ids"][:4] == [
        "headline:100", "recommend:200", "hot_topic:300", "hot_post:400"
    ]

    rows = asyncio.run(aggregator._fetch_ths_discover(checkpoint))
    assert rows == []
