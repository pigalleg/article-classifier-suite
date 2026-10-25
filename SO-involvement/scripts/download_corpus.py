#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_corpus.py
==================
Re-create the curated PDF corpus from the URLs stored in
data/interim/documents_with_citations.csv, without re-crawling the
organization websites.

PDFs are not stored in the repository. This script downloads every
include=yes document to

    data/pdfs/documents_with_citations/<entity-key>/<pdf_filename>

where <entity-key> is the organization key from data/input/organizations.toml.
That is the layout extract_citations.py searches, so after this script you
can run extract_citations.py and analyze_citations.py directly.

Responses that are not PDFs (HTML error pages, login walls) are rejected and
reported. Documents may have moved or been removed since the corpus was
built; such failures are listed at the end and written to
data/pdfs/download_failures.csv.

Usage:
    python scripts/download_corpus.py
    python scripts/download_corpus.py --entities ESIG PJM   # by entity_name
    python scripts/download_corpus.py --limit 3             # first N per entity (smoke test)
"""

import argparse
import csv
import re
import sys
import time
import tomllib
from collections import defaultdict
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from paths import DOCS_WITH_CITATIONS_CSV, ORGANIZATIONS_TOML, PDF_DIR, PDF_CURATED_DIR  # noqa: E402

DELAY = 1.2   # seconds between requests, same as fetch_documents.py
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def entity_keys() -> dict[str, str]:
    """{entity_name: organizations.toml key}"""
    with open(ORGANIZATIONS_TOML, "rb") as f:
        orgs = tomllib.load(f)
    return {org["name"]: key for key, org in orgs.items()}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def download(session: requests.Session, url: str, dest: Path) -> str | None:
    """Download url to dest. Returns None on success, else an error string."""
    try:
        r = session.get(url, timeout=60, stream=True)
        r.raise_for_status()
        it = r.iter_content(8192)
        first = next(it, b"")
        if not first.lstrip().startswith(b"%PDF"):
            return f"not a PDF (content-type {r.headers.get('content-type', '?')})"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            f.write(first)
            for chunk in it:
                f.write(chunk)
        tmp.replace(dest)
        return None
    except Exception as e:  # network errors, HTTP 4xx/5xx
        return str(e)


def parse_args():
    p = argparse.ArgumentParser(description="Download the curated PDF corpus from stored URLs")
    p.add_argument("--entities", nargs="+", metavar="NAME",
                   help="Only these entity_name values (e.g. ESIG PJM 'RTE (France)').")
    p.add_argument("--limit", type=int, metavar="N",
                   help="At most N documents per entity (for a quick test).")
    return p.parse_args()


def main():
    args = parse_args()
    keys = entity_keys()

    with open(DOCS_WITH_CITATIONS_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("include", "yes").strip().lower() != "no"]
    if args.entities:
        rows = [r for r in rows if r["entity_name"] in args.entities]
    if args.limit:
        per = defaultdict(int)
        limited = []
        for r in rows:
            if per[r["entity_name"]] < args.limit:
                per[r["entity_name"]] += 1
                limited.append(r)
        rows = limited

    print(f"  {len(rows)} documents to fetch into {PDF_CURATED_DIR}")
    session = requests.Session()
    session.headers.update(HEADERS)

    ok = present = 0
    failures = []
    for r in tqdm(rows, desc="Downloading"):
        key = keys.get(r["entity_name"]) or slug(r["entity_name"])
        dest = PDF_CURATED_DIR / key / r["pdf_filename"]
        if dest.exists():
            present += 1
            continue
        err = download(session, r["pdf_url"], dest)
        if err:
            failures.append({"entity_name": r["entity_name"], "pdf_filename": r["pdf_filename"],
                             "pdf_url": r["pdf_url"], "error": err})
            tqdm.write(f"  [fail] {r['pdf_filename']}: {err}")
        else:
            ok += 1
        time.sleep(DELAY)

    print(f"\n  Downloaded: {ok}   Already present: {present}   Failed: {len(failures)}")
    if failures:
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        out = PDF_DIR / "download_failures.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["entity_name", "pdf_filename", "pdf_url", "error"])
            w.writeheader()
            w.writerows(failures)
        print(f"  Failures listed in {out}")
    print("\nNext:  python src/extract_citations.py")


if __name__ == "__main__":
    main()
