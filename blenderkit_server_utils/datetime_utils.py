"""Utility functions for working with dates and times.

Provides Python version compatible helpers for UTC handling.
"""

from __future__ import annotations

import datetime


def _utc_tz() -> datetime.tzinfo:
    """Return a UTC tzinfo compatible with Python 3.9+.

    Uses datetime.UTC if available (3.11+), otherwise falls back to datetime.timezone.utc.
    """
    tz = getattr(datetime, "UTC", None)
    if isinstance(tz, datetime.tzinfo):
        return tz
    return datetime.timezone.utc  # noqa: UP017


def today_date_iso() -> str:
    """Return today's date in ISO 8601 format (YYYY-MM-DD) in UTC."""
    tz = _utc_tz()
    today = datetime.datetime.now(tz=tz).date()
    iso_date = today.isoformat()
    return iso_date
