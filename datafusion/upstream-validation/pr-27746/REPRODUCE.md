# Reproduce PR #27746 evidence

Run these commands from the study directory. Evidence verification needs Python 3.10+ and uses the standard library. Execution requires Linux or WSL2, GNU time, Git, Rust 1.97.0, taskset and prlimit. Use a fresh scratch directory on a Linux filesystem. External datasets and compiled outputs belong in scratch.

## A. Evidence only

```sh
python3 scripts/verify_evidence.py
```

The verifier checks the package inventory and hashes, restores recorded executions to temporary directories and compares recomputed statistics, correctness, coverage and recorded provenance. The official hj registration requires three distinct executable fingerprints. The excluded [first hj attempt](results/invalid-hj-001/README.md) remains separate.

## B. Executable smoke

```sh
root="$HOME/df-pr27746-smoke"
mkdir -p "$root"
rustup toolchain install 1.97.0 --profile minimal
bash scripts/bootstrap.sh "$root"
bash scripts/build.sh "$root" A B
python3 scripts/smoke.py "$root/check" --bin "$root/bin" \
  --duckdb "$(command -v duckdb)" --sources "$root/downloads"
```

Use DuckDB 1.5.6 (`069cc9f9b5`). The smoke prepares Enron from the SHA-checked SNAP download and runs its out-star with a dense duplicate fixture and a sparse unique fixture. A writable cache can hold a predownloaded `email-Enron.txt.gz`. Override affinity with `--cpu` and the virtual memory limit with `--address-space-gib`. The smoke tests exact answers. Its timing has no comparative role.

## C. Full campaign

### Acquire and prepare inputs

The [receipt](workloads/input-receipt.json) pins the recorded archive and Parquet identities. SNAP acquisition, normalization and exports are implemented below by `prepare_inputs.py`. Original graphs retain every non-comment edge, use Int32 columns and create a second copy ordered by `hash(src,dst)`. Holdout keeps one orientation for Enron and HepPh via `src <= dst`, preserves Amazon direction and applies the recorded shuffle with `hash(src,dst,20261118),src,dst`. It checks the expected edge count for each holdout graph. Use single-threaded DuckDB 1.5.6 for these recipes.

JOB acquisition uses the pinned CWI IMDb archive from the [benchmark site](https://event.cwi.nl/da/job/). The recovered recipe uses its `schematext.sql` and imports CSV with comma delimiter, double quote and backslash escape. The historical first load was interactive. Exact ingestion order and loader settings are unverified. The export uses ZSTD and 122,880-row groups. The archived SQL is already available in [historical.json](workloads/historical.json). SF10 uses DuckDB's matching-version TPC-H extension and `dbgen(sf=10)`, followed by the same export. Existing DuckDB snapshots can be used with `--database`. Recorded SF10 file digests are in the [hj registration](results/raw/hj-official-sf10-002-registration.json).

```sh
root="$HOME/df-pr27746-full"
mkdir -p "$root"
duckdb="$(command -v duckdb)"
"$duckdb" -c 'INSTALL tpch;'
for kind in graphs holdout job sf10; do
  python3 scripts/prepare_inputs.py "$kind" --duckdb "$duckdb" \
    --sources "$root/downloads" --out "$root/data/$kind"
done
```

The preparation script prints byte identity for each Parquet file. Ties in the original hash order, DuckDB exporter settings, database reload order and compression build can affect row order or bytes. Exact count/sum and legacy fingerprints establish output agreement for the recorded data and measured queries. They alone establish neither full logical dataset equality nor row-order equality for regenerated inputs. Default campaign runners require recorded file digests. Use `--regenerated-inputs` explicitly for a new cohort and report its receipts and correctness separately. For full SNAP cross-checks run `scripts/graph_oracle.py` on the prepared graph directories and compare the query results against the archived oracle.

With DuckDB 1.5.6 and the SHA-checked source gzip files, 15 of the 16 regenerated SNAP Parquet files matched the registered bytes. Delivered WebGoogle matched the archive at every one of the 5,105,039 row positions in a single-threaded DuckDB positional join. Both files had 42 row groups. Its regenerated SHA-256 was `6940dd77390cec2f75a969faf915b0631904b85db6370a715b773cd153c454f6`. Use a new timing registration for this file because encoding differences can affect scan cost.

### Execute

```sh
bash scripts/bootstrap.sh "$root"
bash scripts/build.sh "$root" A B C
for case in pilot snap job job-22d; do
  python3 scripts/run.py "workloads/$case.json" "$root/runs/$case-001" \
    --bin "$root/bin" --graphs "$root/data/graphs" \
    --holdout "$root/data/holdout" --job "$root/data/job"
done
bash scripts/build-memory.sh "$root"
for limit in unbounded 64mib; do
  case "$limit" in
    unbounded) python3 scripts/memory_cases.py > "$root/$limit.json" ;;
    64mib) python3 scripts/memory_cases.py --memory-limit-mb 64 > "$root/$limit.json" ;;
  esac
  python3 scripts/run.py "$root/$limit.json" "$root/runs/memory-$limit-001" \
    --bin "$root/memory-bin" --graphs "$root" --holdout "$root" --job "$root"
done
bash scripts/build-hj.sh "$root"
python3 scripts/hj_official.py "$root" "$root/data/sf10"
python3 scripts/hj_correctness.py "$root" "$root/data/sf10"
python3 scripts/hj_summary.py "$root/runs/hj-official-sf10-002"
```

The frozen configs preserve originally registered CPU 2, seeds, blocks, warmups and limits. Clone the JSON configs into scratch for adaptive CPU affinity or resource limits, change `cpu`, `address_space_gib`, `timeout_seconds` and optional `memory_limit_mb`, and write to a new run directory. The official hj driver exposes matching flags, including `--run`. Separate target directories prevent cross-arm Cargo reuse. The standalone full campaign recipe has not undergone a fresh-machine end-to-end run. See the [protocol](PROTOCOL.md) for measurement and coverage limits.
