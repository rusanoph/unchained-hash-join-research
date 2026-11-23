"""Build the figure, data table and HTML article from the checked-in evidence."""

import base64
import csv
import json
from pathlib import Path

import markdown
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASELINE = "#425b73"
TREATMENT = "#00756c"


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


def draw_panel(axis, rows, title, xlabel, ticks, limits):
    axis.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=12)
    axis.axvline(1, color="#687683", linewidth=0.9, linestyle="--", zorder=1)
    axis.set_axisbelow(True)
    axis.grid(axis="x", color="#e5e9ed")
    for i, (_, label, count, speedup, lo, hi) in enumerate(rows):
        color = TREATMENT if lo > 1 else BASELINE
        axis.errorbar(speedup, i, xerr=[[speedup - lo], [hi - speedup]], fmt="o",
                      color=color, capsize=3, markersize=5, linewidth=1.4, zorder=2)
        axis.text(1.02, i, f"{speedup:.3f}  [{lo:.3f}, {hi:.3f}]",
                  transform=axis.get_yaxis_transform(), va="center", fontsize=9)
    axis.set_yticks(range(len(rows)), [f"{label}  ({count})" for _, label, count, *_ in rows])
    axis.set_xticks(ticks, [f"{x:g}×" for x in ticks])
    axis.set_xlim(*limits)
    axis.set_ylim(len(rows) - 0.45, -0.55)
    axis.set_xlabel(xlabel, labelpad=7)
    axis.tick_params(axis="both", length=0, pad=5)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.spines["bottom"].set_color("#b9c4cc")


def build_figure(rows):
    with plt.rc_context({"font.size": 10, "text.color": "#24333e", "axes.labelcolor": "#24333e",
                         "xtick.color": "#24333e", "ytick.color": "#24333e"}):
        fig, (duckdb, datafusion) = plt.subplots(2, 1, figsize=(8.4, 4.7))
        fig.subplots_adjust(left=0.28, right=0.77, top=0.91, bottom=0.14, hspace=0.85)
        draw_panel(duckdb, rows[:4], "DuckDB 1.5.6", "Stock / v17",
                   [0.94, 0.97, 1.00, 1.03, 1.06, 1.09], (0.93, 1.09))
        draw_panel(datafusion, rows[4:], "DataFusion 55.2.0", "200K / 50K threshold",
                   [1.0, 1.25, 1.5, 1.75, 2.0, 2.25], (0.90, 2.27))
        fig.savefig(HERE / "results.png", dpi=220, bbox_inches="tight", pad_inches=0.16)
        fig.savefig(HERE / "results.pdf", bbox_inches="tight", pad_inches=0.16)
        plt.close(fig)


def build_article():
    source = (HERE / "article.md").read_text(encoding="utf-8")
    title = source.splitlines()[0].removeprefix("# ")
    body = markdown.markdown(source, extensions=["tables"])
    figure = base64.b64encode((HERE / "results.png").read_bytes()).decode("ascii")
    body = body.replace('src="results.png"', f'src="data:image/png;base64,{figure}"')
    css = (HERE / "article.css").read_text(encoding="utf-8")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="author" content="Maxim Rusanov">
<title>{title}</title>
<style>{css}</style>
</head>
<body><main>{body}</main></body>
</html>
"""
    (HERE / "article.html").write_text(html, encoding="utf-8")


def main():
    rows = list(result_rows())
    with (HERE / "results.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["engine", "workload", "queries", "speedup", "ci95_low", "ci95_high"])
        writer.writerows(rows)
    build_figure(rows)
    build_article()


if __name__ == "__main__":
    main()
