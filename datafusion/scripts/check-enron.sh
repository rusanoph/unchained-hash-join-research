#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

worker=build/target/release/hashman-df-worker
if [[ ! -x "$worker" ]]; then
    echo "Build the worker first with bash datafusion/scripts/build-worker.sh from the repository root." >&2
    exit 1
fi

mkdir -p build/enron
archive=build/enron/email-Enron.txt.gz
checksum="55cfead79b1f0161786179a48796c2a119bd7026a246238b257b6be9d8b69b68  $archive"
if [[ ! -f "$archive" ]] || ! sha256sum --check --status <<< "$checksum"; then
    curl -fLsS --connect-timeout 15 --max-time 120 \
        https://snap.stanford.edu/data/email-Enron.txt.gz -o "$archive"
fi
sha256sum --check <<< "$checksum"

duckdb -bail <<'SQL'
SET threads = 1;
COPY (
    SELECT * FROM read_csv('build/enron/email-Enron.txt.gz', delim='\t',
        header=false, skip=4, auto_detect=false, columns={'src':'INTEGER','dst':'INTEGER'})
    WHERE src <= dst
) TO 'build/enron/enron_file.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
SQL

for threshold in 200000 50000; do
    "$worker" --data build/enron --parquet-direct true --target-partitions 1 \
        --warmups 0 --repetitions 1 --set datafusion.execution.hashman_join=auto \
        --set "datafusion.execution.hashman_join_min_build_rows=$threshold" \
        --sql 'SELECT count(*), sum(a.src::BIGINT + b.dst)
               FROM enron_file a JOIN enron_file b ON a.dst = b.src' \
        --result-file "build/enron/result-$threshold.tsv" > "build/enron/metrics-$threshold.json"
    printf '5982269\t58340459753\n' | diff - "build/enron/result-$threshold.tsv"
done

python3 - <<'PY'
import json
from pathlib import Path

for threshold, expected in [(200000, "native_array"), (50000, "csr_exact")]:
    report = json.loads(Path(f"build/enron/metrics-{threshold}.json").read_bytes())
    sample, = report["samples"]
    builds = [(b["kind"], b["build_rows"]) for b in sample["hashman"]["builds"]]
    if builds != [(expected, 183831)]:
        raise SystemExit(f"Threshold {threshold}: expected {expected} on 183831 rows, got {builds}")
    print(f"Threshold {threshold}: {expected}, 183831 build rows, SQL result matched")
PY
