from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    # Ensure tests import the in-repo `src/` code (not an old installed wheel).
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    sys.path.insert(0, str(src))

