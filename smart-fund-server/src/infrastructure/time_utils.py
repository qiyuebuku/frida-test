"""Time helpers used by collection and persistence code."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamp for TIMESTAMPTZ fields."""
    return datetime.now(timezone.utc)


def app_now() -> datetime:
    """Return timezone-aware application local timestamp."""
    return datetime.now(APP_TZ)


def app_today() -> date:
    """Return business date in application timezone."""
    return app_now().date()


def app_today_iso() -> str:
    return app_today().isoformat()
