# Pilot

The pilot compared A `6a060586ed85d2ff71837b6d818b7eddc075b699`, an identical-binary A/A control, and B `2f6c665fef63936f08a4aa7a4fb01b87a4d2b009`. It used one partition and pinned workers to logical CPU 2 on the Ryzen 5 7500F WSL2 machine. [Source and build provenance](../analysis/architecture.md), [environment](../environment/wsl-pilot-start.txt), [registration](../results/raw/pilot-001-registration.json).

All 93 slots executed successfully. The exact outputs and logical schemas matched across arms. The SNAP and controlled smoke queries matched independent expected rows. All 30 pilot JOB observations matched historical native result fingerprints from v10b. Each pair had identical plan text. The three controlled joins checked NULL-safe equality and residual filtering with duplicates, a dense unique key with a small probe, and a sparse-hit composite key. The last two had one million build rows and three probe rows in the saved operator metrics. [Correctness](../results/correctness/pilot-001-A-B.json), [JOB archive cross-check](../results/correctness/pilot-001-job-legacy.json), [plans and metrics](../results/plans/pilot-001.jsonl.gz).

| Query | Pairs | A median ms | B median ms | Paired median A/B | Median A/AA |
|---|---:|---:|---:|---:|---:|
| WikiVote two-hop | 3 | 16.16 | 9.63 | 1.810 | 1.016 |
| Epinions selective two-hop | 3 | 5.41 | 5.62 | 0.901 | 0.704 |
| Enron delivered out-star | 3 | 123.26 | 73.52 | 1.676 | 1.015 |
| HepPh permuted two-hop | 3 | 58.53 | 32.97 | 1.982 | 0.953 |
| JOB 2d | 3 | 84.36 | 99.44 | 0.828 | 1.083 |
| JOB 4b | 3 | 30.34 | 29.03 | 0.990 | 1.152 |
| JOB 7a | 3 | 304.50 | 376.43 | 0.809 | 0.860 |
| JOB 17b | 1 | 35,743.94 | 26,264.82 | 1.361 | 0.955 |
| Dense unique, three probes | 3 | 12.33 | 10.87 | 1.135 | 1.127 |
| Composite key, three probes | 3 | 40.70 | 64.88 | 0.598 | 0.824 |
| NULL-safe full join with residual | 3 | 0.54 | 0.54 | 0.984 | 0.888 |

The A/A spread matters for JOB 4b, JOB 7a and selective Epinions. Those pilot point estimates need the full campaign. JOB 17b fits the run limits and warrants separate timing and memory attention. In its single pilot pair, peak RSS was 4.84 GiB on A and 6.04 GiB on B with equal outputs and the same plan text. Inside that plan, a join with about 80.27M build rows and 4.52M probe rows recorded build time of 7.77 s on A and 15.87 s on B, with join time of 25.74 s on A and 8.06 s on B. The composite-key diagnostic shows a candidate construction cost. Its median operator build wall time was 39.38 ms on A and 63.46 ms on B, while median join time was below 0.04 ms in both arms. These are rendered wall-clock operator timers. Build may include time spent receiving child output in a multi-join plan. [Operator comparisons](../results/profiles/pilot-001-join-timers.json), [process measurements](../results/raw/pilot-001.jsonl.gz), [all A/B estimates](../results/summaries/pilot-001-A-B.json), [A/A estimates](../results/summaries/pilot-001-A-AA.json).

The preregistered calibration maps the 90th percentile of absolute median A/A log ratios to a symmetric 24% ratio margin. The main run uses nine SNAP blocks, five JOB blocks, and three for the nine previously expensive JOB queries. The 24% margin controls the coarse faster/slower classification. The report will also show per-query point estimates and intervals to expose smaller effects. [Calibration](../workloads/decision.json), [main SNAP](../workloads/snap.json), [main JOB](../workloads/job.json).
