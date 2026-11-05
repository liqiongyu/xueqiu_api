"""Python client for Xueqiu (雪球) and Danjuan market data.

Result models and the Literal types of parameters are in ``xueqiu_api.models``.
"""

from importlib.metadata import version

from ._async import AsyncClient
from ._pages import Page
from ._sync import Client
from .endpoints import REGISTRY
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
    "LoginRequired",
    "Market",
    "Page",
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
