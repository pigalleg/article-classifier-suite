"""Step 4: which organisations publish most, counted in papers (one paper counts once per
organisation, however many of its authors work there).

Writes, to results/top_organisations/:
  so_direct.csv                SOs and grid companies (TSO, GRIDCO, DSO)
  so_research.csv              SO-owned research institutes, grouped by owner
  so_research_institutes.csv   the same, one row per institute (with its owner)
  universities.csv             universities and similar academic institutions
Counts only research papers with affiliations (IS_PAPER and HAS_AFFILIATIONS).

Organisation names: single-organisation entries of so_organisations.csv are reported by name
(research institutes by their parent, e.g. all State Grid institutes as State Grid). Group
entries ("US investor-owned utilities", "Japanese regional electric power companies", ...) are
reported by the company name actually matched, e.g. "Dominion Energy", so one company written
in two ways is listed twice. Institute names come from data/reference/research_institute_names.csv
(first matching alias wins); provincial State Grid and CSG institutes are named by province; the
rest fall back to "<owner>: other research units". University names are the comma-separated
part of the affiliation that names the university, lightly normalised, so a university written
in two different ways is also counted twice.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from so_matching import (  # noqa: E402
    CLASSIFICATION_CSV, DIRECT_TYPES, INSTITUTE_NAMES_CSV, TOP_ORGANISATIONS, Matcher, load_papers,
    split_units,
)

TOP_N = 10
INSTITUTE_ALIASES = [
    (row["institute"], re.compile(row["pattern"], re.IGNORECASE))
    for _, row in pd.read_csv(INSTITUTE_NAMES_CSV, keep_default_na=False).iterrows()
]
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


def so_label(hit: dict) -> str:
    group = GROUP_ENTRY.search(hit["parent"])
    if group and hit["type"] == "RESEARCH":
        return f"{hit['parent']} (research arms)"
    if group:
        return re.sub(r"\s+", " ", hit["text"]).strip().title() + f"  [{hit['name']}]"
    return hit["parent"]


def institute_label(unit: str, hit: dict) -> tuple[str, str]:
    """(institute, owner) for an affiliation classified as research."""
    owner = so_label(hit).removesuffix(" (research arms)")
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


def university_label(unit: str) -> str | None:
    for part in (p.strip() for p in unit.split(",")):
        if UNIVERSITY.search(part) and not re.match(r"(Department|Dept|School|Faculty|College of)\b", part, re.I):
            part = re.sub(r"^the\s+", "", part, flags=re.I)
            part = re.sub(r"\bUniv\.?(?=\s|$)", "University", part)
            return re.sub(r"\s+", " ", part)
    return None


def main() -> None:
    papers = load_papers()
    flags = pd.read_csv(CLASSIFICATION_CSV, keep_default_na=False)
    keep = set(flags.loc[(flags["IS_PAPER"] == 1) & (flags["HAS_AFFILIATIONS"] == 1), "doi"])
    papers = papers[papers["doi"].isin(keep)]
    matcher = Matcher()
    direct, research, institutes, universities = Counter(), Counter(), Counter(), Counter()
    for affiliations in papers["author_affiliations"]:
        d, r, i, u = set(), set(), set(), set()
        for unit in split_units(affiliations):
            hits = matcher.match(unit)
            # "Midcontinent Independent System Operator" also matches the unnamed-operator
            # entry; count the named organisation only.
            named = [h for h in hits if "(unnamed)" not in h["name"]]
            for hit in named or hits:
                if hit["type"] in DIRECT_TYPES:
                    d.add(so_label(hit))
                else:
                    r.add(so_label(hit))
                    i.add(institute_label(matcher.masked(unit), hit))
            if not hits:
                label = university_label(unit)
                if label:
                    u.add(label)
        direct.update(d)
        research.update(r)
        institutes.update(i)
        universities.update(u)
    TOP_ORGANISATIONS.mkdir(parents=True, exist_ok=True)
    print(f"Counted over {len(papers)} research papers with affiliations\n")
    # Most papers first; ties by name, so every run writes the same order.
    for name, counter in [("so_direct", direct), ("so_research", research), ("universities", universities)]:
        table = pd.DataFrame(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])),
                             columns=["organisation", "papers"])
        table.to_csv(TOP_ORGANISATIONS / f"{name}.csv", index=False)
        print(f"Top {TOP_N} {name} (of {len(table)}):")
        print(table.head(TOP_N).to_string(index=False), "\n")
    table = pd.DataFrame([(inst, owner, n) for (inst, owner), n in
                          sorted(institutes.items(), key=lambda kv: (-kv[1], kv[0]))],
                         columns=["institute", "owner", "papers"])
    table.to_csv(TOP_ORGANISATIONS / "so_research_institutes.csv", index=False)
    print(f"Top {TOP_N} research institutes (of {len(table)}):")
    print(table.head(TOP_N).to_string(index=False))


if __name__ == "__main__":
    main()
