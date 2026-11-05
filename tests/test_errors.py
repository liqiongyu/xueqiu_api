from __future__ import annotations

from datetime import date
from typing import get_args

from xueqiu_api import ERROR_TABLE, ErrorCode, RateLimited, Recoverable, XueqiuError


def test_error_table_covers_every_code_once() -> None:
    assert list(ERROR_TABLE) == list(get_args(ErrorCode))
    exit_codes = [info.exit_code for info in ERROR_TABLE.values()]
    assert len(set(exit_codes)) == len(exit_codes)
    assert 0 not in exit_codes
    for code, info in ERROR_TABLE.items():
        assert info.exception.code == code
        assert issubclass(info.exception, XueqiuError)
        assert info.recoverable in get_args(Recoverable)
        assert info.hint.endswith(".")


def test_errors_take_defaults_from_the_table() -> None:
    error = RateLimited("slow down", endpoint="quotec", retry_after_s=2.0, params={"a": 1})

    assert (error.recoverable, error.exit_code) == ("backoff", 7)
    assert error.hint == ERROR_TABLE["rate_limited"].hint
    assert str(error) == "slow down"
    assert "retry_after_s=2.0" in repr(error)
    assert error.to_dict()["params"] == {"a": 1}


def test_credential_like_params_are_redacted() -> None:
    params = {"xq_a_token": "x", "Cookie": "y", "session_id": "z", "trade_session": 2, "q": "k"}

    error = XueqiuError("boom", params=params)

    assert error.params == {
        "xq_a_token": "<redacted>",
        "Cookie": "<redacted>",
        "session_id": "<redacted>",
        "trade_session": 2,
        "q": "k",
    }


def test_payload_params_are_json_safe() -> None:
    error = XueqiuError(
        "boom", params={"begin": date(2026, 11, 4), "symbols": ("A", "B"), "nested": {"token": 1}}
    )

    assert error.to_dict()["params"] == {
        "begin": "2026-11-04",
        "symbols": ["A", "B"],
        "nested": {"token": "<redacted>"},
    }
