"""Child process for tests/test_sqlite_db.py; not collected by pytest.

``open DIR INDEX ROUNDS``: in each round, mark ``ready-<round>-<index>``, wait for
``go-<round>``, then open ``round-<round>.sqlite3`` and insert a row. Prints one JSON list
with "ok" or the error for each round.

``fork PATH``: open a ``Db``, fork, write from the child and the parent, and print JSON.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from xueqiu_api._sqlite import Db, open_db

SETUP = ("CREATE TABLE IF NOT EXISTS rows (pid INTEGER NOT NULL)",)


def open_rounds(directory: Path, index: int, rounds: int) -> list[str]:
    results: list[str] = []
    for round_ in range(rounds):
        (directory / f"ready-{round_}-{index}").touch()
        while not (directory / f"go-{round_}").exists():
            time.sleep(0.001)
        try:
            conn = open_db(directory / f"round-{round_}.sqlite3", busy_timeout_s=30, setup=SETUP)
            try:
                conn.execute("INSERT INTO rows VALUES (?)", (os.getpid(),))
            finally:
                conn.close()
        except Exception as exc:
            results.append(f"{type(exc).__name__}: {exc}")
        else:
            results.append("ok")
    return results


def fork(path: Path) -> dict[str, object]:
    db = Db(path, busy_timeout_s=5, setup=SETUP, label="test store")
    insert = "INSERT INTO rows VALUES (?)"
    db.run(lambda conn: conn.execute(insert, (os.getpid(),)))
    inherited = db.run(lambda conn: conn)
    child = os.fork()
    if child == 0:
        code = 1
        try:
            wrote = db.run(lambda conn: conn.execute(insert, (os.getpid(),)).rowcount)
            reopened = db.run(lambda conn: conn is not inherited)
            code = 0 if wrote == 1 and reopened else 1
        finally:
            os._exit(code)
    _, status = os.waitpid(child, 0)
    wrote = db.run(lambda conn: conn.execute(insert, (os.getpid(),)).rowcount)
    kept = db.run(lambda conn: conn is inherited)
    rows = db.run(lambda conn: [pid for (pid,) in conn.execute("SELECT pid FROM rows")])
    integrity = db.run(lambda conn: conn.execute("PRAGMA integrity_check").fetchall())
    db.close()
    return {
        "child_exit": os.waitstatus_to_exitcode(status),
        "parent_wrote": wrote,
        "parent_kept_its_connection": kept,
        "rows": rows,
        "parent": os.getpid(),
        "child": child,
        "integrity": integrity,
    }


def main(argv: list[str]) -> None:
    if argv[0] == "open":
        result: object = open_rounds(Path(argv[1]), int(argv[2]), int(argv[3]))
    else:
        result = fork(Path(argv[1]))
    print(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1:])
