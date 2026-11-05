"""Session state: the configured credentials, the anonymous cookie set and its cache file."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
import secrets
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ._core import COOKIE_NAME, COOKIE_VALUE, Call, log, usable_cookie
from ._paths import cache_dir

TOKEN_ENV = "XUEQIU_TOKEN"
_VERSION = 1
_SALT = secrets.token_bytes(32)
"""Key of the identity HMAC, made for this process and never stored."""


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
    return cache_dir() / "session.json"


@dataclass(frozen=True)
class Saved:
    """An anonymous cookie set read from the store."""

    cookies: dict[str, str]
    saved_at: float
    """Seconds since the epoch when it was written; 0.0 when the file does not say."""


class SessionStore:
    """Anonymous cookies persisted as JSON, written atomically with mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Saved | None:
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or cast(dict[str, Any], data).get("version") != _VERSION:
            return None
        cookies: Any = cast(dict[str, Any], data).get("cookies")
        if not isinstance(cookies, dict) or not cast(dict[Any, Any], cookies).get("xq_a_token"):
            return None
        items = cast(dict[Any, Any], cookies).items()
        if not all(
            isinstance(k, str) and isinstance(v, str) and usable_cookie(k, v) for k, v in items
        ):
            return None
        stamp: Any = cast(dict[str, Any], data).get("saved_at")
        known = isinstance(stamp, int | float) and not isinstance(stamp, bool)
        return Saved(
            cast(dict[str, str], cookies), float(stamp) if known and math.isfinite(stamp) else 0.0
        )

    def save(self, cookies: Mapping[str, str], saved_at: float) -> None:
        payload = {"version": _VERSION, "saved_at": saved_at, "cookies": dict(cookies)}
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
        saved = store.load()
        self._anonymous = None if saved is None else saved.cookies
        self._saved_at = 0.0 if saved is None else saved.saved_at
        self.generation = 0

    @property
    def has_credentials(self) -> bool:
        return self._credentials is not None

    def ready(self, call: Call) -> bool:
        """Whether the set this call uses is there and holds a value for each cookie it needs."""
        cookies = self._cookies(call.credentials)
        return cookies is not None and all(cookies.get(name) for name in call.needs_cookies)

    @property
    def kind(self) -> str:
        return "token" if self._credentials is not None else "anonymous"

    def cookie_header(self, credentials: bool) -> str | None:
        cookies = self._cookies(credentials)
        if not cookies:
            return None
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def identity(self, credentials: bool) -> str:
        """Who a call is sent as, for cache keys: ``anonymous``, or for the configured
        credentials the first 32 hex digits of an HMAC of their cookie string."""
        cookies = self.cookie_header(credentials) if credentials else None
        if cookies is None:
            return "anonymous"
        return hmac.new(_SALT, cookies.encode(), hashlib.sha256).hexdigest()[:32]

    def secrets(self) -> list[str]:
        """Cookie values long enough to be worth scrubbing from text."""
        values = [*(self._credentials or {}).values(), *(self._anonymous or {}).values()]
        return [value for value in values if len(value) >= 6]

    def adopt_saved(self) -> bool:
        """Take the anonymous set in the store when it was saved after the one held here.

        Compared by ``saved_at``, so that when this session could not write its own set, the
        older one the store still holds does not come back.
        """
        saved = self._store.load()
        if saved is None or saved.saved_at <= self._saved_at:
            return False
        self._anonymous, self._saved_at = saved.cookies, saved.saved_at
        self.generation += 1
        return True

    def set_anonymous(self, cookies: Mapping[str, str]) -> None:
        self._anonymous = dict(cookies)
        self._saved_at = time.time()
        self.generation += 1
        try:
            self._store.save(cookies, self._saved_at)
        except OSError as exc:
            log.warning("could not write the session cache %s: %s", self._store.path, exc.strerror)

    def _cookies(self, credentials: bool) -> dict[str, str] | None:
        return self._credentials if credentials else self._anonymous

    def __repr__(self) -> str:
        return f"Session(kind={self.kind!r})"
