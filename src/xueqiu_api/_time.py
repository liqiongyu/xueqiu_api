from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def from_ms(value: object, tz: tzinfo) -> datetime:
    """Convert an upstream millisecond timestamp to an aware datetime in ``tz``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected a millisecond timestamp, got {type(value).__name__}")
    return (_EPOCH + timedelta(milliseconds=int(value))).astimezone(tz)


def to_ms(moment: datetime) -> int:
    """Milliseconds since the epoch of an aware datetime."""
    return (moment - _EPOCH) // timedelta(milliseconds=1)
