# Memory and resource observations

## Peak process RSS

The values below are medians of per-process peak RSS in KiB across the matched blocks. JOB 17b completed its three registered blocks. The process peak covers input load, warm-up, timed execution and final result handling. These measurements describe headroom on the WSL2 host with the registered 10 GiB virtual address-space ceiling. Operator build memory tracks a different quantity inside DataFusion.

| Case | A peak RSS KiB | B peak RSS KiB | Change |
|---|---:|---:|---:|
| WebGoogle delivered out-star | 180,032 | 225,964 | +25.5% |
| WebGoogle permuted two-hop | 180,824 | 221,120 | +22.3% |
| Amazon delivered out-star | 94,740 | 109,212 | +15.3% |
| Amazon permuted out-star | 97,336 | 110,120 | +13.1% |
| Slashdot selective two-hop | 83,776 | 82,540 | −1.5% |
| JOB 17b | 5,068,764 | 6,330,184 | +24.9% |

Across the 35 original graph cases, the median per-query RSS change was +3.9%. Across the 18 holdout cases it was +1.9%. The larger increases deserve attention even when execution time improves. No SNAP slot hit the address-space ceiling or reported execution resource exhaustion. [SNAP per-query summary](../results/summaries/snap-001-A-B.json), [raw SNAP process peaks](../results/raw/snap-001.jsonl.gz), [raw JOB process peaks](../results/raw/job-001.jsonl.gz).

## Reservation analysis

The [source audit](../analysis/architecture.md) identifies two allocations to test separately. The non-dense directories use a whole-build hash buffer. The group builder reserves for worst-case pending entries before it knows the actual duplicate count. The selected graph examples use the dense directory on B, according to `array_map_created_count`. For the composite three-probe pilot, displayed build memory was 68.3 MB on A and 68.6 MB on B. The process peaks were 169,764 and 184,628 KiB. [Pilot join metrics](../results/profiles/pilot-001-join-timers.json).

Separate diagnostic binaries wrap the standard DataFusion pool in `PeakRecordingPool` and reset its watermark after setup. Each case builds one million rows in 8,192-row batches and probes three rows. The registered plans hold build orientation fixed. The memory instrumentation runs outside primary timings. The following unbounded medians use five independent processes per arm and case. The 64 MiB column gives successful runs out of five per arm. [Diagnostic source generation](../scripts/instrument_peak.py), [cases](../scripts/memory_cases.py), [measured pool and timing data](../results/summaries/memory-controlled.json).

| Build shape | Peak pool A / B / C (MiB) | Build wall A / B / C (ms) | 64 MiB success A / B / C |
|---|---:|---:|---:|
| Dense unique | 22.95 / 32.66 / 22.95 | 11.61 / 11.40 / 10.67 | 5 / 5 / 5 |
| Dense, 100 rows per key | 30.58 / 32.78 / 30.58 | 11.79 / 18.86 / 11.88 | 5 / 5 / 5 |
| Dense singletons and one hot key | 24.88 / 33.74 / 24.88 | 10.86 / 12.96 / 11.50 | 5 / 5 / 5 |
| Sparse integer unique | 53.07 / 70.48 / 70.48 | 37.17 / 40.48 / 41.13 | 5 / 0 / 0 |
| Sparse integer, 100 rows per key | 53.07 / 74.37 / 74.37 | 31.64 / 49.02 / 49.87 | 5 / 0 / 0 |
| General composite | 68.33 / 89.90 / 89.90 | 42.48 / 68.62 / 70.75 | 0 / 0 / 0 |

All 90 unbounded and 55 successful limited observations matched their exact outputs. The other 35 limited slots reported DataFusion `ResourcesExhausted`. The sparse B/C errors were repeatable at the same additional reservation request. Each reported 34.0 MB requested with 27.5 MB available in the 64 MiB greedy pool. That failure establishes a resource regression under the pinned plans and settings. The failure evidence combines directory, group and hash-buffer effects. It does not isolate each transient allocation's contribution. The unbounded measurements show the extra pool headroom that permitted the same rows to complete. [Unbounded raw](../results/raw/memory-unbounded-001.jsonl.gz), [64 MiB raw and errors](../results/raw/memory-64mib-001.jsonl.gz).

C restores the A pool watermark in these three dense fixtures. Its effect on physical peak RSS varies. For dense duplicates the peak RSS was 101,696 / 108,560 / 103,760 KiB on A/B/C, and on dense unique it was 92,848 / 93,568 / 94,492 KiB. The sparse integer and general controls retain B's pool values and resource behavior. These observations localize the patch benefit to the dense path. They leave whole-build non-dense scratch and reservation growth as separate follow-ups.

The `build_time` and `join_time` metrics are elapsed wall time. CPU sampling with the installed `perf` launcher was unavailable for the WSL kernel. GNU time process CPU is available separately in the raw artifacts. [Process and pool measurements](../results/raw/memory-unbounded-001.jsonl.gz).

In the independently registered official hj SF10 campaign, median Q4 pool peaks were 915.31 / 1,506.78 / 915.31 MiB on A/B/C. Q5 measured 915.31 / 1,502.98 / 915.31 MiB. Q4 process peaks were 1,205,180 / 1,928,584 / 1,219,152 KiB. These cases independently show the dense patch restoring pool headroom at a 60M-row build scale. [Official x86 report](counting-sort-x86.md).
