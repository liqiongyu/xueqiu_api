from __future__ import annotations

from datetime import datetime, timezone

import respx
from httpx import Response

from xueqiu import XueqiuClient


@respx.mock
def test_quotec_parses_quote_models() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/realtime/quotec.json",
        params={"symbol": "SZ002027"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "symbol": "SZ002027",
                        "current": 1.341,
                        "percent": -0.89,
                        "timestamp": 1541486940000,
                    }
                ],
                "error_code": 0,
                "error_description": None,
            },
        )
    )

    client = XueqiuClient()
    resp = client.realtime.quotec("SZ002027")
    assert route.called
    assert resp.error_code == 0
    assert resp.data is not None
    assert resp.data[0].symbol == "SZ002027"
    assert resp.data[0].current == 1.341
    assert resp.data[0].timestamp == datetime.fromtimestamp(1541486940, tz=timezone.utc)


@respx.mock
def test_kline_builds_params() -> None:
    route = respx.get("https://stock.xueqiu.com/v5/stock/chart/kline.json").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "symbol": "SH601288",
                    "column": ["timestamp", "open", "close"],
                    "item": [[1672329600000, 2.89, 2.91]],
                },
                "error_code": 0,
                "error_description": "",
            },
        )
    )

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.realtime.kline("SH601288", begin_ms=123, count=2, period="day")
    assert route.called

    # Verify query params were constructed as expected.
    request = route.calls[0].request
    params = dict(request.url.params)
    assert params["symbol"] == "SH601288"
    assert params["begin"] == "123"
    assert params["period"] == "day"
    assert params["type"] == "before"
    assert params["count"] == "-2"

    assert resp.data is not None
    assert resp.data.symbol == "SH601288"
    bars = resp.data.bars()
    assert len(bars) == 1
    assert bars[0].timestamp == datetime.fromtimestamp(1672329600, tz=timezone.utc)
    assert bars[0].open == 2.89
    assert bars[0].close == 2.91
