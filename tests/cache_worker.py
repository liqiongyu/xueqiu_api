"""Child process for tests/test_cache_backends.py; not collected by pytest.

``mixed PATH INDEX OPS``: open a ``SqliteCache`` at ``PATH``, mark ``ready-INDEX`` next to it,
wait for ``go``, then run ``OPS`` random operations on a shared set of keys. Prints one JSON
object: the errors seen, and how many reads found an entry another process wrote.

``fork PATH``: put from the parent, fork, use the inherited cache in the child and then in the
parent, and print JSON.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

from xueqiu_api._sqlite import open_db
from xueqiu_api.cache import CacheEntry, SqliteCache

KEYS = 200
ENDPOINTS = ("kline", "f10_company", "finance_income")
MAX_BYTES = 256 * 1024
"""Less than the bodies of all keys take, so the processes evict each other's entries."""


class Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def mixed(path: Path, index: int, ops: int) -> dict[str, object]:
    warnings = Warnings()
    logging.getLogger("xueqiu_api").addHandler(warnings)
    rng = random.Random(index)
    cache = SqliteCache(path, max_bytes=MAX_BYTES)
    (path.parent / f"ready-{index}").touch()
    while not (path.parent / "go").exists():
        time.sleep(0.001)
    errors: list[str] = []
    foreign = 0
    for seq in range(ops):
        number = rng.randrange(KEYS)
        key, endpoint = f"key-{number}", ENDPOINTS[number % len(ENDPOINTS)]
        roll = rng.random()
        try:
            if roll < 0.45:
                entry = cache.get(key)
                if entry is not None:
                    owner, writer, _, _ = entry.body.split(b"|", 3)
                    if owner != key.encode() or entry.endpoint != endpoint:
                        errors.append(f"{key} holds an entry of {owner!r} for {entry.endpoint}")
                    foreign += int(writer) != index
            elif roll < 0.8:
                body = f"{key}|{index}|{seq}|".encode() + rng.randbytes(rng.randrange(512, 6144))
                cache.put(key, CacheEntry(body, time.time(), endpoint))
            elif roll < 0.9:
                cache.delete(key)
            elif roll < 0.99:
                stats = cache.stats()
                if not 0 <= stats.entries <= KEYS:
                    errors.append(f"stats counted {stats.entries} entries")
            else:
                cache.clear(endpoint)
        except Exception as exc:
            errors.append(f"operation {seq}: {type(exc).__name__}: {exc}")
        status = cache.status()
        if not status.available:
            errors.append(f"unavailable after operation {seq}: {status.reason}")
    return {"errors": errors + warnings.messages, "foreign_reads": foreign}


def fork(path: Path) -> dict[str, object]:
    cache = SqliteCache(path)
    now = time.time()
    cache.put("parent-1", CacheEntry(b"parent 1", now, "kline"))
    inherited = cache._db.run(lambda conn: conn)  # pyright: ignore[reportPrivateUsage]
    child = os.fork()
    if child == 0:
        code = 1
        try:
            cache.put("child", CacheEntry(b"child", now, "kline"))
            seen = cache.get("parent-1")
            reopened = cache._db.run(lambda conn: conn is not inherited)  # pyright: ignore[reportPrivateUsage]
            ok = seen is not None and seen.body == b"parent 1" and reopened
            code = 0 if ok and cache.status().available else 1
        finally:
            os._exit(code)
    _, status = os.waitpid(child, 0)
    cache.put("parent-2", CacheEntry(b"parent 2", now, "kline"))
    bodies = {}
    for key in ("parent-1", "child", "parent-2"):
        entry = cache.get(key)
        bodies[key] = None if entry is None else entry.body.decode()
    kept = cache._db.run(lambda conn: conn is inherited)  # pyright: ignore[reportPrivateUsage]
    check = open_db(path, busy_timeout_s=5, readonly=True)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchall()
    finally:
        check.close()
    return {
        "child_exit": os.waitstatus_to_exitcode(status),
        "bodies": bodies,
        "parent_kept_its_connection": kept,
        "available": cache.status().available,
        "integrity": integrity,
    }


def main(argv: list[str]) -> None:
    if argv[0] == "mixed":
        result = mixed(Path(argv[1]), int(argv[2]), int(argv[3]))
    else:
        result = fork(Path(argv[1]))
    print(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1:])
