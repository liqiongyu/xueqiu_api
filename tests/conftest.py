from __future__ import annotations

import sys
from pathlib import Path

import pytest


def pytest_configure() -> None:
    # Ensure tests import the in-repo `src/` code (not an old installed wheel).
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    sys.path.insert(0, str(src))


@pytest.fixture(autouse=True)
def _clear_xueqiu_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make tests deterministic even if the developer has tokens set.
    monkeypatch.delenv("XUEQIU_TOKEN", raising=False)
    monkeypatch.delenv("XUEQIU_COOKIE", raising=False)
