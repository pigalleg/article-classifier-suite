"""
analyze_citations.py
====================
Script 2 of 2 — Read the (user-edited) document_index.csv and all_citations.csv
produced by fetch_documents.py, then generate:

  data/results/stats/
      summary.json              — overall counts
      entity_<name>.json        — per-entity breakdown
  data/results/figures/
      gpst_<area>_<entity>.png  — vertical stacked-bar per G-PST area per entity
      gpst_all_entities.png     — 6-panel overview across all entities

Bar label convention:  ieee_transactions / total_citations

Usage (from any directory; paths come from src/paths.py):
    python src/analyze_citations.py
    python src/analyze_citations.py --figures-only       # skip stats recomputation
    python src/analyze_citations.py --no-figures         # stats only

NOTE: several files in data/results/stats/ are hand-curated and are read back
on every run (ieee_citations_review.csv, unresolved_other_journals*.csv,
journal_citations_by_document_*.csv, *_documents_with_citations.csv) — do not
delete that folder before re-running.
"""

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker

import tikz_export

# ─── Paths ────────────────────────────────────────────────────────────────────
import paths

STATS_DIR    = str(paths.STATS_DIR)
FIGURES_DIR  = str(paths.FIGURES_DIR)
ALL_CIT_CSV  = str(paths.ALL_CITATIONS_CSV)
JOURNAL_PUBLISHERS_CSV = str(paths.JOURNAL_PUBLISHERS_CSV)
CITATION_CORRECTIONS_CSV = str(paths.CITATION_CORRECTIONS_CSV)
DOC_IDX_CSV = str(paths.DOCS_WITH_CITATIONS_CSV)

# Entities that are NOT grid/system operators (research consortia, industry
# associations, vendors) — used to build the "system operators only" scope
# by exclusion, so any current or future TSO/ISO/RTO entity is automatically
# included without having to be named explicitly.
NON_SYSTEM_OPERATOR_ENTITIES = {
    "ESIG", "G-PST", "GE Vernova", "Siemens Energy", "ABB",
    "Hitachi Energy", "Prysmian", "NERC",
}
# ─── G-PST area metadata ──────────────────────────────────────────────────────
GPST_AREAS = [
    ("ibrs",     "IBRs",    "Q1–6: GFM/GFL controls, protection, frequency standards"),
    ("stability","Stability",       "Q7–11: SSO, oscillations, stability tools"),
    ("ders",     "DER", "Q12–18: DER aggregation, valuation, grid planning"),
    ("crof",     "CROF",   "Q19–27: Grid-enhancing tech, topology, operations"),
    ("services", "Services",      "Q28–36: Ancillary services, market design"),
    ("planning", "Planning",              "Q37–48: Resource adequacy, transmission, load forecasting"),
]
AREA_KEYS   = [a[0] for a in GPST_AREAS]
AREA_TITLES = {a[0]: a[1] for a in GPST_AREAS}
AREA_SUBS   = {a[0]: a[2] for a in GPST_AREAS}

# ─── IEEE figure dimensions & style ──────────────────────────────────────────
IEEE_COL1 = 3.5    # single-column width (inches) per IEEE author guide
IEEE_COL2 = 7.16   # double-column width (inches)
IEEE_DPI  = 900    # print-quality raster DPI

# Standard IEEE Transactions abbreviations (IEEE style guide)
IEEE_TRANS_ABBREV: dict[str, str] = {
    "IEEE Transactions on Power Apparatus And Systems":  "IEEE Trans. Power App. Syst.",
    "IEEE Transactions on Power Apparatus and Systems":  "IEEE Trans. Power App. Syst.",
    "IEEE Transactions on Power Delivery":               "IEEE Trans. Power Del.",
    "IEEE Transactions on Power Systems":                "IEEE Trans. Power Syst.",
    "IEEE Transactions on Energy Conversion":            "IEEE Trans. Energy Convers.",
    "IEEE Transactions on Sustainable Energy":           "IEEE Trans. Sustain. Energy",
    "IEEE Transactions on Power Electronics":            "IEEE Trans. Power Electron.",
    "IEEE Transactions on Industry Applications":        "IEEE Trans. Ind. Appl.",
    "IEEE Transactions on Smart Grid":                   "IEEE Trans. Smart Grid",
    "IEEE Transactions on Industrial Electronics":       "IEEE Trans. Ind. Electron.",
    "IEEE Transactions on Automatic Control":            "IEEE Trans. Autom. Control",
    "IEEE Transactions on Control Systems Technology":   "IEEE Trans. Control Syst. Technol.",
    "IEEE Transactions on Signal Processing":            "IEEE Trans. Signal Process.",
    "IEEE Transactions on Neural Networks":              "IEEE Trans. Neural Netw.",
}


def _set_ieee_style() -> None:
    """Apply IEEE journal rcParams globally (call once before any plotting)."""
    plt.rcParams.update({
        "font.family":        "sans-serif",
        "font.size":          8,
        "axes.titlesize":     9,
        "axes.titleweight":   "bold",
        "axes.labelsize":     8,
        "axes.labelcolor":    "#2C2C2A",
        "axes.linewidth":     0.5,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "xtick.labelsize":    7,
        "ytick.labelsize":    7,
        "xtick.major.width":  0.5,
        "ytick.major.width":  0.5,
        "xtick.direction":    "in",
        "ytick.direction":    "in",
        "legend.fontsize":    7,
        "legend.frameon":     False,
        "legend.borderpad":   0.3,
        "grid.linewidth":     0.4,
        "grid.color":         "#E0E0E0",
        "patch.linewidth":    0.4,
        "figure.facecolor":   "white",
        "axes.facecolor":     "white",
    })


# ─── Plot colors ──────────────────────────────────────────────────────────────
COLOR_OTHER = "#B4B2A9"   # gray-200
COLOR_JRNL  = "#378ADD"   # blue-400  (other journals)
COLOR_IEEE  = "#042C53"   # blue-900  (IEEE Transactions)
COLOR_CONF  = "#D98B3F"   # amber-500 (conferences)

# Matches journal name after "IEEE Transactions on " / "IEEE Trans. on ".
# Character class excludes digits so volume numbers (e.g. "36") are never captured.
# Lookahead also stops at any bare digit (catches "Power Systems 36, ..." patterns)
# or at legacy ABBR-digit volume notation (e.g. "PAS-101" for Power Apparatus and Systems).
IEEE_JOURNAL_RE = re.compile(
    r'IEEE\s+Tran(?:s(?:actions)?)?\.?\s+on\s+'
    r'([A-Za-z][A-Za-z &\-\/\.]*?)'
    r'(?=\s*[,\.(;]|\s+[Vv]ol\.?|\s+[Pp]p\.|\s+[Nn]o\.|\s+\d|\s+[A-Za-z]{2,}-\d|\s*$)',
    re.I,
)

# Extracts the first URL from a citation string (prefers doi.org links)
_DOI_URL_RE = re.compile(r'https?://(?:dx\.)?doi\.org/[^\s,;)\]>\"\']+', re.I)
_URL_RE      = re.compile(r'https?://[^\s,;)\]>\"\']+', re.I)

# ─── Load data ────────────────────────────────────────────────────────────────

def load_index(entity_filter: list | None = None) -> dict:
    """
    Returns {pdf_filename: row_dict} for rows where include == 'yes'.
    Applies entity_filter if provided.
    """
    index = {}
    if not os.path.exists(DOC_IDX_CSV):
        raise FileNotFoundError(f"{DOC_IDX_CSV} not found. Run fetch_documents.py first.")
    with open(DOC_IDX_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("include", "yes").strip().lower() == "no":
                continue
            if entity_filter and row["entity_name"] not in entity_filter:
                continue
            index[row["pdf_filename"]] = row
    return index

def load_citations(index: dict) -> list:
    """
    Return citation rows for documents present in index — exactly one row
    per (citation, source document) pair, regardless of how many G-PST areas
    that document is validated under. A document relevant to multiple areas
    (gpst_area_validation has a comma-separated list) is common — ESIG
    reports especially — but every one of its citations must still be
    counted once, not once per area; that would silently inflate every
    downstream count (total citations, entity totals, journal counts, IEEE
    Transactions counts — all of it) by however many areas the document
    spans. Each citation is attributed to the first-listed validated area
    for area-breakdown purposes.
    """
    if not os.path.exists(ALL_CIT_CSV):
        raise FileNotFoundError(f"{ALL_CIT_CSV} not found. Run fetch_documents.py first.")
    rows = []
    with open(ALL_CIT_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["pdf_filename"] not in index:
                continue
            # Enrich with index metadata (user-edited gpst_area overrides)
            idx = index[row["pdf_filename"]]
            # gpst_area_validation is filled in by hand during review; fall
            # back to the auto-guessed gpst_area for not-yet-reviewed rows
            validated = (idx.get("gpst_area_validation") or "").strip() or idx.get("gpst_area", "")
            first_area = validated.split(",")[0].strip()
            row["gpst_area"]     = first_area.lower()     # use user-corrected value
            row["entity_name"]   = idx["entity_name"]
            row["entity_region"] = idx["entity_region"]
            rows.append(row)
    return rows


# ─── Statistics ───────────────────────────────────────────────────────────────

def _zero_area_dict():
    return {area: {"total": 0, "ieee_transactions": 0, "other_journals": 0,
                   "conferences": 0, "other": 0, "docs": set()}
            for area in AREA_KEYS}


def compute_stats(index: dict, citations: list) -> dict:
    """
    Returns:
      {
        "overall": {...},
        "by_entity": {
            entity_name: {
                "region": ...,
                "total_docs": int,
                "total_citations": int,
                "ref_type_counts": {...},
                "ieee_transactions": int,
                "by_gpst_area": {
                    area_key: {
                        "total": int, "ieee_transactions": int,
                        "other_journals": int, "other": int,
                        "docs": [...],
                        "by_doc": {
                            pdf_filename: {
                                "doc_title": str, "doc_year": str,
                                "total": int, "ieee_transactions": int,
                                "other_journals": int, "other": int,
                            }
                        }
                    }
                }
            }
        }
      }
    """
    stats: dict = {"by_entity": {}}

    # — Per-entity aggregation ─────────────────────────────────────────────────
    for row in citations:
        ent    = row["entity_name"]
        area   = row.get("gpst_area", "planning") if row.get("gpst_area", "") in AREA_KEYS else "planning"
        fn     = row["pdf_filename"]
        is_ieee = row["is_ieee_transactions"] == "Yes"
        is_jrnl = row["ref_type"] == "journal" and not is_ieee
        is_conf = row["ref_type"] == "conference" and not is_ieee

        if ent not in stats["by_entity"]:
            stats["by_entity"][ent] = {
                "region":          index[fn]["entity_region"] if fn in index else "",
                "total_docs":      0,
                "total_citations": 0,
                "ref_type_counts": Counter(),
                "ieee_transactions": 0,
                "by_gpst_area":    _zero_area_dict(),
                "_docs_with_citations": set(),
            }

        e = stats["by_entity"][ent]
        e["total_citations"] += 1
        e["ref_type_counts"][row["ref_type"]] += 1
        e["_docs_with_citations"].add(fn)
        if is_ieee:
            e["ieee_transactions"] += 1

        a = e["by_gpst_area"][area]
        a["total"]  += 1
        a["docs"].add(fn)
        if is_ieee:
            a["ieee_transactions"] += 1
        elif is_jrnl:
            a["other_journals"] += 1
        elif is_conf:
            a["conferences"] += 1
        else:
            a["other"] += 1

        # Per-doc within area
        if "by_doc" not in a:
            a["by_doc"] = {}
        if fn not in a["by_doc"]:
            idx_row = index.get(fn, {})
            a["by_doc"][fn] = {
                "doc_title":       idx_row.get("doc_title", fn),
                "doc_year":        idx_row.get("doc_year", ""),
                "total":           0,
                "ieee_transactions": 0,
                "other_journals":  0,
                "conferences":     0,
                "other":           0,
            }
        d = a["by_doc"][fn]
        d["total"] += 1
        if is_ieee:
            d["ieee_transactions"] += 1
        elif is_jrnl:
            d["other_journals"] += 1
        elif is_conf:
            d["conferences"] += 1
        else:
            d["other"] += 1

    # total_docs = documents that actually contributed >=1 citation, NOT
    # every curated (include=yes) document for the entity — a curated
    # document that yielded zero citations (e.g. reviewed and kept as a
    # candidate, but the extractor found nothing) does not count here.
    for e in stats["by_entity"].values():
        e["total_docs"] = len(e.pop("_docs_with_citations"))

    # Convert sets → sorted lists for JSON serialisation
    for ent_data in stats["by_entity"].values():
        ent_data["ref_type_counts"] = dict(ent_data["ref_type_counts"])
        for area_data in ent_data["by_gpst_area"].values():
            area_data["docs"] = sorted(area_data["docs"])

    # — Overall summary ─────────────────────────────────────────────────────────
    stats["overall"] = {
        "total_entities":   len(stats["by_entity"]),
        "total_docs":       len(index),
        "total_citations":  len(citations),
        "ieee_transactions": sum(
            e["ieee_transactions"] for e in stats["by_entity"].values()
        ),
        "by_region": dict(Counter(
            index[fn]["entity_region"]
            for fn in index
        )),
        "by_gpst_area": {
            area: sum(
                e["by_gpst_area"][area]["total"]
                for e in stats["by_entity"].values()
            )
            for area in AREA_KEYS
        },
    }

    return stats


# ─── Write JSON ───────────────────────────────────────────────────────────────

def write_stats(stats: dict):
    if not os.path.exists(STATS_DIR):
        os.makedirs(STATS_DIR)

    # Overall summary
    summary_path = os.path.join(STATS_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(stats["overall"], f, indent=2)
    print(f"  [ok] {summary_path}")

    # Per-entity
    for ent_name, ent_data in stats["by_entity"].items():
        slug = re.sub(r"[^a-z0-9]+", "_", ent_name.lower()).strip("_")
        path = os.path.join(STATS_DIR, f"entity_{slug}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entity": ent_name, **ent_data}, f, indent=2)
        print(f"  [ok] {path}")


# ─── LaTeX table ─────────────────────────────────────────────────────────────

def write_latex_table(stats: dict, out_path: str, label: str = "tab:entity_summary",
                      caption_suffix: str = "", all_entities: list | None = None,
                      index: dict | None = None, csv_path: str | None = None):
    """
    Write a LaTeX longtable: one row per entity, columns for docs per G-PST
    area, total docs, total citations, journal publications (IEEE Transactions
    + other journals), and IEEE Transactions citations.

    By default, entities come from stats["by_entity"] — which only contains
    entities that have at least one citation, since compute_stats() only
    populates an entity when it sees a citation row. Pass `all_entities`
    (e.g. every entity_name in the curated document index) to guarantee every
    entity gets a row even if it has zero citations entirely; `index` is then
    required to correctly count that entity's documents (stats won't have
    that number either, for the same reason).
    """
    entities = sorted(all_entities) if all_entities is not None else sorted(stats["by_entity"].keys())
    area_labels = [AREA_TITLES[k] for k in AREA_KEYS]  # short labels

    col_spec = "l" + "r" * len(AREA_KEYS) + "rrrr"
    def rot(text):
        return r"\rotatebox{45}{\strut " + text + r"}"

    header_areas = " & ".join(rot(lbl) for lbl in area_labels)
    header = (
        f"Entity & {header_areas} & "
        + rot("Docs") + " & "
        + rot("Citations") + " & "
        + rot("Journal Pubs") + " & "
        + rot("IEEE PES Trans.") + r" \\"
    )

    rows = []
    csv_rows = []
    totals = {k: 0 for k in AREA_KEYS}
    total_docs = total_cites = total_jrnl = total_ieee = 0

    for ent in entities:
        e = stats["by_entity"].get(ent)
        if e is None:
            # Entity has zero citations at all — not present in stats["by_entity"].
            # Doc count must come from the curated index directly.
            doc_count = sum(1 for r in index.values() if r["entity_name"] == ent) if index else 0
            area_doc_counts = [0] * len(AREA_KEYS)
            docs_e, cites_e, jrnl_e, ieee_e = doc_count, 0, 0, 0
        else:
            area_doc_counts = [len(e["by_gpst_area"][k]["docs"]) for k in AREA_KEYS]
            docs_e  = e["total_docs"]
            cites_e = e["total_citations"]
            jrnl_e  = e["ref_type_counts"].get("journal", 0)
            ieee_e  = e["ieee_transactions"]

        for k, cnt in zip(AREA_KEYS, area_doc_counts):
            totals[k] += cnt
        total_docs  += docs_e
        total_cites += cites_e
        total_jrnl  += jrnl_e
        total_ieee  += ieee_e

        cells = " & ".join(str(c) if c else "--" for c in area_doc_counts)
        rows.append(f"    {ent} & {cells} & {docs_e} & {cites_e} & {jrnl_e} & {ieee_e} \\\\")
        csv_rows.append({
            "entity": ent,
            **{k: c for k, c in zip(AREA_KEYS, area_doc_counts)},
            "docs": docs_e, "citations": cites_e,
            "journal_publications": jrnl_e, "ieee_transactions": ieee_e,
        })

    total_cells = " & ".join(str(totals[k]) if totals[k] else "--" for k in AREA_KEYS)
    total_row = (
        f"    \\textbf{{Total}} & {total_cells} & "
        f"\\textbf{{{total_docs}}} & \\textbf{{{total_cites}}} & "
        f"\\textbf{{{total_jrnl}}} & \\textbf{{{total_ieee}}} \\\\"
    )

    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{" + col_spec + "}",
        r"\toprule",
        f"    {header}",
        r"\midrule",
        *rows,
        r"\midrule",
        total_row,
        r"\bottomrule",
        r"\end{tabular}",
        r"\caption{Number of documents per G-PST research area, total citations, "
        r"journal publications (IEEE PES Transactions + other journals), and "
        r"IEEE PES Transactions citations by entity" + caption_suffix + r". "
        r"An entity's document may cover multiple areas.}",
        rf"\label{{{label}}}",
        r"\end{table}",
    ]

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {out_path}")

    if csv_path:
        fields = ["entity", *AREA_KEYS, "docs", "citations", "journal_publications", "ieee_transactions"]
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(csv_rows)
        print(f"  [ok] {csv_path}")


# ─── Area summary ─────────────────────────────────────────────────────────────

def compute_area_summary(stats: dict) -> list:
    """
    Returns 7 dicts (6 G-PST areas + 1 total row), each with:
      area, label, total, ieee_transactions, other_journals, conferences, other
    """
    rows = []
    grand = {
        "area": "total", "label": "Total",
        "total": 0, "ieee_transactions": 0, "other_journals": 0,
        "conferences": 0, "other": 0,
    }
    for area_key, area_label, _ in GPST_AREAS:
        ieee = sum(e["by_gpst_area"][area_key]["ieee_transactions"]
                   for e in stats["by_entity"].values())
        jrnl = sum(e["by_gpst_area"][area_key]["other_journals"]
                   for e in stats["by_entity"].values())
        conf = sum(e["by_gpst_area"][area_key]["conferences"]
                   for e in stats["by_entity"].values())
        othr = sum(e["by_gpst_area"][area_key]["other"]
                   for e in stats["by_entity"].values())
        row = {
            "area":              area_key,
            "label":             area_label,
            "total":             ieee + jrnl + conf + othr,
            "ieee_transactions": ieee,
            "other_journals":    jrnl,
            "conferences":       conf,
            "other":             othr,
        }
        rows.append(row)
        for k in ("total", "ieee_transactions", "other_journals", "conferences", "other"):
            grand[k] += row[k]
    rows.append(grand)
    return rows


def write_area_summary_csv(rows: list, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = [
        "area", "label", "total",
        "ieee_transactions", "ieee_pct",
        "other_journals", "other_journals_pct",
        "conferences", "conferences_pct",
        "other", "other_pct",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            tot = row["total"] or 1
            w.writerow({
                "area":               row["area"],
                "label":              row["label"],
                "total":              row["total"],
                "ieee_transactions":  row["ieee_transactions"],
                "ieee_pct":           round(100 * row["ieee_transactions"] / tot, 1),
                "other_journals":     row["other_journals"],
                "other_journals_pct": round(100 * row["other_journals"] / tot, 1),
                "conferences":        row["conferences"],
                "conferences_pct":    round(100 * row["conferences"] / tot, 1),
                "other":              row["other"],
                "other_pct":          round(100 * row["other"] / tot, 1),
            })
    print(f"  [ok] {path}")


# ─── Comparison table: with vs. without a given entity ────────────────────────

def write_entity_exclusion_comparison(stats_all: dict, stats_excl: dict,
                                      area_rows_all: list, area_rows_excl: list,
                                      excluded_name: str, csv_path: str, tex_path: str):
    """
    Write a side-by-side comparison table (CSV + LaTeX): total docs/citations/
    IEEE PES Transactions, plus per-G-PST-area citation and IEEE PES
    Transactions counts, computed with the excluded entity included vs.
    excluded.
    """
    grand_all = area_rows_all[-1]
    grand_excl = area_rows_excl[-1]

    docs_all  = sum(e["total_docs"] for e in stats_all["by_entity"].values())
    docs_excl = sum(e["total_docs"] for e in stats_excl["by_entity"].values())

    def pct(n, d):
        return round(100 * n / d, 1) if d else 0.0

    rows = [
        {"metric": "Total documents",
         "with": docs_all, "without": docs_excl, "excluded_only": docs_all - docs_excl},
        {"metric": "Total citations",
         "with": grand_all["total"], "without": grand_excl["total"],
         "excluded_only": grand_all["total"] - grand_excl["total"]},
        {"metric": "IEEE PES Transactions citations",
         "with": grand_all["ieee_transactions"], "without": grand_excl["ieee_transactions"],
         "excluded_only": grand_all["ieee_transactions"] - grand_excl["ieee_transactions"]},
        {"metric": "IEEE PES Transactions % of citations",
         "with": pct(grand_all["ieee_transactions"], grand_all["total"]),
         "without": pct(grand_excl["ieee_transactions"], grand_excl["total"]),
         "excluded_only": ""},
    ]
    for area_key, area_label, _ in GPST_AREAS:
        row_all  = next(r for r in area_rows_all  if r["area"] == area_key)
        row_excl = next(r for r in area_rows_excl if r["area"] == area_key)
        rows.append({
            "metric": f"{area_label}: citations",
            "with": row_all["total"], "without": row_excl["total"],
            "excluded_only": row_all["total"] - row_excl["total"],
        })
        rows.append({
            "metric": f"{area_label}: IEEE PES Transactions",
            "with": row_all["ieee_transactions"], "without": row_excl["ieee_transactions"],
            "excluded_only": row_all["ieee_transactions"] - row_excl["ieee_transactions"],
        })

    # CSV
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "with", "without", "excluded_only"])
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {csv_path}")

    # LaTeX
    lines = [
        r"\begin{table}[htbp]", r"\centering", r"\small",
        r"\begin{tabular}{lrrr}", r"\toprule",
        f"    Metric & With {excluded_name} & Without {excluded_name} & {excluded_name} only \\\\",
        r"\midrule",
    ]
    for i, r in enumerate(rows):
        if i == 4:  # visual break before per-area rows start
            lines.append(r"\midrule")
        # "%" starts a LaTeX comment if left unescaped — "IEEE Transactions %
        # of citations" was silently truncating that row (and corrupting the
        # tabular's row state enough to break the next \midrule) until this fix.
        metric = r['metric'].replace('%', r'\%')
        lines.append(f"    {metric} & {r['with']} & {r['without']} & {r['excluded_only']} \\\\")
    lines += [
        r"\bottomrule", r"\end{tabular}",
        f"\\caption{{Documents and citations with vs.\\ without {excluded_name}.}}",
        r"\label{tab:entity_exclusion_comparison}",
        r"\end{table}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def write_citations_by_area_comparison(area_rows_all: list, area_rows_excl: list,
                                       excluded_name: str, csv_path: str, tex_path: str):
    """
    Dedicated citations-by-G-PST-area table (CSV + LaTeX): for each area
    (+ a total row), total citations and IEEE PES Transactions citations with
    the excluded entity included vs. excluded.
    """
    by_key_all  = {r["area"]: r for r in area_rows_all}
    by_key_excl = {r["area"]: r for r in area_rows_excl}
    area_order  = AREA_KEYS + ["total"]
    labels      = {**AREA_TITLES, "total": "Total"}

    rows = []
    for key in area_order:
        a = by_key_all[key]
        e = by_key_excl[key]
        rows.append({
            "area": key, "label": labels[key],
            "with_esig_total": a["total"], "with_esig_ieee": a["ieee_transactions"],
            "without_esig_total": e["total"], "without_esig_ieee": e["ieee_transactions"],
            "esig_only_total": a["total"] - e["total"],
            "esig_only_ieee": a["ieee_transactions"] - e["ieee_transactions"],
        })

    # CSV
    fields = ["area", "label", "with_esig_total", "with_esig_ieee",
              "without_esig_total", "without_esig_ieee", "esig_only_total", "esig_only_ieee"]
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {csv_path}")

    # LaTeX (grouped column headers via multicolumn)
    lines = [
        r"\begin{table}[htbp]", r"\centering", r"\small",
        r"\setlength{\tabcolsep}{5pt}",
        r"\begin{tabular}{lrrrrrr}", r"\toprule",
        rf"     & \multicolumn{{2}}{{c}}{{With {excluded_name}}} & "
        rf"\multicolumn{{2}}{{c}}{{Without {excluded_name}}} & "
        rf"\multicolumn{{2}}{{c}}{{{excluded_name} only}} \\",
        r"    \cmidrule(lr){2-3} \cmidrule(lr){4-5} \cmidrule(lr){6-7}",
        r"    G-PST Area & Citations & IEEE PES Trans. & Citations & IEEE PES Trans. & Citations & IEEE PES Trans. \\",
        r"\midrule",
    ]
    for i, r in enumerate(rows):
        if i == len(rows) - 1:
            lines.append(r"\midrule")
            lines.append(
                f"    \\textbf{{{r['label']}}} & \\textbf{{{r['with_esig_total']}}} & "
                f"\\textbf{{{r['with_esig_ieee']}}} & \\textbf{{{r['without_esig_total']}}} & "
                f"\\textbf{{{r['without_esig_ieee']}}} & \\textbf{{{r['esig_only_total']}}} & "
                f"\\textbf{{{r['esig_only_ieee']}}} \\\\"
            )
        else:
            lines.append(
                f"    {r['label']} & {r['with_esig_total']} & {r['with_esig_ieee']} & "
                f"{r['without_esig_total']} & {r['without_esig_ieee']} & "
                f"{r['esig_only_total']} & {r['esig_only_ieee']} \\\\"
            )
    lines += [
        r"\bottomrule", r"\end{tabular}",
        f"\\caption{{Citations by G-PST research area, with vs.\\ without {excluded_name}.}}",
        r"\label{tab:citations_by_area_comparison}",
        r"\end{table}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


# ─── IEEE journal counter ─────────────────────────────────────────────────────

def extract_article_url(text: str) -> str:
    """Return the first DOI URL in text, falling back to any URL, or empty string."""
    m = _DOI_URL_RE.search(text) or _URL_RE.search(text)
    if not m:
        return ""
    return re.sub(r'[.,;]+$', '', m.group(0))


_YEAR_RE = re.compile(r'\b(19[0-9]\d|20[0-2]\d)\b')

def extract_publication_year(text: str) -> str:
    """Return the first plausible publication year (1950–2029) found in the citation."""
    m = _YEAR_RE.search(text)
    return m.group(1) if m else ""


_TITLE_RE = re.compile(
    r'"([^"]{6,300})"'                                    # straight double quotes
    r'|“([^”]{6,300})”'                    # curly double “...”
    r"|(?<![\w])'([A-Z][^']{5,299})'"                     # straight single quotes —
                                                 # opening quote must NOT be
                                                 # preceded by a letter (so a
                                                 # possessive/contraction like
                                                 # "O'Neill" or "company's"
                                                 # can't be mistaken for an
                                                 # opening quote) and the
                                                 # quoted span must start with
                                                 # a capital letter, like a
                                                 # real title does
    r'|«([^»]{6,300})»'                    # French guillemets «...»
    r'|»([^«]{6,300})«'                    # German guillemets (reversed) »...«
    r'|„([^“”"]{6,300})[“”"]'        # German low-9 „...“ / „..."
)

def extract_article_title(text: str) -> str:
    """
    Return the article title — the first quoted span in the citation (this
    corpus's consistent style is Author. Year. "Title." Journal ...).
    Recognizes straight/curly double quotes, straight single quotes, French
    guillemets «...», German guillemets (reversed) »...«, and the German
    low-9 opening quote „...“/„..." — the several citation styles this
    corpus's European TSOs (ENTSO-E, 50Hertz, RTE, Swissgrid) actually use.
    Citations that don't quote the title at all (mostly pre-1980s IEEE
    Transactions references using a bare period-separated format) return ""
    rather than guessing at a wrong substring.
    """
    m = _TITLE_RE.search(text)
    if not m:
        return ""
    title = next(g for g in m.groups() if g is not None)
    return re.sub(r'\s+', ' ', title).strip().rstrip(".,;:")


def deduplicate_citations(citations: list) -> tuple[list, int]:
    """
    Drop citations whose article URL has already been seen.
    Citations with no extractable URL are always kept (can't deduplicate them).
    Returns (deduplicated list, number of rows removed).
    """
    seen: set[str] = set()
    kept, removed = [], 0
    for row in citations:
        url = extract_article_url(row.get("full_reference", ""))
        if not url:
            kept.append(row)
            continue
        if url in seen:
            removed += 1
        else:
            seen.add(url)
            kept.append(row)
    return kept, removed


def extract_ieee_journal_name(text: str) -> str | None:
    m = IEEE_JOURNAL_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().rstrip(".,;:")
    name = " ".join(name.split())
    name = re.sub(r'\s*&\s*', ' And ', name)  # normalise & → And before title-casing
    return f"IEEE Transactions on {name.title()}" if name else None


_DECADE_BINS = [
    (1900, 1969, "pre-1970"),
    (1970, 1979, "1970–1979"),
    (1980, 1989, "1980–1989"),
    (1990, 1999, "1990–1999"),
    (2000, 2009, "2000–2009"),
    (2010, 2019, "2010–2019"),
    (2020, 9999, "2020–present"),
]


def compute_ieee_journals(citations: list) -> Counter:
    """Counter of IEEE PES Transactions journal names across all ieee-flagged citations."""
    counts: Counter = Counter()
    for row in citations:
        if row.get("is_ieee_transactions") != "Yes":
            continue
        name = extract_ieee_journal_name(row.get("full_reference", ""))
        name = name if name else "Unresolved"
        if not _is_pes_journal(name):
            continue
        counts[name] += 1
    return counts


def write_ieee_journals_csv(journal_counts: Counter, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    total = sum(journal_counts.values()) or 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["journal", "citations", "pct"])
        w.writeheader()
        for journal, count in journal_counts.most_common():
            w.writerow({
                "journal":   journal,
                "citations": count,
                "pct":       round(100 * count / total, 1),
            })
    print(f"  [ok] {path}")


# ─── Non-IEEE "other journal" name extraction ────────────────────────────────
#
# classify_reference() (extract_citations.py) only detects that a citation
# IS a journal article (ref_type=="journal") — it never extracts WHICH
# journal, unlike the IEEE Transactions path above. This fills that gap for
# the subset of "other journal" citations that name a recognizable journal.
# It will not resolve every one — many journal citations don't match any of
# these patterns and are left out of the count entirely (a name resolved
# from a formatting fluke is worse than an honest gap).

# A handful of patterns below are constrained beyond a bare name match,
# because an audit of every match against the real corpus (see the "check
# all documents and all journals" pass) found they otherwise fire on
# unrelated text at a high rate:
#   - \bScience\b alone matched "National Renewable Energy Laboratory"-style
#     substrings only for OTHER patterns, but standalone matched the tail of
#     unrelated journal names ("Energy & Environmental Science", "Climate
#     and Atmospheric Science", "CIGRE Science and Engineering", "Earth
#     System Science Data") and generic prose ("the Art and Science of...",
#     "American Association for the Advancement of Science") far more often
#     than real citations to the journal Science — 11 of 12 matches in this
#     corpus were false positives. Restricted to: not preceded by another
#     discipline word that makes it part of a different journal's name, and
#     followed shortly by a volume number (the only true positive found,
#     "...Estimates." Science 367 (6481): 984-986, fits this).
#   - "Renewable Energy" bare (no "and Sustainable") matched "National
#     Renewable Energy Laboratory" (NREL, an extremely common author
#     affiliation in this corpus) and generic phrases ("Renewable Energy
#     World", "...Renewable Energy Periods...") far more than real journal
#     citations — of 48 matches, only a handful were genuine. Restricted to
#     not preceded by "National " and followed shortly by a volume number
#     (every real "Renewable Energy VOL: PAGES" citation in the corpus
#     fits this; "Renewable and Sustainable Energy (Reviews)" is unaffected,
#     handled by its own unconstrained pattern above it).
#   - "Energy Policy" / "Energies" bare matched "Energy Policy Act of 2005"
#     (a law) and "...Kleinman Center for Energy Policy" (an institute), and
#     "International Conference on Renewable Energies..." — restricted the
#     same way (followed shortly by a volume number).
#   - "Journal of Environmental ... Economics ... Management" matched a
#     citation to a paper merely *presented at a conference of* that
#     journal, not published in it — excluded via a lookbehind.
#   - "Journal of Power and Energy" and "Journal of Economics" bare: every
#     real citation in the corpus is actually to "IEEE Open Access Journal
#     of Power and Energy" (IEEE) and "RAND Journal of Economics" (Wiley)
#     respectively — different journals, different publishers, from the
#     unrelated SAGE/Springer journals those bare names would otherwise
#     misattribute to. Captures the real prefix when present.
_OTHER_JOURNAL_PATTERNS = [
    r'Nature\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?',
    r'(?<!Environmental )(?<!Atmospheric )(?<!Political )(?<!Computer )'
    r'(?<!Social )(?<!Library )(?<!Materials )(?<!Cognitive )(?<!Data )'
    r'\bScience\b(?=[\s,;:.()]{0,3}\d)',
    r'Energy Policy(?=[\s,;:.()]{0,3}\d)',
    r'Applied Energy',
    r'Renewable and Sustainable Energy(?: Reviews)?',
    r'(?<!National )Renewable Energy(?=[\s,;:.()]{0,3}\d)',
    r'Electric Power Systems Research',
    r'International Journal of Electrical Power(?: and Energy Systems)?',
    r'International Journal of Hydrogen Energy',
    r'International Journal of Climatology',
    r'Journal of Big Data',
    r'Journal of Applied Meteorology and Climatology',
    r'Journal of Modern Power Systems and Clean Energy',
    r'(?<!Conference of the )'
    r'Journal of Environmental(?:\s+and)? Economics(?:\s+and)? Management',
    r'Journal of Regulatory Economics',
    r'Journal of Institutional Economics',
    r'CSEE Journal of Power and Energy Systems|'
    r'(?:IEEE Open Access )?Journal of Power and Energy',
    r'Journal of Engineering',
    r'(?:RAND )?Journal of Economics',
    r'The Energy Journal',
    r'\bEnergies\b(?=[\s,;:.()]{0,3}\d)',
    r'Electricity Journal',
    r'\bJoule\b',
    r'Electric Power Components and Systems',
    r'Sustainable Energy, Grids and Networks',
]
OTHER_JOURNAL_RE = re.compile('|'.join(_OTHER_JOURNAL_PATTERNS))

# Collapses near-duplicate matches caused by truncated/loosely-worded
# citations onto one canonical name (must match a name in
# data/input/journal_publishers.csv exactly).
_JOURNAL_NAME_CANONICAL = {
    "renewable and sustainable energy": "Renewable and Sustainable Energy Reviews",
    "international journal of electrical power": "International Journal of Electrical Power and Energy Systems",
    "journal of environmental and economics management": "Journal of Environmental Economics and Management",
}


def extract_other_journal_name(text: str) -> str | None:
    m = OTHER_JOURNAL_RE.search(text)
    if not m:
        return None
    name = re.sub(r'\s+', ' ', m.group(0)).strip().rstrip('.,;:')
    return _JOURNAL_NAME_CANONICAL.get(name.lower(), name)


def compute_other_journals(citations: list, manual_names: dict | None = None) -> Counter:
    """Counter of identifiable non-IEEE journal names among journal-type
    citations. Citations whose journal name can't be pattern-matched are
    silently excluded (not lumped into an "Unresolved" bucket) — see
    docstring above _OTHER_JOURNAL_PATTERNS for why. `manual_names`
    ({full_reference: journal}) — from apply_citation_corrections() — is
    checked first, for citations only identifiable by manual review."""
    manual_names = manual_names or {}
    counts: Counter = Counter()
    for row in citations:
        if row.get("ref_type") != "journal" or row.get("is_ieee_transactions") == "Yes":
            continue
        name = manual_names.get(row.get("full_reference", "")) or \
            extract_other_journal_name(row.get("full_reference", ""))
        if name:
            counts[name] += 1
    return counts


def write_unresolved_other_journals_csv(citations: list, path: str, manual_names: dict | None = None):
    """
    Write (or update) a review CSV — one row per "other journal" citation
    whose journal name couldn't be auto-extracted (see compute_other_journals()
    / extract_other_journal_name()) — for manual identification.

    Same pattern as write_ieee_citations_review_csv(): every run, all
    currently-unresolved citations are (re)written, but any journal/
    publisher/publisher_url you've already filled in for a given
    full_reference is preserved (matched by exact reference text), so
    re-running the pipeline never wipes out manual corrections. Once you
    fill in the `journal` column here, add that journal to
    data/input/journal_publishers.csv (with its publisher + a link) to get it
    picked up by the rest of the pipeline.
    """
    manual_names = manual_names or {}
    existing: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["full_reference"]] = {
                    "journal": row.get("journal", "").strip(),
                    "publisher": row.get("publisher", "").strip(),
                    "publisher_url": row.get("publisher_url", "").strip(),
                }

    rows = []
    for row in citations:
        if row.get("ref_type") != "journal" or row.get("is_ieee_transactions") == "Yes":
            continue
        ref = row.get("full_reference", "")
        if ref in manual_names or extract_other_journal_name(ref):
            continue  # already resolved (manually or automatically) — not unresolved
        prev = existing.get(ref, {})
        rows.append({
            "full_reference": ref,
            "entity_name":    row.get("entity_name", ""),
            "pdf_url":        row.get("pdf_url", ""),
            "journal":        prev.get("journal", ""),
            "publisher":      prev.get("publisher", ""),
            "publisher_url":  prev.get("publisher_url", ""),
        })

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["full_reference", "entity_name", "pdf_url", "journal", "publisher", "publisher_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {path}  ({len(rows)} unresolved journal citations — "
          f"fill in journal/publisher/publisher_url columns to identify them)")


# ─── Manual corrections from completed unresolved_other_journals*.csv ────────
#
# Once you fill in a row's `journal` column (see write_unresolved_other_journals_csv
# above), this classifies each filled-in value into an action and applies it
# GLOBALLY to the citations list before any stats/figures are computed — not
# just within the journal/publisher tables — so a correction here also moves
# total_citations, area breakdowns, entity tables, etc.
#
#   "remove"                    -> not a real citation at all (legal
#                                   boilerplate, chart captions, appendix
#                                   text) — dropped from the dataset entirely.
#   "IEEE Transactions on ..."   -> was misclassified as a generic journal;
#                                   reclassified as an IEEE Transactions
#                                   citation under that journal.
#   "IEEE conference"             -> real citation, wrong ref_type (a
#                                   conference paper, not a journal article)
#                                   — reclassified, stays counted as a
#                                   citation but excluded from journal counts.
#   anything else non-empty      -> a manually-identified other-journal name
#                                   (add the journal + publisher to
#                                   data/input/journal_publishers.csv so it's
#                                   recognized going forward).
#
# Corrections are applied POSITIONALLY per full_reference: if the same
# full_reference appears N times in the citations list (this happens for
# documents tagged under multiple G-PST areas — each area duplicates that
# document's citations once), the 1st row in the review CSV for that
# full_reference corrects the 1st occurrence, the 2nd row the 2nd occurrence,
# etc. — matching write_unresolved_other_journals_csv's iteration order. This
# is what lets you resolve one duplicate copy as a real citation while
# removing the rest as over-counted duplicates, exactly as filled in.

# Reconciles hand-typed journal names in the review CSV (typos, capitalization
# variants) with the exact spelling already used elsewhere in the pipeline
# (data/input/journal_publishers.csv, existing ieee_journals.csv entries), so a
# manually-identified journal merges into existing counts instead of forking
# a near-duplicate entry.
_MANUAL_JOURNAL_CANONICAL = {
    "transactions of the american institue of electrical engineers":
        "Transactions of the American Institute of Electrical Engineers",
    "ieee transactions on power apparatus and systems":
        "IEEE Transactions on Power Apparatus And Systems",
    "ieee transactions on power system":
        "IEEE Transactions on Power Systems",
    "ieee transactions in power delivery":
        "IEEE Transactions on Power Delivery",
    "journal of environmental and economics management":
        "Journal of Environmental Economics and Management",
    "cigre science and engineering journal":
        "CIGRE Science and Engineering",
}

# Bare ref_type names usable in the review CSV's `journal` column to
# reclassify a citation that was extracted as ref_type="journal" but isn't
# actually a journal article (e.g. a book cited alongside journal articles).
# The citation is kept (still counted in totals), just no longer treated as
# a journal for journal/publisher tables. "ieee conference" (handled
# separately below, not in this set) predates this and stays a literal
# two-word marker for backward compatibility with existing corrections.
_REF_TYPE_RECLASSIFY_MARKERS = {
    "book", "report", "standard", "magazine", "thesis", "dataset", "website",
    "conference",
}

# Non-ref_type-marker text that a reviewer typed in the `journal` column
# meaning "this isn't a journal, here's what it actually is" rather than an
# actual journal name — e.g. "EPRI" naming the org behind a technical report,
# or "Springer Nature" naming the publisher of a book chapter cited alongside
# journal articles. Mapped to the ref_type it actually is.
_MANUAL_REF_TYPE_ALIASES = {
    "epri": "report",
    "springer nature": "book",
}


def load_citation_corrections(paths: list) -> dict:
    """{full_reference: [ordered list of correction dicts]} from one or more
    completed unresolved_other_journals*.csv files."""
    corrections: dict = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                journal = row.get("journal", "").strip()
                if not journal:
                    continue
                journal = _MANUAL_JOURNAL_CANONICAL.get(journal.lower(), journal)
                jl = journal.lower()
                if jl == "remove":
                    corr = {"action": "remove"}
                elif jl.startswith("ieee transactions on"):
                    corr = {"action": "ieee", "journal": journal}
                elif jl == "ieee conference":
                    corr = {"action": "reclassify", "ref_type": "conference"}
                elif jl in _REF_TYPE_RECLASSIFY_MARKERS:
                    corr = {"action": "reclassify", "ref_type": jl}
                elif jl in _MANUAL_REF_TYPE_ALIASES:
                    corr = {"action": "reclassify", "ref_type": _MANUAL_REF_TYPE_ALIASES[jl]}
                else:
                    corr = {
                        "action": "other", "journal": journal,
                        "publisher": row.get("publisher", "").strip(),
                        "publisher_url": row.get("publisher_url", "").strip(),
                    }
                corrections.setdefault(row["full_reference"], []).append(corr)
    return corrections


def apply_citation_corrections(citations: list, corrections: dict):
    """
    Applies load_citation_corrections()'s output to a citations list.
    Returns (corrected_citations, manual_ieee_names, manual_other_names) —
    the two manual_* dicts are {full_reference: journal_name}, for seeding
    write_ieee_citations_review_csv() and compute_other_journals()
    respectively so the manually-identified name is used instead of (or
    when regex extraction would otherwise fail to find) an automatic one.
    """
    occurrence_idx: dict = {}
    kept, manual_ieee_names, manual_other_names = [], {}, {}
    removed = 0
    reclassified: Counter = Counter()
    for c in citations:
        ref = c.get("full_reference", "")
        corr_list = corrections.get(ref)
        if not corr_list:
            kept.append(c)
            continue
        idx = occurrence_idx.get(ref, 0)
        occurrence_idx[ref] = idx + 1
        if idx >= len(corr_list):
            kept.append(c)  # more occurrences than review rows — leave as-is
            continue
        corr = corr_list[idx]
        if corr["action"] == "remove":
            removed += 1
            continue
        c = dict(c)
        if corr["action"] == "reclassify":
            c["ref_type"] = corr["ref_type"]
            reclassified[corr["ref_type"]] += 1
        elif corr["action"] == "ieee":
            c["is_ieee_transactions"] = "Yes"
            manual_ieee_names[ref] = corr["journal"]
        elif corr["action"] == "other":
            manual_other_names[ref] = corr["journal"]
        kept.append(c)

    if removed or reclassified or manual_ieee_names or manual_other_names:
        reclass_str = ", ".join(f"{n} as {t}" for t, n in reclassified.items())
        print(f"    Applied citation corrections: {removed} removed, "
              f"reclassified [{reclass_str}], "
              f"{len(manual_ieee_names)} reclassified as IEEE Transactions, "
              f"{len(manual_other_names)} other-journal names resolved")
    return kept, manual_ieee_names, manual_other_names


# ─── Journal → publisher lookup and grouping ─────────────────────────────────

def load_journal_publishers(path: str) -> dict:
    """{journal_name: {"publisher": ..., "publisher_url": ...}} from
    data/input/journal_publishers.csv."""
    lookup = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lookup[row["journal"]] = {
                "publisher": row["publisher"],
                "publisher_url": row["publisher_url"],
            }
    return lookup


def write_journal_publishers_csv(ieee_counts: Counter, other_counts: Counter,
                                 publishers: dict, path: str, tex_path: str | None = None,
                                 label: str = "tab:journal_publishers",
                                 caption_suffix: str = ""):
    """One row per journal (IEEE Transactions + identified other journals):
    name, citations, is_ieee_transactions, publisher, publisher_url."""
    rows = []
    for name, count in ieee_counts.most_common():
        info = publishers.get(name, {"publisher": "Unknown", "publisher_url": ""})
        rows.append({"journal": name, "citations": count, "is_ieee_transactions": "Yes",
                     "publisher": info["publisher"], "publisher_url": info["publisher_url"]})
    for name, count in other_counts.most_common():
        info = publishers.get(name, {"publisher": "Unknown", "publisher_url": ""})
        rows.append({"journal": name, "citations": count, "is_ieee_transactions": "No",
                     "publisher": info["publisher"], "publisher_url": info["publisher_url"]})
    rows.sort(key=lambda r: -r["citations"])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["journal", "citations", "is_ieee_transactions", "publisher", "publisher_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {path}")

    if tex_path:
        _write_journal_publishers_tex(rows, tex_path, label=label, caption_suffix=caption_suffix)


def _write_journal_publishers_tex(rows: list, tex_path: str,
                                  label: str = "tab:journal_publishers",
                                  caption_suffix: str = ""):
    """
    LaTeX longtable version of write_journal_publishers_csv()'s rows: one
    row per journal, with a clickable \\href link to that journal's page.
    This is a table FRAGMENT (no \\documentclass/\\begin{document}), meant to
    be \\input into a paper that already loads hyperref and booktabs — unlike
    the standalone figures under data/results/figures/, which compile on their own.
    """
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    total_cites = sum(r["citations"] for r in rows)
    lines = [
        r"% Requires \usepackage{hyperref} and \usepackage{booktabs} in the",
        r"% including document.",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\begin{tabular}{lrcll}",
        r"\toprule",
        r"Journal & Citations & IEEE Trans. & Publisher & Link \\",
        r"\midrule",
    ]
    for r in rows:
        ieee_mark = "Yes" if r["is_ieee_transactions"] == "Yes" else "--"
        if r["publisher_url"]:
            link = f"\\href{{{r['publisher_url']}}}{{link}}"
        else:
            link = ""
        lines.append(
            f"    {esc(r['journal'])} & {r['citations']} & {ieee_mark} & "
            f"{esc(r['publisher'])} & {link} \\\\"
        )
    lines += [
        r"\midrule",
        f"    \\textbf{{Total}} & \\textbf{{{total_cites}}} & & & \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\caption{Identified journals cited, with citation count, whether "
        r"the journal is an IEEE Transactions title, publisher, and a link "
        r"to the journal's page" + caption_suffix + r".}",
        rf"\label{{{label}}}",
        r"\end{table}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


# Journals actually titled "IEEE Transactions on ___" that are sponsored by
# the IEEE Power & Energy Society (PES) — this is THE canonical definition of
# "IEEE PES Transactions" used everywhere in this pipeline (entity/area
# tables, ieee_journals.csv, every ieee_* figure, and the publisher_groups
# "IEEE PES Transactions" vs "IEEE Other" split below). Everything else
# IEEE-published — PELS/IAS/IES/Computer-Society-sponsored Transactions,
# Proceedings of the IEEE, magazines, the AIEE-era Transactions, and PES's
# own non-"Transactions"-titled publications like the Open Access Journal of
# Power and Energy — counts as "IEEE Other", not as an "IEEE PES Transactions"
# citation, even though the raw extractor (extract_citations.py) flags ANY
# "IEEE Transactions on X" title as a candidate; apply_pes_transactions_filter()
# narrows that broad candidate set down to this one at analysis time.
IEEE_PES_JOURNALS = {
    "IEEE Transactions on Power Systems",
    "IEEE Transactions on Power Delivery",
    "IEEE Transactions on Power Apparatus And Systems",
    "IEEE Transactions on Energy Conversion",
    "IEEE Transactions on Sustainable Energy",
    "IEEE Transactions on Smart Grid",
}


def _is_pes_journal(name: str) -> bool:
    return name.strip().lower() in {j.lower() for j in IEEE_PES_JOURNALS}


def ieee_publisher_subgroup(journal_name: str) -> str:
    return "IEEE PES Transactions" if _is_pes_journal(journal_name) else "IEEE Other"


# Publisher's own homepage — used for the grouped table, instead of
# arbitrarily borrowing one member journal's URL (which journal happened to
# be processed last previously, so e.g. "Elsevier" pointed at whichever
# specific Elsevier journal page was seen last rather than elsevier.com).
PUBLISHER_HOMEPAGES = {
    "IEEE": "https://www.ieee.org",
    "IEEE PES Transactions": "https://www.ieee-pes.org",
    "IEEE Other": "https://www.ieee.org",
    "Elsevier": "https://www.elsevier.com",
    "Cell Press (Elsevier)": "https://www.cell.com",
    "Springer Nature": "https://www.springernature.com",
    "AAAS": "https://www.aaas.org",
    "MDPI": "https://www.mdpi.com",
    "Wiley (Royal Meteorological Society)": "https://www.wiley.com",
    "SAGE Publishing": "https://sagepub.com",
    "Cambridge University Press": "https://www.cambridge.org",
    "American Meteorological Society": "https://www.ametsoc.org",
    "IET": "https://www.theiet.org",
}


def write_publisher_groups(ieee_counts: Counter, other_counts: Counter,
                           publishers: dict, csv_path: str, tex_path: str | None = None,
                           label: str = "tab:publisher_groups", caption_suffix: str = ""):
    """
    Groups every identified journal (IEEE Transactions + other) by publisher:
    total citations, number of distinct journals, and which journals.
    """
    groups: dict = {}
    for counts in (ieee_counts, other_counts):
        for name, count in counts.items():
            info = publishers.get(name, {"publisher": "Unknown", "publisher_url": ""})
            pub = info["publisher"]
            if pub == "IEEE":
                pub = ieee_publisher_subgroup(name)
            url = PUBLISHER_HOMEPAGES.get(pub, info["publisher_url"])
            g = groups.setdefault(pub, {"publisher_url": url, "citations": 0, "journals": []})
            g["citations"] += count
            g["journals"].append((name, count))

    rows = []
    for pub, g in groups.items():
        journals_sorted = sorted(g["journals"], key=lambda x: -x[1])
        rows.append({
            "publisher": pub,
            "publisher_url": g["publisher_url"],
            "citations": g["citations"],
            "num_journals": len(journals_sorted),
            "journals": "; ".join(f"{n} ({c})" for n, c in journals_sorted),
        })
    rows.sort(key=lambda r: -r["citations"])

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    fields = ["publisher", "publisher_url", "citations", "num_journals", "journals"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {csv_path}")

    if tex_path:
        _write_publisher_groups_tex(rows, groups, tex_path, label=label,
                                    caption_suffix=caption_suffix)


def _write_publisher_groups_tex(rows: list, groups: dict, tex_path: str,
                                label: str = "tab:publisher_groups",
                                caption_suffix: str = ""):
    """
    Compact summary table: one row per publisher, just the journal count and
    citation count — no per-journal breakdown or journal names. (For the
    full per-journal detail, see journal_publishers.csv/.tex instead.)
    """
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    total_cites = sum(r["citations"] for r in rows)
    total_jrnls = sum(r["num_journals"] for r in rows)
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\begin{tabular}{lrr}",
        r"\toprule",
        r"Publisher & Journals & Citations \\",
        r"\midrule",
    ]
    for r in rows:
        lines.append(f"    {esc(r['publisher'])} & {r['num_journals']} & {r['citations']} \\\\")
    lines += [
        r"\midrule",
        f"    \\textbf{{Total}} & \\textbf{{{total_jrnls}}} & \\textbf{{{total_cites}}} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\caption{Identified journals grouped by publisher: number of "
        r"distinct journals and total citations per publisher (IEEE "
        r"Transactions journals + other journals whose name could be "
        r"resolved from the citation text). Citation counts only cover "
        r"journals a name could be extracted for, not every "
        r"journal-type citation" + caption_suffix + r".}",
        rf"\label{{{label}}}",
        r"\end{table}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def write_citation_counts_summary(grand: dict, grand_excl: dict, comp_entity: str,
                                  named_total: int, named_total_excl: int,
                                  tex_path: str, label: str = "tab:citation_counts_summary"):
    """
    Top-level with/without-{comp_entity} breakdown: total citations, IEEE
    PES Transactions citations, other journal citations (every non-PES
    journal-type citation, whether or not the specific journal could be
    named — this includes non-PES IEEE Transactions titles like Big Data),
    conference citations, and other non-journal, non-conference citations
    (reports, standards, books, etc). `named_total`/`named_total_excl` is
    the narrower count from journal_publishers.tex — IEEE PES Transactions +
    only the other-journal citations a specific journal name could be
    resolved for.
    """
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    rows = [
        ("Total citations", grand["total"], grand_excl["total"]),
        ("IEEE PES Transactions citations", grand["ieee_transactions"], grand_excl["ieee_transactions"]),
        ("Other journal citations", grand["other_journals"], grand_excl["other_journals"]),
        ("Conference citations", grand.get("conferences", 0), grand_excl.get("conferences", 0)),
        ("Other (non-journal, non-conference) citations", grand["other"], grand_excl["other"]),
    ]
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\begin{tabular}{lrr}",
        r"\toprule",
        rf"Metric & With {esc(comp_entity)} & Without {esc(comp_entity)} \\",
        r"\midrule",
    ]
    for name, w, wo in rows:
        lines.append(f"    {esc(name)} & {w} & {wo} \\\\")
    lines += [
        r"\midrule",
        f"    \\quad of which named other-journal citations & "
        f"{named_total - grand['ieee_transactions']} & "
        f"{named_total_excl - grand_excl['ieee_transactions']} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\caption{Citation counts " + esc(f"with and without {comp_entity}") +
        r": IEEE PES Transactions, other (non-PES) journal citations, "
        r"conference citations, and other non-journal citations. "
        r"\emph{Other journal citations} counts every non-PES journal-type "
        r"citation; the final row (\emph{of which named}) narrows that to "
        r"the subset where the specific journal name could be resolved "
        r"(see journal\_publishers.tex).}",
        rf"\label{{{label}}}",
        r"\end{table}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def load_ieee_journal_names_from_review_csv(path: str) -> dict:
    """{full_reference: journal name} for every non-excluded IEEE Transactions
    citation in the review CSV — the per-citation counterpart to
    load_ieee_journals_from_review_csv()'s aggregate Counter."""
    names: dict = {}
    if not os.path.exists(path):
        return names
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                continue
            journal = row.get("journal", "").strip() or "Unresolved"
            if not _is_pes_journal(journal):
                continue
            names[row["full_reference"]] = journal
    return names


def write_journal_citations_by_document_csv(citations: list, ieee_names: dict,
                                            manual_other_names: dict, publishers: dict,
                                            path: str, exclude_entity: str | None = None,
                                            tex_path: str | None = None,
                                            label: str = "tab:journal_citations_by_document",
                                            caption_suffix: str = ""):
    """
    One row per counted journal citation (IEEE Transactions + named other
    journals) — the same rows journal_publishers.csv sums up — broken out
    per citing document with a link, so a journal's citation count can be
    traced back to exactly which document(s) it came from.

    `doc_title` is carried in the in-memory rows (for the optional `tex_path`
    table below) but deliberately left out of the CSV's own columns/fields —
    the CSV keeps `document` (pdf_filename) as its per-document identifier.
    """
    rows = []
    for c in citations:
        if exclude_entity and c["entity_name"] == exclude_entity:
            continue
        ref = c.get("full_reference", "")
        if c.get("is_ieee_transactions") == "Yes":
            name = ieee_names.get(ref)  # None if excluded via review CSV
        elif c.get("ref_type") == "journal":
            name = manual_other_names.get(ref) or extract_other_journal_name(ref)
        else:
            name = None
        if not name:
            continue
        info = publishers.get(name, {"publisher": "Unknown", "publisher_url": ""})
        rows.append({
            "journal": name,
            "article_title": extract_article_title(ref),
            "publisher": info["publisher"],
            "publisher_url": info["publisher_url"],
            "is_ieee_transactions": c.get("is_ieee_transactions"),
            "entity_name": c["entity_name"],
            "research_area": AREA_TITLES.get(c.get("gpst_area", ""), c.get("gpst_area", "")),
            "document": c.get("pdf_filename", ""),
            "document_url": c.get("pdf_url", ""),
            "doc_title": c.get("doc_title", ""),
        })
    rows.sort(key=lambda r: (r["journal"].lower(), r["entity_name"], r["document"]))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["journal", "article_title", "publisher", "publisher_url", "is_ieee_transactions",
              "entity_name", "research_area", "document", "document_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {path}  ({len(rows)} journal citations, traced to source document)")

    if tex_path:
        _write_journal_citations_by_document_tex(rows, tex_path, label=label,
                                                 caption_suffix=caption_suffix)


def _write_journal_citations_by_document_tex(rows: list, tex_path: str,
                                             label: str = "tab:journal_citations_by_document",
                                             caption_suffix: str = ""):
    """
    LaTeX longtable version of write_journal_citations_by_document_csv()'s
    rows — same data, but with publisher_url and document_url dropped (not
    useful in print), and the citing document identified by its title
    (doc_title, as extracted from the document itself) rather than its
    pdf_filename. Deliberately tex-only: the CSV keeps pdf_filename and both
    URL columns as its stable, machine-readable identifiers.
    """
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    lines = [
        r"% Requires \usepackage{longtable} and \usepackage{booktabs} in the",
        r"% including document. longtable needs a single-column layout to",
        r"% paginate itself, so in a twocolumn class (e.g. \documentclass",
        r"% [conference]{IEEEtran}) wrap this \input with \onecolumn ... ",
        r"% \twocolumn, e.g.:",
        r"%   \onecolumn",
        r"%   \input{journal_citations_by_document_system_operators}",
        r"%   \twocolumn",
        r"% Omitting that wrapper in a twocolumn document raises",
        r"% \"Package longtable Error: longtable not in -1 column mode\".",
        r"\begin{longtable}{p{2.6cm} p{4cm} p{2cm} l l p{3.2cm}}",
        r"\toprule",
        r"Journal & Article Title & Publisher & Entity & "
        r"Research Area & Document Title \\",
        r"\midrule",
        r"\endhead",
    ]
    for r in rows:
        lines.append(
            f"{esc(r['journal'])} & {esc(r['article_title'])} & {esc(r['publisher'])} & "
            f"{esc(r['entity_name'])} & {esc(r['research_area'])} & "
            f"{esc(r['doc_title'])} \\\\"
        )
    lines += [
        r"\bottomrule",
        r"\caption{Journal citations traced to their citing document" + caption_suffix + r".}",
        rf"\label{{{label}}}",
        r"\end{longtable}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def _write_journal_citations_by_document_tex_from_csv(csv_path: str, tex_path: str,
                                                       label: str = "tab:journal_citations_by_document",
                                                       caption_suffix: str = ""):
    """
    Same rendering as _write_journal_citations_by_document_tex(), but reads
    its rows directly from an on-disk journal_citations_by_document*.csv
    instead of freshly-computed citations — used where that CSV is
    human-curated ground truth (hand-corrected research areas in a
    'validated_research_area' column, hand-supplied document titles in a
    'document title' column, possibly hand-cleared article titles) that must
    never be silently regenerated over. Falls back to the auto columns
    (research_area, document) when the human-curated ones aren't present,
    so this also works against a freshly auto-written CSV with no manual
    columns yet.
    """
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    def doi_cell(doi):
        # \url, not plain text: it can break a long DOI at / and . to fit
        # the narrow column, and needs no escaping of DOI characters.
        doi = doi.strip()
        return f"\\url{{{doi}}}" if doi else "--"

    with open(csv_path, encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))

    lines = [
        r"% Requires \usepackage{longtable}, \usepackage{booktabs},",
        r"% \usepackage{array}, and \usepackage{url} in the including",
        r"% document. longtable needs",
        r"% a single-column layout to paginate itself, so in a twocolumn class",
        r"% (e.g. \documentclass[conference]{IEEEtran}) wrap this \input with",
        r"% \onecolumn ... \twocolumn, e.g.:",
        r"%   \onecolumn",
        r"%   \input{journal_citations_by_document_system_operators}",
        r"%   \twocolumn",
        r"% Omitting that wrapper in a twocolumn document raises",
        r"% \"Package longtable Error: longtable not in -1 column mode\".",
        r"% \tiny, \tabcolsep and \arraystretch are scoped to the {...}",
        r"% group below so they don't leak into the rest of the including",
        r"% document — neither table nor longtable opens its own group for",
        r"% font-size commands. Column widths are fractions of \linewidth so",
        r"% the table always fits the text block, whatever the page layout.",
        r"{\tiny",
        r"\setlength{\tabcolsep}{2pt}",
        r"\renewcommand{\arraystretch}{1.0}",
        # L{w}: ragged-right p{w} — justified text in columns this narrow
        # leaves wide inter-word gaps. Defined inside the {...} group, so it
        # doesn't clobber an L column type the including document may have.
        r"\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}",
        r"\begin{longtable}{c L{0.065\linewidth} L{0.12\linewidth} L{0.06\linewidth} "
        r"L{0.17\linewidth} L{0.11\linewidth} c L{0.13\linewidth} L{0.11\linewidth} "
        r"L{0.07\linewidth}}",
        r"\caption{Journal citations traced to their citing document" + caption_suffix + r".}",
        rf"\label{{{label}}}\\",
        r"\toprule",
        r"No. & Entity & Document Title & Research Area & "
        r"Article Title & Authors & Year & DOI & Journal & Publisher \\",
        r"\midrule",
        r"\endhead",
    ]

    def doc_title_of(r):
        return r.get("document title", "").strip() or r.get("document", "").strip()

    # Group rows by document (preserving each document's row order) so every
    # citation from the same document sits together, then number documents
    # in title order — each document gets one number, shared by all its rows.
    rows = sorted(rows, key=lambda r: doc_title_of(r).lower())
    doc_numbers: dict[str, int] = {}
    for r in rows:
        title = doc_title_of(r)
        if title not in doc_numbers:
            doc_numbers[title] = len(doc_numbers) + 1

    groups: list[list[dict]] = []
    for r in rows:
        title = doc_title_of(r)
        if groups and doc_title_of(groups[-1][0]) == title:
            groups[-1].append(r)
        else:
            groups.append([r])

    article_num = 0  # runs continuously across all rows/documents, 1..N
    for gi, group in enumerate(groups):
        if gi > 0:
            lines.append(r"\midrule")
        n = len(group)
        title = doc_title_of(group[0])
        area = group[0].get("validated_research_area", "").strip() or group[0].get("research_area", "").strip()
        # No./Entity/Document Title/Research Area are document-level — shown
        # once, top-aligned in the group's first row, not repeated on every
        # citation row. Deliberately plain cells, not \multirow: a \multirow
        # box doesn't stretch its rows, so a document title taller than the
        # group's citation rows overprinted the next group's rows.
        doc_cells = (f"{doc_numbers[title]} & {esc(group[0]['entity_name'])} & "
                     f"{esc(title)} & {esc(area)}")
        for i, r in enumerate(group):
            article_num += 1
            article_cell = (f"{article_num}. {esc(r['article_title'])} & "
                            f"{esc(r.get('authors', ''))} & {esc(r.get('year', ''))} & "
                            f"{doi_cell(r.get('doi', ''))} & "
                            f"{esc(r['journal'])} & {esc(r['publisher'])} \\\\")
            if i == 0:
                lines.append(f"{doc_cells} & {article_cell}")
            else:
                lines.append(f" & & & & {article_cell}")
    lines += [
        r"\bottomrule",
        r"\end{longtable}",
        r"}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def write_ieee_citations_review_csv(citations: list, path: str, manual_names: dict | None = None):
    """
    Write (or update) a review CSV with one row per IEEE Transactions citation.
    Columns: full_reference, article_url, auto_pub_year, pub_year, pdf_url,
    entity_name, auto_journal, journal, exclude

    Every run: all IEEE-flagged citations are written.
    Manual edits to the 'journal' and 'pub_year' columns are preserved across
    runs (pub_year regex extraction fails on citations whose text is
    truncated mid-year by a PDF page/column-wrap artifact — e.g. "...IEEE
    Transactions on Power Systems, September 202" — where a human can supply
    the true year from the source; auto_pub_year keeps the raw extraction
    result visible alongside it).
    To permanently drop a false positive, set exclude=yes in that row — it will
    be kept in the file (so the setting persists) but omitted from stats and figures.

    `manual_names` ({full_reference: journal}) — from apply_citation_corrections()
    — seeds auto_journal for citations reclassified as IEEE Transactions by manual
    review, where regex extraction won't find the name in the citation text.
    """
    manual_names = manual_names or {}
    existing: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["full_reference"]] = {
                    "journal":  row.get("journal", "").strip(),
                    "pub_year": row.get("pub_year", "").strip(),
                    "exclude":  row.get("exclude", "").strip().lower(),
                }

    rows = []
    for cit in citations:
        if cit.get("is_ieee_transactions") != "Yes":
            continue
        ref  = cit.get("full_reference", "")
        auto = extract_ieee_journal_name(ref) or manual_names.get(ref) or "Unresolved"
        auto_year = extract_publication_year(ref)
        prev = existing.get(ref, {})
        rows.append({
            "full_reference": ref,
            "article_url":    extract_article_url(ref),
            "auto_pub_year":  auto_year,
            "pub_year":       prev.get("pub_year") or auto_year,  # keep manual edit
            "pdf_url":        cit.get("pdf_url", ""),
            "entity_name":    cit.get("entity_name", ""),
            "auto_journal":   auto,
            "journal":        prev.get("journal", auto),   # keep manual edit
            "exclude":        prev.get("exclude", ""),     # keep exclusion flag
        })

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["full_reference", "article_url", "auto_pub_year", "pub_year", "pdf_url", "entity_name", "auto_journal", "journal", "exclude"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {path}  ({len(rows)} IEEE citations — edit 'journal' column; set exclude=yes to drop false positives)")


def load_ieee_journals_from_review_csv(path: str, exclude_entities: set | None = None) -> Counter:
    """Build a journal Counter from the user-editable review CSV, skipping excluded rows
    and (optionally) rows belonging to any entity in exclude_entities."""
    exclude_entities = exclude_entities or set()
    counts: Counter = Counter()
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                continue
            if row.get("entity_name", "") in exclude_entities:
                continue
            journal = row.get("journal", "").strip() or "Unresolved"
            if not _is_pes_journal(journal):
                continue
            counts[journal] += 1
    return counts


def load_ieee_years_from_review_csv(path: str, exclude_entities: set | None = None) -> dict:
    """Build decade-bin counts from pub_year in the user-editable review CSV, skipping
    excluded rows and (optionally) rows belonging to any entity in exclude_entities."""
    exclude_entities = exclude_entities or set()
    counts = {label: 0 for _, _, label in _DECADE_BINS}
    counts["Unknown"] = 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                continue
            if row.get("entity_name", "") in exclude_entities:
                continue
            if not _is_pes_journal(row.get("journal", "").strip() or "Unresolved"):
                continue
            year_str = row.get("pub_year", "").strip()
            if not year_str:
                counts["Unknown"] += 1
                continue
            year = int(year_str)
            placed = False
            for lo, hi, label in _DECADE_BINS:
                if lo <= year <= hi:
                    counts[label] += 1
                    placed = True
                    break
            if not placed:
                counts["Unknown"] += 1
    return counts


def load_ieee_journals_by_year_from_review_csv(path: str, exclude_entities: set | None = None) -> dict:
    """
    Like load_ieee_years_from_review_csv(), but counts DISTINCT IEEE PES
    journal articles per decade bin (deduplicated by article_url, falling
    back to full_reference when no URL was extracted) instead of every
    citation instance — the "how many different papers were actually
    published" counterpart to citation volume. The same paper cited by
    more than one system-operator document counts once here but multiple
    times in load_ieee_years_from_review_csv()'s citation count, so pairing
    the two on one chart shows citation volume vs. underlying paper count.
    """
    exclude_entities = exclude_entities or set()
    seen: set[str] = set()
    counts = {label: 0 for _, _, label in _DECADE_BINS}
    counts["Unknown"] = 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                continue
            if row.get("entity_name", "") in exclude_entities:
                continue
            if not _is_pes_journal(row.get("journal", "").strip() or "Unresolved"):
                continue
            key = row.get("article_url", "").strip() or row.get("full_reference", "").strip()
            if key in seen:
                continue
            seen.add(key)
            year_str = row.get("pub_year", "").strip()
            if not year_str:
                counts["Unknown"] += 1
                continue
            year = int(year_str)
            placed = False
            for lo, hi, label in _DECADE_BINS:
                if lo <= year <= hi:
                    counts[label] += 1
                    placed = True
                    break
            if not placed:
                counts["Unknown"] += 1
    return counts


def compute_ieee_journals_by_year(citations: list) -> dict:
    """Fallback for when ieee_citations_review.csv doesn't exist yet: distinct
    IEEE Transactions articles by decade bin, deduplicated by full_reference —
    the compute_ieee_by_year() counterpart to load_ieee_journals_by_year_from_review_csv()."""
    seen: set[str] = set()
    counts = {label: 0 for _, _, label in _DECADE_BINS}
    counts["Unknown"] = 0
    for row in citations:
        if row.get("is_ieee_transactions") != "Yes":
            continue
        ref = row.get("full_reference", "")
        if ref in seen:
            continue
        seen.add(ref)
        year_str = extract_publication_year(ref)
        if not year_str:
            counts["Unknown"] += 1
            continue
        year = int(year_str)
        placed = False
        for lo, hi, label in _DECADE_BINS:
            if lo <= year <= hi:
                counts[label] += 1
                placed = True
                break
        if not placed:
            counts["Unknown"] += 1
    return counts


def load_ieee_years_split_from_review_csv(path: str, split_entity: str = "ESIG") -> dict:
    """
    Like load_ieee_years_from_review_csv(), but instead of a single count per
    decade bin, returns {label: {"split": n, "other": n}} — citations from
    `split_entity` counted separately from everyone else's, so a chart can
    stack them with a distinct color rather than merging them into one count.
    """
    counts = {label: {"split": 0, "other": 0} for _, _, label in _DECADE_BINS}
    counts["Unknown"] = {"split": 0, "other": 0}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                continue
            if not _is_pes_journal(row.get("journal", "").strip() or "Unresolved"):
                continue
            key = "split" if row.get("entity_name", "") == split_entity else "other"
            year_str = row.get("pub_year", "").strip()
            if not year_str:
                counts["Unknown"][key] += 1
                continue
            year = int(year_str)
            placed = False
            for lo, hi, label in _DECADE_BINS:
                if lo <= year <= hi:
                    counts[label][key] += 1
                    placed = True
                    break
            if not placed:
                counts["Unknown"][key] += 1
    return counts


def apply_review_csv_corrections(citations: list, review_path: str) -> list:
    """
    Downgrade is_ieee_transactions for citations the user marked exclude=yes in the review CSV.
    """
    if not os.path.exists(review_path):
        return citations
    excluded_refs: set[str] = set()
    with open(review_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exclude", "").strip().lower() == "yes":
                excluded_refs.add(row.get("full_reference", "").strip())
    if not excluded_refs:
        return citations
    result = []
    for row in citations:
        if row.get("is_ieee_transactions") == "Yes" and row.get("full_reference", "").strip() in excluded_refs:
            row = dict(row)
            row["is_ieee_transactions"] = "No"
        result.append(row)
    return result


def apply_pes_transactions_filter(citations: list, review_path: str, manual_other_names: dict) -> list:
    """
    Narrows the broad "is IEEE Transactions on X for ANY X" flag set by
    extract_citations.py's IEEE_TRANS_RE down to IEEE_PES_JOURNALS only, so
    "IEEE Transactions"/"IEEE PES Transactions" means the same thing
    everywhere: entity/area tables, ieee_journals.csv, every ieee_* figure,
    and publisher_groups' "IEEE PES Transactions" row all agree.

    A citation whose review-CSV-resolved journal name isn't in
    IEEE_PES_JOURNALS (e.g. IEEE Transactions on Big Data — Computer
    Society-sponsored, not PES) is downgraded to is_ieee_transactions="No"
    and its resolved name is added to manual_other_names (mutated in place)
    so it still counts as an identified "IEEE Other" journal citation instead
    of silently disappearing from the journal counts.
    """
    if not os.path.exists(review_path):
        return citations
    journal_by_ref: dict[str, str] = {}
    with open(review_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            journal_by_ref[row.get("full_reference", "").strip()] = row.get("journal", "").strip()

    result = []
    for row in citations:
        if row.get("is_ieee_transactions") == "Yes":
            ref = row.get("full_reference", "").strip()
            journal = journal_by_ref.get(ref, "")
            if journal and not _is_pes_journal(journal):
                row = dict(row)
                row["is_ieee_transactions"] = "No"
                manual_other_names.setdefault(ref, journal)
        result.append(row)
    return result


# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _short_label(filename: str, doc_year: str = "") -> str:
    name = filename.replace(".pdf", "")
    for pfx in ("ESIG-", "v2-ESIG-", "v2-"):
        name = name.replace(pfx, "")
    name = name.replace("-", " ").replace("_", " ")
    m = re.search(r"\b(20\d{2}[a-z]?)\b", name)
    year_tag = f"'{m.group(1)[2:]}".rstrip("abc") if m else (f"'{doc_year[2:]}" if doc_year else "")
    body = re.sub(r"\b20\d{2}[a-z]?\b", "", name).strip()
    words = body.split()
    mid = max(1, len(words) // 2)
    return f"{' '.join(words[:mid])}\n{' '.join(words[mid:])} {year_tag}".strip()


def _stacked_bar_ax(ax, docs_data: list, area_title: str, area_sub: str):
    """
    Draw a vertical stacked bar chart on `ax`.

    docs_data: list of dicts with keys: label, other, other_journals,
    conferences, ieee_transactions
    """

    if not docs_data:
        ax.axis("off")
        ax.text(0.5, 0.5, "No documents", transform=ax.transAxes,
                ha="center", va="center", fontsize=7, color="#888780")
        return

    labels   = [d["label"]              for d in docs_data]
    v_other  = [d["other"]              for d in docs_data]
    v_conf   = [d["conferences"]        for d in docs_data]
    v_jrnl   = [d["other_journals"]     for d in docs_data]
    v_ieee   = [d["ieee_transactions"]  for d in docs_data]
    totals   = [o + c + j + i for o, c, j, i in zip(v_other, v_conf, v_jrnl, v_ieee)]
    x        = list(range(len(labels)))

    ax.bar(x, v_other, color=COLOR_OTHER, zorder=3)
    ax.bar(x, v_conf,  color=COLOR_CONF,  bottom=v_other, zorder=3)
    ax.bar(x, v_jrnl,  color=COLOR_JRNL,
           bottom=[o + c for o, c in zip(v_other, v_conf)], zorder=3)
    ax.bar(x, v_ieee,  color=COLOR_IEEE,
           bottom=[o + c + j for o, c, j in zip(v_other, v_conf, v_jrnl)], zorder=3)

    # ieee/total label on top
    pad = max(totals) * 0.04 if totals else 1
    for xi, (ieee_c, tot) in enumerate(zip(v_ieee, totals)):
        ax.text(xi, tot + pad, f"{ieee_c}/{tot}",
                ha="center", va="bottom", fontsize=7,
                fontweight="bold", color="#2C2C2A")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, ha="center", multialignment="center")
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=4))
    ax.tick_params(axis="y", labelsize=7)
    ax.set_ylim(0, max(totals, default=1) * 1.25)
    ax.set_ylabel("Citations", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

# ─── Plot: 6-panel per entity ─────────────────────────────────────────────────
def plot_entity(entity_name: str, ent_data: dict, out_path: Path):
    fig, axes = plt.subplots(2, 3, figsize=(IEEE_COL2, 4.5),
                             gridspec_kw={"hspace": 0.70, "wspace": 0.38})

    fig.legend(
        handles=[
            mpatches.Patch(facecolor=COLOR_IEEE,  label="IEEE PES Transactions"),
            mpatches.Patch(facecolor=COLOR_JRNL,  label="Other journals"),
            mpatches.Patch(facecolor=COLOR_CONF,  label="Conferences"),
            mpatches.Patch(facecolor=COLOR_OTHER, label="Other"),
        ],
        loc="upper center", ncol=4, frameon=False, fontsize=7,
        bbox_to_anchor=(0.5, 1.010),
    )

    for ax, (area_key, area_title, area_sub) in zip(axes.flat, GPST_AREAS):
        area_data = ent_data["by_gpst_area"].get(area_key, {})
        by_doc    = area_data.get("by_doc", {})

        sorted_docs = sorted(
            by_doc.items(),
            key=lambda x: (x[1]["other"] + x[1]["other_journals"]
                           + x[1].get("conferences", 0) + x[1]["ieee_transactions"]),
            reverse=True,
        )

        # Assign R-index labels (R1, R2, …) in descending citation order
        docs_data = [
            {
                "label":             f"R{i + 1}",
                "other":             d["other"],
                "other_journals":    d["other_journals"],
                "conferences":       d.get("conferences", 0),
                "ieee_transactions": d["ieee_transactions"],
                "_fn":               fn,
                "_title":            d.get("doc_title", fn),
                "_year":             d.get("doc_year", ""),
            }
            for i, (fn, d) in enumerate(sorted_docs)
        ]

        _stacked_bar_ax(ax, docs_data, area_title, area_sub)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: 6-panel all-entities overview ─────────────────────────────────────

def plot_all_entities(stats: dict, out_path: Path):
    """
    One 6-panel figure where each panel shows one G-PST area,
    and bars are grouped by entity (one bar per entity, stacked).
    """


    entities = sorted(stats["by_entity"].keys())

    fig, axes = plt.subplots(2, 3, figsize=(IEEE_COL2, 4.5),
                             gridspec_kw={"hspace": 0.70, "wspace": 0.38})

    fig.legend(
        handles=[
            mpatches.Patch(facecolor=COLOR_IEEE,  label="IEEE PES Transactions"),
            mpatches.Patch(facecolor=COLOR_JRNL,  label="Other journals"),
            mpatches.Patch(facecolor=COLOR_CONF,  label="Conferences"),
            mpatches.Patch(facecolor=COLOR_OTHER, label="Other"),
        ],
        loc="upper center", ncol=4, frameon=False, fontsize=7,
        bbox_to_anchor=(0.5, 1.010),
    )

    for ax, (area_key, area_title, area_sub) in zip(axes.flat, GPST_AREAS):
        docs_data = []
        for ent in entities:
            area = stats["by_entity"][ent]["by_gpst_area"].get(area_key, {})
            tot  = area.get("total", 0)
            if tot == 0:
                continue
            docs_data.append({
                "label":             ent,
                "other":             area.get("other", 0),
                "other_journals":    area.get("other_journals", 0),
                "conferences":       area.get("conferences", 0),
                "ieee_transactions": area.get("ieee_transactions", 0),
            })

        docs_data.sort(key=lambda d: (d["other"] + d["other_journals"]
                                      + d["conferences"] + d["ieee_transactions"]),
                       reverse=True)
        _stacked_bar_ax(ax, docs_data, area_title, area_sub)

    if not os.path.exists(os.path.dirname(out_path)):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: area summary (6 areas + total bar) ─────────────────────────────────

def plot_area_summary(rows: list, out_path):
    """
    7-bar stacked chart: one bar per G-PST area + one total bar.
    Each bar is split into IEEE Transactions / other journals / conferences /
    other, with count and percentage labels inside each segment.
    """
    xlabels  = [r["label"] for r in rows]
    v_ieee   = [r["ieee_transactions"] for r in rows]
    v_jrnl   = [r["other_journals"]    for r in rows]
    v_conf   = [r.get("conferences", 0) for r in rows]
    v_other  = [r["other"]             for r in rows]
    totals   = [r["total"]             for r in rows]
    x        = list(range(len(rows)))
    max_total = max(totals, default=1)

    fig, ax = plt.subplots(figsize=(IEEE_COL2, 3.2))

    ax.bar(x, v_other, color=COLOR_OTHER, zorder=3)
    ax.bar(x, v_conf,  color=COLOR_CONF,  bottom=v_other, zorder=3)
    ax.bar(x, v_jrnl,  color=COLOR_JRNL,
           bottom=[o + c for o, c in zip(v_other, v_conf)], zorder=3)
    ax.bar(x, v_ieee,  color=COLOR_IEEE,
           bottom=[o + c + j for o, c, j in zip(v_other, v_conf, v_jrnl)], zorder=3)

    thresh = max_total * 0.04  # minimum segment height to show a label

    for xi, (io, ic, ij, ii, tot) in enumerate(zip(v_other, v_conf, v_jrnl, v_ieee, totals)):
        if tot == 0:
            continue
        if io >= thresh:
            ax.text(xi, io / 2,
                    f"{io}\n({100 * io / tot:.0f}%)",
                    ha="center", va="center", fontsize=6, color="#555555")
        if ic >= thresh:
            ax.text(xi, io + ic / 2,
                    f"{ic}\n({100 * ic / tot:.0f}%)",
                    ha="center", va="center", fontsize=6, color="#ffffff")
        if ij >= thresh:
            ax.text(xi, io + ic + ij / 2,
                    f"{ij}\n({100 * ij / tot:.0f}%)",
                    ha="center", va="center", fontsize=6, color="#ffffff")
        if ii >= thresh:
            ax.text(xi, io + ic + ij + ii / 2,
                    f"{ii}\n({100 * ii / tot:.0f}%)",
                    ha="center", va="center", fontsize=6,
                    color="#ffffff", fontweight="bold")
        ax.text(xi, tot + max_total * 0.015, f"{ii}/{tot}",
                ha="center", va="bottom", fontsize=7,
                fontweight="bold", color="#2C2C2A")

    # Visual separator before total bar
    ax.axvline(len(rows) - 1.5, color="#AAAAAA", linewidth=0.6, linestyle="--", zorder=2)

    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=8)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=5))
    ax.set_ylim(0, max_total * 1.20)
    ax.set_ylabel("Citations", fontsize=8)
    ax.legend(
        handles=[
            mpatches.Patch(facecolor=COLOR_IEEE,  label="IEEE PES Transactions"),
            mpatches.Patch(facecolor=COLOR_JRNL,  label="Other journals"),
            mpatches.Patch(facecolor=COLOR_CONF,  label="Conferences"),
            mpatches.Patch(facecolor=COLOR_OTHER, label="Other"),
        ],
        loc="upper left", frameon=False, fontsize=7,
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


def plot_area_summary_journals(rows: list, out_path, title: str | None = None):
    """
    7-bar stacked chart: one bar per G-PST area + one total bar, restricted
    to journal-type citations only (the "other" ref-type bucket — reports,
    standards, conferences, etc. — is excluded from both the bars and the
    percentage denominator). Each bar shows the ratio of IEEE Transactions
    to other-journal citations within that area's journal citations.
    """
    xlabels = [r["label"] for r in rows]
    v_ieee  = [r["ieee_transactions"] for r in rows]
    v_jrnl  = [r["other_journals"]    for r in rows]
    totals  = [i + j for i, j in zip(v_ieee, v_jrnl)]
    x       = list(range(len(rows)))
    max_total = max(totals, default=1)

    fig, ax = plt.subplots(figsize=(IEEE_COL2, 4.2))

    ax.bar(x, v_jrnl, color=COLOR_JRNL, zorder=3)
    ax.bar(x, v_ieee, color=COLOR_IEEE, bottom=v_jrnl, zorder=3)

    thresh = max_total * 0.04  # minimum segment height to show a label
    # The IEEE segment sits on top, right below the bold count/total label —
    # if it's a thin sliver of its own bar (e.g. 11 of 105), a 2-line label
    # centered in it collides with that header regardless of overall figure
    # scale, so it needs a stricter threshold than the bottom segment (which
    # has open space below it and no such neighbor).
    thresh_top = max_total * 0.07
    # With only two segments (no third "other" bucket pushing them apart),
    # a short bar can't fit two 2-line inside-segment labels without them
    # overlapping each other. min_room is the gap needed between the two
    # segment-label centers (each ~tot/2 apart) to avoid that.
    min_room = max_total * 0.122

    for xi, (ij, ii, tot) in enumerate(zip(v_jrnl, v_ieee, totals)):
        if tot == 0:
            continue
        show_ij, show_ii = ij >= thresh, ii >= thresh_top
        if show_ij and show_ii and tot < min_room:
            # Not enough vertical room for both — keep only the larger segment's label.
            if ij >= ii:
                show_ii = False
            else:
                show_ij = False
        if show_ij:
            ax.text(xi, ij / 2,
                    f"{ij}\n({100 * ij / tot:.0f}%)",
                    ha="center", va="center", fontsize=6, color="#ffffff")
        if show_ii:
            ax.text(xi, ij + ii / 2,
                    f"{ii}\n({100 * ii / tot:.0f}%)",
                    ha="center", va="center", fontsize=6,
                    color="#ffffff", fontweight="bold")
        ax.text(xi, tot + max_total * 0.045, f"{ii}/{tot}",
                ha="center", va="bottom", fontsize=7,
                fontweight="bold", color="#2C2C2A")

    # Visual separator before total bar
    ax.axvline(len(rows) - 1.5, color="#AAAAAA", linewidth=0.6, linestyle="--", zorder=2)

    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=8)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=5))
    ax.set_ylim(0, max_total * 1.30)
    ax.set_ylabel("Journal citations", fontsize=8)
    if title:
        ax.set_title(title, fontsize=9, fontweight="bold", pad=10)
    ax.legend(
        handles=[
            mpatches.Patch(facecolor=COLOR_IEEE, label="IEEE PES Transactions"),
            mpatches.Patch(facecolor=COLOR_JRNL, label="Other journals"),
        ],
        loc="upper left", frameon=False, fontsize=7,
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: IEEE Transactions journals ─────────────────────────────────────────
# (IEEE_PES_JOURNALS / _is_pes_journal are defined earlier in this file,
# alongside ieee_publisher_subgroup(), which shares the same definition.)

def plot_ieee_journals(journal_counts: Counter, out_path):
    """Horizontal bar chart of IEEE PES Transactions citations by journal
    name. Every entry here is already IEEE_PES_JOURNALS-only (see
    apply_pes_transactions_filter()); the per-bar PES check below is just a
    defensive fallback in case a non-PES name ever leaks through.
    """
    if not journal_counts:
        print("  [skip] No IEEE PES Transactions citations found.")
        return

    items  = journal_counts.most_common()
    labels = [IEEE_TRANS_ABBREV.get(name, name) for name, _ in items]
    values = [count for _,    count in items]
    total  = sum(values)
    colors = [COLOR_IEEE if _is_pes_journal(name) else COLOR_JRNL for name, _ in items]

    fig_h = max(2.0, len(labels) * 0.30 + 0.9)
    fig, ax = plt.subplots(figsize=(IEEE_COL2, fig_h))

    y = list(range(len(labels)))
    ax.barh(y, values, color=colors, zorder=3, height=0.6)

    for yi, (v, col) in enumerate(zip(values, colors)):
        ax.text(v + total * 0.015, yi,
                f"{v}  ({100 * v / total:.1f}%)",
                va="center", ha="left", fontsize=7, color="#2C2C2A")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Citations", fontsize=8)
    ax.set_xlim(0, max(values, default=1) * 1.35)
    ax.invert_yaxis()

    legend_handles = [mpatches.Patch(facecolor=COLOR_IEEE, label="IEEE PES Transactions")]
    if COLOR_JRNL in colors:  # defensive fallback path only — see docstring
        legend_handles.append(mpatches.Patch(facecolor=COLOR_JRNL, label="Non-PES (unexpected)"))
    fig.legend(
        handles=legend_handles,
        loc="lower right", frameon=False, fontsize=7,
        bbox_to_anchor=(0.99, 0.12),
    )

    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: IEEE citations by G-PST area ───────────────────────────────────────

def plot_ieee_by_area(area_rows: list, out_path):
    """Horizontal bar chart of IEEE Transactions citations by G-PST research area."""
    rows   = [r for r in area_rows if r["area"] != "total"]
    labels = [r["label"] for r in rows]
    values = [r["ieee_transactions"] for r in rows]
    total  = sum(values) or 1

    fig_h = max(1.8, len(labels) * 0.35 + 0.8)
    fig, ax = plt.subplots(figsize=(IEEE_COL1, fig_h))

    y = list(range(len(labels)))
    ax.barh(y, values, color=COLOR_IEEE, zorder=3, height=0.6)

    for yi, v in enumerate(values):
        ax.text(v + total * 0.015, yi,
                f"{v}  ({100 * v / total:.1f}%)",
                va="center", ha="left", fontsize=5.5, color="#2C2C2A")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Citations", fontsize=8)
    ax.set_xlim(0, max(values, default=1) * 1.40)
    ax.invert_yaxis()

    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(axis="x", color="#E8E6DF", linewidth=0.4, zorder=0)

    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: IEEE citations by decade ───────────────────────────────────────────

def compute_ieee_by_year(citations: list) -> dict:
    """Count IEEE Transactions citations by decade bin using pub_year."""
    counts = {label: 0 for _, _, label in _DECADE_BINS}
    counts["Unknown"] = 0
    for row in citations:
        if row.get("is_ieee_transactions") != "Yes":
            continue
        year_str = extract_publication_year(row.get("full_reference", ""))
        if not year_str:
            counts["Unknown"] += 1
            continue
        year = int(year_str)
        placed = False
        for lo, hi, label in _DECADE_BINS:
            if lo <= year <= hi:
                counts[label] += 1
                placed = True
                break
        if not placed:
            counts["Unknown"] += 1
    return counts


def compute_docs_by_year(citations: list) -> dict:
    """
    Count DISTINCT citing DOCUMENTS (not citations) per decade bin, using
    each document's doc_year — the documents-themselves counterpart to
    compute_ieee_by_year()'s citations-by-decade. A document contributes
    once regardless of how many citations it has.
    """
    doc_years: dict[str, str] = {}
    for c in citations:
        fn = c.get("pdf_filename", "")
        if fn and fn not in doc_years:
            doc_years[fn] = c.get("doc_year", "").strip()

    counts = {label: 0 for _, _, label in _DECADE_BINS}
    counts["Unknown"] = 0
    for year_str in doc_years.values():
        if not year_str:
            counts["Unknown"] += 1
            continue
        try:
            year = int(year_str)
        except ValueError:
            counts["Unknown"] += 1
            continue
        placed = False
        for lo, hi, label in _DECADE_BINS:
            if lo <= year <= hi:
                counts[label] += 1
                placed = True
                break
        if not placed:
            counts["Unknown"] += 1
    return counts


def plot_docs_by_year(year_counts: dict, out_path, title: str | None = None):
    """Vertical bar chart of citing DOCUMENTS (not citations) by decade bin."""
    labels = [label for _, _, label in _DECADE_BINS]
    if year_counts.get("Unknown", 0) > 0:
        labels.append("Unknown")
    values = [year_counts.get(lbl, 0) for lbl in labels]
    total  = sum(values) or 1

    fig, ax = plt.subplots(figsize=(IEEE_COL1, 2.5))
    x = list(range(len(labels)))
    ax.bar(x, values, color=COLOR_JRNL, zorder=3, width=0.6)
    for xi, v in enumerate(values):
        if v == 0:
            continue
        ax.text(xi, v + total * 0.02, f"{v}\n({100 * v / total:.1f}%)",
                ha="center", va="bottom", fontsize=5, color="#2C2C2A")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=30, ha="right")
    ax.set_ylabel("Documents", fontsize=8)
    ax.set_ylim(0, max(values, default=1) * 1.35)
    if title:
        ax.set_title(title, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


def plot_ieee_by_year(year_counts: dict, out_path, journal_counts: dict | None = None):
    """
    IEEE citations by decade bin. When `journal_counts` (from
    load_ieee_journals_by_year_from_review_csv() / compute_ieee_journals_by_year())
    is given, draws a dual-axis chart: distinct journal articles as bars
    (left axis) and total citation volume as a line (right axis) — the same
    paper cited more than once inflates the line above the bars. Without
    `journal_counts`, falls back to the original single bar chart of
    citation counts only.
    """
    labels = [label for _, _, label in _DECADE_BINS]
    if year_counts.get("Unknown", 0) > 0 or (journal_counts and journal_counts.get("Unknown", 0) > 0):
        labels.append("Unknown")
    values = [year_counts.get(lbl, 0) for lbl in labels]
    total  = sum(values) or 1

    if journal_counts is None:
        fig, ax = plt.subplots(figsize=(IEEE_COL1, 2.5))

        x = list(range(len(labels)))
        ax.bar(x, values, color=COLOR_IEEE, zorder=3, width=0.6)

        for xi, v in enumerate(values):
            if v == 0:
                continue
            ax.text(xi, v + total * 0.02, f"{v}\n({100 * v / total:.1f}%)",
                    ha="center", va="bottom", fontsize=5, color="#2C2C2A")

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=30, ha="right")
        ax.set_ylabel("Citations", fontsize=8)
        ax.set_ylim(0, max(values, default=1) * 1.35)

        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color("#D3D1C7")
        ax.tick_params(colors="#888780")
        ax.grid(False)

        fig.tight_layout()
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
        plt.close(fig)
        print(f"  [ok] {out_path}")
        return

    jvalues = [journal_counts.get(lbl, 0) for lbl in labels]

    fig, ax = plt.subplots(figsize=(IEEE_COL1, 2.5))
    ax2 = ax.twinx()

    x = list(range(len(labels)))
    bars = ax.bar(x, jvalues, color=COLOR_JRNL, zorder=2, width=0.6, label="Journal articles")
    line, = ax2.plot(x, values, color=COLOR_IEEE, marker="o", markersize=3.5,
                     linewidth=1.4, zorder=3, label="Citations")

    # Both series can land on the same pixel height (e.g. when no paper in a
    # bin is cited more than once, bar value == line value everywhere) — use
    # a fixed point-offset stack (not data-space offsets, which coincide
    # exactly when the values match) so the two labels never overlap.
    for xi, v in enumerate(jvalues):
        if v == 0:
            continue
        ax.annotate(f"{v}", (xi, v), textcoords="offset points", xytext=(0, 2),
                    ha="center", va="bottom", fontsize=5.5, color="#2C2C2A")
    for xi, v in enumerate(values):
        ax2.annotate(f"{v}", (xi, v), textcoords="offset points", xytext=(0, 11),
                     ha="center", va="bottom", fontsize=5.5, color=COLOR_IEEE)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=30, ha="right")
    ax.set_ylabel("Journal articles", fontsize=8, color=COLOR_JRNL)
    ax2.set_ylabel("Citations", fontsize=8, color=COLOR_IEEE)
    ax.set_ylim(0, max(jvalues, default=1) * 1.35)
    ax2.set_ylim(0, max(values, default=1) * 1.35)

    ax.spines[["top"]].set_visible(False)
    ax2.spines[["top"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax2.spines[["right"]].set_color("#D3D1C7")
    ax.tick_params(axis="y", colors=COLOR_JRNL)
    ax2.tick_params(axis="y", colors=COLOR_IEEE)
    ax.tick_params(axis="x", colors="#888780")
    ax.grid(False)

    ax.legend(handles=[bars, line], loc="upper left", fontsize=6, frameon=False)

    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


def plot_ieee_by_year_split(year_counts_split: dict, out_path, split_entity: str = "ESIG"):
    """
    Vertical stacked bar chart of IEEE Transactions citations by decade bin,
    split into `split_entity`'s citations (top segment) vs. everyone else's
    (bottom segment), so its contribution to each decade is visible rather
    than folded into one undifferentiated count.
    """
    labels = [label for _, _, label in _DECADE_BINS]
    if year_counts_split.get("Unknown", {}).get("split", 0) or year_counts_split.get("Unknown", {}).get("other", 0):
        labels.append("Unknown")
    v_other = [year_counts_split.get(lbl, {}).get("other", 0) for lbl in labels]
    v_split = [year_counts_split.get(lbl, {}).get("split", 0) for lbl in labels]
    totals  = [o + s for o, s in zip(v_other, v_split)]
    total_all = sum(totals) or 1

    fig, ax = plt.subplots(figsize=(IEEE_COL1, 2.5))

    x = list(range(len(labels)))
    ax.bar(x, v_other, color=COLOR_JRNL, zorder=3, width=0.6)
    ax.bar(x, v_split, color=COLOR_IEEE, bottom=v_other, zorder=3, width=0.6)

    for xi, (o, s, tot) in enumerate(zip(v_other, v_split, totals)):
        if tot == 0:
            continue
        ax.text(xi, tot + total_all * 0.02, f"{tot}\n({100 * tot / total_all:.1f}%)",
                ha="center", va="bottom", fontsize=5, color="#2C2C2A")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=30, ha="right")
    ax.set_ylabel("Citations", fontsize=8)
    ax.set_ylim(0, max(totals, default=1) * 1.35)

    ax.legend(
        handles=[
            mpatches.Patch(facecolor=COLOR_IEEE, label=split_entity),
            mpatches.Patch(facecolor=COLOR_JRNL, label=f"Other than {split_entity}"),
        ],
        loc="upper left", frameon=False, fontsize=6,
    )

    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


# ─── Plot: overall citation shares (donut) ────────────────────────────────────

def _single_stacked_barh(labels: list, values: list, colors: list, xlabel: str, out_path,
                         title: str | None = None):
    """
    Single 100%-stacked horizontal bar: one segment per (label, value, color),
    with count + percentage labeled inside each segment wide enough to hold it
    (else above the bar), and a swatch legend above.
    """
    total = sum(values) or 1

    fig, ax = plt.subplots(figsize=(IEEE_COL2, 1.5))

    left = 0.0
    thresh = total * 0.05
    for v, c, lbl in zip(values, colors, labels):
        ax.barh(0, v, left=left, color=c, height=0.5, zorder=3)
        pct = 100 * v / total
        text = f"{v} ({pct:.1f}%)"
        if v >= thresh:
            ax.text(left + v / 2, 0, text, ha="center", va="center",
                    fontsize=7, fontweight="bold",
                    color="white" if c != COLOR_OTHER else "#2C2C2A")
        else:
            ax.text(left + v / 2, 0.42, text, ha="center", va="bottom",
                    fontsize=6, color="#2C2C2A")
        left += v

    ax.set_xlim(0, total)
    ax.set_ylim(-0.55, 0.75)
    ax.set_yticks([])
    ax.set_xlabel(xlabel, fontsize=8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#D3D1C7")
    ax.tick_params(colors="#888780")
    ax.grid(False)

    fig.legend(
        handles=[mpatches.Patch(facecolor=c, label=lbl) for c, lbl in zip(colors, labels)],
        loc="upper center", ncol=len(labels), frameon=False, fontsize=7,
        bbox_to_anchor=(0.5, 1.16),
    )
    if title:
        ax.set_title(title, fontsize=9, fontweight="bold", pad=26)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=IEEE_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok] {out_path}")


def plot_citation_shares(grand: dict, out_path, title: str | None = None):
    """
    Single 100%-stacked bar of overall citation composition across the whole
    corpus: IEEE PES Transactions / other journals / conferences / other
    reference types. `grand` is the "total" row from compute_area_summary()
    (or any dict with the same ieee_transactions / other_journals /
    conferences / other / total keys).
    """
    _single_stacked_barh(
        labels=["IEEE PES Transactions", "Other journals", "Conferences", "Other"],
        values=[grand["ieee_transactions"], grand["other_journals"],
                grand.get("conferences", 0), grand["other"]],
        colors=[COLOR_IEEE, COLOR_JRNL, COLOR_CONF, COLOR_OTHER],
        xlabel="Citations", out_path=out_path, title=title,
    )


# ─── Plot: IEEE PES Transactions' share of journal citations (stacked bar) ──

def plot_journal_ieee_share(grand: dict, out_path, title: str | None = None):
    """
    Single 100%-stacked bar restricted to journal-type citations only (IEEE
    PES Transactions + other journals — "other" ref types like reports/
    standards/conferences are excluded from the denominator here), showing
    what share of journal citations specifically are to IEEE PES Transactions.
    """
    _single_stacked_barh(
        labels=["IEEE PES Transactions", "Other journals"],
        values=[grand["ieee_transactions"], grand["other_journals"]],
        colors=[COLOR_IEEE, COLOR_JRNL],
        xlabel="Journal citations", out_path=out_path, title=title,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Analyze citations from fetch_documents.py output")
    p.add_argument("--entities", nargs="+", metavar="NAME",
                   help="Limit to these entity display names (e.g. ESIG NERC). Default: all.")
    p.add_argument("--figures-only", action="store_true",
                   help="Regenerate figures without rewriting JSON stats.")
    p.add_argument("--no-figures", action="store_true",
                   help="Write JSON stats only, skip figures.")
    p.add_argument("--comparison-entity", metavar="NAME", default="ESIG",
                   help="Entity to build the with/without comparison table and "
                        "figure variants against (default: ESIG). Pass '' to skip.")
    return p.parse_args()


def write_documents_with_citations_csv(citations: list, index: dict, path: str,
                                       tex_path: str | None = None,
                                       label: str = "tab:documents_with_citations",
                                       caption_suffix: str = ""):
    """
    One row per citing DOCUMENT (not per citation) — entity, title, research
    area, citation count (broken down by IEEE PES Transactions / other
    journals / conferences / other), publication year, and URL. Companion to
    write_journal_citations_by_document_csv() (per-citation, journal-only
    rows); this is per-document, across all ref types (journal, conference,
    report, etc.) — for reviewing exactly which documents in a scope
    contribute citations, and how many each.
    """
    per_doc: dict[str, Counter] = {}
    for c in citations:
        fn = c["pdf_filename"]
        bucket = per_doc.setdefault(fn, Counter())
        if c.get("is_ieee_transactions") == "Yes":
            bucket["ieee_pes_transactions"] += 1
        elif c.get("ref_type") == "journal":
            bucket["other_journals"] += 1
        elif c.get("ref_type") == "conference":
            bucket["conferences"] += 1
        else:
            bucket["other"] += 1

    rows = []
    for fn, bucket in per_doc.items():
        idx_row = index.get(fn, {})
        first_area = idx_row.get("gpst_area_validation", "").split(",")[0].strip().lower()
        area_label = AREA_TITLES.get(first_area, idx_row.get("gpst_area_validation", "") or "(unresolved)")
        n = sum(bucket.values())
        rows.append({
            "entity_name":          idx_row.get("entity_name", ""),
            "pdf_filename":         fn,
            "doc_title":            idx_row.get("doc_title", ""),
            "doc_year":             idx_row.get("doc_year", ""),
            "research_area":        area_label,
            "num_citations":        n,
            "ieee_pes_transactions": bucket.get("ieee_pes_transactions", 0),
            "other_journals":       bucket.get("other_journals", 0),
            "conferences":          bucket.get("conferences", 0),
            "other":                bucket.get("other", 0),
            "pdf_url":              idx_row.get("pdf_url", ""),
        })
    rows.sort(key=lambda r: (r["entity_name"], -r["num_citations"]))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["entity_name", "pdf_filename", "doc_title", "doc_year",
              "research_area", "num_citations", "ieee_pes_transactions",
              "other_journals", "conferences", "other", "pdf_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  [ok] {path}  ({len(rows)} documents, {sum(r['num_citations'] for r in rows)} citations)")

    if tex_path:
        _write_documents_with_citations_tex(rows, tex_path, label=label,
                                            caption_suffix=caption_suffix)


def _write_documents_with_citations_tex(rows: list, tex_path: str,
                                        label: str = "tab:documents_with_citations",
                                        caption_suffix: str = ""):
    """LaTeX longtable version of write_documents_with_citations_csv()'s rows."""
    def esc(s):
        return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")

    lines = [
        r"% Requires \usepackage{longtable} and \usepackage{booktabs} in the",
        r"% including document. longtable needs a single-column layout to",
        r"% paginate itself, so in a twocolumn class (e.g. \documentclass",
        r"% [conference]{IEEEtran}) wrap this \input with \onecolumn ... ",
        r"% \twocolumn, e.g.:",
        r"%   \onecolumn",
        r"%   \input{system_operators_documents_with_citations}",
        r"%   \twocolumn",
        r"% Omitting that wrapper in a twocolumn document raises",
        r"% \"Package longtable Error: longtable not in -1 column mode\".",
        r"% \small and \arraystretch are scoped to the {...} group below so",
        r"% they don't leak into the rest of the including document.",
        r"{\small",
        r"\renewcommand{\arraystretch}{1.0}",
        r"\begin{longtable}{c l p{5.5cm} l c c}",
        r"\caption{Documents with citations" + caption_suffix + r".}",
        rf"\label{{{label}}}\\",
        r"\toprule",
        r"No. & Entity & Document Title & Research Area & Citations & Year \\",
        r"\midrule",
        r"\endhead",
    ]
    total_cites = 0
    for i, r in enumerate(rows):
        total_cites += r["num_citations"]
        lines.append(
            f"{i + 1} & {esc(r['entity_name'])} & {esc(r['doc_title'])} & "
            f"{esc(r['research_area'])} & {r['num_citations']} & {esc(r['doc_year'])} \\\\"
        )
    lines += [
        r"\midrule",
        f"    \\multicolumn{{4}}{{r}}{{\\textbf{{Total}}}} & \\textbf{{{total_cites}}} & \\\\",
        r"\bottomrule",
        r"\end{longtable}",
        r"}",
    ]
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [ok] {tex_path}")


def _write_documents_with_citations_tex_from_csv(csv_path: str, tex_path: str,
                                                  label: str = "tab:documents_with_citations",
                                                  caption_suffix: str = ""):
    """
    Same rendering as _write_documents_with_citations_tex(), but reads its
    rows directly from an on-disk *_documents_with_citations.csv instead of
    freshly-computed citations — used once that CSV is human-curated ground
    truth (hand-corrected research areas / citation-type breakdown) that
    must never be silently regenerated over.
    """
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["num_citations"] = int(r.get("num_citations", 0) or 0)
    _write_documents_with_citations_tex(rows, tex_path, label=label,
                                        caption_suffix=caption_suffix)


def build_inclusion_scope_outputs(scope_slug: str, scope_label: str,
                                  index: dict, citations: list, stats: dict,
                                  publishers: dict, ieee_names: dict,
                                  manual_other_names: dict, review_path: str,
                                  include_predicate, args):
    """
    Write the full '_no_esig'-style output family (area summary, entity
    summary, journal/publisher tables, journal-citations-by-document,
    unresolved-journal review file, and the full figure set in PNG + TikZ)
    for an arbitrary entity INCLUSION filter, suffixed with scope_slug.

    include_predicate(entity_name) -> bool decides membership in the scope.
    Unlike the with/without-{comp_entity} block, this is a general subset
    (e.g. "system operators only") rather than a single-entity exclusion, so
    no with/without comparison tables are produced here — just the scoped
    outputs themselves, mirroring what the _no_esig files provide.
    """
    index_s     = {fn: r for fn, r in index.items() if include_predicate(r["entity_name"])}
    citations_s = [c for c in citations if include_predicate(c["entity_name"])]
    if not citations_s:
        print(f"    Skipping {scope_label} — no matching citations.")
        return

    stats_s     = compute_stats(index_s, citations_s)
    area_rows_s = compute_area_summary(stats_s)
    grand_s     = area_rows_s[-1]

    for e in sorted(stats_s["by_entity"]):
        d = stats_s["by_entity"][e]
        print(f"    ({scope_label}) {e:<28}  docs={d['total_docs']:3d}  "
              f"cites={d['total_citations']:4d}  ieee={d['ieee_transactions']:3d}")

    # Entities present overall but excluded from this scope — needed so the
    # review-CSV-based journal/year loaders skip their rows the same way the
    # with/without-ESIG block does via load_ieee_journals_from_review_csv's
    # exclude_entities set.
    excluded_entities = {e for e in stats["by_entity"] if e not in stats_s["by_entity"]}

    if not args.figures_only:
        write_area_summary_csv(area_rows_s,
                               os.path.join(STATS_DIR, f"area_summary_{scope_slug}.csv"))
        write_latex_table(
            stats_s, os.path.join(STATS_DIR, f"entity_summary_{scope_slug}.tex"),
            label=f"tab:entity_summary_{scope_slug}",
            caption_suffix=f", {scope_label}",
            csv_path=os.path.join(STATS_DIR, f"entity_summary_{scope_slug}.csv"),
        )

        if os.path.exists(review_path):
            ieee_journals_s = load_ieee_journals_from_review_csv(review_path, excluded_entities)
        else:
            ieee_journals_s = compute_ieee_journals(citations_s)
        other_journals_s = compute_other_journals(citations_s, manual_other_names)

        write_unresolved_other_journals_csv(
            citations_s,
            os.path.join(STATS_DIR, f"unresolved_other_journals_{scope_slug}.csv"),
            manual_names=manual_other_names,
        )
        write_journal_publishers_csv(
            ieee_journals_s, other_journals_s, publishers,
            os.path.join(STATS_DIR, f"journal_publishers_{scope_slug}.csv"),
            tex_path=os.path.join(STATS_DIR, f"journal_publishers_{scope_slug}.tex"),
            label=f"tab:journal_publishers_{scope_slug}",
            caption_suffix=f", {scope_label}",
        )
        write_publisher_groups(
            ieee_journals_s, other_journals_s, publishers,
            os.path.join(STATS_DIR, f"publisher_groups_{scope_slug}.csv"),
            os.path.join(STATS_DIR, f"publisher_groups_{scope_slug}.tex"),
            label=f"tab:publisher_groups_{scope_slug}",
            caption_suffix=f", {scope_label}",
        )
        jcbd_csv_path = os.path.join(STATS_DIR, f"journal_citations_by_document_{scope_slug}.csv")
        if os.path.exists(jcbd_csv_path):
            # Human-curated ground truth (validated_research_area, "document
            # title" columns added by hand) — never auto-regenerated once it
            # exists, so manual review/edits are never silently clobbered.
            print(f"    (kept as-is: {jcbd_csv_path} — manually curated)")
        else:
            write_journal_citations_by_document_csv(
                citations_s, ieee_names, manual_other_names, publishers,
                jcbd_csv_path,
            )
        _write_journal_citations_by_document_tex_from_csv(
            jcbd_csv_path,
            os.path.join(STATS_DIR, f"journal_citations_by_document_{scope_slug}.tex"),
            label=f"tab:journal_citations_by_document_{scope_slug}",
            caption_suffix=f", {scope_label}",
        )
        dwc_csv_path = os.path.join(STATS_DIR, f"{scope_slug}_documents_with_citations.csv")
        if os.path.exists(dwc_csv_path):
            # Human-curated ground truth (hand-corrected research areas
            # and/or citation-type breakdown) — never auto-regenerated once
            # it exists, so manual review/edits are never silently clobbered.
            print(f"    (kept as-is: {dwc_csv_path} — manually curated)")
        else:
            write_documents_with_citations_csv(citations_s, index_s, dwc_csv_path)
        _write_documents_with_citations_tex_from_csv(
            dwc_csv_path,
            os.path.join(STATS_DIR, f"{scope_slug}_documents_with_citations.tex"),
            label=f"tab:documents_with_citations_{scope_slug}",
            caption_suffix=f", {scope_label}",
        )

    if not args.no_figures:
        title = scope_label[:1].upper() + scope_label[1:]

        plot_all_entities(stats_s, os.path.join(FIGURES_DIR, f"gpst_all_entities_{scope_slug}.png"))
        tikz_export.write_tikz_all_entities(
            stats_s, AREA_KEYS, AREA_TITLES,
            os.path.join(FIGURES_DIR, f"gpst_all_entities_{scope_slug}.tex"), title=title)

        plot_area_summary(area_rows_s, os.path.join(FIGURES_DIR, f"gpst_area_summary_{scope_slug}.png"))
        tikz_export.write_tikz_area_summary(
            area_rows_s, os.path.join(FIGURES_DIR, f"gpst_area_summary_{scope_slug}.tex"), title=title)

        plot_area_summary_journals(
            area_rows_s, os.path.join(FIGURES_DIR, f"gpst_area_summary_journals_{scope_slug}.png"),
            title=title)
        tikz_export.write_tikz_area_summary_journals(
            area_rows_s, os.path.join(FIGURES_DIR, f"gpst_area_summary_journals_{scope_slug}.tex"),
            title=title)

        plot_ieee_by_area(area_rows_s, os.path.join(FIGURES_DIR, f"gpst_ieee_by_area_{scope_slug}.png"))
        tikz_export.write_tikz_ieee_by_area(
            area_rows_s, os.path.join(FIGURES_DIR, f"gpst_ieee_by_area_{scope_slug}.tex"), title=title)

        plot_citation_shares(grand_s, os.path.join(FIGURES_DIR, f"gpst_citation_shares_{scope_slug}.png"),
                             title=title)
        tikz_export.write_tikz_citation_shares(
            grand_s, os.path.join(FIGURES_DIR, f"gpst_citation_shares_{scope_slug}.tex"), title=title)

        plot_journal_ieee_share(
            grand_s, os.path.join(FIGURES_DIR, f"gpst_journal_ieee_share_{scope_slug}.png"), title=title)
        tikz_export.write_tikz_journal_ieee_share(
            grand_s, os.path.join(FIGURES_DIR, f"gpst_journal_ieee_share_{scope_slug}.tex"), title=title)

        if os.path.exists(review_path):
            ieee_journals_fig_s = load_ieee_journals_from_review_csv(review_path, excluded_entities)
            ieee_by_year_s      = load_ieee_years_from_review_csv(review_path, excluded_entities)
            ieee_articles_by_year_s = load_ieee_journals_by_year_from_review_csv(review_path, excluded_entities)
        else:
            ieee_journals_fig_s = compute_ieee_journals(citations_s)
            ieee_by_year_s      = compute_ieee_by_year(citations_s)
            ieee_articles_by_year_s = compute_ieee_journals_by_year(citations_s)

        plot_ieee_journals(ieee_journals_fig_s,
                           os.path.join(FIGURES_DIR, f"gpst_ieee_journals_{scope_slug}.png"))
        tikz_export.write_tikz_ieee_journals(
            ieee_journals_fig_s, os.path.join(FIGURES_DIR, f"gpst_ieee_journals_{scope_slug}.tex"),
            title=title)

        plot_ieee_by_year(ieee_by_year_s,
                          os.path.join(FIGURES_DIR, f"gpst_ieee_by_year_{scope_slug}.png"),
                          journal_counts=ieee_articles_by_year_s)
        tikz_export.write_tikz_ieee_by_year(
            ieee_by_year_s, os.path.join(FIGURES_DIR, f"gpst_ieee_by_year_{scope_slug}.tex"), title=title,
            journal_counts=ieee_articles_by_year_s)

        docs_by_year_s = compute_docs_by_year(citations_s)
        plot_docs_by_year(docs_by_year_s,
                          os.path.join(FIGURES_DIR, f"gpst_docs_by_year_{scope_slug}.png"), title=title)
        tikz_export.write_tikz_docs_by_year(
            docs_by_year_s, os.path.join(FIGURES_DIR, f"gpst_docs_by_year_{scope_slug}.tex"), title=title)


def main():
    args = parse_args()

    print("=" * 60)
    print("Citation Analyzer")
    print("=" * 60)

    _set_ieee_style()

    # 1 — Load
    print("\n[1] Loading data ...")
    # entity_filter = args.entities
    entity_filter = None
    index     = load_index(entity_filter=entity_filter)
    citations = load_citations(index)
    citations, n_dupes = deduplicate_citations(citations)
    print(f"    {len(index)} documents  |  {len(citations)} citations  ({n_dupes} duplicates removed by article URL)")

    # Apply manually-reviewed corrections from completed
    # unresolved_other_journals*.csv files: drops non-citation "remove" rows,
    # reclassifies misfiled IEEE Transactions / conference citations, and
    # surfaces manually-identified other-journal names — globally, before
    # any stats/figures are computed, so it ripples through everything
    # (total_citations, area breakdowns, entity tables, all figures).
    corrections = load_citation_corrections([CITATION_CORRECTIONS_CSV])
    citations, manual_ieee_names, manual_other_names = apply_citation_corrections(citations, corrections)

    if not index:
        print("  Nothing to analyse. Check document_index.csv and --entities filter.")
        return

    # 2 — Compute stats
    print("\n[2] Computing statistics ...")
    review_path = os.path.join(STATS_DIR, "ieee_citations_review.csv")

    # Defaults for --figures-only runs, where section 3 below (which
    # normally populates these) is skipped entirely.
    publishers  = {}
    ieee_names  = {}

    # 3 — Write stats
    if not args.figures_only:
        print("\n[3] Writing stats ...")
        # Write / update the review file first so corrections apply to stats
        write_ieee_citations_review_csv(citations, review_path, manual_names=manual_ieee_names)

    # Apply user deletions from review CSV before computing stats/figures
    citations = apply_review_csv_corrections(citations, review_path)

    # Narrow "IEEE Transactions on X" (any X) down to IEEE PES Transactions
    # only (IEEE_PES_JOURNALS) — the canonical definition used everywhere
    # downstream. Non-PES IEEE Transactions titles (e.g. Big Data,
    # Visualization and Computer Graphics) are folded into manual_other_names
    # so they still count as an identified "IEEE Other" journal citation.
    citations = apply_pes_transactions_filter(citations, review_path, manual_other_names)

    # Recompute stats with corrected IEEE flags
    stats = compute_stats(index, citations)
    entities = sorted(stats["by_entity"].keys())
    for ent in entities:
        e = stats["by_entity"][ent]
        print(f"    {ent:<28}  docs={e['total_docs']:3d}  "
              f"cites={e['total_citations']:4d}  "
              f"ieee={e['ieee_transactions']:3d}")

    area_rows = compute_area_summary(stats)

    if not args.figures_only:
        write_stats(stats)
        write_area_summary_csv(area_rows, os.path.join(STATS_DIR, "area_summary.csv"))

    # Build journal counts from the review CSV if it exists, else fall back to regex
    if os.path.exists(review_path):
        ieee_journals = load_ieee_journals_from_review_csv(review_path)
    else:
        ieee_journals = compute_ieee_journals(citations)

    if not args.figures_only:
        write_ieee_journals_csv(ieee_journals, os.path.join(STATS_DIR, "ieee_journals.csv"))

        # Journal -> publisher lookup: per-journal table + grouped-by-publisher
        # rollup (IEEE Transactions journals + identified "other journal" names).
        other_journals = compute_other_journals(citations, manual_other_names)
        publishers = load_journal_publishers(JOURNAL_PUBLISHERS_CSV)
        write_unresolved_other_journals_csv(
            citations,
            os.path.join(STATS_DIR, "unresolved_other_journals.csv"),
            manual_names=manual_other_names,
        )
        write_journal_publishers_csv(
            ieee_journals, other_journals, publishers,
            os.path.join(STATS_DIR, "journal_publishers.csv"),
            tex_path=os.path.join(STATS_DIR, "journal_publishers.tex"),
        )
        write_publisher_groups(
            ieee_journals, other_journals, publishers,
            os.path.join(STATS_DIR, "publisher_groups.csv"),
            os.path.join(STATS_DIR, "publisher_groups.tex"),
        )
        ieee_names = load_ieee_journal_names_from_review_csv(review_path)
        write_journal_citations_by_document_csv(
            citations, ieee_names, manual_other_names, publishers,
            os.path.join(STATS_DIR, "journal_citations_by_document.csv"),
        )

        write_latex_table(stats, os.path.join(STATS_DIR, "entity_summary.tex"))

        # Complete version: every curated entity gets a row, even ones with
        # zero citations at all (compute_stats only populates an entity once
        # it sees at least one citation, so those would otherwise be silently
        # absent from entity_summary.tex above).
        all_curated_entities = sorted({r["entity_name"] for r in index.values()})
        write_latex_table(
            stats, os.path.join(STATS_DIR, "entity_summary_all.tex"),
            label="tab:entity_summary_all",
            caption_suffix=", including entities with zero citations",
            all_entities=all_curated_entities, index=index,
            csv_path=os.path.join(STATS_DIR, "entity_summary_all.csv"),
        )

    # 4 — Figures
    if not args.no_figures:
        print("\n[4] Generating figures ...")

        # All-entities overview (6-panel, one per area)
        plot_all_entities(stats, os.path.join(FIGURES_DIR, "gpst_all_entities.png"))
        tikz_export.write_tikz_all_entities(
            stats, AREA_KEYS, AREA_TITLES, os.path.join(FIGURES_DIR, "gpst_all_entities.tex"))

        # Area summary: 6 areas + total bar
        plot_area_summary(area_rows, os.path.join(FIGURES_DIR, "gpst_area_summary.png"))
        tikz_export.write_tikz_area_summary(area_rows, os.path.join(FIGURES_DIR, "gpst_area_summary.tex"))

        # Area summary, journal citations only: IEEE Transactions vs other journals
        plot_area_summary_journals(area_rows, os.path.join(FIGURES_DIR, "gpst_area_summary_journals.png"))
        tikz_export.write_tikz_area_summary_journals(
            area_rows, os.path.join(FIGURES_DIR, "gpst_area_summary_journals.tex"))

        # IEEE Transactions journal breakdown
        plot_ieee_journals(ieee_journals, os.path.join(FIGURES_DIR, "gpst_ieee_journals.png"))
        tikz_export.write_tikz_ieee_journals(
            ieee_journals, os.path.join(FIGURES_DIR, "gpst_ieee_journals.tex"))

        # IEEE Transactions citations by G-PST area
        plot_ieee_by_area(area_rows, os.path.join(FIGURES_DIR, "gpst_ieee_by_area.png"))
        tikz_export.write_tikz_ieee_by_area(area_rows, os.path.join(FIGURES_DIR, "gpst_ieee_by_area.tex"))

        # IEEE Transactions citations by decade (use review CSV to match journal figure)
        if os.path.exists(review_path):
            ieee_by_year = load_ieee_years_from_review_csv(review_path)
            ieee_articles_by_year = load_ieee_journals_by_year_from_review_csv(review_path)
        else:
            ieee_by_year = compute_ieee_by_year(citations)
            ieee_articles_by_year = compute_ieee_journals_by_year(citations)
        plot_ieee_by_year(ieee_by_year, os.path.join(FIGURES_DIR, "gpst_ieee_by_year.png"),
                          journal_counts=ieee_articles_by_year)
        tikz_export.write_tikz_ieee_by_year(ieee_by_year, os.path.join(FIGURES_DIR, "gpst_ieee_by_year.tex"),
                                             journal_counts=ieee_articles_by_year)

        # Same, but with the comparison entity's (default ESIG) citations
        # broken out as a separate stacked color instead of folded in
        split_entity = (args.comparison_entity or "ESIG").strip() or "ESIG"
        if os.path.exists(review_path):
            ieee_by_year_split = load_ieee_years_split_from_review_csv(review_path, split_entity)
            plot_ieee_by_year_split(
                ieee_by_year_split,
                os.path.join(FIGURES_DIR, "gpst_ieee_by_year_esig_split.png"),
                split_entity=split_entity,
            )
            tikz_export.write_tikz_ieee_by_year_split(
                ieee_by_year_split,
                os.path.join(FIGURES_DIR, "gpst_ieee_by_year_esig_split.tex"),
                split_entity=split_entity,
            )

        # Overall citation-composition (IEEE Trans / other journals / other)
        grand = area_rows[-1]
        plot_citation_shares(grand, os.path.join(FIGURES_DIR, "gpst_citation_shares.png"))
        tikz_export.write_tikz_citation_shares(grand, os.path.join(FIGURES_DIR, "gpst_citation_shares.tex"))

        # IEEE Transactions' share of journal-only citations
        plot_journal_ieee_share(grand, os.path.join(FIGURES_DIR, "gpst_journal_ieee_share.png"))
        tikz_export.write_tikz_journal_ieee_share(
            grand, os.path.join(FIGURES_DIR, "gpst_journal_ieee_share.tex"))

        # Per-entity 6-panel
        for ent_name, ent_data in stats["by_entity"].items():
            slug = re.sub(r"[^a-z0-9]+", "_", ent_name.lower()).strip("_")
            out  = os.path.join(FIGURES_DIR, f"gpst_{slug}.png")
            plot_entity(ent_name, ent_data, out)

    # 5 — With/without comparison (default: ESIG)
    comp_entity = (args.comparison_entity or "").strip()
    if comp_entity and comp_entity in stats["by_entity"]:
        print(f"\n[5] Building with/without-{comp_entity} comparison ...")
        slug = re.sub(r"[^a-z0-9]+", "_", comp_entity.lower()).strip("_")

        index_excl     = {fn: r for fn, r in index.items() if r["entity_name"] != comp_entity}
        citations_excl = [c for c in citations if c["entity_name"] != comp_entity]
        stats_excl     = compute_stats(index_excl, citations_excl)
        area_rows_excl = compute_area_summary(stats_excl)
        grand_excl     = area_rows_excl[-1]

        for e in sorted(stats_excl["by_entity"]):
            d = stats_excl["by_entity"][e]
            print(f"    (no {comp_entity}) {e:<28}  docs={d['total_docs']:3d}  "
                  f"cites={d['total_citations']:4d}  ieee={d['ieee_transactions']:3d}")

        if not args.figures_only:
            write_area_summary_csv(area_rows_excl,
                                   os.path.join(STATS_DIR, f"area_summary_no_{slug}.csv"))
            write_entity_exclusion_comparison(
                stats, stats_excl, area_rows, area_rows_excl, comp_entity,
                os.path.join(STATS_DIR, f"comparison_with_without_{slug}.csv"),
                os.path.join(STATS_DIR, f"comparison_with_without_{slug}.tex"),
            )
            write_citations_by_area_comparison(
                area_rows, area_rows_excl, comp_entity,
                os.path.join(STATS_DIR, f"citations_by_area_with_without_{slug}.csv"),
                os.path.join(STATS_DIR, f"citations_by_area_with_without_{slug}.tex"),
            )
            write_latex_table(
                stats_excl, os.path.join(STATS_DIR, f"entity_summary_no_{slug}.tex"),
                label=f"tab:entity_summary_no_{slug}",
                caption_suffix=f", excluding {comp_entity}",
            )

            if os.path.exists(review_path):
                ieee_journals_excl_stats = load_ieee_journals_from_review_csv(
                    review_path, {comp_entity})
            else:
                ieee_journals_excl_stats = compute_ieee_journals(citations_excl)
            other_journals_excl = compute_other_journals(citations_excl, manual_other_names)
            write_unresolved_other_journals_csv(
                citations_excl,
                os.path.join(STATS_DIR, f"unresolved_other_journals_no_{slug}.csv"),
                manual_names=manual_other_names,
            )
            write_journal_publishers_csv(
                ieee_journals_excl_stats, other_journals_excl, publishers,
                os.path.join(STATS_DIR, f"journal_publishers_no_{slug}.csv"),
                tex_path=os.path.join(STATS_DIR, f"journal_publishers_no_{slug}.tex"),
                label=f"tab:journal_publishers_no_{slug}",
                caption_suffix=f", excluding {comp_entity}",
            )
            write_publisher_groups(
                ieee_journals_excl_stats, other_journals_excl, publishers,
                os.path.join(STATS_DIR, f"publisher_groups_no_{slug}.csv"),
                os.path.join(STATS_DIR, f"publisher_groups_no_{slug}.tex"),
                label=f"tab:publisher_groups_no_{slug}",
                caption_suffix=f", excluding {comp_entity}",
            )
            write_journal_citations_by_document_csv(
                citations, ieee_names, manual_other_names, publishers,
                os.path.join(STATS_DIR, f"journal_citations_by_document_no_{slug}.csv"),
                exclude_entity=comp_entity,
            )
            write_citation_counts_summary(
                area_rows[-1], area_rows_excl[-1], comp_entity,
                sum(ieee_journals.values()) + sum(other_journals.values()),
                sum(ieee_journals_excl_stats.values()) + sum(other_journals_excl.values()),
                os.path.join(STATS_DIR, "citation_counts_summary.tex"),
            )

        if not args.no_figures:
            plot_all_entities(stats_excl,
                              os.path.join(FIGURES_DIR, f"gpst_all_entities_no_{slug}.png"))
            tikz_export.write_tikz_all_entities(
                stats_excl, AREA_KEYS, AREA_TITLES,
                os.path.join(FIGURES_DIR, f"gpst_all_entities_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_area_summary(area_rows_excl,
                              os.path.join(FIGURES_DIR, f"gpst_area_summary_no_{slug}.png"))
            tikz_export.write_tikz_area_summary(
                area_rows_excl, os.path.join(FIGURES_DIR, f"gpst_area_summary_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_area_summary_journals(
                area_rows_excl, os.path.join(FIGURES_DIR, f"gpst_area_summary_journals_no_{slug}.png"),
                title=f"Excluding {comp_entity}")
            tikz_export.write_tikz_area_summary_journals(
                area_rows_excl, os.path.join(FIGURES_DIR, f"gpst_area_summary_journals_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_ieee_by_area(area_rows_excl,
                              os.path.join(FIGURES_DIR, f"gpst_ieee_by_area_no_{slug}.png"))
            tikz_export.write_tikz_ieee_by_area(
                area_rows_excl, os.path.join(FIGURES_DIR, f"gpst_ieee_by_area_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_citation_shares(grand_excl,
                                 os.path.join(FIGURES_DIR, f"gpst_citation_shares_no_{slug}.png"),
                                 title=f"Excluding {comp_entity}")
            tikz_export.write_tikz_citation_shares(
                grand_excl, os.path.join(FIGURES_DIR, f"gpst_citation_shares_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_journal_ieee_share(grand_excl,
                                    os.path.join(FIGURES_DIR, f"gpst_journal_ieee_share_no_{slug}.png"),
                                    title=f"Excluding {comp_entity}")
            tikz_export.write_tikz_journal_ieee_share(
                grand_excl, os.path.join(FIGURES_DIR, f"gpst_journal_ieee_share_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            if os.path.exists(review_path):
                exclude_set = {comp_entity}
                ieee_journals_excl = load_ieee_journals_from_review_csv(review_path, exclude_set)
                ieee_by_year_excl  = load_ieee_years_from_review_csv(review_path, exclude_set)
                ieee_articles_by_year_excl = load_ieee_journals_by_year_from_review_csv(review_path, exclude_set)
            else:
                ieee_journals_excl = compute_ieee_journals(citations_excl)
                ieee_by_year_excl  = compute_ieee_by_year(citations_excl)
                ieee_articles_by_year_excl = compute_ieee_journals_by_year(citations_excl)
            plot_ieee_journals(ieee_journals_excl,
                               os.path.join(FIGURES_DIR, f"gpst_ieee_journals_no_{slug}.png"))
            tikz_export.write_tikz_ieee_journals(
                ieee_journals_excl, os.path.join(FIGURES_DIR, f"gpst_ieee_journals_no_{slug}.tex"),
                title=f"Excluding {comp_entity}")

            plot_ieee_by_year(ieee_by_year_excl,
                              os.path.join(FIGURES_DIR, f"gpst_ieee_by_year_no_{slug}.png"),
                              journal_counts=ieee_articles_by_year_excl)
            tikz_export.write_tikz_ieee_by_year(
                ieee_by_year_excl, os.path.join(FIGURES_DIR, f"gpst_ieee_by_year_no_{slug}.tex"),
                title=f"Excluding {comp_entity}", journal_counts=ieee_articles_by_year_excl)
    elif comp_entity:
        print(f"\n[5] Skipping with/without-{comp_entity} comparison — "
              f"'{comp_entity}' not present in the loaded data.")

    # 6 — System operators only (grid TSOs/ISOs/RTOs; excludes research
    # consortia like G-PST/ESIG and vendors like GE Vernova/Siemens Energy/ABB)
    print("\n[6] Building system-operators-only scope ...")
    build_inclusion_scope_outputs(
        "system_operators", "system operators only",
        index, citations, stats, publishers, ieee_names, manual_other_names,
        review_path, lambda ent: ent not in NON_SYSTEM_OPERATOR_ENTITIES, args,
    )

    print("\nDone.")
    print(f"  Stats  → {STATS_DIR}/")
    print(f"  Figures → {FIGURES_DIR}/")


if __name__ == "__main__":
    main()