"""Recompute SNAP order and holdout subgroup results from the per-query paired evidence."""

import argparse
import json
from pathlib import Path

from summarize import geometric


def aggregate(rows):
    a = sum(q["A_median_ms"] for q in rows)
    b = sum(q["B_median_ms"] for q in rows)
    return {"n": len(rows), "sum_a_ms": a, "sum_b_ms": b, "reduction_pct": 100 * (1 - b / a),
            "geomean_a_over_b": geometric(q["A_median_ms"] / q["B_median_ms"] for q in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    result = json.loads(args.summary.read_bytes())
    assert result["observed_slots"] == result["total_slots"]
    rows = {q["id"]: q for q in result["queries"]}
    assert all(q["correctness"] == "consistent" and q["paired_blocks"] == q["intended_blocks"]
               for q in rows.values())
    original = [q for q in rows.values() if q["suite"] == "snap"]
    holdout = [q for q in rows.values() if q["suite"] == "snap_holdout"]
    subgroups = {"original_delivered": [q for q in original if "_sh_" not in q["id"]],
                 "original_permuted": [q for q in original if "_sh_" in q["id"]],
                 "holdout_enron_hepph": [q for q in holdout if not q["id"].startswith("amazon_")],
                 "holdout_amazon0302": [q for q in holdout if q["id"].startswith("amazon_")]}
    paired_order = {}
    for key, q in rows.items():
        if "_sh_" in key:
            original_key = key.replace("_sh_", "_")
        elif "_shuffle_" in key:
            original_key = key.replace("_shuffle_", "_file_")
        else:
            continue
        orig = rows[original_key]
        paired_order[original_key] = {
            "permuted": key,
            "delivered_paired_a_over_b": orig["paired_median_ratio"],
            "permuted_paired_a_over_b": q["paired_median_ratio"],
            "a_permuted_over_delivered": q["A_median_ms"] / orig["A_median_ms"],
            "b_permuted_over_delivered": q["B_median_ms"] / orig["B_median_ms"],
        }
    print(json.dumps({"groups": {group: aggregate(queries) for group, queries in subgroups.items()},
                      "matched_order": paired_order}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
