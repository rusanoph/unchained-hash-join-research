"""Extract the rendered HashJoinExec operator metrics for targeted comparisons."""

import argparse
import gzip
import json
from pathlib import Path
import re


TIME_SCALE = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1, "s": 1e3}


def parse(text):
    for depth, line in enumerate(text.splitlines()):
        if "HashJoinExec:" not in line or ", metrics=[" not in line:
            continue
        operator, metrics = line.rsplit(", metrics=[", 1)
        values = dict(re.findall(r"(?:^|, )([a-zA-Z0-9_]+)=([^,\]]+)", metrics))
        record = {"plan_line": depth + 1, "operator": operator}
        for key in ("build_time", "join_time", "elapsed_compute"):
            match = re.fullmatch(r"([.\d]+)\s*(ns|µs|us|ms|s)", values.get(key, ""))
            if match:
                record[key + "_ms"] = float(match[1]) * TIME_SCALE[match[2]]
        for key in ("build_input_rows", "input_rows", "output_rows", "build_mem_used",
                    "array_map_created_count", "dense_index_created_count", "probe_hit_rate", "avg_fanout"):
            if key in values:
                record[key] = values[key]
        yield record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("plans", type=Path)
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()
    with gzip.open(args.plans, "rt", encoding="utf-8") as stream:
        plans = {row["sha256"]: row["text"] for row in map(json.loads, stream)}
    with gzip.open(args.raw, "rt", encoding="utf-8") as stream:
        for observation in map(json.loads, stream):
            if observation["status"] != "succeeded":
                continue
            key = observation.get("query", observation.get("case"))
            if args.queries and key not in args.queries:
                continue
            dimensions = ("case",) if "case" in observation else ("query", "block", "arm")
            summary = {name: observation[name] for name in (*dimensions, "metrics_sha256")}
            summary["joins"] = list(parse(plans[observation["metrics_sha256"]]))
            print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
