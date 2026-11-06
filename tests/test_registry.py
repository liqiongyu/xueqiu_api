"""Registry lint and reachability: every endpoint is complete and callable by name."""

from __future__ import annotations

import builtins
import inspect
import pkgutil
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import is_dataclass, replace
from importlib import metadata
from typing import Any, cast, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel, ConfigDict, Field

import xueqiu_api
from xueqiu_api import REGISTRY, AsyncClient, Client
from xueqiu_api import endpoints as endpoint_package
from xueqiu_api._model import USER_KEY, UpstreamModel
from xueqiu_api._replay import ReplayTransport
from xueqiu_api._result import FetchFacts
from xueqiu_api.registry import CATEGORY_TITLES, Auth, Category, Endpoint, Freshness, Pagination
from xueqiu_api.sites import SITES
from xueqiu_api.symbols import SymbolParams

from .harness import FAKE_LOGIN, REPLAY_DIR, Harness, synthetic_endpoint

CJK = re.compile(r"[一-鿿]")
ENDPOINTS = sorted(REGISTRY)
CODE_PARAMS = {"symbol", "symbols", "fund_code", "index_code", "cube_symbol", "cube_symbols"}
"""The params that name a security, fund, portfolio or index; they may be positional."""
CODE_LIKE = re.compile(r"(^|_)(symbols?|codes?|tickers?)$|^(stocks?|funds?|cubes?)$")
NOT_A_SECURITY = {"ind_code"}
"""Names that match ``CODE_LIKE`` but select something else: an industry."""
UNPREFIXED = {"kline", "quotec", "batch_quote", "suggest_stock"}
"""Names from before the rule that a name starts with its category."""
KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
SHORT_PAGE_ENDS = {"capital_block_trades"}
"""Endpoints with a measured page size cap, so that a short page ends them: full pages 1 and 2
at size=100 for capital_block_trades, measured 2026-11-05."""


def result_fields(hint: Any, seen: set[Any]) -> Iterator[str]:
    """Names of every model, dataclass and named tuple field reachable from a return type."""
    for argument in get_args(hint):
        yield from result_fields(argument, seen)
    origin = get_origin(hint)
    found = origin if isinstance(origin, type) else hint
    if not isinstance(found, type) or found in seen:
        return
    seen.add(found)
    cls = cast(type[Any], found)
    if issubclass(cls, BaseModel):
        for name, field in cls.model_fields.items():
            yield name
            yield from result_fields(field.annotation, seen)
    elif is_dataclass(cls) or hasattr(cls, "_fields"):
        for name, annotation in get_type_hints(cls).items():
            yield name
            yield from result_fields(annotation, seen)


def lint_spec(module: str, spec: Endpoint[Any, Any]) -> list[str]:
    """What is wrong with one endpoint, defined in the module named ``module``."""
    problems: list[str] = []
    agent = spec.agent
    if module != spec.name:
        problems.append(f"module {module} does not match the name {spec.name}")
    if spec.name not in UNPREFIXED and not spec.name.startswith(f"{agent.category}_"):
        problems.append(f"{spec.name} does not start with its category {agent.category}")
    if (agent.group is None) != (agent.section is None):
        problems.append(f"{spec.name} must have both a group and a section, or neither")
    for name, field in spec.params.model_fields.items():
        if hasattr(builtins, name):
            problems.append(f"param {name} shadows a builtin")
        if "token" in name.lower():
            problems.append(f"param {name} names a credential")
        if name not in CODE_PARAMS | NOT_A_SECURITY and CODE_LIKE.search(name):
            problems.append(f"param {name} must be one of {sorted(CODE_PARAMS)}")
        if not field.description:
            problems.append(f"param {name} has no description")
    if spec.auth != "required" and spec.verified_on is None:
        problems.append(f"{spec.name} needs no login but has no verified_on")
    returns = get_type_hints(spec.parse).get("return")
    for name in result_fields(returns, set()):
        if USER_KEY.search(name):
            problems.append(f"result field {name} names a user")
    return problems + lint_pagination(spec)


def lint_pagination(spec: Endpoint[Any, Any]) -> list[str]:
    """What is wrong with how one endpoint declares its pages."""
    problems: list[str] = []
    rule = spec.pagination
    paged = rule is not None and rule.style == "page"
    if ("page" in spec.params.model_fields) != paged:
        problems.append(f"{spec.name} needs both a page param and style='page', or neither")
    if rule is None:
        return problems
    if rule.end == "short" and spec.name not in SHORT_PAGE_ENDS:
        problems.append(f"{spec.name} ends on a short page without a measured page size cap")
    field = spec.params.model_fields.get("page_size")
    if field is not None:
        schema = spec.params.model_json_schema()["properties"]["page_size"]
        if not schema.get("maximum") == rule.size_cap == spec.agent.upstream_cap:
            problems.append(f"{spec.name} page_size le, size_cap and upstream_cap differ")
        if rule.size_cap is not None and field.default != min(50, rule.size_cap):
            problems.append(f"{spec.name} page_size must default to min(50, size_cap)")
    return problems


def lint_registry(specs: Iterable[Endpoint[Any, Any]]) -> list[str]:
    """What is wrong across endpoints: repeated routes and repeated sections of a group."""
    problems: list[str] = []
    routes: set[tuple[str, str]] = set()
    sections: set[tuple[str, str]] = set()
    for spec in specs:
        route = (spec.host, spec.path)
        if route in routes:
            problems.append(f"route {route} is registered twice")
        routes.add(route)
        if spec.agent.group is not None and spec.agent.section is not None:
            section = (spec.agent.group, spec.agent.section)
            if section in sections:
                problems.append(f"section {section} is registered twice")
            sections.add(section)
    return problems


def shadowed(names: Iterable[str]) -> set[str]:
    """Those of ``names`` that are also a standard library module or a dependency."""
    requires = metadata.requires("xueqiu-api") or []
    dependencies = {re.split(r"[^\w-]", line, maxsplit=1)[0].replace("-", "_") for line in requires}
    return set(names) & (set(sys.stdlib_module_names) | dependencies)


def test_registry_holds_exactly_the_endpoint_modules_in_name_order() -> None:
    assert list(REGISTRY) == ENDPOINTS
    assert set(REGISTRY) == {info.name for info in pkgutil.iter_modules(endpoint_package.__path__)}


def test_categories_all_have_titles() -> None:
    assert list(CATEGORY_TITLES) == list(get_args(Category))


def test_module_names_do_not_shadow_stdlib_or_dependencies() -> None:
    modules = pkgutil.walk_packages(xueqiu_api.__path__, f"{xueqiu_api.__name__}.")
    names = {info.name.rpartition(".")[2] for info in modules}

    assert "kline" in names
    assert shadowed(names) == set()
    assert shadowed({"mcp", "calendar", "kline"}) == {"mcp", "calendar"}


@pytest.mark.parametrize("name", ENDPOINTS)
def test_endpoint_passes_the_lint(name: str) -> None:
    assert lint_spec(name, REGISTRY[name]) == []


def test_the_registry_passes_the_lint_across_endpoints() -> None:
    assert lint_registry(REGISTRY.values()) == []


class _TypeParam(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    type: str = "all"
    """Described, but it shadows a builtin."""


class _UndescribedParam(SymbolParams):
    window: str = "day"


class _UndescribedCode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: str


class _StockCodeParam(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    stock_code: str
    """Described, but not one of the code param names."""


class _TokenParam(SymbolParams):
    xq_access_token: str = ""
    """Described, but a credential."""


class _OwnerRow(UpstreamModel):
    owner_name: str


class _OwnerResult(BaseModel):
    rows: tuple[_OwnerRow, ...]


def _owner_result(payload: Any, params: SymbolParams) -> _OwnerResult:
    return _OwnerResult.model_validate(payload)


@pytest.mark.parametrize(
    ("module", "spec", "problem"),
    [
        ("synthetic", synthetic_endpoint("synthetic"), "does not start with its category"),
        ("quote_other", synthetic_endpoint(), "does not match the name"),
        ("quote_synthetic", synthetic_endpoint(params=_TypeParam), "shadows a builtin"),
        ("quote_synthetic", synthetic_endpoint(params=_UndescribedParam), "has no description"),
        ("quote_synthetic", synthetic_endpoint(params=_UndescribedCode), "has no description"),
        ("quote_synthetic", synthetic_endpoint(verified_on=None), "has no verified_on"),
        ("quote_synthetic", synthetic_endpoint(params=_StockCodeParam), "must be one of"),
        ("quote_synthetic", synthetic_endpoint(params=_TokenParam), "names a credential"),
        ("quote_synthetic", synthetic_endpoint(parse=_owner_result), "names a user"),
    ],
    ids=[
        "prefix",
        "module",
        "builtin",
        "description",
        "code-description",
        "unverified",
        "code-param",
        "token",
        "user-field",
    ],
)
def test_the_lint_rejects_a_bad_endpoint(
    module: str, spec: Endpoint[Any, Any], problem: str
) -> None:
    assert any(problem in line for line in lint_spec(module, spec))


@pytest.mark.parametrize(
    "key",
    [
        "user",
        "users",
        "userId",
        "user_id",
        "uid",
        "screen_name",
        "reply_screenName",
        "bonus_screen_name",
        "screenname_pinyin",
        "fortuneUser",
        "answer_users",
        "reply_user_images",
        "nick_name",
        "avatar_url",
        "profile_image_url",
        "photo_domain",
        "comments",
        "last_comment_id",
        "retweeted_status",
        "owner_id",
        "cube_owner",
        "contractor_name",
    ],
)
def test_a_key_naming_a_user_is_dropped_under_any_spelling(key: str) -> None:
    class Row(UpstreamModel):
        symbol: str

    row = Row.model_validate({"symbol": "ZH2567925", "rows": [{key: "someone"}], key: "someone"})

    assert key not in (row.model_extra or {})
    assert (row.model_extra or {})["rows"] == [{}]


@pytest.mark.parametrize(
    "key",
    ["last_user_rb_gid", "other_illiquid_fnncl_assets", "followers", "follower_count", "profile"],
)
def test_a_key_that_only_looks_like_one_stays(key: str) -> None:
    assert not USER_KEY.search(key)


class _PageParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    page: int = Field(default=1, ge=1)
    """Page number."""
    page_size: int = Field(default=50, ge=1, le=100)
    """Rows per page."""


class _PageLeParams(_PageParams):
    page_size: int = Field(default=50, ge=1, le=200)
    """Rows per page, with another cap."""


class _PageDefaultParams(_PageParams):
    page_size: int = Field(default=100, ge=1, le=100)
    """Rows per page, with another default."""


def _paged(rule: Pagination, params: type[BaseModel] = _PageParams) -> Endpoint[Any, Any]:
    agent = replace(synthetic_endpoint().agent, upstream_cap=100)
    return synthetic_endpoint(params=params, agent=agent, pagination=rule)


def test_the_lint_accepts_a_page_endpoint() -> None:
    assert lint_pagination(_paged(Pagination(style="page", size_cap=100))) == []


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        (_paged(Pagination(style="latest", size_cap=100)), "both a page param"),
        (synthetic_endpoint(pagination=Pagination(style="page")), "both a page param"),
        (_paged(Pagination(style="page", size_cap=50)), "differ"),
        (_paged(Pagination(style="page", size_cap=100), _PageLeParams), "differ"),
        (_paged(Pagination(style="page", size_cap=100), _PageDefaultParams), "min(50, size_cap)"),
        (_paged(Pagination(style="page", end="short", size_cap=100)), "short page"),
    ],
    ids=["style", "param", "cap", "le", "default", "short"],
)
def test_the_lint_rejects_bad_pagination(spec: Endpoint[Any, Any], problem: str) -> None:
    assert any(problem in line for line in lint_pagination(spec))


def test_the_lint_rejects_a_section_without_a_group() -> None:
    agent = replace(synthetic_endpoint().agent, section="synthetic")

    problems = lint_spec("quote_synthetic", synthetic_endpoint(agent=agent))

    assert any("both a group and a section" in line for line in problems)


def test_the_lint_rejects_repeated_routes_and_sections() -> None:
    agent = replace(synthetic_endpoint().agent, group="xq_market", section="synthetic")
    first = synthetic_endpoint("quote_a", agent=agent)
    second = synthetic_endpoint("quote_b", agent=agent)

    problems = lint_registry([first, second])

    assert any("route" in line for line in problems)
    assert any("section" in line for line in problems)


@pytest.mark.parametrize("name", ENDPOINTS)
def test_endpoint_spec_is_complete(name: str) -> None:
    spec: Endpoint[Any, Any] = REGISTRY[name]
    agent = spec.agent

    assert re.fullmatch(r"[a-z][a-z0-9_]*", spec.name) and spec.name == name
    assert spec.doc.endswith(".")
    assert spec.host in SITES and spec.path.startswith("/")
    assert spec.auth in get_args(Auth) and spec.freshness in get_args(Freshness)
    assert CJK.search(agent.title_zh) and agent.title_en.isascii() and agent.title_en
    assert CJK.search(agent.summary) and agent.summary.endswith("。")
    assert agent.summary.count("。") == 1
    assert any(CJK.search(word) for word in agent.keywords)
    assert any(word.isascii() for word in agent.keywords)
    assert agent.default_fields
    assert 0 < agent.row_default <= agent.row_cap and agent.upstream_cap > 0
    assert agent.mcp_tool is None or re.fullmatch(r"xq_[a-z0-9_]+", agent.mcp_tool)
    assert agent.precision >= 0
    assert not set(agent.free_text) & set(agent.default_fields)
    assert not set(agent.free_text) & set(agent.labels)
    assert agent.examples
    for example in agent.examples:
        spec.validate(example)


def test_dedicated_mcp_tools_are_unique() -> None:
    tools = sorted(
        (spec.agent.mcp_tool, name) for name, spec in REGISTRY.items() if spec.agent.mcp_tool
    )
    assert tools == [
        ("xq_kline", "kline"),
        ("xq_quote", "batch_quote"),
        ("xq_search", "suggest_stock"),
    ]


@pytest.mark.parametrize("name", ENDPOINTS)
def test_every_endpoint_example_is_reachable_through_call(
    make: Callable[..., Harness], name: str
) -> None:
    spec = REGISTRY[name]
    token = FAKE_LOGIN if spec.auth == "required" else None
    h = make(token=token, transport=ReplayTransport(REPLAY_DIR))

    result = h.call(name, **spec.agent.examples[0])

    # Lists, Kline and Financials are empty when nothing came back; records are models.
    assert result
    assert isinstance(result, FetchFacts)
    assert result.fetched_at is not None
    assert (result.from_cache, result.stale) == (False, False)


@pytest.mark.parametrize("name", ENDPOINTS)
def test_every_endpoint_has_matching_client_methods(name: str) -> None:
    sync_method = getattr(Client, name)
    async_method = getattr(AsyncClient, name)
    signature = inspect.signature(sync_method)

    assert inspect.iscoroutinefunction(async_method)
    assert not inspect.iscoroutinefunction(sync_method)
    assert inspect.signature(async_method) == signature
    *params, cache, max_age = list(signature.parameters.values())[1:]
    fields = REGISTRY[name].params.model_fields
    assert [param.name for param in params] == list(fields)
    assert (cache.name, cache.kind, cache.default) == ("cache", KEYWORD_ONLY, True)
    assert (max_age.name, max_age.kind, max_age.default) == ("max_age", KEYWORD_ONLY, None)
    for index, param in enumerate(params):
        field = fields[param.name]
        if index == 0 and param.name in CODE_PARAMS:
            assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        else:
            assert param.kind is KEYWORD_ONLY, param.name
        if field.is_required():
            assert param.default is inspect.Parameter.empty, param.name
        else:
            assert param.default == field.default, param.name


def test_endpoint_methods_are_exactly_the_registry() -> None:
    public = {name for name in dir(Client) if not name.startswith("_")}
    walks = {
        f"{name}_pages"
        for name, spec in REGISTRY.items()
        if spec.pagination is not None and spec.pagination.style == "page"
    }

    handwritten = {
        "call",
        "request",
        "close",
        "status",
        "kline_history",
        "security_snapshot",
        "trading_calendar",
        "market_state",
        "adjust_factors",
    }

    assert public - handwritten == set(REGISTRY) | walks
