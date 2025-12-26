from __future__ import annotations

import os
from datetime import datetime

from xueqiu import XueqiuClient

SYMBOLS = ["SZ002437", "SH600887", "SH601318"]


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

    client = XueqiuClient(cookie=cookie)
    with client:
        for symbol in SYMBOLS:
            quote_resp = client.realtime.quotec(symbol)
            quote = quote_resp.data[0] if quote_resp.data else None

            current = quote.current if quote else None
            ts = quote.timestamp if quote else None
            print(f"[quotec] {symbol}: current={current} ts={ts}")

            detail_resp = client.realtime.quote_detail(symbol)
            if detail_resp.data and detail_resp.data.quote:
                detail_quote = detail_resp.data.quote
            else:
                detail_quote = None
            name = detail_quote.name if detail_quote else None
            detail_current = detail_quote.current if detail_quote else None
            print(f"[quote_detail] {symbol}: name={name} current={detail_current}")

            kline_resp = client.realtime.kline(symbol, period="day", count=5)
            bars = kline_resp.data.bars() if kline_resp.data else []
            last_bar = bars[0] if bars else None
            first_ts = last_bar.timestamp if last_bar else None
            close = last_bar.close if last_bar else None
            print(f"[kline] {symbol}: bars={len(bars)} first_ts={first_ts} close={close}")

            indicator_resp = client.finance.indicator(symbol, count=2)
            first_period = (
                indicator_resp.data.periods[0]
                if indicator_resp.data and indicator_resp.data.periods
                else None
            )
            report_date: datetime | None = getattr(first_period, "report_date", None)
            metrics = getattr(first_period, "metrics", {}) or {}
            print(f"[finance.indicator] {symbol}: report_date={report_date} metrics={len(metrics)}")


if __name__ == "__main__":
    main()
