# DuckDB v17 results

Each comparison uses the native hash join from stock DuckDB 1.5.6 on
the same SQL workload. The Unchained arm uses v17 in prefer mode, with
automatic radix alignment and native fallback below 8 MiB of build
staging. It preserves the native perfect-hash path when available.

| Workload | Queries | Stock / v17, geometric mean [95% CI] | Per-query data |
|---|---:|---:|---|
| SNAP graph self-joins | 35 | **1.068 [1.059, 1.077]** | [SNAP](evidence/snap.json) |
| Join Order Benchmark (JOB) | 113 | 0.948 [0.945, 0.951] | [JOB](evidence/job.json) |
| LDBC BI SF10, local SQL port and initial snapshot | 56 | 0.986 [0.980, 0.992] | [LDBC BI](evidence/ldbc-bi-sf10.json) |
| TPC-DS SF10, held out from tuning | 99 | 0.988 [0.986, 0.990] | [TPC-DS](evidence/tpcds-sf10.json) |

Ratios above one favor v17. Each query contributes the median of its
paired within-block ratios. Cohort intervals resample blocks. A fresh
process, one query thread and one pinned logical CPU were used for each
observation on a WSL2 host. Blocks randomized the order of observations.
The baseline was run twice as an A/A control. All four A/A cohort
intervals covered one.

The registered follow-up required a lower interval bound of at least
0.98 for both JOB and LDBC BI. JOB fell short by a wide margin, and the
unrounded lower bound for LDBC BI was 0.97959.

## Why build cost matters

In a diagnostic run of JOB 22d, the largest hash join built 5,536,051
rows and received three probe rows. The instrumented build spent 345 ms
scattering rows and 25 ms grouping exact keys. A separate timed
ablation turned off exact ranges, changing both key grouping and probe
lookup. The 95% interval for each of the four selected JOB queries
included one. The two selected WebGoogle queries slowed down, with
v17 / range-off ratios of 0.926 [0.919, 0.962] and 0.914 [0.889, 0.943].
All 192 observations completed with matching plans and query results.
[Ablation data](evidence/exact-ranges.json)

## Configuration and provenance

The following options select the measured v17 path. Omit `--result`
to use the timed worker's value-hash drain. Select the appropriate
database, query file and affinity for the workload.

```sh
build/hashman-duckdb --database workload.duckdb --read-only true \
    --query query.sql --threads 1 --warmups 1 --memory-limit 12GB \
    --mode unchained_prefer --controlled false --native-first true \
    --partitioned-build auto --min-build-bytes 8388608 \
    --directory-format packed48 --lookup-kernel tags --host-filters true \
    --exact-ranges true --exact-lookup separate_nodiv_inline --refine-prefetch ahead
```

The JSON summaries retain per-query contrasts and the A/A control.
The table uses the `automin` contrast. The suite analyses come from
`hashman-sql-lab` at `2ae8655` under `evidence/partition/`. The ablation
comes from `1e96df2` under `evidence/exact-range-ablation-001/`. This
repository contains the aggregate evidence and synthetic verification
fixtures. The original SQL workload files and raw observations remain
in the research archive.

The archived v17 inputs were verified against the package.

| File | SHA-256 |
|---|---|
| [Engine patch](../integrations/duckdb/patches/0001-serial-unchained.patch) | `0c1c4489e9eb3d0b954ca4f3597d1cbbc075cce77b22c05c12c9b1bfe60dd7c5` |
| [Worker source](../worker/main.cpp) | `4acae97adf32e906b79619a5057cba9359aae1dd794aa3ccba74c7be3ee22ffc` |

## Package verification

A clean release build passed the SQL smoke and the research suite's
66 capability checks and 64 lifecycle checks across automatic and
forced-aligned builds. The lifecycle suite covers collisions,
allocation and memory-budget fallback, cancellation, and repeated
execution with integer and string payloads. All three standalone C++
tests passed under AddressSanitizer and UndefinedBehaviorSanitizer.

On all six ablation queries, the rebuilt worker returned the same
row multiset as stock DuckDB with exact ranges enabled and disabled.
The two v17 arms matched on the physical plan and the set of converted
builds. The runtime records confirmed the two probe paths. These
release checks did not contribute timings to the tables above.
