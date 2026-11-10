#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tikz_export.py
===============
Standalone-compilable pgfplots/TikZ (.tex) versions of the "headline"
corpus-level figures from analyze_citations.py — same underlying data,
rendered as native LaTeX instead of a matplotlib PNG. Generated *alongside*
the PNGs (not a replacement); called from analyze_citations.py's main().

Each write_tikz_* function takes the same in-memory data structures
analyze_citations.py already computes (area_rows, grand totals, ieee_journals
Counter, ieee_by_year dict) and writes a self-contained standalone.tex file
that compiles directly with pdflatex to a tightly-cropped PDF of the figure.

Color palette matches analyze_citations.py's COLOR_OTHER / COLOR_JRNL / COLOR_IEEE.
"""

import os

COLOR_OTHER_HEX = "B4B2A9"
COLOR_JRNL_HEX  = "378ADD"
COLOR_IEEE_HEX  = "042C53"
COLOR_CONF_HEX  = "D98B3F"

IEEE_PES_JOURNALS = {
    "IEEE Transactions on Power Systems",
    "IEEE Transactions on Power Delivery",
    "IEEE Transactions on Energy Conversion",
    "IEEE Transactions on Sustainable Energy",
    "IEEE Transactions on Smart Grid",
    "IEEE Transactions on Power Apparatus And Systems",
}


def _is_pes_journal(name: str) -> bool:
    return name.strip().lower() in {j.lower() for j in IEEE_PES_JOURNALS}


def _esc(text: str) -> str:
    """Escape characters LaTeX treats specially, for use in node/legend text."""
    repl = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    return text


def _sym(text: str) -> str:
    """Sanitize a string for use as a pgfplots symbolic coordinate name
    (no commas/braces/parens; keep it simple and unique). Parentheses are
    stripped, not escaped — a literal "(" inside a coordinate name (e.g.
    "RTE (France)") is parsed by TikZ as opening a nested coordinate group,
    which corrupts the whole `(axis cs:NAME,Y)` expression and can cascade
    into a "nested axis environments" pgfplots error on the *next* axis."""
    return (text.replace(",", "").replace("{", "").replace("}", "")
                .replace("(", "").replace(")", ""))


def _preamble(extra_libs: str = "") -> str:
    return (
        "\\documentclass[tikz,border=2pt]{standalone}\n"
        "\\usepackage{pgfplots}\n"
        "\\pgfplotsset{compat=1.18}\n"
        "\\usetikzlibrary{positioning}\n"
        f"{extra_libs}"
        "\\usepackage{helvet}\n"
        "\\renewcommand{\\familydefault}{\\sfdefault}\n"
        f"\\definecolor{{ieeecolor}}{{HTML}}{{{COLOR_IEEE_HEX}}}\n"
        f"\\definecolor{{jrnlcolor}}{{HTML}}{{{COLOR_JRNL_HEX}}}\n"
        f"\\definecolor{{othercolor}}{{HTML}}{{{COLOR_OTHER_HEX}}}\n"
        f"\\definecolor{{confcolor}}{{HTML}}{{{COLOR_CONF_HEX}}}\n"
    )


def _write(out_path: str, body: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"  [ok] {out_path}")


def _manual_legend(items: list, prefix: str = "leg") -> str:
    """
    items: list of (color_macro, label). Emits a horizontal row of colored
    swatches + labels using TikZ's `positioning` library (right=<gap> of
    <node>) so spacing is computed from each label's real rendered width —
    no character-count width guessing, which reliably clipped/overlapped
    longer labels (e.g. "IEEE Transactions") when estimated by hand.
    """
    parts = []
    prev = None
    for i, (color, label) in enumerate(items):
        box, txt = f"{prefix}box{i}", f"{prefix}txt{i}"
        if prev is None:
            box_opts = "anchor=west"
            box_pos = " at (0,0)"
        else:
            box_opts = f"anchor=west, right=16pt of {prev}"
            box_pos = ""
        parts.append(
            f"\\node[fill={color}, minimum width=10pt, minimum height=8pt, {box_opts}] "
            f"({box}){box_pos} {{}};"
        )
        parts.append(
            f"\\node[anchor=west, font=\\small, right=3pt of {box}] ({txt}) {{{_esc(label)}}};"
        )
        prev = txt
    return " ".join(parts)


# ─── Figure: overall citation shares (100%-stacked single bar) ───────────────

def write_tikz_citation_shares(grand: dict, out_path: str, title: str | None = None):
    values = [grand["ieee_transactions"], grand["other_journals"], grand["other"]]
    labels = ["IEEE PES Transactions", "Other journals", "Other"]
    colors = ["ieeecolor", "jrnlcolor", "othercolor"]
    total = sum(values) or 1

    coords = []
    left = 0
    for v, c, lbl in zip(values, colors, labels):
        pct = 100 * v / total
        coords.append((left, v, c, lbl, pct))
        left += v

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += f"\\begin{{axis}}[\n"
    body += "    width=14cm, height=2.6cm,\n"
    body += "    hide y axis,\n"
    body += f"    xmin=0, xmax={total},\n"
    body += "    ymin=-0.6, ymax=0.9,\n"
    body += "    axis x line=bottom,\n"
    body += "    xlabel={Citations},\n"
    body += "    every axis x label/.style={at={(axis description cs:0.5,-0.15)}, anchor=north},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    for left, v, c, lbl, pct in coords:
        body += f"\\draw[fill={c}, draw=white] (axis cs:{left},-0.25) rectangle (axis cs:{left+v},0.25);\n"
        mid = left + v / 2
        text_color = "white" if c != "othercolor" else "black"
        label_text = f"{v} ({pct:.1f}\\%)"
        if v / total >= 0.05:
            body += (f"\\node[font=\\small\\bfseries, text={text_color}] at "
                     f"(axis cs:{mid},0) {{{label_text}}};\n")
        else:
            body += (f"\\node[font=\\footnotesize, anchor=south] at "
                     f"(axis cs:{mid},0.28) {{{label_text}}};\n")
    body += "\\end{axis}\n"
    # Manual legend above the axis
    body += "\\begin{scope}[shift={(1.5,2.6)}]\n"
    body += _manual_legend([(c, lbl) for _, _, c, lbl, _ in coords])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: journal-only IEEE Transactions share (100%-stacked single bar) ──

def write_tikz_journal_ieee_share(grand: dict, out_path: str, title: str | None = None):
    ieee = grand["ieee_transactions"]
    other_jrnl = grand["other_journals"]
    values = [ieee, other_jrnl]
    labels = ["IEEE PES Transactions", "Other journals"]
    colors = ["ieeecolor", "jrnlcolor"]
    total = sum(values) or 1

    coords = []
    left = 0
    for v, c, lbl in zip(values, colors, labels):
        pct = 100 * v / total
        coords.append((left, v, c, lbl, pct))
        left += v

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    width=14cm, height=2.6cm,\n"
    body += "    hide y axis,\n"
    body += f"    xmin=0, xmax={total},\n"
    body += "    ymin=-0.6, ymax=0.9,\n"
    body += "    axis x line=bottom,\n"
    body += "    xlabel={Journal citations},\n"
    body += "    every axis x label/.style={at={(axis description cs:0.5,-0.15)}, anchor=north},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    for left, v, c, lbl, pct in coords:
        body += f"\\draw[fill={c}, draw=white] (axis cs:{left},-0.25) rectangle (axis cs:{left+v},0.25);\n"
        mid = left + v / 2
        label_text = f"{v} ({pct:.1f}\\%)"
        body += f"\\node[font=\\small\\bfseries, text=white] at (axis cs:{mid},0) {{{label_text}}};\n"
    body += "\\end{axis}\n"
    body += "\\begin{scope}[shift={(2.5,2.6)}]\n"
    body += _manual_legend([(c, lbl) for _, _, c, lbl, _ in coords])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: area summary (6 areas + total, stacked vertical bars) ───────────

def write_tikz_area_summary(area_rows: list, out_path: str, title: str | None = None):
    cats = [(r["label"], r) for r in area_rows]
    max_total = max((r["total"] for r in area_rows), default=1)

    def coord_list(key):
        return " ".join(f"({_sym(lbl)},{r[key]})" for lbl, r in cats)

    sym_coords = ",".join(_sym(lbl) for lbl, _ in cats)

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    ybar stacked, bar width=16pt,\n"
    body += "    width=15cm, height=6.5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    x tick label style={font=\\small},\n"
    body += "    ylabel={Citations},\n"
    body += f"    ymin=0, ymax={max_total * 1.25:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    ymajorgrids, grid style={color=black!10},\n"
    body += "    enlarge x limits=0.08,\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=othercolor] coordinates {{{coord_list('other')}}};\n"
    body += f"\\addplot[fill=jrnlcolor] coordinates {{{coord_list('other_journals')}}};\n"
    body += f"\\addplot[fill=ieeecolor] coordinates {{{coord_list('ieee_transactions')}}};\n"

    # Percentage labels inside segments large enough, + total ieee/total label above
    for lbl, r in cats:
        tot = r["total"] or 1
        cum = 0
        for key, col in (("other", "black"), ("other_journals", "white"), ("ieee_transactions", "white")):
            v = r[key]
            if v and v / tot >= 0.05:
                mid = cum + v / 2
                pct = 100 * v / tot
                body += (f"\\node[font=\\tiny, text={col}] at "
                         f"(axis cs:{_sym(lbl)},{mid}) {{{v} ({pct:.0f}\\%)}};\n")
            cum += v
        body += (f"\\node[font=\\small\\bfseries, anchor=south] at "
                 f"(axis cs:{_sym(lbl)},{r['total']}) {{{r['ieee_transactions']}/{r['total']}}};\n")

    body += "\\end{axis}\n"
    body += "\\begin{scope}[shift={(2.0,7.0)}]\n"
    body += _manual_legend([
        ("ieeecolor", "IEEE PES Transactions"),
        ("jrnlcolor", "Other journals"),
        ("othercolor", "Other"),
    ])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


def write_tikz_area_summary_journals(area_rows: list, out_path: str, title: str | None = None):
    """
    Same layout as write_tikz_area_summary(), restricted to journal-type
    citations only (IEEE Transactions + other journals — the "other" ref-type
    bucket is excluded from both the bars and the percentage denominator).
    Mirrors analyze_citations.plot_area_summary_journals()'s label-collision
    handling: with only two segments instead of three, a short bar can't
    always fit two labels without them overlapping each other or the total
    label above, so short bars drop the smaller segment's inside label and
    thin top (IEEE) slivers use a stricter visibility threshold than the
    bottom segment.
    """
    cats = [(r["label"], r["other_journals"], r["ieee_transactions"]) for r in area_rows]
    totals = [j + i for _, j, i in cats]
    max_total = max(totals, default=1)
    thresh, thresh_top = max_total * 0.05, max_total * 0.09
    min_room = max_total * 0.15

    sym_coords = ",".join(_sym(lbl) for lbl, _, _ in cats)
    jrnl_coord = " ".join(f"({_sym(lbl)},{j})" for lbl, j, _ in cats)
    ieee_coord = " ".join(f"({_sym(lbl)},{i})" for lbl, _, i in cats)

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    ybar stacked, bar width=16pt,\n"
    body += "    width=15cm, height=6.5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    x tick label style={font=\\small},\n"
    body += "    ylabel={Journal citations},\n"
    body += f"    ymin=0, ymax={max_total * 1.3:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    ymajorgrids, grid style={color=black!10},\n"
    body += "    enlarge x limits=0.08,\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=jrnlcolor] coordinates {{{jrnl_coord}}};\n"
    body += f"\\addplot[fill=ieeecolor] coordinates {{{ieee_coord}}};\n"

    for lbl, j, i in cats:
        tot = j + i
        if tot == 0:
            continue
        show_j, show_i = j >= thresh, i >= thresh_top
        # Keep only the larger segment's label when the bar is too short for both.
        if show_j and show_i and tot < min_room:
            if j >= i:
                show_i = False
            else:
                show_j = False
        if show_j:
            pct = 100 * j / tot
            body += (f"\\node[font=\\tiny, text=white] at (axis cs:{_sym(lbl)},{j / 2}) "
                     f"{{{j} ({pct:.0f}\\%)}};\n")
        if show_i:
            pct = 100 * i / tot
            body += (f"\\node[font=\\tiny\\bfseries, text=white] at "
                     f"(axis cs:{_sym(lbl)},{j + i / 2}) {{{i} ({pct:.0f}\\%)}};\n")
        body += (f"\\node[font=\\small\\bfseries, anchor=south] at "
                 f"(axis cs:{_sym(lbl)},{tot}) {{{i}/{tot}}};\n")

    body += "\\end{axis}\n"
    body += "\\begin{scope}[shift={(2.0,7.0)}]\n"
    body += _manual_legend([
        ("ieeecolor", "IEEE PES Transactions"),
        ("jrnlcolor", "Other journals"),
    ])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: IEEE Transactions citations by G-PST area (horizontal bars) ─────

def write_tikz_ieee_by_area(area_rows: list, out_path: str, title: str | None = None):
    rows = [r for r in area_rows if r["area"] != "total"]
    sym_coords = ",".join(_sym(r["label"]) for r in rows)
    coord_list = " ".join(f"({r['ieee_transactions']},{_sym(r['label'])})" for r in rows)
    max_v = max((r["ieee_transactions"] for r in rows), default=1)

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    xbar, bar width=12pt,\n"
    body += "    width=9cm, height=5.5cm,\n"
    body += f"    symbolic y coords={{{sym_coords}}},\n"
    body += "    ytick=data,\n"
    body += "    y dir=reverse,\n"
    body += "    xlabel={Citations},\n"
    body += f"    xmin=0, xmax={max_v * 1.45:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    enlarge y limits=0.15,\n"
    body += "    nodes near coords, every node near coord/.append style={font=\\footnotesize, anchor=west},\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=ieeecolor] coordinates {{{coord_list}}};\n"
    body += "\\end{axis}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: IEEE Transactions journal breakdown (horizontal bars, 2-tone) ──

def write_tikz_ieee_journals(journal_counts, out_path: str, title: str | None = None):
    from collections import Counter
    if isinstance(journal_counts, Counter):
        items = journal_counts.most_common()
    else:
        items = sorted(journal_counts.items(), key=lambda kv: kv[1], reverse=True)
    if not items:
        return
    total = sum(v for _, v in items) or 1
    sym_coords = ",".join(_sym(name) for name, _ in items)
    max_v = max(v for _, v in items)

    pes_coords = " ".join(f"({v if _is_pes_journal(n) else 0},{_sym(n)})" for n, v in items)
    other_coords = " ".join(f"({0 if _is_pes_journal(n) else v},{_sym(n)})" for n, v in items)
    # Every item here is already IEEE_PES_JOURNALS-only (see
    # apply_pes_transactions_filter() in analyze_citations.py) — this stays
    # False in normal operation; it's a defensive fallback only.
    has_non_pes = any(not _is_pes_journal(n) for n, _ in items)

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    xbar, bar width=10pt, bar shift=0pt,\n"
    body += f"    width=15cm, height={max(2.0, len(items) * 0.4 + 1.0):.1f}cm,\n"
    body += f"    symbolic y coords={{{sym_coords}}},\n"
    body += "    ytick=data,\n"
    body += "    y dir=reverse,\n"
    body += "    y tick label style={font=\\footnotesize},\n"
    body += "    xlabel={Citations},\n"
    body += f"    xmin=0, xmax={max_v * 1.4:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    enlarge y limits=0.08,\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=ieeecolor, draw=none] coordinates {{{pes_coords}}};\n"
    if has_non_pes:
        body += f"\\addplot[fill=jrnlcolor, draw=none] coordinates {{{other_coords}}};\n"
    for name, v in items:
        pct = 100 * v / total
        body += (f"\\node[anchor=west, font=\\footnotesize] at (axis cs:{v},{_sym(name)}) "
                 f"{{{v} ({pct:.1f}\\%)}};\n")
    body += "\\end{axis}\n"
    fig_height = max(2.0, len(items) * 0.4 + 1.0)
    body += f"\\begin{{scope}}[shift={{(5,{fig_height + 0.6:.1f})}}]\n"
    legend_entries = [("ieeecolor", "IEEE PES Transactions")]
    if has_non_pes:
        legend_entries.append(("jrnlcolor", "Non-PES (unexpected)"))
    body += _manual_legend(legend_entries)
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: IEEE Transactions citations by decade (vertical bars) ───────────

def write_tikz_ieee_by_year(year_counts: dict, out_path: str, title: str | None = None,
                             journal_counts: dict | None = None):
    """
    IEEE citations by decade bin. When `journal_counts` is given, draws a
    dual-axis chart: distinct journal articles as bars (left axis) and total
    citation volume as a line (right axis) — the same paper cited more than
    once inflates the line above the bars. Without `journal_counts`, falls
    back to the original single bar chart of citation counts only.
    """
    _decade_labels = ["pre-1970", "1970--1979", "1980--1989", "1990--1999",
                      "2000--2009", "2010--2019", "2020--present"]
    labels = list(_decade_labels)
    has_unknown = year_counts.get("Unknown", 0) > 0 or (journal_counts and journal_counts.get("Unknown", 0) > 0)
    if has_unknown:
        labels.append("Unknown")
    # year_counts (from analyze_citations.py) uses en-dash decade labels;
    # our TeX-safe labels use "--" instead, so map back to look up counts.
    key_map = {
        "pre-1970": "pre-1970", "1970--1979": "1970\u20131979", "1980--1989": "1980\u20131989",
        "1990--1999": "1990\u20131999", "2000--2009": "2000\u20132009",
        "2010--2019": "2010\u20132019", "2020--present": "2020\u2013present", "Unknown": "Unknown",
    }
    values = [year_counts.get(key_map.get(lbl, lbl), 0) for lbl in labels]
    total = sum(values) or 1
    sym_coords = ",".join(_sym(l) for l in labels)
    coord_list = " ".join(f"({_sym(l)},{v})" for l, v in zip(labels, values))
    max_v = max(values, default=1)

    if journal_counts is None:
        body = _preamble()
        body += "\\begin{document}\n\\begin{tikzpicture}\n"
        body += "\\begin{axis}[\n"
        body += "    ybar, bar width=12pt,\n"
        body += "    width=9cm, height=5cm,\n"
        body += f"    symbolic x coords={{{sym_coords}}},\n"
        body += "    xtick=data,\n"
        body += "    x tick label style={rotate=30, anchor=east, font=\\footnotesize},\n"
        body += "    ylabel={Citations},\n"
        body += f"    ymin=0, ymax={max_v * 1.35:.0f},\n"
        body += "    axis line style={color=black!40},\n"
        body += "    tick style={color=black!40},\n"
        body += "    enlarge x limits=0.1,\n"
        if title:
            body += f"    title={{{_esc(title)}}},\n"
        body += "]\n"
        body += f"\\addplot[fill=ieeecolor] coordinates {{{coord_list}}};\n"
        for lbl, v in zip(labels, values):
            if v == 0:
                continue
            pct = 100 * v / total
            body += (f"\\node[font=\\tiny, anchor=south] at (axis cs:{_sym(lbl)},{v}) "
                     f"{{{v} ({pct:.1f}\\%)}};\n")
        body += "\\end{axis}\n"
        body += "\\end{tikzpicture}\n\\end{document}\n"
        _write(out_path, body)
        return

    jvalues = [journal_counts.get(key_map.get(lbl, lbl), 0) for lbl in labels]
    max_jv = max(jvalues, default=1)
    coord_list_j = " ".join(f"({_sym(l)},{v})" for l, v in zip(labels, jvalues))

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    ybar, bar width=12pt,\n"
    body += "    width=9cm, height=5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    x tick label style={rotate=30, anchor=east, font=\\footnotesize},\n"
    body += "    axis y line*=left,\n"
    body += "    ylabel={Journal articles},\n"
    body += "    ylabel style={color=jrnlcolor},\n"
    body += "    y tick style={color=jrnlcolor},\n"
    body += "    y tick label style={color=jrnlcolor},\n"
    body += f"    ymin=0, ymax={max_jv * 1.35:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    enlarge x limits=0.1,\n"
    body += "    legend style={font=\\tiny, draw=none, at={(0,1)}, anchor=north west},\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=jrnlcolor] coordinates {{{coord_list_j}}};\n"
    body += "\\addlegendentry{Journal articles}\n"
    for lbl, v in zip(labels, jvalues):
        if v == 0:
            continue
        body += (f"\\node[font=\\tiny, anchor=south, text=black] at (axis cs:{_sym(lbl)},{v}) "
                 f"{{{v}}};\n")
    body += "\\end{axis}\n"
    body += "\\begin{axis}[\n"
    body += "    width=9cm, height=5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    hide x axis,\n"
    body += "    axis y line*=right,\n"
    body += "    ylabel={Citations},\n"
    body += "    ylabel style={color=ieeecolor},\n"
    body += "    y tick style={color=ieeecolor},\n"
    body += "    y tick label style={color=ieeecolor},\n"
    body += f"    ymin=0, ymax={max_v * 1.35:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    enlarge x limits=0.1,\n"
    body += "    legend style={font=\\tiny, draw=none, at={(0,0.85)}, anchor=north west},\n"
    body += "]\n"
    body += f"\\addplot[color=ieeecolor, mark=*, mark size=1.5pt, thick] coordinates {{{coord_list}}};\n"
    body += "\\addlegendentry{Citations}\n"
    # Fixed point yshift (not a data-space offset, which would coincide
    # exactly with the bar label when bar value == line value) keeps the two
    # labels from overlapping when a bin's journal-article and citation
    # counts are equal.
    for lbl, v in zip(labels, values):
        body += (f"\\node[font=\\tiny, anchor=south, text=ieeecolor, yshift=6pt] at (axis cs:{_sym(lbl)},{v}) "
                 f"{{{v}}};\n")
    body += "\\end{axis}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


def write_tikz_docs_by_year(year_counts: dict, out_path: str, title: str | None = None):
    """Vertical bar chart of citing DOCUMENTS (not citations) by decade bin —
    the documents-themselves counterpart to write_tikz_ieee_by_year()."""
    _decade_labels = ["pre-1970", "1970--1979", "1980--1989", "1990--1999",
                      "2000--2009", "2010--2019", "2020--present"]
    labels = list(_decade_labels)
    if year_counts.get("Unknown", 0) > 0:
        labels.append("Unknown")
    key_map = {
        "pre-1970": "pre-1970", "1970--1979": "1970\u20131979", "1980--1989": "1980\u20131989",
        "1990--1999": "1990\u20131999", "2000--2009": "2000\u20132009",
        "2010--2019": "2010\u20132019", "2020--present": "2020\u2013present", "Unknown": "Unknown",
    }
    values = [year_counts.get(key_map.get(lbl, lbl), 0) for lbl in labels]
    total = sum(values) or 1
    sym_coords = ",".join(_sym(l) for l in labels)
    coord_list = " ".join(f"({_sym(l)},{v})" for l, v in zip(labels, values))
    max_v = max(values, default=1)

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    ybar, bar width=12pt,\n"
    body += "    width=9cm, height=5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    x tick label style={rotate=30, anchor=east, font=\\footnotesize},\n"
    body += "    ylabel={Documents},\n"
    body += f"    ymin=0, ymax={max_v * 1.35:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    enlarge x limits=0.1,\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=jrnlcolor] coordinates {{{coord_list}}};\n"
    for lbl, v in zip(labels, values):
        if v == 0:
            continue
        pct = 100 * v / total
        body += (f"\\node[font=\\tiny, anchor=south] at (axis cs:{_sym(lbl)},{v}) "
                 f"{{{v} ({pct:.1f}\\%)}};\n")
    body += "\\end{axis}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


def write_tikz_ieee_by_year_split(year_counts_split: dict, out_path: str,
                                  split_entity: str = "ESIG", title: str | None = None):
    """
    Vertical stacked bar chart of IEEE Transactions citations by decade bin,
    split_entity's citations (top segment, ieeecolor) stacked on top of
    everyone else's (bottom segment, jrnlcolor). `year_counts_split` is
    {label: {"split": n, "other": n}} — see
    analyze_citations.load_ieee_years_split_from_review_csv().
    """
    _decade_labels = ["pre-1970", "1970--1979", "1980--1989", "1990--1999",
                      "2000--2009", "2010--2019", "2020--present"]
    labels = list(_decade_labels)
    key_map = {
        "pre-1970": "pre-1970", "1970--1979": "1970\u20131979", "1980--1989": "1980\u20131989",
        "1990--1999": "1990\u20131999", "2000--2009": "2000\u20132009",
        "2010--2019": "2010\u20132019", "2020--present": "2020\u2013present", "Unknown": "Unknown",
    }
    unknown = year_counts_split.get("Unknown", {})
    if unknown.get("split", 0) or unknown.get("other", 0):
        labels.append("Unknown")

    other_vals = [year_counts_split.get(key_map.get(lbl, lbl), {}).get("other", 0) for lbl in labels]
    split_vals = [year_counts_split.get(key_map.get(lbl, lbl), {}).get("split", 0) for lbl in labels]
    totals     = [o + s for o, s in zip(other_vals, split_vals)]
    total_all  = sum(totals) or 1
    max_tot    = max(totals, default=1)

    sym_coords  = ",".join(_sym(l) for l in labels)
    other_coord = " ".join(f"({_sym(l)},{v})" for l, v in zip(labels, other_vals))
    split_coord = " ".join(f"({_sym(l)},{v})" for l, v in zip(labels, split_vals))

    body = _preamble()
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{axis}[\n"
    body += "    ybar stacked, bar width=12pt,\n"
    body += "    width=9cm, height=5.5cm,\n"
    body += f"    symbolic x coords={{{sym_coords}}},\n"
    body += "    xtick=data,\n"
    body += "    x tick label style={rotate=30, anchor=east, font=\\footnotesize},\n"
    body += "    ylabel={Citations},\n"
    body += f"    ymin=0, ymax={max_tot * 1.35:.0f},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    enlarge x limits=0.1,\n"
    if title:
        body += f"    title={{{_esc(title)}}},\n"
    body += "]\n"
    body += f"\\addplot[fill=jrnlcolor] coordinates {{{other_coord}}};\n"
    body += f"\\addplot[fill=ieeecolor] coordinates {{{split_coord}}};\n"
    for lbl, tot in zip(labels, totals):
        if tot == 0:
            continue
        pct = 100 * tot / total_all
        body += (f"\\node[font=\\tiny, anchor=south] at (axis cs:{_sym(lbl)},{tot}) "
                 f"{{{tot} ({pct:.1f}\\%)}};\n")
    body += "\\end{axis}\n"
    body += "\\begin{scope}[shift={(1.2,5.9)}]\n"
    body += _manual_legend([
        ("ieeecolor", split_entity),
        ("jrnlcolor", f"Other than {split_entity}"),
    ])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)


# ─── Figure: all-entities overview (2x3 groupplot, one panel per area) ──────

def write_tikz_all_entities(stats: dict, area_keys: list, area_titles: dict, out_path: str,
                            title: str | None = None):
    entities = sorted(stats["by_entity"].keys())

    body = _preamble("\\usepgfplotslibrary{groupplots}\n")
    # 'ybar stacked, bar width=...' must be set via a global style, not inline
    # in \begin{groupplot}[...] — pgfplots raises "unknown key /pgfplots/bar
    # width" if the bar-plot keys are passed directly as groupplot options.
    body += "\\pgfplotsset{every axis/.append style={ybar stacked, bar width=8pt}}\n"
    body += "\\begin{document}\n\\begin{tikzpicture}\n"
    body += "\\begin{groupplot}[\n"
    body += "    group style={group size=3 by 2, horizontal sep=1.6cm, vertical sep=1.8cm},\n"
    body += "    width=5.3cm, height=4cm,\n"
    body += "    ylabel={Citations},\n"
    body += "    x tick label style={font=\\tiny, rotate=45, anchor=east},\n"
    body += "    y tick label style={font=\\tiny},\n"
    body += "    label style={font=\\small},\n"
    body += "    title style={font=\\small\\bfseries},\n"
    body += "    axis line style={color=black!40},\n"
    body += "    tick style={color=black!40},\n"
    body += "    enlarge x limits=0.15,\n"
    body += "]\n"

    for area_key in area_keys:
        panel_data = []
        for ent in entities:
            area = stats["by_entity"][ent]["by_gpst_area"].get(area_key, {})
            tot = area.get("total", 0)
            if tot == 0:
                continue
            panel_data.append((ent, area.get("other", 0), area.get("other_journals", 0),
                               area.get("ieee_transactions", 0)))
        panel_data.sort(key=lambda d: d[1] + d[2] + d[3], reverse=True)

        sym_coords = ",".join(_sym(d[0]) for d in panel_data) if panel_data else "none"
        max_tot = max((d[1] + d[2] + d[3] for d in panel_data), default=1)

        body += (f"\\nextgroupplot[title={{{_esc(area_titles[area_key])}}}, "
                 f"symbolic x coords={{{sym_coords}}}, xtick=data, "
                 f"ymin=0, ymax={max_tot * 1.3:.0f}]\n")
        if panel_data:
            other_c = " ".join(f"({_sym(d[0])},{d[1]})" for d in panel_data)
            jrnl_c  = " ".join(f"({_sym(d[0])},{d[2]})" for d in panel_data)
            ieee_c  = " ".join(f"({_sym(d[0])},{d[3]})" for d in panel_data)
            body += f"\\addplot[fill=othercolor] coordinates {{{other_c}}};\n"
            body += f"\\addplot[fill=jrnlcolor] coordinates {{{jrnl_c}}};\n"
            body += f"\\addplot[fill=ieeecolor] coordinates {{{ieee_c}}};\n"
            for d in panel_data:
                tot = d[1] + d[2] + d[3]
                body += (f"\\node[font=\\tiny\\bfseries, anchor=south] at "
                         f"(axis cs:{_sym(d[0])},{tot}) {{{d[3]}/{tot}}};\n")
        else:
            body += "\\node at (axis description cs:0.5,0.5) {\\footnotesize No documents};\n"

    body += "\\end{groupplot}\n"
    body += "\\begin{scope}[shift={(4,5.3)}]\n"
    body += _manual_legend([
        ("ieeecolor", "IEEE PES Transactions"),
        ("jrnlcolor", "Other journals"),
        ("othercolor", "Other"),
    ])
    body += "\n\\end{scope}\n"
    body += "\\end{tikzpicture}\n\\end{document}\n"
    _write(out_path, body)
