from __future__ import annotations

__all__ = [
    "__version__",
    "AsyncXueqiuClient",
    "XueqiuAPIError",
    "XueqiuAuthError",
    "XueqiuClient",
    "XueqiuDecodeError",
    "XueqiuError",
    "XueqiuHTTPError",
    "XueqiuResponse",
]

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:  # pragma: no cover
    __version__ = "0.0.0"
else:
    try:
        __version__ = version("xueqiu_api")
    except PackageNotFoundError:  # pragma: no cover
        __version__ = "0.0.0"

from xueqiu.client import AsyncXueqiuClient, XueqiuClient  # noqa: E402
from xueqiu.errors import (  # noqa: E402
    XueqiuAPIError,
    XueqiuAuthError,
    XueqiuDecodeError,
    XueqiuError,
    XueqiuHTTPError,
)
from xueqiu.models import XueqiuResponse  # noqa: E402
