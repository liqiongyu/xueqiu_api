from importlib.metadata import version

import xueqiu_api


def test_version_matches_metadata() -> None:
    assert xueqiu_api.__version__ == version("xueqiu-api")
