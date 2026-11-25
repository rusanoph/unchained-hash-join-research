"""Cross-check official hj SQL with one separate full-stream multiset digest per arm."""

import argparse
import json
from pathlib import Path
import re

from digests import sha256
from run import encode, execute, replace, signature_env


def queries(tree):
    path = tree / "benchmarks/src/hj.rs"
    text = path.read_text()
    return path, re.findall(
        r'sql:\s*r###"(.*?)"###,.*?isolate_partitioned_join:\s*(true|false)',
        text, re.S,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("sf10_parquet", type=Path)
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--address-space-gib", type=int, default=10)
    parser.add_argument("--pool-mib", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    root = args.root.resolve()
    folder = root / "runs/hj-correctness-sf10-001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plans").mkdir(exist_ok=True)
    path, definitions = queries(root / "source-B")
    assert len(definitions) >= 28
    assert definitions == queries(root / "source-A")[1] == queries(root / "source-C")[1]
    ids = (2, 4, 5, 23, 26, 28)
    arms = ("A", "B", "C")
    data = args.sf10_parquet.resolve(strict=True)
    binaries = {arm: root / "bin" / f"worker-{arm}" for arm in arms}
    config = {
        "cpu": [args.cpu], "partitions": 1, "batch_size": 8192, "warmups": 0, "timeout_seconds": args.timeout,
        "address_space_gib": args.address_space_gib, "memory_limit_mb": args.pool_mib, "data": {"sf10": str(data)},
        "settings": ["datafusion.optimizer.join_reordering=false"],
    }
    receipt = {"config": config, "queries": ids, "environment": signature_env(config["cpu"]),
               "source_sha256": sha256(path), "driver_sha256": sha256(Path(__file__)),
               "worker_sha256": {arm: sha256(path) for arm, path in binaries.items()},
               "parquet_sha256": {file.name: sha256(file) for file in sorted(data.glob("*.parquet"))}}
    registered = folder / "registration.json"
    content = encode(receipt) + b"\n"
    if registered.exists():
        assert registered.read_bytes() == content
    else:
        replace(registered, content)
    summary = {}
    for index in ids:
        sql, threshold = definitions[index - 1]
        case = {"id": f"q{index}", "sql": sql, "data": "sf10", "mode": "direct", "exact": False}
        if threshold == "true":
            case["settings"] = [
                "datafusion.optimizer.hash_join_single_partition_threshold=0",
                "datafusion.optimizer.hash_join_single_partition_threshold_rows=0",
            ]
        results = {}
        for arm in arms:
            file = folder / f"q{index}-{arm}.json"
            if file.exists():
                sample = json.loads(file.read_bytes())
            else:
                sample = execute(config, folder, binaries[arm], case)
                replace(file, encode(sample) + b"\n")
            results[arm] = sample
        ok = all(r["status"] == "succeeded" for r in results.values())
        compatible = ok and all(
            all(result[k] == results["A"][k] for k in ("schema", "rows", "digest"))
            for result in results.values()
        )
        summary[index] = {
            "match": compatible, "statuses": {arm: result["status"] for arm, result in results.items()},
            "rows": results["A"].get("rows"),
        }
        print(f"Q{index} {summary[index]}", flush=True)
    replace(folder / "summary.json", encode(summary) + b"\n")
    if not all(r["match"] for r in summary.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
