"""Regenerate the figure data and vector illustrations from the bundled evidence."""
import csv
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def evidence(engine, name):
    path = ROOT / engine / "docs" / "evidence" / name
    return json.loads(path.read_bytes())


def result_rows():
    for name, label in (
        ("snap", "SNAP graph queries"),
        ("job", "JOB"),
        ("ldbc-bi-sf10", "LDBC BI SF10"),
        ("tpcds-sf10", "TPC-DS SF10"),
    ):
        data = evidence("duckdb", name + ".json")
        contrast = data["contrasts"]["automin"]
        yield ("DuckDB", label, data["cohort_size"], contrast["geometric_mean"], *contrast["ci95"])

    graphs = evidence("datafusion", "snap-threshold.json")["cohorts"]
    analytical = evidence("datafusion", "natural-threshold.json")
    for label, count, stats in (
        ("Enron and HepPh", 12, graphs["primary"]["200k_over_50k"]),
        ("Amazon0302", 6, graphs["amazon"]["200k_over_50k"]),
        ("Analytical, changed CSR builds",
         sum(query["selection"] == "changed_csr_builds" for query in analytical["per_query"].values()),
         analytical["by_selection"]["changed_csr_builds"]),
        ("hj_q22", 1, analytical["per_query"]["hj_q22"]["200k_over_50k"]),
    ):
        yield ("DataFusion", label, count, stats["speedup"], *stats["ci95"])


def main():
    rows = list(result_rows())
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["engine", "workload", "queries", "speedup", "ci95_low", "ci95_high"])
        writer.writerows(rows)
    subprocess.run([sys.executable, HERE / "figures" / "draw.py"], check=True)


if __name__ == "__main__":
    main()
