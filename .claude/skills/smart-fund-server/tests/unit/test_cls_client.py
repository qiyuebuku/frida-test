import httpx
import pytest

from src.infrastructure.clients.cls import CLSClient


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_hot_article_list_uses_signed_web_request_and_preserves_ranking() -> None:
    expected_items = [
        {
            "id": 2439549,
            "title": "热门文章一",
            "brief": "摘要一",
            "img": "https://image.cls.cn/one.jpg",
            "ctime": 1785279600,
            "readNum": 178605,
            "author": "财联社",
            "stocks": "",
        },
        {
            "id": 2439531,
            "title": "热门文章二",
            "brief": "摘要二",
            "img": "https://image.cls.cn/two.jpg",
            "ctime": 1785276882,
            "readNum": 203792,
            "author": "财联社 记者",
            "stocks": "",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/article/hot/list"
        assert dict(request.url.params) == {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": "8.7.9",
            "sign": "b02d8f7bc4c45eeb3e86904203597da2",
        }
        assert request.headers["referer"] == "https://www.cls.cn/depth?id=1000"
        return httpx.Response(
            200,
            request=request,
            json={"errno": 0, "msg": "", "data": expected_items},
        )

    client = CLSClient()
    await client._client.aclose()
    client._client = _mock_client(handler)
    try:
        result = await CLSClient.get_hot_article_list.__wrapped__(client)
    finally:
        await client.close()

    assert result == expected_items


@pytest.mark.asyncio
async def test_get_hot_article_list_raises_for_cls_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"errno": 1001, "msg": "invalid sign", "data": []},
        )

    client = CLSClient()
    await client._client.aclose()
    client._client = _mock_client(handler)
    try:
        with pytest.raises(RuntimeError, match=r"errno=1001 msg=invalid sign"):
            await CLSClient.get_hot_article_list.__wrapped__(client)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_hot_article_list_returns_empty_list_for_non_list_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"errno": 0, "msg": "", "data": None},
        )

    client = CLSClient()
    await client._client.aclose()
    client._client = _mock_client(handler)
    try:
        result = await CLSClient.get_hot_article_list.__wrapped__(client)
    finally:
        await client.close()

    assert result == []


@pytest.mark.asyncio
async def test_get_article_detail_accepts_detail_url_and_returns_full_article() -> None:
    expected_detail = {
        "id": 2439549,
        "status": 1,
        "title": "热门文章一",
        "brief": "摘要一",
        "content": "<p>完整正文</p>",
        "ctime": 1785279600,
        "readingNum": 178874,
        "author": {"name": "财联社"},
        "images": ["https://image.cls.cn/one.jpg"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/articles/v1/detail"
        assert dict(request.url.params) == {
            "app": "0",
            "id": "2439549",
            "os": "web",
            "sv": "8.7.9",
            "sign": "afd1425f4d435d77c45d8d21861325cf",
        }
        assert request.headers["referer"] == "https://www.cls.cn/detail/2439549"
        return httpx.Response(200, request=request, json=expected_detail)

    client = CLSClient()
    await client._client.aclose()
    client._client = _mock_client(handler)
    try:
        result = await CLSClient.get_article_detail.__wrapped__(
            client,
            "https://www.cls.cn/detail/2439549",
        )
    finally:
        await client.close()

    assert result == expected_detail
    assert result["content"] == "<p>完整正文</p>"


@pytest.mark.asyncio
async def test_get_article_detail_raises_when_article_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={})

    client = CLSClient()
    await client._client.aclose()
    client._client = _mock_client(handler)
    try:
        with pytest.raises(RuntimeError, match=r"article 999999999 not found or unavailable"):
            await CLSClient.get_article_detail.__wrapped__(client, 999999999)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "article",
    [
        0,
        -1,
        True,
        "",
        "https://example.com/detail/2439549",
        "https://www.cls.cn/depth?id=1000",
    ],
)
def test_parse_article_id_rejects_invalid_values(article) -> None:
    with pytest.raises(ValueError):
        CLSClient._parse_article_id(article)
