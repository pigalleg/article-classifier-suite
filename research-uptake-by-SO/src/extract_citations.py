#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_citations.py
====================
Script 1.7 of 2 — Read documents_with_citations.csv (include=yes rows), find
each PDF on disk, extract its references section, and write all_citations.csv.

Run this between organize_citations.py and analyze_citations.py:

    python src/fetch_documents.py
    python scripts/export_document_index.py
    python src/organize_citations.py
    python src/extract_citations.py
    python src/analyze_citations.py

Usage:
    python src/extract_citations.py
    python src/extract_citations.py --overwrite   # reprocess PDFs already in all_citations.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import pdfplumber
from tqdm import tqdm

# ─── Paths ────────────────────────────────────────────────────────────────────

from paths import PDF_DIR as PDF_BASE, DOCS_WITH_CITATIONS_CSV as DOC_IDX_CSV, \
    ALL_CITATIONS_CSV as ALL_CIT_CSV

FIELDNAMES = [
    "pdf_filename", "pdf_url", "entity_name", "doc_title", "doc_year",
    "gpst_area", "citation_number", "full_reference", "ref_type",
    "is_ieee_transactions",
]

# ─── Text cleaning ────────────────────────────────────────────────────────────

_ENCODING_MAP = [
    ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
    ("–", "-"), ("—", "--"),
    ("‚Äú", '"'), ("‚Äù", '"'), ("‚Äî", "--"), ("‚Äì", "-"),
    ("ﬁ", "fi"), ("ﬂ", "fl"), ("­", ""),
]

def clean_text(s: str) -> str:
    for bad, good in _ENCODING_MAP:
        s = s.replace(bad, good)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)


# ─── Reference classification ─────────────────────────────────────────────────

IEEE_TRANS_RE = re.compile(
    r"IEEE Trans(?:actions)?\.?\s+on\b|"
    r"IEEE Tran\.\s+on\b|"
    r"IEEE Transactions on (?:Power|Energy|Smart Grid|Sustainable|Control|Industry|Signal|Neural)",
    re.IGNORECASE,
)

CLASSIFICATION_RULES = [
    ("journal", re.compile(
        r"IEEE Trans|Nature [A-Z]|\bScience\b|Energy Policy|Applied Energy|"
        r"Renewable (?:and Sustainable )?Energy|Electric Power Systems Research|"
        r"International Journal|Journal of|The Energy Journal|"
        r"\bEnergies\b|Electricity Journal|EURO.?J\.|Joule\b|"
        r"(?:vol\.|volume)\s*\d+.{0,20}(?:no\.|pp?\.|issue)|"
        r"\bpp\.\s*\d+[–\-]\d+\b",
        re.IGNORECASE,
    )),
    ("conference", re.compile(
        r"Proc(?:eedings)?\.?\s+of|In Proc\.|IEEE PES\b|IEEE PESGM|"
        r"Power Systems Computation|CIGRE|PSCC\b|"
        r"(?:Annual|International|National)\s+(?:Conference|Symposium|Workshop|Meeting|Convention)|"
        r"Conf\.\s+on|Workshop on|\bSummit\b",
        re.IGNORECASE,
    )),
    ("standard", re.compile(
        r"IEEE Std\b|IEEE P\d{3,}|IEC \d{5}|"
        r"NERC\s+(?:Standard|Reliability Standard|FAC|MOD|PRC|TPL|VAR|BAL|CIP)|"
        r"ANSI/IEEE|ISO \d{4}|EN \d{4,}|FERC Order|FERC Regulation",
        re.IGNORECASE,
    )),
    ("report", re.compile(
        r"Technical Report|Tech\. Rep\.|NREL/|DOE/|\bEPRI\b|"
        r"Lawrence Berkeley|LBNL|Argonne|Oak Ridge|Pacific Northwest|Sandia|"
        r"National Renewable Energy|National Laboratory|"
        r"Report(?:\s+(?:No\.|Number|#))?\s*[\w\-]+|"
        r"Working Paper|White Paper|Whitepaper|"
        r"Task Force Report|Staff Report|Issue Brief|Fact Sheet|"
        r"ESIG\b.*[Rr]eport|[Rr]eport\s+(?:by|of)",
        re.IGNORECASE,
    )),
    ("thesis", re.compile(
        r"(?:PhD|Ph\.D|doctoral|master'?s?|M\.Sc|M\.S\.)\s+(?:thesis|dissertation)|"
        r"(?:thesis|dissertation),?\s+(?:submitted|University|Institute)",
        re.IGNORECASE,
    )),
    ("book", re.compile(
        r"(?:MIT|Cambridge|Oxford|Springer|Elsevier|Wiley|McGraw|CRC|Prentice)\s+(?:Press|[A-Z])|"
        r"\bIn\b.{0,60}\bEd(?:itors?|s\.)\b|\bChapter\s+\d+\b|\bISBN\b",
        re.IGNORECASE,
    )),
    ("magazine", re.compile(
        r"IEEE (?:Spectrum|Power(?:\s*&\s*Energy|and Energy)\s*Magazine|Magazine)|"
        r"Power Magazine|Utility Dive|Canary Media|Bloomberg|"
        r"E&E News|Politico|Reuters|[Mm]agazine\b|[Nn]ewsletter\b",
        re.IGNORECASE,
    )),
    ("dataset", re.compile(
        r"[Dd]ataset\b|[Dd]ata [Ss]et\b|[Dd]atabase\b|"
        r"EIA\s+(?:Form|Annual Energy Outlook|Electric Power|Monthly)|"
        r"FERC\s+Form\s+\d+|accessed\s+(?:at|from|via)\s+https?://",
        re.IGNORECASE,
    )),
    ("website", re.compile(
        r"(?:Retrieved|Available|Accessed)\s+(?:from\s+)?https?://|"
        r"[Ww]eb(?:site|page)?\s+at\s+https?://",
        re.IGNORECASE,
    )),
]


def classify_reference(text: str):
    for ref_type, pattern in CLASSIFICATION_RULES:
        if pattern.search(text):
            is_ieee = (ref_type == "journal") and bool(IEEE_TRANS_RE.search(text))
            return ref_type, is_ieee
    return "other", False


# ─── Citation extraction ──────────────────────────────────────────────────────

REFERENCE_HEADERS_RE = re.compile(
    r"(?m)^[ \t]*(?:\d+\.?\s+)?"
    r"(?:References|Bibliography|Works Cited|Notes|Endnotes|Footnotes"
    r"|Literatur(?:verzeichnis)?|Quellenverzeichnis|Quellen|Referenzen"
    r"|Bibliographie|Literaturliste|Literaturangaben"
    r"|Références|Bibliographie|Lista de referencias|Referencias)"
    r"[ \t]*$",
    re.IGNORECASE,
)
RE_NUMBERED       = re.compile(r"^[ \t]{0,4}(\d{1,3})[.)]\s+(\S.+)$")
RE_BRACKETED      = re.compile(r"^[ \t]{0,4}\[(\d{1,3})\]\s+(\S.+)$")
# A bullet-glyph list marker (e.g. a Wingdings/Symbol-font bullet character)
# that pdfplumber decodes as a bare lowercase "u" followed by whitespace,
# seen in some German bibliography formats (one entry per bullet, no
# numbering). Requires whitespace (not ".") right after "u" so it doesn't
# match the German abbreviation "u." ("u. a." = "unter anderem").
RE_BULLET         = re.compile(r"^[ \t]{0,4}u[ \t]+(\S.+)$")
# Bare "Kapitel N" ("Chapter N") section-divider lines that appear between
# bullet entries in that same bibliography format — not part of any entry,
# so they shouldn't be glued onto the preceding citation as continuation text.
_CHAPTER_HEADER_RE = re.compile(r"^[ \t]{0,4}Kapitel\s+\d+[ \t]*$")
_MONTHS = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
)
RE_AUTHOR_YEAR    = re.compile(
    r"^(?!(?:" + _MONTHS + r")\b)"  # exclude bare month-year continuation lines
    r"("
    r"(?:[A-Z][A-Za-z\-\.']+(?:[,.\s]+[A-Z][A-Za-z\.\-]*)*"  # a single bare
                                                 # capitalized word (e.g. "Oncor.
                                                 # 2025." / "Tesla. 2025.") is a
                                                 # legitimate org/surname-only
                                                 # author-year opener — kept
                                                 # permissive here; the mid-entry
                                                 # false-trigger case (a wrapped
                                                 # "City: Publisher, Year." line
                                                 # inside an already-open
                                                 # numbered/bracketed/bulleted
                                                 # entry) is instead guarded in
                                                 # _collect_entry(), which knows
                                                 # the current entry's marker
                                                 # style and only lets an
                                                 # "authoryear" match break
                                                 # collection of another
                                                 # "authoryear" entry.
    r"(?:,?\s+and\s+[A-Z][A-Za-z\.\-][^0-9]{0,40})?"
    r"|[A-Z]{2,}(?:[\s\-][A-Za-z]{2,})*"
    r"|(?:U\.S\.|U\.K\.|U\.N\.)\s+[A-Z][A-Za-z\s\.\-]+"
    r")"
    r"[,.]?\s+\d{4}[a-z]?[,.]?\s+.{10,})$"
)
RE_FOOTNOTE       = re.compile(r"^(\d{1,3})[ \t]{1,4}([A-Z].{20,})$")


def _is_entry_start(line):
    s = line.rstrip()
    m = RE_BRACKETED.match(s)
    if m:
        return ("numbered", m.group(1), m.group(2))
    m = RE_NUMBERED.match(s)
    if m:
        return ("numbered", m.group(1), m.group(2))
    m = RE_BULLET.match(s)
    if m:
        return ("bullet", "", m.group(1))
    m = RE_AUTHOR_YEAR.match(s)
    if m:
        return ("authoryear", "", m.group(1))
    return None


def _collect_entry(lines, start, start_type=None, style_counts=None):
    """
    `start_type` is the marker style ("numbered"/"bullet"/"authoryear") that
    `lines[start]` itself matched. `style_counts` (optional) is a running
    {style: n} count of already-validated entries earlier in this same
    document's reference list.

    A reference list that has already produced several genuine
    numbered/bracketed/bulleted entries never legitimately switches to
    unmarked author-year entries mid-list — so once that style is clearly
    *dominant* for this document, an "authoryear" match on a later line is
    almost always a false trigger (a wrapped "City: Publisher, Year."
    continuation clause that happens to look like an author-year opener),
    not a real new entry, and shouldn't break collection.

    This protection requires a high bar (>=5 already-validated entries of
    that style) before it applies — a genuinely numbered/bracketed/bulleted
    reference list accumulates many of these quickly (entry 12 of a "[N]"
    list has already seen 11), but an author-year-style document can easily
    produce two or three *coincidental* numbered-looking matches (e.g. a
    quoted source title that itself starts with a footnote-style "25." or
    a stray "24. TO BE COMPLETED." placeholder) without ever being a real
    numbered list. A low threshold would let one such fluke gain immunity
    and swallow every following real author-year entry as "continuation"
    text until the next numbered/bracketed/bullet marker (which may never
    come).
    """
    protected = (
        start_type in ("numbered", "bullet")
        and style_counts is not None
        and style_counts.get(start_type, 0) >= 5
    )
    result = [lines[start].rstrip()]
    consumed = 1
    blank_streak = 0
    for j in range(start + 1, min(start + 20, len(lines))):
        ln = lines[j].rstrip()
        if not ln.strip():
            blank_streak += 1
            if blank_streak >= 2:
                break
            consumed += 1
            continue
        blank_streak = 0
        nxt = _is_entry_start(ln)
        if nxt is not None and not (nxt[0] == "authoryear" and protected):
            break
        if _CHAPTER_HEADER_RE.match(ln):
            consumed += 1
            continue
        result.append(ln.strip())
        consumed += 1
    return " ".join(r for r in result if r.strip()), consumed


# ─── Citation-shape validation ─────────────────────────────────────────────
#
# _is_entry_start()/RE_NUMBERED only checks that a line starts with "N. " —
# true of both real bibliography entries AND, e.g., numbered regulatory
# requirement lists or procedural steps elsewhere in the document. Once a
# numbered/bracketed/author-year line has been collected into a candidate
# entry, check whether its *content* actually looks like a citation before
# accepting it — a real reference names an author or issuing entity, a
# title or venue, and (almost always) a date or URL/DOI. A candidate with
# fewer than 2 of these signals is almost certainly not a citation.

_CITATION_AUTHOR_RE = re.compile(
    r"^(?:"
    r"[A-Z][A-Za-z\-\.']+,\s+[A-Z][A-Za-z\-']+[:.,]|"       # "Smith, J." AND "Schmid, Tobias:"/
                                                 # "Guminski, Andrej:" — full (unabbreviated)
                                                 # first name, common in European citation styles,
                                                 # not just an initial-plus-period.
    r"[A-Z]{2,}\b(?=[,.(:])|"                   # acronym org AS author: "NERC," "IEEE." "FERC (" /
                                                 # "RWI:" — added ':' since "Author: Title" is a
                                                 # common citation format (not just German).
                                                 # Still requires punctuation right after the
                                                 # acronym, so "PJM ALL-CALL notification..." (the
                                                 # acronym used mid-sentence as an operational
                                                 # reference, not as a cited author) doesn't match.
    r"(?:U\.S\.|U\.K\.|U\.N\.)|"
    r"[A-Z][a-z]+(?:\s+[A-Z&][a-z]*){0,4}\s+"
    r"(?:Commission|Agency|Institute|Institut|Laboratory|Corporation|Group|"
    r"Association|Council|Administration|Department|Authority|"
    r"Company|Organization|Operator|Board|Grid)\b|"
    r"[A-ZÄÖÜ][^:\n]{1,79}:"                     # generic "Name(s)/Org: Title" opener — any
                                                 # uppercase-led run of up to 80 chars before a
                                                 # colon, regardless of internal punctuation/
                                                 # spacing (covers "Fraunhofer ISI:", "RWI -
                                                 # Leibniz-Institut für Wirtschaftsforschung:",
                                                 # etc.) that doesn't fit the more specific
                                                 # patterns above; a colon this early is
                                                 # characteristic of a citation's author/org
                                                 # field, not ordinary prose.
    r")"
)
_CITATION_VENUE_RE = re.compile(
    r"\bVol\.|\bpp\.|\bJournal\b|\bTransactions\b|\bPress\b|"
    r"\bProceedings\b|\bConference\b|\bReport\s*(?:No\.|#)\s*[\w\-]|"
    r"NREL/|DOE/|\bISBN\b|\bStd\.?\s*\d|IEC\s*\d|\bTechnical Report\b|"
    r"\bWorking Paper\b|\bWhite Paper\b",
    re.IGNORECASE,
)
# A negative lookbehind excludes quoted spans that are an internal
# cross-reference to another section of the SAME document — a recurring
# pattern in revision-history changelogs, either naming where a change
# happened ('...added "Attachment E" under "Process Diagrams" of "Section
# 2"...') or what changed ('Added "Attachment E: Process Diagrams"',
# 'Deleted "...congestion" from Exhibit 5.2') — none of which is an
# external work's title.
_CITATION_TITLE_RE = re.compile(
    r'(?<!under )(?<!Under )(?<!of )(?<!Of )'
    r'(?<!added )(?<!Added )(?<!deleted )(?<!Deleted )'
    r'(?<!changed )(?<!Changed )(?<!removed )(?<!Removed )'
    r'["“][^"”]{8,300}["”]'
)
_CITATION_YEAR_RE  = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")
_CITATION_URL_RE   = re.compile(r"https?://|doi\.org|www\.\w")
# Recurring, unambiguous internal-document markers (not citations at all)
# that otherwise accumulate enough incidental signals (a date, a quoted
# cross-reference, an org-acronym) to pass the signal-count check above.
_NON_CITATION_MARKERS_RE = re.compile(
    r"Revision History|Effective Date:\s*\d|Periodic Review\b.{0,20}Criteria|"
    r"ALL-CALL notification",
    re.IGNORECASE,
)

_ENTRY_FAIL_STREAK_LIMIT = 4  # consecutive non-citation-shaped "entries" -> stop


_LEADING_ENTRY_MARKER_RE = re.compile(r"^[ \t]{0,4}(?:\[\d{1,3}\]|\d{1,3}[.)])\s+")


def _looks_like_citation(text: str) -> bool:
    if _NON_CITATION_MARKERS_RE.search(text):
        return False
    signals = 0
    if _CITATION_YEAR_RE.search(text):
        signals += 1
    if _CITATION_URL_RE.search(text):
        signals += 1
    if _CITATION_TITLE_RE.search(text):
        signals += 1
    if _CITATION_VENUE_RE.search(text):
        signals += 1
    # Strip a leading "[N] "/"N. " entry marker before testing the author
    # pattern — otherwise it can never match (no pattern accounts for a
    # leading bracket/number), silently making this signal depend entirely
    # on the "et al." fallback for every numbered/bracketed citation.
    author_text = _LEADING_ENTRY_MARKER_RE.sub("", text.lstrip(), count=1)
    if _CITATION_AUTHOR_RE.match(author_text) or re.search(r"\bet al\.?\b", text):
        signals += 1
    return signals >= 2


def _parse_entries(ref_lines, start_idx, make_record):
    """
    Walk ref_lines from start_idx, collecting entry-shaped chunks and
    validating each looks like an actual citation before accepting it. A
    run of _ENTRY_FAIL_STREAK_LIMIT consecutive candidates that don't look
    like citations signals we've walked past the end of the real reference
    list into unrelated numbered body text — stop there rather than
    continuing to scan to the end of the document (which otherwise pulls
    in things like numbered regulatory requirement lists as fake
    citations).
    """
    citations = []
    i = start_idx
    fail_streak = 0
    style_counts = {}
    while i < len(ref_lines):
        entry = _is_entry_start(ref_lines[i])
        if not entry:
            i += 1
            continue
        full_ref, consumed = _collect_entry(ref_lines, i, entry[0], style_counts)
        if _looks_like_citation(full_ref):
            rec = make_record(entry[1], full_ref)
            if rec:
                citations.append(rec)
                style_counts[entry[0]] = style_counts.get(entry[0], 0) + 1
            fail_streak = 0
        else:
            fail_streak += 1
            if fail_streak >= _ENTRY_FAIL_STREAK_LIMIT:
                break
        i += consumed
    return citations


def _column_aware_page_text(page, plain_text: str) -> str:
    """
    Two-column reference lists (common in ENTSO-E/CIGRE-adjacent reports)
    get scrambled by plain top-to-bottom-then-left-to-right extraction: it
    reads both columns' lines interleaved, so a right-column entry's number
    (e.g. "[13]") lands mid-line instead of at line start, corrupting the
    left-column entry it gets appended to AND making the right-column entry
    itself invisible to _is_entry_start (which only checks line starts).

    Splitting the page into left/right halves and extracting each
    separately fixes this — but only actually helps on genuinely
    two-column pages; on a single-column page it's a no-op or can hurt
    (mid-sentence line breaks at the crop boundary). Rather than trying to
    pre-classify layout (unreliable — word-density heuristics fire on
    ordinary single-column pages too, since text naturally spans both
    halves), just extract both ways and keep whichever yields more
    recognizable reference-entry starts.
    """
    try:
        width = page.width
        if width < 100:
            return plain_text
        left  = page.crop((0, 0, width / 2, page.height)).extract_text() or ""
        right = page.crop((width / 2, 0, width, page.height)).extract_text() or ""
        split_text = clean_text(left) + "\n" + clean_text(right)
    except Exception:
        return plain_text

    def count_entries(text):
        return sum(1 for ln in text.splitlines() if _is_entry_start(ln))

    return split_text if count_entries(split_text) > count_entries(plain_text) else plain_text


def extract_citations(pdf_path: Path, meta: dict) -> list:
    """
    Extract references from a PDF.  `meta` must contain keys:
      pdf_url, entity_name, doc_title, doc_year, gpst_area
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = [clean_text(p.extract_text() or "") for p in pdf.pages]

            # Locate the references section (scan last 30 pages) using the
            # plain extraction — headers are short single lines, rarely
            # affected by column layout — then re-extract just that
            # column-aware for the pages actually parsed below (the PDF
            # must still be open for the crop-based re-extraction).
            ref_start_page = None
            for i in range(len(pages_text) - 1, max(len(pages_text) - 31, -1), -1):
                if REFERENCE_HEADERS_RE.search(pages_text[i]):
                    ref_start_page = i
                    break

            if ref_start_page is not None:
                for i in range(ref_start_page, len(pages_text)):
                    pages_text[i] = _column_aware_page_text(pdf.pages[i], pages_text[i])
            else:
                cutoff = max(0, int(len(pages_text) * 0.7))
                for i in range(cutoff, len(pages_text)):
                    pages_text[i] = _column_aware_page_text(pdf.pages[i], pages_text[i])
    except Exception as e:
        tqdm.write(f"  [warn] cannot open {pdf_path.name}: {e}")
        return []

    full_text = "\n".join(pages_text)
    gpst_area = meta.get("gpst_area_validation", meta.get("gpst_area", "planning")).strip()
    if "," in gpst_area:
        gpst_area = gpst_area.split(",")[0].strip()
    gpst_area = gpst_area.lower() or "planning"

    def make_record(num, text):
        text = " ".join(text.split())
        if len(text) < 20:
            return None
        ref_type, is_ieee = classify_reference(text)
        return {
            "pdf_filename":         pdf_path.name,
            "pdf_url":              meta.get("pdf_url", ""),
            "entity_name":          meta.get("entity_name", ""),
            "doc_title":            meta.get("doc_title", ""),
            "doc_year":             meta.get("doc_year", ""),
            "gpst_area":            gpst_area,
            "citation_number":      num,
            "full_reference":       text,
            "ref_type":             ref_type,
            "is_ieee_transactions": "Yes" if is_ieee else "No",
        }

    citations = []

    if ref_start_page is not None:
        ref_lines = "\n".join(pages_text[ref_start_page:]).splitlines()
        i = 0
        for k, ln in enumerate(ref_lines):
            if re.match(r"^\s*(?:\d+\.?\s+)?(?:References|Bibliography|Works Cited)\s*$", ln, re.I):
                i = k + 1
                break
        citations.extend(_parse_entries(ref_lines, i, make_record))
    else:
        # Fallback: no References header found — scan last 30% of pages for a
        # numbered [N] block that contains at least one IEEE citation.
        _ieee_num_re = re.compile(r"(?m)^\s*\[\d{1,3}\]\s+.{0,200}IEEE", re.I)
        cutoff = max(0, int(len(pages_text) * 0.7))
        tail_text = "\n".join(pages_text[cutoff:])
        if _ieee_num_re.search(tail_text):
            ref_lines = tail_text.splitlines()
            # Find the first [N] or N. line in the block
            i = 0
            while i < len(ref_lines):
                ln = ref_lines[i].rstrip()
                if RE_BRACKETED.match(ln) or RE_NUMBERED.match(ln):
                    break
                i += 1
            citations.extend(_parse_entries(ref_lines, i, make_record))

    # Supplement with inline footnotes
    captured = {c["citation_number"] for c in citations}
    for m in RE_FOOTNOTE.finditer(full_text):
        if m.group(1) in captured:
            continue
        raw = clean_text(m.group(2))
        if not (re.search(r"\b\d{4}\b", raw) or re.search(r"https?://", raw)):
            continue
        rec = make_record(m.group(1), raw)
        if rec:
            citations.append(rec)

    # Deduplicate by first 100 chars of reference text
    seen, deduped = set(), []
    for c in citations:
        k = c["full_reference"][:100]
        if k not in seen:
            seen.add(k)
            deduped.append(c)

    return deduped


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Extract citations from included PDFs")
    p.add_argument(
        "--overwrite", action="store_true",
        help="Reprocess PDFs already present in all_citations.csv (default: skip them).",
    )
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("Citation Extractor")
    print("=" * 60)

    # Load document index
    if not DOC_IDX_CSV.exists():
        print(f"[error] {DOC_IDX_CSV} not found — run organize_citations.py first.")
        sys.exit(1)

    with open(DOC_IDX_CSV, encoding="utf-8") as f:
        index = {
            r["pdf_filename"]: r
            for r in csv.DictReader(f)
            if r.get("include", "").strip().lower() == "yes"
        }
    print(f"\n  {len(index)} documents to process (include=yes)")

    # Determine which PDFs to skip (already extracted)
    already_done: set[str] = set()
    if not args.overwrite and ALL_CIT_CSV.exists():
        with open(ALL_CIT_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                already_done.add(row["pdf_filename"])
        if already_done:
            print(f"  {len(already_done)} already in all_citations.csv — skipping (use --overwrite to reprocess)")

    to_process = {fn: meta for fn, meta in index.items() if fn not in already_done}
    print(f"  {len(to_process)} PDFs to extract\n")

    if not to_process:
        print("Nothing to do.")
        return

    # Build disk index: filename (lower) → path
    disk_index: dict[str, Path] = {}
    for p in PDF_BASE.rglob("*.pdf"):
        disk_index[p.name.lower()] = p

    # Extract
    all_new: list[dict] = []
    missing = 0
    for fn, meta in tqdm(to_process.items(), desc="Extracting"):
        path = disk_index.get(fn.lower())
        if path is None:
            tqdm.write(f"  [warn] not found on disk: {fn}")
            missing += 1
            continue
        cites = extract_citations(path, meta)
        all_new.extend(cites)
        tqdm.write(f"  {fn}: {len(cites)} citations")

    print(f"\n  Total new citations extracted: {len(all_new)}")
    if missing:
        print(f"  PDFs missing on disk: {missing}")

    if not all_new:
        print("No citations extracted — all_citations.csv not modified.")
        return

    # Write (append or overwrite)
    write_header = args.overwrite or not ALL_CIT_CSV.exists()
    mode = "w" if write_header else "a"

    with open(ALL_CIT_CSV, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(all_new)

    total = len(all_new)
    if not write_header:
        with open(ALL_CIT_CSV, encoding="utf-8") as f:
            total = sum(1 for _ in csv.DictReader(f))

    print(f"  [ok] {ALL_CIT_CSV}  ({total} total rows)")
    print(f"\nDone.  Next step:  python src/analyze_citations.py")


if __name__ == "__main__":
    main()
