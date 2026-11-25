"""Bind exact binary SNAP input statistics to the observed physical join orientation."""

import argparse
import gzip
import json
from pathlib import Path
import re

from join_metrics import parse

PACKAGE = Path(__file__).resolve().parents[1]


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    plans = {row["sha256"]: row["text"] for row in rows(args.results / "plans/snap-001.jsonl.gz")}
    oracle = json.loads((PACKAGE / "workloads/graph-oracle.json").read_bytes())["queries"]
    output = {}
    for row in rows(args.results / "raw/snap-001.jsonl.gz"):
        if row["block"] != 0:
            continue
        assert row["status"] == "succeeded" and row["oracle_match"]
        name = row["query"]
        metadata = oracle[name]
        entry = output.setdefault(name, {
            "order": "permuted" if "_sh_" in name or "_shuffle_" in name else "delivered",
            "query_output_count": int(metadata["expected"][0][0]), "operator_observations": {},
        })
        joins = list(parse(plans[row["metrics_sha256"]]))
        entry["operator_observations"][row["arm"]] = joins
        stats = metadata.get("frequencies")
        if stats is None:
            continue
        operator, = joins
        keys = re.search(r"on=\[\((src|dst)@\d+, (src|dst)@\d+\)\]", operator["operator"])
        assert keys, operator
        directed = (metadata["left_key"], metadata["right_key"])
        if keys.groups() == directed:
            build, probe = "left", "right"
        else:
            assert keys.groups() == directed[::-1], (name, keys.groups(), directed)
            build, probe = "right", "left"
        b, p = (int(stats[side + "_rows"]) for side in (build, probe))
        case = {
            "build_alias": "a" if build == "left" else "b",
            "build_rows": b,
            "probe_rows": p,
            "distinct_build": stats[build + "_distinct"],
            "distinct_probe": stats[probe + "_distinct"],
            "build_size_biased_multiplicity": stats[build + "_size_biased_multiplicity"],
            "probe_size_biased_multiplicity": stats[probe + "_size_biased_multiplicity"],
            "shared_keys": stats["shared_keys"],
            "frequency_overlap": stats["distribution_overlap"],
            "equal_key_pairs": int(stats["matches"]),
            "matches_per_probe": int(stats["matches"]) / p,
        }
        if "exact_binary_shape" in entry:
            assert entry["exact_binary_shape"] == case, name
        else:
            entry["exact_binary_shape"] = case
    assert set(output) == set(oracle) and all(set(q["operator_observations"]) == {"A", "B"} for q in output.values())
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
