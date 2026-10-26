#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paths.py
========
Single source of truth for every file and folder the SO-involvement pipeline
reads or writes. All paths are resolved from this file's location, so the
scripts work from any current working directory.

Layout (relative to SO-involvement/):

  data/input/      hand-maintained configuration and correction tables
  data/interim/    pipeline intermediates (document index, citations) — the
                   curated CSVs here are the hand-reviewed ground truth
  data/pdfs/       downloaded PDFs (git-ignored, re-creatable)
  data/results/    stats/ and figures/ produced by analyze_citations.py

Set the environment variable SO_DATA_DIR to point the whole pipeline at a
different data folder (e.g. a scratch copy for testing) without editing code.
"""

import os
from pathlib import Path

ROOT_DIR    = Path(__file__).resolve().parent.parent
DATA_DIR    = Path(os.environ.get("SO_DATA_DIR", ROOT_DIR / "data")).resolve()

INPUT_DIR   = DATA_DIR / "input"
INTERIM_DIR = DATA_DIR / "interim"
PDF_DIR     = DATA_DIR / "pdfs"
RESULTS_DIR = DATA_DIR / "results"
STATS_DIR   = RESULTS_DIR / "stats"
FIGURES_DIR = RESULTS_DIR / "figures"

# ─── Inputs ───────────────────────────────────────────────────────────────────
ORGANIZATIONS_TOML       = INPUT_DIR / "organizations.toml"
CLASSIFICATION_RULES     = INPUT_DIR / "classification_rules.toml"
FETCH_TARGETS_TXT        = INPUT_DIR / "fetch_targets.txt"
JOURNAL_PUBLISHERS_CSV   = INPUT_DIR / "journal_publishers.csv"
CITATION_CORRECTIONS_CSV = INPUT_DIR / "citation_corrections.csv"

# ─── Intermediates ────────────────────────────────────────────────────────────
DB_PATH                  = INTERIM_DIR / "documents.db"
DOCUMENTS_CATALOG_CSV    = INTERIM_DIR / "documents_catalog.csv"
DOCUMENT_INDEX_CSV       = INTERIM_DIR / "document_index.csv"
DOCS_WITH_CITATIONS_CSV  = INTERIM_DIR / "documents_with_citations.csv"
ALL_CITATIONS_CSV        = INTERIM_DIR / "all_citations.csv"

# ─── PDFs ─────────────────────────────────────────────────────────────────────
PDF_CURATED_DIR          = PDF_DIR / "documents_with_citations"
