@echo off
setlocal

if "%UV_CACHE_DIR%"=="" set UV_CACHE_DIR=.uv-cache

uv sync --group dev || exit /b 1

