# Contributing

Thanks for your interest in improving `xueqiu_api`!

## Development setup

This repo uses `uv` and keeps the virtualenv in `.venv/`.

```bash
uv sync --group dev
```

If your environment restricts access to `$HOME/.cache`, set:

```bash
export UV_CACHE_DIR=.uv-cache
```

## Running checks

Run the full local validation:

```bash
bash scripts/validate.sh
```

Or run them individually:

```bash
bash scripts/lint.sh
bash scripts/test.sh
bash scripts/format.sh
```

## Adding/Updating endpoints

- Keep breaking changes low; prefer additive changes and compatibility helpers.
- Response/data models should be tolerant to upstream schema changes:
  - default to `extra="allow"`
  - keep most fields `Optional` unless clearly stable
  - keep a raw JSON escape hatch available via `client.request_json(...)`
- All network I/O should go through the client layer (`XueqiuClient` / `AsyncXueqiuClient`).
- Tests should mock HTTP (e.g. `respx`). Avoid tests that require real cookies/tokens.

## Submitting a PR

- Keep PRs focused (one logical change).
- Add/adjust tests where it makes sense.
- Update docs/examples (`README.md`, `cookbook/`, `examples/`) when user-facing behavior changes.
- Please do **not** include real cookies/tokens in code, logs, or test fixtures.

