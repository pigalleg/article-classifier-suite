"""Generate TPWRS PRP publication trend figures and supporting tables."""

from __future__ import annotations

import argparse
import warnings
from contextlib import contextmanager, nullcontext
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_ROOT = REPO_ROOT / "data" / "results" / "affinity_benchmark"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "notebooks" / "outputs" / "tpwrs_prp_publications_by_year"
# Programme names as printed in the paper; the data files keep their original names.
PROGRAMME_DISPLAY_NAMES = {"System Services": "Services"}
PROGRAMME_ORDER = ["IBR", "Stability", "DER", "CROF", "Services", "Planning"]
LEVEL_ORDER = ["Low", "Low+", "Moderate", "Moderate+", "High", "High+"]
PLOT_LEVEL_ORDER = list(reversed(LEVEL_ORDER))
LEVEL_DTYPE = pd.CategoricalDtype(categories=LEVEL_ORDER, ordered=True)
THRESHOLDS = {
    "Moderate-or-better": ["Moderate", "Moderate+", "High", "High+"],
    "High-or-better": ["High", "High+"],
}
ERA_BINS = [2014, 2017, 2020, 2023, 2026]
ERA_LABELS = ["2015-17", "2018-20", "2021-23", "2024-26"]
LEVEL_COLORS = {
    "Low": "#ffffd9",
    "Low+": "#d9f0a3",
    "Moderate": "#4ab68c",
    "Moderate+": "#41c4b3",
    "High": "#225ea8",
    "High+": "#081d58",
}
ACCENT = "#104281"
CONTEXT = "#898781"
LOSS = "#2a78d6"
GAIN = "#e34948"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
FIGURE_DIM = (516, 310)
# Kaleido writes PDFs at 96 px per inch; print sizes are in points (72 per inch).
PX_PER_PT = 96 / 72
IEEE_COLUMN_WIDTH_PT = 252  # IEEE Transactions single column, 3.5 in
FONT_FAMILY = "Arial"
ARTICLE_FONT_SIZE_PT = 8
# Figures saved without Kaleido's PDF page margins, so the page is exactly the figure.
ZERO_MARGIN_PDF_FIGURES = {
    "article_ra_affinity_distribution",
    "headline_agenda_reach",
    "programme_coverage_by_year",
    "programme_coverage_era_shift",
}
COVERAGE_HEIGHT_PT = 330  # six panels, 3 rows x 2
COVERAGE_SUMMARY_FONT_SIZE_PT = 5.5  # summary text in each panel
# Change from the first to the last era (ERA_LABELS), after each 2015-2026 share.
COVERAGE_SHOW_CHANGE = True
COVERAGE_COLOUR_CHANGE = False  # False: arrows and numbers in black
COVERAGE_CHANGE_COLOURS = {"up": "#1a9641", "down": "#d7191c", "small": "#808080"}
COVERAGE_SMALL_CHANGE_PP = 2  # changes smaller than this, in percentage points, are grey
# Low in grey rather than LEVEL_COLORS' pale yellow; darker than the plot background.
COVERAGE_LEVEL_COLORS = {**LEVEL_COLORS, "Low": "#d9d9d9"}
ERA_SHIFT_HEIGHT_PT = 300  # two panels, one per threshold
ERA_SHIFT_LABEL_FONT_SIZE_PT = 7  # the "+10.9 pp" change labels
HEADLINE_HEIGHT_PT = 200
# Years and paper counts on the x axis; 8 pt "2015" is wider than the space per year.
HEADLINE_YEAR_TICK_FONT_SIZE_PT = 7
HEADLINE_COUNT_FONT_SIZE_PT = 6  # paper counts "(358)" under each year
# Headline lines, in drawing order: the first is drawn at the back, the last on top.
# "first"/"last": whether the first and last values sit above or below their markers
# (chosen so labels of crossing lines do not collide). The line name goes at the right
# edge, name_dy below the last value, or, if name_year is set, centred at that year,
# name_dy from the line.
HEADLINE_LINES = {
    # Papers whose best programme reaches only Low or Low+ (none reaches Moderate).
    "Low": dict(colour="#808080", first="above", last="below", name_year=None, name_dy=-10),
    "Moderate-or-better": dict(
        colour=LEVEL_COLORS["Moderate"], first="above", last="above", name_year=None, name_dy=-7
    ),
    "High-or-better": dict(
        colour=LEVEL_COLORS["High"], first="below", last="above", name_year=None, name_dy=-15
    ),
}
ARTICLE_RUN_ID = "tpwrs_2026_tpwrs_2026"
ARTICLE_IGNORE_MODELS = [
    "smollm2-1.7b",
    "gemma3-4b",
    "phi3-3.8b",
    "deepseek-v3.2-cloud",
    "qwen3.5-397b-cloud",
]
ARTICLE_SHOW_BAND_LABELS = True  # band names (Low+ ... High+) at the right of the plot
ARTICLE_HIDDEN_BAND_LABELS = {"Low"}  # "Low" would sit on top of the data
ARTICLE_BAND_LABEL_COLOUR = "#808080"
ARTICLE_SHOW_MEDIAN = False  # median line inside each box
ARTICLE_SHOW_POINTS = False  # individual model scores drawn over each box
ARTICLE_POINT_JITTER = 0.3  # sideways spread of those points; 0 centres them exactly
ARTICLE_PLOT_BACKGROUND = "#f5f5f5"
ARTICLE_ONE_LINE_LEGEND = False  # one text line above the plot; False uses Plotly's legend
ARTICLE_LEGEND_GAP = 2  # spaces between one-line legend entries
ARTICLE_PROGRAMME_COLOURS = {
    "IBR": "#4f8b75",
    "Planning": "#9b6fb6",
    "Stability": "#d96c7c",
    "CROF": "#3c9fca",
    "Services": "#78ae68",
    "DER": "#d68a4a",
}


def ieee_column_layout(height_pt: float) -> dict:
    """Shared paper style: one IEEE column at print size, Arial, black text, grey plot area.

    Sizes are given in points and converted to Plotly's pixels, so fonts print at
    ARTICLE_FONT_SIZE_PT when the PDF is included at column width.
    """
    return dict(
        width=round(IEEE_COLUMN_WIDTH_PT * PX_PER_PT),
        height=round(height_pt * PX_PER_PT),
        font=dict(family=FONT_FAMILY, size=ARTICLE_FONT_SIZE_PT * PX_PER_PT, color="black"),
        plot_bgcolor=ARTICLE_PLOT_BACKGROUND,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate TPWRS PRP publication-by-year figures and Excel tables."
    )
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=DEFAULT_BENCHMARK_ROOT,
        help=f"Benchmark result directory (default: {DEFAULT_BENCHMARK_ROOT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for PDFs and Excel files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--article-run-id",
        default=ARTICLE_RUN_ID,
        help=f"Benchmark run for the article affinity figure (default: {ARTICLE_RUN_ID})",
    )
    return parser.parse_args()


def use_display_names(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename programmes for the paper and check every programme is one we plot."""
    frame = frame.assign(PRP_Name=frame["PRP_Name"].replace(PROGRAMME_DISPLAY_NAMES))
    unknown = set(frame["PRP_Name"].dropna()) - set(PROGRAMME_ORDER)
    if unknown:
        raise ValueError(f"Programmes missing from PROGRAMME_ORDER: {sorted(unknown)}")
    return frame


def load_assignments(benchmark_root: Path) -> pd.DataFrame:
    """Load each yearly TPWRS adjudication result and label PRP affinities."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.analysis import assign_prps

    result_paths = sorted(benchmark_root.glob("tpwrs_*/adjudicated_ra_affinities.csv"))
    if not result_paths:
        raise FileNotFoundError(
            f"No TPWRS adjudicated RA affinity files found under {benchmark_root}"
        )

    assignments = pd.concat(
        [
            assign_prps(pd.read_csv(path), six_bands=True).assign(
                Year=int(path.parent.name.split("_")[1])
            )
            for path in result_paths
        ],
        ignore_index=True,
    )
    assignments = use_display_names(assignments)
    assignments["Affinity_Level"] = assignments["Affinity_Level"].astype(LEVEL_DTYPE)
    assignments["Era"] = pd.cut(
        assignments["Year"], bins=ERA_BINS, labels=ERA_LABELS
    )
    return assignments


def share_by(
    assignments_df: pd.DataFrame,
    totals: pd.Series,
    *group_columns: str,
) -> pd.DataFrame:
    """Calculate each threshold's share of papers within each group."""
    frames = [
        assignments_df[assignments_df["Affinity_Level"].isin(levels)]
        .groupby(list(group_columns), observed=True)["Abstract_Index"]
        .nunique()
        .rename("Papers_Reaching")
        .reset_index()
        .assign(Threshold=label)
        for label, levels in THRESHOLDS.items()
    ]
    share = pd.concat(frames, ignore_index=True).merge(
        totals.reset_index(), on=totals.index.name
    )
    share["Share"] = 100 * share["Papers_Reaching"] / share["Papers_Total"]
    return share


def add_low_share(reach_share: pd.DataFrame) -> pd.DataFrame:
    """Add a "Low" series: papers whose best programme reaches only Low or Low+.

    share_by counts papers with *any* programme in the given levels, which for Low would
    include nearly every paper, so Low is computed as the complement of Moderate-or-better.
    """
    moderate = reach_share[reach_share["Threshold"] == "Moderate-or-better"]
    low = moderate.assign(
        Threshold="Low",
        Papers_Reaching=moderate["Papers_Total"] - moderate["Papers_Reaching"],
        Share=100 - moderate["Share"],
    )
    return pd.concat([reach_share, low], ignore_index=True)


def build_headline_figure(
    reach_share: pd.DataFrame,
    years: list[int],
    year_ticktext: list[str],
) -> px.line:
    """Build the overall agenda reach figure."""
    figure = px.line(
        reach_share[reach_share["Threshold"].isin(HEADLINE_LINES)],
        x="Year",
        y="Share",
        color="Threshold",
        color_discrete_map={name: line["colour"] for name, line in HEADLINE_LINES.items()},
        markers=True,
        line_shape="linear",
        category_orders={"Threshold": list(HEADLINE_LINES)},
    )
    figure.update_traces(line_width=1.5, marker_size=5)
    for threshold, line in HEADLINE_LINES.items():
        color = line["colour"]
        series = reach_share[reach_share["Threshold"] == threshold].set_index("Year")["Share"]
        first = dict(Year=series.index[0], Share=series.iloc[0])
        last = dict(Year=series.index[-1], Share=series.iloc[-1])
        name = dict(text=f"<b>{threshold}", showarrow=False, yanchor="middle", font=dict(color=color))
        if line["name_year"] is None:
            # Right-aligned at the plot edge, offset from the line's last value.
            figure.add_annotation(
                **name, x=1, xref="paper", xanchor="right", xshift=-3,
                y=last["Share"] + line["name_dy"],
            )
        else:
            figure.add_annotation(
                **name, x=line["name_year"], xanchor="center",
                y=series[line["name_year"]] + line["name_dy"],
            )
        # figure.add_annotation(
        #     x=first["Year"],
        #     y=first["Share"] + 13,
        #     text=f"2015 -> 2026: <b>{first['Share']:.1f}% -> {last['Share']:.1f}% ",
        #     showarrow=False,
        #     xanchor="left",
        #     yanchor="middle",
        #     font=dict(color=color),
        # )
        # Values of the first and last points, just above or below their markers.
        for point, side in ((first, line["first"]), (last, line["last"])):
            figure.add_annotation(
                x=point["Year"],
                y=point["Share"],
                yshift=5 if side == "above" else -5,
                text=f"{point['Share']:.1f}%",
                showarrow=False,
                xanchor="center",
                yanchor="bottom" if side == "above" else "top",
                font=dict(color=color),
            )
    figure.update_layout(
        **ieee_column_layout(height_pt=HEADLINE_HEIGHT_PT),
        showlegend=False,
        # The axes reserve their own space; the small top margin keeps "100%" on the page.
        margin=dict(l=0, r=0, t=round(5 * PX_PER_PT), b=0),
    )
    figure.update_xaxes(
        title=None,  # the partial 2026 is explained in the caption
        tickmode="array",
        tickvals=years,
        ticktext=year_ticktext,
        tickfont_size=HEADLINE_YEAR_TICK_FONT_SIZE_PT * PX_PER_PT,
        tickangle=0,  # never rotate the year labels
        # 0.7 years either side leaves room for the values centred on the end markers.
        range=[years[0] - 0.7, years[-1] + 0.7],
        automargin=True,
    )
    figure.update_yaxes(
        title=dict(text="Share of papers, %", standoff=4),  # the unit is in the title
        range=[0, 100],
        dtick=20,
        ticklabelstandoff=4,
        automargin=True,
    )
    return figure


def build_coverage_data(assignments_df: pd.DataFrame, papers_per_year: pd.Series) -> pd.DataFrame:
    coverage_levels = (
        assignments_df.groupby(["Year", "PRP_Name", "Affinity_Level"], observed=True)[
            "Abstract_Index"
        ]
        .nunique()
        .rename("Papers")
        .reset_index()
    )
    return (
        pd.MultiIndex.from_product(
            [papers_per_year.index, PROGRAMME_ORDER, PLOT_LEVEL_ORDER],
            names=["Year", "PRP_Name", "Affinity_Level"],
        )
        .to_frame(index=False)
        .merge(coverage_levels, on=["Year", "PRP_Name", "Affinity_Level"], how="left")
        .merge(papers_per_year.reset_index(), on="Year", how="left")
        .fillna({"Papers": 0})
        .assign(Share=lambda frame: 100 * frame["Papers"] / frame["Papers_Total"])
    )


def change_text(change: float) -> str:
    """Format an era change as an arrow and size, e.g. (▲10.9 pp)."""
    arrow = "▲" if change > 0 else "▼"
    if not COVERAGE_COLOUR_CHANGE:
        return f"({arrow}{abs(change):.1f} pp)"
    if abs(change) < COVERAGE_SMALL_CHANGE_PP:
        colour = COVERAGE_CHANGE_COLOURS["small"]
    else:
        colour = COVERAGE_CHANGE_COLOURS["up" if change > 0 else "down"]
    return f"(<span style='color:{colour}'>{arrow}{abs(change):.1f}</span> pp)"


def build_coverage_figure(
    coverage_plot_data: pd.DataFrame, era_shift: pd.DataFrame
) -> px.area:
    """Build the faceted six-band programme coverage figure."""
    era_change = era_shift.set_index(["PRP_Name", "Threshold"])["Change"]
    all_year_coverage = (
        coverage_plot_data.groupby(
            ["PRP_Name", "Affinity_Level"], observed=True
        )[["Papers", "Papers_Total"]]
        .sum()
        .assign(Share=lambda frame: 100 * frame["Papers"] / frame["Papers_Total"])
        .reset_index()
    )
    overall_shares = (
        pd.concat(
            [
                all_year_coverage.loc[
                    lambda frame: frame["Affinity_Level"].isin(
                        THRESHOLDS["Moderate-or-better"]
                    )
                ].assign(Threshold="Moderate-or-better"),
                all_year_coverage.loc[
                    lambda frame: frame["Affinity_Level"].isin(
                        THRESHOLDS["High-or-better"]
                    )
                ].assign(Threshold="High-or-better"),
            ],
            ignore_index=True,
        )
        .groupby(["PRP_Name", "Threshold"], observed=True)
        .agg(Papers=("Papers", "sum"), Papers_Total=("Papers_Total", "first"))
        .assign(Share=lambda frame: 100 * frame["Papers"] / frame["Papers_Total"])
    )
    figure = px.area(
        coverage_plot_data,
        x="Year",
        y="Share",
        color="Affinity_Level",
        color_discrete_map=COVERAGE_LEVEL_COLORS,
        facet_col="PRP_Name",
        facet_col_wrap=2,
        facet_row_spacing=0.07,
        facet_col_spacing=0.04,
        category_orders={
            "PRP_Name": PROGRAMME_ORDER,
            "Affinity_Level": PLOT_LEVEL_ORDER,
        },
        labels={"Affinity_Level": ""},
    )
    figure.update_traces(line_width=1)
    figure.update_layout(
        **ieee_column_layout(height_pt=COVERAGE_HEIGHT_PT),
        legend=dict(
            title_text="",
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0,
            itemwidth=30,
        ),
        # The axes and legend reserve their own space.
        margin=dict(l=0, r=0, t=0, b=0),
    )
    figure.update_yaxes(
        title=None,
        range=[0, 100],
        dtick=20,
        ticklabelstandoff=4,
        automargin=True,
    )
    # One y title for all panels, on the middle row so it is centred.
    figure.update_yaxes(title=dict(text="Share of papers, %", standoff=4), row=2, col=1)
    figure.update_xaxes(title=None, dtick=3, automargin=True)
    facet_axes = {}
    for trace in figure.data:
        programme = trace.hovertemplate.split("PRP_Name=", 1)[1].split("<br>", 1)[0]
        facet_axes.setdefault(programme, (trace.xaxis, trace.yaxis))
    # Rename all panel titles before adding the summaries: add_annotation replaces the
    # annotation list, so titles edited after it would not reach the figure.
    programmes = []
    for annotation in figure.layout.annotations:
        programme = annotation.text.split("=", 1)[-1]
        programmes.append(programme)
        annotation.text = f"<b>{programme}</b>"
    for programme in programmes:
        lines = []
        for threshold in THRESHOLDS:
            line = f"{threshold}: {overall_shares.loc[(programme, threshold), 'Share']:.1f}%"
            if COVERAGE_SHOW_CHANGE:
                line += " " + change_text(era_change[(programme, threshold)])
            lines.append(line)
        xaxis, yaxis = facet_axes[programme]
        figure.add_annotation(
            x=0.02,
            y=0.96,
            xref=f"{xaxis} domain",
            yref=f"{yaxis} domain",
            text="<br>".join(lines),
            showarrow=False,
            xanchor="left",
            yanchor="top",
            align="left",
            font=dict(size=COVERAGE_SUMMARY_FONT_SIZE_PT * PX_PER_PT),
        )
    return figure


def build_era_shift_data(
    assignments_df: pd.DataFrame, papers_per_era: pd.Series
) -> pd.DataFrame:
    era_coverage = share_by(assignments_df, papers_per_era, "Era", "PRP_Name")
    return (
        era_coverage[era_coverage["Era"].isin([ERA_LABELS[0], ERA_LABELS[-1]])]
        .pivot(index=["Threshold", "PRP_Name"], columns="Era", values="Share")
        .assign(Change=lambda frame: frame[ERA_LABELS[-1]] - frame[ERA_LABELS[0]])
        .reset_index()
    )


def build_era_shift_figure(era_shift: pd.DataFrame) -> px.line:
    """Build the programme coverage endpoint comparison figure."""
    plot_data = era_shift.melt(
        id_vars=["Threshold", "PRP_Name", "Change"],
        var_name="Era",
        value_name="Share",
    )
    plot_data["Change_direction"] = pd.Categorical(
        plot_data["Change"].lt(0).map({True: "+", False: "-"}),
        categories=["+", "-"],
        ordered=True,
    )
    figure = px.line(
        plot_data,
        x="Share",
        y="PRP_Name",
        symbol="PRP_Name",
        color="Change_direction",
        color_discrete_map={"-": LOSS, "+": GAIN},
        facet_row="Threshold",
        facet_row_spacing=0.09,
        markers=True,
        category_orders={
            "Threshold": list(THRESHOLDS),
            "PRP_Name": PROGRAMME_ORDER,
        },
    )
    figure.update_traces(marker=dict(symbol="circle", size=5), line_width=1)
    figure.update_xaxes(
        matches=None,
        showticklabels=True,
        nticks=8,
        title=None,
        automargin=True,
    )
    figure.update_yaxes(
        title=None,
        tickmode="array",
        tickvals=PROGRAMME_ORDER,
        ticklabelstandoff=4,
        automargin=True,
    )
    figure.update_layout(
        **ieee_column_layout(height_pt=ERA_SHIFT_HEIGHT_PT),
        showlegend=False,
        # The axes reserve their own space; the top panel's title, an annotation, does not.
        margin=dict(l=0, r=0, t=round(12 * PX_PER_PT), b=0),
    )

    facet_axes = {}
    for trace in figure.data:
        threshold = trace.hovertemplate.split("Threshold=", 1)[1].split("<br>", 1)[0]
        facet_axes.setdefault(threshold, (trace.xaxis, trace.yaxis))
    # One x title, under the bottom panel.
    bottom_xaxis = facet_axes[list(THRESHOLDS)[-1]][0]
    figure.layout["xaxis" + bottom_xaxis[1:]].title = dict(
        text="Share of papers, %", standoff=4
    )

    for annotation in figure.layout.annotations:
        if annotation.text.startswith("Threshold="):
            threshold = annotation.text.split("=", 1)[-1]
            xaxis, yaxis = facet_axes[threshold]
            annotation.update(
                text=f"<b>{threshold}</b>",
                x=0.5,
                y=1.0,
                xref=f"{xaxis} domain",
                yref=f"{yaxis} domain",
                xanchor="center",
                yanchor="bottom",
                textangle=0,
            )

    for threshold in THRESHOLDS:
        xaxis, yaxis = facet_axes[threshold]
        for _, row in era_shift[era_shift["Threshold"] == threshold].iterrows():
            change = row["Change"]
            colour = LOSS if change >= 0 else GAIN
            figure.add_annotation(
                x=row[ERA_LABELS[-1]],
                y=row["PRP_Name"],
                ax=row[ERA_LABELS[0]],
                ay=row["PRP_Name"],
                xref=xaxis,
                yref=yaxis,
                axref=xaxis,
                ayref=yaxis,
                showarrow=True,
                arrowhead=2,
                arrowsize=1,
                arrowwidth=1.5,
                arrowcolor=colour,
            )
            midpoint = (row[ERA_LABELS[0]] + row[ERA_LABELS[-1]]) / 2
            figure.add_annotation(
                x=midpoint,
                y=row["PRP_Name"],
                xref=xaxis,
                yref=yaxis,
                text=f"<b>{change:+.1f} pp</b>",
                yshift=3,
                xanchor="center",
                yanchor="bottom",
                showarrow=False,
                font=dict(color=colour, size=ERA_SHIFT_LABEL_FONT_SIZE_PT * PX_PER_PT),
            )
    return figure


# Figure 4: selected-article RA affinity distribution
def build_article_ra_plot_data(
    benchmark_root: Path, article_run_id: str
) -> tuple[pd.DataFrame, pd.DataFrame, object]:
    """Load the notebook's selected RA case and comparison-series scores."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    run_dir = benchmark_root / article_run_id
    merged_path = run_dir / "merged_ra_affinities.csv"
    adjudicated_path = run_dir / "adjudicated_ra_affinities.csv"
    missing_paths = [path for path in (merged_path, adjudicated_path) if not path.exists()]
    if missing_paths:
        raise FileNotFoundError(
            "Missing required article figure input(s): "
            + ", ".join(str(path) for path in missing_paths)
        )

    benchmark_rows = pd.read_csv(merged_path)
    benchmark_rows = benchmark_rows[
        ~benchmark_rows["Model_Slug"].isin(ARTICLE_IGNORE_MODELS)
    ].copy()
    benchmark_models = set(benchmark_rows["Model_Slug"].dropna().astype(str))
    adjudicated_rows = pd.read_csv(adjudicated_path)
    final_rows = adjudicated_rows.rename(
        columns={
            "Final_Affinity": "LLM_Affinity",
            "Final_Reason": "LLM_Affinity_Reason",
        }
    ).copy()
    provided_slug = final_rows["Model_Slug"].astype("string")
    final_method = final_rows["Final_Method"].fillna("adjudicator").astype(str)
    final_rows["Model_Slug"] = np.where(
        provided_slug.notna(),
        provided_slug.astype(str) + "_adjudicator",
        final_method,
    )
    for column in benchmark_rows.columns:
        if column not in final_rows.columns:
            final_rows[column] = pd.NA
    comparison_rows = pd.concat(
        [benchmark_rows, final_rows[benchmark_rows.columns]], ignore_index=True
    )

    mapping = pd.read_csv(REPO_ROOT / "data" / "processed" / "ra_grouping_ra2025.csv")
    ra_to_programme = (
        mapping[["RA2025", "Grouping"]]
        .dropna()
        .drop_duplicates()
        .rename(columns={"RA2025": "RA2025_ID", "Grouping": "PRP_Name"})
    )
    ra_to_programme["RA2025_ID"] = pd.to_numeric(
        ra_to_programme["RA2025_ID"], errors="coerce"
    ).astype("Int64")
    comparison_rows["RA2025_ID"] = pd.to_numeric(
        comparison_rows["RA2025_ID"], errors="coerce"
    ).astype("Int64")
    comparison_rows = use_display_names(
        comparison_rows.merge(ra_to_programme, on="RA2025_ID", how="left")
    )

    selected_abstract = "10.1109/TPWRS.2026.3659399"
    selected_rows = comparison_rows[
        comparison_rows["Abstract_Index"] == selected_abstract
    ].copy()
    selected_rows["Comparison_Type"] = np.where(
        selected_rows["Model_Slug"].isin(benchmark_models), "Benchmark", "Final"
    )
    return (
        selected_rows[selected_rows["Comparison_Type"] == "Benchmark"],
        selected_rows[selected_rows["Comparison_Type"] == "Final"],
        selected_abstract,
    )


def _rgba(hex_colour: str, alpha: float) -> str:
    red, green, blue = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"


def build_article_ra_figure(
    benchmark_rows: pd.DataFrame,
    final_rows: pd.DataFrame,
    selected_abstract: object,
) -> go.Figure:
    """Build the annotated selected-article RA affinity distribution figure.

    Boxes are drawn from precomputed statistics rather than with ``px.box``, because
    Plotly box plots always draw a median line; here the median is optional. Each box
    spans the quartiles, and its whiskers run to the lowest and highest model score.
    """
    from src.analysis.affinity_levels import affinity_level_fixed

    box_width = 0.6  # in question units, so neighbouring boxes do not touch
    scores = benchmark_rows.assign(
        LLM_Affinity=pd.to_numeric(benchmark_rows["LLM_Affinity"], errors="coerce")
    ).dropna(subset=["LLM_Affinity"])
    # "hazen" quartiles are the ones Plotly box plots draw by default.
    box_stats = (
        scores.groupby(["PRP_Name", "RA2025_ID"], observed=True)["LLM_Affinity"]
        .agg(
            Lowest="min",
            Q1=lambda values: np.percentile(values, 25, method="hazen"),
            Median="median",
            Q3=lambda values: np.percentile(values, 75, method="hazen"),
            Highest="max",
        )
        .reset_index()
    )
    # Fixed seed so the jittered points are identical on every run.
    jitter_rng = np.random.default_rng(0)

    figure = go.Figure()
    for programme in PROGRAMME_ORDER:
        stats = box_stats[box_stats["PRP_Name"] == programme]
        if stats.empty:
            continue
        colour = ARTICLE_PROGRAMME_COLOURS[programme]
        questions = stats["RA2025_ID"].astype(float)

        def segments(x_starts, x_ends, y_starts, y_ends):
            """Interleave line segments with gaps for a single scatter trace."""
            xs, ys = [], []
            for x0, x1, y0, y1 in zip(x_starts, x_ends, y_starts, y_ends):
                xs += [x0, x1, None]
                ys += [y0, y1, None]
            return xs, ys

        whisker_x, whisker_y = segments(
            [*questions, *questions],
            [*questions, *questions],
            [*stats["Lowest"], *stats["Q3"]],
            [*stats["Q1"], *stats["Highest"]],
        )
        figure.add_scatter(
            x=whisker_x,
            y=whisker_y,
            mode="lines",
            line=dict(color=colour, width=1.2),
            legendgroup=programme,
            showlegend=False,
            hoverinfo="skip",
        )
        figure.add_bar(
            x=questions,
            y=stats["Q3"] - stats["Q1"],
            base=stats["Q1"],
            width=box_width,
            name=programme,
            legendgroup=programme,
            marker=dict(color=_rgba(colour, 0.5), line=dict(color=colour, width=1.2)),
            customdata=stats[["Lowest", "Q1", "Median", "Q3", "Highest"]],
            hovertemplate=(
                f"{programme}, question %{{x}}<br>lowest %{{customdata[0]}}, "
                "Q1 %{customdata[1]}, median %{customdata[2]}, Q3 %{customdata[3]}, "
                "highest %{customdata[4]}<extra></extra>"
            ),
        )
        if ARTICLE_SHOW_MEDIAN:
            median_x, median_y = segments(
                questions - box_width / 2,
                questions + box_width / 2,
                stats["Median"],
                stats["Median"],
            )
            figure.add_scatter(
                x=median_x,
                y=median_y,
                mode="lines",
                line=dict(color=colour, width=1),
                legendgroup=programme,
                showlegend=False,
                hoverinfo="skip",
            )
        if ARTICLE_SHOW_POINTS:
            programme_scores = scores[scores["PRP_Name"] == programme]
            offsets = (
                jitter_rng.uniform(-0.5, 0.5, len(programme_scores))
                * box_width
                * ARTICLE_POINT_JITTER
            )
            figure.add_scatter(
                x=programme_scores["RA2025_ID"].astype(float) + offsets,
                y=programme_scores["LLM_Affinity"],
                mode="markers",
                marker=dict(color=colour, size=3),
                legendgroup=programme,
                showlegend=False,
                hoverinfo="skip",
            )
    # Band names sit inside the plot at its right edge, centred in each band; the y
    # gridlines (every 20) mark the band boundaries.
    for band_start in range(0, 120, 20) if ARTICLE_SHOW_BAND_LABELS else []:
        band = affinity_level_fixed(band_start, six_bands=True)
        if band in ARTICLE_HIDDEN_BAND_LABELS:
            continue
        figure.add_annotation(
            x=1,
            xref="paper",
            xanchor="right",
            xshift=-3,
            y=band_start + 10,
            yref="y",
            # Labels are right-aligned, so "High" gets an invisible "=" (as wide as "+" in
            # Arial) to start exactly where "High+" starts.
            text=band if band.endswith("+") else f"{band}<span style='color:rgba(0,0,0,0)'>=</span>",
            font=dict(color=ARTICLE_BAND_LABEL_COLOUR),
            showarrow=False,
        )
    figure.add_scatter(
        x=final_rows["RA2025_ID"],
        y=final_rows["LLM_Affinity"],
        mode="markers",
        name="Mean",
        marker=dict(size=4, symbol="circle", color="black"),
        hovertext=final_rows["PRP_Name"],
        hovertemplate=(
            "RA2025_ID=%{x}<br>LLM_Affinity=%{y}<br>PRP=%{hovertext}<extra></extra>"
        ),
    )
    if ARTICLE_ONE_LINE_LEGEND:
        # Plotly's legend needs two rows at this width (each entry has a 30 px symbol and a
        # fixed gap), so the legend is one line of text: a coloured square per programme,
        # then a dot for the mean. Anchored to the right edge, where it has the most room.
        entries = [
            f"<span style='color:{ARTICLE_PROGRAMME_COLOURS[programme]}'>■</span>"
            f" {programme}"
            for programme in PROGRAMME_ORDER
        ] + ["● Mean"]
        figure.add_annotation(
            x=1,
            xref="paper",
            xanchor="right",
            y=1,
            yref="paper",
            yanchor="bottom",
            yshift=2,
            text=(" " * ARTICLE_LEGEND_GAP).join(entries),
            showarrow=False,
        )
    figure.update_layout(
        **ieee_column_layout(height_pt=200),
        showlegend=not ARTICLE_ONE_LINE_LEGEND,
        xaxis_title="G-PST Research Agenda question",
        yaxis_title="Affinity score",
        # Each question belongs to one programme; "group" mode would offset each box
        # into its programme's slot, away from the question's x position and its mean marker.
        barmode="overlay",
        # automargin sizes the margins to fit the tick labels and axis titles exactly.
        xaxis=dict(
            range=[0, 49],
            tickmode="array",
            tickvals=[1, 10, 20, 30, 40, 48],  # first and last question, plus every 10
            title_standoff=8,  # gap (px) between the tick labels and the axis title
            automargin=True,
        ),
        yaxis=dict(
            range=[-0.5, 120],  # a little room below 0 so boxes starting at 0 are not clipped
            dtick=20,
            title_standoff=4,
            ticklabelstandoff=4,
            automargin=True,
        ),
        legend=dict(
            title=None,
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            itemwidth=30,
            tracegroupgap=0,
        ),
        # No extra margins: the axes and Plotly's legend reserve their own space. The
        # one-line legend is an annotation, which does not, so it gets a 12 pt top margin.
        margin=dict(l=0, r=0, t=round(12 * PX_PER_PT) if ARTICLE_ONE_LINE_LEGEND else 0, b=0),
    )
    return figure


@contextmanager
def pdf_pages_without_margins():
    """Temporarily make Kaleido print PDFs with zero page margins.

    Kaleido (1.4-1.5) prints every PDF with fixed 0.1 in page margins and shrinks the
    figure to fit inside them, so fonts come out about 5% smaller than specified and the
    bottom edge can be clipped. This swaps in the same print call with zero margins. It
    relies on Kaleido internals; if those change, figures are saved the default way.
    """
    try:
        from kaleido._kaleido_tab import _devtools_utils
    except ImportError:
        warnings.warn("Kaleido internals changed; PDFs keep Kaleido's page margins.")
        yield
        return

    async def print_pdf_without_margins(tab):
        response = await tab.send_command(
            "Page.printToPDF",
            params={
                "printBackground": True,
                "marginTop": 0,
                "marginBottom": 0,
                "marginLeft": 0,
                "marginRight": 0,
                "preferCSSPageSize": True,
                "pageRanges": "1",
            },
        )
        _devtools_utils._raise_error(response)
        return response.get("result", {}).get("data")

    original = _devtools_utils.print_pdf
    _devtools_utils.print_pdf = print_pdf_without_margins
    try:
        yield
    finally:
        _devtools_utils.print_pdf = original


def write_outputs(output_dir: Path, figures: dict[str, object], tables: dict[str, pd.DataFrame]) -> None:
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    for slug, figure in figures.items():
        exact_page = slug in ZERO_MARGIN_PDF_FIGURES
        with pdf_pages_without_margins() if exact_page else nullcontext():
            figure.write_image(
                str(figure_dir / f"{slug}.pdf"),
                width=figure.layout.width,
                height=figure.layout.height,
            )
    for slug, table in tables.items():
        table.to_excel(output_dir / f"{slug}.xlsx", index=False)


def main() -> None:
    args = parse_args()

    # Figures 1-3: TPWRS publication trends by year and programme.
    assignments_df = load_assignments(args.benchmark_root)
    papers_per_year = assignments_df.groupby("Year")["Abstract_Index"].nunique().rename("Papers_Total")
    papers_per_era = (
        assignments_df.groupby("Era", observed=True)["Abstract_Index"]
        .nunique()
        .rename("Papers_Total")
    )
    years = papers_per_year.index.tolist()
    # Each tick label is one text with a line break; the span gives the count its own size.
    count_font_px = HEADLINE_COUNT_FONT_SIZE_PT * PX_PER_PT
    year_ticktext = [
        f"{year}<br><span style='font-size:{count_font_px:.2f}px'>({papers_per_year[year]})</span>"
        for year in years
    ]
    reach_share = add_low_share(share_by(assignments_df, papers_per_year, "Year"))
    coverage_plot_data = build_coverage_data(assignments_df, papers_per_year)
    era_shift = build_era_shift_data(assignments_df, papers_per_era)

    # Figure 4: selected high-disagreement article's RA score distribution.
    article_benchmark_rows, article_final_rows, selected_abstract = build_article_ra_plot_data(
        args.benchmark_root, args.article_run_id
    )
    figures = {
        "headline_agenda_reach": build_headline_figure(reach_share, years, year_ticktext),
        "programme_coverage_by_year": build_coverage_figure(coverage_plot_data, era_shift),
        "programme_coverage_era_shift": build_era_shift_figure(era_shift),
        "article_ra_affinity_distribution": build_article_ra_figure(
            article_benchmark_rows, article_final_rows, selected_abstract
        ),
    }
    tables = {
        "headline_agenda_reach": reach_share,
        "programme_coverage_by_year": coverage_plot_data,
        "programme_coverage_era_shift": era_shift,
        "article_ra_affinity_distribution": pd.concat(
            [article_benchmark_rows, article_final_rows], ignore_index=True
        ),
    }
    write_outputs(args.output_dir, figures, tables)
    print(f"{len(figures)} figures -> {args.output_dir / 'figures'}")
    print(f"{len(tables)} Excel tables -> {args.output_dir}")


if __name__ == "__main__":
    main()