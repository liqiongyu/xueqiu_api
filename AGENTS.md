# AGENTS.md

## Project goals

- Provide a modern Xueqiu (雪球) SDK: **sync + async**, **Pydantic-first**, ergonomic errors.
- Keep breaking changes low: prefer additive changes, add compatibility helpers when helpful.

## Tooling

- Use `uv` for dependency management and running tools:
  - `uv sync --group dev`
  - `uv run pytest`
  - `uv run ruff check .`
- Virtualenv lives in `.venv/` (gitignored).
- If your environment restricts access to `$HOME/.cache`, set `UV_CACHE_DIR=.uv-cache`.

## Code conventions

- Source code under `src/` (src-layout).
- Prefer Pydantic v2 models; default to being tolerant to upstream schema changes:
  - `extra="allow"` on response/data models
  - keep most fields `Optional` unless truly stable
  - always keep a raw JSON escape hatch on the client
- All network IO goes through the client layer; tests should mock HTTP (e.g. `respx`).
