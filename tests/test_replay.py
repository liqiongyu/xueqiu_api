"""Replay transport: routing by endpoint and params to fixture files."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from xueqiu_api import UpstreamChanged
from xueqiu_api._replay import ReplayTransport, path_pattern

from .harness import REPLAY_DIR, Harness, replay_body


def write_case(root: Path, endpoint: str, name: str, case: object) -> None:
    directory = root / endpoint
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(case), encoding="utf-8")


def get(transport: ReplayTransport, url: str) -> httpx2.Response:
    with httpx2.Client(transport=transport) as client:
        return client.get(url)


def test_routes_by_endpoint_and_most_specific_params(tmp_path: Path) -> None:
    write_case(tmp_path, "kline", "any", {"params": {}, "body": {"which": "any"}})
    write_case(tmp_path, "kline", "day", {"params": {"period": "day"}, "body": {"which": "day"}})
    write_case(
        tmp_path,
        "kline",
        "sh_day",
        {"params": {"symbol": "SH600000", "period": "day"}, "body": {"which": "sh"}},
    )
    transport = ReplayTransport(tmp_path)
    base = "https://stock.xueqiu.com/v5/stock/chart/kline.json"

    assert get(transport, f"{base}?symbol=SH600000&period=day").json() == {"which": "sh"}
    assert get(transport, f"{base}?symbol=AAPL&period=day").json() == {"which": "day"}
    assert get(transport, f"{base}?symbol=AAPL&period=week").json() == {"which": "any"}


def test_status_defaults_to_200_and_can_be_set(tmp_path: Path) -> None:
    write_case(tmp_path, "quotec", "error", {"status": 429, "body": {"error_code": 1}})
    response = get(
        ReplayTransport(tmp_path), "https://stock.xueqiu.com/v5/stock/realtime/quotec.json"
    )
    assert response.status_code == 429


def test_unmatched_requests_get_404(tmp_path: Path) -> None:
    write_case(tmp_path, "kline", "sh", {"params": {"symbol": "SH600000"}, "body": {}})
    transport = ReplayTransport(tmp_path)

    kline = get(transport, "https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol=AAPL")
    other = get(transport, "https://stock.xueqiu.com/v5/stock/unknown.json")

    assert (kline.status_code, other.status_code) == (404, 404)


def test_unknown_endpoint_directories_are_rejected(tmp_path: Path) -> None:
    write_case(tmp_path, "no_such_endpoint", "x", {"body": {}})
    with pytest.raises(ValueError, match="unknown endpoint"):
        ReplayTransport(tmp_path)


def test_path_templates_match_their_values() -> None:
    pattern = path_pattern("/v5/stock/finance/{region}/income.json")
    match = pattern.fullmatch("/v5/stock/finance/hk/income.json")
    assert match is not None and match.groupdict() == {"region": "hk"}
    assert pattern.fullmatch("/v5/stock/finance/hk/x/income.json") is None


def test_clients_run_offline_on_the_replay_directory(make: Callable[..., Harness]) -> None:
    h = make(transport=ReplayTransport(REPLAY_DIR))

    bars = h.kline("SH600000", count=3)
    quotes = h.quotec(["SH600000", "AAPL"])

    expected = replay_body("kline/sh600000_day")["data"]["item"]
    assert bars.columns["close"] == tuple(row[5] for row in expected)
    assert len(quotes) == 4
    with pytest.raises(UpstreamChanged):
        h.kline("SZ000001")
