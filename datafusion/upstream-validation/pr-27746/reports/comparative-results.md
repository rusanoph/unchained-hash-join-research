# DataFusion PR #27746 upstream comparison

## Executive summary

The PR reduced the sum of per-query median times by **74.1% across the 35 original SNAP queries**, **38.2% across the 18 holdout instances**, and **26.3% across the 100 original JOB queries**. All 954 SNAP and 964 JOB observations succeeded with matching outputs. The JOB geometric mean of per-query ratios was 0.996×. Almost all time saved in the JOB sum came from the six queries in family 17. The strongest confirmed JOB regression was 7b. [SNAP estimates](../results/summaries/snap-001-A-B.json), [JOB estimates](../results/summaries/job-001-A-B.json), [pilot](pilot.md).

Arm A is upstream main at common ancestor `6a060586ed85d2ff71837b6d818b7eddc075b699`. Arm B is unchanged PR head `2f6c665fef63936f08a4aa7a4fb01b87a4d2b009`. The two revisions differ by the contiguous index and inline integer key commits. Main at discovery was seven commits ahead on its side of the fork. Both arms use the same worker and identical external Cargo lockfiles, Rust 1.97.0, the release profile, an AMD Ryzen 5 7500F under WSL2, one logical CPU and one execution partition. [Source audit](../analysis/architecture.md), [build and platform evidence](../environment/), [protocol](../PROTOCOL.md).

## SNAP

| Cohort | Cases | A sum of medians | B sum of medians | Reduction | Geometric mean A/B | Paired block sum ratio and 95% interval |
|---|---:|---:|---:|---:|---:|---:|
| Original SNAP | 35 | 26.266 s | 6.814 s | 74.1% | 2.714× | 3.927× [3.841, 4.051] |
| Holdout | 18 | 2.626 s | 1.623 s | 38.2% | 1.644× | 1.640× [1.591, 1.660] |

Nine blocks covered all 53 cases. The planner emitted the same physical-plan text on A and B for each case. The two graph cohorts use disjoint datasets. The holdout includes 12 Enron/HepPh graph, pattern and order instances and six Amazon0302 controls. Those subgroups reduced their sums by 55.7% and 25.3% respectively. The 14 permuted original instances reduced their sums by 81.2%, and the 21 delivered instances by 52.5%. These two sets include different shapes and aggregate weights. The matched WebGoogle examples below isolate ordering within one shape. [SNAP registration](../results/raw/snap-001-registration.json), [groups and order pairs](../results/summaries/snap-001-cohorts.json), [all per-query estimates](../results/summaries/snap-001-A-B.json), [raw timings](../results/raw/snap-001.jsonl.gz), [correctness](../results/correctness/snap-001-A-B.json), [plans](../results/plans/snap-001.jsonl.gz).

Using the preregistered 24% ratio margin, 28 original and 13 holdout cases were faster, while seven and five remained inconclusive. No slower case met that margin. For engineering triage the smaller negative point estimates include the following. These are pointwise intervals from paired blocks.

| Instance | A median | B median | Paired A/B [95%] |
|---|---:|---:|---:|
| Slashdot permuted in-star | 1,764 ms | 293 ms | 6.256 [5.926, 6.720] |
| Epinions permuted in-star | 1,114 ms | 187 ms | 6.083 [5.722, 6.504] |
| WebGoogle permuted two-hop | 5,555 ms | 1,082 ms | 5.537 [5.062, 5.644] |
| Slashdot selective two-hop | 8.27 ms | 9.13 ms | 0.887 [0.819, 0.925] |
| Amazon permuted out-star | 180.4 ms | 205.2 ms | 0.896 [0.837, 0.915] |
| Amazon delivered out-star | 110.5 ms | 120.1 ms | 0.911 [0.890, 0.942] |

Build work rises in several of the largest wins. On permuted WebGoogle two-hop, the per-operator median build wall time rises from 46.8 to 129.9 ms and join time falls from 5,420 to 885 ms. Delivered WebGoogle has the same 5,105,039 rows per side and 60,687,836 matches, with join time dropping from 385 to 162 ms. The build-side size-biased multiplicity is 269.8. Actual matches per probe before residual filtering are 11.89 and the frequency overlap is 0.482. Those values are shared across orderings. The much larger drop in join time on the permuted input is consistent with row order and memory access affecting probe cost beyond what key frequencies describe.

Enron out-star has 194.62 matches per probe. Its permuted input recorded a build cost increase from 3.03 to 4.46 ms and a join wall time reduction from 315 to 45.0 ms. Amazon out-star has 4.90 matches per probe with the same complete-overlap star shape. On the permuted Amazon input the median build rose from 17.9 to 34.3 ms and join time rose from 59.1 to 67.4 ms. The selectivity-controlled Slashdot case builds 14,427 rows against 948,464 probe rows with 1.91 matches per probe. Its build time is about 1.13 ms on both arms and its median join time rises from 4.71 to 5.85 ms. The measured timer split supports construction overhead as a factor for Amazon out-star and probe work as a factor for selective Slashdot. Allocation, candidate processing and payload gathering within those timers need narrower attribution. [Phase metrics](../results/profiles/snap-001-join-timers.json), [input characteristics and independent graph oracle](../workloads/graph-oracle.json).

## JOB

Each arm completed 482 observations. The 91 shorter cases ran five pairs each and the nine historically expensive cases ran three pairs each. All 100 SQL statements were the exact archived statements and match the official IMDb query tokens. All saved schemas, exact results and physical plan text agreed across arms within every case. All 964 observations also match the archived 55.2.0 native fingerprints. The portable bundle was restored and independently recomputed to the identical summary. [Registration](../results/raw/job-001-registration.json), [raw JOB observations](../results/raw/job-001.jsonl.gz), [per-query CSV](../results/summaries/job-001-queries.csv), [archived correctness cross-check](../results/correctness/job-001-legacy.json), [plans and metrics](../results/plans/job-001.jsonl.gz).

| JOB scope | Cases | A sum of medians | B sum of medians | Reduction | Geometric mean A/B |
|---|---:|---:|---:|---:|---:|
| Original cohort | 100 | 208.113 s | 153.416 s | 26.3% | 0.996× |
| Heavy cohort | 9 | 166.720 s | 112.449 s | 32.6% | 1.299× |
| Remaining cohort | 91 | 41.393 s | 40.967 s | 1.0% | 0.970× |
| Family 17, subset of heavy | 6 | 164.581 s | 110.392 s | 32.9% | 1.457× |

The median ratio of summed JOB times across the three common blocks was 1.360 [1.349, 1.376]. There were five faster, one slower and 94 inconclusive cases at the registered margin. The sums emphasize the high-cost queries and the geometric mean weights each query equally. The 91-query subgroup illustrates the difference. Family 17 accounts for approximately 99% of the net seconds saved across JOB. The paired intervals are pointwise and three pairs limit the precision of the heavy case estimates. [All per-query statistics](../results/summaries/job-001-A-B.json).

| Case | A median | B median | Paired A/B [95%] |
|---|---:|---:|---:|
| JOB 17f | 35.45 s | 23.04 s | 1.544 [1.511, 1.612] |
| JOB 17c | 34.54 s | 22.09 s | 1.540 [1.493, 1.604] |
| JOB 7b | 277.5 ms | 360.4 ms | 0.764 [0.734, 0.799] |
| JOB 7a | 288.8 ms | 373.8 ms | 0.773 [0.730, 0.840] |
| JOB 2d | 76.19 ms | 93.86 ms | 0.810 [0.713, 0.828] |

The three registered JOB 17b pairs show a median A/B of 1.500 [1.476, 1.581]. Statement medians were 33.90 s on A and 22.87 s on B. In the unchanged plan, the operator receiving 80.27M build rows and 4.52M probe rows took 7.02 s versus 13.27 s to build, and 24.77 s versus 7.24 s in its join timer. The extra construction time was amortized by probing in this case. Peak process RSS rose from 5,068,764 to 6,330,184 KiB. [JOB operator timings](../results/profiles/job-001-join-timers.json), [JOB process measurements](../results/raw/job-001.jsonl.gz).

The 7b plan includes a non-dense join with about 348.5K build rows and 92.0K probe rows. Its reported build wall rose from 251.47 to 335.18 ms. Its direct join timer was about 0.8 ms on A and 0.7 ms on B. That build timer includes child input production. A dense child with about 36.24M probes also increased its join wall from 123.34 to 187.21 ms. The current evidence locates a larger build-side subtree cost and an independently visible dense-child change. Profiles or fixed-input runs are needed to assign shares to index construction, payload materialization or the child operator. [Focused regressions](regression-analysis.md).

JOB 22d was measured separately with no warm-up. Both arms produced the same exact row under the same plan. The single observed pair was 498.7 ms versus 399.3 ms. One join had approximately 2.61M build rows and 126.2K probes. The historical DataFusion coverage pass recorded allocation failures on 22d. The current result establishes coverage for these revisions and settings. The other 12 historical exclusions are outside this run. [22d registration](../results/raw/job-22d-001-registration.json), [22d raw](../results/raw/job-22d-001.jsonl.gz), [22d operator evidence](../results/profiles/job-22d-001-join-timers.json).

## Interpretation

The interpretation follows the earlier cost model `ΔT = ΔB + Δprobe + ΔG + ΔO`, with build, probe, payload gather and other work. The exact binary graph statistics use `M = Σ f_B(k) f_P(k)` and `m = M/P`. The join timers combine probe and gather work, so the evidence supports changes in their combined time. The job plans also include nested input production inside some build timers. [Cost-model publication](https://doi.org/10.5281/zenodo.24519118).

## Reproduction

The [reproduction commands](../REPRODUCE.md) build isolated workers and resume the immutable registration. The [workload receipt](../workloads/input-receipt.json) gives exact SQL correspondences and checksums of the files. The original graph modes load Parquet into MemTables before statement timing. Holdout reads Parquet inside the statement. Each measured statement includes SQL and physical planning and complete result consumption. The raw record keeps load time, planning time, whole process wall/CPU times and exact rows separately.

The [physical SNAP shape table](../results/profiles/snap-001-shapes.json) maps all 49 single-join exact key distributions to the actual build/probe orientation and records directory and output metrics for all 53 graph cases. The four multi-join cases have operator observations and exact query counts. Intermediate exact key histograms and raw candidate counts before equality checks remain unmeasured.
