from __future__ import annotations

import pytest

import xueqiu_api
from xueqiu_api.cli import main


def test_main_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert capsys.readouterr().out == f"{xueqiu_api.__version__}\n"
