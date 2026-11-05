"""Text rendering helpers shared by the MCP server and the CLI; no mcp SDK needed."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import InvalidParams, Kline, RateLimited
from xueqiu_api._render import (
    ERROR_CHARS,
    FREE_TEXT_CHARS,
    cell,
    clean_text,
    csv_line,
    error_text,
    fit,
    kline_stats,
    number,
)

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
