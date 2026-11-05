"""Query building, report dates and report periods shared by the F10 endpoints."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, tzinfo
from typing import Annotated, Any, cast

from pydantic import BeforeValidator, Field, ValidationInfo

from ._model import UpstreamModel
from ._time import date_from_ms
from .symbols import SymbolParams, normalize_symbol

_PERIOD_LABEL = re.compile(r"(\d{4})(一季报|中报|三季报|年报)")
_PERIOD_ENDS = {"一季报": (3, 31), "中报": (6, 30), "三季报": (9, 30), "年报": (12, 31)}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def region_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    """``build_query`` of a path with a ``{region}`` segment: the region of the symbol's market."""
    return {"region": normalize_symbol(params.symbol).region, "symbol": params.symbol}


def report_date_from_label(text: object) -> date | None:
    """Period end of an upstream label such as 2026三季报, or the date of a label in ISO form.

    Other labels give None, such as the 2026年FY of Hong Kong and US statements, whose fiscal
    year end is not in the label.
    """
    if not isinstance(text, str):
        return None
    if _ISO_DATE.fullmatch(text):
        return date.fromisoformat(text)
    match = _PERIOD_LABEL.fullmatch(text)
    if match is None:
        return None
    month, day = _PERIOD_ENDS[match[2]]
    return date(int(match[1]), month, day)


def _exchange_date(value: Any, info: ValidationInfo) -> Any:
    context = cast(Mapping[str, tzinfo], info.context)
    return date_from_ms(value, context["tz"])


ExchangeDate = Annotated[date | None, BeforeValidator(_exchange_date)]
"""A date the upstream sends as midnight in the exchange timezone. The rows are validated with
``context={"tz": <timezone>}``, because a row does not say which market it belongs to."""


class ReportOption(UpstreamModel):
    """One report period an endpoint offers."""

    report_name: str | None = Field(default=None, validation_alias="name")
    """Upstream label, such as 2026三季报."""
    report_date: ExchangeDate = Field(default=None, validation_alias="value")
    """Period end."""
