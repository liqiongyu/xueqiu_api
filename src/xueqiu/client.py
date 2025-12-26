from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter

from xueqiu.errors import XueqiuAPIError, XueqiuAuthError, XueqiuDecodeError, XueqiuHTTPError

DEFAULT_STOCK_BASE_URL = "https://stock.xueqiu.com"
DEFAULT_MAIN_BASE_URL = "https://xueqiu.com"

ModelT = TypeVar("ModelT")


def _env_cookie() -> str | None:
    return os.environ.get("XUEQIU_TOKEN") or os.environ.get("XUEQIU_COOKIE")


def _clean_cookie(cookie: str | None) -> str | None:
    if cookie is None:
        return None
    cookie = cookie.strip()
    return cookie or None


def _default_headers(*, cookie: str | None, user_agent: str | None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/json",
        # Use a "realistic enough" UA; allow overriding via constructor.
        "User-Agent": user_agent or "Mozilla/5.0 (XueqiuAPI; +https://pypi.org/project/xueqiu-api/)",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _raise_for_api_error(payload: Any, *, url: str) -> None:
    # Be defensive: only check when payload is the common envelope shape.
    if not isinstance(payload, dict):
        return

    # Style A: {"error_code": 0, "error_description": "...", "data": ...}
    if "error_code" in payload:
        try:
            error_code = int(payload.get("error_code") or 0)
        except Exception:
            return
        if error_code == 0:
            return
        raise XueqiuAPIError(
            error_code=error_code,
            error_description=payload.get("error_description"),
            url=url,
            payload=payload,
        )

    # Style B: {"code": 0, "message": "...", "success": true, ...}
    if "success" in payload:
        success = payload.get("success")
        if success is True:
            return
        if success is False:
            try:
                error_code = int(payload.get("code") or 0)
            except Exception:
                error_code = 0
            raise XueqiuAPIError(
                error_code=error_code,
                error_description=payload.get("message"),
                url=url,
                payload=payload,
            )



def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, seconds)


def _backoff_seconds(attempt: int) -> float:
    # attempt: 0,1,2...  -> 0.2, 0.4, 0.8 ... up to 4s
    return min(0.2 * (2**attempt), 4.0)


class XueqiuClient:
    """Synchronous Xueqiu client."""

    def __init__(
        self,
        *,
        cookie: str | None = None,
        cookies: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_STOCK_BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 2,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        cookie = _clean_cookie(cookie or _env_cookie())

        if client is None:
            self._client = httpx.Client(
                base_url=base_url,
                timeout=httpx.Timeout(timeout),
                headers=_default_headers(cookie=cookie, user_agent=user_agent),
                cookies=cookies,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

        self._cookie = cookie
        self._max_retries = max(0, int(max_retries))

        from xueqiu.api.capital import CapitalAPI
        from xueqiu.api.cube import CubeAPI
        from xueqiu.api.f10 import F10API
        from xueqiu.api.finance import FinanceAPI
        from xueqiu.api.portfolio import PortfolioAPI
        from xueqiu.api.realtime import RealtimeAPI
        from xueqiu.api.report import ReportAPI
        from xueqiu.api.suggest import SuggestAPI

        self.capital = CapitalAPI(self)
        self.cube = CubeAPI(self)
        self.f10 = F10API(self)
        self.finance = FinanceAPI(self)
        self.portfolio = PortfolioAPI(self)
        self.realtime = RealtimeAPI(self)
        self.report = ReportAPI(self)
        self.suggest = SuggestAPI(self)

    @property
    def cookie(self) -> str | None:
        return self._cookie

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> XueqiuClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
    ) -> Any:
        if require_auth and not self._cookie:
            raise XueqiuAuthError("This endpoint requires a Xueqiu cookie.")

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.request(method, path, params=params)
                if resp.status_code >= 400:
                    # Retry on 429/5xx, otherwise raise immediately.
                    if resp.status_code == 429 or resp.status_code >= 500:
                        retry_after = _parse_retry_after_seconds(resp.headers.get("Retry-After"))
                        if retry_after is not None:
                            sleep_s = retry_after
                        else:
                            sleep_s = _backoff_seconds(attempt)
                        time.sleep(sleep_s)
                        continue
                    raise XueqiuHTTPError(
                        status_code=resp.status_code,
                        url=str(resp.request.url),
                        response_text=resp.text[:2000] if resp.text else None,
                    )

                try:
                    payload = resp.json()
                except json.JSONDecodeError as e:
                    raise XueqiuDecodeError(url=str(resp.request.url), message=str(e)) from e

                if check_api_error:
                    _raise_for_api_error(payload, url=str(resp.request.url))
                return payload
            except (httpx.TransportError, XueqiuDecodeError) as e:
                last_exc = e
                # Retry only on transport errors; a retry may help transient decode errors.
                if attempt >= self._max_retries:
                    raise
                time.sleep(_backoff_seconds(attempt))

        # Should be unreachable.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("request_json fell through without a response")

    def request_model(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
        model: Any,
    ) -> ModelT:
        payload = self.request_json(
            method,
            path,
            params=params,
            require_auth=require_auth,
            check_api_error=check_api_error,
        )
        return TypeAdapter(model).validate_python(payload)


class AsyncXueqiuClient:
    """Asynchronous Xueqiu client."""

    def __init__(
        self,
        *,
        cookie: str | None = None,
        cookies: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_STOCK_BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 2,
        user_agent: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cookie = _clean_cookie(cookie or _env_cookie())

        if client is None:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(timeout),
                headers=_default_headers(cookie=cookie, user_agent=user_agent),
                cookies=cookies,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

        self._cookie = cookie
        self._max_retries = max(0, int(max_retries))

        from xueqiu.api.capital import AsyncCapitalAPI
        from xueqiu.api.cube import AsyncCubeAPI
        from xueqiu.api.f10 import AsyncF10API
        from xueqiu.api.finance import AsyncFinanceAPI
        from xueqiu.api.portfolio import AsyncPortfolioAPI
        from xueqiu.api.realtime import AsyncRealtimeAPI
        from xueqiu.api.report import AsyncReportAPI
        from xueqiu.api.suggest import AsyncSuggestAPI

        self.capital = AsyncCapitalAPI(self)
        self.cube = AsyncCubeAPI(self)
        self.f10 = AsyncF10API(self)
        self.finance = AsyncFinanceAPI(self)
        self.portfolio = AsyncPortfolioAPI(self)
        self.realtime = AsyncRealtimeAPI(self)
        self.report = AsyncReportAPI(self)
        self.suggest = AsyncSuggestAPI(self)

    @property
    def cookie(self) -> str | None:
        return self._cookie

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncXueqiuClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
    ) -> Any:
        if require_auth and not self._cookie:
            raise XueqiuAuthError("This endpoint requires a Xueqiu cookie.")

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.request(method, path, params=params)
                if resp.status_code >= 400:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        retry_after = _parse_retry_after_seconds(resp.headers.get("Retry-After"))
                        if retry_after is not None:
                            sleep_s = retry_after
                        else:
                            sleep_s = _backoff_seconds(attempt)
                        await _async_sleep(sleep_s)
                        continue
                    raise XueqiuHTTPError(
                        status_code=resp.status_code,
                        url=str(resp.request.url),
                        response_text=(await resp.aread()).decode(errors="replace")[:2000],
                    )

                try:
                    payload = resp.json()
                except json.JSONDecodeError as e:
                    raise XueqiuDecodeError(url=str(resp.request.url), message=str(e)) from e

                if check_api_error:
                    _raise_for_api_error(payload, url=str(resp.request.url))
                return payload
            except (httpx.TransportError, XueqiuDecodeError) as e:
                last_exc = e
                if attempt >= self._max_retries:
                    raise
                await _async_sleep(_backoff_seconds(attempt))

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("request_json fell through without a response")

    async def request_model(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        check_api_error: bool = True,
        model: Any,
    ) -> ModelT:
        payload = await self.request_json(
            method,
            path,
            params=params,
            require_auth=require_auth,
            check_api_error=check_api_error,
        )
        return TypeAdapter(model).validate_python(payload)


async def _async_sleep(seconds: float) -> None:
    # anyio is a dependency of httpx (async), so it's safe to rely on it here.
    import anyio

    await anyio.sleep(seconds)
