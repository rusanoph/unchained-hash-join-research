# When Contiguous Hash Joins Pay Off in DuckDB and DataFusion

A systems engineering article by Maxim Rusanov.
[Read the article](article.md) · [PDF](article.pdf) · [Standalone HTML](article.html)

The engine directories contain the implementations, build and smoke
commands, and the machine-readable results used in the article.
[DuckDB](../duckdb/README.md) · [DataFusion](../datafusion/README.md)

## Regenerate the figure and HTML

Run from the repository root with Python 3, Markdown 3.7 and
Matplotlib 3.10.1.

```sh
python3 paper/build.py
```

The script reads the JSON evidence in the two engine directories and writes
the [figure data](results.csv), the [vector figure](results.pdf) and
the HTML with its figure embedded. The HTML includes a print stylesheet
for exporting the article to PDF.
