"""Verify a portable evidence bundle and restore its run-directory layout."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from evidence.archives import read_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("run")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    assert Path(args.run).name == args.run
    raw = args.results / "raw"
    registration = raw / f"{args.run}-registration.json"
    slots = json.loads(registration.read_bytes())["slots"]
    observations = list(read_rows(raw / f"{args.run}.jsonl.gz"))
    plans = {}
    for plan in read_rows(args.results / "plans" / f"{args.run}.jsonl.gz"):
        sha = plan["sha256"]
        text = plan["text"].encode()
        assert re.fullmatch(r"[a-f0-9]{64}", sha)
        assert hashlib.sha256(text).hexdigest() == sha and sha not in plans
        plans[sha] = text
    indices = set()
    for observation in observations:
        index = observation["index"]
        assert isinstance(index, int) and 0 <= index < len(slots) and index not in indices
        indices.add(index)
        assert slots[index] == [observation[key] for key in ("block", "query", "arm")]
        for key in ("plan_sha256", "metrics_sha256"):
            if key in observation:
                assert observation[key] in plans
    out = args.destination
    out.mkdir(parents=True)
    (out / "observations").mkdir()
    (out / "plans").mkdir()
    shutil.copyfile(registration, out / "registration.json")
    for sha, plan in plans.items():
        (out / "plans" / (sha + ".txt")).write_bytes(plan)
    for observation in observations:
        (out / "observations" / f"{observation['index']:06d}.json").write_text(
            json.dumps(observation, sort_keys=True), encoding="utf-8"
        )
    print(f"Verified {len(observations)} observations, {len(slots)} registered slots and {len(plans)} plans")


if __name__ == "__main__":
    main()
