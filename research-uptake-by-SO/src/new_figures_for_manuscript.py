#!/usr/bin/env python3
"""Figures for the manuscript, styled like the paper's other figures (Arial, print size).

Separate from analyze_citations.py and tikz_export.py, which stay as they are. This script
only reads the statistics that analyze_citations.py has already written to
data/results/stats/, and saves to data/results/figures_manuscript/ (outside the folders that
scripts/verify_outputs.py checks).

Figure: citations of IEEE PES Transactions in SO documents, by research programme. One
stacked bar per programme (optionally plus a total bar), system operators only, three segments:
  IEEE PES Transactions | other journals and conferences | other sources (reports,
  standards, websites, and references the pipeline could not classify)
The segments add up to the number above each bar.

Run from anywhere:  python src/new_figures_for_manuscript.py
"""

import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import RESULTS_DIR, STATS_DIR  # noqa: E402

# Which entities' documents: "_system_operators" (TSOs/ISOs/RTOs only), "_no_esig"
# (all but ESIG) or "" (all entities).
SCOPE = "_system_operators"
INPUT_CSV = STATS_DIR / f"area_summary{SCOPE}.csv"
OUTPUT_DIR = RESULTS_DIR / "figures_manuscript"
OUTPUT = OUTPUT_DIR / f"citations_by_programme{SCOPE}.pdf"
SAVE_PNG = True  # also save a PNG copy next to the PDF, for sharing
PNG_DPI = 300

# Print size: IEEE Transactions single column (3.5 in); matplotlib sizes are in points.
FIG_WIDTH_PT = 252
FIG_HEIGHT_PT = 150
FONT_SIZE_PT = 8
LEGEND_FONT_SIZE_PT = 6.5
BAR_LABEL_FONT_SIZE_PT = 6.5
# Programme names under the bars; at 8 pt "Services" and "Planning" touch.
XTICK_FONT_SIZE_PT = 7
BAR_WIDTH = 0.45  # fraction of the space per bar
# Bars sit this far (fraction of the space per bar) right of the programme name, so the
# segment counts fit on their left.
BAR_SHIFT = 0.00

# Count of each segment, printed left of the bar at the segment's height.
SHOW_SEGMENT_LABELS = True
SEGMENT_LABEL_FONT_SIZE_PT = 5.5
SEGMENT_LABEL_GAP_PT = 1.5  # between a count and the bar's left edge
# Counts closer than this (centre to centre) are pushed up, so that small segments on top
# of each other (e.g. IEEE 2 above journals 4) keep readable counts.
SEGMENT_LABEL_MIN_SPACING_PT = 6  # about 1.1 x the font size
SHOW_ZERO_SEGMENT_LABELS = True  # e.g. "0" IEEE citations for DER

SHOW_TOTAL_BAR = False  # True: a last bar with all programmes together
# Number above each bar: "total" (e.g. 674) or "ieee/total" (e.g. 24/674).
BAR_LABEL = "total"
SHOW_TOTAL_SEPARATOR = True  # dashed line before the total bar, when it is shown
# Top of the axis as a multiple of the highest bar: room for its label, and without the total
# bar also for the legend above the programme bars.
Y_HEADROOM = 1.15 if SHOW_TOTAL_BAR else 1.2
Y_TICK_STEP = 200 if SHOW_TOTAL_BAR else 100
# Totals over all programmes in the legend entries:
#   "count": "IEEE PES Transactions (24 in total)"
#   "share": "IEEE PES Transactions (24 in total, 3.6%)"
#   None: names only
LEGEND_TOTALS = "share"
# Bar names in the paper, where they differ from the "label" column of the CSV.
LABEL_RENAMES = {"IBRs": "IBR"}

# Segments from the bottom up: (CSV columns added together, legend label, bar colour, colour
# of its counts). Bar colours of the paper's other figures: High+ and High of LEVEL_COLORS in
# article-alignment-classifier/scripts/util/generate_article_figures.py, and the bar grey of
# SO-involvement-in-authorship/scripts/05_plot_figure.py. The grey counts are darker than the
# bars, to stay readable on the grey plot area.
SEGMENTS = [
    (["other"], "Other sources", "#a6a6a6", "#6e6e6e"),
    (["other_journals", "conferences"], "Other journals and conferences", "#225ea8", "#225ea8"),
    (["ieee_transactions"], "IEEE PES Transactions", "#081d58", "#081d58"),
]
# Plot style of the paper's other figures (SO-involvement, alignment): light-grey plot area,
# white horizontal grid lines, no frame.
PLOT_BACKGROUND = "#f5f5f5"


def load_rows() -> list[dict]:
    """The rows of INPUT_CSV with counts as int; checks that the segments add up."""
    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    count_columns = [c for columns, *_ in SEGMENTS for c in columns] + ["total"]
    for row in rows:
        for column in count_columns:
            row[column] = int(row[column])
        parts = sum(row[c] for columns, *_ in SEGMENTS for c in columns)
        if parts != row["total"]:
            raise ValueError(f"{row['label']}: segments add up to {parts}, total is {row['total']}")
    print(f"{len(rows)} rows in {INPUT_CSV.name}")
    return rows


def legend_label(label: str, columns: list[str], total_row: dict) -> str:
    """The segment's name, with its total over all programmes if LEGEND_TOTALS asks for it."""
    count = sum(total_row[c] for c in columns)
    if LEGEND_TOTALS == "count":
        return f"{label} ({count} in total)"
    if LEGEND_TOTALS == "share":
        return f"{label} ({count} in total, {100 * count / total_row['total']:.1f}%)"
    return label


def add_segment_labels(fig, ax, bar_x: list[float], segments: list[tuple]) -> None:
    """Each segment's count left of its bar, at the segment's middle, top segment first.

    The top count stays level with its segment (the top of the bar). A count that would come
    closer than SEGMENT_LABEL_MIN_SPACING_PT to the one above it is moved down, so thin
    segments on top do not push their counts above the bar. If that would put the bottom count
    below the axis (very short bars), all counts of the bar move up together.
    """
    fig.canvas.draw()  # final layout, so the axes height in points is known
    axes_height_pt = ax.get_window_extent().height * 72 / fig.dpi
    y_min, y_max = ax.get_ylim()
    min_spacing = SEGMENT_LABEL_MIN_SPACING_PT * (y_max - y_min) / axes_height_pt
    # The bar's left edge, moved left by the gap (in points).
    gap = SEGMENT_LABEL_GAP_PT / 72 * fig.dpi
    for i, xi in enumerate(bar_x):
        left_px = ax.transData.transform((xi - BAR_WIDTH / 2, 0))[0] - gap
        label_x = ax.transData.inverted().transform((left_px, 0))[0]
        placed = []  # (y, count, colour), top segment first
        for heights, bottoms, text_colour in reversed(segments):
            height = heights[i]
            if height == 0 and not SHOW_ZERO_SEGMENT_LABELS:
                continue
            y = bottoms[i] + height / 2
            if placed:
                y = min(y, placed[-1][0] - min_spacing)
            placed.append((y, height, text_colour))
        # Keep the lowest count's text above the axis (half a line above zero).
        lift = max(0, y_min + min_spacing / 2 - placed[-1][0]) if placed else 0
        for y, height, text_colour in placed:
            ax.text(label_x, y + lift, str(height), ha="right", va="center",
                    fontsize=SEGMENT_LABEL_FONT_SIZE_PT, color=text_colour)


def main() -> None:
    rows = load_rows()
    total_row = next(row for row in rows if row["area"] == "total")
    if not SHOW_TOTAL_BAR:
        rows = [row for row in rows if row["area"] != "total"]
    labels = [LABEL_RENAMES.get(row["label"], row["label"]) for row in rows]
    totals = [row["total"] for row in rows]
    x = range(len(rows))

    plt.rcParams.update({
        "font.family": "Arial",
        "font.size": FONT_SIZE_PT,
        "text.color": "black",
        "axes.labelcolor": "black",
        "xtick.color": "black",
        "ytick.color": "black",
        "pdf.fonttype": 42,  # real text in the PDF, not outlines
    })
    fig, ax = plt.subplots(figsize=(FIG_WIDTH_PT / 72, FIG_HEIGHT_PT / 72), layout="constrained")
    fig.get_layout_engine().set(w_pad=2 / 72, h_pad=2 / 72)
    ax.set_facecolor(PLOT_BACKGROUND)
    ax.grid(axis="y", color="white", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="y", length=0, pad=3)
    ax.tick_params(axis="x", length=0, pad=3)

    bar_x = [xi + BAR_SHIFT for xi in x]
    bottom = [0] * len(rows)
    segments = []  # per segment: (heights, bottoms, count colour)
    for columns, label, colour, text_colour in SEGMENTS:
        heights = [sum(row[c] for c in columns) for row in rows]
        ax.bar(bar_x, heights, BAR_WIDTH, bottom=bottom, color=colour, label=label, zorder=3)
        segments.append((heights, bottom, text_colour))
        bottom = [b + h for b, h in zip(bottom, heights)]
        print(f"  {label}: {heights}")

    top = max(totals) * Y_HEADROOM
    for xi, row in zip(bar_x, rows):
        text = f"{row['ieee_transactions']}/{row['total']}" if BAR_LABEL == "ieee/total" \
            else f"{row['total']}"
        ax.text(xi, row["total"] + top * 0.01, text, ha="center", va="bottom",
                fontsize=BAR_LABEL_FONT_SIZE_PT)
    if SHOW_TOTAL_BAR and SHOW_TOTAL_SEPARATOR:
        ax.axvline(len(rows) - 1.5, color="0.6", linewidth=0.6, linestyle="--", zorder=2)

    ax.set_xticks(list(x), labels, fontsize=XTICK_FONT_SIZE_PT)
    ax.set_xlim(-0.6, len(rows) - 0.4)
    ax.set_ylim(0, top)
    ax.yaxis.set_major_locator(plt.MultipleLocator(Y_TICK_STEP))
    ax.set_ylabel("Citations")
    # Legend top left, over the short programme bars; IEEE first, as the top segment.
    handles = [Patch(facecolor=colour, label=legend_label(label, columns, total_row))
               for columns, label, colour, _ in reversed(SEGMENTS)]
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=LEGEND_FONT_SIZE_PT,
              handlelength=1.2, handleheight=0.8, borderaxespad=0.3, labelspacing=0.25)
    if SHOW_SEGMENT_LABELS:
        add_segment_labels(fig, ax, bar_x, segments)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, bbox_inches="tight", pad_inches=0.01)
    print(f"Saved {OUTPUT}")
    if SAVE_PNG:
        png = OUTPUT.with_suffix(".png")
        fig.savefig(png, dpi=PNG_DPI, bbox_inches="tight", pad_inches=0.01, facecolor="white")
        print(f"Saved {png}")


if __name__ == "__main__":
    main()
