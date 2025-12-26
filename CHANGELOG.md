# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- GitHub Actions release workflow for tags.
- `XueqiuClient.from_env()` / `AsyncXueqiuClient.from_env()` for env-based configuration.
- `client.csindex` endpoints (public, no auth).

### Changed

- Safer auth handling: Xueqiu cookies are not sent to non-`*.xueqiu.com` hosts by default.
- Better errors and retries (include HTTP method, avoid retry fall-through).

## [0.1.0] - 2025-12-26

### Added

- First public release.
- Sync + async Xueqiu clients (httpx) with retries and typed, ergonomic errors.
- Pydantic v2 models with tolerant parsing (`extra="allow"`) and a raw JSON escape hatch.
- Initial API coverage: realtime, finance (+ v2 variants), report, capital, f10, portfolio, cube, suggest.
- Examples and a mocked test suite (respx).
