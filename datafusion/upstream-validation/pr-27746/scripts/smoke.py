"""Run one Enron query and two synthetic join checks using A/B workers."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from summarize import summarize

HERE = Path(__file__).resolve().parent


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("scratch", type=Path)
    cli.add_argument("--bin", required=True, type=Path)
    cli.add_argument("--duckdb", required=True, type=Path)
    cli.add_argument("--sources", required=True, type=Path)
    cli.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    cli.add_argument("--address-space-gib", type=int, default=4)
    args = cli.parse_args()
    args.scratch.mkdir(parents=True)
    enron = args.scratch / "enron"
    subprocess.run([sys.executable, HERE / "prepare_inputs.py", "enron", "--duckdb", args.duckdb,
                    "--sources", args.sources, "--out", enron], check=True)
    config = json.loads(subprocess.check_output([sys.executable, HERE / "memory_cases.py", "--arms", "A", "B"]))
    config.update(queries=["enron_file_out_star"], cpu=[args.cpu], blocks=1,
                  address_space_gib=args.address_space_gib)
    config["custom"] = [q for q in config["custom"] if q["id"] in ("dense_dup100", "sparse_int_unique")]
    config_path = args.scratch / "smoke.json"
    config_path.write_text(json.dumps(config))
    run = args.scratch / "run"
    subprocess.run([sys.executable, HERE / "run.py", config_path, run, "--bin", args.bin,
                    "--graphs", enron, "--holdout", enron, "--job", enron, "--regenerated-inputs"], check=True)
    observed = summarize(run, "A", "B", 0.05, {})
    if len(observed["queries"]) != 3 or any(q["correctness"] != "consistent" or q["paired_blocks"] != 1
                                            for q in observed["queries"]):
        raise SystemExit("Smoke failed")
    print("Smoke verified three exact result multisets on both arms.")


if __name__ == "__main__":
    main()
