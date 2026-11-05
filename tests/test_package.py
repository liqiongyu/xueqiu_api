from __future__ import annotations

import inspect
import json
import logging
import pkgutil
import subprocess
import sys
from importlib.metadata import entry_points, version
from types import ModuleType
from typing import Any

import xueqiu_api


def in_a_new_interpreter(code: str) -> Any:
    """The JSON that ``code`` prints in an interpreter where nothing is imported yet."""
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_version_matches_metadata() -> None:
    assert xueqiu_api.__version__ == version("xueqiu-api")


def test_console_script_points_at_the_cli() -> None:
    scripts = entry_points(group="console_scripts")
    assert [ep.value for ep in scripts if ep.name == "xueqiu"] == ["xueqiu_api.cli:main"]


def test_public_api_has_no_httpx2_types() -> None:
    for name in xueqiu_api.__all__:
        value = getattr(xueqiu_api, name)
        assert not getattr(value, "__module__", "").startswith("httpx"), name
    for cls in (xueqiu_api.Client, xueqiu_api.AsyncClient):
        members = [(name, getattr(cls, name)) for name in dir(cls)]
        members = [(name, member) for name, member in members if callable(member)]
        for name, member in [("__init__", cls.__init__), *members]:
            if name.startswith("_") and name not in {"__init__", "__enter__", "__aenter__"}:
                continue
            signature = inspect.signature(member)
            annotations = [str(p.annotation) for p in signature.parameters.values()]
            annotations.append(str(signature.return_annotation))
            assert not any("httpx" in text for text in annotations), (cls.__name__, name)


def test_client_surfaces_match() -> None:
    def public(cls: type) -> set[str]:
        return {name for name in dir(cls) if not name.startswith("_")}

    assert public(xueqiu_api.Client) == public(xueqiu_api.AsyncClient)
    assert {"quotec", "kline", "call", "request", "close"} <= public(xueqiu_api.Client)


def test_import_leaves_logging_alone() -> None:
    code = """
import json, logging
import xueqiu_api
root = logging.getLogger()
ours = logging.getLogger("xueqiu_api")
print(json.dumps([root.level, len(root.handlers), ours.level, len(ours.handlers)]))
"""
    assert in_a_new_interpreter(code) == [logging.WARNING, 0, logging.NOTSET, 0]


def test_core_import_does_not_load_extras() -> None:
    code = """
import json, sys
import xueqiu_api
print(json.dumps(sorted(sys.modules)))
"""
    modules = in_a_new_interpreter(code)

    assert "xueqiu_api.endpoints.kline" in modules
    assert "xueqiu_api.mcp_server" not in modules
    assert not {"mcp", "pandas", "polars", "duckdb", "bs4"} & {
        name.partition(".")[0] for name in modules
    }


def test_no_public_name_shadows_a_submodule() -> None:
    names = [info.name for info in pkgutil.iter_modules(xueqiu_api.__path__)]
    values = {name: getattr(xueqiu_api, name) for name in names if hasattr(xueqiu_api, name)}

    assert {"universe", "adjust", "errors"} <= set(names)
    assert isinstance(values["errors"], ModuleType)
    assert {name for name, value in values.items() if not isinstance(value, ModuleType)} == set()
