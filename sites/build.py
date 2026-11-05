"""Build the preview websites of the businesses a search found: the step between searching and emailing.

    python sites/build.py job_5deb5c9d7bd1 [job_d38350fa59d5 ...]
    python sites/build.py --rebuild     # every site again, with the current design (dates untouched)

The panel builds them by itself for the businesses of each new campaign (app/outreach/sites.py); this
command does the same for whole searches. A business type's trade pack is ``sites/trades/<type>.json``
(the Roofer type uses ``roofer.json``); a type without one has no website design yet. Each site goes into
``data/sites/<slug>/``, and ``data/sites/sites.json`` lists them: the business, its email, and when it
was built, first emailed and so on (``sites/deploy.py`` works out from that how long it stays online).

- A business without a city on Google gets the city it was searched in.
- A business more than 60 miles from that city is left out: Google's results now and then reach far
  outside the searched place (a Toronto plumber in a Houston search).
- A business listed more than once (the same Google place, or the same email) gets one site.
- An address that isn't the business's own (a BBB or directory address) doesn't count, like in campaigns;
  neither does a shop that sells to the trade ("Roofing supply store").
- A rating under 4.0, or one from fewer than 5 reviews, stays off the site: the page puts it in the hero.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
from app.outreach.leads import email_problem  # noqa: E402
from sites.deploy import ends_at  # noqa: E402
from sites.render import STATES, display_name, render_lead  # noqa: E402

DATA = ROOT.parent / "data"
SITES = DATA / "sites"
MAX_MILES = 60          # farther than this from the searched city: not a business there
AREA_MILES = 30         # the towns on a site's service-area map are within this distance
AREA_TOWNS = 5
MIN_RATING = 4.0        # a lower rating, or fewer reviews, stays off the site
MIN_REVIEWS = 5
STATE_CODES = {name.lower(): code for code, name in STATES.items()}
NOT_THE_TRADE = re.compile(r"\b(store|shop|supply|supplies|supplier|wholesaler|manufacturer|distributor)\b", re.I)


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance; flat-earth, fine within a few hundred miles."""
    dx = (lon2 - lon1) * 69.17 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, (lat2 - lat1) * 69.0)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-") or "business"


def state_code(value: str | None) -> str:
    value = (value or "").strip()
    return value.upper() if value.upper() in STATES else STATE_CODES.get(value.lower(), "")


class Places:
    """The US towns and cities in ``geo/places.tsv`` (see ``geo/make_places.py``)."""

    def __init__(self, path: Path = ROOT / "geo" / "places.tsv") -> None:
        self.rows = []
        self.by_name: dict[tuple[str, str], dict] = {}
        self.everywhere: dict[str, list[dict]] = {}     # name -> the places of that name in every state
        for line in path.read_text(encoding="utf-8").splitlines():
            state, name, lat, lon, area, incorporated = line.split("\t")
            place = {"state": state, "name": name, "lat": float(lat), "lon": float(lon),
                     "area": float(area), "incorporated": incorporated == "1"}
            self.rows.append(place)
            self.everywhere.setdefault(name.lower(), []).append(place)
            key = (name.lower(), state)
            known = self.by_name.get(key)          # a city wins over a same-named community
            if not known or (place["incorporated"], place["area"]) > (known["incorporated"], known["area"]):
                self.by_name[key] = place

    def find(self, name: str, state: str) -> dict | None:
        return self.by_name.get((name.strip().lower(), state))

    def nearest_named(self, name: str, lat: float, lon: float) -> dict | None:
        """The place called ``name`` closest to a point, in any state (Houston, TX - not Houston, MO)."""
        named = self.everywhere.get(name.strip().lower(), [])
        return min(named, key=lambda p: miles(lat, lon, p["lat"], p["lon"]), default=None)

    def service_area(self, home: dict, city: str) -> list[dict]:
        """``city`` and up to 5 bigger towns around it, spread out so their names don't collide."""
        near = [p for p in self.rows if p["incorporated"] and p is not home
                and miles(home["lat"], home["lon"], p["lat"], p["lon"]) <= AREA_MILES]
        picked: list[dict] = []
        for p in sorted(near, key=lambda p: -p["area"]):
            if all(miles(p["lat"], p["lon"], q["lat"], q["lon"]) >= 6 for q in [home, *picked]):
                picked.append(p)
            if len(picked) == AREA_TOWNS:
                break
        return [{"name": city, "lat": home["lat"], "lon": home["lon"]},
                *({"name": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in picked)]


@lru_cache(maxsize=1)
def places() -> Places:
    return Places()


def trade_pack(business_type: str) -> tuple[str, dict] | None:
    """(trade, pack) for a business type ("Roofer" -> trades/roofer.json); None: no website design yet."""
    trade = slugify(business_type) if business_type.strip() else ""
    path = ROOT / "trades" / f"{trade}.json"
    return (trade, json.loads(path.read_text(encoding="utf-8"))) if trade and path.is_file() else None


def searched_city(place_text: str, rows: list[dict]) -> tuple[str, str, dict | None]:
    """The city and state a search was for (``"Katy, TX"``, or ``"Houston"`` plus its businesses' state),
    and where it is: the town's location, else the middle of the businesses found."""
    m = re.match(r"^(.*?),\s*([A-Za-z .]+)$", place_text.strip())
    city, state = (m.group(1).strip(), state_code(m.group(2))) if m else (place_text.strip(), "")
    if not state:
        counts = Counter(state_code(r.get("state")) for r in rows if state_code(r.get("state")))
        state = counts.most_common(1)[0][0] if counts else ""
    coords = [(r["latitude"], r["longitude"]) for r in rows if r.get("latitude") is not None]
    center = None
    if coords:
        lats, lons = sorted(c[0] for c in coords), sorted(c[1] for c in coords)
        center = {"lat": lats[len(lats) // 2], "lon": lons[len(lons) // 2]}
    place = places().find(city, state) if city and state else None
    if city and not state and center:          # no business says its state: the town of that name nearby
        place = places().nearest_named(city, center["lat"], center["lon"])
        if place and miles(center["lat"], center["lon"], place["lat"], place["lon"]) <= MAX_MILES:
            state = place["state"]
        else:
            place = None
    if place:
        return place["name"], state, place
    return city.title(), state, center


def load_manifest(sites_dir: Path = SITES) -> dict:
    path = sites_dir / "sites.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_manifest(manifest: dict, sites_dir: Path = SITES) -> None:
    sites_dir.mkdir(parents=True, exist_ok=True)
    partial = sites_dir / "sites.json.part"
    partial.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    partial.replace(sites_dir / "sites.json")


def pick_slug(name: str, city: str, place_id: str, email: str, manifest: dict, sites_dir: Path) -> str:
    """The business's own slug if it has a site already, else a free one: name, then name-city, then -2, -3."""
    for slug, site in manifest.items():
        if (place_id and site.get("place_id") == place_id) or site.get("email") == email:
            return slug
    base = slugify(display_name(name))
    for slug in (base, f"{base}-{slugify(city)}", *(f"{base}-{slugify(city)}-{n}" for n in range(2, 100))):
        if slug not in manifest and not (sites_dir / slug).exists():
            return slug
    raise RuntimeError(f"no free address for {name}")


def build_site(row: dict, search: tuple[str, str, dict | None], pack: dict, trade: str, manifest: dict,
               sites_dir: Path = SITES, *, job_id: str = "") -> tuple[str, str]:
    """Build one business's site from its search row; returns (slug, "") or ("", why it was left out).

    ``search`` is ``searched_city()`` of the search that found it. ``manifest`` gets the site's entry.
    """
    city, state, center = search
    if NOT_THE_TRADE.search(row.get("category") or ""):
        return "", f"a {row['category'].lower()}, not a business of the trade"
    if center and row.get("latitude") is not None:
        away = miles(center["lat"], center["lon"], row["latitude"], row["longitude"])
        if away > MAX_MILES:
            return "", f"{away:.0f} miles from {city}, outside the searched area"
    lead_city = row.get("city") or city
    lead_state = state_code(row.get("state")) or state
    if not lead_city or not lead_state:
        return "", "no city or state to put on its website"
    email = row["email"].strip().lower()
    home = places().find(lead_city, lead_state)
    rating, reviews = row.get("rating"), row.get("review_count") or 0
    good = rating is not None and rating >= MIN_RATING and reviews >= MIN_REVIEWS
    lead = {
        "business": row["name"], "city": lead_city, "state": lead_state,
        "phone": row.get("phone") or "", "email": row["email"].strip(),
        "rating": rating if good else None, "reviews": reviews if good else None,
        "maps_url": row.get("maps_url") or row.get("url") or "", "place_id": row.get("place_id") or "",
        "service_area": places().service_area(home, lead_city) if home else [lead_city],
    }
    slug = pick_slug(row["name"], lead_city, lead["place_id"], email, manifest, sites_dir)
    site = manifest.get(slug) or {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "first_email_at": None, "link_sent_at": None, "hold": False, "published_at": None}
    site.pop("expires", None)                       # older entries kept their end date here
    site.update({"business": display_name(row["name"]), "email": email, "place_id": lead["place_id"],
                 "city": lead_city, "state": lead_state, "trade": trade, "job_id": job_id or site.get("job_id", "")})
    manifest[slug] = site
    render_lead(pack, lead, sites_dir / slug, ends=ends_at(site) or datetime(2099, 1, 1, tzinfo=timezone.utc))
    return slug, ""


def build_job(job_id: str, manifest: dict, sites_dir: Path = SITES) -> tuple[list[str], list[str]]:
    """Build the sites of one search; returns (slugs built, lines saying who was left out and why)."""
    job = json.loads((DATA / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
    rows = [r for r in job["rows"] if r.get("email") and not email_problem(r["email"])]
    btype = business_type(job)
    found = trade_pack(btype["name"] if btype else job["business"])
    if not found:
        return [], [f"{job['query']}: there's no website design for {btype['name'] if btype else job['business']} yet"]
    trade, pack = found
    search = searched_city(job["place"], rows)
    built, left_out, seen = [], [], set()
    for row in rows:
        email = row["email"].strip().lower()
        key = row.get("place_id") or email
        if key in seen or email in seen:
            continue
        seen.update({key, email})
        slug, why = build_site(row, search, pack, trade, manifest, sites_dir, job_id=job_id)
        if slug:
            built.append(slug)
        else:
            left_out.append(f"{row['name']}: {why}")
    return built, left_out


def rebuild_all(manifest: dict, sites_dir: Path = SITES) -> tuple[list[str], list[str]]:
    """Render every site in ``manifest`` again from its search; it changes only the sites' files, never
    ``sites.json``, so the panel can keep running. Returns (slugs rebuilt, lines saying which weren't and why)."""
    built, problems = [], []
    for slug, site in manifest.items():
        path = DATA / "jobs" / f"{site.get('job_id')}.json"
        if not path.is_file():
            problems.append(f"{slug}: its search {site.get('job_id')} is gone")
            continue
        job = json.loads(path.read_text(encoding="utf-8"))
        rows = [r for r in job["rows"] if r.get("email") and not email_problem(r["email"])]
        row = next((r for r in rows if (site.get("place_id") and r.get("place_id") == site["place_id"])
                    or r["email"].strip().lower() == site.get("email")), None)
        found = trade_pack(site.get("trade") or "")
        if row is None or not found:
            problems.append(f"{slug}: {'not in its search' if row is None else 'no design for its trade'}")
            continue
        got, why = build_site(row, searched_city(job["place"], rows), found[1], found[0], json.loads(json.dumps(manifest)),
                              sites_dir, job_id=site.get("job_id") or "")
        if got == slug:
            built.append(slug)
        else:
            problems.append(f"{slug}: {why or f'would have become {got}'}")
    return built, problems


def business_type(job: dict) -> dict | None:
    types = json.loads((DATA / "business_types.json").read_text(encoding="utf-8"))
    return (next((t for t in types if t["id"] == job.get("business_type_id")), None)
            or next((t for t in types if t["search"].lower() == job["business"].strip().lower()), None))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("jobs", nargs="*", help="search ids, e.g. job_5deb5c9d7bd1")
    ap.add_argument("--rebuild", action="store_true", help="every site again, with the current design")
    args = ap.parse_args()
    manifest = load_manifest()
    if args.rebuild:
        built, problems = rebuild_all(manifest)
        print(f"{len(built)} site(s) rebuilt")
        for line in problems:
            print(f"  not rebuilt - {line}")
        return
    if not args.jobs:
        ap.error("name the searches to build, or --rebuild")
    for job_id in args.jobs:
        built, left_out = build_job(job_id.removesuffix(".json"), manifest)
        print(f"{job_id}: {len(built)} site(s) built")
        for line in left_out:
            print(f"  left out - {line}")
        save_manifest(manifest)


if __name__ == "__main__":
    main()
