from __future__ import annotations

import respx
from httpx import Response

from xueqiu import XueqiuClient
from xueqiu.errors import XueqiuAPIError, XueqiuAuthError


def test_require_auth_without_cookie_raises() -> None:
    client = XueqiuClient(cookie=None)
    try:
        client.request_json("GET", "/v5/stock/quote.json", require_auth=True)
    except XueqiuAuthError:
        return
    raise AssertionError("Expected XueqiuAuthError")


@respx.mock
def test_error_code_raises_api_error() -> None:
    route = respx.get("https://stock.xueqiu.com/v5/stock/realtime/quotec.json").mock(
        return_value=Response(
            200,
            json={"data": None, "error_code": 400016, "error_description": "mock error"},
        )
    )
    client = XueqiuClient()
    try:
        client.request_json("GET", "/v5/stock/realtime/quotec.json")
    except XueqiuAPIError as e:
        assert e.error_code == 400016
        assert route.called
        return
    raise AssertionError("Expected XueqiuAPIError")


@respx.mock
def test_success_false_raises_api_error() -> None:
    route = respx.get("https://xueqiu.com/query/v1/suggest_stock.json").mock(
        return_value=Response(
            200,
            json={"code": 400016, "message": "blocked", "success": False},
        )
    )
    client = XueqiuClient(cookie="xq_a_token=mock; u=mock")
    try:
        client.request_json(
            "GET",
            "https://xueqiu.com/query/v1/suggest_stock.json",
            params={"q": "SH600000"},
        )
    except XueqiuAPIError as e:
        assert e.error_code == 400016
        assert "blocked" in (e.error_description or "")
        assert route.called
        return
    raise AssertionError("Expected XueqiuAPIError")
