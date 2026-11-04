"""Request profiles of the upstream hosts, kept in one place.

Headers, intervals and error codes were measured in the phase 1 survey on 2026-11-04.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Host = Literal["stock.xueqiu.com", "www.xueqiu.com", "danjuanfunds.com"]

APP_HEADERS: Mapping[str, str] = MappingProxyType(
    {"User-Agent": "Xueqiu iPhone 14.15.1", "Accept": "application/json"}
)
BROWSER_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    }
)


@dataclass(frozen=True, kw_only=True)
class Site:
    host: Host
    headers: Mapping[str, str]
    cookies: bool
    """Whether requests carry the anonymous cookie set or the configured credentials."""
    min_interval_s: float
    """Minimum time between request starts; callers can only make it longer."""
    login_codes: frozenset[str]
    """Upstream error codes meaning the session is missing or not logged in."""
    rate_limit_codes: frozenset[str] = frozenset()


SITES: Mapping[Host, Site] = MappingProxyType(
    {
        "stock.xueqiu.com": Site(
            host="stock.xueqiu.com",
            headers=APP_HEADERS,
            cookies=True,
            min_interval_s=1.0,
            login_codes=frozenset({"60201"}),
        ),
        # The apex xueqiu.com 301-redirects API paths here, so requests go to www directly.
        "www.xueqiu.com": Site(
            host="www.xueqiu.com",
            headers=BROWSER_HEADERS,
            cookies=True,
            min_interval_s=3.0,
            login_codes=frozenset({"10022", "21101", "400016"}),
            rate_limit_codes=frozenset({"31005"}),
        ),
        "danjuanfunds.com": Site(
            host="danjuanfunds.com",
            headers=BROWSER_HEADERS,
            cookies=False,
            min_interval_s=1.0,
            login_codes=frozenset({"300001"}),
        ),
    }
)

HOME_URL = "https://xueqiu.com/"
"""Anonymous cookie bootstrap: the apex home page, which 301-redirects to www.xueqiu.com."""
HOME_HOST: Host = "www.xueqiu.com"
"""Site whose limiter and circuit breaker the bootstrap request uses."""
