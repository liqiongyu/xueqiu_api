from __future__ import annotations

import pytest
import respx
from httpx import Response

from xueqiu import AsyncXueqiuClient, XueqiuClient
from xueqiu.api.urls import EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS


@respx.mock
def test_eastmoney_convertible_bond_builds_params_and_omits_cookie() -> None:
    route = respx.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "pageSize": "20",
            "pageNumber": "1",
            "sortColumns": "PUBLIC_START_DATE",
            "sortTypes": "-1",
            "reportName": "RPT_BOND_CB_LIST",
            "columns": "ALL",
            "quoteColumns": EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS,
            "source": "WEB",
            "client": "WEB",
        },
    ).mock(return_value=Response(200, json={"success": True, "result": {"data": []}}))

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.eastmoney.convertible_bond(20, 1)

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.result is not None


@pytest.mark.asyncio
@respx.mock
async def test_async_eastmoney_convertible_bond() -> None:
    route = respx.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "pageSize": "5",
            "pageNumber": "2",
            "sortColumns": "PUBLIC_START_DATE",
            "sortTypes": "-1",
            "reportName": "RPT_BOND_CB_LIST",
            "columns": "ALL",
            "quoteColumns": EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS,
            "source": "WEB",
            "client": "WEB",
        },
    ).mock(return_value=Response(200, json={"success": True, "result": {"data": []}}))

    async with AsyncXueqiuClient(cookie="xq_a_token=mock; u=mock") as client:
        resp = await client.eastmoney.convertible_bond(5, 2)

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.result is not None
