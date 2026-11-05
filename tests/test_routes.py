"""The route of every endpoint against a table kept by hand, so a route change is always a diff.

Replay answers from the path the endpoint declares, so replay tests pass with a wrong path too.
The table is not rewritten from the registry: when a route changes on purpose, edit its row.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xueqiu_api import REGISTRY

SNAPSHOT = Path(__file__).parent / "snapshots" / "endpoint_routes.json"
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)


def test_the_registry_matches_the_route_table() -> None:
    routes = [
        {
            "name": spec.name,
            "host": spec.host,
            "path": spec.path,
            "query_keys": sorted(spec.build_query(spec.validate(spec.agent.examples[0]), NOW)),
        }
        for spec in REGISTRY.values()
    ]

    assert routes == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
