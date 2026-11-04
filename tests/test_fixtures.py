"""Fixture hygiene: small, sanitized, no credentials, headers or user data."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from .harness import ANON_COOKIES, FIXTURES

FILES = sorted(path for path in FIXTURES.rglob("*") if path.is_file())
SECRET_KEY = re.compile(r"token|cookie|passw|secret|^session$|session_?id|auth", re.IGNORECASE)
USER_KEY = re.compile(
    r"^(users?|uid|user_?id|screen_name|nick_?name|avatar\w*|profile_image\w*|comments?)$",
    re.IGNORECASE,
)
COOKIE_NAMES = "|".join(re.escape(name) for name in ANON_COOKIES)
SECRET_TEXT = re.compile(rf"(?<![\w.-])({COOKIE_NAMES})=|\bcookie:|set-cookie", re.IGNORECASE)
SECRET_VALUE = re.compile(r"[0-9a-f]{32,}|eyJ[\w-]+\.[\w-]+", re.IGNORECASE)
"""Long hex strings and JWTs, the shapes of tokens and request ids."""
REPLAY_KEYS = {"params", "status", "body"}


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


def check(name: str, text: str) -> None:
    """Fail on anything a fixture must not carry; ``name`` is the path under the fixtures."""
    assert len(text) < 20_000
    assert not SECRET_TEXT.search(text)
    assert not SECRET_VALUE.search(text)
    if not name.endswith(".json"):
        return
    data = json.loads(text)
    if name.startswith("replay/"):
        assert isinstance(data, dict) and cast(dict[str, Any], data).keys() <= REPLAY_KEYS
    for key, value in walk(data):
        if key is not None:
            assert not SECRET_KEY.search(key), key
            assert not USER_KEY.search(key), key
        if isinstance(value, list):
            assert len(cast(list[Any], value)) <= 10, key
        if isinstance(value, str):
            assert len(value) <= 40, value


def test_fixtures_exist() -> None:
    assert any(path.suffix == ".json" for path in FILES)
    assert (FIXTURES / "README.md").is_file()


@pytest.mark.parametrize("path", FILES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_fixture_is_small_and_sanitized(path: Path) -> None:
    check(path.relative_to(FIXTURES).as_posix(), path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("replay/quotec/probe.json", '{"body": {}, "headers": {"server": "openresty"}}'),
        ("replay/quotec/probe.json", '{"body": {"user": {"id": 1}}}'),
        ("replay/quotec/probe.json", '{"body": {"screen_name": "someone"}}'),
        ("replay/quotec/probe.json", '{"body": {"query_id": "' + "0123456789abcdef" * 2 + '"}}'),
        ("page.html", "Cookie: something"),
        ("page.html", "xq_r_token=fake"),
        ("page.html", "<p>u=fake; acw_tc=fake</p>"),
        ("page.html", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0"),
    ],
)
def test_the_scan_rejects_headers_user_data_and_token_shapes(name: str, text: str) -> None:
    with pytest.raises(AssertionError):
        check(name, text)
