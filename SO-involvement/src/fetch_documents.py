#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_documents.py
==================
Script 1 of 2 — Discover, download, and triage PDF publications from major
power-system organizations (US ISOs/RTOs, NERC, G-PST, ENTSO-E, etc.).

Configuration is loaded from data/input/ (see src/paths.py):
  data/input/organizations.toml      — organization registry with crawl URLs and seed PDFs
  data/input/classification_rules.toml — G-PST area and reference-type classification rules

Outputs (written under data/, see src/paths.py):
  interim/documents.db — SQLite database; one row per discovered URL
                      columns: url, pdf_filename, entity_name, entity_region,
                               gpst_area, has_references, pdf_kept,
                               doc_title, doc_year, processed_at
  pdfs/<entity>/       — downloaded PDFs that contain a references/bibliography section

Workflow per document:
  1. Skip if URL is already in documents.db.
  2. Download the PDF.
  3. Parse with markitdown, check for a references/bibliography section.
  4. Keep PDF if references found; delete otherwise.
  5. Save metadata (url, entity, has_references, …) to documents.db regardless.

After running this script:
  1. Run:  python scripts/export_document_index.py   (documents.db → document_index.csv)
  2. Review document_index.csv by hand, then continue with organize_citations.py

Usage (from SO-involvement/, with the conda env from environment.yml active):
    playwright install chromium   # one-time browser download, needed for JS-rendered sites
    python src/fetch_documents.py
    python src/fetch_documents.py --entities esig nerc g-pst   # subset
    python src/fetch_documents.py --no-fetch                   # skip download, re-triage on-disk PDFs

Some organization sites are JavaScript-rendered (SPA search widgets, React
publication libraries) or block plain HTTP clients outright. For those,
mark the crawl_pages entry in organizations.toml with a third element,
`true`, e.g. ["https://example.com/search#q=ieee", 1, true]. Such pages
are fetched with a real headless Chromium browser (via Playwright) instead
of a raw GET, which also means URL fragments (#...) are honored — a plain
requests.get() never sends the fragment to the server at all.
"""

import argparse
import atexit
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# Python 3.11+ ships tomllib in stdlib; older versions need the 'tomli' package.
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("Error: TOML support not found. Install it with:  pip install tomli")
        sys.exit(1)

# ─── Paths ────────────────────────────────────────────────────────────────────

from paths import INTERIM_DIR, PDF_DIR as PDF_BASE_DIR, DB_PATH, \
    ORGANIZATIONS_TOML, CLASSIFICATION_RULES
DELAY        = 1.2   # seconds between HTTP requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── Load configuration from TOML ────────────────────────────────────────────

def load_organizations() -> dict:
    """Load organization registry from data/input/organizations.toml."""
    path = ORGANIZATIONS_TOML
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    # Convert crawl_pages from list-of-lists to list-of-tuples
    orgs = {}
    for key, org in raw.items():
        orgs[key] = dict(org)
        orgs[key]["crawl_pages"] = [
            (entry[0], entry[1], bool(entry[2]) if len(entry) > 2 else False)
            for entry in org.get("crawl_pages", [])
        ]
    return orgs


def load_classification_rules() -> tuple[dict, list]:
    """
    Load classification rules from data/input/classification_rules.toml.

    Returns:
        gpst_questions  – dict keyed by area (ibrs, stability, …) with
                          'label' and compiled 'pattern'
        ref_type_rules  – list of (type_name, compiled_pattern) in priority order
    """
    path = CLASSIFICATION_RULES
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    gpst_questions = {}
    for area_key, area in raw.get("gpst_areas", {}).items():
        combined = "|".join(area["patterns"])
        gpst_questions[area_key] = {
            "label":   area["label"],
            "pattern": re.compile(combined, re.I),
        }

    ref_type_rules = []
    for entry in raw.get("reference_types", []):
        combined = "|".join(entry["patterns"])
        ref_type_rules.append((entry["type"], re.compile(combined, re.I)))

    return gpst_questions, ref_type_rules


# Load at import time so every function below can use them
ORGANIZATIONS           = load_organizations()
GPST_QUESTIONS, _ = load_classification_rules()

# ─── G-PST auto-classification ────────────────────────────────────────────────

def auto_classify_gpst(filename: str, text_snippet: str = "") -> str:
    combined = filename + " " + text_snippet[:3000]
    for area, meta in GPST_QUESTIONS.items():
        if meta["pattern"].search(combined):
            return area
    return "planning"


# ─── Reference-section detector (used for triage and full extraction) ─────────

REF_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:\d+\.?\s+)?"
    r"(?:References|Bibliography|Works Cited|Notes|Endnotes|Footnotes"
    r"|Literatur(?:verzeichnis)?|Quellenverzeichnis|Quellen|Referenzen"
    r"|Bibliographie|Literaturliste|Literaturangaben"
    r"|Références|Bibliographie|Lista de referencias|Referencias)"
    r"[ \t]*$",
    re.I,
)

# Matches a numbered reference line [N] that contains "IEEE" on the same line.
# Used as a fallback when no formal References header exists.
_IEEE_NUMBERED_RE = re.compile(
    r"(?m)^\s*\[\d{1,3}\]\s+.{0,200}IEEE",
    re.IGNORECASE,
)


def has_references_section(text: str) -> bool:
    """
    Return True if the document has a formal references heading OR contains
    ≥2 numbered [N] reference entries that cite IEEE — catching technical
    operator documents that list references without a section header.
    """
    if REF_HEADER_RE.search(text):
        return True
    return len(_IEEE_NUMBERED_RE.findall(text)) >= 2


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session, url, timeout=20):
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    [warn] GET {url}: {e}")
        return None


# ─── Headless-browser fallback (JS-rendered / bot-gated sites) ───────────────
#
# A handful of organization sites either render their document listings via
# client-side JS (SPA search widgets, React publication libraries) or block
# plain HTTP clients (403/503, TLS fingerprinting). For those, organizations.toml
# marks the crawl_pages entry with a `render=true` flag, and we fetch it with a
# real headless Chromium instance instead of `requests`. This also lets a URL
# fragment (#q=...) reach client-side JS that reads window.location.hash —
# a plain GET never transmits the fragment to the server at all.

_PLAYWRIGHT = None
_BROWSER = None


def _get_browser():
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(
                "Error: playwright not installed but a crawl_pages entry has "
                "render=true. Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
            sys.exit(1)
        _PLAYWRIGHT = sync_playwright().start()
        _BROWSER = _PLAYWRIGHT.chromium.launch(headless=True)
        atexit.register(_close_browser)
    return _BROWSER


def _close_browser():
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        _BROWSER.close()
        _BROWSER = None
    if _PLAYWRIGHT is not None:
        _PLAYWRIGHT.stop()
        _PLAYWRIGHT = None


def render_get(url: str, timeout_ms: int = 20000) -> str | None:
    """
    Fetch a page's fully-rendered HTML via headless Chromium. Returns None on failure.

    Waits for DOMContentLoaded (fast, reliable) rather than networkidle —
    many sites keep persistent connections open (analytics, chat widgets,
    trackers) that make networkidle time out even once the content we want
    has long since rendered.
    """
    browser = _get_browser()
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)  # let async/JS-rendered content settle
        return page.content()
    except Exception as e:
        print(f"    [warn] render GET {url}: {e}")
        try:
            return page.content()
        except Exception:
            return None
    finally:
        page.close()


# ─── SQLite database ─────────────────────────────────────────────────────────

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    url             TEXT PRIMARY KEY,
    pdf_filename    TEXT,
    entity_name     TEXT,
    entity_region   TEXT,
    gpst_area       TEXT,
    has_references  INTEGER NOT NULL DEFAULT 0,
    pdf_kept        INTEGER NOT NULL DEFAULT 0,
    doc_title       TEXT,
    doc_year        TEXT,
    processed_at    TEXT
);
"""


def init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(DB_SCHEMA)
    conn.commit()
    return conn


def is_url_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM documents WHERE url = ?", (url,)).fetchone()
    return row is not None


def save_document(conn: sqlite3.Connection, *, url: str, pdf_filename: str,
                  entity_name: str, entity_region: str, gpst_area: str,
                  has_references: bool, pdf_kept: bool,
                  doc_title: str = "", doc_year: str = "") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (url, pdf_filename, entity_name, entity_region, gpst_area,
             has_references, pdf_kept, doc_title, doc_year, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            url, pdf_filename, entity_name, entity_region, gpst_area,
            int(has_references), int(pdf_kept),
            doc_title, doc_year,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# ─── markitdown parsing ───────────────────────────────────────────────────────

def parse_with_markitdown(pdf_path: Path) -> str:
    """Convert PDF to markdown text via markitdown CLI. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["markitdown", str(pdf_path)],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout
    except Exception as e:
        print(f"    [warn] markitdown {pdf_path.name}: {e}")
        return ""


def _title_year(path: Path, text: str) -> tuple[str, str]:
    """Extract a best-guess title (first non-blank line) and year from text."""
    lines = [l.strip() for l in text[:3000].splitlines() if l.strip()]
    title = lines[0] if lines else path.stem
    m = re.search(r"\b(20\d{2}|19\d{2})\b", text[:600])
    return title[:150], (m.group(1) if m else "")


# ─── Step 1: Discover PDFs ────────────────────────────────────────────────────

def _paginate_url(base: str, page: int) -> str:
    """
    Pagination scheme used by ESIG and similar WordPress sites:
      page 1 → base_url (as-is)
      page N → base_url with query-0-page=N appended before any existing ?...
    Works for both bare URLs and URLs already containing ?cst etc.
    """
    if page == 1:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}query-0-page={page}"


def _extract_filename(url: str) -> str:
    """
    Return a usable local filename for a PDF URL. Normally the last path
    segment. Some CMS platforms (e.g. NYISO's Liferay-based document library)
    append a version UUID *after* the filename, e.g.
    ".../Phase_1_Final_Report.pdf/9b1b6673-...-347b" — the last segment there
    has no extension at all, which would silently save the file with no
    ".pdf" suffix (breaking organize_citations.py's file discovery, which
    only picks up *.pdf). Falls back to the nearest earlier segment that
    actually ends in .pdf; if none exists, behaves as before (last segment).
    """
    segments = url.split("/")
    last = segments[-1]
    if last.lower().endswith(".pdf"):
        return last
    for seg in reversed(segments[:-1]):
        if seg.lower().endswith(".pdf"):
            return seg
    return last


def _fetch_html(page_url: str, session: requests.Session,
                render: bool, timeout: int = 20) -> str | None:
    """Fetch a page's HTML via headless browser (render=True) or plain GET."""
    if render:
        return render_get(page_url, timeout_ms=timeout * 1000)
    r = safe_get(session, page_url, timeout=timeout)
    return r.text if r else None


RENDER_FOLLOW_CAP = 15  # max same-domain links to follow one level deep in render mode
STATIC_FOLLOW_CAP = 40  # same, for plain-GET mode — cheaper per-request but still bounded

# In render mode, a rendered cookie-consent widget (IAB TCF "vendor list"
# style) can inject dozens of <a href="*.pdf"> links to unrelated third-party
# ad-tech vendors' privacy policies — none hosted on the org's own domain, so
# the entity_domain check doesn't catch them either (they're picked up by the
# unconditional top-level .pdf match, which intentionally allows legitimate
# same-org CDN subdomains like assets.example.com or cdn.example.com).
# Found the hard way: a Prysmian crawl returned 41 "documents", 39 of which
# were random ad-tech vendors' privacy policies from a consent widget.
JUNK_PDF_RE = re.compile(
    r"privacy|datenschutz|prywatnosci|privacidad"
    r"|legitimate.?interest|\bLIAs?\b|LIAs?[_.-]|\bcookies?\b|consent|\bgdpr\b"
    r"|terms.?of.?(service|use)|accessibility.?statement",
    re.I,
)


def crawl_for_pdfs(page_url: str, session: requests.Session,
                   entity_domain: str = "", render: bool = False) -> set:
    """
    Fetch one HTML page and collect all .pdf hrefs.
    Follows links one level deep if they are on the same domain
    (to handle 'view report' landing pages that link to the actual PDF).

    Same-domain follow-ups are capped (RENDER_FOLLOW_CAP / STATIC_FOLLOW_CAP)
    since an org's site nav/footer can otherwise turn one crawl_pages entry
    into dozens or hundreds of sequential follow-up requests — a real
    500+ second runtime was observed on one org's page before this cap was
    added. Render-mode is capped tighter since each follow-up is a full
    browser page load, not a cheap GET.
    """
    pdfs = set()
    html = _fetch_html(page_url, session, render)
    if not html:
        return pdfs

    soup = BeautifulSoup(html, "html.parser")
    followed = 0
    follow_cap = RENDER_FOLLOW_CAP if render else STATIC_FOLLOW_CAP
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(page_url, href)
        if full.lower().endswith(".pdf"):
            if not JUNK_PDF_RE.search(full):
                pdfs.add(full)
        elif entity_domain and entity_domain in full:
            if any(x in full for x in ["#", "mailto:", "javascript:"]):
                continue
            if followed >= follow_cap:
                continue
            followed += 1
            time.sleep(0.2)
            html2 = _fetch_html(full, session, render, timeout=12)
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")
            for a2 in soup2.find_all("a", href=True):
                f2 = urljoin(full, a2["href"].strip())
                if f2.lower().endswith(".pdf") and not JUNK_PDF_RE.search(f2):
                    pdfs.add(f2)

    return pdfs


def discover_entity_pdfs(entity_key: str, org: dict,
                         session: requests.Session) -> list:
    """Return deduplicated list of PDF URLs for one organisation."""
    found = set(org.get("seed_pdfs", []))
    domain = urlparse(org["crawl_pages"][0][0]).netloc if org.get("crawl_pages") else ""

    for base_url, max_pages, render in org.get("crawl_pages", []):
        for page in range(1, max_pages + 1):
            url = _paginate_url(base_url, page)
            print(f"    crawling {'[render] ' if render else ''}{url}")
            found.update(crawl_for_pdfs(url, session, domain, render=render))
            time.sleep(0.5)

    # Deduplicate by filename — keep longest URL on collision. Only collapse
    # on the trailing path segment when it looks like a real filename (has
    # an extension); ID-based document endpoints like ".../document/<id>/download"
    # all share the same generic trailing segment despite being distinct
    # documents, so those are keyed on the full URL instead — otherwise every
    # document but one silently vanishes here (found the hard way: National
    # Grid ESO's 3 seed PDFs all end in "/download" and collapsed to 1).
    by_name: dict[str, str] = {}
    for url in found:
        tail = _extract_filename(url).lower()
        key = tail if "." in tail else url
        if key not in by_name or len(url) > len(by_name[key]):
            by_name[key] = url

    return sorted(by_name.values())


# ─── Step 2: Download PDFs ────────────────────────────────────────────────────

def download_pdf(url: str, dest: Path, session: requests.Session) -> bool:
    if dest.exists():
        return True
    try:
        r = session.get(url, timeout=40, stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        time.sleep(DELAY)
        return True
    except Exception as e:
        print(f"    [warn] download {dest.name}: {e}")
        return False


# ─── Summary ──────────────────────────────────────────────────────────────────

def _print_summary(entity_stats: dict):
    W = 84
    print(f"\n{'='*W}")
    print(f"  FETCH SUMMARY")
    print(f"{'='*W}")
    hdr = (f"  {'Organization':<22} {'Region':<10}"
           f" {'Disc':>5} {'Skip':>5} {'DL':>5} {'Fail':>5} {'Keep':>5} {'Drop':>5}")
    print(hdr)
    print(f"  {'-'*22} {'-'*10} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5}")
    for s in entity_stats.values():
        flag = "  ⚠" if s["failed"] > 0 else ""
        print(
            f"  {s['name']:<22} {s['region']:<10}"
            f" {s['discovered']:>5} {s['skipped']:>5}"
            f" {s['downloaded']:>5} {s['failed']:>5}"
            f" {s['kept']:>5} {s['dropped']:>5}"
            f"{flag}"
        )
    print(f"  {'-'*22} {'-'*10} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5}")
    def _sum(key): return sum(s[key] for s in entity_stats.values())
    print(
        f"  {'TOTAL':<22} {'':<10}"
        f" {_sum('discovered'):>5} {_sum('skipped'):>5}"
        f" {_sum('downloaded'):>5} {_sum('failed'):>5}"
        f" {_sum('kept'):>5} {_sum('dropped'):>5}"
    )
    print(f"{'='*W}")
    print(f"  ⚠ = one or more broken/failed links")


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Fetch & extract citations from power-system org PDFs")
    p.add_argument("--entities", nargs="+", metavar="KEY",
                   help="Limit to these entity keys (e.g. esig nerc g-pst). Default: all.")
    p.add_argument("--entities-file", metavar="FILE",
                   help="Path to a text file with one entity key per line (# comments allowed).")
    p.add_argument("--no-fetch", action="store_true",
                   help="Skip downloading; only re-extract from already-downloaded PDFs.")
    return p.parse_args()


def main():
    args = parse_args()
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    PDF_BASE_DIR.mkdir(parents=True, exist_ok=True)

    keys = None
    if args.entities_file:
        path = Path(args.entities_file)
        keys = [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    elif args.entities:
        keys = args.entities

    selected = (
        {k: ORGANIZATIONS[k] for k in keys if k in ORGANIZATIONS}
        if keys else ORGANIZATIONS
    )
    if not selected:
        print(f"Unknown entity keys. Choose from: {list(ORGANIZATIONS)}")
        return

    conn    = init_db(DB_PATH)
    session = make_session()

    entity_stats: dict = {}

    for entity_key, org in selected.items():
        print(f"\n{'='*60}")
        print(f"  {org['name']}  [{org['region']}]")
        print(f"{'='*60}")

        pdf_dir = PDF_BASE_DIR / entity_key
        pdf_dir.mkdir(exist_ok=True)

        stats = {
            "name":       org["name"],
            "region":     org["region"],
            "discovered": 0,
            "skipped":    0,
            "downloaded": 0,
            "failed":     0,
            "kept":       0,
            "dropped":    0,
        }

        # 1 — Discover
        if not args.no_fetch:
            print("  [1] Discovering PDFs ...")
            pdf_urls = discover_entity_pdfs(entity_key, org, session)
        else:
            pdf_urls = [
                str(p.relative_to(PDF_BASE_DIR.parent))
                for p in pdf_dir.glob("*.pdf")
            ] or org.get("seed_pdfs", [])

        stats["discovered"] = len(pdf_urls)
        print(f"      {len(pdf_urls)} PDFs found")

        # 2 — Download → parse → triage
        print("  [2] Downloading and triaging ...")
        for url in tqdm(pdf_urls, desc=f"  {entity_key}", leave=False):
            fname = _extract_filename(url)
            dest  = pdf_dir / fname

            # Skip URLs already in the database
            if is_url_seen(conn, url):
                stats["skipped"] += 1
                continue

            # Download
            if not args.no_fetch:
                ok = download_pdf(url, dest, session)
                if not ok:
                    stats["failed"] += 1
                    save_document(
                        conn, url=url, pdf_filename=fname,
                        entity_name=org["name"], entity_region=org["region"],
                        gpst_area=auto_classify_gpst(fname),
                        has_references=False, pdf_kept=False,
                    )
                    continue
                stats["downloaded"] += 1

            if not dest.exists():
                stats["failed"] += 1
                continue

            # Parse with markitdown and check for references section.
            # has_references_section() is a cheap, narrow heuristic (a
            # formal "References" header, or an IEEE-citing numbered list)
            # — it has known false negatives (e.g. a real reference list
            # that cites NREL/NYISO/CPUC instead of IEEE, or one mangled by
            # column-layout text extraction). It's kept only as an
            # informational triage flag now; the PDF is NEVER deleted based
            # on it. Deleting here was destructive and irreversible — a
            # false negative meant the document was gone before
            # extract_citations.py's much more thorough detection (which
            # handles two-column layouts and doesn't require IEEE) ever got
            # a chance to look at it. That silently dropped real,
            # citation-bearing documents (confirmed: two IESO reports with
            # 37 and 29 real references each were deleted this way).
            md_text   = parse_with_markitdown(dest)
            has_refs  = has_references_section(md_text)
            gpst      = auto_classify_gpst(fname, md_text[:3000])
            title, year = _title_year(dest, md_text)

            stats["kept" if has_refs else "dropped"] += 1

            save_document(
                conn, url=url, pdf_filename=fname,
                entity_name=org["name"], entity_region=org["region"],
                gpst_area=gpst, has_references=has_refs, pdf_kept=True,
                doc_title=title, doc_year=year,
            )
            tqdm.write(
                f"    {'KEEP' if has_refs else 'DROP'}  {fname}  [{gpst}]"
            )

        entity_stats[entity_key] = stats

    conn.close()

    # 3 — Summary
    _print_summary(entity_stats)
    print(f"\nDone.  Next step:  python scripts/export_document_index.py")
    print(f"       Database:   {DB_PATH}")


if __name__ == "__main__":
    main()
