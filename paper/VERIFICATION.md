# Editorial verification

Sections 4 and 5 cover the native operators, integrations, activation, timings and engineering decisions. Section 6 derives a per-probe break-even rule using the overlap of the two key distributions and the cost of extra candidate visits. Figures 1, 4 and 6 illustrate the layouts, payload reads and the effect of candidate penalties on the break-even boundary. Figures 2, 3 and 5 use archived point estimates and confidence limits at full precision.

## Evidence used

| Paper material | Source and check |
| --- | --- |
| DuckDB contiguous-row policy | The `automin` contrasts for SNAP, JOB, LDBC BI and TPC-DS give the four points in Figure 2. Resampling is by block. Figure 3 reads the five outgoing-star pairs directly from the SNAP per-query record. The group ratios are 0.932 in delivered order and 1.170 after permutation. LDBC BI's unrounded lower bound is 0.97958555. [Suite files](../duckdb/docs/evidence/) · [Configuration and decision rule](../duckdb/docs/results.md) |
| DuckDB JOB 22d | The largest diagnostic build had 5,536,051 rows, three probes, 345 ms of scatter and 25 ms of grouping. The separate timed ablation has 192 observations. All four JOB intervals include parity and both WebGoogle intervals favor retaining ranges. [Profile summary](../duckdb/docs/results.md#why-build-cost-matters) · [Ablation medians and intervals](../duckdb/docs/evidence/exact-ranges.json) |
| WebGoogle row order (§4.5) | Read `dTLB-load-misses` under `wg5M_sorted/stock` (1,716,180), `wg5M_perm/stock` (15,453,105) and `wg5M_perm/part` (1,777,649 with partition alignment). These counters come from a separate diagnostic run. The JSON is copied from `evidence/partition/` in `hashman-sql-lab` at `2ae8655`. [Counter measurements](../duckdb/docs/evidence/perf-fresh-analysis.json) |
| DataFusion native versus auto, 200K | This is the main DataFusion comparison. Both modes use the same binary. Summing per-query median times gives −73.325% for SNAP (24 of 35 queries used CSR) and −11.678% for JOB (56 of 100). The recorded native and auto totals for `hj` are 25.796407 and 25.854683 seconds, respectively, giving **+0.226%** elapsed time with zero conversions. [Analytical totals](../datafusion/docs/evidence/natural.json) · [SNAP query medians](../datafusion/docs/evidence/snap.json) · [TPC-DS coverage](../datafusion/docs/evidence/tpcds-sf10.json) |
| DataFusion 200K versus 50K | Figure 5 uses the median of within-block ratios of summed times. The 12 changed graph queries and six Amazon controls include Parquet reads. The 31-query analytical group uses preloaded tables and includes the individual `hj_q22` result. [Graph holdout](../datafusion/docs/evidence/snap-threshold.json) · [Analytical audit](../datafusion/docs/evidence/natural-threshold.json) · [Independent answer checks](../datafusion/docs/evidence/threshold-correctness.json) |
| Implementation and attribution | The frozen DuckDB patch and DataFusion adapter establish the eligibility, layout and fallback descriptions. In particular v10b's window-locality check restricts payload permutation with the measured settings. The bibliography records the original DaMoN work, the two architecture papers, the partitioning study, JOB and the dataset authors' SNAP citation. [DuckDB patch](../duckdb/integrations/duckdb/patches/0001-serial-unchained.patch) · [DataFusion adapter](../datafusion/integrations/datafusion/hashman.rs) · [CSR layouts](../datafusion/core/src/lib.rs) · [Bibliography](references.bib) |

## Release checks

- Recreated the eight summary rows from the bundled JSON. The row-order chart adds ten per-query medians and their pointwise 95% intervals. Recomputed Table 1 from the recorded times.
- Rendered all twelve A4 pages with MuPDF and inspected the title block, figures, tables, equation labels, page breaks and bibliography. Adjusted a clipped axis label, overflowing text and the engine section openings during proofing.
- Checked searchable text, embedded fonts, internal destinations, DOI links and engine source pins. The standalone PDF links to the project on GitHub. Its four package and results links use HTTPS and point to files present at artifact revision `b33f6e504467b7c381943539dc57e45d543de1c5`.
- Built the paper in a clean clone using pdfTeX 1.40.22 and BibTeX 0.99d. Its SHA-256 was `85ba9f3275f7fcc362af5980b234e00cedf0848d762b1863f0b640f78810f1cc`, identical to the checked-in PDF. Regenerating the CSV and six vector figures with Matplotlib 3.10.1 also reproduced their checked-in bytes.
- Ran the Enron example from the repository root with the hash-verified SNAP input. Both thresholds returned 5,982,269 and 58,340,459,753. The worker recorded one 183,831-row build at each threshold, using `native_array` at 200K and `csr_exact` at 50K. Substituting the other index in either report made the check fail with the SQL result intact. [Enron reproduction](../datafusion/docs/results.md#run-the-enron-two-hop-query)
- The engine patches and workers match the original research packages. All performance measurements come from the recorded campaigns.

## Cost model check

For Enron, the build keys are the destination vertices and the probe keys
are the source vertices of the retained edges. First run the
[Enron example](../datafusion/docs/results.md#run-the-enron-two-hop-query),
then run this SQL in DuckDB from the repository root.

```sql
WITH edges AS (
    SELECT * FROM read_parquet('datafusion/build/enron/enron_file.parquet')
),
build_keys AS (SELECT dst AS k, count(*) AS n FROM edges GROUP BY dst),
probe_keys AS (SELECT src AS k, count(*) AS n FROM edges GROUP BY src)
SELECT
    (SELECT sum(n) FROM build_keys) AS build_rows,
    (SELECT sum(n) FROM probe_keys) AS probe_rows,
    (SELECT sum(b.n * p.n) FROM build_keys b JOIN probe_keys p USING (k)) AS pairs,
    (SELECT sum(n * n) FROM build_keys) AS build_square_sum,
    (SELECT sum(n * n) FROM probe_keys) AS probe_square_sum;
```

The five values are 183,831, 183,831, 5,982,269, 3,759,983 and 35,776,927.
Dividing the pair count by the probe rows gives 32.5422. Dividing the
build square sum by the build rows gives the 20.4535 build-only proxy.
Dividing the pair count by the geometric mean of the two square sums
gives a cosine similarity of 0.515788. For JOB 22d, dividing the gross
scatter and grouping time by three probe rows gives 123.3 ms per probe
row. These checks use observed counts and recorded phase timings. The
candidate penalties in the break-even figure are illustrative.
[JOB phase timings](../duckdb/docs/results.md#why-build-cost-matters)
