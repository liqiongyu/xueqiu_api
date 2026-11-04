"""Registry lint and reachability: every endpoint is complete and callable by name."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, get_args

import pytest

from xueqiu_api import REGISTRY, Auth, Endpoint, Freshness
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.sites import SITES

from .harness import REPLAY_DIR, Harness

CJK = re.compile(r"[一-鿿]")
ENDPOINTS = sorted(REGISTRY)


def test_registry_is_discovered_in_name_order() -> None:
    assert list(REGISTRY) == ENDPOINTS
    assert {"kline", "quotec"} <= set(REGISTRY)


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


@pytest.mark.parametrize("name", ENDPOINTS)
def test_every_endpoint_example_is_reachable_through_call(
    make: Callable[..., Harness], name: str
) -> None:
    h = make(transport=ReplayTransport(REPLAY_DIR))

    result = h.call(name, **REGISTRY[name].agent.examples[0])

    assert len(result) > 0
