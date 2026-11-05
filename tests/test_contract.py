"""Contract of every registered endpoint: what it asks the upstream, what it does with errors."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from xueqiu_api import REGISTRY, UpstreamChanged, UpstreamError
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.registry import Endpoint, Envelope
from xueqiu_api.sites import SITES

from .harness import FAKE_LOGIN, REPLAY_DIR, Harness, json_reply

HERE = Path(__file__).parent
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
ENDPOINTS = sorted(REGISTRY)
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

    assert any(params.items() <= query.items() for params in fixture_params(name))


@pytest.mark.parametrize("name", ENDPOINTS)
def test_requests_use_the_profile_of_the_site(make: Callable[..., Harness], name: str) -> None:
    spec = REGISTRY[name]
    site = SITES[spec.host]
    transport = RecordingReplay(REPLAY_DIR)
    h = make(token=token_for(spec), transport=transport)

    h.call(name, **spec.agent.examples[0])

    [request] = [sent for sent in transport.seen if sent.url.path != "/"]
    assert request.url.host == spec.host
    for header, value in site.headers.items():
        assert request.headers[header] == value
    if not (site.cookies and spec.cookies):
        assert "cookie" not in request.headers
    elif spec.auth == "required":
        assert request.headers["cookie"] == FAKE_LOGIN
    else:
        assert "xq_a_token=replay-anonymous" in request.headers["cookie"]


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
