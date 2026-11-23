"""Check duplicate and NULL semantics and require runtime evidence of the join path."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile


SETUP = """
CREATE TABLE b(k INTEGER, s VARCHAR);
CREATE TABLE p(k INTEGER, id INTEGER);
INSERT INTO b VALUES (1, 'alpha'), (1, 'beta'), (2, NULL), (NULL, 'null key');
INSERT INTO p VALUES (1, 101), (1, 102), (2, 103), (3, 104), (NULL, 105);
"""


def run(worker, directory, sql, mode):
    query = directory / "query.sql"
    output = directory / "rows.jsonl"
    query.write_text(sql)
    process = subprocess.run(
        [str(worker), "--setup", str(directory / "setup.sql"), "--query", str(query),
         "--mode", mode, "--threads", "1", "--warmups", "0", "--result", str(output)],
        text=True, capture_output=True, timeout=60,
    )
    if process.returncode:
        return process, None, None
    sample = json.loads(process.stdout)["samples"][0]
    rows = Counter(tuple(json.loads(line)) for line in output.read_text().splitlines())
    return process, sample, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker", type=Path)
    worker = parser.parse_args().worker.resolve(strict=True)
    with tempfile.TemporaryDirectory() as directory, sqlite3.connect(":memory:") as oracle:
        directory = Path(directory)
        (directory / "setup.sql").write_text(SETUP)
        oracle.executescript(SETUP)

        cases = (
            ("JOIN", "native", None),
            ("JOIN", "unchained_required", None),
            ("LEFT JOIN", "unchained_prefer", "join_type_unsupported"),
            ("LEFT JOIN", "unchained_required", "join_type_unsupported"),
        )
        for join, mode, fallback in cases:
            sql = f"SELECT p.id, b.s FROM p {join} b ON p.k = b.k"
            process, sample, rows = run(worker, directory, sql, mode)
            if fallback and mode == "unchained_required":
                if process.returncode != 1 or fallback not in process.stderr:
                    raise RuntimeError(f"{join} {mode}: expected rejection, got {process}")
            else:
                if process.returncode:
                    raise RuntimeError(f"{join} {mode}: {process.stderr}")
                expected = Counter((str(row_id), value) for row_id, value in oracle.execute(sql))
                executed = sample["executed_unchained_joins"]
                reasons = {operator["reason"] for operator in sample["operators"]}
                if rows != expected or executed != int(mode == "unchained_required"):
                    raise RuntimeError(f"{join} {mode}: unexpected rows or runtime path")
                if fallback and fallback not in reasons:
                    raise RuntimeError(f"{join} {mode}: missing fallback reason in {reasons}")
            print(f"{join} / {mode}: passed")


if __name__ == "__main__":
    main()
