"""Build the autopilot's list of places from GeoNames (docs/AUTOPILOT.md, "A list that never runs out").

    python deploy/build_places.py cities5000.txt --countries US --min-population 10000 > places.txt

``cities5000.txt`` is GeoNames' list of the world's towns of 5,000+ people
(https://download.geonames.org/export/dump/cities5000.zip, CC BY 4.0 - "GeoNames, geonames.org").
Towns, not parts of towns (feature code PPLX), from the given countries, larger first - and spread out:
each state or province gives its largest town not yet listed in turn, so the list moves around the
country instead of working through one state. A place is written the way Google Maps finds it and
app/outreach/zones.py reads it: "Plano, TX", "Perth, WA, Australia", "Hamilton, New Zealand". ``--skip`` leaves out places already on the list.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict

csv.field_size_limit(10_000_000)

#: GeoNames' admin1 code -> how a place is written in that country (US codes are the state codes already)
ADMIN1 = {
    "CA": {"01": "AB", "02": "BC", "03": "MB", "04": "NB", "05": "NL", "07": "NS", "08": "ON", "09": "PE",
           "10": "QC", "11": "SK", "12": "YT", "13": "NT", "14": "NU"},
    "AU": {"01": "ACT", "02": "NSW", "03": "NT", "04": "QLD", "05": "SA", "06": "TAS", "07": "VIC", "08": "WA"},
}
#: outside the US the country is written too: "Perth, WA" alone is a US state code (app/outreach/zones.py)
COUNTRY_SUFFIX = {"CA": "Canada", "AU": "Australia", "NZ": "New Zealand"}
#: left out: Quebec (French-speaking - the emails and websites are in English), the northern territories
SKIP_REGIONS = {("CA", "10"), ("CA", "12"), ("CA", "13"), ("CA", "14")}


#: GeoNames names Google Maps knows by another
RENAMED = {"Lexington-Fayette": "Lexington"}
#: parts of a city listed as towns ("Downtown DC")
NOT_TOWNS = ("Downtown ", "Midtown ", "Uptown ")


def label(row: list[str]) -> str | None:
    name, country, admin1 = row[2] or row[1], row[8], row[10]     # the plain-letters name: Ewa Beach
    if name.startswith(NOT_TOWNS):
        return None
    name = RENAMED.get(name, name)
    if country == "US":
        return f"{name}, {admin1}" if len(admin1) == 2 else None
    if country == "NZ":
        return f"{name}, New Zealand"
    region = ADMIN1.get(country, {}).get(admin1)
    return f"{name}, {region}, {COUNTRY_SUFFIX[country]}" if region else None


def build(rows, countries: set[str], min_population: int, skip: set[str]) -> list[str]:
    by_region: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    seen = {s.lower() for s in skip}
    for row in rows:
        if row[8] not in countries or row[7] == "PPLX" or not row[7].startswith("PPL"):
            continue
        if (row[8], row[10]) in SKIP_REGIONS or "(historical)" in row[1]:
            continue
        population = int(row[14] or 0)
        place = label(row)
        if population < min_population or not place or place.lower() in seen:
            continue
        seen.add(place.lower())
        by_region[(row[8], row[10])].append((population, place))
    queues = sorted((sorted(towns, reverse=True) for towns in by_region.values()), key=lambda q: -q[0][0])
    out = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0)[1])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("geonames", help="cities5000.txt (unzipped)")
    parser.add_argument("--countries", default="US", help="comma-separated: US,CA,AU,NZ")
    parser.add_argument("--min-population", type=int, default=10000)
    parser.add_argument("--skip", help="a file of places already on the list, one per line")
    args = parser.parse_args()
    skip = set()
    if args.skip:
        with open(args.skip, encoding="utf-8") as f:
            skip = {line.strip() for line in f if line.strip()}
    with open(args.geonames, encoding="utf-8", newline="") as f:
        rows = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        places = build(rows, set(args.countries.upper().split(",")), args.min_population, skip)
    sys.stdout.write("\n".join(places) + "\n")
    print(f"{len(places)} places", file=sys.stderr)


if __name__ == "__main__":
    main()
