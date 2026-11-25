"""Run official dfbench hj SF10 cases in fixed interleaved blocks."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from digests import sha256
from run import encode, plan, replace, signature_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("sf10_parquet", type=Path)
    parser.add_argument("--blocks", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--run", default="hj-official-sf10-002")
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--address-space-gib", type=int, default=10)
    parser.add_argument("--pool-limit", default="8G")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--regenerated-inputs", action="store_true")
    args = parser.parse_args()
    assert Path(args.run).name == args.run
    root = args.root.resolve()
    data = root / "hj-sf10-data"
    data.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in ("lineitem", "orders", "supplier", "nation", "customer"):
        src = (args.sf10_parquet / f"{table}.parquet").resolve(strict=True)
        table_dir = data / table
        table_dir.mkdir(exist_ok=True)
        link = table_dir / src.name
        if not link.exists():
            link.symlink_to(src)
        assert link.resolve() == src
        files[table] = sha256(src)
    arms = ["A", "B", "C"]
    recorded = Path(__file__).resolve().parents[1] / "results/raw/hj-official-sf10-002-registration.json"
    if not args.regenerated_inputs and json.loads(recorded.read_bytes())["inputs"] != files:
        raise SystemExit("SF10 bytes differ from the recorded inputs. Use --regenerated-inputs for a new cohort.")
    bins = {arm: root / "hj-bin" / f"dfbench-{arm}" for arm in arms}
    cfg = {"arms": arms, "blocks": args.blocks, "seed": 27746006, "iterations": args.iterations,
           "cpu": [args.cpu], "partitions": 1, "batch_size": 8192,
           "address_space_gib": args.address_space_gib, "mem_limit": args.pool_limit,
           "timeout_seconds": args.timeout}
    if args.regenerated_inputs:
        cfg["input_identity"] = "regenerated"
    slots = list(plan(cfg, [2, 4, 5, 23, 26, 28]))
    binaries = {k: sha256(v) for k, v in bins.items()}
    if len(set(binaries.values())) != len(arms):
        raise SystemExit(f"Expected three distinct official executables, got {binaries}")
    experiment = root / "runs" / args.run
    experiment.mkdir(parents=True, exist_ok=True)
    registration = encode({"config": cfg, "slots": slots, "inputs": files, "data": str(data),
                           "environment": signature_env(cfg["cpu"]), "driver_sha256": sha256(Path(__file__)),
                           "binaries": binaries}) + b"\n"
    with (experiment / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reg_file = experiment / "registration.json"
        if reg_file.exists():
            assert reg_file.read_bytes() == registration, "Use a different run directory"
        else:
            replace(reg_file, registration)
        custom_env = {k: v for k, v in os.environ.items()
                      if not k.startswith("DATAFUSION_") and k not in
                      {"ITERATIONS", "MEMORY_LIMIT", "MEM_POOL_TYPE", "DEBUG", "SIMULATE_LATENCY"}}
        for index, (block, query, arm) in enumerate(slots):
            record = experiment / f"{index:04d}.json"
            if record.exists():
                saved = json.loads(record.read_bytes())
                assert (saved["block"], saved["query"], saved["arm"]) == (block, query, arm)
                continue
            with tempfile.TemporaryDirectory(prefix="hj-sf10-") as temp:
                rss = Path(temp) / "time.txt"
                output = Path(temp) / "dfbench.json"
                cmd = ["taskset", "-c", str(args.cpu), "prlimit", "--core=0",
                       "--as=" + str(cfg["address_space_gib"] << 30),
                       "timeout", "--kill-after=5s", str(cfg["timeout_seconds"]),
                       "/usr/bin/time", "-f", "%M %U %S", "-o", str(rss),
                       str(bins[arm]), "hj", "-q", str(query), "-p", str(data),
                       "-n", "1", "-s", "8192", "-i", str(args.iterations),
                       "--memory-limit", cfg["mem_limit"], "-o", str(output)]
                start = time.monotonic_ns()
                proc = subprocess.run(cmd, env=custom_env, capture_output=True, text=True, check=False)
                result = {"index": index, "block": block, "query": query, "arm": arm,
                          "returncode": proc.returncode, "process_wall_ns": time.monotonic_ns() - start,
                          "stdout": proc.stdout, "stderr": proc.stderr}
                if rss.is_file():
                    result["gnu_time"] = rss.read_text()
                if output.is_file():
                    result["dfbench"] = json.loads(output.read_bytes())
                replace(record, encode(result) + b"\n")
                print(f"{index + 1}/{len(slots)} Q{query} {arm} returncode={proc.returncode}", flush=True)


if __name__ == "__main__":
    main()
