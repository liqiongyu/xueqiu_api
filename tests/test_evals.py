"""The eval tasks and the search baseline, offline: the task lint, the scorer, the gate on the
baseline, the expected calls on the replay fixtures, the discovery chain and parameter mutations.

No model runs here. Requests go to the replay transport. Run ``python -m evals.retrieval`` with
XUEQIU_UPDATE_SNAPSHOTS=1 to rewrite evals/baseline.json after reviewing a change of ranks.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any, Self, cast

import httpx2
import pytest

from evals import retrieval
from evals.retrieval import (
    BASELINE,
    GATED_METRICS,
    TASK_FILES,
    UPDATE_ENV,
    Call,
    Metrics,
    Task,
    below_floor,
    bm25_search,
    build_report,
    evaluate,
    has_own_keyword,
    load_tasks,
    main,
    metrics,
    own_keywords_in_top_3,
    pending,
    rank_of,
    render,
    substring_search,
)
from xueqiu_api import REGISTRY
from xueqiu_api._async import AsyncClient, AsyncRuntime
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.errors import ERROR_TABLE
from xueqiu_api.mcp_server import McpOptions
from xueqiu_api.mcp_server.tools import ENDPOINT_QUERY_CHARS, Toolbox
from xueqiu_api.symbols import normalize_symbol

from .harness import FAKE_LOGIN, REPLAY_DIR, FakeClock

TASKS = load_tasks()
DEV = [task for task in TASKS if task.split == "dev"]
HOLDOUT = [task for task in TASKS if task.split == "holdout"]
TAG_FLOORS = {
    "confusable": 20,
    "colloquial": 12,
    "relative_time": 8,
    "adjust": 3,
    "multi_symbol": 4,
    "multi_step": 4,
    "login": 5,
    "recovery": 5,
    "symbol_rule": 3,
    "out_of_scope": 4,
}
"""Fewest tasks of a tag over the whole set: dev and holdout make up the numbers together."""
TAGS = {*TAG_FLOORS, "dedicated"}
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
MAX_CHARS = McpOptions().max_chars
REPLAY = ReplayTransport(REPLAY_DIR)


def task_id(task: Task) -> str:
    return task.id


def endpoint_only(call: Call) -> bool:
    """Whether a call names only its endpoint: its required values depend on an earlier result."""
    fields = REGISTRY[call.endpoint].params.model_fields.values()
    return not call.params and any(field.is_required() for field in fields)


def filled(task: Task) -> list[Call]:
    return [call for call in task.expected if not endpoint_only(call)]


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return build_report(TASKS)


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


# Task lint


def test_the_set_has_100_tasks_with_a_holdout_of_22() -> None:
    assert (len(TASKS), len(DEV), len(HOLDOUT)) == (100, 78, 22)


def test_ids_are_unique_and_sorted_within_each_file() -> None:
    for path in TASK_FILES:
        ids = [task.id for task in load_tasks([path])]
        assert ids == sorted(ids), path.name
    assert len({task.id for task in TASKS}) == len(TASKS)


@pytest.mark.parametrize("task", DEV, ids=task_id)
def test_a_dev_id_names_its_target_and_language(task: Task) -> None:
    assert re.fullmatch(rf"{task.target or 'out_of_scope'}-{task.lang}-\d+", task.id)


def test_dev_targets_every_endpoint_and_nothing_is_pending() -> None:
    assert {task.target for task in DEV if task.target} == set(REGISTRY)
    assert sum(task.target is None for task in DEV) == 4
    assert pending(TASKS) == []


def test_a_task_with_an_unknown_endpoint_is_pending() -> None:
    assert pending(DEV, names=set(REGISTRY) - {"kline"}) == ["kline-en-1", "kline-zh-1"]


def test_the_chinese_shares_stay_in_range() -> None:
    def share(tasks: list[Task]) -> float:
        return sum(task.lang == "zh" for task in tasks) / len(tasks)

    assert 0.70 <= share(DEV) <= 0.78
    assert 0.65 <= share(TASKS) <= 0.80


def test_each_tag_has_its_floor_over_the_whole_set() -> None:
    counts = {tag: sum(tag in task.tags for task in TASKS) for tag in TAG_FLOORS}
    assert {tag: count for tag, count in counts.items() if count < TAG_FLOORS[tag]} == {}


def test_enough_prompts_hold_no_keyword_of_their_target() -> None:
    scored = [task for task in TASKS if task.target is not None]
    assert sum(not has_own_keyword(task) for task in scored) / len(scored) >= 0.30


def test_prompts_are_unique_and_no_holdout_prompt_is_a_keyword() -> None:
    prompts = [task.prompt for task in TASKS]
    assert len(set(prompts)) == len(prompts)
    keywords = {word for spec in REGISTRY.values() for word in spec.agent.keywords}
    assert not keywords.intersection(task.prompt for task in HOLDOUT)


@pytest.mark.parametrize("task", TASKS, ids=task_id)
def test_a_task_is_well_formed(task: Task) -> None:
    assert task.lang in {"zh", "en"}
    assert task.source == ("author" if task.split == "dev" else "blind")
    assert TAGS.issuperset(task.tags)
    assert 0 < len(task.prompt) <= ENDPOINT_QUERY_CHARS
    assert task.inject is None or task.inject["error"] in ERROR_TABLE
    if task.target is None:
        assert not task.also_ok and not task.expected
    else:
        assert task.target not in task.also_ok
        assert {call.endpoint for call in task.expected} & {task.target, *task.also_ok}
        assert not endpoint_only(task.expected[0])


@pytest.mark.parametrize("task", TASKS, ids=task_id)
def test_expected_params_validate_and_are_already_canonical(task: Task) -> None:
    for call in filled(task):
        dumped = REGISTRY[call.endpoint].validate(call.params).model_dump(mode="json")
        assert {key: dumped[key] for key in call.params} == dict(call.params)


# The scorer


def test_rank_of_finds_the_first_accepted_name_in_the_top_10() -> None:
    names = [f"name{i}" for i in range(12)]
    assert rank_of(names, {"name0"}) == 1
    assert rank_of(names, {"name9", "name2"}) == 3
    assert rank_of(names, {"name10"}) is None
    assert rank_of([], {"name0"}) is None


def test_metrics_of_known_ranks() -> None:
    known = metrics([1, 2, 3, 4, None])

    assert (known.n, known.recall_at_1, known.recall_at_3, known.misses) == (5, 0.2, 0.6, 1)
    assert known.mrr_at_10 == pytest.approx((1 + 1 / 2 + 1 / 3 + 1 / 4) / 5)
    assert metrics([]) == Metrics(n=0, recall_at_1=0.0, recall_at_3=0.0, mrr_at_10=0.0, misses=0)


def scored_task(target: str | None, *also_ok: str) -> Task:
    return Task(
        id=f"{target}-zh-1",
        lang="zh",
        split="dev",
        prompt="query",
        target=target,
        also_ok=also_ok,
        expected=(),
        answer_fields=(),
        tags=(),
        inject=None,
        source="author",
    )


def test_evaluate_accepts_also_ok_unless_strict_and_skips_out_of_scope() -> None:
    def search(query: str) -> list[str]:
        return ["a", "b"]

    tasks = [scored_task("b", "a"), scored_task(None)]

    assert evaluate(search, tasks) == {"b-zh-1": 1}
    assert evaluate(search, tasks, strict=True) == {"b-zh-1": 2}


def test_the_loader_reads_out_of_scope_and_absent_keys(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        '[[task]]\nid = "x"\nlang = "zh"\nsplit = "holdout"\nprompt = "p"\n'
        'target = "out_of_scope"\nsource = "blind"\n',
        encoding="utf-8",
    )

    [task] = load_tasks([path])

    assert (task.target, task.also_ok, task.expected) == (None, (), ())
    assert task.tags == ("out_of_scope",)
    assert not has_own_keyword(task)


def test_the_real_holdout_out_of_scope_task_counts_as_one() -> None:
    assert [task.id for task in HOLDOUT if task.target is None] == ["h22"]
    assert sum("out_of_scope" in task.tags for task in TASKS) == 5


def test_the_substring_search_takes_the_query_whole() -> None:
    search = substring_search(REGISTRY.values())
    assert "capital_margin" in search("融资融券")
    assert search("浦发银行的融资融券") == []


def test_the_search_without_synonyms_loses_what_only_an_alias_reaches() -> None:
    specs = REGISTRY.values()
    assert "kline" in bm25_search(specs)("蜡烛图")
    assert bm25_search(specs, synonyms={})("蜡烛图") == []


def test_the_report_prints_the_ablation_and_groups_but_no_holdout_task(
    report: dict[str, Any],
) -> None:
    text = render(report, TASKS)

    for config in ("substring", "no_synonyms", "configured"):
        assert f"{config} all" in text
        assert f"{config} dev" in text
        assert f"{config} holdout" in text
    tags = (f"tag:{tag}" for tag in TAG_FLOORS if tag != "out_of_scope")
    for group in ("all", "dev", "holdout", "zh", "en", "strict", *tags):
        assert f"\n{group} " in text, group
    assert "pending 0" in text
    assert not any(task.id in text or task.prompt in text for task in HOLDOUT)


def test_the_baseline_is_rewritten_only_on_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baseline.json"
    monkeypatch.setattr(retrieval, "BASELINE", path)

    main()
    assert not path.exists()

    monkeypatch.setenv(UPDATE_ENV, "1")
    main()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["tasks"] == 100
    assert "below the floor" not in capsys.readouterr().out

    written["metrics"]["all"]["recall_at_1"] = 1.0
    path.write_text(json.dumps(written), encoding="utf-8")
    main()
    assert "below the floor of the baseline: recall_at_1" in capsys.readouterr().out


# The baseline


def test_the_floor_allows_the_loss_of_one_task_and_no_more() -> None:
    old = {key: 0.5 for key in GATED_METRICS}
    same = dict(old)
    one_lost = {**old, "recall_at_3": 0.5 - 1 / 100}
    two_lost = {**old, "mrr_at_10": 0.5 - 2 / 100}

    assert below_floor(same, old, 100) == []
    assert below_floor(one_lost, old, 100) == []
    assert [line.split()[0] for line in below_floor(two_lost, old, 100)] == ["mrr_at_10"]


def test_the_metrics_stay_above_the_floor_of_the_baseline(
    report: dict[str, Any], baseline: dict[str, Any]
) -> None:
    assert (
        below_floor(report["metrics"]["all"], baseline["metrics"]["all"], baseline["scored"]) == []
    )


def test_a_change_of_ranks_rewrites_the_baseline(
    report: dict[str, Any], baseline: dict[str, Any]
) -> None:
    changed = [key for key, rank in report["ranks"].items() if baseline["ranks"].get(key) != rank]
    assert not changed, f"{len(changed)} ranks changed; review them, then rewrite the baseline"


def test_the_baseline_describes_this_task_set_and_registry(
    report: dict[str, Any], baseline: dict[str, Any]
) -> None:
    for key in ("endpoints", "tasks", "scored", "pending", "out_of_scope"):
        assert baseline[key] == report[key], key
    assert baseline["pending"] == 0
    assert set(baseline["ranks"]) == {task.id for task in TASKS if task.target is not None}
    assert set(baseline["metrics"]) == set(report["metrics"])
    assert set(baseline["ablation"]) == {"substring", "no_synonyms", "configured"}
    for group in ("all", "strict", "dev", "holdout", "zh", "en"):
        assert set(GATED_METRICS) <= set(baseline["metrics"][group]), group
    assert date.fromisoformat(baseline["date"])


def test_own_keywords_keep_ranking_their_endpoint_at_the_baseline_share(
    baseline: dict[str, Any],
) -> None:
    hits, total = own_keywords_in_top_3(bm25_search(REGISTRY.values()))
    floor_hits, floor_total = baseline["own_keyword_top3"]
    assert hits / total >= floor_hits / floor_total


# The expected calls on the replay fixtures


@asynccontextmanager
async def open_toolbox(
    tmp_path: Path, transport: httpx2.AsyncBaseTransport, *, token: str | None = None
) -> AsyncGenerator[Toolbox, None]:
    clock = FakeClock()
    runtime = AsyncRuntime(transport=transport, clock=clock.now, sleep=clock.async_sleep)
    session_path = tmp_path / "session.json"
    async with AsyncClient(token=token, session_path=session_path, _runtime=runtime) as client:
        yield Toolbox(McpOptions(), client=client, now=lambda: NOW)


def token_for(*calls: Call) -> str | None:
    required = any(REGISTRY[call.endpoint].auth == "required" for call in calls)
    return FAKE_LOGIN if required else None


@pytest.mark.parametrize("task", [task for task in TASKS if task.expected], ids=task_id)
def test_the_expected_calls_work_on_the_replay_fixtures(task: Task, tmp_path: Path) -> None:
    calls = filled(task)
    options = {"fields": list(task.answer_fields)} if task.answer_fields else {}

    async def run() -> list[tuple[str, bool]]:
        async with open_toolbox(tmp_path, REPLAY, token=token_for(*calls)) as box:
            return [
                await box.call(
                    "xq_call", {"endpoint": call.endpoint, "params": call.params, **options}
                )
                for call in calls
            ]

    for text, is_error in asyncio.run(run()):
        assert not is_error, text
        assert len(text) <= MAX_CHARS
        for name in task.answer_fields:
            assert name in text, name


# The discovery chain


@pytest.mark.parametrize("task", TASKS, ids=task_id)
def test_a_prompt_searches_and_the_catalog_describes_the_params_to_send(
    task: Task, tmp_path: Path
) -> None:
    async def run() -> tuple[tuple[str, bool], dict[str, tuple[str, bool]]]:
        async with open_toolbox(tmp_path, REPLAY) as box:
            found = await box.call("xq_endpoints", {"query": task.prompt})
            return found, {
                name: await box.call("xq_endpoints", {"name": [name]}) for name in task.endpoints
            }

    (found, is_error), described = asyncio.run(run())

    assert not is_error, found
    assert len(found) <= MAX_CHARS
    for name, (text, failed) in described.items():
        assert not failed, text
        assert f"# endpoint {name}:" in text
    for call in task.expected:
        for key in call.params:
            assert f"param {key}:" in described[call.endpoint][0], key


# Parameter mutations


def _unique_calls() -> list[Call]:
    calls = {
        json.dumps([call.endpoint, call.params], sort_keys=True): call
        for task in TASKS
        for call in task.expected
        if call.params and call.endpoint in REGISTRY
    }
    return [calls[key] for key in sorted(calls)]


GOLDEN = _unique_calls()


def call_id(call: Call) -> str:
    return json.dumps([call.endpoint, call.params], sort_keys=True, ensure_ascii=True)


def _scalars_as_text(params: Mapping[str, Any]) -> dict[str, Any]:
    def text(value: Any) -> Any:
        if isinstance(value, bool):
            return str(value).lower()
        return str(value) if isinstance(value, int | float) else value

    return {key: text(value) for key, value in params.items()}


def _arrays_as_text(params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: ",".join(cast(list[str], value)) if isinstance(value, list) else value
        for key, value in params.items()
    }


def _bare_code(code: str) -> str:
    """The code without its exchange prefix, when the digits alone still resolve to it."""
    if not re.fullmatch(r"S[HZ]\d{6}", code):
        return code
    return code[2:] if normalize_symbol(code[2:]).code == code else code


def _bare_codes(params: Mapping[str, Any]) -> dict[str, Any]:
    changed = dict(params)
    if "symbol" in params:
        changed["symbol"] = _bare_code(params["symbol"])
    if "symbols" in params:
        changed["symbols"] = [_bare_code(code) for code in params["symbols"]]
    return changed


LOOSE_FORMS: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "scalars_as_text": _scalars_as_text,
    "arrays_as_text": _arrays_as_text,
    "bare_codes": _bare_codes,
}
LOOSE_CASES = [
    pytest.param(call, form(call.params), id=f"{label}-{call_id(call)}")
    for label, form in LOOSE_FORMS.items()
    for call in GOLDEN
    if form(call.params) != dict(call.params)
]


def _enum_values(call: Call) -> list[tuple[str, Any]]:
    properties = REGISTRY[call.endpoint].params.model_json_schema()["properties"]
    return [
        (key, value)
        for key, value in call.params.items()
        if key in properties
        and (
            "enum" in properties[key]
            or any("enum" in option for option in properties[key].get("anyOf", ()))
        )
    ]


ENUM_CASES = [
    pytest.param(call, key, value, id=f"{key}-{call_id(call)}")
    for call in GOLDEN
    for key, value in _enum_values(call)
]


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(NOW.timestamp(), tz)


def _requests_of(
    tmp_path: Path, endpoint: str, params: Mapping[str, Any]
) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """What the upstream sees of one call, which must succeed."""
    seen: list[httpx2.Request] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return REPLAY.handle_request(request)

    async def run() -> tuple[str, bool]:
        token = token_for(Call(endpoint, params))
        async with open_toolbox(tmp_path, httpx2.MockTransport(reply), token=token) as box:
            return await box.call("xq_call", {"endpoint": endpoint, "params": params})

    text, is_error = asyncio.run(run())
    assert not is_error, text
    return [
        (request.url.host, request.url.path, sorted(request.url.params.multi_items()))
        for request in seen
        if request.url.path != "/"
    ]


@pytest.mark.parametrize(("call", "loose"), LOOSE_CASES)
def test_a_loose_form_of_a_param_sends_the_same_request(
    call: Call, loose: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xueqiu_api._base.datetime", _FrozenDatetime)

    original = _requests_of(tmp_path, call.endpoint, call.params)

    assert original
    assert _requests_of(tmp_path, call.endpoint, loose) == original


def _error_of(tmp_path: Path, endpoint: str, params: Mapping[str, Any]) -> dict[str, Any]:
    async def run() -> tuple[str, bool]:
        async with open_toolbox(tmp_path, REPLAY) as box:
            return await box.call("xq_call", {"endpoint": endpoint, "params": params})

    text, is_error = asyncio.run(run())
    assert is_error, text
    return json.loads(text)


@pytest.mark.parametrize(("call", "key", "value"), ENUM_CASES)
def test_a_wrong_enum_value_lists_the_right_one(
    call: Call, key: str, value: Any, tmp_path: Path
) -> None:
    error = _error_of(tmp_path, call.endpoint, {**call.params, key: "no_such_value"})

    assert error["error_code"] == "invalid_params"
    assert str(value) in [*error.get("valid_values", ()), *error.get("candidates", ())]


@pytest.mark.parametrize("call", GOLDEN, ids=call_id)
def test_a_wrong_param_name_lists_the_right_one(call: Call, tmp_path: Path) -> None:
    key = next(iter(call.params))
    params = {name: value for name, value in call.params.items() if name != key}

    error = _error_of(tmp_path, call.endpoint, {**params, f"{key}_x": call.params[key]})

    assert error["error_code"] == "invalid_params"
    assert key in error["valid_values"]
