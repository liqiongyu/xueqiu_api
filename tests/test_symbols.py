from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import TypeAdapter, ValidationError

from xueqiu_api import SymbolNotFound, normalize_symbol
from xueqiu_api.symbols import CubeCode, FundCode, IndexCode

CASES = [
    # A shares with an explicit market
    ("SH600519", "SH600519", "CN"),
    ("sh600519", "SH600519", "CN"),
    (" SZ000001 ", "SZ000001", "CN"),
    ("600519.SH", "SH600519", "CN"),
    ("000001.sz", "SZ000001", "CN"),
    ("920045.BJ", "BJ920045", "CN"),
    ("BJ920045", "BJ920045", "CN"),
    # A-share indexes need a prefix
    ("SH000001", "SH000001", "CN"),
    ("SZ399001", "SZ399001", "CN"),
    ("BJ899050", "BJ899050", "CN"),
    # Bare 6 digits are stocks
    ("600000", "SH600000", "CN"),
    ("688001", "SH688001", "CN"),
    ("900901", "SH900901", "CN"),
    ("510300", "SH510300", "CN"),
    ("000001", "SZ000001", "CN"),
    ("159915", "SZ159915", "CN"),
    ("200011", "SZ200011", "CN"),
    ("300750", "SZ300750", "CN"),
    ("430047", "BJ430047", "CN"),
    ("830799", "BJ830799", "CN"),
    ("920045", "BJ920045", "CN"),
    # Bonds and repos with a prefix
    ("SH113050", "SH113050", "CN"),
    ("SZ123001", "SZ123001", "CN"),
    ("SH204001", "SH204001", "CN"),
    # Hong Kong
    ("00700", "00700", "HK"),
    ("89988", "89988", "HK"),
    ("0700.HK", "00700", "HK"),
    ("700.hk", "00700", "HK"),
    ("HKHSI", "HKHSI", "HK"),
    ("hkhstech", "HKHSTECH", "HK"),
    # United States
    ("AAPL", "AAPL", "US"),
    ("aapl", "AAPL", "US"),
    ("BRK.B", "BRK.B", "US"),
    ("HKD", "HKD", "US"),
    (".DJI", ".DJI", "US"),
    (".inx", ".INX", "US"),
]


@pytest.mark.parametrize(("text", "code", "market"), CASES)
def test_normalize_symbol(text: str, code: str, market: str) -> None:
    symbol = normalize_symbol(text)
    assert (symbol.code, symbol.market) == (code, market)
    assert str(symbol) == code


@pytest.mark.parametrize(
    ("text", "region", "timezone"),
    [
        ("SH600519", "cn", "Asia/Shanghai"),
        ("00700", "hk", "Asia/Hong_Kong"),
        ("AAPL", "us", "America/New_York"),
    ],
)
def test_region_and_timezone(text: str, region: str, timezone: str) -> None:
    symbol = normalize_symbol(text)
    assert symbol.region == region
    assert symbol.timezone == ZoneInfo(timezone)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "700000",
        "6005",
        "6005190",
        "SH60051",
        "600519.SS",
        "AAPL.US",
        "ZH2567925",
        "\uff16\uff10\uff10\uff15\uff11\uff19",
        # Bonds, convertible bonds and repos are numbered in both exchanges.
        "110059",
        "113050",
        "118000",
        "204001",
    ],
)
def test_unrecognized_symbols(text: str) -> None:
    with pytest.raises(SymbolNotFound) as caught:
        normalize_symbol(text)
    assert caught.value.code == "symbol_not_found"
    assert caught.value.recoverable == "fix_params"


@pytest.mark.parametrize(
    ("code_type", "text", "expected"),
    [
        (FundCode, "008975", "008975"),
        (FundCode, " 008975 ", "008975"),
        (CubeCode, "ZH2567925", "ZH2567925"),
        (CubeCode, " zh2567925 ", "ZH2567925"),
        (IndexCode, "SH000300", "SH000300"),
        (IndexCode, "sz399006", "SZ399006"),
        (IndexCode, "CSIH30269", "CSIH30269"),
        (IndexCode, "hkhsi", "HKHSI"),
        (IndexCode, "NDX", "NDX"),
        (IndexCode, "935600", "935600"),
    ],
)
def test_code_types_accept(code_type: Any, text: str, expected: str) -> None:
    assert TypeAdapter(code_type).validate_python(text) == expected


@pytest.mark.parametrize(
    ("code_type", "value"),
    [
        (FundCode, "8975"),
        (FundCode, "SH008975"),
        (FundCode, 8975),
        (CubeCode, "ZH"),
        (CubeCode, "SH600000"),
        (CubeCode, 2567925),
        # Bare digits are ambiguous: 000300 is both a stock and an index number.
        (IndexCode, "000300"),
        (IndexCode, ".INX"),
        (IndexCode, "N"),
        (IndexCode, 935600),
    ],
)
def test_code_types_reject(code_type: Any, value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(code_type).validate_python(value)
