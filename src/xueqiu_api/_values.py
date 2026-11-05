"""Parsing of the upstream's text forms of numbers, applied per field.

Never to a whole row: a blanket conversion would turn a fund code such as 008975 into 8975.0.
The functions raise ``ValueError`` on anything else, which becomes ``upstream_changed``.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator

_NO_DATA = frozenset({"", "--"})
"""What the upstream sends in place of a value."""
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_PLAIN = re.compile(_NUMBER)
_PERCENT = re.compile(rf"({_NUMBER})%?")
_AMOUNT = re.compile(rf"({_NUMBER})(万亿|万|亿)?")
_AMOUNT_UNITS = {None: 1, "万": 10**4, "亿": 10**8, "万亿": 10**12}
_RANK = re.compile(r"(\d+)/(\d+)")


def no_data(value: Any) -> Any:
    """Before-validator body: the upstream's markers of a missing value become None."""
    return None if isinstance(value, str) and value.strip() in _NO_DATA else value


def number(value: Any) -> float | None:
    """A JSON number or numeric string, with an optional sign, as a float."""
    return _float(value, _PLAIN)


def percent_points(value: Any) -> float | None:
    """A value already in percent points, with an optional %: ``"+20.48%"`` gives 20.48."""
    return _float(value, _PERCENT)


def fraction_to_points(value: Any) -> float | None:
    """A fraction in percent points: 0.5575 gives 55.75, without binary rounding noise."""
    fraction = number(value)
    return None if fraction is None else float(Decimal(str(fraction)) * 100)


def cn_amount(value: Any) -> float | None:
    """A number with an optional 万, 亿 or 万亿: ``"7.55亿"`` gives 755000000.0."""
    text = no_data(value)
    if text is None or not isinstance(text, str):
        return number(text)
    match = _AMOUNT.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"expected an amount, got {text[:40]!r}")
    return float(Decimal(match[1]) * _AMOUNT_UNITS[match[2]])


def rank_total(value: Any) -> tuple[int, int] | None:
    """A rank among a total: ``"9/4521"`` gives (9, 4521)."""
    text = no_data(value)
    if text is None:
        return None
    match = _RANK.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"expected rank/total, got {str(text)[:40]!r}")
    return int(match[1]), int(match[2])


def _float(value: Any, pattern: re.Pattern[str]) -> float | None:
    cleaned = no_data(value)
    if cleaned is None:
        return None
    if isinstance(cleaned, str) and (match := pattern.fullmatch(cleaned.strip())):
        return float(match[1] if pattern.groups else match[0])
    if (
        isinstance(cleaned, int | float)
        and not isinstance(cleaned, bool)
        and math.isfinite(cleaned)
    ):
        return float(cleaned)
    raise ValueError(f"expected a number, got {str(cleaned)[:40]!r}")


Number = Annotated[float | None, BeforeValidator(number)]
PercentPoints = Annotated[float | None, BeforeValidator(percent_points)]
FractionPoints = Annotated[float | None, BeforeValidator(fraction_to_points)]
CnAmount = Annotated[float | None, BeforeValidator(cn_amount)]
WholeNumber = Annotated[int | None, BeforeValidator(no_data)]
"""An integer, or its string form; pydantic converts the string."""
