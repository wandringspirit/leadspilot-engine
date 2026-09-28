"""CSV export.

`DEFAULT_COLUMNS` mirrors the spreadsheet the panel was designed after:
Name, City, State, Country, Email, Website, Phone, Phone Unformatted, Url,
Rank, Review Count, Description. `EXTENDED_COLUMNS` adds the extra fields the
scraper collects. docs/DATA_MODEL.md documents both lists and
tests/test_docs.py fails if they drift apart.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable

from app.models import PlaceRow

# (CSV header, PlaceRow attribute)
DEFAULT_COLUMNS: list[tuple[str, str]] = [
    ("Name", "name"),
    ("City", "city"),
    ("State", "state"),
    ("Country", "country"),
    ("Email", "email"),
    ("Website", "website"),
    ("Phone", "phone"),
    ("Phone Unformatted", "phone_unformatted"),
    ("Url", "url"),
    ("Rank", "rank"),
    ("Review Count", "review_count"),
    ("Description", "description"),
]

EXTENDED_COLUMNS: list[tuple[str, str]] = DEFAULT_COLUMNS + [
    ("Address", "address"),
    ("Category", "category"),
    ("Website Type", "website_type"),
    ("Rating", "rating"),
    ("Place ID", "place_id"),
    ("Maps URL", "maps_url"),
    ("Latitude", "latitude"),
    ("Longitude", "longitude"),
    ("Email Source", "email_source"),
    ("Phone Source", "phone_source"),
    ("Enrichment Status", "enrichment_status"),
]


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def rows_to_csv(rows: Iterable[PlaceRow], *, extended: bool = False, delimiter: str = ",") -> str:
    """Serialise rows to CSV text (UTF-8 BOM added by the caller for Excel)."""
    columns = EXTENDED_COLUMNS if extended else DEFAULT_COLUMNS
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([header for header, _ in columns])
    for row in sorted(rows, key=lambda r: (r.rank, r.row_id)):
        writer.writerow([_cell(getattr(row, attr)) for _, attr in columns])
    return buf.getvalue()


def rows_to_csv_bytes(rows: Iterable[PlaceRow], *, extended: bool = False) -> bytes:
    """CSV encoded as UTF-8 with BOM so Excel opens accents correctly."""
    return (chr(0xFEFF) + rows_to_csv(rows, extended=extended)).encode("utf-8")


def column_headers(extended: bool = False) -> list[str]:
    return [h for h, _ in (EXTENDED_COLUMNS if extended else DEFAULT_COLUMNS)]
