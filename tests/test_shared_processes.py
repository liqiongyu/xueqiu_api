"""Processes sharing one state file: slot spacing, breakers and pauses, one home-page fetch.

Every child runs ``tests/shared_worker.py`` on this interpreter. The checks bound order and
spacing, not throughput, so a slow machine only loosens them.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from xueqiu_api import Blocked, RateLimited
from xueqiu_api._core import BREAKER_COOLDOWN_S

from .shared_worker import FAST_S, STALE_TOKEN

WORKER = Path(__file__).with_name("shared_worker.py")


def child(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


def run_children(directory: Path, commands: list[list[str]]) -> tuple[float, list[Any]]:
    """Run one child per command, release them together and return t0 and their results.

    t0 is read just before the children are released, so no child books a slot before it.
    """
    argvs = [
        [sys.executable, str(WORKER), str(directory), str(index), *command]
        for index, command in enumerate(commands)
    ]
    ready = [directory / f"ready-{index}" for index in range(len(argvs))]
    with ThreadPoolExecutor(len(argvs)) as pool:
        running = [pool.submit(child, argv) for argv in argvs]
        try:
            while not all(path.exists() for path in ready):
                exited = [future.result() for future in running if future.done()]
                assert not exited, exited[0].stderr
                time.sleep(0.005)
            t0 = time.monotonic()
        finally:
            (directory / "go").touch()
        done = [future.result() for future in running]
    for process in done:
        assert process.returncode == 0, process.stderr
    return t0, [json.loads(process.stdout) for process in done]


def test_processes_book_disjoint_slots(tmp_path: Path) -> None:
    _, results = run_children(tmp_path, [["book"]] * 8)
    booked = [slot for result in results for slot in result["slots"]]

    assert len(booked) == 1600
    assert sum(slot is None for slot in booked) == 0
    assert all(ok for ok, _ in booked)
    slots = sorted(slot for _, slot in booked)
    short = [b - a for a, b in itertools.pairwise(slots) if b - a < 1.0 - 1e-6]
    assert not short, f"{len(short)} of 1599 gaps are shorter than the 1 s interval"


def test_clients_in_processes_keep_the_host_interval(tmp_path: Path) -> None:
    t0, results = run_children(tmp_path, [["sync", "pace"]] * 2 + [["async", "pace"]] * 2)

    assert [result["errors"] for result in results] == [[]] * 4
    assert [result["store_reason"] for result in results] == [None] * 4
    assert all(result["store_available"] for result in results)
    sent = sorted(at for result in results for at in result["sent"])
    assert len(sent) == 20
    tol = max(result["tol"] for result in results)
    early = [k for k, at in enumerate(sent) if at < t0 + k * FAST_S - tol]
    assert not early, f"sends {early} came before t0 + k * {FAST_S} s - {tol} s"


@pytest.mark.parametrize(
    ("scenario", "error", "cooldown"),
    [("418", Blocked, BREAKER_COOLDOWN_S), ("429", RateLimited, 7.0)],
)
def test_a_breaker_or_pause_reaches_the_other_processes(
    tmp_path: Path, scenario: str, error: type[Exception], cooldown: float
) -> None:
    """Four processes make 3 calls each at the real 1 s interval; only the first is sent.

    Calls queued behind it wake at their slots, up to 3 s later, and see that much less of
    the cooldown, so the 7 s pause outlasts the queue; ``retry_after_s`` is rounded to 0.1 s.
    """
    _, results = run_children(tmp_path, [["sync", scenario], ["async", scenario]] * 2)

    sends = [at for result in results for at in result["sent"]]
    assert len(sends) == 1, sends
    [sent] = sends
    errors = [item for result in results for item in result["errors"]]
    assert len(errors) == 12
    assert {item["type"] for item in errors} == {error.__name__}
    [first] = [item for item in errors if item["status"] is not None]
    assert (first["status"], first["retry_after_s"]) == (int(scenario), cooldown)
    others = [item for item in errors if item["status"] is None]
    assert len(others) == 11
    for item in others:
        assert cooldown - (item["at"] - sent) - 0.1 <= item["retry_after_s"] <= cooldown, item


@pytest.mark.parametrize("scenario", ["cold", "stale"])
def test_one_home_request_serves_every_process(tmp_path: Path, scenario: str) -> None:
    """Six processes without cookies, or holding a set the upstream rejects with 60201.

    A process holding the stale set sends it once, then the set one process fetched.
    """
    if scenario == "stale":
        stale = {"version": 1, "saved_at": 1.0, "cookies": {"xq_a_token": STALE_TOKEN}}
        (tmp_path / "session.json").write_text(json.dumps(stale), encoding="utf-8")
    _, results = run_children(tmp_path, [["sync", scenario], ["async", scenario]] * 3)

    assert sum(result["home"] for result in results) == 1
    assert [result["errors"] for result in results] == [[]] * 6
    sends = 2 if scenario == "stale" else 1
    assert [len(result["sent"]) for result in results] == [sends] * 6
