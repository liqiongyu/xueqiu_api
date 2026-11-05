"""Community posts and user data stay out of the registry: no endpoint may reach their paths."""

from __future__ import annotations

import re

import pytest

from xueqiu_api import REGISTRY

COMMUNITY_PATHS = re.compile(
    r"^/(v4/|v5/)?statuses/"
    r"|^/users?/"
    r"|^/recommend/user/"
    r"|/search/(status|user|web)"
    r"|/symbol/search"
    r"|/status/stock_timeline"
    r"|^/query/v1/hot_event"
)
"""Posts and comments, news flashes published as posts, user profiles and hot topics."""


@pytest.mark.parametrize(
    "path",
    [
        "/statuses/hot/listV3.json",
        "/statuses/livenews/list.json",
        "/v4/statuses/user_timeline.json",
        "/v5/statuses/search.json",
        "/user/show.json",
        "/users/search.json",
        "/recommend/user/stock_hot_user.json",
        "/query/v1/search/status.json",
        "/query/v1/symbol/search/status.json",
        "/query/v1/status/stock_timeline.json",
        "/query/v1/hot_event/tag.json",
    ],
)
def test_the_pattern_covers_community_paths(path: str) -> None:
    assert COMMUNITY_PATHS.search(path)


def test_no_endpoint_reaches_a_community_path() -> None:
    assert [name for name, spec in REGISTRY.items() if COMMUNITY_PATHS.search(spec.path)] == []
