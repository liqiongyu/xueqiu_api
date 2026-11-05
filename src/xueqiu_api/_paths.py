"""Per-user directories of ``xueqiu-api``, following each platform's conventions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP = "xueqiu-api"


def cache_dir() -> Path:
    """Session cookies, shared limiter state and cached responses."""
    if sys.platform == "win32":
        return _local_app_data() / _APP / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _APP
    return _xdg("XDG_CACHE_HOME", ".cache") / _APP


def data_dir() -> Path:
    """The local warehouse."""
    if sys.platform == "win32":
        return _local_app_data() / _APP / "Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP
    return _xdg("XDG_DATA_HOME", ".local", "share") / _APP


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _xdg(variable: str, *fallback: str) -> Path:
    """The XDG base directory in ``variable`` when it is absolute, else ``~/<fallback>``."""
    value = os.environ.get(variable, "")
    return Path(value) if os.path.isabs(value) else Path.home().joinpath(*fallback)
