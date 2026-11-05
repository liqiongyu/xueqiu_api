"""Contract of every registered endpoint: what it asks the upstream, what it does with errors."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from pydantic import BaseModel

from xueqiu_api import REGISTRY, Page, UpstreamChanged, UpstreamError
from xueqiu_api._core import unwrap
from xueqiu_api._finance import Financials
from xueqiu_api._model import UpstreamModel
from xueqiu_api._render import Sectioned, Series
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.endpoints.kline import Kline
from xueqiu_api.registry import Endpoint, Envelope
from xueqiu_api.sites import SITES

from .harness import FAKE_LOGIN, REPLAY_DIR, Harness, json_reply

HERE = Path(__file__).parent
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
ENDPOINTS = sorted(REGISTRY)
ONE_ENDPOINT_PER_HOST = sorted({spec.host: name for name, spec in REGISTRY.items()}.values())
TIME_ORDERED = sorted(
    name for name, spec in REGISTRY.items() if spec.order in ("oldest_first", "newest_first")
)
TIME_NAME = re.compile(r"date|time|_at$|^ts$")
"""Field names that say a value is a point in time."""
ROW_TIME_FIELDS = (
    "timestamp",
    "trade_date",
    "month",
    "report_date",
    "chg_date",
    "change_date",
    "repurchase_date",
    "ex_date",
    "event_date",
    "date",
)
"""Fields that order the rows of a table, whichever one the table has."""
ROUTE_HOSTS = {
    route["name"]: route["host"]
    for route in json.loads((HERE / "snapshots" / "endpoint_routes.json").read_text("utf-8"))
}
"""Hosts of the table kept by hand: a request is held to that, and not to its own declaration."""
UPSTREAM_ERRORS: Mapping[Envelope, Any] = {
    "xueqiu": {"error_code": 99999, "error_description": "synthetic"},
    "query": {"code": 99999, "success": False, "message": "synthetic"},
    "danjuan": {"result_code": 99999, "message": "synthetic"},
    "bare": {"error_code": "99999", "error_description": "synthetic"},
}


class RecordingReplay(ReplayTransport):
    """The replay transport, keeping the requests it answered."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.seen: list[httpx2.Request] = []

    def _respond(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        return super()._respond(request)


def token_for(spec: Endpoint[Any, Any]) -> str | None:
    return FAKE_LOGIN if spec.auth == "required" else None


def example_query(spec: Endpoint[Any, Any]) -> dict[str, str]:
    return spec.build_query(spec.validate(spec.agent.examples[0]), NOW)


def fixture_params(name: str) -> list[dict[str, str]]:
    cases = [
        json.loads(path.read_text(encoding="utf-8")) for path in (REPLAY_DIR / name).glob("*.json")
    ]
    return [{key: str(value) for key, value in case.get("params", {}).items()} for case in cases]


@pytest.mark.parametrize("name", ENDPOINTS)
def test_the_example_query_covers_a_replay_fixture(name: str) -> None:
    query = example_query(REGISTRY[name])

    # A file without params answers every request, so it says nothing unless the query is empty.
    assert any(
        (params or not query) and params.items() <= query.items() for params in fixture_params(name)
    )


@pytest.mark.parametrize("name", ENDPOINTS)
def test_requests_use_the_profile_of_the_site(make: Callable[..., Harness], name: str) -> None:
    spec = REGISTRY[name]
    site = SITES[spec.host]
    transport = RecordingReplay(REPLAY_DIR)
    h = make(token=token_for(spec), transport=transport)

    h.call(name, **spec.agent.examples[0])

    [request] = [sent for sent in transport.seen if sent.url.path != "/"]
    assert request.url.host == ROUTE_HOSTS[name]
    for header, value in site.headers.items():
        assert request.headers[header] == value
    if not (site.cookies and spec.cookies):
        assert "cookie" not in request.headers
    elif spec.auth == "required":
        assert request.headers["cookie"] == FAKE_LOGIN
    else:
        assert "xq_a_token=replay-anonymous" in request.headers["cookie"]


@pytest.mark.parametrize("name", ONE_ENDPOINT_PER_HOST)
def test_site_headers_leave_host_and_encoding_to_the_http_client(
    make: Callable[..., Harness], name: str
) -> None:
    spec = REGISTRY[name]
    transport = RecordingReplay(REPLAY_DIR)
    h = make(token=token_for(spec), transport=transport)

    h.call(name, **spec.agent.examples[0])

    [request] = [sent for sent in transport.seen if sent.url.path != "/"]
    with httpx2.Client() as plain:
        assert request.headers["accept-encoding"] == plain.headers["accept-encoding"]
    assert request.headers["host"] == request.url.host
    written = {header.lower() for header in SITES[spec.host].headers}
    assert not written & {"host", "accept-encoding", "cookie"}


@pytest.mark.parametrize("name", ENDPOINTS)
def test_an_error_code_in_the_envelope_is_upstream_error(
    make: Callable[..., Harness], name: str
) -> None:
    spec = REGISTRY[name]
    h = make(token=token_for(spec))
    path = spec.path.format(**example_query(spec))
    h.upstream.route(spec.host, path, json_reply(UPSTREAM_ERRORS[spec.envelope]))

    with pytest.raises(UpstreamError) as caught:
        h.call(name, **spec.agent.examples[0])

    assert (caught.value.endpoint, caught.value.upstream_code) == (name, "99999")
    assert caught.value.message.endswith("synthetic")


@pytest.mark.parametrize("name", ENDPOINTS)
def test_a_body_outside_the_envelope_is_upstream_changed(
    make: Callable[..., Harness], name: str
) -> None:
    spec = REGISTRY[name]
    h = make(token=token_for(spec))
    path = spec.path.format(**example_query(spec))
    h.upstream.route(spec.host, path, json_reply(["unexpected"]))

    with pytest.raises(UpstreamChanged) as caught:
        h.call(name, **spec.agent.examples[0])

    assert caught.value.endpoint == name


def sample_marks(source: str) -> set[str]:
    """Endpoint names in the ``pytest.mark.sample`` calls of a test module."""
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]
    return {
        ast.literal_eval(call.args[0])
        for call in calls
        if ast.unparse(call.func) == "pytest.mark.sample"
    }


def test_every_endpoint_has_a_sample() -> None:
    marks = [sample_marks(path.read_text(encoding="utf-8")) for path in HERE.glob("*.py")]

    assert set[str]().union(*marks) == set(REGISTRY)


def test_the_sample_scan_reads_decorators_and_params() -> None:
    source = """
@pytest.mark.sample("a")
def test_one(): ...

CASES = [pytest.param(1, marks=pytest.mark.sample("b"))]
"""
    assert sample_marks(source) == {"a", "b"}


MARKED_TESTS = """
import pytest

from .harness import Harness, json_reply

QUOTEC = "/v5/stock/realtime/quotec.json"


@pytest.mark.sample("quotec")
def test_with_the_call(h: Harness) -> None:
    h.upstream.route("stock.xueqiu.com", QUOTEC, json_reply({"data": [], "error_code": 0}))
    h.quotec("SH600000")


@pytest.mark.sample("quotec")
def test_without_the_call(h: Harness) -> None:
    pass
"""


def test_a_sample_that_does_not_parse_its_endpoint_fails(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("__init__.py", "conftest.py", "harness.py"):
        shutil.copy(HERE / name, tests / name)
    (tests / "test_marked.py").write_text(MARKED_TESTS, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    failures = [line for line in result.stdout.splitlines() if line.startswith(("ERROR", "FAILED"))]
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(failures) == 2, result.stdout
    assert all("test_without_the_call" in line for line in failures)
    assert "marked as a sample of ['quotec']" in result.stdout


def parsed_fixtures(name: str) -> list[Any]:
    """What ``parse`` makes of each replay fixture of an endpoint, under its example's params."""
    spec = REGISTRY[name]
    results: list[Any] = []
    for path in sorted((REPLAY_DIR / name).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        params = dict(spec.agent.examples[0])
        if "symbol" in spec.params.model_fields and "symbol" in case.get("params", {}):
            params["symbol"] = case["params"]["symbol"]
        results.append(spec.parse(unwrap(spec.envelope, case["body"]), spec.validate(params)))
    return results


def leaves(value: Any, name: str = "", declared: bool = False) -> Iterator[tuple[str, Any, bool]]:
    """Every value in a result, with its field name and whether a class declares that field."""
    if isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from leaves(getattr(value, field), field, True)
        yield from leaves(value.model_extra or {})
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            yield from leaves(getattr(value, field.name), field.name, True)
    elif isinstance(value, Mapping):
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            yield from leaves(item, str(key))  # pyright: ignore[reportUnknownArgumentType]
    elif isinstance(value, list | tuple):
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            yield from leaves(item, name, declared)
    else:
        yield name, value, declared


def raw_ms(value: object) -> bool:
    if isinstance(value, str):
        return re.fullmatch(r"\d{11,14}", value) is not None
    return isinstance(value, int | float) and not isinstance(value, bool) and 1e11 <= value < 1e14


def misplaced_times(result: object) -> list[tuple[str, Any]]:
    """Fields named like a time that hold raw milliseconds or, when declared, anything but a date.

    Undeclared fields stay as the upstream sent them, so only raw milliseconds count against them.
    """
    return [
        (name, value)
        for name, value, declared in leaves(result)
        if TIME_NAME.search(name)
        and (raw_ms(value) or (declared and value is not None and not isinstance(value, date)))
    ]


@pytest.mark.parametrize("name", ENDPOINTS)
def test_no_model_keeps_a_raw_ms_int(name: str) -> None:
    for result in parsed_fixtures(name):
        assert misplaced_times(result) == []


def test_the_time_scan_looks_through_pages_models_and_undeclared_fields() -> None:
    class Row(UpstreamModel):
        report_date: Any = None
        total_assets: float | None = None

    row = Row.model_validate(
        {
            "report_date": "2026三季报",
            "total_assets": 1e13,
            "updated_at": 1790352000000,
            "ts": "1790352000000",
        }
    )

    assert misplaced_times(Page(items=(row,), page=1, page_size=1)) == [
        ("report_date", "2026三季报"),
        ("updated_at", 1790352000000),
        ("ts", "1790352000000"),
    ]


def cell(row: Any, field: str) -> Any:
    return (
        cast(Mapping[str, Any], row).get(field)
        if isinstance(row, Mapping)
        else getattr(row, field, None)
    )


def table_times(table: Sequence[Any]) -> list[date]:
    """The times in a table, from the first of ``ROW_TIME_FIELDS`` it has."""
    for field in ROW_TIME_FIELDS:
        times = [cell(row, field) for row in table if cell(row, field) is not None]
        if times:
            return times
    return []


def time_columns(result: Any) -> list[Sequence[date]]:
    """The times of a result in row order, one sequence per table."""
    if isinstance(result, Kline):
        return [result.timestamps]
    if isinstance(result, Financials):
        return [[period.report_date for period in result.periods]]
    if isinstance(result, Series):
        return [result.index()[1]]
    tables = result.sections().values() if isinstance(result, Sectioned) else [result]
    return [table_times(list(table)) for table in tables]


@pytest.mark.parametrize("name", TIME_ORDERED)
def test_series_order_matches_declaration(name: str) -> None:
    newest_first = REGISTRY[name].order == "newest_first"
    columns = [times for result in parsed_fixtures(name) for times in time_columns(result)]

    for times in columns:
        assert list(times) == sorted(times, reverse=newest_first)
    assert any(len(set(times)) > 1 for times in columns)
