"""Python client for Xueqiu (雪球) and Danjuan market data."""

from importlib.metadata import version

from ._async import AsyncClient
from ._sync import Client
from .endpoints import REGISTRY
from .endpoints.kline import Adjust, Kline, KlineParams, Period
from .endpoints.quotec import Quote, QuotecParams
from .errors import (
    ERROR_TABLE,
    AuthExpired,
    Blocked,
    DisabledByConfig,
    ErrorCode,
    ErrorInfo,
    InternalError,
    InvalidParams,
    LoginRequired,
    RateLimited,
    Recoverable,
    SymbolNotFound,
    UnknownEndpoint,
    UpstreamChanged,
    UpstreamError,
    UpstreamUnavailable,
    XueqiuError,
)
from .registry import AgentInfo, Auth, Endpoint, Freshness
from .symbols import Market, Symbol, normalize_symbol

__version__ = version("xueqiu-api")

__all__ = [
    "ERROR_TABLE",
    "REGISTRY",
    "Adjust",
    "AgentInfo",
    "AsyncClient",
    "Auth",
    "AuthExpired",
    "Blocked",
    "Client",
    "DisabledByConfig",
    "Endpoint",
    "ErrorCode",
    "ErrorInfo",
    "Freshness",
    "InternalError",
    "InvalidParams",
    "Kline",
    "KlineParams",
    "LoginRequired",
    "Market",
    "Period",
    "Quote",
    "QuotecParams",
    "RateLimited",
    "Recoverable",
    "Symbol",
    "SymbolNotFound",
    "UnknownEndpoint",
    "UpstreamChanged",
    "UpstreamError",
    "UpstreamUnavailable",
    "XueqiuError",
    "__version__",
    "normalize_symbol",
]
