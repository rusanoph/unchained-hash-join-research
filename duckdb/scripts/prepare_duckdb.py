"""Fetch and verify the dependencies and apply the frozen DuckDB patch."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "integrations/duckdb/patches/0001-serial-unchained.patch"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def pinned_download(path, url, expected):
    if path.exists():
        if sha256(path) != expected:
            raise ValueError(f"SHA-256 mismatch for {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        candidate = Path(directory) / path.name
        with urllib.request.urlopen(url, timeout=240) as response, candidate.open("wb") as target:
            shutil.copyfileobj(response, target)
        if sha256(candidate) != expected:
            raise ValueError(f"SHA-256 mismatch for {url}")
        candidate.replace(path)


def extract_source(archive, destination):
    with tarfile.open(archive, "r:gz") as source:
        members = []
        for member in source.getmembers():
            parts = Path(member.name).parts
            if parts and (parts[0] == "/" or ".." in parts):
                raise ValueError(f"Unsafe archive member: {member.name}")
            if len(parts) <= 1:
                continue
            if (member.issym() or member.islnk()) and parts[1] == "data":
                continue  # upstream test-data links are unused by the build
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Unsafe archive member: {member.name}")
            member.name = str(Path(*parts[1:]))
            members.append(member)
        source.extractall(destination, members=members, filter="data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    engine = json.loads((ROOT / "deps/engines.lock.json").read_text())["duckdb"]
    dependency = json.loads((ROOT / "deps/json.lock.json").read_text())
    archive = args.cache / f"duckdb-{engine['commit'][:8]}.tar.gz"
    pinned_download(archive, engine["archive_url"], engine["archive_sha256"])
    pinned_download(args.cache / "json/nlohmann/json.hpp", dependency["url"], dependency["sha256"])

    target = args.build / "source"
    inputs = {"commit": engine["commit"], "patch_sha256": sha256(PATCH)}
    seal = Path(".unchained-inputs.json")
    if target.exists():
        if (target / seal).is_file() and json.loads((target / seal).read_text()) == inputs:
            return
        raise ValueError(f"Source in {target} has different inputs; use a fresh build directory")

    args.build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.build) as directory:
        prepared = Path(directory) / "source"
        prepared.mkdir()
        extract_source(archive, prepared)
        subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(PATCH)],
            cwd=prepared, check=True,
        )
        (prepared / seal).write_text(json.dumps(inputs, indent=2) + "\n")
        prepared.rename(target)


if __name__ == "__main__":
    main()
