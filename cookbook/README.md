# Cookbook

Small, runnable recipes for `xueqiu_api` (`import xueqiu`).

> Notes
> - These are unofficial endpoints and may change without notice.
> - Please be gentle: Xueqiu may rate-limit or block aggressive scraping.

## Setup

```bash
export XUEQIU_TOKEN='xq_a_token=...; u=...'
uv sync --group dev
```

Run a recipe:

```bash
uv run python cookbook/01_quickstart_sync.py
```

## Recipes

- `01_quickstart_sync.py`: sync client basics + auth-required endpoint
- `02_quickstart_async.py`: async client basics
- `03_raw_json_escape_hatch.py`: use `client.request_json(...)` for raw payloads
- `04_csindex_no_auth.py`: CSIndex endpoints (no Xueqiu auth required)

