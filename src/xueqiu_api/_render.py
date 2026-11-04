"""Plain-text rendering of results for agents: the MCP server now, the CLI later.

A result renders as ``# `` header lines (endpoint and normalized params, stats, fetch time and
freshness, notes), then an RFC 4180 CSV body with ``\\n`` line ends, or ``key: value`` lines for
a single record, and a last ``# next:`` line holding a copyable call or ``none``. The text never
exceeds the character budget: rows are cut at a row boundary and the ``# next:`` line says so.
"""

from __future__ import annotations

import csv
import difflib
import html
import io
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ._finance import Financials, LineItem, ReportPeriod
from .endpoints.kline import DEFAULT_COUNT, Kline, KlineParams
from .errors import InvalidParams, XueqiuError
from .registry import Endpoint
from .symbols import normalize_symbol

ERROR_CHARS = 1500
FREE_TEXT_CHARS = 400
FENCE_BEGIN = "# untrusted upstream text begin"
FENCE_END = "# untrusted upstream text end"
DEFAULT_ZONE = ZoneInfo("Asia/Shanghai")
"""Zone of ``fetched_at`` for results that do not belong to one symbol."""
STATS_COLUMNS = (
    "bars",
    "first_date",
    "first_close",
    "last_date",
    "last_close",
    "base_close",
    "change_pct",
    "high",
    "high_date",
    "low",
    "low_date",
)
ERROR_KEYS = (
    "error_code",
    "recoverable",
    "message",
    "hint",
    "retry_after_s",
    "field",
    "valid_values",
    "candidates",
    "endpoint",
)

NextCall = Callable[[Mapping[str, Any]], str]
"""Formats a call of the same endpoint with new params, for the ``# next:`` line."""
Pager = Callable[[int, int], dict[str, Any] | None]
"""Params of the next page, given the slice of rows shown, or None."""

_EMPTY = (
    "no rows. Likely causes: a wrong market prefix (indexes need SH or SZ, such as SH000001), "
    "or an endpoint that returns nothing to anonymous sessions."
)
_EMPTY_BARS = (
    "no bars. Likely causes: a wrong market prefix (indexes need SH or SZ, such as SH000001), "
    "no trading in the range, minute bars being kept only about 3 trading days (1m) or about "
    "6 months (60m), or an endpoint that returns nothing to anonymous sessions."
)
_LATE_BARS = (
    "no trading in between, or minute bars being kept only about 3 trading days (1m) to about "
    "6 months (60m); # stats cover only the bars returned."
)
_MISSING = "check the market prefix (indexes need SH or SZ, such as SH000001)."
_TAG = re.compile(r"<[^>]*>")


def render(
    spec: Endpoint[Any, Any],
    params: BaseModel,
    result: object,
    *,
    fields: Sequence[str] | None,
    limit: int,
    max_chars: int,
    fetched_at: datetime,
    next_call: NextCall | None,
) -> str:
    """Render one endpoint result; raise ``InvalidParams`` for fields the result does not have.

    ``fields`` defaults to the registry's ``default_fields``. At most ``limit`` rows are shown:
    the latest bars of a kline, the first rows of anything else. A line item of a financial
    statement renders as two columns, ``<item>`` and ``<item>.yoy``.
    """
    agent = spec.agent
    dump = params.model_dump(mode="json", exclude_none=True)
    head = [f"# {spec.name} {compact_json(dump)}"]
    notes: list[str] = []
    keep_last, pager = False, _no_pager
    if isinstance(result, Kline):
        rows = _bar_rows(result)
        head.append(stats_line(kline_stats(result), agent.precision))
        bar_params = cast(KlineParams, params)
        keep_last, pager = True, _bar_pager(bar_params, result)
        if _starts_late(bar_params, result):
            notes.append(
                f"the first bar is on {result.timestamps[0].date()}, after begin "
                f"{bar_params.begin}: {_LATE_BARS}"
            )
    elif isinstance(result, Financials):
        rows = [_period_row(period) for period in result.periods]
        head.append(f"# statement: name={cell(result.name, 0)} currency={cell(result.currency, 0)}")
    elif isinstance(result, list):
        rows = [item.model_dump() for item in cast(list[BaseModel], result)]
        if "symbols" in dump:
            pager = _symbols_pager(dump, rows)
            returned = {row.get("symbol") for row in rows}
            missing = [
                symbol for symbol in dict.fromkeys(dump["symbols"]) if symbol not in returned
            ]
            if rows and missing:
                notes.append(f"no data for {', '.join(missing)}; {_MISSING}")
    elif isinstance(result, BaseModel):
        rows = [result.model_dump()]
    else:
        raise TypeError(f"cannot render a {type(result).__name__} result")
    zone = _zone(params)
    head.append(
        f"# meta: fetched_at={fetched_at.astimezone(zone).isoformat(timespec='seconds')} "
        f"freshness={spec.freshness} rows={len(rows)}"
    )
    available = list(dict.fromkeys(key for row in rows for key in row))
    if fields:
        wanted = list(dict.fromkeys(fields))
        unknown = [name for name in wanted if name not in available]
        if rows and unknown:
            raise InvalidParams(
                f"{spec.name} results have no field {', '.join(unknown)}",
                endpoint=spec.name,
                field="fields",
                valid_values=available,
                candidates=difflib.get_close_matches(unknown[0], available, n=3),
                hint="Use names from valid_values in fields.",
            )
    else:
        wanted = [name for name in agent.default_fields if name in available]
        missing = [name for name in agent.default_fields if name not in available]
        if rows and missing:
            head.append(
                f"# note: default fields missing here: {', '.join(missing)}; "
                f"available: {', '.join(available)}"
            )
    if not rows:
        notes.append(_EMPTY_BARS if isinstance(result, Kline) else _EMPTY)
    head += [f"# note: {note}" for note in notes]
    free = [name for name in wanted if name in agent.free_text]
    if isinstance(result, BaseModel):
        return _record(head, rows[0], wanted, free, agent.precision, max_chars)
    pairs = {name for name in wanted if any(isinstance(row.get(name), LineItem) for row in rows)}
    shown = min(limit, len(rows))
    start = len(rows) - shown if keep_last else 0
    lines = [
        _row_line(row, wanted, pairs, free, agent.precision) for row in rows[start : start + shown]
    ]
    columns = [
        column
        for name in wanted
        for column in ((name, f"{name}.yoy") if name in pairs else (name,))
    ]
    pre, post = list(head), []
    if lines:
        pre += [FENCE_BEGIN, csv_line(columns)] if free else [csv_line(columns)]
        post = [FENCE_END] if free else []

    def footer(first: int, stop: int) -> str | None:
        page = pager(start + first, start + stop) if stop > first else None
        return None if page is None or next_call is None else next_call(page)

    return fit(pre, lines, footer=footer, max_chars=max_chars, keep_last=keep_last, post=post)


def render_bar_stats(
    spec: Endpoint[Any, Any],
    params: Sequence[KlineParams],
    klines: Sequence[Kline],
    *,
    max_chars: int,
    fetched_at: datetime,
) -> str:
    """One stats row per symbol, for comparing klines of several symbols; no bar rows.

    A param whose value differs between the symbols, such as a ``begin`` taken from today in
    each exchange's timezone, is shown as an object by symbol.
    """
    dumps = [one.model_dump(mode="json", exclude_none=True) for one in params]
    shared: dict[str, Any] = {}
    for key in dict.fromkeys(key for dump in dumps for key in dump if key != "symbol"):
        values = [dump.get(key) for dump in dumps]
        if all(value == values[0] for value in values):
            shared[key] = values[0]
        else:
            shared[key] = {one.symbol: value for one, value in zip(params, values, strict=True)}
    head = [
        f"# {spec.name} {compact_json({'symbols': [p.symbol for p in params], **shared})}",
        f"# meta: fetched_at={fetched_at.astimezone(DEFAULT_ZONE).isoformat(timespec='seconds')} "
        f"freshness={spec.freshness} rows={len(klines)}",
    ]
    if any(len(kline) == 0 for kline in klines):
        head.append(f"# note: {_EMPTY_BARS}")
    late = [
        one.symbol for one, kline in zip(params, klines, strict=True) if _starts_late(one, kline)
    ]
    if late:
        head.append(f"# note: the bars of {', '.join(late)} start after begin: {_LATE_BARS}")
    head.append(csv_line(["symbol", *STATS_COLUMNS]))
    lines: list[str] = []
    for one, kline in zip(params, klines, strict=True):
        stats = kline_stats(kline)
        values = (cell(stats[name], spec.agent.precision) for name in STATS_COLUMNS)
        lines.append(csv_line([one.symbol, *values]))
    return fit(head, lines, footer=lambda first, stop: None, max_chars=max_chars)


def fit(
    head: Sequence[str],
    rows: Sequence[str],
    *,
    footer: Callable[[int, int], str | None],
    max_chars: int,
    keep_last: bool = False,
    post: Sequence[str] = (),
) -> str:
    """Join ``head``, as many ``rows`` as fit in ``max_chars``, ``post`` and the ``# next:`` line.

    ``footer(first, stop)`` gets the slice of ``rows`` shown and returns the next call or None.
    Rows are dropped from the end, or from the start with ``keep_last``.
    """
    fixed = sum(len(line) + 1 for line in (*head, *post))
    sizes = [len(row) + 1 for row in rows]
    total = sum(sizes)
    for shown in range(len(rows), -1, -1):
        first = len(rows) - shown if keep_last else 0
        if shown < len(rows):
            total -= sizes[first - 1] if keep_last else sizes[shown]
        call = footer(first, first + shown) if shown else None
        line = f"# next: {call or 'none'}"
        if shown < len(rows):
            line += f"; truncated to {shown} of {len(rows)} rows by the {max_chars} character limit"
        if fixed + total + len(line) <= max_chars or shown == 0:
            return "\n".join([*head, *rows[first : first + shown], *post, line])
    raise AssertionError("unreachable")


def kline_stats(kline: Kline) -> dict[str, Any]:
    """Stats over every bar: first and last close, change in percent points, high and low.

    The change is measured from ``base_close``, the close before the first bar as that bar's
    ``percent`` implies, so it includes the first bar's own move; without ``percent`` the base
    is the first close. Dates are bar times as rendered; ties go to the earliest bar; missing
    columns give None.
    """
    stats: dict[str, Any] = dict.fromkeys(STATS_COLUMNS)
    stats["bars"] = len(kline)
    closes = _present(kline.columns.get("close"))
    if closes:
        (first, first_close), (last, last_close) = closes[0], closes[-1]
        stats["first_date"] = bar_time(kline, kline.timestamps[first])
        stats["first_close"] = first_close
        stats["last_date"] = bar_time(kline, kline.timestamps[last])
        stats["last_close"] = last_close
        percents = kline.columns.get("percent")
        percent = None if percents is None else percents[first]
        base = (
            first_close if percent is None or percent <= -100 else first_close / (1 + percent / 100)
        )
        stats["base_close"] = base
        if base:
            stats["change_pct"] = (last_close / base - 1) * 100
    for name, pick in (("high", max), ("low", min)):
        values = _present(kline.columns.get(name))
        if values:
            index, value = pick(values, key=lambda pair: pair[1])
            stats[name] = value
            stats[f"{name}_date"] = bar_time(kline, kline.timestamps[index])
    return stats


def stats_line(stats: Mapping[str, Any], precision: int) -> str:
    pairs = (f"{key}={cell(value, precision)}" for key, value in stats.items() if value is not None)
    return f"# stats: {' '.join(pairs)}"


def bar_time(kline: Kline, moment: datetime) -> str:
    """Exchange-local date for day and longer bars, ISO time with offset for minute bars."""
    if kline.period.endswith("m"):
        return moment.isoformat(timespec="seconds")
    return moment.date().isoformat()


def error_text(error: XueqiuError, *, hint: str | None = None) -> str:
    """The error as one line of compact JSON of at most ``ERROR_CHARS`` characters.

    When it is too long, message and hint are shortened first, then the value lists.
    """
    payload: dict[str, Any] = {k: v for k, v in error.to_dict().items() if k in ERROR_KEYS}
    if hint is not None:
        payload["hint"] = hint
    text = compact_json(payload)
    if len(text) > ERROR_CHARS:
        for key, cap in (("message", 500), ("hint", 400), ("field", 100)):
            if len(payload.get(key) or "") > cap:
                payload[key] = payload[key][: cap - 1] + "…"
        text = compact_json(payload)
    for key in ("valid_values", "candidates"):
        while len(text) > ERROR_CHARS and payload.get(key):
            payload[key] = payload[key][:-1]
            text = compact_json({k: v for k, v in payload.items() if v != []})
    while len(text) > ERROR_CHARS and max(len(payload["message"]), len(payload["hint"])) > 1:
        key = max(("message", "hint"), key=lambda name: len(payload[name]))
        keep = max(0, len(payload[key]) - (len(text) - ERROR_CHARS) - 1)
        payload[key] = payload[key][:keep] + "…"
        text = compact_json({k: v for k, v in payload.items() if v != []})
    return text


def cell(value: object, precision: int) -> str:
    """One value as text: floats rounded to ``precision`` places, no thousands separators."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return number(value, precision)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return " ".join(value.split())
    return compact_json(value)


def number(value: float, precision: int) -> str:
    if not math.isfinite(value):
        return ""
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def clean_text(value: object) -> str:
    """Upstream prose with HTML removed and whitespace collapsed, cut to ``FREE_TEXT_CHARS``."""
    text = " ".join(html.unescape(_TAG.sub(" ", str(value))).split())
    return text if len(text) <= FREE_TEXT_CHARS else text[: FREE_TEXT_CHARS - 1] + "…"


def csv_line(cells: Sequence[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(cells)
    return buffer.getvalue()[:-1]


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _record(
    head: list[str],
    row: Mapping[str, Any],
    wanted: Sequence[str],
    free: Sequence[str],
    precision: int,
    max_chars: int,
) -> str:
    lines = [f"{name}: {cell(row.get(name), precision)}" for name in wanted if name not in free]
    if free:
        prose = [f"{name}: {clean_text(row.get(name) or '')}" for name in free]
        lines.append("\n".join([FENCE_BEGIN, *prose, FENCE_END]))
    return fit(head, lines, footer=lambda first, stop: None, max_chars=max_chars)


def _row_line(
    row: Mapping[str, Any],
    wanted: Sequence[str],
    pairs: set[str],
    free: Sequence[str],
    precision: int,
) -> str:
    cells: list[str] = []
    for name in wanted:
        value = row.get(name)
        if name in pairs:
            item = value if isinstance(value, LineItem) else LineItem(None, None)
            cells += [cell(item.value, precision), cell(item.yoy, precision)]
        elif name in free:
            cells.append("" if value is None else clean_text(value))
        else:
            cells.append(cell(value, precision))
    return csv_line(cells)


def _bar_rows(kline: Kline) -> list[dict[str, Any]]:
    names = list(kline.columns)
    return [
        {"timestamp": bar_time(kline, moment), **{n: kline.columns[n][i] for n in names}}
        for i, moment in enumerate(kline.timestamps)
    ]


def _period_row(period: ReportPeriod) -> dict[str, Any]:
    return {
        "report_date": period.report_date,
        "report_name": period.report_name,
        **period.items,
        **period.extra,
    }


def _present(column: Sequence[float | int | None] | None) -> list[tuple[int, float]]:
    return [(index, float(value)) for index, value in enumerate(column or ()) if value is not None]


def _no_pager(first: int, stop: int) -> dict[str, Any] | None:
    return None


def _bar_pager(params: KlineParams, kline: Kline) -> Pager:
    """Older bars: the same call ending just before the first bar shown, with ``end`` the day
    before it, or for minute bars with ``before`` its time.

    There are older bars when some fetched ones are not shown, or when a request without
    ``begin`` came back full.
    """
    count = params.count or DEFAULT_COUNT

    def pager(first: int, stop: int) -> dict[str, Any] | None:
        if first == 0 and (params.begin is not None or len(kline) < count):
            return None
        moment = kline.timestamps[first]
        if kline.period.endswith("m"):
            update: dict[str, Any] = {"end": None, "before": moment}
        else:
            end = moment.date() - timedelta(days=1)
            if params.begin is not None and end < params.begin:
                return None
            update = {"end": end, "before": None}
        return params.model_copy(update=update).model_dump(mode="json", exclude_none=True)

    return pager


def _starts_late(params: KlineParams, kline: Kline) -> bool:
    """Whether minute bars start more than 3 days after ``begin``, more than a weekend or a
    short holiday explains, as when ``begin`` is past what the upstream keeps."""
    return (
        kline.period.endswith("m")
        and params.begin is not None
        and len(kline) > 0
        and (kline.timestamps[0].date() - params.begin).days > 3
    )


def _symbols_pager(dump: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> Pager:
    """The symbols of the rows not shown."""

    def pager(first: int, stop: int) -> dict[str, Any] | None:
        rest = [str(row.get("symbol")) for row in rows[stop:]]
        return {**dump, "symbols": rest} if rest else None

    return pager


def _zone(params: BaseModel) -> ZoneInfo:
    symbol: object = getattr(params, "symbol", None)
    return normalize_symbol(symbol).timezone if isinstance(symbol, str) else DEFAULT_ZONE
