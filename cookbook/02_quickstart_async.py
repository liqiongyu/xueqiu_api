from __future__ import annotations

import asyncio
import os

from xueqiu import AsyncXueqiuClient


def _require_cookie() -> None:
    if os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE"):
        return
    raise SystemExit(
        "Missing auth. Set `XUEQIU_TOKEN` (recommended) or `XUEQIU_COOKIE`.\n"
        "Example:\n"
        "  export XUEQIU_TOKEN='xq_a_token=...; u=...'\n"
    )


async def main() -> None:
    _require_cookie()

    async with AsyncXueqiuClient.from_env() as client:
        resp = await client.realtime.quotec(["SZ002027", "SH600000"])
        quotes = resp.data or []
        print(f"[quotec] returned {len(quotes)} quotes")
        for q in quotes:
            print(f"  {q.symbol}: current={q.current} percent={q.percent} ts={q.timestamp}")


if __name__ == "__main__":
    asyncio.run(main())
