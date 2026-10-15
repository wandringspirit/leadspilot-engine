"""Make ``places.tsv`` - every US town and city with its location - from the Census Bureau's gazetteer.

    python sites/geo/make_places.py 2024_Gaz_place_national.zip

The download (public domain, about 1.2 MB):
https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip

``places.tsv`` has one place per line: state, name, latitude, longitude, land area (sq mi), and 1 for an
incorporated city or town, 0 for a census-designated place (an unincorporated community such as
The Woodlands, TX).
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "places.tsv"
# the kind of place at the end of each name: "Dallas city", "Addison town", "Spring CDP"
KIND = re.compile(r"\s+(city and borough|metropolitan government|metro government|consolidated government|"
                  r"unified government|urban county|city|town|village|borough|municipality|CDP|plantation|"
                  r"corporation|comunidad|zona urbana)$")


def main(zip_path: str) -> None:
    with zipfile.ZipFile(zip_path) as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    lines = [line.rstrip().split("\t") for line in text.splitlines()]
    cols = {name.strip(): i for i, name in enumerate(lines[0])}
    out = []
    for row in lines[1:]:
        status = row[cols["FUNCSTAT"]]
        if status not in ("A", "S", "F", "B"):            # active, statistical, consolidated
            continue
        name = KIND.sub("", row[cols["NAME"]].replace(" (balance)", "")).strip()
        out.append("\t".join([row[cols["USPS"]], name, row[cols["INTPTLAT"]], row[cols["INTPTLONG"]],
                              row[cols["ALAND_SQMI"]], "0" if status == "S" else "1"]))
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(out)} places -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
