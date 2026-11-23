# Unchained joins for DuckDB

A C++ implementation of the Unchained hash table and a serial hash-join
prototype for DuckDB 1.5.6. Build rows with the same hash prefix occupy a
contiguous range. The DuckDB integration can build those ranges one radix
partition at a time and use an exact-key index to narrow each probe.
[Original Unchained paper](https://doi.org/10.1145/3662010.3663442)

On one thread, the final prototype achieved a **1.068×** geometric-mean
speed ratio on 35 SNAP graph queries. It was slower than stock DuckDB
on JOB, LDBC BI and TPC-DS. In JOB 22d, the join laid out 5.54 million build
rows for three probe rows. [Measurements and per-query data](docs/results.md)

## Build and verify

Use Linux or WSL with a current Python 3.10+ security release, Bash, GNU
patch, CMake, Ninja and GCC or Clang.

```sh
cmake -S . -B build/core -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/core
ctest --test-dir build/core --output-on-failure

bash scripts/build-duckdb.sh
python3 -B scripts/smoke.py build/hashman-duckdb
```

The core tests run with AddressSanitizer and UndefinedBehaviorSanitizer.
The engine build verifies the DuckDB source archive and the worker's JSON
header against the SHA-256 pins in [deps/](deps/). Pass a build directory to
the script to keep the build on a Linux filesystem under WSL. Set
`UNCHAINED_CACHE` to reuse downloaded dependencies or `UNCHAINED_BUILD_JOBS`
to control compilation parallelism.

The SQL smoke compares result multisets with SQLite. It checks execution
of the Unchained join, native fallback for a left join, and rejection of
that same join in forced mode.

## Run a SQL query

```sh
build/hashman-duckdb --setup setup.sql --query query.sql \
    --threads 1 --mode unchained_prefer --warmups 1
```

`--setup` initializes an in-memory database. Use `--database` for an
existing DuckDB file. The worker emits JSON with timing, output row count,
physical plan and per-join execution decisions. A positive
`executed_unchained_joins` count confirms that the treatment ran. Use
`native` for comparison or `unchained_required` to require an eligible
experimental join. The prototype supports single-key INT32 and INT64
inner equijoins with supported payload types.

## Source

| Path | Contents |
|---|---|
| [core/](core/) | Standalone C++17 headers, tests and a build microbenchmark |
| [integrations/duckdb/](integrations/duckdb/) | Frozen v17 patch, including the engine adapter and kernel |
| [worker/](worker/) | SQL runner and runtime telemetry |
| [docs/results.md](docs/results.md) | Benchmark configuration, results and evidence |

The v17 patch and worker source match the files used in the measurement
campaigns. The standalone core is a namespace-cleaned port of the
corresponding research headers.

The independent source is available under the [MIT license](LICENSE).
Upstream DuckDB code in the patch retains its
[DuckDB license](licenses/duckdb-LICENSE.txt).
