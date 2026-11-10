"""Step 4: which organisations publish most, counted in papers (one paper counts once per
organisation, however many of its authors work there).

Writes, to results/top_organisations/:
  so_direct.csv                SOs and grid companies (TSO, GRIDCO, DSO)
  so_direct_variants.csv       the spellings merged into each of them
  so_research.csv              SO-owned research institutes, grouped by owner
  so_research_institutes.csv   the same, one row per institute (with its owner)
  universities.csv             universities and similar academic institutions
  universities_variants.csv    the spellings merged into each university
Counts only research papers with affiliations (IS_PAPER and HAS_AFFILIATIONS). The files are
UTF-8 with a byte-order mark, so Excel shows accented names correctly.

Names are merged in two ways:
- Alias lists in data/reference/ (first matching row wins): university_names.csv for the
  universities at the top of the table, company_names.csv for companies within group entries
  of so_organisations.csv ("US investor-owned utilities", ...), and
  research_institute_names.csv for research institutes. They merge spellings, acronyms and
  renamings of the same institution; separate campuses and merged or acquired institutions
  stay separate.
- Everything else is grouped by a normalised name (accents, case, punctuation, "the", "of",
  postcodes and bracketed acronyms ignored) and shown under its most common spelling. Names
  that differ in more than that (e.g. an abbreviation) can still be listed twice further down
  the table.
Single-organisation entries of so_organisations.csv are reported by name (research institutes
by their parent, e.g. all State Grid institutes as State Grid). Provincial State Grid and CSG
institutes not in research_institute_names.csv are named by province; the rest fall back to
"<owner>: other research units".
"""

import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import (  # noqa: E402
    CLASSIFICATION_CSV, DIRECT_TYPES, REFERENCE, TOP_ORGANISATIONS, Matcher, load_papers,
    split_units,
)

TOP_N = 10


def load_aliases(file_name: str) -> list[tuple[str, re.Pattern]]:
    table = pd.read_csv(REFERENCE / file_name, keep_default_na=False)
    return [(row.iloc[0], re.compile(row["pattern"], re.IGNORECASE)) for _, row in table.iterrows()]


INSTITUTE_ALIASES = load_aliases("research_institute_names.csv")
UNIVERSITY_ALIASES = load_aliases("university_names.csv")
COMPANY_ALIASES = load_aliases("company_names.csv")
SGCC = "State Grid Corporation of China (SGCC)"
CSG = "China Southern Power Grid (CSG)"
PROVINCES = re.compile(
    r"\b(Beijing|Tianjin|Shanghai|Chongqing|Hebei|Shanxi|Liaoning|Jilin|Heilongjiang|Jiangsu|Zhejiang|"
    r"Anhui|Fujian|Jiangxi|Shandong|Henan|Hubei|Hu.?nan|Guangdong|Hainan|Sichuan|Guizhou|Yunnan|Shaanxi|"
    r"Gansu|Qinghai|Guangxi|Tibet|Ningxia|Xinjiang|Jibei|Mengdong|East Inner Mongolia|"
    # Sub-provincial companies (Shenzhen, Guangzhou: CSG) and cities whose province is clear.
    r"Shenzhen|Guangzhou|Huanggang|Ningbo|Jiaxing|Hangzhou|Lianyungang|Suzhou|Changsha)\b", re.IGNORECASE)
CITY_PROVINCE = {"Huanggang": "Hubei", "Ningbo": "Zhejiang", "Jiaxing": "Zhejiang", "Hangzhou": "Zhejiang",
                 "Lianyungang": "Jiangsu", "Suzhou": "Jiangsu", "Changsha": "Hunan"}
# Entries of so_organisations.csv that group several companies; reported by matched name.
GROUP_ENTRY = re.compile(r"companies|utilities|operators|groups|agencies|cooperatives|TSOs|boards"
                         r"|Ministries|distributors|DSOs|divisions|unnamed", re.IGNORECASE)
UNIVERSITY = re.compile(r"univ|universit|college|polytechn|politec|hochschule|institute of technology"
                        r"|\b(ETH|EPFL|KTH|DTU|NTNU|MIT|IIT|UPC)\b|Tsinghua|Technion", re.IGNORECASE)


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as Windows-1252 ("Ã©" for "é"); a few IEEE records."""
    if re.search(r"Ã|â€", text):
        try:
            return text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def fold(text: str) -> str:
    """Accents removed (École -> Ecole), for matching the alias patterns."""
    return unicodedata.normalize("NFKD", fix_mojibake(text)).encode("ascii", "ignore").decode()


def name_key(name: str) -> str:
    """Normalised name for grouping spellings of names without an alias."""
    key = fold(name).lower().replace("&", " and ")
    key = re.sub(r"\(.*?\)|\b\w*\d\w*\b", " ", key)  # bracketed acronyms, postcodes
    key = re.sub(r"\buniv\b", "university", key)
    key = re.sub(r"[^a-z]+", " ", key)
    key = re.sub(r"\b(the|of|and|at|de|di|del|der|inc|ltd|co|corp|company|corporation)\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def first_alias(text: str, aliases: list[tuple[str, re.Pattern]]) -> str | None:
    folded = fold(text)
    return next((name for name, pattern in aliases if pattern.search(folded)), None)


class NameCounter:
    """Papers per organisation, with the spellings merged into each one."""

    def __init__(self) -> None:
        self.papers = Counter()  # key -> papers
        self.variant_papers = Counter()  # (key, spelling) -> papers
        self.extra = {}  # key -> extra columns (e.g. the reference-list entry)

    def add_paper(self, found: set[tuple[str, str]]) -> None:
        """found: (key, spelling) pairs from one paper. Alias keys start with "alias:"."""
        self.papers.update({key for key, _ in found})
        self.variant_papers.update(found)

    def tables(self, extra_columns: tuple[str, ...] = ()) -> tuple[pd.DataFrame, pd.DataFrame]:
        spellings = defaultdict(Counter)
        for (key, spelling), n in self.variant_papers.items():
            spellings[key][spelling] = n
        # Alias name, or else the most common spelling (ties by name).
        names = {key: key.removeprefix("alias:") if key.startswith("alias:")
                 else min(counts, key=lambda s: (-counts[s], s)) for key, counts in spellings.items()}
        blank = ("",) * len(extra_columns)
        rows = [(names[key], *self.extra.get(key, blank), n) for key, n in self.papers.items()]
        table = pd.DataFrame(rows, columns=["organisation", *extra_columns, "papers"])
        table = table.sort_values(["papers", "organisation"], ascending=[False, True], ignore_index=True)
        rank = {name: i for i, name in enumerate(table["organisation"])}
        variants = pd.DataFrame(
            sorted(((names[key], spelling, n) for (key, spelling), n in self.variant_papers.items()),
                   key=lambda row: (rank[row[0]], -row[2], row[1])),
            columns=["organisation", "spelling", "papers"])
        return table, variants


def research_owner(hit: dict) -> str:
    """Owner of a research institute: its parent, or a group entry's research arms."""
    if GROUP_ENTRY.search(hit["parent"]):
        return f"{hit['parent']} (research arms)"
    return hit["parent"]


def institute_label(unit: str, hit: dict) -> tuple[str, str]:
    """(institute, owner) for an affiliation classified as research."""
    owner = research_owner(hit).removesuffix(" (research arms)")
    for name, pattern in INSTITUTE_ALIASES:
        if pattern.search(unit):
            return name, owner
    province = PROVINCES.search(unit)
    if province and hit["parent"] in (SGCC, CSG):
        place = re.sub(r"\W", "", province.group(1)).title()
        place = CITY_PROVINCE.get(place, place)
        kind = "Economic Research Institute" if re.search(r"Econom", unit, re.I) else "EPRI"
        prefix = "State Grid" if hit["parent"] == SGCC else "CSG"
        return f"{prefix} {place} {kind}", owner
    return f"{owner}: other research units", owner


def university_part(unit: str) -> str | None:
    """The comma-separated part of an affiliation that names the university."""
    for part in (p.strip() for p in unit.split(",")):
        if UNIVERSITY.search(part) and not re.match(r"(Department|Dept|School|Faculty|College of)\b", part, re.I):
            part = re.sub(r"^the\s+", "", part, flags=re.I)
            part = re.sub(r"\bUniv\.?(?=\s|$)", "University", part)
            return re.sub(r"\s+", " ", part)
    return None


def direct_name(hit: dict, unit: str) -> tuple[str, str, str]:
    """(key, spelling, group entry) of an SO or grid company. Group entries are reported by
    the company actually named, merged by company_names.csv (tried on the matched name, then
    on the whole affiliation, e.g. to tell the Elektroprivreda companies apart) or by
    normalised name."""
    if not GROUP_ENTRY.search(hit["parent"]):
        return hit["parent"], hit["parent"], ""
    spelling = re.sub(r"\s+", " ", hit["text"]).strip()
    alias = first_alias(spelling, COMPANY_ALIASES) or first_alias(unit, COMPANY_ALIASES)
    return (f"alias:{alias}" if alias else name_key(spelling) or spelling), spelling, hit["name"]


def university_name(unit: str) -> tuple[str, str] | None:
    """(key, spelling) of the university named in an affiliation, if any."""
    unit = fix_mojibake(unit)
    part = university_part(unit)
    alias = first_alias(unit, UNIVERSITY_ALIASES)
    if alias:
        return f"alias:{alias}", part or unit
    if part:
        return name_key(part) or part, part
    return None


def main() -> None:
    papers = load_papers()
    flags = pd.read_csv(CLASSIFICATION_CSV, keep_default_na=False)
    keep = set(flags.loc[(flags["IS_PAPER"] == 1) & (flags["HAS_AFFILIATIONS"] == 1), "doi"])
    papers = papers[papers["doi"].isin(keep)]
    matcher = Matcher()
    direct, universities = NameCounter(), NameCounter()
    research, institutes = Counter(), Counter()
    for affiliations in papers["author_affiliations"]:
        d, r, i, u = set(), set(), set(), set()
        for unit in split_units(affiliations):
            hits = matcher.match(unit)
            # "Midcontinent Independent System Operator" also matches the unnamed-operator
            # entry; count the named organisation only.
            named = [h for h in hits if "(unnamed)" not in h["name"]]
            for hit in named or hits:
                if hit["type"] in DIRECT_TYPES:
                    key, spelling, entry = direct_name(hit, unit)
                    direct.extra[key] = (entry,)
                    d.add((key, spelling))
                else:
                    r.add(research_owner(hit))
                    i.add(institute_label(matcher.masked(unit), hit))
            if not hits:
                found = university_name(unit)
                if found:
                    u.add(found)
        direct.add_paper(d)
        universities.add_paper(u)
        research.update(r)
        institutes.update(i)

    tables = {}
    tables["so_direct"], tables["so_direct_variants"] = direct.tables(("group_entry",))
    tables["so_research"] = pd.DataFrame(sorted(research.items(), key=lambda kv: (-kv[1], kv[0])),
                                         columns=["organisation", "papers"])
    tables["so_research_institutes"] = pd.DataFrame(
        [(inst, owner, n) for (inst, owner), n in sorted(institutes.items(), key=lambda kv: (-kv[1], kv[0]))],
        columns=["institute", "owner", "papers"])
    tables["universities"], tables["universities_variants"] = universities.tables()

    TOP_ORGANISATIONS.mkdir(parents=True, exist_ok=True)
    print(f"Counted over {len(papers)} research papers with affiliations\n")
    for name, table in tables.items():
        table.to_csv(TOP_ORGANISATIONS / f"{name}.csv", index=False, encoding="utf-8-sig")
        if not name.endswith("_variants"):
            print(f"Top {TOP_N} {name} (of {len(table)}):")
            print(table.head(TOP_N).to_string(index=False), "\n")


if __name__ == "__main__":
    main()
