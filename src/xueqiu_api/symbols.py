"""Normalization of user input into Xueqiu symbols.

Accepted forms (case-insensitive, surrounding spaces ignored):

- A shares and A-share indexes: ``SH600519``, ``600519.SH``; Beijing codes look like ``BJ920xxx``.
- Bare 6 digits are stocks: 6, 9 and 5 go to SH; 0, 1, 2 and 3 go to SZ; 4, 8 and 920 go to BJ.
  Indexes need the prefix, because ``000001`` is SZ000001 (Ping An Bank), while the Shanghai
  index is ``SH000001``. So do codes starting with 11 or 201 to 209: bonds, convertible bonds
  and repos, numbered in both exchanges.
- Hong Kong: 5 digits such as ``00700``, or ``0700.HK``; indexes such as ``HKHSI``.
- US: tickers such as ``AAPL`` or ``BRK.B``; indexes such as ``.DJI``.

``HK`` followed by three or more letters is read as a Hong Kong index, so a US ticker of that
shape cannot be expressed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict

from .errors import SymbolNotFound

Market = Literal["CN", "HK", "US"]
Region = Literal["cn", "hk", "us"]

TIMEZONES: Mapping[Market, str] = MappingProxyType(
    {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
)
_REGIONS: Mapping[Market, Region] = MappingProxyType({"CN": "cn", "HK": "hk", "US": "us"})

_A_PREFIXED = re.compile(r"(SH|SZ|BJ)[0-9]{6}")
_A_SUFFIXED = re.compile(r"([0-9]{6})\.(SH|SZ|BJ)")
_A_BARE = re.compile(r"[0-9]{6}")
_HK_STOCK = re.compile(r"[0-9]{5}")
_HK_SUFFIXED = re.compile(r"([0-9]{1,5})\.HK")
_HK_INDEX = re.compile(r"HK[A-Z]{3,}")
_US_INDEX = re.compile(r"\.[A-Z]+")
_US_TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z])?")


@dataclass(frozen=True, slots=True)
class Symbol:
    """A normalized Xueqiu symbol and its market."""

    code: str
    """Symbol as Xueqiu writes it: ``SH600519``, ``00700``, ``HKHSI``, ``AAPL``, ``.DJI``."""
    market: Market

    @property
    def region(self) -> Region:
        """Region segment used in finance and F10 paths: ``cn``, ``hk`` or ``us``."""
        return _REGIONS[self.market]

    @property
    def timezone(self) -> ZoneInfo:
        """Exchange timezone: Asia/Shanghai, Asia/Hong_Kong or America/New_York."""
        return ZoneInfo(TIMEZONES[self.market])

    def __str__(self) -> str:
        return self.code


def normalize_symbol(value: str) -> Symbol:
    """Normalize one symbol; raise ``SymbolNotFound`` when no supported format matches."""
    text = value.strip().upper()
    if _A_PREFIXED.fullmatch(text):
        return Symbol(text, "CN")
    if match := _A_SUFFIXED.fullmatch(text):
        return Symbol(match[2] + match[1], "CN")
    if _A_BARE.fullmatch(text) and (exchange := _bare_exchange(text)):
        return Symbol(exchange + text, "CN")
    if _HK_STOCK.fullmatch(text):
        return Symbol(text, "HK")
    if match := _HK_SUFFIXED.fullmatch(text):
        return Symbol(match[1].zfill(5), "HK")
    if _HK_INDEX.fullmatch(text):
        return Symbol(text, "HK")
    if _US_INDEX.fullmatch(text) or _US_TICKER.fullmatch(text):
        return Symbol(text, "US")
    raise SymbolNotFound(f"unrecognized symbol {value[:40]!r}", params={"symbol": value[:40]})


def _bare_exchange(digits: str) -> str | None:
    if digits.startswith("11") or (digits.startswith("20") and not digits.startswith("200")):
        return None
    if digits.startswith("920") or digits[0] in "48":
        return "BJ"
    if digits[0] in "695":
        return "SH"
    if digits[0] in "0123":
        return "SZ"
    return None


def exchange_timezone(code: object) -> ZoneInfo:
    """Timezone of an upstream symbol; ``ValueError`` when the symbol is not recognized."""
    if isinstance(code, str):
        try:
            return normalize_symbol(code).timezone
        except SymbolNotFound:
            pass
    raise ValueError(f"unrecognized upstream symbol {str(code)[:40]!r}")


def _normalized_code(value: str) -> str:
    return normalize_symbol(value).code


SymbolCode = Annotated[str, AfterValidator(_normalized_code)]
"""Pydantic field type: a symbol string normalized to its Xueqiu code."""


def split_list(text: str) -> list[str]:
    """Items of a comma-separated string; ASCII and fullwidth commas both separate."""
    return [part.strip() for part in re.split(r"[,，]", text) if part.strip()]


def split_symbols(value: Any) -> Any:
    """Before-validator for symbol lists: split one comma-separated string, pass other values."""
    return split_list(value) if isinstance(value, str) else value


class SymbolParams(BaseModel):
    """Params of the endpoints that take one symbol and nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
