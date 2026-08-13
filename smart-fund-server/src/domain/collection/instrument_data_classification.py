"""Canonical storage classification for non-market instrument data."""

PROFILE_DATA_TYPES = frozenset(
    {"fund_detail", "style_preference", "trade_rule", "manager_info", "plates"}
)

DISCLOSURE_DATA_TYPES = frozenset(
    {
        "holdings",
        "scale",
        "holder_ratio",
        "dividend",
        "holding_overview",
        "asset_allocation",
        "position_detail",
    }
)

OBSERVATION_DATA_TYPES = frozenset(
    {
        "nav",
        "nav_technical",
        "performance",
        "flow_trend",
        "flow_trend_summary",
        "periodic_rate",
        "profit_contribution",
        "nav_sina",
        "year_return",
        "max_drawdown",
        "valuation",
        "guba_posts",
        "research",
    }
)


def instrument_data_class(data_type: str) -> str:
    value = str(data_type or "").strip()
    if value in PROFILE_DATA_TYPES:
        return "profile"
    if value in DISCLOSURE_DATA_TYPES:
        return "disclosure"
    if value in OBSERVATION_DATA_TYPES:
        return "observation"
    raise ValueError(f"unclassified instrument data_type: {value!r}")
