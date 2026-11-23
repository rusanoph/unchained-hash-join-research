"""Check result parity and the executed join index on small synthetic queries."""

import argparse
import json
import subprocess
from pathlib import Path

QUERY = "SELECT count(*), sum(b.p1), sum(p.v) FROM p JOIN b ON p.k = b.k"
SETTING = "datafusion.execution."


def execute(worker, key_type, build_rows, stride, mode, expected_index, reason=None):
    data = {"build_rows": build_rows, "probe_rows": build_rows * 2, "mult": 8,
            "order": "shuffled", "key_type": key_type, "key_stride": stride}
    command = [str(worker), "--synthetic", json.dumps(data), "--sql", QUERY, "--warmups", "0",
               "--repetitions", "1", "--target-partitions", "1",
               "--set", f"{SETTING}hashman_join={mode}"]
    report = json.loads(subprocess.check_output(command, text=True, timeout=120))
    sample = report["samples"][0]
    builds = sample["hashman"]["builds"]
    if (report["effective"]["hashman_join"] != mode
            or [(b["kind"], b["build_rows"]) for b in builds] != [(expected_index, build_rows)]
            or (reason and builds[0]["reason"] != reason) or sample["rows"] != 1):
        raise SystemExit(f"Unexpected {key_type} index or output in {mode}: {builds}")
    return sample["digest"]


def smoke(worker):
    for key_type, stride, native, csr in (("int64", 1, "native_array", "csr_exact"),
                                         ("utf8", 1000, "native_hash", "csr_hash")):
        expected = execute(worker, key_type, 250000, stride, "native", native)
        for mode in ("unchained", "auto"):
            if execute(worker, key_type, 250000, stride, mode, csr) != expected:
                raise SystemExit(f"Result differs for {key_type} in {mode}")
        print(f"{key_type}: native, unchained and auto agree; {csr} executed.")
    native = execute(worker, "int64", 10000, 1, "native", "native_array")
    auto = execute(worker, "int64", 10000, 1, "auto", "native_array", "below_min_rows")
    if native != auto:
        raise SystemExit("Result differs for the small-build fallback")
    print("Small build: auto retained the native index and returned the same result.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker", type=Path)
    smoke(parser.parse_args().worker.resolve())
