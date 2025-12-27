from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

JsonDict = dict[str, Any]


class SyncRequester(Protocol):
    def request_model(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
        model: Any,
    ) -> Any: ...


class AsyncRequester(Protocol):
    async def request_model(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
        model: Any,
    ) -> Any: ...
