"""Audit coverage, correctness and paired timing without imputing missing observations."""

import argparse
import collections
import csv
import json
import math
from pathlib import Path
import random
import statistics as stats

from digests import sha256


def quantiles(values):
    if not values:
        return None
    values.sort()
    return [values[round(q * (len(values) - 1))] for q in (0.025, 0.975)]


def interval(values, rng, iterations=4000):
    return quantiles([stats.median(rng.choices(values, k=len(values)))
                      for _ in range(iterations)]) if len(values) >= 3 else None


def compatible(value, reference):
    return all(value.get(k) == reference.get(k) for k in ("schema", "rows", "digest", "result"))


def geometric(values):
    return math.exp(stats.mean(math.log(x) for x in values))


def summarize(folder, baseline, candidate, margin, oracle, paths=None):
    registered = json.loads((folder / "registration.json").read_bytes())
    arms = (baseline, candidate)
    slots = registered["slots"]
    observations = {}
    counts = collections.Counter()
    if paths is None:
        paths = sorted((folder / "observations").glob("*.json"))
    for path in paths:
        row = json.loads(path.read_bytes())
        key = tuple(slots[row["index"]])
        assert key == (row["block"], row["query"], row["arm"]) and key not in observations
        assert int(path.stem) == row["index"]
        for label in ("plan", "metrics"):
            if label + "_sha256" in row:
                digest = row[label + "_sha256"]
                assert sha256(folder / "plans" / (digest + ".txt")) == digest
        observations[key] = row
        counts[row["arm"], row["status"]] += 1
    expected = collections.Counter((q, arm) for _, q, arm in slots)
    for _, query, arm in slots:
        assert query in registered["queries"] and arm in registered["binaries"]
    rng = random.Random(27746)
    rows = []
    by_suite = collections.defaultdict(dict)
    for query, meta in sorted(registered["queries"].items()):
        suite = meta.get("suite", "smoke")
        successes = collections.defaultdict(dict)
        status = {arm: collections.Counter() for arm in registered["binaries"]}
        for block in range(registered["config"]["blocks"]):
            for arm in registered["binaries"]:
                value = observations.get((block, query, arm))
                if expected[query, arm] == 0:
                    continue
                status[arm][value["status"] if value else "pending"] += 1
                if value and value["status"] == "succeeded":
                    successes[arm][block] = value
        all_ok = [row for values in successes.values() for row in values.values()]
        oracle_meta = oracle.get(query)
        expected_rows = oracle_meta["expected"] if oracle_meta else meta.get("oracle")
        correctness = ("unverified" if not all_ok else "consistent")
        if (any(value.get("oracle_match") is False for value in all_ok)
                or (expected_rows is not None and any(value.get("result") != expected_rows for value in all_ok))
                or (all_ok and any(not compatible(value, all_ok[0]) for value in all_ok[1:]))):
            correctness = "mismatch"
        blocks = sorted(successes[baseline].keys() & successes[candidate].keys())
        obj = {"id": query, "suite": suite, "statuses": {arm: dict(counter) for arm, counter in status.items()},
               "correctness": correctness, "oracle": expected_rows is not None, "paired_blocks": len(blocks),
               "intended_blocks": expected[query, baseline]}
        if blocks and correctness == "consistent":
            aa, bb = [[successes[arm][block]["statement_wall_ns"] for block in blocks] for arm in arms]
            ratios = [a / b for a, b in zip(aa, bb)]
            ci = interval(ratios, rng)
            obj.update({baseline + "_median_ms": stats.median(aa) / 1e6,
                        candidate + "_median_ms": stats.median(bb) / 1e6,
                        "paired_median_ratio": stats.median(ratios), "pointwise_95": ci,
                        "plan_relation": "identical" if all(successes[baseline][block]["plan_sha256"] ==
                                                             successes[candidate][block]["plan_sha256"]
                                                             for block in blocks) else "text_diff"})
            for arm in arms:
                available = [successes[arm][block].get("peak_rss_kib") for block in blocks]
                if all(rss is not None for rss in available):
                    obj[arm + "_median_peak_rss_kib"] = stats.median(available)
            obj["decision"] = "inconclusive"
            if ci and len(blocks) == obj["intended_blocks"]:
                if ci[0] > 1 + margin:
                    obj["decision"] = "faster"
                elif ci[1] < 1 / (1 + margin):
                    obj["decision"] = "slower"
            by_suite[suite][query] = dict(zip(blocks, zip(aa, bb)))
        rows.append(obj)
    summaries = {}
    for suite in sorted({row["suite"] for row in rows}):
        paired = by_suite[suite]
        shared = set.intersection(*(set(values) for values in paired.values())) if paired else set()
        coverage = [row for row in rows if row["suite"] == suite]
        completed = [row for row in coverage if row["paired_blocks"] == row["intended_blocks"]
                     and row["correctness"] == "consistent"]
        result = {"planned_queries": len(coverage), "queries_with_pairs": len(paired),
                  "queries_with_full_pairs": len(completed), "shared_block_ids": sorted(shared),
                  "correctness_mismatches": [row["id"] for row in coverage if row["correctness"] == "mismatch"],
                  "decisions": collections.Counter(row.get("decision", "unclassified") for row in coverage)}
        if paired:
            med_a, med_b = [[stats.median(v[j] for v in values.values()) for values in paired.values()]
                            for j in (0, 1)]
            result.update(sum_a_ms=sum(med_a) / 1e6, sum_b_ms=sum(med_b) / 1e6,
                          reduction_pct=(1 - sum(med_b) / sum(med_a)) * 100,
                          geomean_median_ratio=geometric([a / b for a, b in zip(med_a, med_b)]))
        if len(shared) >= 3:
            eligible = sorted(shared)
            by_block = [sum(q[b][0] for q in paired.values()) / sum(q[b][1] for q in paired.values())
                        for b in eligible]
            result.update(paired_block_sum_median_ratio=stats.median(by_block),
                          paired_block_sum_pointwise_95=interval(by_block, rng))
        summaries[suite] = result
    return {"baseline": baseline, "candidate": candidate, "ratio_margin": margin,
            "total_slots": len(slots), "observed_slots": len(observations),
            "status": {f"{arm}/{status}": value for (arm, status), value in sorted(counts.items())},
            "suites": summaries, "queries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--baseline", default="A")
    parser.add_argument("--candidate", default="B")
    parser.add_argument("--margin", default=0.05, type=float)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    oracle = json.loads(args.oracle.read_bytes())["queries"] if args.oracle else {}
    out = summarize(args.folder, args.baseline, args.candidate, args.margin, oracle)
    if args.csv:
        cols = sorted({key for row in out["queries"] for key in row})
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=cols)
            writer.writeheader()
            for row in out["queries"]:
                writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                                 for key, value in row.items()})
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
