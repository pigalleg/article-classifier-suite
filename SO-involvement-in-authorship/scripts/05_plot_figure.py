"""Step 5: the SO-involvement figure of the paper, two columns wide.

(a) research papers per year, (b) yearly share of papers with an SO-affiliated author, (c)
heatmap of SO-affiliated papers by journal and year. Reads results/so_classification.csv
(step 3); counts research papers with affiliations only. Saves results/figures/SO_involvement.pdf
(and .png).
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import CLASSIFICATION_CSV, FIGURES  # noqa: E402

OUTPUT = FIGURES / "SO_involvement.pdf"
SAVE_PNG = True  # also save a PNG copy next to the PDF, for sharing
PNG_DPI = 300

# Which flag counts a paper as SO-affiliated in the heatmap (c):
#   "SO_TSO": an author works at a TSO / ISO / RTO
#   "SO_GRIDCO": ... at a state-owned or vertically integrated grid company
#   "SO_DSO": ... at a distribution utility
#   "SO_DIRECT": any of the three above
#   "SO_RESEARCH": ... at a research institute owned by one of them (CEPRI, IREQ, ...)
#   "HAS_SO": SO_DIRECT or SO_RESEARCH
HEATMAP_FLAG = "SO_DIRECT"
# The lines in (b), as (flag, legend label, colour). "heatmap" as the colour means the
# heatmap palette's colour. The orange is darker than the green, so the lines stay apart in
# greyscale print too. With a single line, no legend is drawn.
SHARE_LINES = [
    ("SO_DIRECT", "SOs and grid companies", "heatmap"),
    ("SO_RESEARCH", "SO-affiliated research institutes", "#D9731A"),
]
# Do not count papers whose only SO match is a borderline organisation (European utility
# groups such as EDF or Iberdrola, WECC, J-POWER, ...).
EXCLUDE_BORDERLINE = False
LEGEND_FONT_SIZE_PT = 6
# Room above the highest point in (b), as a multiple of it, so the legend clears the lines.
SHARE_HEADROOM = 1.1 if len(SHARE_LINES) < 2 else 1.45
# Fixed top of the share axis in (b), in percent; None sets it from the data and the headroom.
SHARE_Y_MAX = 16
# Tick spacing of the share axis in (b), in percent. None: from the axis height, every 1% up
# to 7%, every 2% up to 15%, else every 5%.
SHARE_TICK_STEP_PCT = 4

# Print size: IEEE Transactions full text width; matplotlib sizes are in inches and points.
FIG_WIDTH_PT = 516
FIG_HEIGHT_PT = 190
FONT_SIZE_PT = 8
PANEL_LETTER_FONT_SIZE_PT = 8
# Heatmap (c) text sizes.
HEATMAP_VALUE_FONT_SIZE_PT = 5  # counts in the cells, when SHOW_ALL_VALUES is on
HEATMAP_MAX_FONT_SIZE_PT = 6  # each journal's largest count (bold)
HEATMAP_START_YEAR_FONT_SIZE_PT = 7  # "2010", "2023" left of the start lines
COLOURBAR_TICK_FONT_SIZE_PT = 8
COLOURBAR_LABEL_FONT_SIZE_PT = 8
PANEL_LETTER_GAP_PT = 5  # space between (a), (b), (c) and the top of their panels
YEAR_TICK_LENGTH_PT = 3  # year ticks hang below each panel
# Colours, as in article-alignment-classifier/scripts/util/generate_article_figures.py:
# light-grey plot area with white grid lines, grey bars, and the affinity-level colours for
# the heatmap.
PLOT_BACKGROUND = "#f5f5f5"
BAR_COLOUR = "#a6a6a6"
# Heatmap scales: colours evenly spaced from 0 to the largest count, blended in between.
HEATMAP_PALETTES = {
    # LEVEL_COLORS of generate_article_figures.py (Low, Low+, Moderate, High, High+).
    # Moderate+ (#41c4b3) is left out: it is lighter than Moderate, so the scale would
    # not darken steadily.
    "levels": ["#ffffd9", "#d9f0a3", "#4ab68c", "#225ea8", "#081d58"],
    # Light to dark grey (ColorBrewer Greys); 0 stays visibly grey against white cells.
    "grey": ["#f0f0f0", "#bdbdbd", "#737373", "#252525"],
    # Light grey to the Moderate green of LEVEL_COLORS; black text stays readable throughout.
    "green": ["#f0f0f0", "#4ab68c"],
    # Light grey to the High blue of LEVEL_COLORS.
    "blue": ["#f0f0f0", "#225ea8"],
}
HEATMAP_PALETTE = "green"  # "levels", "grey", "green" or "blue"
# The heatmap palette's colour, for a line in (b) whose colour is "heatmap".
LINE_COLOURS = {"levels": "black", "grey": "black", "green": "#4ab68c", "blue": "#225ea8"}
PREPUBLICATION_COLOUR = "#FFFFFF"  # heatmap cells before a journal's first year
SHOW_ALL_VALUES = True  # a count in every heatmap cell; each journal's largest in bold
# The colour bar is only needed when the cells do not all show their counts.
SHOW_COLOURBAR = not SHOW_ALL_VALUES
# True: counts above 55% of the largest are white, for dark cells. False: all black.
WHITE_TEXT_ON_DARK_CELLS = False
SHOW_MAX_LABELS = True  # when SHOW_ALL_VALUES is off: only each journal's largest count
SHOW_FIRST_YEAR = True  # line and year where a journal starts (TSG, TSTE, TEMPR)
# Start-line height as a fraction of the cell height. Below 1 leaves a gap between rows,
# so TSG's and TSTE's 2010 lines stay separate; 1.0 joins them into one line.
START_LINE_HEIGHT = 1.0
TICK_YEAR_STEP = 5

JOURNAL_NAMES = {
    "IEEE Transactions on Energy Conversion": "TEC",
    "IEEE Transactions on Energy Markets, Policy and Regulation": "TEMPR",
    "IEEE Transactions on Power Delivery": "TPWRD",
    "IEEE Transactions on Power Systems": "TPWRS",
    "IEEE Transactions on Smart Grid": "TSG",
    "IEEE Transactions on Sustainable Energy": "TSTE",
}
JOURNAL_ORDER = ["TPWRS", "TPWRD", "TSG", "TSTE", "TEC", "TEMPR"]
SO_FLAGS = ["SO_TSO", "SO_GRIDCO", "SO_DSO", "SO_RESEARCH", "SO_DIRECT", "HAS_SO"]


def load_data() -> pd.DataFrame:
    """Research papers with affiliations, with HEATMAP_FLAG copied into column "SO"."""
    data = pd.read_csv(CLASSIFICATION_CSV, keep_default_na=False)
    print(f"{len(data)} rows in {CLASSIFICATION_CSV.name}")
    if EXCLUDE_BORDERLINE:
        data.loc[data["SO_BORDERLINE_ONLY"] == 1, SO_FLAGS] = 0
    data = data[(data["IS_PAPER"] == 1) & (data["HAS_AFFILIATIONS"] == 1)].copy()
    print(f"  research papers with affiliations: {len(data)}")
    unknown = set(data["publication_title"]) - set(JOURNAL_NAMES)
    if unknown:
        raise ValueError(f"Journals missing from JOURNAL_NAMES: {sorted(unknown)}")
    data["journal"] = data["publication_title"].map(JOURNAL_NAMES)
    data["SO"] = data[HEATMAP_FLAG]
    return data


def main() -> None:
    data = load_data()
    years = np.arange(data["publication_year"].min(), data["publication_year"].max() + 1)
    total = data.groupby("publication_year").size().reindex(years, fill_value=0)
    so = data[data["SO"] == 1]
    so_by_journal = (
        so.groupby(["journal", "publication_year"]).size().unstack(fill_value=0)
        .reindex(index=JOURNAL_ORDER, columns=years, fill_value=0)
    )
    first_year = data.groupby("journal")["publication_year"].min()
    # Lines in (b): yearly share of papers, in %, for each flag.
    heatmap_colour = LINE_COLOURS[HEATMAP_PALETTE]
    lines = [(flag, label, heatmap_colour if colour == "heatmap" else colour)
             for flag, label, colour in SHARE_LINES]
    shares = {flag: 100 * data.groupby("publication_year")[flag].mean().reindex(years)
              for flag, _, _ in lines}

    for flag, label, _ in lines:
        share = shares[flag]
        print(f"{label} ({flag}) share by year, %:")
        print(share.round(2).to_string())
        print(f"Mean over {years[0]}-{years[-1]}: {share.mean():.2f}%")
        for n in (2, 3, 5):
            last = share.iloc[-n:]
            print(f"Mean over last {n} years ({last.index[0]}-{last.index[-1]}): {last.mean():.2f}%")

    plt.rcParams.update({
        "font.family": "Arial",
        "font.size": FONT_SIZE_PT,
        "text.color": "black",
        "axes.labelcolor": "black",
        "xtick.color": "black",
        "ytick.color": "black",
        "axes.titlesize": PANEL_LETTER_FONT_SIZE_PT,
        "pdf.fonttype": 42,  # real text in the PDF, not outlines
    })
    fig = plt.figure(figsize=(FIG_WIDTH_PT / 72, FIG_HEIGHT_PT / 72), layout="constrained")
    fig.get_layout_engine().set(w_pad=2 / 72, h_pad=2 / 72, wspace=0.04, hspace=0.02)
    grid = fig.add_gridspec(2, 2, width_ratios=[1, 1.2])
    ax_total = fig.add_subplot(grid[0, 0])
    ax_share = fig.add_subplot(grid[1, 0], sharex=ax_total)
    ax_heat = fig.add_subplot(grid[:, 1])

    # (a) and (b): Plotly-like panels, grey plot area, white horizontal grid lines, no frame.
    for ax in (ax_total, ax_share):
        ax.set_facecolor(PLOT_BACKGROUND)
        ax.grid(axis="y", color="white", linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(axis="y", length=0, pad=3)
        # Year ticks point down, out of the plot; they also keep "2000" clear of the "0".
        ax.tick_params(axis="x", direction="out", length=YEAR_TICK_LENGTH_PT, width=0.8, pad=2)
        ax.set_xlim(years[0] - 0.6, years[-1] + 0.6)
    ax_total.bar(years, total.values, width=0.75, color=BAR_COLOUR)
    ax_total.set_ylabel("Total papers")
    ax_total.tick_params(axis="x", labelbottom=False)
    # The first line in the list is drawn on top where lines cross (higher zorder).
    for position, (flag, label, colour) in enumerate(lines):
        ax_share.plot(years, shares[flag].values, color=colour, linewidth=1.2, marker="o",
                      markersize=2.5, label=label, zorder=3 + len(lines) - position)
    if len(lines) > 1:
        ax_share.legend(loc="upper right", frameon=False, fontsize=LEGEND_FONT_SIZE_PT,
                        handlelength=1.6, borderaxespad=0.2, labelspacing=0.2)
    ax_share.set_ylabel("SO-affiliated, %")
    highest = max(s.max() for s in shares.values())
    top = SHARE_Y_MAX or highest * SHARE_HEADROOM
    if highest > top:
        print(f"Warning: SHARE_Y_MAX = {top} cuts off the largest share ({highest:.1f}%)")
    ax_share.set_ylim(0, top)  # tick spacing below follows the axis height, not the data
    tick_step = SHARE_TICK_STEP_PCT or (1 if top < 7 else 2 if top < 15 else 5)
    ax_share.yaxis.set_major_locator(plt.MultipleLocator(tick_step))
    ax_share.set_xticks(years[(years - years[0]) % TICK_YEAR_STEP == 0])
    fig.align_ylabels([ax_total, ax_share])

    # (c): heatmap of SO-affiliated papers; cells before a journal's first year are blank.
    values = so_by_journal.to_numpy(dtype=float)
    before_start = np.array([years < first_year[j] for j in JOURNAL_ORDER])
    colourmap = LinearSegmentedColormap.from_list(
        HEATMAP_PALETTE, HEATMAP_PALETTES[HEATMAP_PALETTE]
    )
    colourmap.set_bad(PREPUBLICATION_COLOUR)
    image = ax_heat.pcolormesh(
        np.arange(len(years) + 1) - 0.5, np.arange(len(JOURNAL_ORDER) + 1) - 0.5,
        np.ma.array(values, mask=before_start), cmap=colourmap, vmin=0, shading="flat",
        edgecolors="face", linewidth=0.2,  # no hairline seams between cells in PDF viewers
    )
    ax_heat.set_ylim(len(JOURNAL_ORDER) - 0.5, -0.5)  # first journal at the top
    ax_heat.set_yticks(range(len(JOURNAL_ORDER)), JOURNAL_ORDER)
    tick_years = years[(years - years[0]) % TICK_YEAR_STEP == 0]
    ax_heat.set_xticks(tick_years - years[0], tick_years)
    ax_heat.tick_params(axis="y", length=0, pad=3)
    ax_heat.tick_params(axis="x", direction="out", length=YEAR_TICK_LENGTH_PT, width=0.8, pad=2)
    for spine in ax_heat.spines.values():
        spine.set_visible(False)
    if SHOW_COLOURBAR:
        colourbar = fig.colorbar(image, ax=ax_heat, location="right", fraction=0.04, pad=0.02,
                                 aspect=25)
        colourbar.outline.set_visible(False)
        colourbar.ax.tick_params(length=2, pad=2, labelsize=COLOURBAR_TICK_FONT_SIZE_PT)
        colourbar.set_label("SO-affiliated papers", fontsize=COLOURBAR_LABEL_FONT_SIZE_PT)

    largest = np.nanmax(np.where(before_start, np.nan, values))
    for row, journal in enumerate(JOURNAL_ORDER):
        row_max = np.max(np.where(before_start[row], -1, values[row]))
        for column, value in enumerate(values[row]):
            if before_start[row, column]:
                continue
            is_max = value == row_max  # ties: every cell with the row's largest count
            if not (SHOW_ALL_VALUES or (SHOW_MAX_LABELS and is_max)):
                continue
            ax_heat.text(
                column, row, f"{value:.0f}", ha="center", va="center",
                fontsize=HEATMAP_MAX_FONT_SIZE_PT if is_max else HEATMAP_VALUE_FONT_SIZE_PT,
                fontweight="bold" if is_max else "normal",
                color="white" if WHITE_TEXT_ON_DARK_CELLS and value / largest > 0.55 else "black",
            )
        start = first_year[journal]
        if SHOW_FIRST_YEAR and start > years[0]:
            edge = start - years[0] - 0.5
            half = START_LINE_HEIGHT / 2
            ax_heat.vlines(edge, row - half, row + half, color="0.3", linewidth=1)
            # In the empty cells, left of the line.
            ax_heat.text(edge - 0.3, row, str(start), ha="right", va="center",
                         fontsize=HEATMAP_START_YEAR_FONT_SIZE_PT, color="0.3")

    for ax, letter in ((ax_total, "(a)"), (ax_share, "(b)"), (ax_heat, "(c)")):
        ax.set_title(letter, loc="left", fontweight="bold", pad=PANEL_LETTER_GAP_PT)

    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, bbox_inches="tight", pad_inches=0.01)
    print(f"Saved {OUTPUT}")
    if SAVE_PNG:
        png = OUTPUT.with_suffix(".png")
        fig.savefig(png, dpi=PNG_DPI, bbox_inches="tight", pad_inches=0.01, facecolor="white")
        print(f"Saved {png}")


if __name__ == "__main__":
    main()
