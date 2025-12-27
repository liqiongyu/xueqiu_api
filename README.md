# xueqiu_api

Modern Xueqiu (雪球) API client with Pydantic models (**sync + async**).

> Notes
> - These are unofficial endpoints and may change without notice.
> - You must provide your own Xueqiu cookie (`xq_a_token`, `u`, etc.).

Links:
- GitHub: https://github.com/liqiongyu/xueqiu_api
- Issues: https://github.com/liqiongyu/xueqiu_api/issues
- Changelog: https://github.com/liqiongyu/xueqiu_api/blob/main/CHANGELOG.md

## Install

```bash
pip install xueqiu_api
```

## Import path (important)

This project publishes to PyPI as `xueqiu_api`.

- Primary import: `import xueqiu`
- Alternative import (avoid conflicts): `import xueqiu_api`

`xueqiu` is a common module name and may conflict with other packages. If you hit import conflicts,
use `xueqiu_api` explicitly.

## Quickstart (sync)

```python
from xueqiu import XueqiuClient

client = XueqiuClient(cookie="xq_a_token=...; u=...")  # copy from your browser

resp = client.realtime.quotec(["SZ002027", "SH600000"])
print(resp.data)

finance = client.finance.cash_flow_v2("SH600000", count=5)
print(finance.data)
```

## Quickstart (async)

```python
import asyncio
from xueqiu import AsyncXueqiuClient


async def main() -> None:
    client = AsyncXueqiuClient(cookie="xq_a_token=...; u=...")
    async with client:
        resp = await client.realtime.quotec("SZ002027")
        print(resp.data)


asyncio.run(main())
```

## Auth / Cookie

You can provide auth in a few ways:

- `cookie="xq_a_token=...; u=..."` (simplest; matches browser "Cookie" header)
- `cookies={"xq_a_token": "...", "u": "..."}` (structured; passed to `httpx`)
- env var: `XUEQIU_TOKEN` (recommended)
- env var: `XUEQIU_COOKIE` (fallback)

The client will automatically read `XUEQIU_TOKEN` / `XUEQIU_COOKIE` when no cookie is provided.

Other optional env vars:

- `XUEQIU_BASE_URL` (default: `https://stock.xueqiu.com`)
- `XUEQIU_TIMEOUT` (seconds, default: `10`)
- `XUEQIU_MAX_RETRIES` (default: `2`)
- `XUEQIU_USER_AGENT` (override UA)
- `XUEQIU_DEBUG=1` (enable debug logs)

If you prefer an explicit entrypoint, use `XueqiuClient.from_env()` / `AsyncXueqiuClient.from_env()`.

Some endpoints work without auth, but most `stock.xueqiu.com` endpoints require it.

Smoke test with real data:

```bash
export XUEQIU_TOKEN='xq_a_token=...; u=...'
uv run python examples/fetch_symbols.py
```

Full smoke test (calls all implemented endpoints; may be slow / rate-limited):

```bash
export XUEQIU_TOKEN='xq_a_token=...; u=...'
export XUEQIU_SLEEP_SECONDS=0.2  # optional, be gentle
uv run python examples/smoke_all.py
```

## Cookbook

More runnable recipes live in `cookbook/`:

```bash
export XUEQIU_TOKEN='xq_a_token=...; u=...'
uv run python cookbook/01_quickstart_sync.py
```

## Pydantic stability (fields may change)

Xueqiu endpoints are unofficial and may change response fields. This SDK tries to be resilient:

- Response/data models default to `extra="allow"` (new fields won't break parsing).
- Most fields are optional unless they are clearly stable (e.g. `symbol`).
- You always have a raw JSON escape hatch via `client.request_json(...)`.

## API coverage (Xueqiu only)

This project covers the Xueqiu endpoints from the legacy `pysnowball` project:

- `client.realtime`: `quotec`, `quote_detail`, `pankou`, `kline`
- `client.finance`: `cash_flow/indicator/balance/income/business` (+ `_v2` variants)
- `client.report`: `latest`, `earning_forecast`
- `client.capital`: `margin`, `blocktrans`, `assort`, `flow`, `history`
- `client.f10`: `skholderchg`, `skholder`, `industry`, `holders`, `bonus`, `org_holding_change`, `industry_compare`,
  `business_analysis`, `shareschg`, `top_holders`, `indicator`
- `client.portfolio`: `list`, `stocks`
- `client.cube`: `nav_daily`, `rebalancing_history`, `rebalancing_current`, `quote`
- `client.suggest`: `stock`

## Extras

- `client.csindex`: China Securities Index (中证指数) endpoints (no auth)
- `client.danjuan`: Danjuan (蛋卷基金) fund endpoints (no Xueqiu auth)
- `client.eastmoney`: Eastmoney datacenter endpoints (no Xueqiu auth)

## Credits

API coverage and endpoint shapes are based on the legacy `pysnowball` project (MIT licensed):
https://github.com/uname-yang/pysnowball

## Development

This repo uses `uv` and creates the virtualenv in `.venv/`.

If your environment restricts access to `$HOME/.cache`, set `UV_CACHE_DIR=.uv-cache`.

```bash
uv venv -p python3.10
uv sync --group dev
uv run pytest
uv run ruff check .
```

Convenience scripts:

```bash
bash scripts/dev_setup.sh
bash scripts/format.sh
bash scripts/validate.sh
```

Windows (cmd.exe):

```bat
scripts\\dev_setup.bat
scripts\\format.bat
scripts\\validate.bat
```

Releasing: see `RELEASING.md`.
