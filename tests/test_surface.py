"""The generated client files and the public surface: types, exports and the SDK snapshot."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel, BeforeValidator, Field

import xueqiu_api
from xueqiu_api import REGISTRY, AsyncClient, Client, Page, models
from xueqiu_api._model import UpstreamModel
from xueqiu_api.symbols import SymbolParams, split_symbols

from .harness import synthetic_endpoint

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "generate_client.py"
SNAPSHOT = Path(__file__).parent / "snapshots" / "sdk_surface.json"
UPDATE_ENV = "XUEQIU_UPDATE_SNAPSHOTS"
OUTSIDE_THE_MODELS = {
    "AsyncIterator",
    "Iterator",
    "Sequence",
    "bool",
    "date",
    "datetime",
    "float",
    "int",
    "list",
    "str",
    "tuple",
}
"""Names in generated signatures that are standard library types."""
PAGED = [
    name
    for name, spec in REGISTRY.items()
    if spec.pagination is not None and spec.pagination.style == "page"
]


def load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("generate_client", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_docstrings_keep_the_params_and_what_the_params_class_says() -> None:
    history = Client.index_pe_history.__doc__ or ""
    assort = Client.capital_assort.__doc__ or ""

    assert "One request returns the whole window" in history
    assert "symbol: Security or index code" in assort
    assert "take one symbol and nothing else" not in assort


def test_generated_files_are_fresh() -> None:
    for path, text in load_generator().generate().items():
        assert path.read_text(encoding="utf-8") == text, f"run {SCRIPT.name}: {path.name} is stale"


def test_the_generator_runs_after_an_endpoint_module_is_deleted(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts")
    (tmp_path / "src" / "xueqiu_api" / "endpoints" / "cube_nav_daily.py").unlink()
    code = "import xueqiu_api; print(hasattr(xueqiu_api.Client, 'cube_nav_daily'))"

    def python(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
            capture_output=True,
            text=True,
            check=False,
        )

    stale = python("-c", code)
    assert stale.returncode != 0
    assert "ModuleNotFoundError" in stale.stderr

    generated = python(str(tmp_path / "scripts" / SCRIPT.name))
    assert generated.returncode == 0, generated.stderr

    fresh = python("-c", code)
    assert fresh.returncode == 0, fresh.stderr
    assert fresh.stdout.split() == ["False"]


class _One:
    class Row(UpstreamModel):
        one: int


class _Two:
    class Row(UpstreamModel):
        two: int


def _parse_one(payload: Any, params: SymbolParams) -> _One.Row:
    return _One.Row.model_validate(payload)


def _parse_two(payload: Any, params: SymbolParams) -> _Two.Row:
    return _Two.Row.model_validate(payload)


def _parse_again(payload: Any, params: SymbolParams) -> list[_One.Row]:
    return [_One.Row.model_validate(payload)]


class _RowPage(Page[_One.Row]):
    pass


def _parse_page(payload: Any, params: SymbolParams) -> _RowPage:
    return _RowPage((), page=1, page_size=1)


def test_the_generator_finds_the_row_type_of_a_page_subclass() -> None:
    found = load_generator().public_types([synthetic_endpoint("quote_one", parse=_parse_page)])

    assert found["Row"][0] is _One.Row
    assert found["_RowPage"][0] is _RowPage


def test_the_generator_rejects_two_types_of_one_name() -> None:
    first = synthetic_endpoint("quote_one", parse=_parse_one)
    second = synthetic_endpoint("quote_two", parse=_parse_two)
    generator = load_generator()

    with pytest.raises(ValueError, match="two public types are named Row"):
        generator.public_types([first, second])


def test_the_generator_accepts_one_type_of_several_endpoints() -> None:
    first = synthetic_endpoint("quote_one", parse=_parse_one)
    again = synthetic_endpoint("quote_two", parse=_parse_again)

    assert list(load_generator().public_types([first, again])) == ["Row"]


class OptionalCodes(BaseModel):
    cube_symbols: (
        Annotated[tuple[str, ...], BeforeValidator(split_symbols), Field(min_length=1)] | None
    ) = None


def test_the_generator_keeps_the_loose_type_of_an_optional_code_list() -> None:
    generator = load_generator()
    spec = synthetic_endpoint("quote_one", params=OptionalCodes)

    [param] = generator._params(spec, generator.Imports())

    assert param.annotation == "str | Sequence[str] | None"


def annotation_names(text: str) -> set[str]:
    tree = ast.parse(text, mode="eval")
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


@pytest.mark.parametrize("cls", [Client, AsyncClient])
def test_models_export_every_type_of_a_generated_signature(cls: type) -> None:
    mixin = cls.__mro__[-2]
    assert mixin.__name__.endswith("Endpoints")
    names: set[str] = set()
    for name, member in vars(mixin).items():
        if name.startswith("_"):
            continue
        signature = inspect.signature(member)
        annotations = [param.annotation for param in signature.parameters.values()]
        for text in [signature.return_annotation, *annotations]:
            if text is not inspect.Parameter.empty:
                names |= annotation_names(text)

    assert {"Kline", "Page", "Period", "ReportType", "BlockTrade", "CubeNav", "FundNav"} <= names
    assert names - OUTSIDE_THE_MODELS <= set(models.__all__)


def test_models_exports_nested_result_types() -> None:
    assert {"LineItem", "ReportPeriod", "Adjust"} <= set(models.__all__)


def test_result_models_drop_the_keys_naming_a_user() -> None:
    results = [getattr(models, name) for name in models.__all__]
    pydantic_models = [
        cls for cls in results if isinstance(cls, type) and issubclass(cls, BaseModel)
    ]

    assert pydantic_models
    assert all(issubclass(cls, UpstreamModel) for cls in pydantic_models)


def test_the_top_level_keeps_only_the_short_list() -> None:
    params = {spec.params.__name__ for spec in REGISTRY.values()}

    assert not params & set(xueqiu_api.__all__)
    assert set(models.__all__) & set(xueqiu_api.__all__) == {"Page"}
    assert xueqiu_api.Page is models.Page


@pytest.mark.parametrize("name", PAGED)
def test_page_methods_take_the_params_of_the_method_and_two_limits(name: str) -> None:
    single = inspect.signature(getattr(Client, name)).parameters
    walk = inspect.signature(getattr(Client, f"{name}_pages")).parameters
    async_method = getattr(AsyncClient, f"{name}_pages")
    # Every page is fetched anew, so a walk takes no cache params.
    own = [key for key in single if key not in ("cache", "max_age")]

    assert inspect.signature(async_method).parameters == walk
    assert not inspect.iscoroutinefunction(async_method)
    assert list(single)[-2:] == ["cache", "max_age"]
    assert list(walk) == [*own, "max_pages", "max_items"]
    for key in own:
        if key != "page_size":
            assert walk[key] == single[key]
    for key in ("page_size", "max_pages", "max_items"):
        if key in walk:
            assert walk[key].kind is inspect.Parameter.KEYWORD_ONLY
            assert walk[key].default is None


def methods(cls: type) -> dict[str, str]:
    """Signatures of the public methods and the constructor, without private params."""
    found: dict[str, str] = {}
    for name in ("__init__", *(name for name in dir(cls) if not name.startswith("_"))):
        signature = inspect.signature(getattr(cls, name))
        public = [p for p in signature.parameters.values() if not p.name.startswith("_")]
        found[name] = str(signature.replace(parameters=public))
    return found


def shape(value: Any, generator: Any) -> Any:
    """Field names and types of a result type, or the values of a Literal."""
    if get_origin(value) is Literal:
        return list(get_args(value))
    return generator.field_texts(value)


def test_the_surface_records_the_type_of_each_field() -> None:
    generator = load_generator()

    assert shape(models.ReportPeriod, generator)["report_name"] == "str"
    assert shape(models.Kline, generator)["timestamps"] == "tuple[datetime, ...]"
    assert shape(models.Page, generator)["next_params"] == "Mapping[str, Any] | None"
    assert shape(models.BlockTrade, generator)["vol"] == "float | None"


def test_sdk_surface_snapshot() -> None:
    generator = load_generator()
    surface = {
        "top_level": sorted(xueqiu_api.__all__),
        "Client": methods(Client),
        "AsyncClient": methods(AsyncClient),
        "models": {name: shape(getattr(models, name), generator) for name in models.__all__},
    }
    text = json.dumps(surface, ensure_ascii=False, indent=2) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(text, encoding="utf-8", newline="\n")
        return
    assert SNAPSHOT.read_text(encoding="utf-8") == text, (
        f"{SNAPSHOT.name} drifted; review it, then rewrite with {UPDATE_ENV}=1"
    )
