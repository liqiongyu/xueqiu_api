from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_datetime(value: Any) -> datetime | None:
    """Parse Xueqiu timestamps into timezone-aware UTC datetimes.

    Xueqiu commonly uses Unix timestamps in milliseconds.
    """

    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        # Heuristic: milliseconds are ~1e12 for modern dates, seconds are ~1e9.
        if ts > 10_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return parse_datetime(int(s))

        # Best-effort ISO parsing.
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    return None


