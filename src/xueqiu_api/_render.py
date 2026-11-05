"""Plain-text rendering of results for agents: the MCP server now, the CLI later.

A result renders as ``# `` header lines (endpoint and normalized params, stats, fetch time and
freshness, notes), then an RFC 4180 CSV body with ``\\n`` line ends, or ``key: value`` lines for
a single record, and a last ``# next:`` line holding a copyable call or ``none``. The text never
exceeds the character budget: rows are cut at a row boundary and the ``# next:`` line says so.

``render`` takes ``Kline``, ``Financials``, ``Page``, lists, single models and anything of the
shape of ``Series`` or ``Sectioned``, so a new endpoint needs no branch here. ``Endpoint.order``
decides which rows a cut keeps: the latest of ``oldest_first`` rows, the first of any other.
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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, cast, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from ._finance import FinanceParams, Financials, LineItem, ReportPeriod
from ._pages import Page
from .endpoints.kline import (
    DEFAULT_COUNT,
    MAX_COUNT,
    Kline,
    KlineParams,
    came_back_full,
    older_range,
)
from .errors import InvalidParams, XueqiuError
from .registry import AgentInfo, Endpoint
from .symbols import normalize_symbol

ERROR_CHARS = 1500
FREE_TEXT_CHARS = 400
LABEL_CHARS = 40
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

BATCH_PARAMS = ("symbols", "cube_symbols")
"""Params naming several codes, whose result has one row per code found."""

NextCall = Callable[[Mapping[str, Any], int], str]
"""Formats a call of the same endpoint with new params and offset, for the ``# next:`` line."""
Pager = Callable[[int, int], tuple[Mapping[str, Any], int] | None]
"""Params and offset of the next call, given the slice of rows shown, or None."""

_EMPTY = (
    "no rows. Likely causes: a wrong market prefix (indexes need SH or SZ, such as SH000001), "
    "or an endpoint that returns nothing to anonymous sessions."
)
_EMPTY_NO_SYMBOL = (
    "no rows. Likely causes: a code or value with no data, or an endpoint that returns nothing "
    "to anonymous sessions."
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
_CUT_BARS = (
    f"one request returns at most {MAX_COUNT} bars, the latest of the range, and # stats cover "
    "only those. An end before the first bar reaches older ones."
)
_MISSING = "check the market prefix (indexes need SH or SZ, such as SH000001)."
_TAG = re.compile(r"<[^>]*>")


@runtime_checkable
class Sectioned(Protocol):
    """A result of named tables, in order.

    ``header()`` is a mapping of short values for the ``# info:`` line; it may be empty and holds
    no prose. Rows are models or mappings. The tables render as one run of rows that ``limit``
    and ``offset`` apply to, with ``# section: <name>`` before each when more than one has rows.
    A table's columns are the requested ones it has, or by default the default fields it has a
    value for. A ``Page`` may have this shape too, and then rows come from ``sections()``.
    """

    def header(self) -> Mapping[str, Any]: ...

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]: ...

    def __len__(self) -> int: ...


@runtime_checkable
class Series(Protocol):
    """Rows in columns, one per point in time, oldest first; declare ``order="oldest_first"``.

    ``index()`` gives the name and the values of the time column, ``columns`` the others. The
    ``# stats:`` line covers every row, whichever are shown: the range, and per numeric column
    the last value and the extremes with when they happened. ``header()`` is as in ``Sectioned``.
    """

    @property
    def columns(self) -> Mapping[str, Sequence[Any]]: ...

    def index(self) -> tuple[str, Sequence[date | datetime]]: ...

    def header(self) -> Mapping[str, Any]: ...

    def __len__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class _Table:
    """The rows ``start`` to ``stop`` of a result, with the columns they render."""

    name: str
    start: int
    stop: int
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Frame:
    """The lines around the rows ``start`` to ``stop``, printed when one of them is shown."""

    start: int
    stop: int
    before: tuple[str, ...]
    after: tuple[str, ...]


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
    offset: int = 0,
) -> str:
    """Render one endpoint result; raise ``InvalidParams`` for fields the result does not have.

    ``fields`` defaults to the registry's ``default_fields``. At most ``limit`` rows are shown,
    after skipping the ``offset`` rows already shown by earlier calls for the same params:
    counted back from the latest row where a cut keeps the latest, from the first row otherwise.
    A ``Kline`` and a result for several codes have a ``# next:`` of their own, and ``Financials``
    and a single record come whole from one call, so none of them takes an offset. A line item of
    a financial statement renders as two columns, ``<item>`` and ``<item>.yoy``.
    """
    agent = spec.agent
    dump = params.model_dump(mode="json", exclude_none=True)
    batch = next((name for name in BATCH_PARAMS if name in dump), None)
    sectioned = isinstance(result, Sectioned)
    record = isinstance(result, BaseModel) and not isinstance(result, Sectioned | Series)
    if offset and (
        isinstance(result, Kline | Financials) or record or (batch and isinstance(result, list))
    ):
        raise no_offset(spec)
    keep_last = spec.order == "oldest_first"
    head = [f"# {spec.name} {compact_json(dump)}"]
    notes: list[str] = []
    pager: Pager = _no_pager
    named: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(result, Kline):
        rows = _bar_rows(result)
        head.append(stats_line(kline_stats(result), agent.precision))
        bar_params = cast(KlineParams, params)
        pager = _bar_pager(bar_params, result)
        if _starts_late(bar_params, result):
            notes.append(
                f"the first bar is on {result.timestamps[0].date()}, after begin "
                f"{bar_params.begin}: {_LATE_BARS}"
            )
        if _cut_off(bar_params, result):
            notes.append(f"the first bar is on {result.timestamps[0].date()}: {_CUT_BARS}")
    elif isinstance(result, Financials):
        rows = [_period_row(period) for period in result.periods]
        head.append(f"# statement: name={cell(result.name, 0)} currency={cell(result.currency, 0)}")
        if 0 < limit < len(rows):
            notes.append(
                f"showing {limit} of {len(rows)} report periods; "
                f"a limit of up to {agent.row_cap} shows more."
            )
    elif isinstance(result, Series):
        rows = _series_rows(result)
        head += _info_lines(result.header(), agent.precision)
        head.append(stats_line(series_stats(result), agent.precision))
    elif isinstance(result, Sectioned):
        named = [
            (name, [_row_mapping(row) for row in table])
            for name, table in result.sections().items()
        ]
        rows = [row for _, table in named for row in table]
        head += _info_lines(result.header(), agent.precision)
        empty = [name for name, table in named if not table]
        if rows and empty:
            notes.append(f"empty sections: {', '.join(empty)}")
    elif isinstance(result, Page):
        rows = [_row_mapping(item) for item in cast(Page[Any], result)]
    elif isinstance(result, list):
        rows = [item.model_dump() for item in cast(list[BaseModel], result)]
        if batch:
            pager = _symbols_pager(dump, rows, batch)
            returned = {row.get("symbol") for row in rows}
            missing = [code for code in dict.fromkeys(dump[batch]) if code not in returned]
            if rows and missing:
                advice = f"; {_MISSING}" if batch == "symbols" else ""
                notes.append(f"no data for {', '.join(missing)}{advice}")
    elif record:
        rows = [cast(BaseModel, result).model_dump()]
    else:
        raise TypeError(f"cannot render a {type(result).__name__} result")
    if isinstance(result, Page):
        page = cast(Page[Any], result)
        stats = {"page": page.page, "page_size": page.page_size, "total": page.total}
        head.append(stats_line({**stats, "last_page": page.last_page}, 0, tag="page"))
        notes += _page_notes(spec, page)
    available = list(dict.fromkeys(key for row in rows for key in row))
    later_page = isinstance(result, Page) and cast(Page[Any], result).page > 1
    missing_fields: list[str] = []
    if fields:
        wanted = list(dict.fromkeys(fields))
        unknown = [name for name in wanted if name not in available]
        # A column only some pages have stays, empty, on the others: # next carries the fields on.
        if rows and unknown and not later_page:
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
            missing_fields = [
                f"# note: default fields missing here: {', '.join(missing)}; "
                f"available: {', '.join(available)}"
            ]
    left_out: list[str] = []
    if sectioned:
        rows, tables, left_out = _lay_out(named, wanted, explicit=bool(fields))
        if left_out:
            notes.append(f"tables with none of these columns are left out: {'; '.join(left_out)}")
    else:
        tables = [_Table("", 0, len(rows), tuple(wanted))]
    zone = _zone(params)
    head.append(
        f"# meta: fetched_at={fetched_at.astimezone(zone).isoformat(timespec='seconds')} "
        f"freshness={spec.freshness} rows={len(rows)}"
    )
    head += missing_fields
    if not rows and not left_out:
        notes.append(_empty_note(agent, dump, cast(object, result)))
    head += [f"# note: {note}" for note in notes]
    free = [name for name in wanted if name in agent.free_text]
    labels = [name for name in wanted if name in agent.labels]
    if record:
        if not limit:
            return "\n".join([*head, "# next: none"])
        return _record(head, rows[0], wanted, free, labels, agent.precision, max_chars)
    if isinstance(result, Series | Sectioned | Page) or (isinstance(result, list) and not batch):
        following = result.next_params if isinstance(result, Page) else None
        pager = _row_pager(dump, len(rows), keep_last, following)
    pairs = {name for name in wanted if any(isinstance(row.get(name), LineItem) for row in rows)}
    if keep_last:
        stop = max(len(rows) - offset, 0)
        start = max(stop - limit, 0)
    else:
        start = min(offset, len(rows))
        stop = min(start + limit, len(rows))
    lines: list[str] = []
    frames: list[_Frame] = []
    fenced = {*free, *labels}
    pre, post = list(head), list[str]()
    for table in tables:
        low, high = max(table.start, start), min(table.stop, stop)
        if low >= high:
            continue
        columns = [
            column
            for name in table.columns
            for column in ((name, f"{name}.yoy") if name in pairs else (name,))
        ]
        fence = fenced.intersection(table.columns)
        before = (FENCE_BEGIN, csv_line(columns)) if fence else (csv_line(columns),)
        after = (FENCE_END,) if fence else ()
        if len(tables) > 1:
            before = (f"# section: {table.name}", *before)
            frames.append(_Frame(len(lines), len(lines) + high - low, before, after))
        else:
            pre.extend(before)
            post.extend(after)
        lines += [
            _row_line(rows[index], table.columns, pairs, free, labels, agent.precision)
            for index in range(low, high)
        ]

    def footer(first: int, last: int) -> str | None:
        step = pager(start + first, start + last) if limit else None
        return None if step is None or next_call is None else next_call(*step)

    if not lines:
        return "\n".join([*pre, f"# next: {footer(0, 0) or 'none'}"])
    return fit(
        pre,
        lines,
        footer=footer,
        max_chars=max_chars,
        keep_last=keep_last,
        post=post,
        frames=frames,
        cut_hint="; narrow fields to see the rest" if isinstance(result, Financials) else "",
    )


def refuses_offset(spec: Endpoint[Any, Any]) -> bool:
    """Whether the declaration of ``spec`` alone tells that its results take no offset, so no
    request is needed to find out: bars, statements, one record, or one row per code."""
    rule = spec.pagination
    return (
        spec.agent.row_cap == 1
        or spec.params is FinanceParams
        or (rule is not None and rule.style == "bars")
        or (rule is None and any(name in spec.params.model_fields for name in BATCH_PARAMS))
    )


def no_offset(spec: Endpoint[Any, Any]) -> InvalidParams:
    return InvalidParams(
        f"{spec.name} results take no offset",
        endpoint=spec.name,
        field="offset",
        hint="Follow the # next line; when it says none, raise limit or narrow fields.",
    )


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
    cut = [one.symbol for one, kline in zip(params, klines, strict=True) if _cut_off(one, kline)]
    if cut:
        head.append(f"# note: the bars of {', '.join(cut)} start after begin: {_CUT_BARS}")
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
    frames: Sequence[_Frame] = (),
    cut_hint: str = "",
) -> str:
    """Join ``head``, as many ``rows`` as fit in ``max_chars``, ``post`` and the ``# next:`` line.

    ``footer(first, stop)`` gets the slice of ``rows`` shown and returns the next call or None.
    Rows are dropped from the end, or from the start with ``keep_last``. ``frames`` wrap slices
    of ``rows`` in lines of their own, such as the title and columns of a table. When even one
    row does not fit, the next call skips it, so that the rows after it stay in reach.
    ``cut_hint`` follows the word on the cut, for rows no next call reaches.
    """
    fixed = sum(len(line) + 1 for line in (*head, *post))
    sizes = [len(row) + 1 for row in rows]
    total = sum(sizes)
    for shown in range(len(rows), -1, -1):
        first = len(rows) - shown if keep_last else 0
        if shown < len(rows):
            total -= sizes[first - 1] if keep_last else sizes[shown]
        stop = first + shown
        framing = sum(
            len(line) + 1
            for frame in frames
            if frame.start < stop and first < frame.stop
            for line in (*frame.before, *frame.after)
        )
        if shown:
            call = footer(first, stop)
        else:
            call = footer(max(first - 1, 0), max(stop, 1)) if rows else None
        line = f"# next: {call or 'none'}"
        if shown < len(rows):
            line += f"; truncated to {shown} of {len(rows)} rows by the {max_chars} character limit"
            line += cut_hint
        if not shown and rows:
            line += "; a row alone is over the limit: narrow fields to see it"
        if fixed + total + framing + len(line) <= max_chars or shown == 0:
            return "\n".join([*head, *_framed(rows, frames, first, stop), *post, line])
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


def series_stats(series: Series) -> dict[str, Any]:
    """Stats over every row of a series: the range of its index and, per numeric column, the
    last value and the extremes with when they happened; ties go to the earliest."""
    name, index = series.index()
    stats: dict[str, Any] = {"points": len(index)}
    if index:
        stats[f"first_{name}"], stats[f"last_{name}"] = index[0], index[-1]
    for column, values in series.columns.items():
        present = _present(values)
        if not present:
            continue
        stats[f"{column}_last"] = present[-1][1]
        for label, pick in (("max", max), ("min", min)):
            where, value = pick(present, key=lambda pair: pair[1])
            stats[f"{column}_{label}"] = value
            stats[f"{column}_{label}_{name}"] = index[where]
    return stats


def stats_line(stats: Mapping[str, Any], precision: int, tag: str = "stats") -> str:
    pairs = (f"{key}={cell(value, precision)}" for key, value in stats.items() if value is not None)
    return f"# {tag}: {' '.join(pairs)}"


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


def label_text(value: object) -> str:
    """Short text written by an owner, cleaned like prose and cut to ``LABEL_CHARS``."""
    text = clean_text(value)
    return text if len(text) <= LABEL_CHARS else text[: LABEL_CHARS - 1] + "…"


def csv_line(cells: Sequence[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(cells)
    line = buffer.getvalue()[:-1]
    # Bare, it would read as a header line, or close the fence around upstream text. The writer
    # left this cell unquoted, so it holds nothing that needs escaping.
    return f'"{cells[0]}"{line[len(cells[0]) :]}' if line.startswith("#") else line


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _record(
    head: list[str],
    row: Mapping[str, Any],
    wanted: Sequence[str],
    free: Sequence[str],
    labels: Sequence[str],
    precision: int,
    max_chars: int,
) -> str:
    lines = [
        f"{name}: {cell(row.get(name), precision)}"
        for name in wanted
        if name not in free and name not in labels
    ]
    if free or labels:
        prose = [
            f"{name}: {clean_text(row.get(name) or '')}"
            if name in free
            else f"{name}: {label_text(row.get(name) or '')}"
            for name in wanted
            if name in free or name in labels
        ]
        lines.append("\n".join([FENCE_BEGIN, *prose, FENCE_END]))
    return fit(head, lines, footer=lambda first, stop: None, max_chars=max_chars)


def _row_line(
    row: Mapping[str, Any],
    wanted: Sequence[str],
    pairs: set[str],
    free: Sequence[str],
    labels: Sequence[str],
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
        elif name in labels:
            cells.append("" if value is None else label_text(value))
        else:
            cells.append(cell(value, precision))
    return csv_line(cells)


def _framed(rows: Sequence[str], frames: Sequence[_Frame], first: int, stop: int) -> list[str]:
    """The lines of the rows ``first`` to ``stop``, with the frames of those shown."""
    if not frames:
        return list(rows[first:stop])
    lines: list[str] = []
    for frame in frames:
        shown = rows[max(frame.start, first) : min(frame.stop, stop)]
        if shown:
            lines += [*frame.before, *shown, *frame.after]
    return lines


def _lay_out(
    named: Sequence[tuple[str, list[dict[str, Any]]]], wanted: Sequence[str], *, explicit: bool
) -> tuple[list[dict[str, Any]], list[_Table], list[str]]:
    """One run of rows, where each table sits in it, and what a caller needs to know about each
    table with rows but no column to show, which is left out. A column the caller asked for stays
    as long as the table has it, a default one as long as the table has a value for it."""
    rows: list[dict[str, Any]] = []
    tables: list[_Table] = []
    left_out: list[str] = []
    for name, table in named:
        columns = tuple(
            column
            for column in wanted
            if any((column in row) if explicit else row.get(column) is not None for row in table)
        )
        if columns:
            tables.append(_Table(name, len(rows), len(rows) + len(table), columns))
            rows += table
        elif table:
            has = ", ".join(dict.fromkeys(key for row in table for key in row))
            left_out.append(f"{name} (rows={len(table)}, available: {has})")
    return rows, tables, left_out


def _info_lines(header: Mapping[str, Any], precision: int) -> list[str]:
    return [stats_line(header, precision, tag="info")] if header else []


def _row_mapping(row: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    return row.model_dump() if isinstance(row, BaseModel) else dict(row)


def _series_rows(series: Series) -> list[dict[str, Any]]:
    name, index = series.index()
    columns = series.columns
    return [
        {name: moment, **{column: values[row] for column, values in columns.items()}}
        for row, moment in enumerate(index)
    ]


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


def _present(column: Sequence[Any] | None) -> list[tuple[int, float]]:
    """The numbers of a column, with their positions."""
    return [
        (index, float(value))
        for index, value in enumerate(column or ())
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]


def _page_notes(spec: Endpoint[Any, Any], page: Page[Any]) -> list[str]:
    notes: list[str] = []
    if page.short and len(page):
        notes.append(
            f"this page has {len(page)} of {page.page_size} rows: the list may have ended, or the "
            "upstream's page size cap may be lower than page_size."
        )
    if page.capped and spec.pagination is not None:
        rule = spec.pagination
        notes.append(
            f"the upstream counts {page.total} rows and serves at most {rule.total_cap}. "
            f"{rule.cap_hint}".rstrip()
        )
    return notes


def _empty_note(agent: AgentInfo, dump: Mapping[str, Any], result: object) -> str:
    if isinstance(result, Page) and result.page > 1:
        return f"no rows on page {result.page}: the list ended before it."
    if agent.empty_hint:
        return agent.empty_hint
    if isinstance(result, Kline):
        return _EMPTY_BARS
    return _EMPTY if "symbol" in dump or "symbols" in dump else _EMPTY_NO_SYMBOL


def _no_pager(first: int, stop: int) -> tuple[Mapping[str, Any], int] | None:
    return None


def _row_pager(
    dump: Mapping[str, Any], total: int, keep_last: bool, following: Mapping[str, Any] | None
) -> Pager:
    """The same params with the rows shown so far as the offset while some are left, then
    ``following``, the params of the next page if there is one."""

    def pager(first: int, stop: int) -> tuple[Mapping[str, Any], int] | None:
        seen = total - first if keep_last else stop
        if seen < total:
            return dump, seen
        return None if following is None else (following, 0)

    return pager


def _bar_pager(params: KlineParams, kline: Kline) -> Pager:
    """Older bars: the same call ending just before the first bar shown, with ``end`` the day
    before it, or for minute bars with ``before`` its time.

    There are older bars when some fetched ones are not shown, or when a request going back came
    back full: any without ``begin``, or one whose dates hold more than it returns.
    """
    going_back = params.begin is None or params.count is None
    count = MAX_COUNT if params.begin is not None else params.count or DEFAULT_COUNT

    def pager(first: int, stop: int) -> tuple[Mapping[str, Any], int] | None:
        if first == 0 and not (going_back and came_back_full(kline, count)):
            return None
        update = older_range(kline.timestamps[first], kline.period)
        end = update["end"]
        if end is not None and params.begin is not None and end < params.begin:
            return None
        return params.model_copy(update=update).model_dump(mode="json", exclude_none=True), 0

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


def _cut_off(params: KlineParams, kline: Kline) -> bool:
    """Whether the dates hold more bars than one request returned, so the oldest are missing."""
    return (
        params.begin is not None
        and params.count is None
        and came_back_full(kline, MAX_COUNT)
        and kline.timestamps[0].date() > params.begin
    )


def _symbols_pager(dump: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], batch: str) -> Pager:
    """The codes of the rows not shown."""

    def pager(first: int, stop: int) -> tuple[Mapping[str, Any], int] | None:
        rest = [str(row.get("symbol")) for row in rows[stop:]]
        return ({**dump, batch: rest}, 0) if rest else None

    return pager


def _zone(params: BaseModel) -> ZoneInfo:
    symbol: object = getattr(params, "symbol", None)
    return normalize_symbol(symbol).timezone if isinstance(symbol, str) else DEFAULT_ZONE
