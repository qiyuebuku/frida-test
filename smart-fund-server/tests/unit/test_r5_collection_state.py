"""R5 测试: ft_collection_state 独立列 + 增量采集机制"""
import pytest


# ==================== 表结构 ====================


@pytest.mark.unit
def test_r5_collection_state_has_new_columns():
    """CollectionState ORM 模型包含 mode/target_time/newest_time/oldest_time/backfill_status/cursor"""
    from src.infrastructure.persistence.models.collection import CollectionState
    cols = {c.name for c in CollectionState.__table__.columns}
    for field in ("mode", "target_time", "newest_time", "oldest_time", "backfill_status", "cursor"):
        assert field in cols, f"缺少列: {field}"
    assert "checkpoint" not in cols, "checkpoint 列应已删除"


@pytest.mark.unit
def test_r5_collection_state_no_checkpoint_attr():
    """CollectionState 不再有 checkpoint 属性"""
    from src.infrastructure.persistence.models.collection import CollectionState
    assert not hasattr(CollectionState, "checkpoint")


@pytest.mark.unit
def test_r5_collection_state_mode_default():
    """mode 默认值是 incremental"""
    from src.infrastructure.persistence.models.collection import CollectionState
    col = CollectionState.__table__.columns["mode"]
    assert "incremental" in str(col.default.arg)


# ==================== _compute_checkpoint ====================


@pytest.mark.unit
def test_r5_compute_checkpoint_updates_times():
    """_compute_checkpoint 正确更新 newest_time 和 oldest_time"""
    from src.domain.collection.services.base import BaseAggregator

    agg = BaseAggregator()
    agg.data_domain = "test"
    prev_cp = {"mode": "incremental"}
    items = [
        {"published_at": "2026-04-10T10:00:00+08:00"},
        {"published_at": "2026-04-12T15:30:00+08:00"},
    ]
    new_cp = agg._compute_checkpoint("test_src", items, prev_cp)
    assert new_cp["newest_time"] == "2026-04-12T15:30:00+08:00"
    assert new_cp["oldest_time"] == "2026-04-10T10:00:00+08:00"


@pytest.mark.unit
def test_r5_compute_checkpoint_preserves_mode():
    """_compute_checkpoint 不丢失 mode 和 target_time"""
    from src.domain.collection.services.base import BaseAggregator

    agg = BaseAggregator()
    agg.data_domain = "test"
    prev_cp = {"mode": "backfill", "target_time": "2026-02-11", "cursor": 5}
    items = [{"trade_date": "2026-04-01"}]
    new_cp = agg._compute_checkpoint("test_src", items, prev_cp)
    assert new_cp["mode"] == "backfill"
    assert new_cp["target_time"] == "2026-02-11"


@pytest.mark.unit
def test_r5_compute_checkpoint_strips_internal_fields():
    """_compute_checkpoint 不持久化 _config/_lock 等内部字段"""
    from src.domain.collection.services.base import BaseAggregator

    agg = BaseAggregator()
    agg.data_domain = "test"
    prev_cp = {"mode": "incremental", "_config": {"page_size": 50}, "_lock": "fake"}
    items = [{"trade_date": "2026-04-01"}]
    new_cp = agg._compute_checkpoint("test_src", items, prev_cp)
    assert "_config" not in new_cp
    assert "_lock" not in new_cp


@pytest.mark.unit
def test_r5_compute_checkpoint_backfill_to_incremental():
    """oldest_time <= target_time 时自动切 incremental"""
    from src.domain.collection.services.base import BaseAggregator

    agg = BaseAggregator()
    agg.data_domain = "test"
    prev_cp = {"mode": "backfill", "target_time": "2026-03-01"}
    items = [{"published_at": "2026-02-15T00:00:00+08:00"}]
    new_cp = agg._compute_checkpoint("test_src", items, prev_cp)
    assert new_cp["mode"] == "incremental"
    assert new_cp["backfill_status"] == "done"


@pytest.mark.unit
def test_r5_compute_checkpoint_backfill_not_reached():
    """oldest_time > target_time 时保持 backfill"""
    from src.domain.collection.services.base import BaseAggregator

    agg = BaseAggregator()
    agg.data_domain = "test"
    prev_cp = {"mode": "backfill", "target_time": "2026-01-01"}
    items = [{"published_at": "2026-03-15T00:00:00+08:00"}]
    new_cp = agg._compute_checkpoint("test_src", items, prev_cp)
    assert new_cp["mode"] == "backfill"
    assert new_cp.get("backfill_status") is None


# ==================== NewsAggregator ====================


@pytest.mark.unit
def test_r5_news_source_configs_complete():
    """NewsAggregator.SOURCE_CONFIGS 包含所有 11 个源"""
    from src.domain.collection.services.news import NewsAggregator
    expected = {"cls", "cls_depth", "cls_hot_article", "gov", "pboc_omo", "pboc_monetary", "em_news",
                "em_reports", "ths", "sina", "xueqiu"}
    assert set(NewsAggregator.SOURCE_CONFIGS.keys()) == expected


@pytest.mark.unit
def test_r5_news_source_configs_have_required_fields():
    """每个 SOURCE_CONFIG 都有 target_days 和 interval"""
    from src.domain.collection.services.news import NewsAggregator
    for name, cfg in NewsAggregator.SOURCE_CONFIGS.items():
        assert "target_days" in cfg, f"{name} 缺 target_days"
        assert "interval" in cfg, f"{name} 缺 interval"


@pytest.mark.unit
def test_r5_extract_item_date():
    """_extract_item_date 适配多种时间字段"""
    from src.domain.collection.services.news import NewsAggregator

    # Unix 时间戳（UTC+8: 2024-04-13 04:00）
    assert NewsAggregator._extract_item_date({"ctime": 1712937600}) == "2024-04-13"
    # 毫秒时间戳
    assert NewsAggregator._extract_item_date({"created_at": 1712937600000}) == "2024-04-13"
    # 日期字符串
    assert NewsAggregator._extract_item_date({"date": "2026-04-12"}) == "2026-04-12"
    assert NewsAggregator._extract_item_date({"published_at": "2026-04-12T15:30:00"}) == "2026-04-12"
    # 空
    assert NewsAggregator._extract_item_date({}) == ""


@pytest.mark.unit
def test_r5_extract_item_timestamp():
    """_extract_item_timestamp 返回秒级精度"""
    from src.domain.collection.services.news import NewsAggregator

    ts = NewsAggregator._extract_item_timestamp({"ctime": 1712937600})
    assert ts == 1712937600.0

    ts = NewsAggregator._extract_item_timestamp({"created_at": 1712937600000})
    assert ts == 1712937600.0

    ts = NewsAggregator._extract_item_timestamp({})
    assert ts == 0.0


@pytest.mark.unit
def test_r5_cls_incremental_checkpoint_keeps_full_timestamp():
    """财联社增量 checkpoint 必须保留时分秒，避免反复拉当天全量。"""
    from src.domain.collection.services.news import _iso_to_timestamp

    assert _iso_to_timestamp("2026-07-04T23:52:08+08:00") == 1783180328
    assert _iso_to_timestamp("2026-07-04") == 1783094400


@pytest.mark.unit
def test_r5_cls_realtime_cache_ttl_is_short():
    """财联社实时快讯不能使用长 TTL，否则会持续命中旧 raw cache。"""
    from src.infrastructure.clients.cls import CLSClient

    assert CLSClient.REALTIME_CACHE_TTL_SECONDS <= 60


@pytest.mark.unit
def test_r5_cls_depth_cache_ttl_is_bounded():
    """财联社深度文章列表不能使用长 TTL，避免头条文章长时间不更新。"""
    from src.infrastructure.clients.cls import CLSClient

    assert CLSClient.DEPTH_CACHE_TTL_SECONDS <= 600


@pytest.mark.unit
def test_r5_normalize_cls_depth():
    """财联社深度文章列表规范化为独立 source。"""
    from src.domain.collection.services.news import normalize_cls_depth

    items = normalize_cls_depth([
        {
            "id": 2417256,
            "ctime": 1783230585,
            "title": "人造太阳”时间表更新 第一度电瞄准2030年",
            "brief": "可控核聚变又有重大进展。",
            "article_tag": [{"name": "原创"}],
            "source": "央视新闻",
            "reading_num": 280783,
        }
    ])

    assert len(items) == 1
    item = items[0]
    assert item["source"] == "cls_depth"
    assert item["source_name"] == "财联社深度"
    assert item["title"].startswith("人造太阳")
    assert item["content"] == "可控核聚变又有重大进展。"
    assert item["url"] == "https://www.cls.cn/detail/2417256"
    assert item["tags"] == ["原创"]


@pytest.mark.unit
def test_r5_normalize_cls_telegraph_source_name():
    """财联社电报和财联社深度必须用不同 source_name 展示。"""
    from src.domain.collection.services.news import normalize_cls

    items = normalize_cls([
        {
            "ctime": 1783230585,
            "title": "财联社7月5日电，测试快讯。",
            "brief": "测试快讯。",
        }
    ])

    assert len(items) == 1
    assert items[0]["source"] == "cls"
    assert items[0]["source_name"] == "财联社电报"


@pytest.mark.unit
def test_r5_backfill_signal_mapping():
    """_get_backfill_signal 正确映射 __DONE__/__CEILING__"""
    from src.domain.collection.services.news import NewsAggregator

    agg = NewsAggregator.__new__(NewsAggregator)
    agg._backfill_cursor = "__DONE__"
    assert agg._get_backfill_signal("cls") == "done"

    agg._backfill_cursor = "__CEILING__"
    assert agg._get_backfill_signal("cls") == "ceiling"

    agg._backfill_cursor = 42
    assert agg._get_backfill_signal("cls") == 42

    agg._backfill_cursor = None
    assert agg._get_backfill_signal("cls") is None


# ==================== BaseAggregator ====================


@pytest.mark.unit
def test_r5_base_aggregator_has_max_pages():
    """MAX_PAGES_PER_TICK 在 BaseAggregator 上定义"""
    from src.domain.collection.services.base import BaseAggregator
    assert BaseAggregator.MAX_PAGES_PER_TICK == 500


@pytest.mark.unit
def test_r5_base_aggregator_init_state_classmethod():
    """init_state 是 classmethod"""
    from src.domain.collection.services.base import BaseAggregator
    assert isinstance(BaseAggregator.__dict__["init_state"], classmethod)


@pytest.mark.unit
def test_r5_retryable_errors():
    """RETRYABLE_ERRORS 包含常见网络错误"""
    from src.domain.collection.services.base import BaseAggregator
    for err in ("ConnectTimeout", "ReadTimeout", "ConnectionError"):
        assert err in BaseAggregator.RETRYABLE_ERRORS


# ==================== Repository ====================


@pytest.mark.unit
def test_r5_row_to_dict_has_new_fields():
    """_row_to_dict 返回独立列字段"""
    from unittest.mock import MagicMock
    from src.infrastructure.persistence.repositories.collection_state_repository_impl import _row_to_dict

    row = MagicMock()
    row.id = 1
    row.aggregator = "news"
    row.source_name = "cls"
    row.mode = "backfill"
    row.target_time = "2026-02-11"
    row.newest_time = "2026-04-12"
    row.oldest_time = "2026-03-01"
    row.backfill_status = "done"
    row.cursor = None
    row.config = {"target_days": 60}
    row.last_run_at = None
    row.last_success_at = None
    row.last_error = ""
    row.consecutive_failures = 0
    row.enabled = True
    row.interval_override = None
    row.total_runs = 5
    row.total_saved = 100
    row.created_at = None
    row.updated_at = None

    d = _row_to_dict(row)
    assert d["mode"] == "backfill"
    assert d["target_time"] == "2026-02-11"
    assert d["newest_time"] == "2026-04-12"
    assert d["oldest_time"] == "2026-03-01"
    assert d["backfill_status"] == "done"
    assert d["cursor"] is None
    assert "checkpoint" not in d
