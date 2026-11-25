"""Run a registered, resumable sequence with one isolated process per observation."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import signal
import subprocess
import tempfile
import time

from digests import sha256

PACKAGE = Path(__file__).resolve().parents[1]


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def fingerprint(content):
    return hashlib.sha256(content).hexdigest()


def replace(path, content):
    pending = path.with_suffix(path.suffix + ".partial")
    with pending.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def plan(config, queries):
    rng = random.Random(config["seed"])
    arms = config["arms"]
    order = [(query, (i + rng.randrange(len(arms))) % len(arms))
             for i, query in enumerate(sorted(queries))]
    for block in range(config["blocks"]):
        rng.shuffle(order)
        for query, offset in order:
            if block >= config.get("block_caps", {}).get(query, config["blocks"]):
                continue
            shift = (offset + block) % len(arms)
            for arm in arms[shift:] + arms[:shift]:
                yield (block, query, arm)


def signature_env(cpu_mask):
    cpuinfo = Path("/proc/cpuinfo").read_text().splitlines()
    cpu = next(line.split(":", 1)[1].strip() for line in cpuinfo if line.startswith("model name"))
    allowed = set(os.sched_getaffinity(0))
    assert set(cpu_mask) <= allowed, (cpu_mask, allowed)
    return {"kernel": platform.release(), "arch": platform.machine(), "cpu": cpu,
            "libc": platform.libc_ver(), "affinity": cpu_mask,
            "thp": Path("/sys/kernel/mm/transparent_hugepage/enabled").read_text().strip()}


def execute(config, directory, worker, query):
    with tempfile.TemporaryDirectory(prefix="df-observation-") as temporary:
        scratch = Path(temporary)
        sql = scratch / "stmt.sql"
        sql.write_text(query["sql"], encoding="utf-8")
        args = [str(worker), "--sql-file", str(sql), "--target-partitions", str(config["partitions"]),
                "--batch-size", str(config["batch_size"]), "--warmups", str(config["warmups"]),
                "--repetitions", "1", "--explain", "true"]
        result = scratch / "rows.jsonl"
        if query.get("exact", True):
            args += ["--result-file", str(result)]
        if query.get("setup"):
            setup = scratch / "setup.sql"
            setup.write_text(query["setup"], encoding="utf-8")
            args += ["--setup-sql", str(setup)]
        if query.get("data"):
            args += ["--data", config["data"][query["data"]]]
            if query["mode"] == "prune":
                args += ["--prune-columns", "true"]
            if query["mode"] == "direct":
                args += ["--parquet-direct", "true"]
        if config.get("memory_limit_mb"):
            args += ["--memory-limit-mb", str(config["memory_limit_mb"])]
        for setting in config.get("settings", []) + query.get("settings", []):
            args += ["--set", setting]
        rss = scratch / "rss"
        mask = ",".join(str(cpu) for cpu in config["cpu"])
        command = ["taskset", "-c", mask, "prlimit", "--core=0",
                   "--as=" + str(config["address_space_gib"] << 30),
                   "/usr/bin/time", "-f", "%M %U %S", "-o", str(rss), *args]
        before = time.monotonic_ns()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=config["timeout_seconds"])
                status = "succeeded" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                status = "timeout"
        observation = {"status": status, "returncode": process.returncode,
                       "process_wall_ns": time.monotonic_ns() - before,
                       "stderr": stderr.decode(errors="replace")[-8000:]}
        if rss.exists():
            fields = rss.read_text().splitlines()
            if fields and re.fullmatch(r"\d+ \d+\.\d+ \d+\.\d+", fields[-1]):
                peak, user, system = fields[-1].split()
                observation.update(peak_rss_kib=int(peak), user_cpu_s=float(user), system_cpu_s=float(system))
        if status != "succeeded":
            if status == "failed":
                if re.search(r"ResourcesExhausted|resources exhausted|resource exhausted", observation["stderr"], re.I):
                    observation["status"] = "resource_exhausted"
                elif re.search(r"memory allocation .* failed|out of memory", observation["stderr"], re.I):
                    observation["status"] = "allocation_failed"
                elif process.returncode in (137, -9):
                    observation["status"] = "killed"
            return observation
        try:
            output = json.loads(stdout)
            assert output["schema_version"] == 2
            sample, = output.pop("samples")
            texts = {"plan": output.pop("physical_plan"), "metrics": sample.pop("plan_with_metrics")}
            for label, value in texts.items():
                content = value.encode()
                digest = fingerprint(content)
                target = directory / "plans" / (digest + ".txt")
                if not target.exists():
                    replace(target, content)
                observation[label + "_sha256"] = digest
            if result.exists():
                exact = [
                    json.loads(line) for line in sorted(result.read_text().splitlines())
                ]
                assert len(exact) == sample["rows"]
                observation["result"] = exact
                if "oracle" in query:
                    observation["oracle_match"] = exact == query["oracle"]
            observation.update(output, **sample)
        except (ValueError, AssertionError, KeyError, TypeError) as exc:
            observation.update(status="invalid_worker_output", output_error=str(exc),
                               stdout=stdout.decode(errors="replace")[-3000:])
        return observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--regenerated-inputs", action="store_true",
                        help="Register a new cohort using regenerated Parquet bytes")
    args = parser.parse_args()
    config = json.loads(args.configuration.read_text(encoding="utf-8-sig"))
    if args.regenerated_inputs:
        config["input_identity"] = "regenerated"
    historical = json.loads((PACKAGE / "workloads/historical.json").read_text(encoding="utf-8-sig"))
    oracle = json.loads((PACKAGE / "workloads/graph-oracle.json").read_bytes())
    receipt = json.loads((PACKAGE / "workloads/input-receipt.json").read_bytes())
    chosen = set(config.get("queries", []))
    suites = set(config.get("suites", []))
    queries = {q["id"]: q for q in historical["queries"] if q["id"] in chosen or q["suite"] in suites}
    queries.update({q["id"]: q for q in config.get("custom", [])})
    assert queries and chosen <= queries.keys()
    assert set(config.get("block_caps", {})) <= queries.keys()
    assert all(0 < cap <= config["blocks"] for cap in config.get("block_caps", {}).values())
    for key, query in queries.items():
        if key in oracle["queries"]:
            query["oracle"] = oracle["queries"][key]["expected"]
    bins = {arm: (args.bin / ("worker-" + ("A" if arm == "AA" else arm))).resolve(strict=True)
            for arm in config["arms"]}
    data = {q["data"] for q in queries.values() if q.get("data")}
    config["data"] = {key: str(getattr(args, key).resolve(strict=True)) for key in sorted(data)}
    args.directory.mkdir(parents=True, exist_ok=True)
    slots = list(plan(config, queries))
    with (args.directory / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        register = {"config": config, "environment": signature_env(config["cpu"]),
                    "binaries": {arm: sha256(path) for arm, path in bins.items()},
                    "inputs": {key: {file.name: sha256(file) for file in sorted(Path(path).glob("*.parquet"))}
                               for key, path in config["data"].items()},
                    "driver_sha256": sha256(Path(__file__)), "queries": queries, "slots": slots}
        for key, recorded in (("graphs", "graph"), ("holdout", "holdout"), ("job", "job")):
            if key in register["inputs"] and not args.regenerated_inputs:
                assert register["inputs"][key] == receipt[recorded + "_parquet_sha256"], (
                    key + " Parquet differs from the input receipt"
                )
        for query in queries.values():
            if query["id"] in oracle["queries"] and not args.regenerated_inputs:
                name = oracle["queries"][query["id"]]["table"]
                assert register["inputs"][query["data"]][name + ".parquet"] == oracle["inputs"][name]
        stored = args.directory / "registration.json"
        registration = encode(register) + b"\n"
        if stored.exists():
            if stored.read_bytes() != registration:
                raise SystemExit("Registration changed. Use a different campaign directory")
        else:
            replace(stored, registration)
        print(f"Registration {fingerprint(registration)} has {len(slots)} slots", flush=True)
        observations = args.directory / "observations"
        observations.mkdir(exist_ok=True)
        (args.directory / "plans").mkdir(exist_ok=True)
        for index, (block, query, arm) in enumerate(slots):
            path = observations / f"{index:06d}.json"
            if path.exists():
                existing = json.loads(path.read_bytes())
                assert (existing["block"], existing["query"], existing["arm"]) == (block, query, arm)
                continue
            print(f"{index + 1}/{len(slots)} {query} block={block} arm={arm}", flush=True)
            started = time.time_ns()
            observation = execute(config, args.directory, bins[arm], queries[query])
            observation.update(index=index, block=block, query=query, arm=arm, started_utc_ns=started,
                               loadavg=os.getloadavg())
            replace(path, encode(observation) + b"\n")
            print(f"  {observation['status']} {observation.get('statement_wall_ns', '-')}", flush=True)


if __name__ == "__main__":
    main()
