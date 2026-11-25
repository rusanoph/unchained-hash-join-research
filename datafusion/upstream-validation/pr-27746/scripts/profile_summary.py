"""Summarize operator timers for unchanged-plan, correctness-matched observations."""

import argparse
import collections
import gzip
import json
from pathlib import Path
import statistics

from join_metrics import parse


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("run")
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()
    plans = {row["sha256"]: row["text"] for row in rows(args.results / "plans" / f"{args.run}.jsonl.gz")}
    summary = json.loads((args.results / "summaries" / f"{args.run}-A-B.json").read_bytes())
    selected = {q["id"] for q in summary["queries"] if q["correctness"] == "consistent"
                and q.get("plan_relation") == "identical"}
    if args.queries:
        selected &= set(args.queries)
    related = collections.defaultdict(dict)
    for item in rows(args.results / "raw" / f"{args.run}.jsonl.gz"):
        if item["query"] in selected and item["arm"] in ("A", "B") and item["status"] == "succeeded":
            related[item["query"], item["block"]][item["arm"]] = item
    matched = collections.defaultdict(list)
    for (query, block), arms in related.items():
        if set(arms) != {"A", "B"}:
            continue
        left, right = (list(parse(plans[arms[arm]["metrics_sha256"]])) for arm in ("A", "B"))
        if [j["operator"] for j in left] != [j["operator"] for j in right]:
            continue
        for index, (a, b) in enumerate(zip(left, right)):
            matched[query, index].append((block, a, b))
    report = collections.defaultdict(list)
    for (query, index), group in sorted(matched.items()):
        detail = {"join": index, "operator": group[0][1]["operator"], "pairs": len(group)}
        for arm, position in (("A", 1), ("B", 2)):
            detail[arm] = {
                "available_metric_values": {
                    key: sorted({item[position][key] for item in group if key in item[position]})
                    for key in ("build_input_rows", "input_rows", "output_rows", "build_mem_used",
                                "array_map_created_count", "dense_index_created_count",
                                "probe_hit_rate", "avg_fanout")
                }
            }
            for key in ("build_time_ms", "join_time_ms"):
                available = [item[position][key] for item in group if key in item[position]]
                if available:
                    detail[arm][key] = {"median": statistics.median(available), "n": len(available)}
        report[query].append(detail)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
