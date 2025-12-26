# Releasing

This repository ships:

- GitHub releases (assets under `dist/`) via `.github/workflows/release.yml`
- PyPI releases (manual for now)

## Prerequisites

- `uv` installed
- A clean working tree

If your environment restricts access to `$HOME/.cache`, set:

```bash
export UV_CACHE_DIR=.uv-cache
```

## Local checks

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

## Create a GitHub release (automated)

The `Release` workflow runs on tag pushes that match `v*.*.*` and will:

1. Run lint + tests
2. Build `dist/*` via `uv build`
3. Verify the tag version matches `pyproject.toml` and extract release notes from `CHANGELOG.md`
4. Create a GitHub release with the artifacts attached

To cut a new release:

1. Update `pyproject.toml` version
2. Add a `CHANGELOG.md` entry for that version
3. Commit the changes
4. Create and push a tag, e.g.:

```bash
git tag v0.1.1
git push origin main --tags
```

## Publish to PyPI (manual)

When you're ready to publish to PyPI, set a token and upload:

```bash
export UV_PUBLISH_TOKEN="pypi-***"
uv build
uv publish
```

Notes:

- Consider switching to Trusted Publishing later (so CI can publish without storing long-lived tokens).
- Ensure the PyPI project name matches `xueqiu_api` (PyPI normalizes underscores/hyphens).
