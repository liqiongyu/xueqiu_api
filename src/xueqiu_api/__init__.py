"""Python client for Xueqiu (雪球) and Danjuan market data."""

from importlib.metadata import version

from ._async import AsyncClient
from ._finance import FinanceParams, Financials, LineItem, ReportPeriod, ReportType
from ._sync import Client
from .endpoints import REGISTRY
from .endpoints.batch_quote import BatchQuoteParams
from .endpoints.f10_company import Company
from .endpoints.kline import Adjust, Kline, KlineParams, Period
from .endpoints.quote_detail import QuoteDetail
from .endpoints.quotec import Quote, QuotecParams
from .endpoints.suggest_stock import StockSuggestion, SuggestStockParams
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
from .symbols import Market, Symbol, SymbolParams, normalize_symbol

__version__ = version("xueqiu-api")

__all__ = [
    "ERROR_TABLE",
    "REGISTRY",
    "Adjust",
    "AgentInfo",
    "AsyncClient",
    "Auth",
    "AuthExpired",
    "BatchQuoteParams",
    "Blocked",
    "Client",
    "Company",
    "DisabledByConfig",
    "Endpoint",
    "ErrorCode",
    "ErrorInfo",
    "FinanceParams",
    "Financials",
    "Freshness",
    "InternalError",
    "InvalidParams",
    "Kline",
    "KlineParams",
    "LineItem",
    "LoginRequired",
    "Market",
    "Period",
    "Quote",
    "QuoteDetail",
    "QuotecParams",
    "RateLimited",
    "Recoverable",
    "ReportPeriod",
    "ReportType",
    "StockSuggestion",
    "SuggestStockParams",
    "Symbol",
    "SymbolNotFound",
    "SymbolParams",
    "UnknownEndpoint",
    "UpstreamChanged",
    "UpstreamError",
    "UpstreamUnavailable",
    "XueqiuError",
    "__version__",
    "normalize_symbol",
]
