"""Step 1: list every distinct author affiliation with the number of papers it appears in.

Writes results/review/affiliation_units.csv (unit, papers), which step 2 uses to find
organisations the reference lists do not cover yet.
"""

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import REVIEW, load_papers, split_units  # noqa: E402


def main() -> None:
    papers = load_papers()
    counts = Counter(u for a in papers["author_affiliations"] for u in split_units(a))
    units = pd.DataFrame(counts.items(), columns=["unit", "papers"])
    units = units.sort_values(["papers", "unit"], ascending=[False, True])
    REVIEW.mkdir(parents=True, exist_ok=True)
    units.to_csv(REVIEW / "affiliation_units.csv", index=False)
    print(f"{len(papers)} papers, {(papers['author_affiliations'] == '').sum()} without affiliations")
    print(f"{len(units)} distinct affiliations, {units['papers'].sum()} paper-affiliation pairs")


if __name__ == "__main__":
    main()
