"""Read a lead list back from a CSV file - for a campaign that runs from a file.

The format is the crawler's own CSV (docs/DATA_MODEL.md): a header row, then one
row per business and email. ``Name`` and ``Email`` are needed; every other
column of the default or the extended export (``City``, ``Website``, ``Phone``,
``Review Count``, ``Rating``, ``Place ID`` …) is used when present, unknown
columns are ignored and the header's case doesn't matter. A file edited in
Excel or Google Sheets keeps working: a semicolon or tab as separator and a
UTF-8 byte-order mark are recognised.

Rows of the same business (same ``Place ID``, else the same ``Rank``, else the
same name and phone) become one place, like the rows of a job. Pure functions
(tests/test_drive.py).
"""
from __future__ import annotations

import csv
import io

from app.export.csv_export import EXTENDED_COLUMNS
from app.models import PlaceRow

REQUIRED = ("Name", "Email")
_FIELDS = {header.lower(): attr for header, attr in EXTENDED_COLUMNS if attr != "website_type"}
_NUMBERS = {"rank": int, "review_count": int, "rating": float, "latitude": float, "longitude": float}


class CsvFormatError(ValueError):
    """The file isn't a lead list the crawler can read; the message says why."""


def rows_from_csv(text: str) -> list[PlaceRow]:
    """PlaceRows from CSV text, ranks set so that rows of one business share a rank."""
    text = text.lstrip("﻿")
    if not text.strip():
        raise CsvFormatError("the file is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, [])
    columns = {i: _FIELDS[h.strip().lower()] for i, h in enumerate(header) if h.strip().lower() in _FIELDS}
    missing = [name for name in REQUIRED if name.lower() not in {h.strip().lower() for h in header}]
    if missing:
        raise CsvFormatError(f"the file has no {' and no '.join(missing)} column - use the crawler's CSV format "
                             "(a header row with at least Name and Email)")
    rows: list[PlaceRow] = []
    places: dict[str, int] = {}
    for values in reader:
        data: dict = {}
        for index, attr in columns.items():
            value = values[index].strip() if index < len(values) else ""
            if not value:
                continue
            if attr in _NUMBERS:
                try:
                    data[attr] = _NUMBERS[attr](float(value.replace(",", ""))) if _NUMBERS[attr] is int \
                        else _NUMBERS[attr](value.replace(",", "."))
                except ValueError:
                    continue
            else:
                data[attr] = value
        if not data.get("name"):
            continue
        key = data.get("place_id") or (f"#{data['rank']}" if "rank" in data else
                                       f"{data['name'].lower()}|{data.get('phone', '')}")
        data["rank"] = places.setdefault(key, len(places) + 1)
        rows.append(PlaceRow(**data))
    return rows
