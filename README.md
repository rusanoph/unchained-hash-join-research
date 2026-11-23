# unchained-join

An independent, header-only C++17 core for the *Unchained* hash join.
It implements the table layout, partitioned build, and exact key ranges.
The design draws on the paper by Birler, Schmidt, Fent, and Neumann.
[Simple, Efficient, and Robust Hash Tables for Join Processing](https://doi.org/10.1145/3662010.3663442)

This repository contains the core, its tests, and a build-kernel microbenchmark.
The DuckDB integration and SQL campaigns are part of the separate research repository
`hashman-sql-lab`.

## DuckDB research results

The separate DuckDB 1.5.6 integration was measured against the native join.
It sped up graph self-joins and took longer on the tested analytical suites.

| Workload (single thread, same machine, A/A-controlled) | stock / treatment time, geometric mean [95% CI] |
|---|---|
| Graph self-joins (35 queries, SNAP graphs) | **1.068** [1.059, 1.077] |
| LDBC BI SF10 (56 queries, own port, initial snapshot) | 0.986 [0.980, 0.992] |
| JOB (113 queries) | 0.948 [0.945, 0.951] |
| TPC-DS SF10 (99 queries; held out from tuning) | 0.988 [0.986, 0.990] |

Values above 1 mean the experimental join was faster. In the tested graph workloads,
large shuffled builds benefited most from the new layout. In JOB, some large builds
were probed by only a few rows and spent most of their time on construction. The
ratios compare variants within each measurement session.

## Layout

```
core/include/unchained/   header-only library, no DuckDB dependency
  table.hpp               layout, directory with tags, count/prefix/scatter build
  partitioned_build.hpp   build per radix partition (cache-sized pieces), byte-identical result
  key_ranges.hpp          per-bucket key sorting and exact key ranges
core/tests/               unit and differential tests, build-kernel microbenchmark
```

## Build and test the core

Use CMake, Ninja, and GCC or Clang on Linux or WSL.

```sh
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug     # AddressSanitizer + UBSan are on by default
cmake --build build
ctest --test-dir build --output-on-failure
```

The micro-benchmark (`bench_build_partitioning`) reproduces the build-cost curve: about 6-7 ns per row while the
table fits the cache, about 45 ns per row at 4-8 million rows, and about half of that with partitioning.

## Provenance

The core was ported from `hashman-sql-lab`. Its original port was checked
token by token after removing comments and whitespace and normalizing the namespace.
The SQL results above are from that research repository's v17 campaigns.
The research repository contains the registrations, analysis, and reproduction instructions.

## License

The independent core is available under the MIT license. [LICENSE](LICENSE)
