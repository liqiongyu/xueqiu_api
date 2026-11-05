"""Capital flow endpoints through Client and AsyncClient: units, dates, order, pages and text."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY, InvalidParams, UpstreamChanged
from xueqiu_api._render import compact_json, render
from xueqiu_api.endpoints.capital_assort import CapitalAssort
from xueqiu_api.endpoints.capital_distribution import CapitalDistribution
from xueqiu_api.endpoints.capital_flow import CapitalFlow
from xueqiu_api.endpoints.capital_history import CapitalHistory
from xueqiu_api.endpoints.capital_history_detail import CapitalHistoryDetail
from xueqiu_api.endpoints.capital_longhu import LonghuEntry
from xueqiu_api.endpoints.capital_margin import MarginHistory

from .harness import Harness, fixture_reply, json_reply, replay_body

STOCK = "stock.xueqiu.com"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
LONGHU = REGISTRY["capital_longhu"]
A_SHARES_ONLY = (
    "capital_history",
    "capital_history_detail",
    "capital_distribution",
    "capital_assort",
    "capital_margin",
    "capital_longhu",
)


def checked(h: Harness, name: str, result_type: type, **params: Any) -> Any:
    """The result of ``name``, checked to be a ``result_type``."""
    result = h.call(name, **params)
    assert isinstance(result, result_type)
    return result


def route(h: Harness, name: str, case: str = "sh600000") -> None:
    h.upstream.route(STOCK, REGISTRY[name].path, fixture_reply(f"{name}/{case}"))


def sent_params(h: Harness, name: str) -> dict[str, str]:
    [sent] = h.upstream.requests(STOCK, REGISTRY[name].path)
    return sent.params


def rendered(
    h: Harness, name: str, *, fields: Sequence[str] | None = None, limit: int = 0, **params: Any
) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=fields,
        limit=limit or spec.agent.row_default,
        max_chars=20_000,
        fetched_at=datetime(2026, 11, 5, 10, 0, tzinfo=SHANGHAI),
        next_call=lambda following, offset: compact_json({**following, "offset": offset}),
    )


def longhu_row(seq: int) -> dict[str, Any]:
    return {"seq": seq, "td_date": 1510243200000, "branches": []}


def longhu_reply(*groups: list[dict[str, Any]]) -> Any:
    data = {"items": list(groups), "items_size": len(groups)}
    return json_reply({"data": data, "error_code": 0, "error_description": ""})


@pytest.mark.sample("capital_flow")
def test_capital_flow_is_columnar_in_shanghai_time_for_an_a_share(h: Harness) -> None:
    route(h, "capital_flow")

    flow = checked(h, "capital_flow", CapitalFlow, symbol="600000")

    assert sent_params(h, "capital_flow") == {"symbol": "SH600000"}
    assert (flow.symbol, len(flow)) == ("SH600000", 7)
    assert flow.timestamps[0] == datetime(2026, 11, 4, 9, 30, tzinfo=SHANGHAI)
    assert flow.timestamps[-1] == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert {moment.tzinfo for moment in flow.timestamps} == {SHANGHAI}
    assert flow.columns["net_inflow"][:2] == (8708224.0, 28264747.0)
    assert set(flow.columns["type"]) == {None}
    assert list(flow.timestamps) == sorted(flow.timestamps)
    assert REGISTRY["capital_flow"].order == "oldest_first"


@pytest.mark.sample("capital_flow")
def test_capital_flow_uses_hong_kong_time_for_a_hong_kong_stock(h: Harness) -> None:
    route(h, "capital_flow", "00700")

    flow = h.call("capital_flow", symbol="00700")

    assert sent_params(h, "capital_flow") == {"symbol": "00700"}
    assert {moment.tzinfo for moment in flow.timestamps} == {HONG_KONG}
    assert flow.timestamps[-1] == datetime(2026, 11, 4, 16, 0, tzinfo=HONG_KONG)


def test_capital_flow_without_points_is_empty_and_says_so(h: Harness) -> None:
    empty: Any = {"data": {"symbol": "SH600000", "items": []}, "error_code": 0}
    h.upstream.route(STOCK, REGISTRY["capital_flow"].path, json_reply(empty))

    assert len(h.call("capital_flow", symbol="SH600000")) == 0
    text = rendered(h, "capital_flow", symbol="SH600000")

    assert "rows=0" in text
    assert "# note: no rows" in text


def test_capital_flow_renders_the_latest_points_with_stats_over_all(h: Harness) -> None:
    route(h, "capital_flow")

    text = rendered(h, "capital_flow", symbol="SH600000", limit=2)

    assert "# stats: points=7 first_timestamp=2026-11-04T09:30:00+08:00" in text
    assert "net_inflow_last=-82897800.67" in text
    assert "timestamp,net_inflow\n" in text
    assert "2026-11-04T14:59:00+08:00,-80110000\n" in text
    assert "2026-11-04T09:30:00+08:00," not in text.split("timestamp,net_inflow\n")[1]
    assert text.endswith('# next: {"symbol":"SH600000","offset":2}')


@pytest.mark.sample("capital_history")
def test_capital_history_gives_days_in_shanghai_and_their_sums(h: Harness) -> None:
    route(h, "capital_history")

    history = checked(h, "capital_history", CapitalHistory, symbol="600000")

    assert sent_params(h, "capital_history") == {"symbol": "SH600000", "count": "20"}
    assert (history.symbol, len(history)) == ("SH600000", 10)
    dates = [day.trade_date for day in history.items]
    assert dates == sorted(dates)
    assert (dates[0], dates[-1]) == (date(2026, 10, 22), date(2026, 11, 4))
    raw = replay_body("capital_history/sh600000")["data"]["items"][-1]["timestamp"]
    assert datetime.fromtimestamp(raw / 1000, UTC).date() == date(2026, 11, 3)
    assert REGISTRY["capital_history"].order == "oldest_first"
    # The fixture has 10 of the 20 days, so sum20 can only be read back, not added up.
    flows = [day.net_inflow for day in history.items]
    assert history.sum3 == pytest.approx(sum(flows[-3:]), abs=0.01)
    assert history.sum5 == pytest.approx(sum(flows[-5:]), abs=0.01)
    assert history.sum10 == pytest.approx(sum(flows[-10:]), abs=0.01)
    assert history.sum20 == -444314834.36


def test_capital_history_sorts_days_it_gets_in_another_order(h: Harness) -> None:
    body = replay_body("capital_history/sh600000")
    body["data"]["items"].reverse()
    h.upstream.route(STOCK, REGISTRY["capital_history"].path, json_reply(body))

    history = h.call("capital_history", symbol="SH600000")

    assert history.items[0].trade_date == date(2026, 10, 22)


def test_capital_history_renders_the_sums_as_info_and_the_latest_days(h: Harness) -> None:
    route(h, "capital_history")

    text = rendered(h, "capital_history", symbol="SH600000", limit=2)

    assert "# info: sum3=-256772308.81 sum5=-197571914.29 sum10=-325615440.96 " in text
    assert "trade_date,net_inflow\n2026-11-03,-99286278.31\n2026-11-04,-82897800.67\n" in text
    assert text.endswith('# next: {"symbol":"SH600000","offset":2}')


@pytest.mark.sample("capital_history_detail")
def test_capital_history_detail_splits_each_day_by_order_size(h: Harness) -> None:
    route(h, "capital_history_detail")

    detail = checked(h, "capital_history_detail", CapitalHistoryDetail, symbol="SH600000")

    assert sent_params(h, "capital_history_detail") == {"symbol": "SH600000", "count": "20"}
    days = detail.items
    # The upstream sends the latest first.
    assert replay_body("capital_history_detail/sh600000")["data"]["items"][0]["close"] == 10.09
    assert (days[0].trade_date, days[-1].trade_date) == (date(2026, 10, 22), date(2026, 11, 4))
    assert [day.trade_date for day in days] == sorted(day.trade_date for day in days)
    assert REGISTRY["capital_history_detail"].order == "oldest_first"
    assert (days[-1].close, days[-1].percent) == (10.09, 0.0)
    for older, day in itertools.pairwise(days):
        assert day.percent == pytest.approx((day.close / older.close - 1) * 100, abs=0.051)
    for day in days:
        assert day.net_inflow == pytest.approx(day.xlarge_net + day.large_net, abs=0.01)
        nets = (day.xlarge_net, day.large_net, day.medium_net, day.small_net)
        assert sum(nets) == pytest.approx(0, abs=0.01)


@pytest.mark.sample("capital_history")
@pytest.mark.sample("capital_history_detail")
def test_the_two_daily_series_agree_on_net_inflow(h: Harness) -> None:
    route(h, "capital_history")
    route(h, "capital_history_detail")

    history = h.call("capital_history", symbol="SH600000")
    detail = h.call("capital_history_detail", symbol="SH600000")

    assert [day.trade_date for day in history.items] == [day.trade_date for day in detail.items]
    assert [day.net_inflow for day in history.items] == pytest.approx(
        [day.net_inflow for day in detail.items], abs=0.01
    )


@pytest.mark.sample("capital_distribution")
def test_capital_distribution_flattens_both_sides(h: Harness) -> None:
    route(h, "capital_distribution")

    record = checked(h, "capital_distribution", CapitalDistribution, symbol="SH600000")

    assert sent_params(h, "capital_distribution") == {"symbol": "SH600000"}
    assert record.symbol == "SH600000"
    assert record.timestamp == datetime(2026, 11, 4, 14, 56, 59, 520000, tzinfo=SHANGHAI)
    assert record.timestamp.tzinfo == SHANGHAI
    assert (record.buy_xlarge, record.sell_small) == (183939647.32, 104266951.85)
    bought = (record.buy_xlarge, record.buy_large, record.buy_medium, record.buy_small)
    sold = (record.sell_xlarge, record.sell_large, record.sell_medium, record.sell_small)
    assert sum(bought) == pytest.approx(sum(sold), abs=0.01)
    main = bought[0] + bought[1] - sold[0] - sold[1]
    assert record.net_inflow == pytest.approx(main, abs=0.01)
    assert record.analysis == "合成分析文本一；合成分析文本二"
    assert "distribution" not in (record.model_extra or {})
    assert REGISTRY["capital_distribution"].order is None


def test_capital_distribution_keeps_its_prose_out_unless_asked_and_fenced(h: Harness) -> None:
    route(h, "capital_distribution")

    plain = rendered(h, "capital_distribution", symbol="SH600000")
    asked = rendered(h, "capital_distribution", symbol="SH600000", fields=["analysis"])

    assert "合成分析文本" not in plain
    assert "timestamp: 2026-11-04T14:56:59+08:00\n" in plain
    assert "buy_xlarge: 183939647.32\n" in plain
    assert (
        "# untrusted upstream text begin\n"
        "analysis: 合成分析文本一；合成分析文本二\n"
        "# untrusted upstream text end"
    ) in asked


@pytest.mark.sample("capital_assort")
def test_capital_assort_gives_totals_per_side_on_a_shanghai_date(h: Harness) -> None:
    route(h, "capital_assort")

    record = checked(h, "capital_assort", CapitalAssort, symbol="SH600000")

    assert sent_params(h, "capital_assort") == {"symbol": "SH600000"}
    assert (record.symbol, record.trade_date) == ("SH600000", date(2026, 11, 4))
    assert record.buy_total == record.buy_large + record.buy_medium + record.buy_small
    assert record.sell_total == record.sell_large + record.sell_medium + record.sell_small
    assert (record.buy_xlarge, record.sell_xlarge) == (None, None)
    assert "created_at" in (record.model_extra or {})
    assert REGISTRY["capital_assort"].order is None

    text = rendered(h, "capital_assort", symbol="SH600000")
    assert "trade_date: 2026-11-04\n" in text
    assert "buy_total: 478871068\n" in text


@pytest.mark.sample("capital_margin")
def test_capital_margin_gives_the_latest_days_first(h: Harness) -> None:
    route(h, "capital_margin")

    history = checked(h, "capital_margin", MarginHistory, symbol="600000", count=5)

    assert sent_params(h, "capital_margin") == {"symbol": "SH600000", "page": "1", "size": "5"}
    assert (history.symbol, history.inclusion_date) == ("SH600000", date(2010, 3, 31))
    dates = [day.trade_date for day in history.items]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == date(2026, 11, 4)
    assert REGISTRY["capital_margin"].order == "newest_first"
    latest = history.items[0]
    assert latest.total_balance == 3115099316.0
    assert (latest.margin_trading_buy_amt, latest.margin_trading_net_buy_amt) == (
        46801189.0,
        -25272130.0,
    )
    for day in history.items:
        balances = day.margin_trading_balance + day.short_selling_amt_balance
        assert day.total_balance == balances


def test_capital_margin_asks_for_50_days_by_default_and_120_at_most(h: Harness) -> None:
    route(h, "capital_margin")

    h.call("capital_margin", symbol="SH600000")
    assert sent_params(h, "capital_margin")["size"] == "50"

    spec = REGISTRY["capital_margin"]
    assert spec.pagination is not None and spec.pagination.style == "latest"
    assert spec.validate({"symbol": "SH600000", "count": 120}).count == 120
    assert not hasattr(h.client, "capital_margin_pages")


@pytest.mark.parametrize("params", [{"count": 121}, {"count": 0}, {"page": 2}])
def test_capital_margin_rejects_other_ranges_before_any_request(
    h: Harness, params: dict[str, int]
) -> None:
    with pytest.raises(InvalidParams):
        h.call("capital_margin", symbol="SH600000", **params)

    assert h.upstream.sent == []


def test_capital_margin_past_its_rows_is_empty(h: Harness) -> None:
    empty: Any = {"data": {"items": []}, "error_code": 0}
    h.upstream.route(STOCK, REGISTRY["capital_margin"].path, json_reply(empty))

    history = h.call("capital_margin", symbol="SH600000")

    assert (len(history), history.inclusion_date) == (0, None)
    assert "# note: no rows: this stock may not be" in rendered(
        h, "capital_margin", symbol="SH600000"
    )


def test_capital_margin_renders_the_inclusion_date_and_continues_by_offset(h: Harness) -> None:
    route(h, "capital_margin")

    text = rendered(h, "capital_margin", symbol="SH600000", count=20, limit=2)

    assert "# info: inclusion_date=2010-03-31\n" in text
    assert "# page:" not in text
    assert "\n2026-11-04,3087549580,27549736,3115099316,46801189,-25272130\n2026-11-03," in text
    assert text.endswith('# next: {"symbol":"SH600000","count":20,"offset":2}')


@pytest.mark.sample("capital_longhu")
def test_capital_longhu_flattens_the_groups_latest_first(h: Harness) -> None:
    route(h, "capital_longhu", "sh600000_p1")

    page = h.call("capital_longhu", symbol="600000", page_size=2)

    assert sent_params(h, "capital_longhu") == {"symbol": "SH600000", "page": "1", "size": "2"}
    assert all(isinstance(entry, LonghuEntry) for entry in page.items)
    assert [(entry.seq, entry.trade_date) for entry in page] == [
        (20300285, date(2017, 11, 10)),
        (20297375, date(2017, 9, 27)),
        (20297374, date(2017, 9, 27)),
    ]
    assert REGISTRY["capital_longhu"].order == "newest_first"
    # items_size counts the groups of this page, so it is not a total.
    assert (page.total, page.short) == (None, False)
    assert page.next_params == {"symbol": "SH600000", "page": 2, "page_size": 2}
    assert page.items[0].percent == 0.64
    for entry in page:
        assert entry.net_buy == entry.buy_total - entry.sell_total
        assert entry.buy_total == pytest.approx(sum(b.buy_amt for b in entry.branches), abs=0.01)
        assert entry.ratio_total == pytest.approx(entry.buy_total / entry.trans_amt * 100, abs=0.01)
        for branch in entry.branches:
            assert branch.ratio == pytest.approx(branch.buy_amt / entry.trans_amt * 100, abs=0.01)
    assert "code_tag" in (page.items[0].model_extra or {})


def test_capital_longhu_pages_end_on_the_empty_page(h: Harness) -> None:
    h.upstream.route(
        STOCK,
        LONGHU.path,
        longhu_reply([longhu_row(9)], [longhu_row(8), longhu_row(7)]),
        longhu_reply([longhu_row(6)]),
        longhu_reply(),
    )

    pages, error = h.walk(LONGHU, symbol="SH600000", page_size=2)

    assert error is None
    assert [[entry.seq for entry in page] for page in pages] == [[9, 8, 7], [6], []]
    assert pages[1].short is True
    assert pages[-1].next_params is None
    assert [sent.params["page"] for sent in h.upstream.requests(STOCK, LONGHU.path)] == [
        "1",
        "2",
        "3",
    ]


def test_capital_longhu_rows_after_a_short_page_mean_the_size_was_ignored(h: Harness) -> None:
    h.upstream.route(
        STOCK, LONGHU.path, longhu_reply([longhu_row(9)]), longhu_reply([longhu_row(8)])
    )

    pages, error = h.walk(LONGHU, symbol="SH600000", page_size=2)

    assert len(pages) == 1
    assert isinstance(error, UpstreamChanged)


def test_capital_longhu_drops_rows_repeated_across_pages(h: Harness) -> None:
    h.upstream.route(
        STOCK,
        LONGHU.path,
        longhu_reply([longhu_row(9)], [longhu_row(8)]),
        longhu_reply([longhu_row(8)], [longhu_row(7)]),
        longhu_reply(),
    )

    pages, error = h.walk(LONGHU, symbol="SH600000", page_size=2)

    assert error is None
    assert [[entry.seq for entry in page] for page in pages] == [[9, 8], [7], []]


def test_capital_longhu_renders_a_page_and_its_brokers_on_request(h: Harness) -> None:
    route(h, "capital_longhu", "sh600000_p1")

    text = rendered(h, "capital_longhu", symbol="SH600000", page_size=2)
    brokers = rendered(h, "capital_longhu", symbol="SH600000", fields=["seq", "branches"])

    assert "# page: page=1 page_size=2\n" in text
    assert "trade_date,info_type_name,percent,trans_amt,buy_total,sell_total," in text
    assert "\n2017-09-27,合成上榜原因,-0.08,339097277,5000000,0,5000000,1.47\n" in text
    assert text.endswith('# next: {"symbol":"SH600000","page":2,"page_size":2,"offset":0}')
    assert brokers.count("示例证券1") >= 1


def test_an_empty_capital_longhu_page_says_the_stock_may_not_be_listed(h: Harness) -> None:
    h.upstream.route(STOCK, LONGHU.path, longhu_reply())

    text = rendered(h, "capital_longhu", symbol="SH600000")

    assert "# note: no rows: a stock that was never on this list has none." in text


@pytest.mark.sample("capital_block_trades")
def test_capital_block_trades_turnover_is_price_times_shares_latest_first(h: Harness) -> None:
    h.upstream.route(
        STOCK,
        REGISTRY["capital_block_trades"].path,
        fixture_reply("capital_block_trades/sh600000_p1"),
    )

    page = h.call("capital_block_trades", symbol="SH600000")

    dates = [row.trade_date for row in page]
    assert dates == sorted(dates, reverse=True)
    for row in page:
        assert row.vol * row.trans_price == pytest.approx(row.trans_amt, rel=1e-4)


@pytest.mark.parametrize("name", A_SHARES_ONLY)
def test_a_shares_only_endpoints_reject_other_markets_before_any_request(
    h: Harness, name: str
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, symbol="00700")

    assert (caught.value.field, list(caught.value.valid_values)) == ("symbol", ["CN"])
    assert h.upstream.sent == []


def test_capital_flow_rejects_us_stocks_before_any_request(h: Harness) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("capital_flow", symbol="AAPL")

    assert (caught.value.field, list(caught.value.valid_values)) == ("symbol", ["CN", "HK"])
    assert h.upstream.sent == []


@pytest.mark.parametrize("name", [*A_SHARES_ONLY, "capital_flow"])
@pytest.mark.parametrize("data", [{}, ["unexpected"], None], ids=["empty", "list", "null"])
def test_a_payload_of_another_shape_is_upstream_changed(h: Harness, name: str, data: Any) -> None:
    body = {"data": data, "error_code": 0, "error_description": ""}
    h.upstream.route(STOCK, REGISTRY[name].path, json_reply(body))

    with pytest.raises(UpstreamChanged) as caught:
        h.call(name, symbol="SH600000")

    assert caught.value.endpoint == name
