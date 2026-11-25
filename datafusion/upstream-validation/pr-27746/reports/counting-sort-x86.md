# Dense counting sort on x86-64

Arm C uses the original review patch on PR head `2f6c665fef63936f08a4aa7a4fb01b87a4d2b009`. The saved patch SHA-256 is `13da9749d8003238e2d3b87ad8cc0aa40bd3c42d17221f067133137ecd57283e`. Regenerating the applied diff with nine-character Git object IDs reproduces the review diff byte for byte. The patch changes only `join_index/directory.rs` and `join_index/directory/dense.rs`. [Original review](https://github.com/apache/datafusion/pull/27746#discussion_r4698342517), [patch](../patches/review-4698342517.patch), [C worker provenance](../environment/worker-C.provenance.txt).

The upstream `join_exact_index` SQL logic test exited successfully on C. Its one test file completed. The runner also printed its informational memory-drift diagnostic for an external sorter. That diagnostic is a separate tool output from the controlled join pool experiment. [Test log](../results/correctness/C-join_exact_index.log).

The corrected x86 study used the unchanged official `dfbench hj` SQL on archived TPC-H SF10 Parquet. The three executables were built with Rust 1.97.0 and `release-nonlto` with default benchmark features including mimalloc. The run used one logical CPU, one partition, 8,192-row batches, a fair memory pool with an explicit `8G` limit and a 10 GiB process address-space cap. Each of five interleaved blocks ran three official iterations in a fresh process per arm and query. [Registration with input and binary digests](../results/raw/hj-official-sf10-002-registration.json), [A build](../environment/dfbench-A.log), [B build](../environment/dfbench-B.log), [C build](../environment/dfbench-C.log).

The controlled x86 diagnostics are complete. The million-row dense duplicate case reduces median build wall from 18.86 ms on B to 11.88 ms on C, with A at 11.79 ms. Its peak pool drops from 32.78 to 30.58 MiB. On the unique dense case C restores A's 22.95 MiB peak from 32.66 MiB on B. All dense correctness checks passed. The non-dense B and C controls retain the same reservation peaks and limited-pool outcomes. These are the synthetic controls drawn from the memory review. [Controlled memory report](memory-analysis.md), [raw summary](../results/summaries/memory-controlled.json).

The first official attempt was excluded after equal SHA-256 revealed one Cargo executable attached to all three labels. The rerun retained the verified A binary and built B and C in separate target directories. It repeated all three arms under a new registration that requires three distinct executable digests. All 90 new slots and 270 iterations succeeded, and the official context recorded one logical CPU throughout. The separate multiset pass verified equivalent schemas, counts and fingerprints across the three independent workers. Each verified count also matched all official iterations. Recomputing the accepted summary from the exported raw archive reproduced it byte for byte. [Excluded attempt](../results/invalid-hj-001/README.md), [reproduction instructions](../REPRODUCE.md), [correctness](../results/correctness/hj-sf10-cross-check.json), [raw observations](../results/raw/hj-official-sf10-002.jsonl.gz).

## End-to-end timing

Times are medians of the five process medians, in milliseconds. Ratios are medians of the within-block ratios. The pointwise intervals resample the five paired blocks.

| SF10 case | A ms | B ms | C ms | Paired A/B | Paired B/C [95%] |
|---|---:|---:|---:|---:|---:|
| Q2 | 16.27 | 18.95 | 12.07 | 0.864 | 1.569 [1.524, 1.603] |
| Q4 | 1,749.89 | 1,780.93 | 1,267.06 | 1.007 | 1.415 [1.360, 1.435] |
| Q5 | 8,115.49 | 2,011.94 | 1,404.90 | 4.046 | 1.432 [1.309, 1.462] |
| Q23 | 408.31 | 321.68 | 320.13 | 1.263 | 0.999 [0.986, 1.040] |
| Q26 | 308.33 | 271.47 | 268.22 | 1.140 | 1.021 [0.962, 1.038] |
| Q28 | 1,362.83 | 738.12 | 697.34 | 1.886 | 1.022 [0.968, 1.087] |

The patch reverses Q2's A/B regression and improves Q4 and Q5 against B under these conditions. Q2's paired A/C is 1.362 [1.309, 1.570]. Q23 general-directory behavior is timing-neutral between B and C. The B/C intervals for Q26 and Q28 also include one. The reviewer reported the same directional Q2/Q4/Q5 improvements and stable controls on Apple M5 Max. Absolute times and speed ratios depend on the machine, data export, CPU pinning and compile profile. [All paired estimates](../results/summaries/hj-official-sf10-002.json), [Apple report](https://github.com/apache/datafusion/pull/27746#discussion_r4698342517).

The diagnostic plans show dense builds on Q2, Q4, Q5 and Q26. Q2 built 1.50M rows and probed 25. Q4 and Q5 built 59.99M rows and probed 100K. The full-result pass records one build-wall sample per arm, including upstream input production. Q4 recorded 2.39 / 3.88 / 1.98 s on A/B/C and Q5 recorded 1.39 / 2.37 / 1.59 s. These observations support reduced build wall on C. Repeated whole-statement estimates come from the official timing run. [Plans and timers](../results/plans/hj-sf10-diagnostics.jsonl.gz), [extracted operators](../results/profiles/hj-sf10-operators.jsonl).

Official median peak pool reservation fell from 37.56 to 22.89 MiB on Q2, from 1,506.78 to 915.31 MiB on Q4, and from 1,502.98 to 915.31 MiB on Q5 when moving from B to C. Each C value matches A at the recorded precision. On Q4, process RSS also fell from 1,928,584 to 1,219,152 KiB. The non-dense Q23 and Q28 pool peaks were unchanged between B and C. Their recorded RSS can differ across processes. Peak pool and RSS track distinct resources. The per-join diagnostic timers are elapsed wall. The installed WSL `perf` could not sample CPU for this kernel. [Official memory peaks](../results/summaries/hj-official-sf10-002.json), [controlled memory analysis](memory-analysis.md).
