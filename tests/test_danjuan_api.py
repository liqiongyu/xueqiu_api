from __future__ import annotations

import pytest
import respx
from httpx import Response

from xueqiu import AsyncXueqiuClient, XueqiuClient


@respx.mock
def test_danjuan_requests_do_not_send_xueqiu_cookie() -> None:
    route = respx.get(
        "https://danjuanapp.com/djapi/fund/detail/008975",
    ).mock(return_value=Response(200, json={"code": 0, "data": {"fund_code": "008975"}}))

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.danjuan.fund_detail("008975")

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.data is not None


@respx.mock
def test_danjuan_nav_history_builds_params() -> None:
    route = respx.get(
        "https://danjuanapp.com/djapi/fund/nav/history/008975",
        params={"page": "2", "size": "20"},
    ).mock(return_value=Response(200, json={"code": 0, "data": {"items": []}}))

    client = XueqiuClient()
    resp = client.danjuan.fund_nav_history("008975", page=2, size=20)

    assert route.called
    assert resp.data is not None


@pytest.mark.asyncio
@respx.mock
async def test_async_danjuan_fund_info() -> None:
    route = respx.get(
        "https://danjuanapp.com/djapi/fund/008975",
    ).mock(return_value=Response(200, json={"code": 0, "data": {"fund_code": "008975"}}))

    async with AsyncXueqiuClient(cookie="xq_a_token=mock; u=mock") as client:
        resp = await client.danjuan.fund_info("008975")

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.data is not None
