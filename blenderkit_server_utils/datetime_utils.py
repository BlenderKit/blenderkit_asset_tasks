"""Utility functions for working with dates and times.

Provides Python version compatible helpers for UTC handling.
"""

from __future__ import annotations

import datetime as _dt

# UTC fallback for Python < 3.11
try:
    _UTC_TZ = _dt.UTC  # type: ignore[attr-defined]
except AttributeError:
    _UTC_TZ = _dt.timezone.utc  # noqa: UP017


def today_date_iso() -> str:
    """Return today's date in ISO 8601 format (YYYY-MM-DD) in UTC."""
    today = _dt.datetime.now(tz=_UTC_TZ).date()
    iso_date = today.isoformat()
    return iso_date


def now_timestamp_iso() -> str:
    """Return current UTC timestamp in ISO 8601 with timezone offset."""
    return _dt.datetime.now(tz=_UTC_TZ).isoformat()
