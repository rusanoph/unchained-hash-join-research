"""Prepare a pinned DataFusion source tree and worker in a separate build directory."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "deps/datafusion.json"
PATCHER = ROOT / "scripts/patch_datafusion.py"
REPOSITORY = "https://github.com/apache/datafusion.git"
NOTICE = "Modified by the unchained-join-datafusion project to add a CSR hash join index."


def prepare(build, source):
    files = [PIN, PATCHER, Path(__file__), ROOT / "LICENSE", ROOT / "core/Cargo.toml",
             ROOT / "worker/Cargo.toml", ROOT / "worker/Cargo.lock",
             ROOT / "integrations/datafusion/hashman.rs"]
    files += sorted((ROOT / "core/src").rglob("*.rs"))
    files += sorted((ROOT / "worker/src").rglob("*.rs"))
    inputs = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    prepared = build / "prepared"
    seal = prepared / "inputs.json"
    if prepared.exists():
        if not seal.is_file() or json.loads(seal.read_text(encoding="utf-8")) != inputs:
            raise SystemExit(f"{prepared}: prepared sources differ from this checkout; choose a new build directory")
        return

    build.mkdir(parents=True, exist_ok=True)
    commit = json.loads(PIN.read_text(encoding="utf-8"))["commit"]
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=build) as scratch:
        scratch = Path(scratch)
        upstream = scratch / "upstream"
        if source:
            subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--shared", str(source), str(upstream)], check=True)
        else:
            subprocess.run(["git", "init", "--quiet", str(upstream)], check=True)
            subprocess.run(["git", "-C", str(upstream), "fetch", "--quiet", "--depth=1", REPOSITORY, commit], check=True)
        subprocess.run(["git", "-C", str(upstream), "-c", "core.autocrlf=false",
                        "checkout", "--quiet", "--detach", commit], check=True)

        staged = scratch / "prepared"
        staged.mkdir()
        tree = staged / "datafusion"
        subprocess.run([sys.executable, "-B", str(PATCHER), "--pristine", str(upstream),
                        "--output", str(tree)], check=True)
        # The patcher records hashes before the distribution notices are inserted.
        for name in json.loads(PIN.read_text(encoding="utf-8"))["files"]:
            path = tree / name
            prefix = "#" if path.suffix == ".toml" else "//"
            path.write_bytes(f"{prefix} {NOTICE}\n".encode() + path.read_bytes())
        shutil.copyfile(ROOT / "LICENSE", tree / "hashman-join-core/LICENSE")
        shutil.copytree(ROOT / "worker", staged / "worker", ignore=shutil.ignore_patterns("target"))
        (staged / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
        staged.rename(prepared)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("--source", type=Path, help="Local DataFusion repository containing the pinned commit")
    args = parser.parse_args()
    prepare(args.build.resolve(), args.source.resolve() if args.source else None)
