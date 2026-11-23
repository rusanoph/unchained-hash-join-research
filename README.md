# Unchained Hash Join Research

Two implementations of contiguous build-side indexes for hash joins,
evaluated on one thread in DuckDB 1.5.6 and Apache DataFusion 55.2.0.
The experiments ask when the cost of arranging build rows into contiguous
ranges pays off during probing. [Original Unchained paper](https://doi.org/10.1145/3662010.3663442)

[Read the paper (PDF)](paper/article.pdf) · [Source and build instructions](paper/README.md) ·
[Build DuckDB](duckdb/README.md#build-and-verify) ·
[Build DataFusion](datafusion/README.md#build-and-verify)

| Comparison | Workload | Speed ratio [95% CI] |
| --- | --- | ---: |
| DuckDB stock / contiguous-row policy | 35 SNAP graph queries | 1.068 [1.059, 1.077] |
| DuckDB stock / contiguous-row policy | 113 JOB queries | 0.948 [0.945, 0.951] |
| DataFusion 200K / 50K build-row threshold | 12 held-out Enron and HepPh queries | 2.177 [2.156, 2.212] |
| DataFusion 200K / 50K build-row threshold | 31 analytical queries whose index selection changed | 1.004 [0.977, 1.016] |

Ratios above one favor the experimental configuration in that row.
DuckDB reports a geometric mean of per-query ratios. DataFusion reports
the median paired ratio of summed query times. On JOB 22d, DuckDB's
experimental join scattered 5.54 million build rows for three probe rows.
In DataFusion, the 50K threshold remains a setting for the tested graph
workload.

## Implementations and evidence

- **[DuckDB](duckdb/README.md)** contains the C++ core, the v17 engine
  patch, and a SQL worker. The smoke test checks result rows against
  SQLite and confirms the experimental path and native fallback.
  [Measurements and per-query evidence](duckdb/docs/results.md)
- **[DataFusion](datafusion/README.md)** contains the Rust CSR core,
  the v10b engine patch, and a SQL worker. The smoke test checks both
  CSR layouts and the small-build fallback. The Enron example runs
  one holdout query at both thresholds and checks the exact result and
  the index each run actually built.
  [Measurements and Enron example](datafusion/docs/results.md)
- **[Article](paper/README.md)** traces both layouts from construction through
  SQL execution and derives a break-even condition for probe savings. Its
  measured figures can be regenerated from the bundled engine evidence.

The benchmark tables summarize recorded campaigns. The build and
smoke commands verify the supplied code on small fixtures.

## Licenses

The independent implementations are MIT-licensed.
The patches retain the [DuckDB license](duckdb/licenses/duckdb-LICENSE.txt)
and the [Apache DataFusion license](datafusion/licenses/datafusion-LICENSE.txt)
and [notice](datafusion/NOTICE), respectively.
[DuckDB implementation license](duckdb/LICENSE) · [DataFusion implementation license](datafusion/LICENSE)
