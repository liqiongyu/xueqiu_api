@echo off
setlocal

if "%UV_CACHE_DIR%"=="" set UV_CACHE_DIR=.uv-cache

uv sync --frozen --group dev || exit /b 1
uv run --frozen ruff format --check . || exit /b 1
uv run --frozen ruff check . || exit /b 1
uv run --frozen pytest || exit /b 1

