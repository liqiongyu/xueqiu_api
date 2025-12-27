#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

uv sync --frozen --group dev
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pytest
