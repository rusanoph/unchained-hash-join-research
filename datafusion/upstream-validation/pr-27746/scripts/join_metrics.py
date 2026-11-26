"""Extract the rendered HashJoinExec operator metrics for targeted comparisons."""

import argparse
import json
from pathlib import Path

from evidence.archives import read_plans, read_rows
from evidence.metrics import parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("plans", type=Path)
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()
    plans = read_plans(args.plans)
    for observation in read_rows(args.raw):
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
