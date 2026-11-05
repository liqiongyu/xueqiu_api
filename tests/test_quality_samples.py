"""Quality checks on whole responses: the replay fixtures always, real samples when available.

Real samples are local and uncommitted. ``XUEQIU_SAMPLES_DIR`` points at them: unset, that part
is skipped; set, the directory must exist and every check must have checked something.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast, get_args
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY
from xueqiu_api._finance import Financials
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.kline import Kline
from xueqiu_api.quality import QualityReport, Statement, check_financials, check_kline
from xueqiu_api.symbols import normalize_symbol
from xueqiu_api.trading_calendar import CALENDAR_SOURCES, TradingCalendar, calendar_from_kline

from .harness import REPLAY_DIR

STATEMENTS: tuple[Statement, ...] = get_args(Statement)
KLINE_FIXTURES = sorted((REPLAY_DIR / "kline").glob("*.json"))
STATEMENT_FIXTURES = [
    (statement, path)
    for statement in STATEMENTS
    for path in sorted((REPLAY_DIR / f"finance_{statement}").glob("*.json"))
]
STATEMENT_PATH = re.compile(r"/v5/stock/finance/[a-z]+/(balance|income|cash_flow)\.json")
SAMPLES_FETCHED_AT = datetime(2026, 11, 5, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
FULL_SERIES_BARS = 1000
EVERY_CHECK = {
    "kline.timestamps_increasing",
    "kline.no_duplicate_day",
    "kline.daily_at_midnight",
    "kline.ohlc_order",
    "kline.price_positive",
    "kline.volume_nonneg",
    "kline.vwap_in_range",
    "kline.chg_matches_close",
    "kline.gaps_vs_calendar",
    "kline.last_bar_partial",
    "kline.null_policy",
    "fin.balance_identity",
    "fin.balance_subtotals",
    "fin.equity_split",
    "fin.income_identity",
    "fin.cash_flow_identity",
    "fin.ratio_units",
    "fin.period_dates",
    "fin.currency_present",
}
KNOWN_ERRORS = {
    # .INX has bars on two days XNYS has no session, 2012-10-29 and 2012-10-30, with chg 0, and on
    # Saturday 2015-06-20 (measured 2026-11-05). These days keep it from being a calendar source.
    (".INX day normal", "kline.gaps_vs_calendar", "2012-10-29"),
    (".INX day normal", "kline.gaps_vs_calendar", "2012-10-30"),
    (".INX day normal", "kline.gaps_vs_calendar", "2015-06-20"),
}
"""Errors the real samples are known to have: (subject, check, where)."""


@pytest.mark.parametrize("path", KLINE_FIXTURES, ids=lambda path: path.stem)
def test_replay_bars_pass(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    report = check_kline(parse_bars(case["params"], case["body"]["data"]), as_of=SAMPLES_FETCHED_AT)

    assert {item.status for item in report.results} <= {"pass", "skipped"}, report
    assert report.ok


@pytest.mark.parametrize(
    ("statement", "path"),
    STATEMENT_FIXTURES,
    ids=[f"{statement}-{path.stem}" for statement, path in STATEMENT_FIXTURES],
)
def test_replay_statements_pass(statement: Statement, path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    statements = parse_statement(statement, case["params"], case["body"]["data"])
    report = check_financials(statements, statement)

    assert {item.status for item in report.results} <= {"pass", "skipped"}, report
    assert report.ok
    if statement == "balance":
        checked = next(item for item in report.results if item.check == "fin.balance_identity")
        assert checked.checked > 0


def parse_bars(query: dict[str, Any], data: Any) -> Kline:
    params = KLINE.validate(
        {
            "symbol": query["symbol"],
            "period": query.get("period", "day"),
            "adjust": query.get("type", "before"),
        }
    )
    return KLINE.parse(data, params)


def parse_statement(statement: Statement, query: dict[str, Any], data: Any) -> Financials:
    spec = REGISTRY[f"finance_{statement}"]
    return spec.parse(data, spec.validate({"symbol": query["symbol"]}))


def samples_root() -> Path | None:
    """The directory of real samples, or None when ``XUEQIU_SAMPLES_DIR`` is not set."""
    value = os.environ.get("XUEQIU_SAMPLES_DIR")
    if not value:
        return None
    root = Path(value)
    assert root.is_dir(), f"XUEQIU_SAMPLES_DIR is set to {value}, which is not a directory"
    return root


def recorded_responses(root: Path) -> Iterator[tuple[str, dict[str, Any], Any]]:
    """(path, query, body) of each response recorded under ``root``, in both layouts it has: one
    file with a group of ``probes``, and one file for one response."""
    for file in sorted(root.rglob("*.json")):
        recorded: Any = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(recorded, dict):
            continue
        fields = cast(dict[str, Any], recorded)
        if isinstance(fields.get("probes"), dict):
            for probe in cast(dict[str, Any], fields["probes"]).values():
                request = probe["request"]
                yield request["path"], request["params"], probe["body"]
        elif "path" in fields and "body" in fields:
            yield fields["path"], fields["query"], fields["body"]


@dataclass
class SampleRun:
    reports: list[QualityReport] = field(default_factory=list[QualityReport])
    full_series: int = 0
    org_types: set[int] = field(default_factory=set[int])


def run_samples(root: Path) -> SampleRun:
    """Every check over every recorded series and statement under ``root``."""
    bars: list[Kline] = []
    run = SampleRun()
    for path, query, body in recorded_responses(root):
        envelope = cast(dict[str, Any], body) if isinstance(body, dict) else {}
        data = envelope.get("data")
        if not isinstance(data, dict):
            continue
        fields = cast(dict[str, Any], data)
        if path == KLINE.path and fields.get("item"):
            bars.append(parse_bars(query, data))
        elif (match := STATEMENT_PATH.fullmatch(path)) and fields.get("list"):
            statement = cast(Statement, match[1])
            statements = parse_statement(statement, query, data)
            run.reports.append(check_financials(statements, statement))
            if normalize_symbol(statements.symbol).market == "CN":
                run.org_types.add(statements.extra["org_type"])
    calendars = source_calendars(bars)
    for kline in bars:
        calendar = calendars[normalize_symbol(kline.symbol).market]
        run.reports.append(check_kline(kline, calendar=calendar, as_of=SAMPLES_FETCHED_AT))
        run.full_series += len(kline) >= FULL_SERIES_BARS
    return run


def source_calendars(bars: list[Kline]) -> dict[str, TradingCalendar]:
    calendars: dict[str, TradingCalendar] = {}
    for market, source in CALENDAR_SOURCES.items():
        series = [
            kline
            for kline in bars
            if (kline.symbol, kline.period, kline.adjust) == (source.symbol, "day", "normal")
        ]
        assert series, f"the samples have no unadjusted daily bars of {source.symbol}"
        longest = max(series, key=len)
        calendars[market] = calendar_from_kline(longest, fetched_at=SAMPLES_FETCHED_AT)
    return calendars


@pytest.fixture(scope="module")
def real_run() -> SampleRun:
    root = samples_root()
    if root is None:
        pytest.skip("XUEQIU_SAMPLES_DIR is not set")
    return run_samples(root)


def test_real_samples_run_every_check(real_run: SampleRun) -> None:
    checked: Counter[str] = Counter()
    for report in real_run.reports:
        for item in report.results:
            checked[item.check] += item.checked

    assert real_run.full_series > 0
    assert {check for check in EVERY_CHECK if checked[check] <= 0} == set()
    assert set(checked) == EVERY_CHECK


def test_real_samples_have_only_the_known_errors(real_run: SampleRun) -> None:
    errors = {
        (report.subject, item.check, finding.where)
        for report in real_run.reports
        for item in report.results
        for finding in item.findings
        if finding.severity == "error"
    }
    assert errors <= KNOWN_ERRORS


def test_real_samples_cover_every_kind_of_a_share_company(real_run: SampleRun) -> None:
    assert real_run.org_types >= {1, 2, 3, 4}


def test_samples_are_skipped_when_the_variable_is_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XUEQIU_SAMPLES_DIR", raising=False)
    assert samples_root() is None


def test_a_directory_that_does_not_exist_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XUEQIU_SAMPLES_DIR", str(tmp_path / "missing"))
    with pytest.raises(AssertionError, match="not a directory"):
        samples_root()


def test_a_directory_without_samples_fails(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="no unadjusted daily bars"):
        run_samples(tmp_path)


def test_both_recorded_layouts_are_read(tmp_path: Path) -> None:
    case = json.loads((REPLAY_DIR / "kline" / "sh600000_day.json").read_text(encoding="utf-8"))
    query = {"symbol": "SH600000", "period": "day", "type": "normal"}
    grouped = {
        "id": "kline",
        "probes": {"CN": {"request": {"path": KLINE.path, "params": query}, "body": case["body"]}},
    }
    single = {"path": KLINE.path, "query": query, "status": 200, "body": case["body"]}
    (tmp_path / "group").mkdir()
    (tmp_path / "group" / "kline.json").write_text(json.dumps(grouped), encoding="utf-8")
    (tmp_path / "one.json").write_text(json.dumps(single), encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps(["not a response"]), encoding="utf-8")

    found = list(recorded_responses(tmp_path))

    assert [path for path, _, _ in found] == [KLINE.path, KLINE.path]
    assert all(body == case["body"] for _, _, body in found)
