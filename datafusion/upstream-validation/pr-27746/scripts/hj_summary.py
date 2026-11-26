"""Summarize matched official hj observations and preserve row counts and resource failures."""

import argparse
import collections
import json
from pathlib import Path
import random
import statistics

from evidence.archives import read_rows
from summarize import interval


def iter_observations(path, archive):
    if archive is not None:
        for row in read_rows(archive):
            yield row["index"], row
    else:
        for file in sorted(path.glob("[0-9][0-9][0-9][0-9].json")):
            yield int(file.stem), json.loads(file.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Run directory, or registration JSON with --archive")
    parser.add_argument("--archive", type=Path, help="Exported gzip JSON lines")
    args = parser.parse_args()
    registration = args.path if args.archive else args.path / "registration.json"
    reg = json.loads(registration.read_bytes())
    if len(set(reg["binaries"].values())) != len(reg["config"]["arms"]):
        raise SystemExit("Invalid comparison: the registered executable digests are not distinct")
    good = collections.defaultdict(dict)
    statuses = collections.defaultdict(collections.Counter)
    counts = collections.defaultdict(set)
    observed = set()
    for index, row in iter_observations(args.path, args.archive):
        assert 0 <= index < len(reg["slots"]) and index not in observed
        block, query, arm = reg["slots"][row["index"]]
        assert (row["block"], row["query"], row["arm"]) == (block, query, arm)
        assert row["index"] == index
        observed.add(index)
        if row["returncode"] != 0 or "dfbench" not in row:
            status = "timeout" if row["returncode"] == 124 else f"exit_{row['returncode']}"
            statuses[query][f"{arm}/{status}"] += 1
            continue
        result, = row["dfbench"]["queries"]
        if not result["success"] or len(result["iterations"]) != reg["config"]["iterations"]:
            statuses[query][f"{arm}/invalid_iterations"] += 1
            continue
        statuses[query][f"{arm}/succeeded"] += 1
        counts[query].update(iteration["row_count"] for iteration in result["iterations"])
        gnu_time = row.get("gnu_time", "").splitlines()
        peak = int(gnu_time[-1].split()[0]) if gnu_time else None
        good[query][block, arm] = {
            "median_ms": statistics.median(it["elapsed"] for it in result["iterations"]),
            "peak_rss_kib": peak,
            "pool_peak_bytes": result.get("pool_peak_bytes"),
        }
    output = {}
    rng = random.Random(27746)
    for query in sorted({q for _, q, _ in reg["slots"]}):
        per_query = {"status": dict(statuses[query]), "output_counts": sorted(counts[query])}
        if len(counts[query]) == 1:
            observations = good[query]
            comparisons = {}
            for left, right in (("A", "B"), ("B", "C"), ("A", "C")):
                blocks = [block for block in range(reg["config"]["blocks"])
                          if all((block, arm) in observations for arm in (left, right))]
                if not blocks:
                    continue
                a, b = [[observations[block, arm]["median_ms"] for block in blocks] for arm in (left, right)]
                ratios = [x / y for x, y in zip(a, b)]
                comparisons[left + "/" + right] = {
                    "pairs": len(blocks), "medians_ms": {left: statistics.median(a), right: statistics.median(b)},
                    "ratio": statistics.median(ratios), "pointwise_95": interval(ratios, rng),
                }
            per_query["comparisons"] = comparisons
            per_query["memory"] = {}
            for arm in reg["config"]["arms"]:
                per_query["memory"][arm] = {
                    metric: statistics.median(values) for metric in ("peak_rss_kib", "pool_peak_bytes")
                    if (values := [value[metric] for (_, label), value in observations.items()
                                   if label == arm and value[metric] is not None])
                }
        output[query] = per_query
    print(json.dumps({"observed_slots": len(observed), "registered_slots": len(reg["slots"]),
                      "queries": output}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
