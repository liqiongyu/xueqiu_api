from __future__ import annotations

from datetime import datetime, timezone

import respx
from httpx import Response

from xueqiu import XueqiuClient


@respx.mock
def test_finance_cash_flow_v2_builds_params() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/finance/cn/cash_flow.json",
        params={"symbol": "SH600000", "type": "all", "is_detail": "true", "count": "5"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "quote_name": "浦发银行",
                    "currency": "CNY",
                    "list": [
                        {
                            "report_date": 1514649600000,
                            "report_name": "2017年报",
                            "ncf_from_oa": [-140673000000.0, 0.2673],
                        }
                    ],
                },
                "error_code": 0,
                "error_description": "",
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.finance.cash_flow_v2("SH600000", count=5)
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None
    assert resp.data.quote_name == "浦发银行"
    assert len(resp.data.periods) == 1
    assert resp.data.periods[0].report_date == datetime.fromtimestamp(1514649600, tz=timezone.utc)
    assert resp.data.periods[0].metrics["ncf_from_oa"].value == -140673000000.0


@respx.mock
def test_report_latest() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/stock/report/latest.json",
        params={"symbol": "SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {"list": [{"title": "mock", "pub_date": 1514649600000}]},
                "error_code": 0,
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.report.latest("SH600000")
    assert route.called
    assert resp.data is not None
    assert len(resp.data.items) == 1
    assert resp.data.items[0].title == "mock"
    assert resp.data.items[0].pub_date == datetime.fromtimestamp(1514649600, tz=timezone.utc)


@respx.mock
def test_capital_margin() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/capital/margin.json",
        params={"symbol": "SH600000", "page": "1", "size": "180"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {"items": [{"td_date": 1541347200000}]},
                "error_code": 0,
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.capital.margin("SH600000")
    assert route.called
    assert resp.data is not None
    assert len(resp.data.items) == 1
    assert resp.data.items[0].trade_date == datetime.fromtimestamp(1541347200, tz=timezone.utc)


@respx.mock
def test_f10_industry_compare() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/f10/cn/industry/compare.json",
        params={"type": "single", "symbol": "SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "ind_name": "银行",
                    "ind_code": "801780",
                    "ind_class": "sw_l1",
                    "quote_time": 1514649600000,
                    "report_name": "2017年报",
                    "count": 1,
                    "avg": {"pe_ttm": 6.0},
                    "min": {},
                    "max": {},
                    "items": [{"symbol": "SH600000", "name": "浦发银行", "pe_ttm": 5.0}],
                },
                "error_code": 0,
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.f10.industry_compare("SH600000")
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None
    assert resp.data.industry_name == "银行"
    assert resp.data.ind_name == "银行"
    assert resp.data.industry_code == "801780"
    assert resp.data.ind_code == "801780"
    assert resp.data.industry_class == "sw_l1"
    assert resp.data.ind_class == "sw_l1"
    assert resp.data.quote_at == datetime.fromtimestamp(1514649600, tz=timezone.utc)
    assert resp.data.quote_time == datetime.fromtimestamp(1514649600, tz=timezone.utc)
    assert resp.data.items[0].pe_ttm == 5.0


@respx.mock
def test_f10_top_holders_parses_pythonic_fields() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/f10/cn/top_holders.json",
        params={"symbol": "SH600000", "circula": "1"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "times": [{"name": "2017年报", "value": 1514649600000}],
                    "items": [
                        {
                            "chg": 123.0,
                            "held_num": 456.0,
                            "held_ratio": 1.23,
                            "holder_name": "mock holder",
                        }
                    ],
                },
                "error_code": 0,
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.f10.top_holders("SH600000")
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None
    assert resp.data.times[0].value == datetime.fromtimestamp(1514649600, tz=timezone.utc)

    item = resp.data.items[0]
    assert item.change == 123.0
    assert item.chg == 123.0
    assert item.held_shares == 456.0
    assert item.held_num == 456.0
    assert item.shareholder_name == "mock holder"
    assert item.holder_name == "mock holder"


@respx.mock
def test_f10_org_holding_change_parses_pythonic_fields() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/f10/cn/org_holding/change.json",
        params={"symbol": "SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "chg_date": "2017年报",
                            "institution_num": "10",
                            "chg": 0.5,
                            "held_ratio": 1.2,
                            "price": 10.0,
                            "timestamp": 1514649600000,
                        }
                    ]
                },
                "error_code": 0,
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.f10.org_holding_change("SH600000")
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None

    item = resp.data.items[0]
    assert item.report_name == "2017年报"
    assert item.chg_date == "2017年报"
    assert item.institution_count == "10"
    assert item.institution_num == "10"
    assert item.change == 0.5
    assert item.chg == 0.5
    assert item.timestamp == datetime.fromtimestamp(1514649600, tz=timezone.utc)


@respx.mock
def test_portfolio_list() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/portfolio/list.json",
        params={"system": "true"},
    ).mock(return_value=Response(200, json={"data": {"list": []}, "error_code": 0}))

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.portfolio.list()
    assert route.called
    assert resp.data is not None


@respx.mock
def test_cube_nav_daily_uses_main_domain() -> None:
    route = respx.get(
        "https://xueqiu.com/cubes/nav_daily/all.json",
        params={"cube_symbol": "ZH000000"},
    ).mock(return_value=Response(200, json=[{"symbol": "ZH000000", "name": "mock", "list": []}]))

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.cube.nav_daily("ZH000000")
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None
    assert len(resp.data) == 1
    assert resp.data[0].symbol == "ZH000000"
    assert resp.data[0].items == []


@respx.mock
def test_suggest_stock_uses_code_success_shape() -> None:
    route = respx.get(
        "https://xueqiu.com/query/v1/suggest_stock.json",
        params={"q": "SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "code": 0,
                "message": "",
                "success": True,
                "data": [{"code": "SH600000"}],
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.suggest.stock("SH600000")
    assert route.called
    assert resp.success is True
    assert resp.code == 0
    assert len(resp.data) == 1
    assert resp.data[0].code == "SH600000"


@respx.mock
def test_suggest_stock_unwraps_items_shape() -> None:
    route = respx.get(
        "https://xueqiu.com/query/v1/suggest_stock.json",
        params={"q": "SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "code": 0,
                "message": "",
                "success": True,
                "data": {"items": [{"symbol": "SH600000"}]},
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.suggest.stock("SH600000")
    assert route.called
    assert resp.success is True
    assert resp.code == 0
    assert len(resp.data) == 1
    assert resp.data[0].code == "SH600000"
