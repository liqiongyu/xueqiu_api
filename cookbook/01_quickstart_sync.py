from __future__ import annotations

import os

from xueqiu import XueqiuClient


def _require_cookie() -> None:
    if os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE"):
        return
    raise SystemExit(
        "Missing auth. Set `XUEQIU_TOKEN` (recommended) or `XUEQIU_COOKIE`.\n"
        "Example:\n"
        "  export XUEQIU_TOKEN='xq_a_token=...; u=...'\n"
    )


def main() -> None:
    _require_cookie()

    symbols = ["SZ002027", "SH600000"]

    with XueqiuClient.from_env() as client:
        # Often works without auth (but can still be rate-limited).
        quotes = client.realtime.quotec(symbols).data or []
        print(f"[quotec] returned {len(quotes)} quotes")
        for q in quotes:
            print(f"  {q.symbol}: current={q.current} percent={q.percent} ts={q.timestamp}")

        # Requires auth.
        detail = client.realtime.quote_detail(symbols[0]).data
        name = detail.quote.name if detail and detail.quote else None
        current = detail.quote.current if detail and detail.quote else None
        print(f"[quote_detail] {symbols[0]}: name={name} current={current}")


if __name__ == "__main__":
    main()
