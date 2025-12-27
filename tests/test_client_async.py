from __future__ import annotations

import pytest
import respx
from httpx import Response

from xueqiu import AsyncXueqiuClient


@pytest.mark.asyncio
@respx.mock
async def test_async_quotec() -> None:
    route = respx.get(
        "https://stock.xueqiu.com/v5/stock/realtime/quotec.json",
        params={"symbol": "SZ002027,SH600000"},
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [{"symbol": "SZ002027"}, {"symbol": "SH600000"}],
                "error_code": 0,
                "error_description": None,
            },
        )
    )

    async with AsyncXueqiuClient() as client:
        resp = await client.realtime.quotec(["SZ002027", "SH600000"])

    assert route.called
    assert resp.data is not None
    assert [q.symbol for q in resp.data] == ["SZ002027", "SH600000"]
