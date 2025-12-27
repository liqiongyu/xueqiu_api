@echo off
setlocal

if "%UV_CACHE_DIR%"=="" set UV_CACHE_DIR=.uv-cache

uv run ruff check . || exit /b 1

