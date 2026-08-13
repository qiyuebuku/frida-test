from src.infrastructure.clients.ths import ETF_TRACKING_INDEX_UNIQUE_TYPE


def test_tracking_index_filter_uses_native_ths_unique_type() -> None:
    # The upstream spelling is intentionally "tack", not "track".
    assert ETF_TRACKING_INDEX_UNIQUE_TYPE == "tackMainIndexThscode"
