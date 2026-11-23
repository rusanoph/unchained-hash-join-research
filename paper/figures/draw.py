"""Draw the paper's vector figures. results.csv contains the unrounded contrasts."""
import csv
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
    fig, ax = plt.subplots(figsize=(6.0, 2.18))
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
    fig, ax = plt.subplots(figsize=(5.5, 2.1))
    fig.subplots_adjust(left=.18, right=.96, top=.94, bottom=.28)
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
    fig = plt.figure(figsize=(6.0, 1.95))
    ax = fig.add_axes([.31, .28, .385, .66])
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
        ax.text(1.06, y, f"{ratio:.3f}  [{lo:.3f}, {hi:.3f}]",
                transform=ax.get_yaxis_transform(), va="center", fontsize=8.8)
    save(fig, name)


if __name__ == "__main__":
    with (HERE.parent / "results.csv").open(newline="", encoding="utf-8") as data:
        rows = list(csv.DictReader(data))
    layout()
    break_even()
    forest(rows[:4], ["SNAP  (35)", "JOB  (113)", "LDBC BI SF10  (56)", "TPC-DS SF10  (99)"],
           [.94, .98, 1, 1.04, 1.08], (.93, 1.09), "Stock / v17", "duckdb")
    forest(rows[4:], ["Enron + HepPh  (12)", "Amazon0302  (6)", "Changed builds  (31)", "hj_q22  (1)"],
           [1, 1.4, 1.8, 2.2], (.90, 2.26), "Auto 200K / auto 50K", "threshold")
