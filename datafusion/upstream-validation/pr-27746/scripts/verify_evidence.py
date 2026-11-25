"""Verify the published evidence using only the Python standard library."""

import collections
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from digests import sha256
from summarize import summarize

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return list(map(json.loads, stream))


def script(name, *args):
    return subprocess.check_output([sys.executable, ROOT / "scripts" / name, *args])


def verify_checksums():
    listed = set()
    for line in (ROOT / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT) and name not in listed, name)
        require(sha256(path) == expected, name)
        listed.add(name)
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()
              and "__pycache__" not in p.parts and p.name != "SHA256SUMS"}
    require(actual == listed, "Checksum inventory differs from package")


def provenance():
    upstream = read(ROOT / "MANIFEST.json")["upstream"]
    require(upstream["arm_a"] == "6a060586ed85d2ff71837b6d818b7eddc075b699"
            and upstream["arm_b"] == "2f6c665fef63936f08a4aa7a4fb01b87a4d2b009", "A/B source pins")
    require(upstream["arm_c_base"] == upstream["arm_b"], "C base")
    require(sha256(ROOT / upstream["arm_c_patch"]) ==
            "13da9749d8003238e2d3b87ad8cc0aa40bd3c42d17221f067133137ecd57283e", "review patch")
    worker, diagnostic, official = {}, {}, {}
    instrumented = hashlib.sha256(script("instrument_peak.py", ROOT / "worker/src/main.rs")).hexdigest()
    for arm in "ABC":
        src = upstream["arm_" + arm.lower() + ("_base" if arm == "C" else "")]
        for group, name, binary in (
            (worker, f"worker-{arm}.provenance.txt", f"bin/worker-{arm}"),
            (diagnostic, f"worker-{arm}.memory-provenance.txt", f"memory-bin/worker-{arm}"),
            (official, f"dfbench-{arm}.log", f"hj-bin/dfbench-{arm}"),
        ):
            lines = (ROOT / "environment" / name).read_text().splitlines()
            require(lines[0] == src, name)
            hashes = dict((path, sha) for sha, path in
                          re.findall(r"^([0-9a-f]{64})  (.+)$", "\n".join(lines), re.M))
            group[arm], = [sha for path, sha in hashes.items() if path.endswith("/" + binary)]
            if group is worker:
                require(sha256(ROOT / "worker/src/main.rs") in hashes.values(), "worker source")
                require(sha256(ROOT / f"environment/worker-{arm}.Cargo.lock") in hashes.values(), "worker lock")
            if group is diagnostic:
                require(instrumented in hashes.values(), "diagnostic worker source")
    require(all(len(set(group.values())) == 3 for group in (worker, diagnostic, official)),
            "Executable digests must be distinct in each three-arm comparison")
    return worker, diagnostic, official


def main():
    verify_checksums()
    worker, diagnostic, official = provenance()
    receipt = read(ROOT / "workloads/input-receipt.json")
    counts = {"pilot-001": 93, "snap-001": 954, "job-001": 964, "job-22d-001": 2,
              "memory-unbounded-001": 90, "memory-64mib-001": 90}
    require({p.name for p in (RESULTS / "raw").glob("*.jsonl.gz")} ==
            {name + ".jsonl.gz" for name in (*counts, "hj-official-sf10-002")}, "Accepted run inventory")
    with tempfile.TemporaryDirectory() as tmp:
        for name, total in counts.items():
            run = Path(tmp) / name
            script("restore.py", RESULTS, name, run)
            reg = read(run / "registration.json")
            for key, recorded in (("graphs", "graph"), ("holdout", "holdout"), ("job", "job")):
                if key in reg["inputs"]:
                    require(reg["inputs"][key] == receipt[recorded + "_parquet_sha256"], name + " input receipts")
            bins = diagnostic if name.startswith("memory") else worker
            require(all(bins["A" if arm == "AA" else arm] == sha
                        for arm, sha in reg["binaries"].items()), name + " provenance")
            observed = list((run / "observations").glob("*.json"))
            require(len(observed) == len(reg["slots"]) == total, name + " coverage")
            status = collections.Counter(read(p)["status"] for p in observed)
            expected = {"succeeded": 55, "resource_exhausted": 35} if name == "memory-64mib-001" else {"succeeded": total}
            require(status == expected, name + " execution status")
            pairs = [("A", "B")]
            if "AA" in reg["binaries"]:
                pairs.append(("A", "AA"))
            if "C" in reg["binaries"]:
                pairs += [("A", "C"), ("B", "C")]
            for a, b in pairs:
                summary = summarize(run, a, b, 0.24 if name.startswith(("snap", "job")) else 0.05, {})
                key = f"{name}-{a}-{b}.json"
                rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
                require(rendered.encode() == (RESULTS / "summaries" / key).read_bytes(), key)
                checks = [{"id": row["id"], "suite": row["suite"], "exact_oracle": row["oracle"],
                           "status": row["correctness"], "arms": row["statuses"]} for row in summary["queries"]]
                require(checks == read(RESULTS / "correctness" / key)
                        and all(row["status"] != "mismatch" for row in checks), key + " correctness")
            print(f"{name}: {total} verified slots")
    legacy = read(RESULTS / "correctness/job-001-legacy.json")["job-001.jsonl.gz"]
    job_counts = collections.Counter(r["query"] for r in rows(RESULTS / "raw/job-001.jsonl.gz"))
    require(legacy == {q: {"matched": n} for q, n in job_counts.items()}, "Legacy JOB coverage")
    memory = script("pool_summary.py", RESULTS, "memory-unbounded-001", "memory-64mib-001")
    require(json.loads(memory) == read(RESULTS / "summaries/memory-controlled.json"), "Memory summary")
    invalid = read(RESULTS / "invalid-hj-001/hj-official-sf10-001-registration.json")
    require(len(set(invalid["binaries"].values())) == 1, "Excluded attempt record")
    reg = RESULTS / "raw/hj-official-sf10-002-registration.json"
    raw = RESULTS / "raw/hj-official-sf10-002.jsonl.gz"
    require(read(reg)["binaries"] == official, "Official hj provenance")
    hj = script("hj_summary.py", reg, "--archive", raw)
    require(hj.replace(b"\r\n", b"\n") == (RESULTS / "summaries/hj-official-sf10-002.json").read_bytes(),
            "hj summary")
    observed = rows(raw)
    require(len(observed) == len(read(reg)["slots"]) == 90 and all(
        r["returncode"] == 0 and len(r["dfbench"]["queries"][0]["iterations"]) == 3
        and r["dfbench"]["context"]["num_cpus"] == 1 for r in observed), "hj coverage")
    check = read(RESULTS / "correctness/hj-sf10-registration.json")
    require(check["worker_sha256"] == worker, "hj correctness provenance")
    evidence = {row["case"]: row for row in rows(RESULTS / "correctness/hj-sf10-digests.jsonl.gz")}
    require(len(evidence) == 18, "hj correctness coverage")
    plans = rows(RESULTS / "plans/hj-sf10-diagnostics.jsonl.gz")
    known = {p["sha256"] for p in plans}
    require(len(known) == len(plans) and all(hashlib.sha256(p["text"].encode()).hexdigest() == p["sha256"]
                                            for p in plans), "hj diagnostic plan integrity")
    require(all(r[key] in known for r in evidence.values() for key in ("plan_sha256", "metrics_sha256")),
            "hj diagnostic plan references")
    published = read(RESULTS / "correctness/hj-sf10-cross-check.json")
    for query, report in json.loads(hj)["queries"].items():
        matches = [evidence[f"q{query}-{arm}"] for arm in "ABC"]
        require(all(r["status"] == "succeeded" for r in matches) and
                all(len({json.dumps(r[k]) for r in matches}) == 1 for k in ("digest", "schema", "rows"))
                and report["output_counts"] == [matches[0]["rows"]]
                and published[query]["match"] and published[query]["row_counts_match_official"]
                and published[query]["rows"] == matches[0]["rows"], "hj Q" + query)
    print("Accepted hj: 90 slots, 270 iterations, 18 independent fingerprints. Invalid hj excluded.")
    print("Checksums, coverage, provenance and published summaries verified.")


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("Run without Python optimization")
    main()
