"""Read the portable JSONL archives of observations and plans."""

import gzip
import json


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def read_plans(path):
    return {row["sha256"]: row["text"] for row in read_rows(path)}
