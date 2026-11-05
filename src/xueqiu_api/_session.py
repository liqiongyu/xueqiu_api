"""Session state: the configured credentials, the anonymous cookie set and its cache file."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ._core import COOKIE_NAME, COOKIE_VALUE, Call, log, usable_cookie

TOKEN_ENV = "XUEQIU_TOKEN"
_VERSION = 1


def parse_credentials(token: str) -> dict[str, str]:
    """Parse a bare ``xq_a_token`` value or a ``name=value; name=value`` cookie string.

    Values containing ``=`` are read as cookie strings. Error messages never echo the input;
    they name the cookie whose value is not valid.
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
        if not COOKIE_NAME.fullmatch(name):
            raise ValueError("a cookie name contains characters that are not valid in a cookie")
        if not COOKIE_VALUE.fullmatch(value):
            raise ValueError(
                f"the value of the {name} cookie contains characters that are not valid"
            )
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
        if not all(
            isinstance(k, str) and isinstance(v, str) and usable_cookie(k, v) for k, v in items
        ):
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

    Two independent sets: the configured credentials, never written to disk, and the anonymous
    set, loaded from the store or fetched by the client's bootstrap and saved there. Each call
    says which one it uses. ``generation`` changes whenever the anonymous set is replaced.
    """

    def __init__(self, *, credentials: dict[str, str] | None, store: SessionStore) -> None:
        self._credentials = credentials
        self._store = store
        self._anonymous = store.load()
        self.generation = 0

    @property
    def has_credentials(self) -> bool:
        return self._credentials is not None

    def ready(self, call: Call) -> bool:
        """Whether the set this call uses is there and holds the cookies the call needs."""
        cookies = self._cookies(call.credentials)
        return cookies is not None and all(name in cookies for name in call.needs_cookies)

    @property
    def kind(self) -> str:
        return "token" if self._credentials is not None else "anonymous"

    def cookie_header(self, credentials: bool) -> str | None:
        cookies = self._cookies(credentials)
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

    def _cookies(self, credentials: bool) -> dict[str, str] | None:
        return self._credentials if credentials else self._anonymous

    def __repr__(self) -> str:
        return f"Session(kind={self.kind!r})"
