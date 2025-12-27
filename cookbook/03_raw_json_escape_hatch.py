from __future__ import annotations

import os
from pprint import pprint

from xueqiu import XueqiuClient


def _get_cookie() -> str | None:
    return os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE")


def main() -> None:
    cookie = _get_cookie()
    if not cookie:
        raise SystemExit(
            "Missing auth. Set `XUEQIU_TOKEN` (recommended) or `XUEQIU_COOKIE`.\n"
            "Example:\n"
            "  export XUEQIU_TOKEN='xq_a_token=...; u=...'\n"
        )

    with XueqiuClient(cookie=cookie) as client:
        # Raw JSON escape hatch: useful when an endpoint changes schema or isn't modeled yet.
        payload = client.request_json(
            "GET",
            "/v5/stock/realtime/quotec.json",
            params={"symbol": "SZ002027"},
            require_auth=False,
        )
        pprint(payload)


if __name__ == "__main__":
    main()
