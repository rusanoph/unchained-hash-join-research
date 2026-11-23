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

Build the worker in its default location. From the root of the combined
repository, run the example with the DuckDB 1.5.6 CLI (`duckdb`) on your
`PATH`, Bash, curl, GNU coreutils, diff and Python 3.
[Worker build instructions](../README.md#build-and-verify)

```sh
bash datafusion/scripts/check-enron.sh
```

For an offline run, place the original `email-Enron.txt.gz` in
`datafusion/build/enron/` first. The script verifies the SHA-256 of either
the cached or downloaded input, keeps one record per undirected edge
and writes the Parquet file, SQL results and worker reports to
`datafusion/build/enron/`. At both thresholds it checks the two-hop join
against the independent result (count 5,982,269 and sum 58,340,459,753).
It also checks `samples[0].hashman.builds` for one 183,831-row build:
`native_array` at 200K and `csr_exact` at 50K. The published intervals
use the full randomized-block campaign.
[Script and SQL](../scripts/check-enron.sh) · [SNAP Enron dataset](https://snap.stanford.edu/data/email-Enron.html)

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
