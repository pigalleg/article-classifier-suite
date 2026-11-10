#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_document_index.py
========================
Bridge between fetch_documents.py and organize_citations.py.

Reads data/interim/documents.db (written by fetch_documents.py) and writes:

  data/interim/documents_catalog.csv — full dump of documents.db (every URL
                                       discovered, kept or not); a plain-text,
                                       version-controllable record of the crawl
  data/interim/document_index.csv    — one row per kept PDF, for manual review:
                                       has_citations / include / gpst_area / notes

Manual edits in an existing document_index.csv are preserved: for any
pdf_filename already present, the gpst_area, has_citations, include and notes
values are kept as you left them. Rows you added by hand that are not in the
database are kept as well. New documents get has_citations from the
fetch-time heuristic (has_references) and include=yes.

Usage:
    python scripts/export_document_index.py
"""

import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from paths import DB_PATH, DOCUMENTS_CATALOG_CSV, DOCUMENT_INDEX_CSV  # noqa: E402

CATALOG_FIELDS = [
    "url", "pdf_filename", "entity_name", "entity_region", "gpst_area",
    "has_references", "pdf_kept", "doc_title", "doc_year", "processed_at",
]

IDX_FIELDS = [
    "pdf_filename", "pdf_url", "entity_name", "entity_region",
    "doc_title", "doc_year",
    "gpst_area",      # auto-guessed; EDIT THIS
    "has_citations",  # yes / no — drives organize_citations.py
    "include",        # yes / no — set to 'no' to exclude from analysis
    "notes",          # free-text notes field for user
]

MANUAL_FIELDS = ("gpst_area", "has_citations", "include", "notes")


def main():
    if not DB_PATH.exists():
        print(f"[error] {DB_PATH} not found — run src/fetch_documents.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db_rows = [dict(r) for r in conn.execute(
        f"SELECT {', '.join(CATALOG_FIELDS)} FROM documents ORDER BY entity_name, url"
    )]
    conn.close()

    # 1 — Full catalog of the crawl
    with open(DOCUMENTS_CATALOG_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        w.writerows(db_rows)
    print(f"  [ok] {DOCUMENTS_CATALOG_CSV}  ({len(db_rows)} rows)")

    # 2 — Review index of kept PDFs, merged with any existing manual edits
    existing: dict[str, dict] = {}
    if DOCUMENT_INDEX_CSV.exists():
        with open(DOCUMENT_INDEX_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["pdf_filename"]] = row

    merged, seen = [], set()
    for r in db_rows:
        if not r["pdf_kept"]:
            continue
        fn = r["pdf_filename"]
        if fn in seen:
            continue
        seen.add(fn)
        row = {
            "pdf_filename":  fn,
            "pdf_url":       r["url"],
            "entity_name":   r["entity_name"],
            "entity_region": r["entity_region"],
            "doc_title":     r["doc_title"] or "",
            "doc_year":      r["doc_year"] or "",
            "gpst_area":     r["gpst_area"] or "",
            "has_citations": "yes" if r["has_references"] else "no",
            "include":       "yes",
            "notes":         "",
        }
        prev = existing.get(fn)
        if prev:
            for k in MANUAL_FIELDS:
                if k in prev:
                    row[k] = prev[k]
        merged.append(row)

    # Keep hand-added rows that are not (or no longer) in the database
    kept_manual = [r for fn, r in existing.items() if fn not in seen]
    merged.extend(kept_manual)

    with open(DOCUMENT_INDEX_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=IDX_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(merged)
    print(f"  [ok] {DOCUMENT_INDEX_CSV}  ({len(merged)} rows, "
          f"{len(kept_manual)} kept from previous file only)")
    print("\nNext: review has_citations / include / gpst_area in document_index.csv,")
    print("      then run:  python src/organize_citations.py")


if __name__ == "__main__":
    main()
