"""Draw vector figures from the suite contrasts and the SNAP per-query record."""
import csv
import json
from pathlib import Path

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

HERE = Path(__file__).resolve().parent
INK = "#243648"
TEAL = "#12695e"
RULE = "#788a94"
mpl.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9.5, "pdf.fonttype": 42,
    "text.color": INK, "axes.labelcolor": INK, "xtick.color": INK,
    "ytick.color": INK, "axes.unicode_minus": False,
})


def save(fig, name):
    fig.savefig(HERE / (name + ".pdf"), metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)


def layout():
    fig, ax = plt.subplots(figsize=(6.55, 1.95))
    fig.subplots_adjust(left=.01, right=.99, top=.99, bottom=.02)
    ax.set(xlim=(0, 10), ylim=(0, 4.1))
    ax.axis("off")
    for bottom, title, keys in [
        (2.60, "Linked duplicates", "ABACBA"),
        (.50, "Exact-key ranges", "AAABBC"),
    ]:
        ax.text(.16, bottom+.88, title, weight="bold", va="center")
        for i, k in enumerate(keys):
            x = 2.75 + i * 1.13
            active = k == "A"
            ax.add_patch(Rectangle((x, bottom), 1.07, .65,
                         facecolor="#e1f0ec" if active else "#f2f4f5",
                         edgecolor=TEAL if active else RULE, lw=1.0))
            ax.text(x+.535, bottom+.33, k, ha="center", va="center",
                    color=TEAL if active else INK)
    ax.text(.16, 2.93, "head for A", va="center")
    arrow = dict(arrowstyle="-|>", color=TEAL, lw=1.1, shrinkA=0, shrinkB=0)
    ax.annotate("", (2.71, 2.93), (1.75, 2.93), arrowprops=arrow)
    for a, b in [(0, 2), (2, 5)]:
        ax.annotate("", (3.285+b*1.13, 3.32), (3.285+a*1.13, 3.32),
                    arrowprops=dict(**arrow, connectionstyle="arc3,rad=-.25"))
    ax.text(.16, .83, "A: [0, 3)", va="center")
    ax.annotate("", (2.71, .83), (1.75, .83), arrowprops=arrow)
    ax.plot([2.75, 2.75, 6.08, 6.08], [.37, .17, .17, .37], color=TEAL, lw=1)
    ax.axhline(2.15, xmin=.016, xmax=.97, color="#c9d2d5", lw=.7)
    save(fig, "layout")


def break_even():
    fig, ax = plt.subplots(figsize=(6.55, 2.0))
    fig.subplots_adjust(left=.09, right=.985, top=.96, bottom=.30)
    ax.set(xlim=(0, 1.1), ylim=(0, 1.1),
           xlabel="Lookup savings / cost", ylabel="Visit savings / cost")
    ax.set_xticks([0, .25, .5, .75, 1.0])
    ax.set_yticks([0, .5, 1.0])
    ax.fill_between([0, 1, 1.1], [1, 0, 0], 1.1, color="#e1f0ec")
    ax.plot([0, 1], [1, 0], color=TEAL, lw=1.5)
    ax.text(.66, .76, "Contiguous faster", ha="center", color=TEAL)
    ax.text(.25, .20, "Native faster", ha="center")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(RULE)
    ax.tick_params(length=2, pad=4)
    save(fig, "break-even")


def forest(rows, labels, ticks, limits, xlabel, name):
    fig = plt.figure(figsize=(6.55, 1.82))
    ax = fig.add_axes([.23, .28, .54, .67])
    ax.set(xlim=limits, ylim=(len(rows)-.5, -.5), xlabel=xlabel)
    ax.set_yticks(range(len(rows)), labels)
    ax.set_xticks(ticks, [f"{x:g}" for x in ticks])
    ax.axvline(1, color=RULE, linewidth=1, linestyle="--")
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#e3e8ea", linewidth=.65)
    ax.tick_params(length=0, pad=4)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    for y, row in enumerate(rows):
        ratio, lo, hi = [float(row[k]) for k in ("speedup", "ci95_low", "ci95_high")]
        ax.errorbar(ratio, y, xerr=[[ratio-lo], [hi-ratio]], color=TEAL,
                    capsize=3, marker="o", markersize=4, linewidth=1.35)
        ax.text(1.035, y, f"{ratio:.3f}  [{lo:.3f}, {hi:.3f}]",
                transform=ax.get_yaxis_transform(), va="center", fontsize=8.8)
    save(fig, name)


def row_order():
    path = HERE.parents[1] / "duckdb/docs/evidence/snap.json"
    queries = json.loads(path.read_bytes())["contrasts"]["automin"]["per_query"]
    graphs = {"wikivote": "WikiVote", "astroph": "AstroPh", "epinions": "Epinions",
              "slashdot": "Slashdot", "webgoogle": "WebGoogle"}
    fig, ax = plt.subplots(figsize=(6.55, 2.35))
    fig.subplots_adjust(left=.13, right=.985, top=.82, bottom=.23)
    ax.axvline(1, color=RULE, linewidth=1, linestyle="--")
    for suffix, label, offset, marker, color in [
        ("_star_out", "As delivered", -.15, "s", INK),
        ("_sh_star_out", "Permuted", .15, "o", TEAL),
    ]:
        for i, graph in enumerate(graphs):
            result = queries[graph + suffix]
            ratio = result["median"]
            lo, hi = result["ci95"]
            ax.errorbar(ratio, i + offset, xerr=[[ratio-lo], [hi-ratio]], color=color,
                        marker=marker, mfc="white" if offset < 0 else color, markersize=4,
                        capsize=2.5, lw=1.3, label=label if i == 0 else None)
    ax.set(ylim=(len(graphs)-.55, -.55), xlim=(.78, 1.56),
           xlabel="Stock time / contiguous-row policy time")
    ax.set_yticks(range(len(graphs)), graphs.values())
    ax.set_xticks([.8, .9, 1, 1.1, 1.2, 1.3, 1.4, 1.5])
    ax.grid(axis="x", color="#e3e8ea", linewidth=.65)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(length=0, pad=4)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2,
              frameon=False, borderaxespad=0)
    save(fig, "row-order")


def gather():
    fig, ax = plt.subplots(figsize=(6.55, 1.6))
    fig.subplots_adjust(left=.01, right=.99, top=.98, bottom=.025)
    ax.set(xlim=(0, 16.4), ylim=(0, 3.2))
    ax.axis("off")
    ax.text(.04, 1.90, "Row IDs for A", va="center")
    ax.text(.04, .55, "Arrow column", va="center")
    ax.plot([9.45, 9.45], [.18, 3.04], color="#c9d2d5", lw=.7)
    for x, title, indices, payload in [
        (2.88, "Original batch", [0, 2, 5], [0, 1, 2, 3, 4, 5]),
        (10.08, "After permutation", [0, 1, 2], [0, 2, 5, 1, 4, 3]),
    ]:
        ax.text(x, 2.85, title, weight="bold", va="center")
        for i, original in enumerate(payload):
            selected = i in indices
            ax.add_patch(Rectangle((x+i, .24), .93, .62,
                         facecolor="#e1f0ec" if selected else "#f2f4f5",
                         edgecolor=TEAL if selected else RULE, lw=.9))
            ax.text(x+i+.465, .55, f"$v_{original}$", va="center", ha="center")
        for i, slot in enumerate(indices):
            ax.add_patch(Rectangle((x+i, 1.60), .93, .60, facecolor="white",
                                   edgecolor=TEAL, lw=.9))
            ax.text(x+i+.465, 1.90, str(slot), va="center", ha="center")
            ax.annotate("", (x+slot+.465, .89), (x+i+.465, 1.58),
                        arrowprops=dict(arrowstyle="-|>", lw=1, color=TEAL,
                                        shrinkA=0, shrinkB=0))
    save(fig, "gather")


if __name__ == "__main__":
    with (HERE.parent / "results.csv").open(newline="", encoding="utf-8") as data:
        rows = list(csv.DictReader(data))
    layout()
    gather()
    row_order()
    break_even()
    forest(rows[:4], ["SNAP  (35)", "JOB  (113)", "LDBC BI SF10  (56)", "TPC-DS SF10  (99)"],
           [.94, .98, 1, 1.04, 1.08], (.93, 1.09), "Stock time / contiguous-row policy time", "duckdb")
    forest(rows[4:], ["Enron + HepPh  (12)", "Amazon0302  (6)", "Changed builds  (31)", "hj_q22  (1)"],
           [1, 1.4, 1.8, 2.2], (.90, 2.26), "Auto 200K / auto 50K", "threshold")
