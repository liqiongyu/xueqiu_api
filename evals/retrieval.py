"""Score the endpoint search on the eval tasks: Recall@1, Recall@3 and MRR@10.

``uv run python -m evals.retrieval`` prints an ablation over three search configurations, the
groups of the configured search and the dev tasks that miss the first rank. Holdout tasks show in
aggregate only. With ``XUEQIU_UPDATE_SNAPSHOTS=1`` it also rewrites ``evals/baseline.json``.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from xueqiu_api import REGISTRY
from xueqiu_api._search import RESULT_CAP, EndpointIndex
from xueqiu_api._synonyms import SYNONYMS
from xueqiu_api.registry import Endpoint

HERE = Path(__file__).parent
TASK_FILES = (HERE / "tasks.toml", HERE / "holdout.toml")
BASELINE = HERE / "baseline.json"
UPDATE_ENV = "XUEQIU_UPDATE_SNAPSHOTS"
OUT_OF_SCOPE = "out_of_scope"
GATED_METRICS = ("recall_at_1", "recall_at_3", "mrr_at_10")
DIGITS = 4

Search = Callable[[str], Sequence[str]]
"""Endpoint names for a query, best first."""


@dataclass(frozen=True, slots=True)
class Call:
    endpoint: str
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    lang: str
    split: str
    prompt: str
    target: str | None
    """Endpoint the search should find; None when no endpoint can answer the prompt."""
    also_ok: tuple[str, ...]
    expected: tuple[Call, ...]
    """Calls that complete the task. Empty params mean only the endpoint is prescribed, when the
    values depend on an earlier result."""
    answer_fields: tuple[str, ...]
    tags: tuple[str, ...]
    inject: Mapping[str, Any] | None
    source: str

    @property
    def endpoints(self) -> set[str]:
        """Every endpoint name the task refers to."""
        found = {*self.also_ok, *(call.endpoint for call in self.expected)}
        return found if self.target is None else {self.target, *found}


@dataclass(frozen=True, slots=True)
class Metrics:
    n: int
    recall_at_1: float
    recall_at_3: float
    mrr_at_10: float
    misses: int


def _task(raw: Mapping[str, Any]) -> Task:
    target: str = raw["target"]
    tags = tuple(raw.get("tags", ()))
    out_of_scope = target == OUT_OF_SCOPE
    return Task(
        id=raw["id"],
        lang=raw["lang"],
        split=raw["split"],
        prompt=raw["prompt"],
        target=None if out_of_scope else target,
        also_ok=tuple(raw.get("also_ok", ())),
        expected=tuple(Call(call["endpoint"], call["params"]) for call in raw.get("expected", ())),
        answer_fields=tuple(raw.get("answer_fields", ())),
        tags=(*tags, OUT_OF_SCOPE) if out_of_scope and OUT_OF_SCOPE not in tags else tags,
        inject=raw.get("inject"),
        source=raw["source"],
    )


def load_tasks(paths: Iterable[Path] = TASK_FILES) -> list[Task]:
    return [
        _task(raw)
        for path in paths
        for raw in tomllib.loads(path.read_text(encoding="utf-8"))["task"]
    ]


def pending(tasks: Iterable[Task], names: Collection[str] = frozenset(REGISTRY)) -> list[str]:
    """Ids of the tasks that refer to an endpoint outside ``names``."""
    return [task.id for task in tasks if not task.endpoints <= set(names)]


def rank_of(names: Sequence[str], accepted: Collection[str]) -> int | None:
    """Rank from 1 of the first accepted name in the top 10, or None."""
    for rank, name in enumerate(names[:10], start=1):
        if name in accepted:
            return rank
    return None


def metrics(ranks: Sequence[int | None]) -> Metrics:
    """The metrics of one rank per task; a miss counts as 0 in MRR@10."""
    n = len(ranks)

    def share(k: int) -> float:
        return sum(rank is not None and rank <= k for rank in ranks) / n if n else 0.0

    return Metrics(
        n=n,
        recall_at_1=share(1),
        recall_at_3=share(3),
        mrr_at_10=sum(1 / rank for rank in ranks if rank is not None) / n if n else 0.0,
        misses=sum(rank is None for rank in ranks),
    )


def evaluate(
    search: Search, tasks: Iterable[Task], *, strict: bool = False
) -> dict[str, int | None]:
    """Rank of each task with a target; ``strict`` does not accept ``also_ok`` endpoints."""
    return {
        task.id: rank_of(search(task.prompt), {task.target, *(() if strict else task.also_ok)})
        for task in tasks
        if task.target is not None
    }


def bm25_search(
    specs: Iterable[Endpoint[Any, Any]], *, synonyms: Mapping[str, tuple[str, ...]] = SYNONYMS
) -> Search:
    index = EndpointIndex(specs, synonyms=synonyms)
    return lambda query: [hit.name for hit in index.search(query)]


def substring_search(specs: Iterable[Endpoint[Any, Any]]) -> Search:
    """The whole query as a substring of an endpoint's texts, in registry order."""
    texts = {
        spec.name: " ".join(
            (
                spec.name,
                spec.agent.title_zh,
                spec.agent.title_en,
                spec.agent.summary,
                *spec.agent.keywords,
            )
        ).casefold()
        for spec in specs
    }

    def search(query: str) -> list[str]:
        needle = query.strip().casefold()
        return [name for name, text in texts.items() if needle in text][:RESULT_CAP]

    return search


def has_own_keyword(task: Task) -> bool:
    """Whether the prompt holds a keyword or title of the task's target word for word."""
    if task.target is None:
        return False
    agent = REGISTRY[task.target].agent
    prompt = task.prompt.casefold()
    return any(
        word.casefold() in prompt for word in (agent.title_zh, agent.title_en, *agent.keywords)
    )


def own_keywords_in_top_3(search: Search) -> tuple[int, int]:
    """How many keywords of the registry rank their own endpoint in the top 3, of how many."""
    found = [
        name in search(keyword)[:3]
        for name, spec in REGISTRY.items()
        for keyword in spec.agent.keywords
    ]
    return sum(found), len(found)


def _rounded(values: Metrics) -> dict[str, float | int]:
    return {
        key: round(value, DIGITS) if isinstance(value, float) else value
        for key, value in asdict(values).items()
    }


def _groups(tasks: Sequence[Task], ranks: Mapping[str, int | None]) -> dict[str, Metrics]:
    scored = [task for task in tasks if task.target is not None]

    def of(*chosen: Iterable[Task]) -> Metrics:
        return metrics([ranks[task.id] for group in chosen for task in group])

    groups = {
        "all": of(scored),
        "dev": of(task for task in scored if task.split == "dev"),
        "holdout": of(task for task in scored if task.split == "holdout"),
        "zh": of(task for task in scored if task.lang == "zh"),
        "en": of(task for task in scored if task.lang == "en"),
        "own_keyword": of(task for task in scored if has_own_keyword(task)),
        "no_own_keyword": of(task for task in scored if not has_own_keyword(task)),
    }
    tags = sorted({tag for task in scored for tag in task.tags})
    return groups | {f"tag:{tag}": of(t for t in scored if tag in t.tags) for tag in tags}


def build_report(tasks: Sequence[Task]) -> dict[str, Any]:
    """Everything ``evals/baseline.json`` holds, from a real run over the registry."""
    specs = list(REGISTRY.values())
    configured = bm25_search(specs)
    searches = {
        "substring": substring_search(specs),
        "no_synonyms": bm25_search(specs, synonyms={}),
        "configured": configured,
    }
    splits = {"all": tasks, **{s: [t for t in tasks if t.split == s] for s in ("dev", "holdout")}}
    ranks = evaluate(configured, tasks)
    outside = [task for task in tasks if task.target is None]
    return {
        "date": date.today().isoformat(),
        "endpoints": len(specs),
        "tasks": len(tasks),
        "scored": len(ranks),
        "pending": len(pending(tasks)),
        "metrics": {
            **{key: _rounded(value) for key, value in _groups(tasks, ranks).items()},
            "strict": _rounded(metrics(list(evaluate(configured, tasks, strict=True).values()))),
        },
        "ablation": {
            config: {
                split: _rounded(metrics(list(evaluate(search, chosen).values())))
                for split, chosen in splits.items()
            }
            for config, search in searches.items()
        },
        "out_of_scope": {
            "n": len(outside),
            "zero_rows": sum(not configured(task.prompt) for task in outside),
        },
        "own_keyword_top3": list(own_keywords_in_top_3(configured)),
        "ranks": ranks,
    }


def below_floor(
    current: Mapping[str, float], baseline: Mapping[str, float], scored: int
) -> list[str]:
    """The gated metrics that fell by more than one task of ``scored``."""
    tolerance = 1 / scored
    return [
        f"{key} {current[key]} is under {baseline[key]} minus 1/{scored}"
        for key in GATED_METRICS
        if current[key] < baseline[key] - tolerance
    ]


def _table(rows: Iterable[tuple[str, Mapping[str, Any]]]) -> list[str]:
    head = f"{'':<26}{'n':>4}{'R@1':>8}{'R@3':>8}{'MRR@10':>8}{'misses':>8}"
    return [
        head,
        *(
            f"{label:<26}{row['n']:>4}{row['recall_at_1']:>8.3f}{row['recall_at_3']:>8.3f}"
            f"{row['mrr_at_10']:>8.3f}{row['misses']:>8}"
            for label, row in rows
        ),
    ]


def render(report: Mapping[str, Any], tasks: Sequence[Task]) -> str:
    """The report as text; of the holdout tasks only the aggregates show."""
    configured = bm25_search(REGISTRY.values())
    hits, total = report["own_keyword_top3"]
    lines = [
        f"tasks {report['tasks']}, scored {report['scored']}, pending {report['pending']}, "
        f"endpoints {report['endpoints']}",
        f"out of scope with zero rows: {report['out_of_scope']['zero_rows']} of "
        f"{report['out_of_scope']['n']}",
        f"own keywords in the top 3: {hits} of {total}",
        "",
        "ablation",
        *_table(
            (f"{config} {split}", row)
            for config, splits in report["ablation"].items()
            for split, row in splits.items()
        ),
        "",
        "groups of the configured search",
        *_table(report["metrics"].items()),
        "",
        "dev tasks not ranked first",
    ]
    for task in tasks:
        rank = report["ranks"].get(task.id)
        if task.split == "dev" and task.target is not None and rank != 1:
            top = " ".join(configured(task.prompt)[:3]) or "(no rows)"
            lines.append(f"{task.id} rank={rank} {task.prompt} -> {top}")
    return "\n".join(lines)


def main() -> None:
    tasks = load_tasks()
    report = build_report(tasks)
    print(render(report, tasks))
    if BASELINE.is_file():
        old = json.loads(BASELINE.read_text(encoding="utf-8"))
        for problem in below_floor(report["metrics"]["all"], old["metrics"]["all"], old["scored"]):
            print(f"below the floor of the baseline: {problem}")
    if os.environ.get(UPDATE_ENV) == "1":
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        BASELINE.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
