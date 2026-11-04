"""Replay transport: serve recorded responses instead of calling the upstream.

Layout: ``DIR/<endpoint name>/<any name>.json``, each file holding::

    {"params": {"symbol": "SH600000", "period": "day"}, "status": 200, "body": {...}}

A request is routed to an endpoint by host and path, then to the first file whose ``params``
all equal the upstream query values (and path template values); files with more params are
tried first, then by file name. ``status`` defaults to 200. The cookie bootstrap gets a
synthetic anonymous cookie set; anything else gets HTTP 404.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx2

from .endpoints import REGISTRY

_HOME_HOSTS = frozenset({"xueqiu.com", "www.xueqiu.com"})
_REPLAY_COOKIES = (
    "xq_a_token=replay-anonymous; Domain=.xueqiu.com; Path=/",
    "u=replay-anonymous; Domain=.xueqiu.com; Path=/",
)


@dataclass(frozen=True)
class _Case:
    file: str
    params: dict[str, str]
    status: int
    body: Any


class ReplayTransport(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        root = Path(directory)
        self._routes = [
            (spec.name, spec.host, path_pattern(spec.path)) for spec in REGISTRY.values()
        ]
        self._cases: dict[str, list[_Case]] = {}
        for file in sorted(root.glob("*/*.json")):
            name = file.parent.name
            if name not in REGISTRY:
                raise ValueError(f"replay fixture {file} is for unknown endpoint {name!r}")
            self._cases.setdefault(name, []).append(_load_case(file))
        for cases in self._cases.values():
            cases.sort(key=lambda case: (-len(case.params), case.file))

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        return self._respond(request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return self._respond(request)

    def _respond(self, request: httpx2.Request) -> httpx2.Response:
        host, path = request.url.host, request.url.path
        if host in _HOME_HOSTS and path == "/":
            headers = [("Content-Type", "text/html")]
            headers += [("Set-Cookie", cookie) for cookie in _REPLAY_COOKIES]
            return httpx2.Response(200, headers=headers, text="<!doctype html><html></html>")
        for name, route_host, pattern in self._routes:
            match = pattern.fullmatch(path) if host == route_host else None
            if match is None:
                continue
            values = {**dict(request.url.params), **match.groupdict()}
            for case in self._cases.get(name, []):
                if all(values.get(key) == value for key, value in case.params.items()):
                    return httpx2.Response(case.status, json=case.body)
            return httpx2.Response(404, text=f"no replay fixture for {name}")
        return httpx2.Response(404, text="no replay route")


def path_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"\{(\w+)\}", template)
    pattern = "".join(
        re.escape(part) if index % 2 == 0 else f"(?P<{part}>[^/]+)"
        for index, part in enumerate(parts)
    )
    return re.compile(pattern)


def _load_case(file: Path) -> _Case:
    try:
        data: Any = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"replay fixture {file} is not UTF-8 JSON: {exc}") from None
    if not isinstance(data, dict) or "body" not in data:
        raise ValueError(f"replay fixture {file} needs a body")
    fields = cast(dict[str, Any], data)
    params: Any = fields.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"replay fixture {file} has params that are not an object")
    try:
        status = int(fields.get("status", 200))
    except (TypeError, ValueError):
        raise ValueError(f"replay fixture {file} has a status that is not an integer") from None
    return _Case(
        file=file.name,
        params={str(key): str(value) for key, value in cast(dict[Any, Any], params).items()},
        status=status,
        body=fields["body"],
    )
