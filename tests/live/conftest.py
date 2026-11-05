from __future__ import annotations

import re

import pytest

_SELECTED = re.compile(r"(?<!not )\blive\b")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect live tests unless the marker expression asks for them, as in ``-m live``."""
    if _SELECTED.search(config.getoption("markexpr") or ""):
        return
    live = [item for item in items if item.get_closest_marker("live")]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if not item.get_closest_marker("live")]
