"""Summarize the controlled process and memory-pool observations for all three arms."""

import argparse
import collections
import json
from pathlib import Path
import statistics

from evidence.archives import read_plans, read_rows
from evidence.metrics import parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("runs", nargs="+")
    args = parser.parse_args()
    whole = {}
    for run in args.runs:
        rows = list(read_rows(args.results / "raw" / (run + ".jsonl.gz")))
        plans = read_plans(args.results / "plans" / (run + ".jsonl.gz"))
        cells = collections.defaultdict(list)
        for row in rows:
            cells[row["query"], row["arm"]].append(row)
        result = collections.defaultdict(dict)
        for (query, arm), obs in sorted(cells.items()):
            good = [row for row in obs if row["status"] == "succeeded"]
            assert all(row["oracle_match"] for row in good)
            failures = {row["status"]: row["stderr"] for row in obs if row["status"] != "succeeded"}
            arm_summary = {"status": collections.Counter(row["status"] for row in obs)}
            if failures:
                arm_summary["failure_excerpt"] = failures
            if good:
                for field in ("statement_wall_ns", "peak_rss_kib", "pool_peak_bytes"):
                    arm_summary["median_" + field] = statistics.median(row[field] for row in good)
                joins = []
                for row in good:
                    join, = parse(plans[row["metrics_sha256"]])
                    joins.append(join)
                for field in ("build_time_ms", "join_time_ms"):
                    arm_summary["median_" + field] = statistics.median(join[field] for join in joins)
                for field in ("build_input_rows", "input_rows", "array_map_created_count"):
                    arm_summary[field] = sorted({join[field] for join in joins if field in join})
            result[query][arm] = arm_summary
        whole[run] = result
    print(json.dumps(whole, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
