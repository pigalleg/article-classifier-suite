"""Paths, data loading and the affiliation matcher shared by the scripts in ../scripts."""

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PAPERS_CSV = ROOT / "data" / "ieee_papers.csv"
REFERENCE = ROOT / "data" / "reference"
SO_ORGANISATIONS_CSV = REFERENCE / "so_organisations.csv"
EXCLUDED_ORGANISATIONS_CSV = REFERENCE / "excluded_organisations.csv"
INSTITUTE_NAMES_CSV = REFERENCE / "research_institute_names.csv"
RESULTS = ROOT / "results"
CLASSIFICATION_CSV = RESULTS / "so_classification.csv"
TOP_ORGANISATIONS = RESULTS / "top_organisations"
FIGURES = RESULTS / "figures"
REVIEW = RESULTS / "review"  # review lists from steps 1-2; regenerated, not versioned


def load_papers() -> pd.DataFrame:
    """One row per DOI: doi, title, authors, author_affiliations, publication_title,
    publication_year."""
    papers = pd.read_csv(PAPERS_CSV, dtype=str, keep_default_na=False)
    if papers["doi"].duplicated().any():
        raise ValueError(f"Duplicate DOIs in {PAPERS_CSV.name}")
    papers["publication_year"] = papers["publication_year"].astype(int)
    return papers


def split_units(affiliations: str) -> list[str]:
    """One string per author affiliation; IEEE separates them with ';'. Duplicates removed."""
    units = [re.sub(r"\s+", " ", u).strip() for u in affiliations.split(";")]
    return list(dict.fromkeys(u for u in units if u))


# --- Finding affiliations to review --------------------------------------------------------

# Universities and similar. Used only to shorten the review list in step 2; a university
# affiliation that names an SO is still matched in step 3.
ACADEMIC = re.compile(
    r"univ|college|polytechn|politec|[ée]cole|hochschule|\bETH\b|EPFL|\bKTH\b|"
    r"institute of technology|academy of sciences|\bIIT\b|faculty|school of|tecnol[oó]gico|"
    r"\bUSP\b|UNICAMP|\bUFSC\b|\bUFRJ\b|\bCOPPE\b|instituto superior|Technion|\bNTNU\b|\bDTU\b|"
    r"technische|technical university",
    re.IGNORECASE,
)
# Words that suggest a utility, grid company or system operator.
GRID_WORDS = re.compile(
    r"grid|power supply|electric(al|ity)? power (company|co\b|corp|bureau|board|authority)|"
    r"power (company|corporation|co\b|corp\b|authority|administration|board|commission|pool|"
    r"exchange|cooperative|district|systems? operat)|"
    r"electric(al|ity)? (company|corporation|co\b|corp\b|authority|board|commission|cooperative|"
    r"membership|utilit|department|supply|distribution|transmission)|"
    r"utilit|transmission|distribution|system operator|independent system|\bISO\b|\bTSO\b|"
    r"\bDSO\b|\bRTO\b|dispatch|despatch|control cent|hydro|light (and|&) power|power (and|&) light|"
    r"gas (and|&) electric|edison|energy (company|corporation|networks?|delivery|authority)|"
    r"networks? (inc|ltd|limited|plc|operator)|netz|r[ée]seau|red el[ée]ctrica|\brede\b|elektro|"
    r"energi|electricidad|electricidade|eletr|elettric|energia|public service|municipal|"
    r"power (and|&) water|water (and|&) (power|electricity)|\bpower\b",
    re.IGNORECASE,
)


# --- Matching affiliations against the reference lists ------------------------------------

SO_TYPES = ["TSO", "GRIDCO", "DSO", "RESEARCH"]
DIRECT_TYPES = ["TSO", "GRIDCO", "DSO"]
# An affiliation of a grid company that names a research body (institute, laboratory,
# research or technology centre, academy) is that company's research arm, not its operating
# staff. "Research Department" or "R&D Department" inside the company stays direct.
RESEARCH_BODY = re.compile(
    r"institut|laborator|\blab\b|\blabs\b|academy|"
    r"(research|R ?& ?D|science|technology|innovation)( (and|&) development)?( and technology)? "
    r"(cent(er|re)|base)",
    re.IGNORECASE,
)


def _compile(pattern: str, case_sensitive) -> re.Pattern:
    return re.compile(pattern, 0 if str(case_sensitive).strip() == "1" else re.IGNORECASE)


class Matcher:
    """Finds SO organisations in one affiliation string.

    Excluded organisations marked mask=1 are blanked out of the text first, so that, for
    example, a university chair funded by Hydro-Quebec does not count as Hydro-Quebec. Then
    every SO pattern is tried; an optional "unless" pattern vetoes a match (used, e.g., to
    split Chinese grid companies between State Grid and China Southern Power Grid by province).
    """

    def __init__(self) -> None:
        orgs = pd.read_csv(SO_ORGANISATIONS_CSV, dtype=str, keep_default_na=False)
        excluded = pd.read_csv(EXCLUDED_ORGANISATIONS_CSV, dtype=str, keep_default_na=False)
        bad_types = set(orgs["type"]) - set(SO_TYPES)
        if bad_types:
            raise ValueError(f"Unknown types in {SO_ORGANISATIONS_CSV.name}: {bad_types}")
        self.orgs = [
            (row, _compile(row["pattern"], row["case_sensitive"]),
             _compile(row["unless"], row["case_sensitive"]) if row["unless"] else None)
            for _, row in orgs.iterrows()
        ]
        self.excluded = [
            (row, _compile(row["pattern"], row["case_sensitive"])) for _, row in excluded.iterrows()
        ]
        self.masks = [p for row, p in self.excluded if row["mask"] == "1"]

    def masked(self, unit: str) -> str:
        for pattern in self.masks:
            unit = pattern.sub(" ", unit)
        return unit

    def match(self, unit: str) -> list[dict]:
        """SO organisations named in one affiliation, with the type that applies here."""
        text = self.masked(unit)
        hits = {}
        for row, pattern, unless in self.orgs:
            found = pattern.search(text)
            if found and not (unless and unless.search(text)):
                kind = row["type"]
                if kind in DIRECT_TYPES and RESEARCH_BODY.search(text):
                    kind = "RESEARCH"
                hits.setdefault(row["name"], {
                    "name": row["name"], "parent": row["parent"] or row["name"], "type": kind,
                    "borderline": row["borderline"] == "1", "text": found.group(0),
                })
        # "Hydro-Quebec/IREQ" or "EDF R&D": the research arm, not also its parent company.
        research_parents = {h["parent"] for h in hits.values() if h["type"] == "RESEARCH"}
        return [h for h in hits.values()
                if not (h["type"] in DIRECT_TYPES and h["parent"] in research_parents)]

    def excluded_names(self, unit: str) -> list[str]:
        """Excluded organisations named in one affiliation (already reviewed, not SOs)."""
        return [row["name"] for row, pattern in self.excluded if pattern.search(unit)]
