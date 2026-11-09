"""Step 3: classify every paper, writing results/so_classification.csv (one row per DOI).

Columns (flags are 0/1):
  doi, publication_title, publication_year
  IS_PAPER            research paper or letter (not front matter, discussion, correction, ...)
  NOT_PAPER_REASON    why IS_PAPER is 0
  HAS_AFFILIATIONS    at least one author affiliation is listed (needed to check for SOs)
  SO_TSO              an author works at a TSO / ISO / RTO
  SO_GRIDCO           ... at a state-owned or vertically integrated grid company
  SO_DSO              ... at a distribution utility
  SO_RESEARCH         ... at a research institute owned by one of the above
  SO_DIRECT           SO_TSO or SO_GRIDCO or SO_DSO
  HAS_SO              SO_DIRECT or SO_RESEARCH
  SO_BORDERLINE_ONLY  HAS_SO, but every match is an organisation flagged borderline
  SO_PARENTS          parent organisations, e.g. "State Grid Corporation of China (SGCC)"
  SO_ORGS             reference-list entries that matched
  SO_EVIDENCE         the matching affiliations, separated by " | "
Shares should be computed over IS_PAPER == 1 and HAS_AFFILIATIONS == 1.
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import CLASSIFICATION_CSV, DIRECT_TYPES, Matcher, load_papers, split_units  # noqa: E402

# Titles of items that are not research papers. Rows with no authors at all are front
# matter (tables of contents, society pages, adverts) or, rarely, committee reports
# without named authors; they are excluded by the first rule in classify_item().
NOT_PAPER_TITLES = [
    ("front matter", r"^\W*(Table of contents|Contents|Front cover|Back cover|Blank page|Breaker page|Masthead)\b"
                     r"|Information for authors|publication information|society information|^\W*\d{4} Index\b"
                     r"|^\W*Index\b|copyright form|\[advertisement\]|Call for (papers|nominations|participation)"),
    ("discussion or closure", r"^\W*(Discussions?|Closure)\b|Closure (to|on)\b|and closure\b|\[and reply\]"
                              r"|^\W*(Reply|Response) to\b|^\W*Comments? (on|to)\b|^\W*Authors?'? reply"),
    # "Correction of current transformer ..." is a technical paper; only "Correction(s) to".
    ("correction or retraction", r"^\W*Corrections? (to|on)\b|^\W*Corrections?\s*[:\[]|^\W*Corrections?\W*$"
                                 r"|^\W*(Errat(um|a)|Addendum|Corrigendum|Retraction|Retracted"
                                 r"|Expression of concern)\b|Retraction Notice|^\W*Notice of (Retraction|Removal)"),
    ("editorial", r"Editorial\b|^\W*Foreword\b|^\W*Preface\b|^\W*Introduction to the special"
                  r"|^\W*(Guest )?Editor'?s?'? (note|message|introduction)|^\W*Message from"
                  r"|^\W*In memoriam|^\W*Obituary|^\W*Memorial\b|^\W*Tribute\b|^\W*Special (Section|Issue) on"),
    ("awards or reviewer lists", r"Reviewers\b|Best Papers?\b|Outstanding (Reviewers|Associate Editors|Papers|Editors)"
                                 r"|^\W*(PES )?Awards?\b|Prize Paper|Acknowledg(e)?ments? (of|to) Reviewers"),
]
NOT_PAPER_TITLES = [(reason, re.compile(p, re.IGNORECASE)) for reason, p in NOT_PAPER_TITLES]
FLAGS = ["SO_TSO", "SO_GRIDCO", "SO_DSO", "SO_RESEARCH", "SO_DIRECT", "HAS_SO", "SO_BORDERLINE_ONLY"]


def classify_item(title: str, authors: str) -> str:
    """Empty string for a research paper, otherwise the reason it is not one."""
    if not authors.strip():
        return "no authors listed"
    for reason, pattern in NOT_PAPER_TITLES:
        if pattern.search(title):
            return reason
    return ""


def classify_affiliations(affiliations: str, matcher: Matcher) -> dict:
    hits, evidence = [], []
    for unit in split_units(affiliations):
        unit_hits = matcher.match(unit)
        if unit_hits:
            hits += unit_hits
            evidence.append(unit)
    types = {h["type"] for h in hits}
    row = {f"SO_{t}": int(t in types) for t in ["TSO", "GRIDCO", "DSO", "RESEARCH"]}
    row["SO_DIRECT"] = int(bool(types & set(DIRECT_TYPES)))
    row["HAS_SO"] = int(bool(hits))
    row["SO_BORDERLINE_ONLY"] = int(bool(hits) and all(h["borderline"] for h in hits))
    row["SO_PARENTS"] = "; ".join(sorted({h["parent"] for h in hits}))
    row["SO_ORGS"] = "; ".join(sorted({h["name"] for h in hits}))
    row["SO_EVIDENCE"] = " | ".join(evidence)
    return row


def main() -> None:
    papers = load_papers()
    matcher = Matcher()
    reasons = [classify_item(t, a) for t, a in zip(papers["title"], papers["authors"])]
    out = pd.DataFrame({
        "doi": papers["doi"],
        "publication_title": papers["publication_title"],
        "publication_year": papers["publication_year"],
        "IS_PAPER": [int(not r) for r in reasons],
        "NOT_PAPER_REASON": reasons,
        "HAS_AFFILIATIONS": (papers["author_affiliations"].str.strip() != "").astype(int),
    })
    so = pd.DataFrame([classify_affiliations(a, matcher) for a in papers["author_affiliations"]])
    out = pd.concat([out, so], axis=1)
    CLASSIFICATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CLASSIFICATION_CSV, index=False)

    not_paper = out[out["IS_PAPER"] == 0]
    counted = out[(out["IS_PAPER"] == 1) & (out["HAS_AFFILIATIONS"] == 1)]
    print(f"Wrote {CLASSIFICATION_CSV.name}: {len(out)} rows")
    print(f"Not papers: {len(not_paper)} {not_paper['NOT_PAPER_REASON'].value_counts().to_dict()}")
    print(f"Papers without affiliations: {((out['IS_PAPER'] == 1) & (out['HAS_AFFILIATIONS'] == 0)).sum()}")
    print(f"Papers counted (research papers with affiliations): {len(counted)}")
    for col in FLAGS:
        print(f"  {col}: {counted[col].sum()} ({100 * counted[col].mean():.2f}%)")


if __name__ == "__main__":
    main()
