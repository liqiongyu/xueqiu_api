"""Session state: configured credentials or the anonymous cookie set, and its cache file."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ._core import log

TOKEN_ENV = "XUEQIU_TOKEN"
_VERSION = 1
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_VALUE = re.compile(r"[!#-+\--:<-\[\]-~]+")


def parse_credentials(token: str) -> dict[str, str]:
    """Parse a bare ``xq_a_token`` value or a ``name=value; name=value`` cookie string.

    Values containing ``=`` are read as cookie strings. Error messages never echo the input.
    """
    text = token.strip()
    if not text:
        raise ValueError("the token is empty")
    if "=" not in text:
        pairs = {"xq_a_token": text}
    else:
        pairs: dict[str, str] = {}
        for part in text.split(";"):
            item = part.strip()
            if not item:
                continue
            name, sep, value = item.partition("=")
            if not sep:
                raise ValueError("a cookie string must contain only name=value pairs")
            pairs[name.strip()] = value.strip()
    for name, value in pairs.items():
        if not _COOKIE_NAME.fullmatch(name) or not _COOKIE_VALUE.fullmatch(value):
            raise ValueError("the token contains characters that are not valid in a cookie")
    return pairs


def default_session_path() -> Path:
    """``session.json`` in the per-user cache directory of ``xueqiu-api``."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "xueqiu-api" / "Cache" / "session.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "xueqiu-api" / "session.json"
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "xueqiu-api" / "session.json"


class SessionStore:
    """Anonymous cookies persisted as JSON, written atomically with mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str] | None:
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or cast(dict[str, Any], data).get("version") != _VERSION:
            return None
        cookies: Any = cast(dict[str, Any], data).get("cookies")
        if not isinstance(cookies, dict) or "xq_a_token" not in cookies:
            return None
        items = cast(dict[Any, Any], cookies).items()
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in items):
            return None
        return cast(dict[str, str], cookies)

    def save(self, cookies: Mapping[str, str]) -> None:
        payload = {"version": _VERSION, "saved_at": time.time(), "cookies": dict(cookies)}
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


class Session:
    """Cookies one client sends to the Xueqiu hosts.

    With credentials, those are sent and never written to disk. Without, the anonymous cookie
    set is loaded from the store, or fetched by the client's bootstrap and saved there.
    ``generation`` changes whenever the anonymous set is replaced.
    """

    def __init__(self, *, credentials: dict[str, str] | None, store: SessionStore) -> None:
        self._credentials = credentials
        self._store = store
        self._anonymous = None if credentials is not None else store.load()
        self.generation = 0

    @property
    def has_credentials(self) -> bool:
        return self._credentials is not None

    @property
    def ready(self) -> bool:
        return self._credentials is not None or self._anonymous is not None

    @property
    def kind(self) -> str:
        return "token" if self._credentials is not None else "anonymous"

    def cookie_header(self) -> str | None:
        cookies = self._credentials if self._credentials is not None else self._anonymous
        if not cookies:
            return None
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def secrets(self) -> list[str]:
        """Cookie values long enough to be worth scrubbing from text."""
        values = [*(self._credentials or {}).values(), *(self._anonymous or {}).values()]
        return [value for value in values if len(value) >= 6]

    def set_anonymous(self, cookies: Mapping[str, str]) -> None:
        self._anonymous = dict(cookies)
        self.generation += 1
        try:
            self._store.save(cookies)
        except OSError as exc:
            log.warning("could not write the session cache %s: %s", self._store.path, exc.strerror)

    def __repr__(self) -> str:
        return f"Session(kind={self.kind!r})"
