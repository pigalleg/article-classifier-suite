#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
organize_citations.py
=====================
Script 1.5 of 2 — Copy PDFs that have extracted citations into a
dedicated subfolder, organised by entity.

Run this AFTER fetch_documents.py and BEFORE analyze_citations.py:

    python src/fetch_documents.py
    python scripts/export_document_index.py
    python src/organize_citations.py
    python src/extract_citations.py
    python src/analyze_citations.py

Source layout:
    data/pdfs/<entity>/                     ← all downloaded PDFs

Destination layout:
    data/pdfs/documents_with_citations/<entity>/   ← only PDFs with citations

The flag column "has_citations" in data/interim/document_index.csv drives the
selection.  Files are copied (not moved) so the originals remain intact.

Usage:
    python src/organize_citations.py
    python src/organize_citations.py --dry-run   # show what would be copied
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

from paths import PDF_DIR as PDF_BASE, PDF_CURATED_DIR as DEST_BASE, \
    DOCUMENT_INDEX_CSV as DOC_IDX_CSV, DOCS_WITH_CITATIONS_CSV as CITATIONS_CSV


# ─── Helpers ──────────────────────────────────────────────────────────────────

def build_filename_index(pdf_base: Path) -> dict[str, Path]:
    """
    Walk all entity subdirectories (excluding documents_with_citations) and
    return a mapping of filename (case-insensitive) → absolute Path.

    If the same filename appears in multiple entity dirs the last one wins
    (collisions are extremely unlikely in practice).
    """
    index: dict[str, Path] = {}
    for entity_dir in pdf_base.iterdir():
        if not entity_dir.is_dir():
            continue
        if entity_dir.name == "documents_with_citations":
            continue
        for pdf_file in entity_dir.iterdir():
            if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                index[pdf_file.name.lower()] = pdf_file
    return index


def parse_args():
    p = argparse.ArgumentParser(
        description="Organise PDFs with citations into documents_with_citations/"
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be copied without touching the filesystem.",
    )
    return p.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    if not DOC_IDX_CSV.exists():
        print(f"[error] {DOC_IDX_CSV} not found — run fetch_documents.py and "
              f"scripts/export_document_index.py first.")
        sys.exit(1)

    if not PDF_BASE.exists():
        print(f"[error] {PDF_BASE} not found — run fetch_documents.py first.")
        sys.exit(1)

    # Read document index
    with open(DOC_IDX_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with_citations = [r for r in rows if r.get("has_citations", "").strip().lower() == "yes"]

    print(f"  Documents total:         {len(rows)}")
    print(f"  Documents with citations:{len(with_citations)}")

    if not with_citations:
        print("[info] No documents flagged with has_citations=yes. Nothing to copy.")
        return

    # Index all existing PDFs by filename for fast lookup
    filename_index = build_filename_index(PDF_BASE)

    copied = 0
    skipped = 0
    missing = 0
    csv_rows = []

    for row in with_citations:
        pdf_filename = row["pdf_filename"].strip()
        if not pdf_filename:
            continue

        src_path = filename_index.get(pdf_filename.lower())
        if src_path is None:
            print(f"  [warn] not found on disk: {pdf_filename}")
            missing += 1
            continue

        # Preserve entity folder from the source path
        entity_folder = src_path.parent.name
        dest_dir  = DEST_BASE / entity_folder
        dest_path = dest_dir / src_path.name

        csv_rows.append(row)

        if dest_path.exists():
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [dry-run] would copy  {src_path.relative_to(PDF_BASE)}"
                  f"  →  {dest_path.relative_to(PDF_BASE)}")
            copied += 1
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        copied += 1

    # Write CSV of documents with citations — merge with any existing rows
    if not args.dry_run and csv_rows:
        # Load existing rows so we don't lose entries from prior runs / other pipelines
        existing: list[dict] = []
        existing_fns: set[str] = set()
        if CITATIONS_CSV.exists():
            with open(CITATIONS_CSV, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    existing.append(r)
                    existing_fns.add(r.get("pdf_filename", ""))

        # Only append rows whose filename is not already present
        truly_new = [r for r in csv_rows if r.get("pdf_filename", "") not in existing_fns]

        # Determine unified column set (existing cols take priority for ordering)
        if existing:
            all_cols = list(existing[0].keys())
            for c in (csv_rows[0].keys() if csv_rows else []):
                if c not in all_cols:
                    all_cols.append(c)
        else:
            all_cols = list(csv_rows[0].keys())

        merged = existing + truly_new
        with open(CITATIONS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(merged)
        print(f"  [ok] {CITATIONS_CSV}  ({len(merged)} rows total, {len(truly_new)} newly added)")

    action = "Would copy" if args.dry_run else "Copied"
    print(f"\n  {action}:  {copied}")
    print(f"  Already present (skipped): {skipped}")
    print(f"  Missing on disk:           {missing}")
    if not args.dry_run:
        print(f"\n  Output: {DEST_BASE}")
        print(f"\nDone.  Next step:  python src/extract_citations.py")


if __name__ == "__main__":
    main()
