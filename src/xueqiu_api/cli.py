"""Command line entry point ``xueqiu``. For now it prints the package version."""

from __future__ import annotations

from . import __version__


def main() -> int:
    print(__version__)
    return 0
