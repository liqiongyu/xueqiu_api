from __future__ import annotations

from datetime import date

import pytest
import respx
from httpx import Response

from xueqiu import AsyncXueqiuClient, XueqiuClient


@respx.mock
def test_csindex_requests_do_not_send_xueqiu_cookie() -> None:
    route = respx.get(
        "https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info/000300",
    ).mock(return_value=Response(200, json={"data": {"indexCode": "000300"}}))

    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    resp = client.csindex.index_basic_info("000300")

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.data is not None


@respx.mock
def test_csindex_details_data_builds_params() -> None:
    route = respx.get(
        "https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data",
        params={"fileLang": "1", "indexCode": "000300"},
    ).mock(return_value=Response(200, json={"data": {"ok": True}}))

    client = XueqiuClient()
    resp = client.csindex.index_details_data("000300")

    assert route.called
    assert resp.data is not None


@respx.mock
def test_csindex_perf_formats_dates() -> None:
    route = respx.get(
        "https://www.csindex.com.cn/csindex-home/perf/index-perf",
        params={"indexCode": "000300", "startDate": "20250101", "endDate": "20250131"},
    ).mock(return_value=Response(200, json={"data": {"items": []}}))

    client = XueqiuClient()
    resp = client.csindex.index_perf(
        "000300",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
    )

    assert route.called
    assert resp.data is not None


@pytest.mark.asyncio
@respx.mock
async def test_async_csindex_basic_info() -> None:
    route = respx.get(
        "https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info/000300",
    ).mock(return_value=Response(200, json={"data": {"indexCode": "000300"}}))

    async with AsyncXueqiuClient(cookie="xq_a_token=mock; u=mock") as client:
        resp = await client.csindex.index_basic_info("000300")

    assert route.called
    request = route.calls[0].request
    assert "Cookie" not in request.headers
    assert resp.data is not None

