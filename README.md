# unchained-join

An independent implementation of the *Unchained* hash join
([Birler, Schmidt, Fent, Neumann, "Simple, Efficient, and Robust Hash Tables for Join Processing", DaMoN 2024](https://doi.org/10.1145/3662010.3663442)),
built into DuckDB 1.5.6 and measured against DuckDB's own join on about 300 queries (graphs, JOB, LDBC BI, TPC-DS).

> **Status: work in progress.** The engine-independent core library is ported and tested. The DuckDB integration,
> the benchmark harness and the full report are being moved over from the research repository this project grew out
> of (see [Provenance](#provenance)). Nothing here is a production-ready join.

## The short answer

The idea does **not** make DuckDB's hash join faster in general. It is a measured win in one narrow class and a
small loss elsewhere:

| Workload (single thread, same machine, A/A-controlled) | stock / treatment time, geometric mean [95% CI] |
|---|---|
| Graph self-joins (35 queries, SNAP graphs) | **1.068** [1.059, 1.077] |
| LDBC BI SF10 (56 queries, own port, initial snapshot) | 0.986 [0.980, 0.992] |
| JOB (113 queries) | 0.948 [0.945, 0.951] |
| TPC-DS SF10 (99 queries; held out from tuning) | 0.988 [0.986, 0.990] |

Values above 1 mean the treatment is faster. The gain comes from large build tables whose rows are in random order:
DuckDB's table takes about 9x more TLB misses there, the partitioned Unchained build does not. The loss comes from
joins where the build table is large but only a handful of probe rows read it, so the extra build work is never
repaid. Numbers of different sessions are only comparable within a session (the same build gave 1.034 and 1.059 on the
graphs in two sessions).

## Layout

```
core/include/unchained/   header-only library, no DuckDB dependency
  table.hpp               layout, directory with tags, count/prefix/scatter build
  partitioned_build.hpp   build per radix partition (cache-sized pieces), byte-identical result
  key_ranges.hpp          per-bucket key sorting and exact key ranges
core/tests/               unit and differential tests, build-kernel micro-benchmark
docs/                     report, design notes, figures (in progress)
```

## Build and test the core

```sh
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug     # AddressSanitizer + UBSan are on by default
cmake --build build
ctest --test-dir build --output-on-failure
```

The micro-benchmark (`bench_build_partitioning`) reproduces the build-cost curve: about 6-7 ns per row while the
table fits the cache, about 45 ns per row at 4-8 million rows, and about half of that with partitioning.

## Provenance

This code is a cleaned-up port of an internal research repository. The core library here has the same code as the
research repository's core (compared token by token after removing comments and whitespace and renaming the namespace);
the DuckDB integration will be a cleaned port whose equivalence is checked by a sanity campaign (same session, the
old build against the new one on a sample of queries, a criterion fixed in advance); its result will be stated here.
Every performance number in the report comes from the research repository's builds. The research repository keeps the raw campaign data, registrations and the full chronicle including failed attempts.

## License

MIT, see [LICENSE](LICENSE). The paper's PDF and the datasets used (SNAP graphs, IMDb/JOB, LDBC, TPC-DS) are not
redistributed; scripts and checksums will be provided instead.
