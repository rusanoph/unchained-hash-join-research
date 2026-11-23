# DataFusion research results

The measurements below come from the v10b research build on Apache
DataFusion 55.2.0. The single-thread experiments ran on one logical CPU
under WSL2. The source package includes the pinned index implementation
and a synthetic smoke check. Full SQL datasets and raw campaign output
remain with the research archive.

## Native join versus auto mode

The original single-thread campaigns used the 200,000-row build threshold.

| Workload | Queries | Queries using CSR | Change in total query time |
|---|---:|---:|---:|
| SNAP graph self-joins | 35 | 24 | −73% |
| JOB | 100 | 56 | −11.7% |
| TPC-H SF10 | 20 | 3 | −2.6% |
| TPC-DS SF10 | 94 | 24 | −0.1% |
| DataFusion hj | 25 | 0 | +0.2% |

## Lowering the build threshold in auto mode

| Workload | Timed queries | 200K / 50K speed ratio [95% CI] |
|---|---:|---:|
| Enron and HepPh graph queries | 12 | **2.177 [2.156, 2.212]** |
| Amazon0302 graph control | 6 | 0.988 [0.967, 1.022] |
| Natural queries with different CSR builds | 31 | 1.004 [0.977, 1.016] |
| hj_q22 | 1 | **1.464 [1.404, 1.515]** |

The graph holdout timed complete SQL statements including Parquet reads.
The natural-query audit screened 97 queries. It timed 39 queries over
eight randomized blocks with native, native A/A, 200K, and 50K arms.
The reported 31-query ratio covers the queries whose CSR builds changed.
Natural-query timing starts after loading the tables. Ratios above one
indicate a faster 50K arm. The 50K threshold is supported for the tested
graph workload. The audit gives no basis for a global change.

The graph audit matched the exact integer `COUNT` and `SUM` results of all
18 queries with an independent DuckDB calculation. The natural-query
audit matched result row counts and digests with the native join.
[Correctness record](evidence/threshold-correctness.json)

The timing summaries and per-query data are available for the
[original graphs](evidence/snap.json), [original analytical workloads](evidence/natural.json),
[TPC-DS](evidence/tpcds-sf10.json), [graph threshold holdout](evidence/snap-threshold.json)
and [analytical threshold audit](evidence/natural-threshold.json).

## Run the Enron two-hop query

Run from the repository root after building the worker. This example uses
the DuckDB 1.5.6 CLI to export the SNAP edge list to Parquet. It keeps one
record per undirected edge, as in the original holdout.
[SNAP Enron dataset](https://snap.stanford.edu/data/email-Enron.html)

```sh
mkdir -p build/enron
curl -fLsS https://snap.stanford.edu/data/email-Enron.txt.gz -o build/enron/email-Enron.txt.gz
printf '%s  %s\n' 55cfead79b1f0161786179a48796c2a119bd7026a246238b257b6be9d8b69b68 \
    build/enron/email-Enron.txt.gz | sha256sum -c

duckdb -bail <<'SQL'
SET threads = 1;
COPY (
    SELECT * FROM read_csv('build/enron/email-Enron.txt.gz', delim='\t',
        header=false, skip=4, auto_detect=false, columns={'src':'INTEGER','dst':'INTEGER'})
    WHERE src <= dst
) TO 'build/enron/enron_file.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
SQL

for threshold in 200000 50000; do
    build/target/release/hashman-df-worker \
        --data build/enron --parquet-direct true --target-partitions 1 --warmups 0 \
        --set datafusion.execution.hashman_join=auto \
        --set "datafusion.execution.hashman_join_min_build_rows=$threshold" \
        --sql 'SELECT count(*), sum(a.src::BIGINT + b.dst)
               FROM enron_file a JOIN enron_file b ON a.dst = b.src' \
        --result-file "build/enron/result-$threshold.tsv" > "build/enron/metrics-$threshold.json"
    printf '5982269\t58340459753\n' | diff - "build/enron/result-$threshold.tsv"
done
```

The 200K arm uses a native index. The 50K arm reports `csr_exact` for
the 183,831-row build in `samples[0].hashman.builds`. This example
checks one query and the runtime selection. The published intervals use
the full randomized-block campaign.

## Package verification

The rebuilt v10b worker passed the synthetic smoke and 43 differential
SQL cases in six modes (258 executions). On the six hash-verified
Parquet inputs from the holdout, the rebuilt worker also matched all 36
integer result pairs at 200K and 50K against the archived independent
oracle. The runtime records showed six CSR builds at 200K and 18 at 50K.
These release checks did not contribute timings to the tables above.

## Research records

| Record in hashman-datafusion-lab | Git revision |
|---|---|
| `docs/results-final-001.ru.md` and original evidence | `ae18a8f` |
| `docs/results-snap-holdout-001.ru.md` and `evidence/snap-holdout-001/` | `e9f75c6` |
| `docs/results-threshold-audit-001.ru.md` and `evidence/threshold-audit-001/` | `95aba8d` |
