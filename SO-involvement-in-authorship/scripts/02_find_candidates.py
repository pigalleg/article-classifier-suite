"""Step 2: list affiliations that the reference lists do not cover yet, for review.

Reads results/review/affiliation_units.csv (step 1) and the reference lists. Writes, to
results/review/:
  candidates.csv     non-university affiliations with grid-related words that match neither
                     an SO nor an excluded organisation (review these first)
  other_units.csv    the remaining unmatched non-university affiliations (skim)
  matched_units.csv  affiliations matched to an SO, to check for false matches
Each review round adds organisations to data/reference/so_organisations.csv or
data/reference/excluded_organisations.csv; this step then runs again, until candidates.csv
holds nothing relevant.
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import ACADEMIC, GRID_WORDS, REVIEW, Matcher  # noqa: E402


def organisation_key(unit: str) -> str:
    """The comma-separated part that names the organisation, for grouping spellings."""
    parts = [p.strip() for p in unit.split(",")]
    hits = [p for p in parts if GRID_WORDS.search(p)] or parts[:1]
    key = re.sub(r"\b(inc|ltd|limited|co|corp|llc|plc|s\.?a|ag|gmbh)\b\.?", "", hits[0], flags=re.I)
    return re.sub(r"[^\w&]+", " ", key).strip().lower()


def main() -> None:
    matcher = Matcher()
    units = pd.read_csv(REVIEW / "affiliation_units.csv", keep_default_na=False)
    matches = units["unit"].map(matcher.match)
    matched = units[matches.str.len() > 0].copy()
    matched["orgs"] = matches[matched.index].map(
        lambda hits: "; ".join(f"{h['name']} [{h['type']}{'*' if h['borderline'] else ''}]" for h in hits)
    )
    matched.to_csv(REVIEW / "matched_units.csv", index=False)

    rest = units[(matches.str.len() == 0)]
    rest = rest[rest["unit"].map(lambda u: not matcher.excluded_names(u))]
    rest = rest[~rest["unit"].map(lambda u: bool(ACADEMIC.search(u)))]
    is_grid = rest["unit"].map(lambda u: bool(GRID_WORDS.search(u)))
    for name, part in (("candidates", rest[is_grid]), ("other_units", rest[~is_grid])):
        part = part.assign(key=part["unit"].map(organisation_key))
        grouped = (part.groupby("key")
                   .agg(papers=("papers", "sum"), spellings=("unit", "size"), example=("unit", "first"))
                   .sort_values("papers", ascending=False))
        grouped.to_csv(REVIEW / f"{name}.csv")
        print(f"{name}: {len(part)} affiliations, {len(grouped)} organisation keys, "
              f"{part['papers'].sum()} paper-affiliation pairs")
    print(f"matched to an SO: {len(matched)} affiliations, {matched['papers'].sum()} pairs")


if __name__ == "__main__":
    main()
