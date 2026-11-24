# When Contiguous Hash Joins Pay Off in DuckDB and DataFusion

Maxim Rusanov · November 2026

[Read the paper (PDF)](article.pdf) · [LaTeX source](article.tex) · [References](references.bib) · [Editorial verification](VERIFICATION.md)

The main DataFusion comparison is native execution versus automatic CSR
selection at the 200,000-row build threshold on the same engine build
(Table 1). Auto reduced the sum of per-query median times by 73% on SNAP
and 11.7% on JOB. Figure 5 reports the follow-up comparison of two auto
thresholds. [DataFusion results](../datafusion/docs/results.md#native-join-versus-auto-mode)

## Rebuild the PDF

From the repository root on Linux or WSL, run

```sh
make -C paper paper
```

From PowerShell at the same location, use `wsl -- make -C paper paper`.
The build uses GNU Make, pdfLaTeX, BibTeX and Latin Modern. On Ubuntu the
TeX packages are `texlive-latex-recommended` and `lmodern`.
The checked-in vector figures are sufficient for an offline build.
Intermediate files go to `paper/build/`. A successful final pass replaces
`paper/article.pdf`.

The standalone PDF links to both engine packages and results guides
at the [artifact revision](https://github.com/rusanoph/unchained-hash-join-research/tree/b33f6e504467b7c381943539dc57e45d543de1c5).

## Recreate the figures

From the repository root, run the following with Python 3 and Matplotlib
(the release figures used 3.10.1), then rerun the PDF build.

```sh
python3 paper/build.py
```

The script extracts full-precision contrasts from the two evidence directories
into [results.csv](results.csv). The [drawing source](figures/draw.py) generates
the layout and payload-gather diagrams, two suite-level interval plots and
the analytical break-even figure. It also plots the five outgoing-star
queries in delivered and permuted order directly from the
[per-query SNAP record](../duckdb/docs/evidence/snap.json).
The engine guides provide the runtime settings, SQL smoke
checks and the provenance of the recorded campaigns.
[DuckDB](../duckdb/docs/results.md) · [DataFusion](../datafusion/docs/results.md)
