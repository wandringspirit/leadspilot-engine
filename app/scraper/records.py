"""Decode the place records Google Maps ships as nested JSON arrays.

Google Maps (the web app) does not expose a JSON API, but three responses
carry the *same* undocumented place record layout:

* ``GET /search?tbm=map&q=...``     - fired for every page of the results feed
                                      (20 records per page, no description /
                                      review count)
* ``GET /maps/preview/place?...``    - fired when a place page is opened
                                      (one full record at ``data[6]``)
* ``window.APP_INITIALIZATION_STATE``- embedded in place page HTML (usually a
                                      stub nowadays, but handled for safety)

All three start with the anti-JSON prefix ``)]}'``. We never depend on the
absolute position of a record inside a response: ``find_place_records``
walks the tree and returns every list that *looks like* a place record
(``[11]`` is a name string and ``[78]`` is a place id). Field indexes used
here are documented in docs/SCRAPING.md; every lookup is defensive, so a
moved index degrades to an empty value instead of a crash.
"""
from __future__ import annotations

import json
import re
from typing import Any

RPC_PREFIX = ")]}'"
_STATE_RE = re.compile(
    r"window\.APP_INITIALIZATION_STATE\s*=\s*(\[.*?\]);\s*window\.APP_FLAGS",
    re.S,
)

# Index map for a place record. Keep in sync with docs/SCRAPING.md.
IDX = {
    "name": (11,),
    "address": (39,),
    "address_lines": (2,),
    "website": (7, 0),
    "phone_e164": (178, 0, 3),
    "phone_intl": (178, 0, 0),
    "rating": (4, 7),
    "review_count": (4, 8),
    "place_id": (78,),
    "cid": (10,),
    "categories": (13,),
    "latitude": (9, 2),
    "longitude": (9, 3),
    "components": (183, 1),      # [borough, street, street, city, postal, state, country]
    "description": (154, 0, 0),  # "From the business" text
    "editorial": (32, 1, 1),     # Google's editorial summary (older layout)
    "status": (34, 4, 4),        # e.g. "Permanently closed"
}


def dig(data: Any, *path: int) -> Any:
    """Safely walk nested lists: dig(x, 6, 11) == x[6][11] or None."""
    cur = data
    for idx in path:
        if not isinstance(cur, list) or idx >= len(cur) or idx < -len(cur):
            return None
        cur = cur[idx]
    return cur


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_rpc_json(body: str) -> Any | None:
    """Parse a ``)]}'``-prefixed Google RPC body (or plain JSON)."""
    if not body:
        return None
    text = body.lstrip()
    if text.startswith(RPC_PREFIX):
        text = text[len(RPC_PREFIX):]
    try:
        return json.loads(text.lstrip())
    except (json.JSONDecodeError, ValueError):
        return None


def looks_like_place(node: Any) -> bool:
    return (
        isinstance(node, list)
        and len(node) > 40
        and isinstance(dig(node, 11), str)
        and (isinstance(dig(node, 78), str) or isinstance(dig(node, 10), str))
    )


def find_place_records(data: Any, *, max_depth: int = 8) -> list[list]:
    """Return every place record found anywhere inside ``data`` (in document order)."""
    found: list[list] = []

    def walk(node: Any, depth: int) -> None:
        if depth > max_depth or not isinstance(node, list):
            return
        if looks_like_place(node):
            found.append(node)
            return
        for item in node:
            if isinstance(item, list):
                walk(item, depth + 1)

    walk(data, 0)
    return found


def record_to_dict(record: list) -> dict[str, Any]:
    """Flatten a raw record into named fields (empty string / None when absent)."""
    categories = dig(record, *IDX["categories"])
    components = dig(record, *IDX["components"])
    description = _s(dig(record, *IDX["description"]))
    if not description:
        # some records store several paragraphs: [154][i][0]
        paragraphs = dig(record, 154)
        if isinstance(paragraphs, list):
            description = " ".join(_s(dig(p, 0)) for p in paragraphs if _s(dig(p, 0)))
    if not description:
        description = _s(dig(record, *IDX["editorial"]))

    address_lines = dig(record, *IDX["address_lines"])
    return {
        "name": _s(dig(record, *IDX["name"])),
        "address": _s(dig(record, *IDX["address"])) or (
            ", ".join(x for x in address_lines if isinstance(x, str)) if isinstance(address_lines, list) else ""),
        "website": _s(dig(record, *IDX["website"])),
        "phone": _s(dig(record, *IDX["phone_e164"])) or _s(dig(record, *IDX["phone_intl"])),
        "rating": _num(dig(record, *IDX["rating"])),
        "review_count": _num(dig(record, *IDX["review_count"])),
        "place_id": _s(dig(record, *IDX["place_id"])),
        "cid": _s(dig(record, *IDX["cid"])),
        "category": _s(dig(categories, 0)) if isinstance(categories, list) else "",
        "categories": [c for c in categories if isinstance(c, str)] if isinstance(categories, list) else [],
        "latitude": _num(dig(record, *IDX["latitude"])),
        "longitude": _num(dig(record, *IDX["longitude"])),
        "street": _s(dig(components, 1)),
        "city": _s(dig(components, 3)),
        "postal_code": _s(dig(components, 4)),
        "state": _s(dig(components, 5)),
        "country": _s(dig(components, 6)),
        "description": description,
        "status": _s(dig(record, *IDX["status"])),
    }


def records_from_rpc(body: str) -> list[dict[str, Any]]:
    """Every place record in a search / preview response, as dicts."""
    data = parse_rpc_json(body)
    if data is None:
        return []
    return [record_to_dict(r) for r in find_place_records(data)]


def records_from_html(html: str) -> list[dict[str, Any]]:
    """Place records embedded in a place page's APP_INITIALIZATION_STATE."""
    m = _STATE_RE.search(html or "")
    if not m:
        return []
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(node, str) and node.startswith(RPC_PREFIX):
            out.extend(records_from_rpc(node))
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(state)
    return out


def is_search_response(url: str) -> bool:
    return "/search?" in url and "tbm=map" in url


def is_place_response(url: str) -> bool:
    return "/maps/preview/place" in url
