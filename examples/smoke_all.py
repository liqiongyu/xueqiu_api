from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from xueqiu import XueqiuClient
from xueqiu.errors import XueqiuError

SYMBOLS = ["SZ002437", "SH600887", "SH601318"]


def _get_cookie() -> str | None:
    return os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE")


def _print_ok(name: str, extra: str = "") -> None:
    suffix = f" {extra}" if extra else ""
    print(f"[OK] {name}{suffix}")


def _print_err(name: str, err: BaseException) -> None:
    print(f"[ERR] {name}: {err}")


def _safe(name: str, fn: Callable[[], Any], *, sleep_s: float = 0.0) -> Any | None:
    try:
        value = fn()
        _print_ok(name)
        return value
    except XueqiuError as e:
        _print_err(name, e)
        return None
    finally:
        if sleep_s:
            time.sleep(sleep_s)


def main() -> None:
    cookie = _get_cookie()
    if not cookie:
        raise SystemExit(
            "Missing auth. Set `XUEQIU_TOKEN` (recommended) or `XUEQIU_COOKIE`.\n"
            "Example:\n"
            "  export XUEQIU_TOKEN='xq_a_token=...; u=...'\n"
        )

    # Avoid rate limits / bans: keep it gentle by default.
    sleep_s = float(os.environ.get("XUEQIU_SLEEP_SECONDS") or 0.0)

    with XueqiuClient(cookie=cookie) as client:
        for symbol in SYMBOLS:
            print(f"\n=== {symbol} ===")

            _safe(
                "realtime.quotec",
                lambda symbol=symbol: client.realtime.quotec(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "realtime.quote_detail",
                lambda symbol=symbol: client.realtime.quote_detail(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "realtime.pankou",
                lambda symbol=symbol: client.realtime.pankou(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "realtime.kline",
                lambda symbol=symbol: client.realtime.kline(symbol, period="day", count=30),
                sleep_s=sleep_s,
            )

            _safe(
                "finance.cash_flow",
                lambda symbol=symbol: client.finance.cash_flow(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.indicator",
                lambda symbol=symbol: client.finance.indicator(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.balance",
                lambda symbol=symbol: client.finance.balance(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.income",
                lambda symbol=symbol: client.finance.income(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.business",
                lambda symbol=symbol: client.finance.business(symbol),
                sleep_s=sleep_s,
            )

            _safe(
                "finance.cash_flow_v2",
                lambda symbol=symbol: client.finance.cash_flow_v2(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.indicator_v2",
                lambda symbol=symbol: client.finance.indicator_v2(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.balance_v2",
                lambda symbol=symbol: client.finance.balance_v2(symbol, count=5),
                sleep_s=sleep_s,
            )
            _safe(
                "finance.income_v2",
                lambda symbol=symbol: client.finance.income_v2(symbol, count=5),
                sleep_s=sleep_s,
            )

            _safe(
                "report.latest",
                lambda symbol=symbol: client.report.latest(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "report.earning_forecast",
                lambda symbol=symbol: client.report.earning_forecast(symbol),
                sleep_s=sleep_s,
            )

            _safe(
                "capital.margin",
                lambda symbol=symbol: client.capital.margin(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "capital.blocktrans",
                lambda symbol=symbol: client.capital.blocktrans(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "capital.assort",
                lambda symbol=symbol: client.capital.assort(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "capital.flow",
                lambda symbol=symbol: client.capital.flow(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "capital.history",
                lambda symbol=symbol: client.capital.history(symbol),
                sleep_s=sleep_s,
            )

            _safe(
                "f10.skholderchg",
                lambda symbol=symbol: client.f10.skholderchg(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.skholder",
                lambda symbol=symbol: client.f10.skholder(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.industry",
                lambda symbol=symbol: client.f10.industry(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.holders",
                lambda symbol=symbol: client.f10.holders(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.bonus",
                lambda symbol=symbol: client.f10.bonus(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.org_holding_change",
                lambda symbol=symbol: client.f10.org_holding_change(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.industry_compare",
                lambda symbol=symbol: client.f10.industry_compare(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.business_analysis",
                lambda symbol=symbol: client.f10.business_analysis(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.shareschg",
                lambda symbol=symbol: client.f10.shareschg(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.top_holders",
                lambda symbol=symbol: client.f10.top_holders(symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "f10.indicator",
                lambda symbol=symbol: client.f10.indicator(symbol),
                sleep_s=sleep_s,
            )

            _safe(
                "suggest.stock",
                lambda symbol=symbol: client.suggest.stock(symbol),
                sleep_s=sleep_s,
            )

        print("\n=== portfolio ===")
        portfolio = _safe("portfolio.list", lambda: client.portfolio.list(), sleep_s=sleep_s)
        if portfolio and portfolio.data:
            # Try to pick a stock watchlist (pid) if available.
            candidates = portfolio.data.stocks or portfolio.data.cubes or portfolio.data.funds
            pid = candidates[0].id if candidates else None
            if pid is not None:
                _safe("portfolio.stocks", lambda: client.portfolio.stocks(pid), sleep_s=sleep_s)
            else:
                print("[SKIP] portfolio.stocks: no pid found in portfolio.list")

        cube_symbol = os.environ.get("XUEQIU_CUBE_SYMBOL")
        if cube_symbol:
            print("\n=== cube ===")
            _safe("cube.nav_daily", lambda: client.cube.nav_daily(cube_symbol), sleep_s=sleep_s)
            _safe(
                "cube.rebalancing_history",
                lambda: client.cube.rebalancing_history(cube_symbol),
                sleep_s=sleep_s,
            )
            _safe(
                "cube.rebalancing_current",
                lambda: client.cube.rebalancing_current(cube_symbol),
                sleep_s=sleep_s,
            )
            _safe("cube.quote", lambda: client.cube.quote(cube_symbol), sleep_s=sleep_s)
        else:
            print(
                "\n[SKIP] cube.*: set `XUEQIU_CUBE_SYMBOL='ZHxxxxxxx'` "
                "to smoke test cube endpoints."
            )


if __name__ == "__main__":
    main()
