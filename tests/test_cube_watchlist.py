"""The portfolio and watchlist endpoints: units, times, order, privacy and the login-only rules."""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx2
import pytest

from xueqiu_api import (
    REGISTRY,
    AuthExpired,
    InvalidParams,
    LoginRequired,
    SymbolNotFound,
    UpstreamChanged,
    XueqiuError,
)
from xueqiu_api._model import UnverifiedModel, UpstreamModel
from xueqiu_api._render import FENCE_BEGIN, FENCE_END, LABEL_CHARS, compact_json, render
from xueqiu_api._replay import ReplayTransport
from xueqiu_api._time import from_ms
from xueqiu_api.endpoints.cube_holdings import CubeHolding, CubeHoldings
from xueqiu_api.endpoints.cube_max_drawdown import CubeMaxDrawdown
from xueqiu_api.endpoints.cube_monthly_returns import CubeMonthlyReturn
from xueqiu_api.endpoints.cube_quote import CubeQuote
from xueqiu_api.endpoints.cube_rank_list import CubeSummary
from xueqiu_api.endpoints.cube_rebalancing_history import (
    Rebalancing,
    RebalancingChange,
    RebalancingPage,
)
from xueqiu_api.endpoints.cube_score import CubeScore
from xueqiu_api.endpoints.watchlist_groups import WatchlistGroup
from xueqiu_api.endpoints.watchlist_stocks import WatchlistItem, WatchlistStocksParams
from xueqiu_api.sites import SITES

from .harness import (
    ANON_COOKIES,
    FAKE_LOGIN,
    REPLAY_DIR,
    FakeClock,
    FakeUpstream,
    Harness,
    check_login_only_endpoint,
    error_reply,
    fixture_reply,
    json_reply,
    replay_body,
)

WWW = "www.xueqiu.com"
STOCK = "stock.xueqiu.com"
SHANGHAI = ZoneInfo("Asia/Shanghai")
EIGHT_HOURS = timedelta(hours=8)
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=SHANGHAI)
QUOTE = REGISTRY["cube_quote"].path
RANK = REGISTRY["cube_rank_list"].path
HOLDINGS = REGISTRY["cube_holdings"].path
HISTORY = REGISTRY["cube_rebalancing_history"].path
GROUPS = REGISTRY["watchlist_groups"].path
STOCKS = REGISTRY["watchlist_stocks"].path
PORTFOLIO_FIXTURES = {
    "cube_holdings": "cube_holdings/synthetic",
    "cube_max_drawdown": "cube_max_drawdown/zh2567925",
    "cube_monthly_returns": "cube_monthly_returns/zh2567925",
    "cube_quote": "cube_quote/zh2567925",
    "cube_rank_list": "cube_rank_list/page1",
    "cube_rebalancing_history": "cube_rebalancing_history/synthetic",
    "cube_score": "cube_score/zh2567925",
}
LOGIN_ONLY = ["cube_holdings", "cube_rebalancing_history", "watchlist_groups", "watchlist_stocks"]
BANNED_KEYS = {
    "owner",
    "owner_id",
    "contractor_id",
    "contractor_name",
    "description",
    "following",
    "recommend_reason",
    "view_rebalancing",
    "last_rebalancing",
    "last_success_rebalancing",
    "comment",
}
PRIVATE: Mapping[str, Any] = {
    "owner": {"id": 7, "screen_name": "secret-owner", "description": "secret-bio"},
    "owner_id": "secret-owner-id",
    "contractor_id": 9,
    "contractor_name": "secret-contractor",
    "description": "secret-description",
    "following": True,
    "recommend_reason": "secret-reason",
    "view_rebalancing": {"comment": "secret-comment"},
    "last_rebalancing": {"comment": "secret-comment"},
    "last_success_rebalancing": {"comment": "secret-comment"},
    "performance": {"owner": {"screen_name": "secret-nested"}, "description": "secret-nested"},
}


def rendered(h: Harness, name: str, *, fields: list[str] | None = None, **params: Any) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=fields,
        limit=spec.agent.row_default,
        max_chars=4000,
        fetched_at=NOW,
        next_call=lambda following, offset: compact_json(following),
    )


def quote_row(code: str = "ZH2567925", **fields: Any) -> dict[str, Any]:
    return {
        "symbol": code,
        "name": "示例组合",
        "net_value": "1.5",
        "total_gain": "50.00",
        "closed_at": "",
        **fields,
    }


def rank_row(index: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": 1_000_000 + index,
        "symbol": f"ZH{index:07d}",
        "name": f"示例组合{index}",
        "net_value": 1.5,
        "total_gain": 50.0,
        "annualized_gain_rate": 5.0,
        "follower_count": 3,
        "updated_at": 1_700_000_100_000,
        "closed_at": None,
        **fields,
    }


def rank_body(page: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": 20,
        "filter": {"category": 14},
        "page": page,
        "totalCount": 100,
        "maxPage": 5,
        "list": rows,
    }


ONE_PRIVATE_ROW = {
    **rank_body(1, [rank_row(1, **PRIVATE)]),
    "totalCount": 1,
    "maxPage": 1,
}


def rank_pages(request: httpx2.Request) -> httpx2.Response:
    page = int(request.url.params["page"])
    rows = [rank_row((page - 1) * 20 + index) for index in range(1, 21)]
    return httpx2.Response(200, json=rank_body(page, rows))


def all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        fields = cast(dict[str, Any], value)
        return set(fields).union(*(all_keys(item) for item in fields.values()))
    if isinstance(value, list | tuple):
        return set[str]().union(*(all_keys(item) for item in cast(list[Any], value)))
    return set()


def holding(**fields: Any) -> dict[str, Any]:
    return {"stock_symbol": "SH600000", "stock_name": "浦发银行", "weight": 100.0, **fields}


def rebalancing(identifier: int, **fields: Any) -> dict[str, Any]:
    change = {"stock_symbol": "SH600000", "stock_name": "浦发银行", "target_weight": 100.0}
    return {
        "id": identifier,
        "status": "success",
        "updated_at": 1_700_000_100_000,
        "rebalancing_histories": [change],
        **fields,
    }


# Requests


@pytest.mark.parametrize("name", sorted(PORTFOLIO_FIXTURES))
def test_portfolio_requests_go_to_www_as_a_browser(make: Callable[..., Harness], name: str) -> None:
    spec = REGISTRY[name]
    h = make(token=FAKE_LOGIN if spec.auth == "required" else None)
    h.upstream.route(WWW, spec.path, fixture_reply(PORTFOLIO_FIXTURES[name]))

    h.call(name, **spec.agent.examples[0])

    [sent] = [one for one in h.upstream.sent if one.path != "/"]
    assert (sent.host, sent.path) == (WWW, spec.path)
    assert sent.headers["user-agent"].startswith("Mozilla")
    if spec.auth == "required":
        assert sent.headers["cookie"] == FAKE_LOGIN
    else:
        assert f"xq_a_token={ANON_COOKIES['xq_a_token']}" in sent.headers["cookie"]


@pytest.mark.parametrize(
    "name",
    [
        "cube_holdings",
        "cube_max_drawdown",
        "cube_monthly_returns",
        "cube_rebalancing_history",
        "cube_score",
    ],
)
def test_a_stock_code_is_rejected_before_any_request(
    make: Callable[..., Harness], name: str
) -> None:
    h = make(token=FAKE_LOGIN)

    with pytest.raises(InvalidParams) as caught:
        h.call(name, cube_symbol="SH600000")

    assert caught.value.field == "cube_symbol"
    assert h.upstream.sent == []


@pytest.mark.parametrize(
    ("name", "query"),
    [
        ("cube_monthly_returns", {"cube_symbol": "ZH2567925"}),
        ("cube_max_drawdown", {"cube_symbol": "ZH2567925", "type": "max_draw"}),
        ("cube_score", {"symbol": "ZH2567925", "ua": "web"}),
    ],
)
def test_a_lower_case_code_is_normalized_in_the_query(
    h: Harness, name: str, query: dict[str, str]
) -> None:
    h.upstream.route(WWW, REGISTRY[name].path, fixture_reply(PORTFOLIO_FIXTURES[name]))

    h.call(name, cube_symbol=" zh2567925 ")

    [sent] = h.upstream.requests(WWW, REGISTRY[name].path)
    assert sent.params == query


# cube_quote


@pytest.mark.sample("cube_quote")
def test_cube_quote_parses_text_into_numbers_and_shanghai_times(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, fixture_reply("cube_quote/zh2567925"))

    [quote] = h.call("cube_quote", cube_symbols=["zh2567925"])

    [sent] = h.upstream.requests(WWW, QUOTE)
    assert sent.params == {"code": "ZH2567925"}
    assert isinstance(quote, CubeQuote)
    assert (quote.symbol, quote.market, quote.name) == ("ZH2567925", "cn", "示例组合")
    assert (quote.net_value, quote.daily_gain, quote.monthly_gain) == (0.8994, -0.64, 1.66)
    assert quote.annualized_gain == -5.42
    assert quote.net_value is not None and quote.total_gain is not None
    assert quote.total_gain == pytest.approx((quote.net_value - 1) * 100, abs=0.01)
    assert quote.closed_at == datetime(2023, 5, 4, 21, 47, 20, 790000, tzinfo=SHANGHAI)
    assert quote.closed_at is not None and quote.closed_at.utcoffset() == EIGHT_HOURS
    assert quote.model_extra == {"hasexist": "false", "badges_exist": "false", "game_id": 0}
    assert REGISTRY["cube_quote"].order is None


def test_cube_quote_batches_in_the_order_requested(h: Harness) -> None:
    body = {"ZH0000001": quote_row("ZH0000001"), "ZH2567925": quote_row("ZH2567925")}
    h.upstream.route(WWW, QUOTE, json_reply(body))

    quotes = h.call("cube_quote", cube_symbols="zh2567925, ZH0000001")

    [sent] = h.upstream.requests(WWW, QUOTE)
    assert sent.params == {"code": "ZH2567925,ZH0000001"}
    assert [quote.symbol for quote in quotes] == ["ZH2567925", "ZH0000001"]


def test_cube_quote_reads_an_empty_closed_at_as_open(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, json_reply({"ZH2567925": quote_row(closed_at="")}))

    [quote] = h.call("cube_quote", cube_symbols=["ZH2567925"])

    assert quote.closed_at is None


def test_cube_quote_notes_a_code_the_body_lacks(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, json_reply({"ZH2567925": quote_row()}))

    text = rendered(h, "cube_quote", cube_symbols=["ZH2567925", "ZH0000001"])

    assert "# note: no data for ZH0000001\n" in text


def test_cube_quote_of_an_empty_body_is_no_rows(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, json_reply({}))

    assert h.call("cube_quote", cube_symbols=["ZH2567925"]) == []


def test_cube_quote_unknown_code_is_symbol_not_found(h: Harness) -> None:
    body = {"error_description": "示例说明", "error_uri": QUOTE, "error_code": "20809"}
    h.upstream.route(WWW, QUOTE, json_reply(body, 400))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("cube_quote", cube_symbols=["ZH0000000"])

    assert caught.value.upstream_code == "20809"
    assert len(h.upstream.requests(WWW, QUOTE)) == 1


def test_cube_quote_unknown_code_has_a_hint_about_portfolio_codes(h: Harness) -> None:
    body = {"error_description": "示例说明", "error_uri": QUOTE, "error_code": "20809"}
    h.upstream.route(WWW, QUOTE, json_reply(body, 400))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("cube_quote", cube_symbols=["ZH0000000"])

    assert caught.value.hint.startswith("A portfolio code is ZH plus digits")
    assert "A shares" not in caught.value.hint


def test_cube_quote_http_400_without_a_code_is_no_missing_code(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, lambda request: httpx2.Response(400, text="<html>400</html>"))

    with pytest.raises(XueqiuError) as caught:
        h.call("cube_quote", cube_symbols=["ZH2567925"])

    assert not isinstance(caught.value, SymbolNotFound)
    assert caught.value.status == 400


@pytest.mark.parametrize(
    "codes",
    [["SH600000"], [f"ZH{index:07d}" for index in range(11)], []],
    ids=["stock", "too-many", "none"],
)
def test_cube_quote_rejects_bad_codes_before_any_request(h: Harness, codes: list[str]) -> None:
    with pytest.raises(InvalidParams):
        h.call("cube_quote", cube_symbols=codes)

    assert h.upstream.sent == []


@pytest.mark.parametrize(
    ("name", "reply", "params"),
    [
        (
            "cube_quote",
            {"ZH2567925": quote_row(name="长" * 60)},
            {"cube_symbols": ["ZH2567925"]},
        ),
        ("cube_rank_list", rank_body(5, [rank_row(1, name="长" * 60)]), {"page": 5}),
    ],
    ids=["quote", "rank"],
)
def test_a_portfolio_name_renders_by_default_fenced_and_cut(
    h: Harness, name: str, reply: dict[str, Any], params: dict[str, Any]
) -> None:
    h.upstream.route(WWW, REGISTRY[name].path, json_reply(reply))

    lines = rendered(h, name, **params).splitlines()

    begin, end = lines.index(FENCE_BEGIN), lines.index(FENCE_END)
    [columns, row] = list(csv.reader(lines[begin + 1 : end]))
    cell = row[columns.index("name")]
    assert len(cell) == 40
    assert cell == "长" * 39 + "…"


# cube_monthly_returns


@pytest.mark.sample("cube_monthly_returns")
def test_cube_monthly_returns_are_percent_points_latest_first(h: Harness) -> None:
    h.upstream.route(
        WWW, REGISTRY["cube_monthly_returns"].path, fixture_reply("cube_monthly_returns/zh2567925")
    )

    rows = h.call("cube_monthly_returns", cube_symbol="ZH2567925")

    assert all(isinstance(row, CubeMonthlyReturn) for row in rows)
    assert [row.month for row in rows] == [date(2023, 4, 1), date(2023, 3, 1), date(2023, 2, 1)]
    assert rows[0].gain == pytest.approx(1.6173, abs=1e-4)
    assert rows[0].benchmark_gain == pytest.approx(-0.5391, abs=1e-4)
    assert {row.benchmark for row in rows} == {"SH000300"}
    points = replay_body("cube_monthly_returns/zh2567925")[0]["profit_list"]
    for row, point in zip(rows, points, strict=True):
        assert row.month == from_ms(point["time"], SHANGHAI).date()
    assert REGISTRY["cube_monthly_returns"].order == "newest_first"
    assert [row.month for row in rows] == sorted((row.month for row in rows), reverse=True)


def test_cube_monthly_returns_join_the_series_by_month(h: Harness) -> None:
    cube = {
        "symbol": "ZH2567925",
        "name": None,
        "profit_list": [{"date": "2023-04", "value": 0.01}, {"date": "2023-03", "value": 0.02}],
    }
    benchmark = {
        "symbol": "SH000300",
        "name": "沪深300",
        "profit_list": [{"date": "2023-03", "value": -0.03}],
    }
    path = REGISTRY["cube_monthly_returns"].path
    h.upstream.route(WWW, path, json_reply([cube, benchmark]), json_reply([cube]))

    joined = h.call("cube_monthly_returns", cube_symbol="ZH2567925")
    alone = h.call("cube_monthly_returns", cube_symbol="ZH2567925")

    assert [(row.gain, row.benchmark_gain) for row in joined] == [(1.0, None), (2.0, -3.0)]
    assert {row.benchmark for row in alone} == {None}


@pytest.mark.parametrize(
    "body",
    [[], [{"symbol": "ZH2567925"}], [{"symbol": "ZH", "profit_list": [{"date": "2023-04-15"}]}]],
    ids=["no-series", "no-list", "not-a-month"],
)
def test_cube_monthly_returns_of_another_shape_are_upstream_changed(h: Harness, body: Any) -> None:
    h.upstream.route(WWW, REGISTRY["cube_monthly_returns"].path, json_reply(body))

    with pytest.raises(UpstreamChanged):
        h.call("cube_monthly_returns", cube_symbol="ZH2567925")


def test_cube_monthly_returns_render_with_a_hint_when_empty(h: Harness) -> None:
    body: list[dict[str, Any]] = [{"symbol": "ZH2567925", "name": None, "profit_list": []}]
    h.upstream.route(WWW, REGISTRY["cube_monthly_returns"].path, json_reply(body))

    text = rendered(h, "cube_monthly_returns", cube_symbol="ZH2567925")

    assert "only complete calendar months" in text


# cube_max_drawdown


@pytest.mark.sample("cube_max_drawdown")
def test_cube_max_drawdown_is_negative_percent_points_with_shanghai_times(h: Harness) -> None:
    path = REGISTRY["cube_max_drawdown"].path
    h.upstream.route(WWW, path, fixture_reply("cube_max_drawdown/zh2567925"))

    result = h.call("cube_max_drawdown", cube_symbol="ZH2567925")

    [sent] = h.upstream.requests(WWW, path)
    assert sent.params == {"cube_symbol": "ZH2567925", "type": "max_draw"}
    assert isinstance(result, CubeMaxDrawdown)
    assert result.symbol == "ZH2567925"
    assert result.max_drawdown == pytest.approx(-38.4143, abs=1e-4)
    assert (result.begin_date, result.end_date) == (date(2021, 6, 28), date(2022, 10, 11))
    assert result.begin_time == datetime(2021, 6, 28, 16, 44, 27, 485000, tzinfo=SHANGHAI)
    assert result.end_time == datetime(2022, 10, 11, 17, 11, 46, 604000, tzinfo=SHANGHAI)
    assert result.begin_time is not None and result.begin_time.utcoffset() == EIGHT_HOURS
    assert REGISTRY["cube_max_drawdown"].order is None


def test_cube_max_drawdown_renders_as_one_record(h: Harness) -> None:
    path = REGISTRY["cube_max_drawdown"].path
    h.upstream.route(WWW, path, fixture_reply("cube_max_drawdown/zh2567925"))

    text = rendered(h, "cube_max_drawdown", cube_symbol="ZH2567925")

    assert "max_drawdown: -38.4143\nbegin_date: 2021-06-28\nend_date: 2022-10-11\n" in text


# cube_score


@pytest.mark.sample("cube_score")
def test_cube_score_parses_the_card_and_its_parts(h: Harness) -> None:
    path = REGISTRY["cube_score"].path
    h.upstream.route(WWW, path, fixture_reply("cube_score/zh2567925"))

    score = h.call("cube_score", cube_symbol="ZH2567925")

    [sent] = h.upstream.requests(WWW, path)
    assert sent.params == {"symbol": "ZH2567925", "ua": "web"}
    assert isinstance(score, CubeScore)
    assert (score.total_score, score.max_total_score, score.level) == (56.0, 100, 3)
    assert [part.param for part in score.sub_scores] == ["absGain", "maxDrawDown", "volatility"]
    assert (score.sub_scores[1].score, score.sub_scores[1].max_score) == (7.0, 20)
    assert score.score_updated_time == datetime(2023, 5, 23, 8, 56, 13, 571000, tzinfo=SHANGHAI)
    assert score.report_updated_time is not None
    assert score.report_updated_time.utcoffset() == EIGHT_HOURS
    assert score.model_extra == {"badges": [], "cube_id": 0}
    assert REGISTRY["cube_score"].order is None


def test_cube_score_renders_its_parts_on_one_line(h: Harness) -> None:
    path = REGISTRY["cube_score"].path
    h.upstream.route(WWW, path, fixture_reply("cube_score/zh2567925"))

    text = rendered(h, "cube_score", cube_symbol="ZH2567925")

    assert "total_score: 56\n" in text
    assert 'sub_scores: [{"name":"盈利能力","param":"absGain","score":12.0' in text


# cube_rank_list


@pytest.mark.sample("cube_rank_list")
def test_cube_rank_list_reads_the_page_and_its_rows(h: Harness) -> None:
    h.upstream.route(WWW, RANK, fixture_reply("cube_rank_list/page1"))

    page = h.call("cube_rank_list")

    [sent] = h.upstream.requests(WWW, RANK)
    assert sent.params == {"category": "14", "page": "1", "size": "20"}
    assert (page.page, page.page_size, page.total, page.last_page) == (1, 20, 3, 1)
    assert page.next_params is None
    first = page.items[0]
    assert isinstance(first, CubeSummary)
    assert (first.symbol, first.cube_id, first.name) == ("ZH0000001", 1000001, "示例组合一")
    assert first.annualized_gain == 20.32
    assert first.follower_count == 110
    assert first.last_rb_id == 2000001
    assert first.closed_at is None
    assert first.created_at == from_ms(1612439369147, SHANGHAI)
    assert first.created_at is not None and first.created_at.utcoffset() == EIGHT_HOURS
    assert first.model_extra is not None and first.model_extra["bb_rate"] == 4.8
    for row in page:
        assert row.total_gain == pytest.approx((row.net_value - 1) * 100, abs=0.01)
    assert REGISTRY["cube_rank_list"].order == "rank"


def test_cube_rank_list_asks_for_the_page_and_names_the_next(h: Harness) -> None:
    h.upstream.route(WWW, RANK, rank_pages)

    first = rendered(h, "cube_rank_list")
    last = rendered(h, "cube_rank_list", page=5)

    assert [sent.params["page"] for sent in h.upstream.requests(WWW, RANK)] == ["1", "5"]
    assert "# page: page=1 page_size=20 total=100 last_page=5\n" in first
    assert first.endswith('# next: {"page":2}')
    assert last.endswith("# next: none")


def test_cube_rank_list_rejects_a_page_past_the_end(h: Harness) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("cube_rank_list", page=6)

    assert caught.value.field == "page"
    assert h.upstream.sent == []


def test_cube_rank_list_takes_no_page_size(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.call("cube_rank_list", page_size=3)

    assert h.upstream.sent == []


def test_cube_rank_list_walks_every_page(h: Harness) -> None:
    h.upstream.route(WWW, RANK, rank_pages)

    pages, error = h.walk(REGISTRY["cube_rank_list"])

    assert error is None
    assert [len(page) for page in pages] == [20] * 5
    assert len({row.symbol for page in pages for row in page}) == 100
    assert pages[-1].next_params is None


def test_cube_rank_list_walk_drops_a_row_repeated_on_the_next_page(h: Harness) -> None:
    first = [rank_row(index) for index in range(1, 21)]
    second = [rank_row(index) for index in range(20, 40)]
    body = {**rank_body(1, first), "totalCount": 40, "maxPage": 2}
    h.upstream.route(
        WWW,
        RANK,
        json_reply(body),
        json_reply({**rank_body(2, second), "totalCount": 40, "maxPage": 2}),
    )

    pages, error = h.walk(REGISTRY["cube_rank_list"])

    assert error is None
    assert [len(page) for page in pages] == [20, 19]


# What the owner of a portfolio wrote, and who the owner is


def test_owner_data_reaches_no_result(h: Harness) -> None:
    h.upstream.route(WWW, RANK, json_reply(ONE_PRIVATE_ROW))

    page = h.call("cube_rank_list")

    dump = page.items[0].model_dump()
    assert not all_keys(dump) & BANNED_KEYS
    assert "secret" not in json.dumps(dump, default=str)
    assert dump["performance"] == {}


def test_owner_data_reaches_no_column_and_no_text(h: Harness) -> None:
    h.upstream.route(WWW, RANK, json_reply(ONE_PRIVATE_ROW))
    columns = list(h.call("cube_rank_list").items[0].model_dump())

    text = rendered(h, "cube_rank_list", fields=columns)

    assert "secret" not in text
    assert not set(columns) & BANNED_KEYS
    with pytest.raises(InvalidParams) as caught:
        rendered(h, "cube_rank_list", fields=["owner"])
    assert caught.value.valid_values
    assert not set(caught.value.valid_values) & BANNED_KEYS


def test_owner_data_reaches_no_quote(h: Harness) -> None:
    h.upstream.route(WWW, QUOTE, json_reply({"ZH2567925": quote_row(**PRIVATE)}))

    [quote] = h.call("cube_quote", cube_symbols=["ZH2567925"])

    assert not all_keys(quote.model_dump()) & BANNED_KEYS
    assert "secret" not in json.dumps(quote.model_dump(), default=str)


def xq_call(
    upstream: FakeUpstream,
    clock: FakeClock,
    tmp_path: Path,
    arguments: Mapping[str, Any],
    token: str | None = None,
) -> tuple[str, bool]:
    """Text and error flag of one ``xq_call`` through the MCP server on ``upstream``."""
    pytest.importorskip("mcp")
    from mcp import Client as McpClient
    from mcp.types import TextContent

    from xueqiu_api._async import AsyncClient, AsyncRuntime
    from xueqiu_api.mcp_server import McpOptions
    from xueqiu_api.mcp_server.server import build_server

    async def run() -> tuple[str, bool]:
        runtime = AsyncRuntime(
            transport=upstream.transport, clock=clock.now, sleep=clock.async_sleep
        )
        async with AsyncClient(
            token=token, session_path=tmp_path / "session.json", _runtime=runtime
        ) as client:
            server = build_server(McpOptions(), client=client, now=lambda: NOW)
            async with McpClient(server, raise_exceptions=True, mode="auto") as session:
                result = await session.call_tool("xq_call", dict(arguments))
        [content] = result.content
        assert isinstance(content, TextContent)
        return content.text, result.is_error

    return asyncio.run(run())


def test_the_mcp_text_holds_no_owner_data_and_fences_the_name(
    upstream: FakeUpstream, clock: FakeClock, tmp_path: Path
) -> None:
    upstream.route(WWW, RANK, json_reply(ONE_PRIVATE_ROW))
    call = {"endpoint": "cube_rank_list", "params": {"page": 1}}

    text, failed = xq_call(upstream, clock, tmp_path, call)
    refused, refused_failed = xq_call(upstream, clock, tmp_path, {**call, "fields": ["owner_id"]})

    assert not failed
    assert "secret" not in text
    assert FENCE_BEGIN in text and "示例组合1" in text
    assert text.index(FENCE_BEGIN) < text.index("示例组合1") < text.index(FENCE_END)
    assert refused_failed
    assert json.loads(refused)["valid_values"]
    assert not set(json.loads(refused)["valid_values"]) & BANNED_KEYS


@pytest.mark.parametrize("name", LOGIN_ONLY)
def test_the_mcp_tool_asks_for_a_login_and_sends_nothing(
    upstream: FakeUpstream, clock: FakeClock, tmp_path: Path, name: str
) -> None:
    call = {"endpoint": name, "params": REGISTRY[name].agent.examples[0]}

    text, failed = xq_call(upstream, clock, tmp_path, call)

    assert failed
    assert json.loads(text)["error_code"] == "login_required"
    assert upstream.sent == []


# Endpoints that only answer a login


@pytest.mark.parametrize("name", LOGIN_ONLY)
def test_login_only_endpoints_refuse_without_a_token_and_reject_a_bad_one(
    make: Callable[..., Harness], name: str
) -> None:
    check_login_only_endpoint(make, REGISTRY[name])


@pytest.mark.parametrize(
    ("name", "code", "status"),
    [
        ("cube_holdings", "10022", 400),
        ("cube_rebalancing_history", "10022", 400),
        ("watchlist_groups", 60201, 200),
        ("watchlist_stocks", 60201, 200),
    ],
)
def test_the_measured_login_codes_are_auth_expired_with_a_token(
    make: Callable[..., Harness], name: str, code: int | str, status: int
) -> None:
    spec = REGISTRY[name]
    assert str(code) in SITES[spec.host].login_codes
    h = make(token=FAKE_LOGIN)
    h.upstream.route(spec.host, spec.path, error_reply(code, "synthetic login error", status))

    with pytest.raises(AuthExpired) as caught:
        h.call(name, **spec.agent.examples[0])

    assert caught.value.upstream_code == str(code)
    assert len(h.upstream.sent) == 1


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("cube_holdings", CubeHoldings),
        ("cube_holdings", CubeHolding),
        ("cube_rebalancing_history", Rebalancing),
        ("cube_rebalancing_history", RebalancingChange),
        ("watchlist_groups", WatchlistGroup),
        ("watchlist_stocks", WatchlistItem),
    ],
)
def test_login_only_models_are_unverified(name: str, model: type[UpstreamModel]) -> None:
    assert REGISTRY[name].verified_on is None
    assert REGISTRY[name].auth == "required"
    assert issubclass(model, UnverifiedModel)


# cube_holdings


@pytest.mark.sample("cube_holdings")
def test_cube_holdings_reads_the_last_successful_rebalancing(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HOLDINGS, fixture_reply("cube_holdings/synthetic"))

    result = h.call("cube_holdings", cube_symbol="zh2567925")

    [sent] = h.upstream.sent
    assert sent.params == {"cube_symbol": "ZH2567925"}
    assert sent.headers["cookie"] == FAKE_LOGIN
    assert isinstance(result, CubeHoldings)
    assert (result.symbol, result.rebalancing_id, result.status) == (
        "ZH2567925",
        2000001,
        "success",
    )
    assert (result.last_rebalancing_id, result.last_rebalancing_status) == (2000002, "pending")
    assert result.updated_at == from_ms(1700000100000, SHANGHAI)
    assert result.updated_at is not None and result.updated_at.utcoffset() == EIGHT_HOURS
    assert [row.symbol for row in result.holdings] == ["SH600000", "SZ000001", "SH510300"]
    assert sum(row.weight or 0 for row in result.holdings) + (result.cash_weight or 0) == 100
    assert result.holdings[0].name == "浦发银行"
    assert result.holdings[0].segment_name == "示例分组"
    assert REGISTRY["cube_holdings"].order is None


def test_cube_holdings_keeps_no_undeclared_key(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    done = {
        "id": 1,
        "comment": "secret-comment",
        "error_message": "secret-message",
        "holdings": [holding(volume=0.5, undeclared="secret-field", owner={"id": 7})],
    }
    h.upstream.route(WWW, HOLDINGS, json_reply({"last_success_rb": done, "undeclared": "secret"}))

    result = h.call("cube_holdings", cube_symbol="ZH2567925")

    assert result.model_extra is None
    assert result.holdings[0].model_extra is None
    assert "secret" not in json.dumps(result.model_dump(), default=str)
    assert set(result.holdings[0].model_dump()) == {"symbol", "name", "weight", "segment_name"}


def test_cube_holdings_reads_a_nested_error_code_as_rebalancing_state(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    latest = {"id": 2, "status": "failed", "error_code": "90001", "error_message": "示例"}
    done = {"id": 1, "status": "success", "holdings": [holding()]}
    h.upstream.route(WWW, HOLDINGS, json_reply({"last_rb": latest, "last_success_rb": done}))

    result = h.call("cube_holdings", cube_symbol="ZH2567925")

    assert (result.rebalancing_id, result.last_rebalancing_status) == (1, "failed")


@pytest.mark.parametrize(
    "body",
    [{}, {"last_success_rb": None}, {"last_success_rb": {"id": 1}}, {"last_success_rb": {}}],
    ids=["empty", "null", "no-holdings", "no-id"],
)
def test_cube_holdings_needs_the_keys_it_declares(
    make: Callable[..., Harness], body: dict[str, Any]
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HOLDINGS, json_reply(body))

    with pytest.raises(UpstreamChanged):
        h.call("cube_holdings", cube_symbol="ZH2567925")


def test_cube_holdings_renders_the_rebalancing_above_the_rows(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HOLDINGS, fixture_reply("cube_holdings/synthetic"))

    text = rendered(h, "cube_holdings", cube_symbol="ZH2567925")

    assert (
        "# info: rebalancing_id=2000001 status=success updated_at=2023-11-15T06:15:00+08:00 "
        "cash_weight=5 last_rebalancing_id=2000002 last_rebalancing_status=pending\n"
    ) in text
    assert f"{FENCE_BEGIN}\nsymbol,name,weight,segment_name\n" in text
    assert "SH600000,浦发银行,40,示例分组\n" in text
    assert "SH510300,示例ETF,20,\n" in text
    assert "示例系统提示" not in text


# cube_rebalancing_history


@pytest.mark.sample("cube_rebalancing_history")
def test_cube_rebalancing_history_reads_the_page_and_the_changes(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HISTORY, fixture_reply("cube_rebalancing_history/synthetic"))

    page = h.call("cube_rebalancing_history", cube_symbol="ZH2567925", page_size=2)

    [sent] = h.upstream.sent
    assert sent.params == {"cube_symbol": "ZH2567925", "page": "1", "count": "2"}
    assert isinstance(page, RebalancingPage)
    assert (page.page, page.page_size, page.total, page.last_page) == (1, 20, 2, 1)
    latest, oldest = page.items
    assert isinstance(latest, Rebalancing)
    assert latest.rebalancing_id == 2000002
    assert (latest.prev_rebalancing_id, oldest.prev_rebalancing_id) == (2000001, None)
    assert latest.category == "user_rebalancing"
    assert latest.cash_weight == 0.0
    assert latest.created_at == from_ms(1700000300000, SHANGHAI)
    assert latest.updated_at is not None and latest.updated_at.utcoffset() == EIGHT_HOURS
    change = latest.changes[0]
    assert (change.symbol, change.name) == ("SH600000", "浦发银行")
    assert (change.prev_weight_adjusted, change.prev_weight) == (57.29, 50.0)
    assert (change.target_weight, change.weight) == (33.0, 33.0)
    assert (change.price, change.prev_price) == (9.89, 9.5)
    new = oldest.changes[0]
    assert (new.prev_weight_adjusted, new.prev_weight, new.prev_price) == (None, None, None)
    assert REGISTRY["cube_rebalancing_history"].order is None


def test_cube_rebalancing_history_defaults_to_the_first_full_page(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HISTORY, fixture_reply("cube_rebalancing_history/synthetic"))

    h.call("cube_rebalancing_history", cube_symbol="ZH2567925", page=2)

    [sent] = h.upstream.sent
    assert sent.params == {"cube_symbol": "ZH2567925", "page": "2", "count": "50"}


def test_cube_rebalancing_history_rejects_a_page_size_over_the_cap(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)

    with pytest.raises(InvalidParams) as caught:
        h.call("cube_rebalancing_history", cube_symbol="ZH2567925", page_size=51)

    assert caught.value.field == "page_size"
    assert h.upstream.sent == []


def test_cube_rebalancing_history_keeps_no_undeclared_key(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    row = rebalancing(1, comment="secret-comment", undeclared="secret-field")
    row["rebalancing_histories"][0] |= {"volume": 0.5, "undeclared": "secret-field"}
    body = {"count": 20, "page": 1, "totalCount": 1, "maxPage": 1, "list": [row]}
    h.upstream.route(WWW, HISTORY, json_reply(body))

    page = h.call("cube_rebalancing_history", cube_symbol="ZH2567925")

    assert page.items[0].model_extra is None
    assert page.items[0].changes[0].model_extra is None
    assert "secret" not in json.dumps(page.items[0].model_dump(), default=str)


def test_cube_rebalancing_history_renders_one_row_per_change(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, HISTORY, fixture_reply("cube_rebalancing_history/synthetic"))

    text = rendered(h, "cube_rebalancing_history", cube_symbol="ZH2567925", page_size=20)

    assert "# page: page=1 page_size=20 total=2 last_page=1\n" in text
    assert "rows=4" in text
    assert "updated_at,status,symbol,name,prev_weight_adjusted,target_weight,price\n" in text
    assert "2023-11-15T06:20:00+08:00,success,SH600000,浦发银行,57.29,33,9.89\n" in text
    assert "2023-11-15T06:15:00+08:00,success,SZ000001,平安银行,,50,11.8\n" in text
    assert text.endswith("# next: none")


def test_cube_rebalancing_history_walks_to_the_last_page(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)

    def reply(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        rows = {1: [rebalancing(3), rebalancing(2)], 2: [rebalancing(1)]}[page]
        body = {"count": 2, "page": page, "totalCount": 3, "maxPage": 2, "list": rows}
        return httpx2.Response(200, json=body)

    h.upstream.route(WWW, HISTORY, reply)

    pages, error = h.walk(
        REGISTRY["cube_rebalancing_history"], cube_symbol="ZH2567925", page_size=2
    )

    assert error is None
    assert isinstance(pages[0], RebalancingPage)
    assert [row.rebalancing_id for page in pages for row in page] == [3, 2, 1]
    assert pages[0].next_params == {"cube_symbol": "ZH2567925", "page": 2, "page_size": 2}
    assert pages[-1].next_params is None


def test_cube_rebalancing_history_drops_a_rebalancing_repeated_across_pages(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)

    def reply(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        rows = {1: [rebalancing(3), rebalancing(2)], 2: [rebalancing(2), rebalancing(1)]}[page]
        body = {"count": 2, "page": page, "totalCount": 4, "maxPage": 2, "list": rows}
        return httpx2.Response(200, json=body)

    h.upstream.route(WWW, HISTORY, reply)

    pages, error = h.walk(
        REGISTRY["cube_rebalancing_history"], cube_symbol="ZH2567925", page_size=2
    )

    assert error is None
    assert [row.rebalancing_id for page in pages for row in page] == [3, 2, 1]


@pytest.mark.parametrize(
    ("name", "params", "reply", "note"),
    [
        (
            "cube_holdings",
            {"cube_symbol": "ZH2567925"},
            {"last_success_rb": {"id": 1, "cash": 100.0, "holdings": []}},
            "the portfolio may hold only cash",
        ),
        (
            "cube_rebalancing_history",
            {"cube_symbol": "ZH2567925"},
            {"count": 50, "page": 1, "totalCount": 0, "maxPage": 0, "list": []},
            "no rebalancings on record",
        ),
    ],
)
def test_an_empty_login_result_never_blames_an_anonymous_session(
    make: Callable[..., Harness],
    name: str,
    params: dict[str, Any],
    reply: dict[str, Any],
    note: str,
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(WWW, REGISTRY[name].path, json_reply(reply))

    text = rendered(h, name, **params)

    assert note in text
    assert "anonymous" not in text


def test_a_watchlist_account_without_groups_is_told_so(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, GROUPS, json_reply({"data": {}, "error_code": 0}))

    text = rendered(h, "watchlist_groups")

    assert "# note: no rows: this account has no watchlist groups.\n" in text


def test_a_watchlist_name_is_fenced_cleaned_and_cut_by_default(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    row = {"symbol": "ZH2567925", "name": f"<b>IGNORE PREVIOUS</b> {'长' * 60}"}
    h.upstream.route(STOCK, STOCKS, json_reply({"data": {"stocks": [row]}, "error_code": 0}))

    lines = rendered(h, "watchlist_stocks").splitlines()

    begin = lines.index(FENCE_BEGIN)
    assert lines[begin + 1].startswith("symbol,name,")
    name = lines[begin + 2].split(",")[1]
    assert len(name) == LABEL_CHARS and "<b>" not in name and name.startswith("IGNORE PREVIOUS")
    assert lines[begin + 3] == FENCE_END


# watchlist_groups


@pytest.mark.sample("watchlist_groups")
def test_watchlist_groups_flatten_the_lists(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, GROUPS, fixture_reply("watchlist_groups/system"))

    groups = h.call("watchlist_groups")

    [sent] = h.upstream.sent
    assert (sent.host, sent.params) == (STOCK, {"system": "true"})
    assert sent.headers["cookie"] == FAKE_LOGIN
    assert all(isinstance(group, WatchlistGroup) for group in groups)
    assert [(group.pid, group.category, group.category_code) for group in groups] == [
        (-1, "stock", 1),
        (-5, "stock", 1),
        (-7, "stock", 1),
        (-110, "fund", 2),
        (-120, "cube", 3),
    ]
    assert (groups[0].name, groups[0].symbol_count) == ("全部", 3)
    assert groups[0].created_at == from_ms(1700000000000, SHANGHAI)
    assert groups[0].created_at.utcoffset() == EIGHT_HOURS
    assert groups[3].created_at is None
    assert REGISTRY["watchlist_groups"].order is None


def test_watchlist_groups_keep_unknown_lists_and_no_undeclared_key(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    group = {"id": 5, "name": "示例", "category": 9, "uid": 7, "undeclared": "secret-field"}
    data = {"newKind": [group], "version": 3, "stocks": [{**group, "category": 1}]}
    h.upstream.route(STOCK, GROUPS, json_reply({"data": data, "error_code": 0}))

    groups = h.call("watchlist_groups")

    assert [(one.category, one.category_code) for one in groups] == [("newKind", 9), ("stock", 1)]
    assert groups[0].model_extra is None
    assert "secret" not in json.dumps(groups[0].model_dump(), default=str)
    assert "uid" not in groups[0].model_dump()


@pytest.mark.parametrize(
    "data",
    [{"stocks": ["not an object"]}, {"stocks": [{"name": "no id"}]}, ["not an object"]],
    ids=["item", "no-id", "data"],
)
def test_watchlist_groups_of_another_shape_are_upstream_changed(
    make: Callable[..., Harness], data: Any
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, GROUPS, json_reply({"data": data, "error_code": 0}))

    with pytest.raises(UpstreamChanged):
        h.call("watchlist_groups")


def test_watchlist_groups_render_the_name_in_the_fence(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, GROUPS, fixture_reply("watchlist_groups/system"))

    text = rendered(h, "watchlist_groups")

    assert f"{FENCE_BEGIN}\npid,name,category,symbol_count\n-1,全部,stock,3\n" in text
    assert text.index("-120,全部,cube,2") < text.index(FENCE_END)


# watchlist_stocks


@pytest.mark.sample("watchlist_stocks")
def test_watchlist_stocks_read_the_members_of_a_group(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, STOCKS, fixture_reply("watchlist_stocks/all_stocks"))

    items = h.call("watchlist_stocks")

    [sent] = h.upstream.sent
    assert (sent.host, sent.params) == (STOCK, {"pid": "-1", "category": "1", "size": "1000"})
    assert sent.headers["cookie"] == FAKE_LOGIN
    assert all(isinstance(item, WatchlistItem) for item in items)
    assert [(item.symbol, item.stock_type, item.exchange) for item in items] == [
        ("SH600000", 11, "SH"),
        ("SH510300", 13, "SH"),
        ("SZ000001", 11, "SZ"),
    ]
    assert items[0].name == "浦发银行"
    assert items[0].remark == ""
    assert items[0].created == from_ms(1700000000000, SHANGHAI)
    assert items[0].created.utcoffset() == EIGHT_HOURS
    assert REGISTRY["watchlist_stocks"].order is None


def test_watchlist_stocks_map_category_and_pid(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, STOCKS, fixture_reply("watchlist_stocks/empty"))

    h.call("watchlist_stocks", pid=-110, category="stock")

    [sent] = h.upstream.sent
    assert sent.params == {"pid": "-110", "category": "1", "size": "1000"}


def test_watchlist_stocks_take_no_uid(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)

    with pytest.raises(InvalidParams) as caught:
        h.call("watchlist_stocks", uid=123)

    assert "uid" not in WatchlistStocksParams.model_fields
    assert list(caught.value.valid_values) == ["pid", "category"]
    assert h.upstream.sent == []


def test_watchlist_stocks_reject_an_unknown_category(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)

    with pytest.raises(InvalidParams) as caught:
        h.call("watchlist_stocks", category="fund")

    assert list(caught.value.valid_values) == ["stock"]
    assert h.upstream.sent == []


def test_watchlist_stocks_need_the_u_cookie_in_the_credentials(
    make: Callable[..., Harness],
) -> None:
    h = make(token="fake-bare-token")

    with pytest.raises(LoginRequired) as caught:
        h.call("watchlist_stocks")

    assert "u cookie" in caught.value.message
    assert h.upstream.sent == []


def test_watchlist_stocks_ignore_the_echo_and_keep_no_undeclared_key(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    item = {"symbol": "SH600000", "uid": 7, "undeclared": "secret-field"}
    data = {"pid": -5, "category": 1, "stocks": [item]}
    h.upstream.route(STOCK, STOCKS, json_reply({"data": data, "error_code": 0}))

    [only] = h.call("watchlist_stocks", pid=-1)

    assert only.model_extra is None
    assert "secret" not in json.dumps(only.model_dump(), default=str)
    assert "uid" not in only.model_dump()


def test_watchlist_stocks_without_a_list_are_upstream_changed(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN)
    h.upstream.route(STOCK, STOCKS, json_reply({"data": {"pid": -1}, "error_code": 0}))

    with pytest.raises(UpstreamChanged):
        h.call("watchlist_stocks")


def test_watchlist_stocks_fence_the_remark_only_when_asked(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN)
    item = {"symbol": "SH600000", "name": "浦发银行", "remark": "<b>示例</b>备注"}
    h.upstream.route(STOCK, STOCKS, json_reply({"data": {"stocks": [item]}, "error_code": 0}))

    default = rendered(h, "watchlist_stocks")
    asked = rendered(h, "watchlist_stocks", fields=["symbol", "remark"])

    assert "备注" not in default
    assert f"{FENCE_BEGIN}\nsymbol,remark\nSH600000,示例 备注\n{FENCE_END}\n" in asked


def test_an_empty_watchlist_group_names_the_u_cookie(make: Callable[..., Harness]) -> None:
    h = make(token=FAKE_LOGIN, transport=ReplayTransport(REPLAY_DIR))

    text = rendered(h, "watchlist_stocks", pid=-7)

    assert "# note: no rows: the group is empty, or the credentials lack a valid u cookie." in text
    assert text.endswith("# next: none")


def test_watchlist_stocks_pick_a_group_from_the_replay_fixtures(
    make: Callable[..., Harness],
) -> None:
    h = make(token=FAKE_LOGIN, transport=ReplayTransport(REPLAY_DIR))

    assert len(h.call("watchlist_stocks")) == 3
    assert h.call("watchlist_stocks", pid=-7) == []
