# A/B protocol

Arm A is upstream main at the PR merge-base. Arm B is the unchanged PR head. See the [source and implementation audit](analysis/architecture.md). Both workers compile the same harness against the source checkouts. Natural physical plans and their expression/join differences remain in scope.

The primary machine is the AMD Ryzen 5 7500F under Ubuntu WSL2. Pin each worker to logical CPU 2 and use one DataFusion partition with 8,192-row batches. Capture the [environment](environment/) and exact compiler, lockfiles, feature resolution, and binary digests. During timing keep builds and other benchmark processes idle. WSL may move the guest vCPU between host hardware threads. Retain page caches and capture process CPU and load averages for context.

## Input and execution boundaries

| Suite | Input mode | Unit |
|---|---|---|
| SNAP | Original archived Parquet loaded into MemTable before timing | 35 archived instances |
| SNAP holdout | Direct Parquet scan inside the measured statement | 18 archived instances |
| JOB | Original archived Parquet loaded into MemTable before timing, with the historical SQL-token column projection | Published 100-query cohort |
| JOB exclusions | Same load mode and SQL source, separate coverage | 13 archived exclusions |

Each observation uses a fresh worker, separate planning and input-load timings, and a pinned affinity. Statement time spans SQL planning, physical planning, execution and full stream consumption including row verification. Attach textual plans and operator metrics after the timer. When a query requests exact outputs, the statement boundary includes writing the usually one-row result to a temporary file. Process wall time includes worker launch, table loading, warm-up and output encoding. GNU time records peak RSS and whole-process CPU. Build/probe metrics carry the meaning of the upstream implementation. Peak process RSS includes loads and warm-ups and can diverge from memory-pool accounting or steady-state index size.

The Linux address-space ceiling and per-statement process timeout apply symmetrically. Distinguish worker error, explicit memory exhaustion, failed allocation, external kill, timeout and invalid worker output. A killed process with unknown cause retains the killed status. The ceiling constrains virtual size and cannot be read as a physical RSS limit.

## Correctness and reporting

For the historical SNAP and JOB queries, compare field names and logical types, result row count, order-independent digest and exact sorted JSON rows with explicit NULL markers. These workloads predominantly return one integral or string result row. If floating-point output requires tolerance, inspect full values and document the rationale and threshold before accepting a discrepancy. Two-input SNAP frequency statistics apply to the logical SQL aliases. Orient them with the saved physical plan before attributing build or probe costs. [SNAP independent oracles](workloads/graph-oracle.json).

Each registered block visits all cases in seeded random order and rotates arm order within each case. The initial pilot includes A/A from identical binaries. Compare pointwise paired median ratios A/B with 95% percentile bootstrap intervals resampling whole observation pairs. For suites, report the geometric mean of per-query median time ratios, sum of per-query medians and percent reduction. The suite paired bootstrap resamples common blocks and uses the median ratio of summed times. Pointwise bounds support triage across queries, with unresolved and unstable cases listed. A/B pairs with consistent outputs enter comparative aggregates. Failures and incomplete coverage remain visible. Keep confirmations under a separate immutable registration.

## Resumption

The runner registers slot order, complete SQL/settings, executable and dataset digests, driver digest, affinity and kernel in the run directory. It stores each observation atomically, including failed observations, and verifies the registration again on restart. Use a new run ID for changed configurations and for intentional repeats. Preserve previous evidence.

Pilot parameters are frozen in [pilot.json](workloads/pilot.json). The [pilot report](reports/pilot.md) and [calibration](workloads/decision.json) explain the main [SNAP](workloads/snap.json) and [JOB](workloads/job.json) registrations and the 24% ratio margin. The larger margin reflects observed A/A variability. Smaller point estimates and their intervals remain available for engineering triage.
