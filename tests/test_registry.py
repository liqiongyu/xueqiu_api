"""Registry lint and reachability: every endpoint is complete and callable by name."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, get_args

import pytest

from xueqiu_api import REGISTRY, AsyncClient, Auth, Client, Endpoint, Freshness
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.sites import SITES

from .harness import REPLAY_DIR, Harness

CJK = re.compile(r"[一-鿿]")
ENDPOINTS = sorted(REGISTRY)
SYMBOL_PARAMS = {"symbol", "symbols"}


def test_registry_is_discovered_in_name_order() -> None:
    assert list(REGISTRY) == ENDPOINTS
    assert set(REGISTRY) == {
        "batch_quote",
        "f10_company",
        "finance_balance",
        "finance_cash_flow",
        "finance_income",
        "finance_indicator",
        "kline",
        "quote_detail",
        "quotec",
        "suggest_stock",
    }


@pytest.mark.parametrize("name", ENDPOINTS)
def test_endpoint_spec_is_complete(name: str) -> None:
    spec: Endpoint[Any, Any] = REGISTRY[name]
    agent = spec.agent

    assert re.fullmatch(r"[a-z][a-z0-9_]*", spec.name) and spec.name == name
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
    h = make(transport=ReplayTransport(REPLAY_DIR))

    result = h.call(name, **REGISTRY[name].agent.examples[0])

    # Lists, Kline and Financials are empty when nothing came back; records are models.
    assert result


@pytest.mark.parametrize("name", ENDPOINTS)
def test_every_endpoint_has_matching_client_methods(name: str) -> None:
    sync_method = getattr(Client, name)
    async_method = getattr(AsyncClient, name)
    signature = inspect.signature(sync_method)

    assert inspect.iscoroutinefunction(async_method)
    assert not inspect.iscoroutinefunction(sync_method)
    assert inspect.signature(async_method) == signature
    params = list(signature.parameters.values())[1:]
    fields = REGISTRY[name].params.model_fields
    assert [param.name for param in params] == list(fields)
    for index, param in enumerate(params):
        field = fields[param.name]
        if index == 0 and param.name in SYMBOL_PARAMS:
            assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        else:
            assert param.kind is inspect.Parameter.KEYWORD_ONLY, param.name
        if field.is_required():
            assert param.default is inspect.Parameter.empty, param.name
        else:
            assert param.default == field.default, param.name


def test_endpoint_methods_are_exactly_the_registry() -> None:
    public = {name for name in vars(Client) if not name.startswith("_")}

    assert public - {"call", "request", "close"} == set(REGISTRY)
