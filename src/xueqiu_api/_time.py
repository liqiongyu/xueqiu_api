from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def from_ms(value: object, tz: tzinfo) -> datetime:
    """Convert an upstream millisecond timestamp to an aware datetime in ``tz``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected a millisecond timestamp, got {type(value).__name__}")
    try:
        return (_EPOCH + timedelta(milliseconds=int(value))).astimezone(tz)
    except OverflowError:
        raise ValueError(f"timestamp {value!r} is out of range") from None


def date_from_ms(value: Any, tz: tzinfo) -> Any:
    """Before-validator body: a millisecond timestamp becomes its date in ``tz``, usually the
    exchange timezone; any other value passes through for the field's own validation."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return from_ms(value, tz).date()
    return value


def to_ms(moment: datetime) -> int:
    """Milliseconds since the epoch of an aware datetime."""
    return (moment - _EPOCH) // timedelta(milliseconds=1)
