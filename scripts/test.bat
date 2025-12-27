@echo off
setlocal

if "%UV_CACHE_DIR%"=="" set UV_CACHE_DIR=.uv-cache

uv run pytest || exit /b 1

