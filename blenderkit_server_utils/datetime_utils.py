"""Utility functions for working with dates and times."""

from __future__ import annotations

import datetime


def today_date_iso() -> str:
    """Return today's date in ISO 8601 format (YYYY-MM-DD)."""
    return datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
