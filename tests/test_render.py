"""Text rendering helpers shared by the MCP server and the CLI; no mcp SDK needed."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict

from xueqiu_api import InvalidParams, Kline, RateLimited
from xueqiu_api._finance import Financials
from xueqiu_api._pages import Page, finish
from xueqiu_api._render import (
    ERROR_CHARS,
    FENCE_BEGIN,
    FENCE_END,
    FREE_TEXT_CHARS,
    LABEL_CHARS,
    cell,
    clean_text,
    compact_json,
    csv_line,
    error_text,
    fit,
    kline_stats,
    number,
    render,
)
from xueqiu_api.registry import Endpoint, Pagination, RowOrder
from xueqiu_api.symbols import SymbolParams

from .harness import synthetic_endpoint

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1e10, "10000000000"),
        (835024727.76, "835024727.76"),
        (0.1 + 0.2, "0.3"),
        (-8.593750000000007, "-8.5938"),
        (-0.00001, "0"),
        (1.5, "1.5"),
        (float("nan"), ""),
    ],
)
def test_numbers_are_rounded_without_exponents_or_separators(value: float, text: str) -> None:
    assert number(value, 4) == text


def test_cells() -> None:
    moment = datetime(2026, 11, 4, 15, 0, 0, 123_000, tzinfo=SHANGHAI)

    assert [cell(None, 4), cell(True, 4), cell(7, 4), cell(2.50, 4)] == ["", "true", "7", "2.5"]
    assert cell(moment, 4) == "2026-11-04T15:00:00+08:00"
    assert cell(date(2026, 9, 30), 4) == "2026-09-30"
    assert cell({"status_id": 7, "region": "CN"}, 4) == '{"status_id":7,"region":"CN"}'


def test_text_cells_stay_on_one_line() -> None:
    address = "One Apple Park Way\r\nCupertino\r\nCalifornia 95014"

    assert cell(address, 4) == "One Apple Park Way Cupertino California 95014"
    assert cell("x\n# next: forged\u2028y", 4) == "x # next: forged y"


def test_csv_lines_quote_like_rfc_4180() -> None:
    assert csv_line(["a", "b,c", 'say "hi"', ""]) == 'a,"b,c","say ""hi""",'


def test_free_text_is_stripped_of_html_collapsed_and_cut() -> None:
    text = clean_text("<p>Line  one</p>\n\n<b>bold</b> &amp; more " + "x" * 1000)

    assert text.startswith("Line one bold & more xxx")
    assert len(text) == FREE_TEXT_CHARS and text.endswith("…")
    assert "<" not in text and "\n" not in text


def test_error_text_is_one_line_of_json_within_the_cap() -> None:
    error = InvalidParams(
        "bad " * 600,
        endpoint="kline",
        field="fields",
        valid_values=[f"field_{index}" for index in range(400)],
        candidates=["field_1"],
        params={"symbol": "SH600000"},
    )

    text = error_text(error)

    payload = json.loads(text)
    assert len(text) <= ERROR_CHARS and "\n" not in text
    assert set(payload) == {
        "error_code",
        "recoverable",
        "message",
        "hint",
        "endpoint",
        "field",
        "valid_values",
        "candidates",
    }
    assert len(payload["message"]) == 500 and payload["message"].endswith("…")
    assert payload["valid_values"] == list(error.valid_values[: len(payload["valid_values"])])
    assert payload["candidates"] == ["field_1"]


def test_a_long_field_name_keeps_the_error_within_the_cap() -> None:
    error = InvalidParams("unknown parameter", field="x" * 3000, hint="Parameters: kline(symbol).")

    text = error_text(error)

    payload = json.loads(text)
    assert len(text) <= ERROR_CHARS
    assert len(payload["field"]) == 100 and payload["field"].endswith("…")
    assert payload["hint"] == "Parameters: kline(symbol)."


def test_error_text_keeps_retry_after_and_drops_upstream_details() -> None:
    error = RateLimited("slow down", status=429, retry_after_s=7.0, params={"symbols": ["X"]})

    payload = json.loads(error_text(error, hint="Wait."))

    assert payload == {
        "error_code": "rate_limited",
        "recoverable": "backoff",
        "message": "slow down",
        "hint": "Wait.",
        "retry_after_s": 7.0,
    }


def test_fit_cuts_rows_at_a_boundary_and_says_so_in_the_last_line() -> None:
    rows = [f"row-{index:03d}," + "x" * 90 for index in range(100)]

    text = fit(["# head"], rows, footer=lambda first, stop: f"next {first}", max_chars=1000)

    lines = text.splitlines()
    assert len(text) <= 1000
    assert lines[1] == rows[0]
    assert lines[-1].startswith("# next: next 0; truncated to ")
    assert lines[-1].endswith(" of 100 rows by the 1000 character limit")


def test_fit_keeps_the_latest_rows_of_a_series() -> None:
    rows = [f"bar-{index:03d}," + "x" * 90 for index in range(100)]

    text = fit(["# head"], rows, footer=lambda first, stop: None, max_chars=1000, keep_last=True)

    lines = text.splitlines()
    assert lines[-2] == rows[-1]
    assert lines[-1].startswith("# next: none; truncated to ")


def test_kline_stats_skip_missing_values_and_take_the_earliest_extreme() -> None:
    days = [datetime(2026, 11, day, tzinfo=SHANGHAI) for day in (2, 3, 4, 5)]
    kline = Kline(
        symbol="SH600000",
        period="day",
        adjust="before",
        timestamps=tuple(days),
        columns={
            "close": (None, 10.0, 11.0, 12.5),
            "high": (13.0, 13.0, 12.0, 12.9),
            "low": (9.0, 9.5, 9.0, 12.0),
        },
    )

    assert kline_stats(kline) == {
        "bars": 4,
        "first_date": "2026-11-03",
        "first_close": 10.0,
        "last_date": "2026-11-05",
        "last_close": 12.5,
        "base_close": 10.0,
        "change_pct": 25.0,
        "high": 13.0,
        "high_date": "2026-11-02",
        "low": 9.0,
        "low_date": "2026-11-02",
    }


def test_the_change_includes_the_first_bar_s_own_move() -> None:
    # The year's first bar rose 10% from the 100 close of the year before.
    days = [datetime(2026, 1, 2, tzinfo=SHANGHAI), datetime(2026, 11, 4, tzinfo=SHANGHAI)]
    kline = Kline(
        symbol="SH600000",
        period="day",
        adjust="before",
        timestamps=tuple(days),
        columns={"close": (110.0, 121.0), "percent": (10.0, 0.5)},
    )

    stats = kline_stats(kline)

    assert stats["base_close"] == pytest.approx(100.0)
    assert stats["change_pct"] == pytest.approx(21.0)


class PageParams(BaseModel):
    page: int = 1
    page_size: int = 20


class NoCodes(BaseModel):
    pass


class CubeParams(BaseModel):
    cube_symbols: tuple[str, ...]


class Row(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class Flow:
    """A series of the shape ``render`` takes."""

    dates: tuple[date, ...]
    columns: Mapping[str, tuple[float | None, ...]]

    def index(self) -> tuple[str, tuple[date, ...]]:
        return "date", self.dates

    def header(self) -> Mapping[str, Any]:
        return {"unit": "yuan"}

    def __len__(self) -> int:
        return len(self.dates)


@dataclass(frozen=True)
class Tables:
    """A result of named tables, of the shape ``render`` takes."""

    tables: Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]

    def header(self) -> Mapping[str, Any]:
        return {"holder": "fixed"}

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return self.tables

    def __len__(self) -> int:
        return sum(len(table) for table in self.tables.values())


@dataclass(frozen=True, slots=True)
class TablesPage(Page[Row]):
    """A page whose rows come from named tables, one of them only on the first page."""

    extra: tuple[Row, ...] = ()

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {"items": self.items, "extra": self.extra}


class Holdings(BaseModel):
    """A single model that holds tables."""

    holdings: tuple[Row, ...]

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {"holdings": self.holdings}

    def __len__(self) -> int:
        return len(self.holdings)


def spec_of(
    *fields: str,
    order: RowOrder | None = None,
    labels: tuple[str, ...] = (),
    free_text: tuple[str, ...] = (),
    params: type[BaseModel] = PageParams,
) -> Endpoint[Any, Any]:
    agent = replace(
        synthetic_endpoint().agent, default_fields=fields, labels=labels, free_text=free_text
    )
    return synthetic_endpoint(params=params, agent=agent, order=order)


def rows_of(count: int, start: int = 0) -> tuple[Row, ...]:
    return tuple(Row(id=f"r{index:04d}") for index in range(start, start + count))


def flow_of(count: int) -> Flow:
    dates = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(count))
    return Flow(dates, {"value": tuple(float(index) for index in range(count))})


MOMENT = datetime(2026, 11, 5, 10, 0, tzinfo=SHANGHAI)
ROW_LINE = re.compile(r"r\d{4}\b|\d{4}-\d{2}-\d{2}\b")
Fetch = Callable[[Mapping[str, Any]], object]


def next_of(params: Mapping[str, Any], offset: int) -> str:
    return compact_json({"params": params, "offset": offset})


def text_of(
    spec: Endpoint[Any, Any],
    result: object,
    *,
    params: Mapping[str, Any] | None = None,
    fields: Sequence[str] | None = None,
    limit: int = 50,
    max_chars: int = 20_000,
    offset: int = 0,
) -> str:
    return render(
        spec,
        spec.params.model_validate(params or {}),
        result,
        fields=fields,
        limit=limit,
        max_chars=max_chars,
        fetched_at=MOMENT,
        next_call=next_of,
        offset=offset,
    )


def next_step(text: str) -> tuple[Mapping[str, Any], int] | None:
    step = text.splitlines()[-1].removeprefix("# next: ").split("; truncated")[0]
    if step == "none":
        return None
    decoded = json.loads(step)
    return decoded["params"], decoded["offset"]


def follow(spec: Endpoint[Any, Any], fetch: Fetch, *, limit: int, max_chars: int) -> list[str]:
    """Follow ``# next`` to none, fetching for each call; the row keys in the order shown."""
    params: Mapping[str, Any] = {}
    offset = 0
    shown: list[str] = []
    for _ in range(2000):
        text = text_of(
            spec, fetch(params), params=params, limit=limit, max_chars=max_chars, offset=offset
        )
        assert len(text) <= max_chars
        assert "truncated to 0 " not in text
        shown += [line.split(",")[0] for line in text.splitlines() if ROW_LINE.match(line)]
        step = next_step(text)
        if step is None:
            return shown
        params, offset = step
    raise AssertionError("no progress")


def page_fetch(total: int, end: Any) -> Fetch:
    rule = Pagination(style="page", end=end)

    def fetch(params: Mapping[str, Any]) -> object:
        checked = PageParams.model_validate(params)
        first = (checked.page - 1) * checked.page_size
        items = rows_of(min(checked.page_size, max(total - first, 0)), first)
        count = total if end == "total" else None
        page = Page(items, page=checked.page, page_size=checked.page_size, total=count)
        return finish(rule, page, checked.model_dump())

    return fetch


def tables_page_fetch(total: int) -> Fetch:
    rule = Pagination(style="page", end="short")

    def fetch(params: Mapping[str, Any]) -> object:
        checked = PageParams.model_validate(params)
        first = (checked.page - 1) * checked.page_size
        items = rows_of(min(checked.page_size, max(total - first, 0)), first)
        extra = rows_of(3, 9000) if checked.page == 1 else ()
        page = TablesPage(items, page=checked.page, page_size=checked.page_size, extra=extra)
        return finish(rule, page, checked.model_dump())

    return fetch


LIMITS = [1, 7, 50]
BUDGETS = [500, 560, 650, 900, 2000, 20_000]
ALL_ROWS = [row.id for row in rows_of(95)]


@pytest.mark.parametrize("limit", LIMITS)
@pytest.mark.parametrize("max_chars", BUDGETS)
@pytest.mark.parametrize("order", ["newest_first", "oldest_first", None])
@pytest.mark.parametrize("end", ["total", "empty"])
def test_following_next_shows_each_row_of_a_page_list_once(
    limit: int, max_chars: int, order: RowOrder | None, end: Any
) -> None:
    shown = follow(
        spec_of("id", "value", order=order),
        page_fetch(95, end),
        limit=limit,
        max_chars=max_chars,
    )

    assert sorted(shown) == ALL_ROWS


@pytest.mark.parametrize("limit", LIMITS)
@pytest.mark.parametrize("max_chars", BUDGETS)
def test_following_next_shows_each_row_of_a_series_once(limit: int, max_chars: int) -> None:
    series = flow_of(137)
    spec = spec_of("date", "value", order="oldest_first", params=NoCodes)

    shown = follow(spec, lambda params: series, limit=limit, max_chars=max_chars)

    assert sorted(shown) == [day.isoformat() for day in series.dates]
    if limit == 1:
        assert shown == [day.isoformat() for day in reversed(series.dates)]


@pytest.mark.parametrize("limit", LIMITS)
@pytest.mark.parametrize("max_chars", BUDGETS)
@pytest.mark.parametrize("order", ["newest_first", "oldest_first"])
def test_following_next_shows_each_row_of_several_tables_once(
    limit: int, max_chars: int, order: RowOrder
) -> None:
    tables = Tables({"first": rows_of(17), "none": (), "second": rows_of(23, 17)})
    spec = spec_of("id", order=order, params=NoCodes)

    shown = follow(spec, lambda params: tables, limit=limit, max_chars=max_chars)

    assert sorted(shown) == [row.id for row in rows_of(40)]


@pytest.mark.parametrize("limit", LIMITS)
@pytest.mark.parametrize("max_chars", BUDGETS)
def test_following_next_shows_each_row_of_a_page_of_tables_once(limit: int, max_chars: int) -> None:
    shown = follow(spec_of("id"), tables_page_fetch(95), limit=limit, max_chars=max_chars)

    assert sorted(shown) == [*ALL_ROWS, "r9000", "r9001", "r9002"]


def test_a_cut_keeps_the_latest_rows_of_oldest_first_results() -> None:
    spec = spec_of("date", "value", order="oldest_first", params=NoCodes)

    lines = text_of(spec, flow_of(10), limit=3).splitlines()

    assert lines[-4:-1] == ["2026-01-08,7", "2026-01-09,8", "2026-01-10,9"]
    assert next_step("\n".join(lines)) == ({}, 3)


def test_other_orders_keep_the_first_rows() -> None:
    for order in ("newest_first", "rank", None):
        tables = Tables({"only": rows_of(10)})

        text = text_of(spec_of("id", order=order, params=NoCodes), tables, limit=2)

        assert [line for line in text.splitlines() if ROW_LINE.match(line)] == ["r0000", "r0001"]


def test_series_stats_cover_every_row_and_the_header_goes_first() -> None:
    spec = spec_of("date", "value", order="oldest_first", params=NoCodes)

    lines = text_of(spec, flow_of(10), limit=1).splitlines()

    assert lines[1] == "# info: unit=yuan"
    assert lines[2] == (
        "# stats: points=10 first_date=2026-01-01 last_date=2026-01-10 value_last=9 "
        "value_max=9 value_max_date=2026-01-10 value_min=0 value_min_date=2026-01-01"
    )


def test_a_series_without_rows_has_no_range() -> None:
    spec = spec_of("date", "value", order="oldest_first", params=NoCodes)

    text = text_of(spec, Flow((), {"value": ()}))

    assert "# stats: points=0\n" in text
    assert text.splitlines()[-1] == "# next: none"


def test_several_tables_are_titled_and_one_table_is_not() -> None:
    spec = spec_of("id", params=NoCodes)

    two = text_of(spec, Tables({"a": rows_of(1), "b": (), "c": rows_of(1, 1)})).splitlines()
    one = text_of(spec, Tables({"a": rows_of(2), "b": ()})).splitlines()

    assert two[-7:-1] == ["# section: a", "id", "r0000", "# section: c", "id", "r0001"]
    assert "# note: empty sections: b" in two
    assert not any(line.startswith("# section") for line in one)
    assert one[-4:-1] == ["id", "r0000", "r0001"]


def test_a_table_keeps_its_title_when_a_cut_drops_its_first_rows() -> None:
    spec = spec_of("id", order="oldest_first", params=NoCodes)
    tables = Tables({"a": rows_of(3), "b": rows_of(3, 3)})

    lines = text_of(spec, tables, limit=2).splitlines()

    assert lines[-5:-1] == ["# section: b", "id", "r0004", "r0005"]
    assert not any("# section: a" in line for line in lines)


def test_tables_show_only_their_own_columns() -> None:
    spec = spec_of("id", "price", "pay_date", params=NoCodes)
    tables = Tables(
        {
            "a": [{"id": "r0001", "price": 1.5, "pay_date": None}],
            "b": [{"id": "r0002", "price": None, "pay_date": None}],
        }
    )

    lines = text_of(spec, tables).splitlines()

    assert lines[-7:-1] == [
        "# section: a",
        "id,price",
        "r0001,1.5",
        "# section: b",
        "id",
        "r0002",
    ]


def test_requested_fields_leave_out_a_table_without_them() -> None:
    spec = spec_of("id", params=NoCodes)
    tables = Tables({"a": [{"id": "r0001", "price": 1.5}], "b": [{"id": "r0002"}]})

    lines = text_of(spec, tables, fields=["price"]).splitlines()

    assert lines[-3:-1] == ["price", "1.5"]
    assert not any(line.startswith("# section") for line in lines)


def test_a_model_holding_tables_is_not_a_record() -> None:
    result = Holdings(holdings=rows_of(3))

    text = text_of(spec_of("id", params=NoCodes), result, limit=1, offset=1)

    assert [line for line in text.splitlines() if ROW_LINE.match(line)] == ["r0001"]
    assert next_step(text) == ({}, 2)


def test_a_page_says_where_it_is() -> None:
    page = Page(rows_of(2), page=2, page_size=2, total=9, last_page=5)

    assert "# page: page=2 page_size=2 total=9 last_page=5" in text_of(spec_of("id"), page)


def test_a_page_shown_in_full_gives_its_next_params_without_an_offset() -> None:
    page = Page(rows_of(2), page=1, page_size=2, next_params={"page": 2, "page_size": 2})

    assert next_step(text_of(spec_of("id"), page)) == ({"page": 2, "page_size": 2}, 0)


def test_an_offset_past_the_page_goes_on_to_the_next_one() -> None:
    page = Page(rows_of(2), page=1, page_size=2, next_params={"page": 2, "page_size": 2})

    text = text_of(spec_of("id"), page, offset=5)

    assert not any(ROW_LINE.match(line) for line in text.splitlines())
    assert next_step(text) == ({"page": 2, "page_size": 2}, 0)


def test_limit_zero_shows_no_rows_and_no_next() -> None:
    page = Page(rows_of(2), page=1, page_size=2, next_params={"page": 2, "page_size": 2})

    text = text_of(spec_of("id"), page, limit=0)

    assert not any(ROW_LINE.match(line) for line in text.splitlines())
    assert text.splitlines()[-1] == "# next: none"


@pytest.mark.parametrize(
    ("spec", "result", "params"),
    [
        (
            spec_of("timestamp", params=SymbolParams),
            Kline("SH600000", "day", "before", (), {}),
            {"symbol": "SH600000"},
        ),
        (
            spec_of("report_date", params=SymbolParams),
            Financials("SH600000", "X", "CNY", (), {}),
            {"symbol": "SH600000"},
        ),
        (spec_of("id", params=NoCodes), Row(id="r0001"), {}),
        (spec_of("symbol", params=CubeParams), [], {"cube_symbols": ["ZH1"]}),
    ],
    ids=["kline", "financials", "record", "batch"],
)
def test_results_with_their_own_next_take_no_offset(
    spec: Endpoint[Any, Any], result: object, params: Mapping[str, Any]
) -> None:
    with pytest.raises(InvalidParams) as caught:
        text_of(spec, result, params=params, offset=1)

    assert caught.value.field == "offset"


LONG_LABEL = "<b>Portfolio</b>\n" + "名" * 60


def test_labels_are_shown_by_default_fenced_and_short() -> None:
    spec = spec_of("id", "name", labels=("name",), params=NoCodes)

    lines = text_of(spec, [Row(id="r0001", name=LONG_LABEL)]).splitlines()

    begin = lines.index(FENCE_BEGIN)
    assert lines[begin + 1] == "id,name"
    label = lines[begin + 2].split(",")[1]
    assert lines[begin + 3] == FENCE_END
    assert len(label) == LABEL_CHARS and label.endswith("…")
    assert label.startswith("Portfolio 名") and "<" not in label


def test_a_short_label_is_left_whole() -> None:
    spec = spec_of("id", "name", labels=("name",), params=NoCodes)

    lines = text_of(spec, [Row(id="r0001", name="看人品")]).splitlines()

    assert "r0001,看人品" in lines


def test_a_label_in_a_record_is_fenced_and_short() -> None:
    spec = spec_of("id", "name", labels=("name",), params=NoCodes)

    lines = text_of(spec, Row(id="r0001", name=LONG_LABEL)).splitlines()

    begin = lines.index(FENCE_BEGIN)
    assert "id: r0001" in lines[:begin]
    assert lines[begin + 1].startswith("name: Portfolio")
    assert len(lines[begin + 1].removeprefix("name: ")) == LABEL_CHARS
    assert lines[begin + 2] == FENCE_END


def test_only_the_table_holding_a_label_is_fenced() -> None:
    spec = spec_of("id", "name", labels=("name",), params=NoCodes)
    tables = Tables({"a": [Row(id="r0001", name="x")], "b": [Row(id="r0002")]})

    lines = text_of(spec, tables).splitlines()

    assert lines[-9:-1] == [
        "# section: a",
        FENCE_BEGIN,
        "id,name",
        "r0001,x",
        FENCE_END,
        "# section: b",
        "id",
        "r0002",
    ]


def test_missing_cube_symbols_get_a_no_data_note_and_the_rest_a_next() -> None:
    spec = spec_of("symbol", params=CubeParams)
    params = {"cube_symbols": ["ZH1", "ZH2", "ZH3"]}
    rows = [Row(id="a", symbol="ZH1"), Row(id="b", symbol="ZH3")]

    text = text_of(spec, rows, params=params, limit=1)

    assert "# note: no data for ZH2\n" in text
    assert next_step(text) == ({"cube_symbols": ["ZH3"]}, 0)


def test_missing_symbols_keep_their_market_advice() -> None:
    class Quotes(BaseModel):
        symbols: tuple[str, ...]

    spec = spec_of("symbol", params=Quotes)

    text = text_of(spec, [Row(id="a", symbol="SH1")], params={"symbols": ["SH1", "SH2"]})

    assert "# note: no data for SH2; check the market prefix" in text
