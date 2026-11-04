"""Endpoint registry.

Every module in this package defines one ``Endpoint`` named ``ENDPOINT``. Modules are
discovered at import time, so adding an endpoint means adding a module here.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

from ..registry import Endpoint


def _discover() -> dict[str, Endpoint[Any, Any]]:
    found: dict[str, Endpoint[Any, Any]] = {}
    for module_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        endpoint: object = getattr(module, "ENDPOINT", None)
        if not isinstance(endpoint, Endpoint):
            raise TypeError(f"{module.__name__} must define ENDPOINT as an Endpoint")
        spec = cast(Endpoint[Any, Any], endpoint)
        if spec.name in found:
            raise ValueError(f"duplicate endpoint name {spec.name!r}")
        found[spec.name] = spec
    return dict(sorted(found.items()))


REGISTRY: Mapping[str, Endpoint[Any, Any]] = MappingProxyType(_discover())
"""Every registered endpoint by name, in name order."""
