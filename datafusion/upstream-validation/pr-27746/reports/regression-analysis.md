# Regression analysis

## SNAP

The strongest negative paired median ratios are 0.887 for selective Slashdot two-hop, 0.896 for permuted Amazon out-star and 0.911 for delivered Amazon out-star. Their pointwise intervals lie below 1 in the nine-block campaign. Each is inconclusive under the registered 24% ratio margin calibrated from pilot A/A. The smaller repeated slowdown remains relevant to the review because the instances use the same plans and produce exact oracle results. [Per-query estimates and intervals](../results/summaries/snap-001-A-B.json).

| Case | Physical join | Build wall A → B | Join wall A → B | Evidence-supported interpretation |
|---|---|---:|---:|---|
| Slashdot selective two-hop | Dense, 14,427 build rows and 948,464 probes | 1.13 → 1.13 ms | 4.71 → 5.85 ms | The measured difference is in join processing |
| Amazon delivered out-star | Dense, 1,234,877 rows per side | 17.67 → 26.66 ms | 13.86 → 16.15 ms | Build time is the larger measured increment |
| Amazon permuted out-star | Same keys, row order changes | 17.87 → 34.28 ms | 59.11 → 67.38 ms | Both timers rise, with the larger increase in build |

The graphs expose contrasts for the cost model. The low-fanout Amazon star has 4.90 matches per probe compared with 194.62 on Enron out-star. Both have complete frequency overlap. Enron's probe savings outweigh its extra build time. Selective Slashdot has 1.91 matches per probe and around 0.442 overlap, which leaves little benefit from traversing shorter duplicate groups contiguously. On the two matched WebGoogle two-hop instances, equal cardinalities, key frequencies and fanout coexist with very different timing effects. The sustained ordering effect calls for a separate gather/cache diagnosis. [Matched ordering results](../results/summaries/snap-001-cohorts.json), [join phase timings](../results/profiles/snap-001-join-timers.json), [exact graph input statistics](../workloads/graph-oracle.json).

The rendered join timer spans batch probing, candidate expansion, filtering and result materialization. A rise there leaves several plausible causes. Use the build and join timer directions for prioritization and retain individual cache, allocation and gather explanations as hypotheses.

## JOB and controlled diagnostics

The [historical workload audit](../analysis/workloads.md) identifies JOB 7a and 4b as the largest negative point estimates in the published DataFusion cohort. JOB 2d and 29a were negative with an earlier one-observation integration. JOB 22d was outside the historical 100-case timing cohort after an allocation failure in the earlier DataFusion coverage pass. The current campaign completed all 100 original cases plus both 22d observations.

The pilot's sparse-hit composite control built 1M rows, probed three and returned 67 matches. Its median build wall time increased from 39.38 to 63.46 ms and probe-side join wall was below 0.04 ms on both arms. The dense unique control with one million build rows and three probes had median build wall of 11.35 and 10.04 ms. These comparisons distinguish the two observed paths. They leave general-directory key equality and temporary allocation costs to narrower diagnosis. The multi-join JOB 17b pilot contrasts increased build wall with reduced join wall on its approximately 80M-row build. Its overall gain came with higher process RSS. [Pilot evidence and A/A variability](pilot.md), [join timer data](../results/profiles/pilot-001-join-timers.json).

JOB 7b was the only case classified slower by the registered margin. JOB 7a and 2d also have repeated negative point estimates with pointwise intervals below 1, while 4b and 29a have smaller effects. JOB 7c has a negative point estimate with a broad interval. The old adapter's losses persist on some shapes with the new architecture despite the large weighted overall gains.

| Historical or related case | Current paired A/B [95%] | Diagnostic evidence |
|---|---:|---|
| JOB 7b | 0.764 [0.734, 0.799] | Non-dense build-side timer 251.47 → 335.18 ms on 348.5K build and 92.0K probe rows. The dense child with 36.24M probes also rose in join wall time by 63.87 ms |
| JOB 7a | 0.773 [0.730, 0.840] | Related non-dense build-side timer 245.15 → 333.96 ms with 348.5K build rows. Its dense child's join timer rose by 68.38 ms |
| JOB 2d | 0.810 [0.713, 0.828] | The dense 1.15M-row build increased from 21.67 to 27.79 ms and the same join's join wall from 34.37 to 44.87 ms |
| JOB 4b | 0.963 [0.862, 0.981] | A short statement with small joins. Per-operator timers show only small increments. Pilot A/A dispersion supports caution |
| JOB 29a | 0.968 [0.928, 1.007] | Its three-probe join costs about 0.1 ms on each arm. Most time is in the build-side subtree and both scans and nested joins contribute |

Identical plan text and exact results allow like-for-like timer comparisons. Build-side wall timers in these natural plans include child execution and can overlap across joins. The 7a and 7b profiles therefore motivate a smaller controlled reproducer before changing the general directory. In 22d the one comparable build is 2.61M rows against 126.2K probes, with a 1.249× one-pair overall observation and correctness agreement. It gives current coverage of a large-build case and a bounded basis for further diagnostics. [JOB per-query intervals](../results/summaries/job-001-A-B.json), [JOB operator timings](../results/profiles/job-001-join-timers.json), [22d operator timings](../results/profiles/job-22d-001-join-timers.json).
