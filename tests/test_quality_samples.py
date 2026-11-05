"""Quality checks on whole responses: the replay fixtures always, real samples when available.

Real samples are local and uncommitted. ``XUEQIU_SAMPLES_DIR`` points at them: unset, that part
is skipped; set, the directory must exist and every check must have checked something.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
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
STATEMENT_FIXTURES: list[tuple[Statement, Path]] = [
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
OF_EVERY_KIND_OF_COMPANY = {
    "fin.balance_identity",
    "fin.income_identity",
    "fin.cash_flow_identity",
    "fin.ratio_units",
}
"""Checks a bank, a non-financial company, an insurer and a broker all have the items for."""
A_SHARE_ORG_TYPES = {1, 2, 3, 4}
"""Non-financial, bank, insurer and broker (measured 2026-11-05)."""
LACKING_IN_REPLAY_STATEMENTS = {
    "fin.balance_subtotals",
    "fin.equity_split",
    "fin.income_identity",
}
"""The replay statements are cut down to a few line items, and these checks need others. They run
on synthetic statements in ``test_quality`` and on real ones here when samples are available."""
KNOWN_ERRORS = {
    # .INX has bars on two days XNYS has no session, 2012-10-29 and 2012-10-30, with chg 0, and on
    # Saturday 2015-06-20 (measured 2026-11-05). These days keep it from being a calendar source.
    (".INX day normal", "kline.gaps_vs_calendar", "2012-10-29"),
    (".INX day normal", "kline.gaps_vs_calendar", "2012-10-30"),
    (".INX day normal", "kline.gaps_vs_calendar", "2015-06-20"),
    # HKHSI has rows on 14 days 00700 has none since 2012-11-01, closed days with volume 0 or 1 and
    # one on 2015-07-01 with a stray volume (measured 2026-11-05). They keep it from being a source.
    *(
        ("HKHSI day normal", "kline.gaps_vs_calendar", day)
        for day in (
            "2012-12-26",
            "2013-02-11",
            "2013-02-12",
            "2013-02-13",
            "2013-04-01",
            "2013-04-04",
            "2013-05-01",
            "2013-05-17",
            "2013-07-01",
            "2013-09-20",
            "2015-04-07",
            "2015-05-01",
            "2015-07-01",
            "2021-10-13",
        )
    ),
}
"""Errors the real samples are known to have: (subject, check, where)."""


def replay_bars_report(path: Path) -> QualityReport:
    """The checks of a replay series, taken as unadjusted unless its params say otherwise, against
    a calendar of its own days."""
    case = json.loads(path.read_text(encoding="utf-8"))
    bars = parse_bars({"type": "normal", **case["params"]}, case["body"]["data"])
    days = tuple(stamp.date() for stamp in bars.timestamps)
    market = normalize_symbol(bars.symbol).market
    calendar = TradingCalendar(market, bars.symbol, days, SAMPLES_FETCHED_AT)
    return check_kline(bars, calendar=calendar, as_of=SAMPLES_FETCHED_AT)


@pytest.mark.parametrize("path", KLINE_FIXTURES, ids=lambda path: path.stem)
def test_replay_bars_pass(path: Path) -> None:
    report = replay_bars_report(path)

    assert {item.status for item in report.results} <= {"pass", "skipped"}, report
    assert report.ok


def test_replay_bars_run_every_check_of_bars() -> None:
    checked: Counter[str] = Counter()
    for path in KLINE_FIXTURES:
        for item in replay_bars_report(path).results:
            checked[item.check] += item.checked

    assert set(checked) == {check for check in EVERY_CHECK if check.startswith("kline.")}
    assert {check for check, rows in checked.items() if rows <= 0} == set()


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


def test_replay_statements_run_every_check_they_have_the_items_for() -> None:
    checked: Counter[str] = Counter()
    for statement, path in STATEMENT_FIXTURES:
        case = json.loads(path.read_text(encoding="utf-8"))
        report = check_financials(
            parse_statement(statement, case["params"], case["body"]["data"]), statement
        )
        for item in report.results:
            checked[item.check] += item.checked

    assert set(checked) == {check for check in EVERY_CHECK if check.startswith("fin.")}
    assert {check for check, rows in checked.items() if rows <= 0} <= LACKING_IN_REPLAY_STATEMENTS


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
    if value is None:
        return None
    assert value and Path(value).is_dir(), (
        f"XUEQIU_SAMPLES_DIR is set to {value!r}, which is not a directory"
    )
    return Path(value)


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
    checked_by_org_type: defaultdict[int, Counter[str]] = field(
        default_factory=lambda: defaultdict[int, Counter[str]](Counter)
    )


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
            report = check_financials(statements, statement)
            run.reports.append(report)
            if normalize_symbol(statements.symbol).market == "CN":
                counts = run.checked_by_org_type[statements.extra["org_type"]]
                for item in report.results:
                    counts[item.check] += item.checked
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


def uncovered_company_checks(
    checked_by_org_type: Mapping[int, Counter[str]],
) -> set[tuple[int, str]]:
    """The (org type, check) pairs of ``OF_EVERY_KIND_OF_COMPANY`` that read no row."""
    return {
        (org_type, check)
        for org_type in A_SHARE_ORG_TYPES
        for check in OF_EVERY_KIND_OF_COMPANY
        if checked_by_org_type.get(org_type, Counter[str]())[check] <= 0
    }


def test_real_samples_cover_every_kind_of_a_share_company(real_run: SampleRun) -> None:
    assert uncovered_company_checks(real_run.checked_by_org_type) == set()


def test_a_kind_of_company_whose_identities_were_all_skipped_is_uncovered() -> None:
    full = Counter(dict.fromkeys(OF_EVERY_KIND_OF_COMPANY, 2))
    covered = {org_type: full for org_type in A_SHARE_ORG_TYPES}

    assert uncovered_company_checks(covered) == set()
    assert uncovered_company_checks({**covered, 3: full - Counter({"fin.ratio_units": 2})}) == {
        (3, "fin.ratio_units")
    }
    assert uncovered_company_checks({1: full, 2: full, 4: full}) == {
        (3, check) for check in OF_EVERY_KIND_OF_COMPANY
    }


def test_samples_are_skipped_when_the_variable_is_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XUEQIU_SAMPLES_DIR", raising=False)
    assert samples_root() is None


def test_an_empty_variable_fails_and_does_not_read_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XUEQIU_SAMPLES_DIR", "")
    with pytest.raises(AssertionError, match="not a directory"):
        samples_root()


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
