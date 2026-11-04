"""Fixture hygiene: small, sanitized, no credentials."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from .harness import FIXTURES

FILES = sorted(path for path in FIXTURES.rglob("*") if path.is_file())
SECRET_KEY = re.compile(r"token|cookie|passw|secret|^session$|session_?id|auth", re.IGNORECASE)
SECRET_TEXT = re.compile(r"xq_a_token=|xqat=|set-cookie", re.IGNORECASE)


def walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    """Yield (key, value) for every nested value; key is None inside lists."""
    if isinstance(value, dict):
        for key, item in cast(dict[str, Any], value).items():
            yield key, item
            yield from walk(item)
    elif isinstance(value, list):
        for item in cast(list[Any], value):
            yield None, item
            yield from walk(item)


def test_fixtures_exist() -> None:
    assert any(path.suffix == ".json" for path in FILES)
    assert (FIXTURES / "README.md").is_file()


@pytest.mark.parametrize("path", FILES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_fixture_is_small_and_sanitized(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert len(text) < 20_000
    assert not SECRET_TEXT.search(text)
    if path.suffix != ".json":
        return
    for key, value in walk(json.loads(text)):
        if key is not None:
            assert not SECRET_KEY.search(key), key
        if isinstance(value, list):
            assert len(cast(list[Any], value)) <= 10, key
        if isinstance(value, str):
            assert len(value) <= 40, value
