"""Render a business's website: a design + a trade pack + one lead.

    python sites/render.py sites/trades/roofer.json sites/samples/roofer-dallas.json
    python sites/render.py ... --variant 2 --live --out data/sites/demo

A *design* (``sites/designs/<name>/``) holds the page templates, ``site.css`` and ``site.js``.
A *trade pack* (``sites/trades/<trade>.json``) holds the words, photos, services and colours for one
kind of business; its strings may use ``{{ city }}``, ``{{ state_name }}`` and ``{{ business }}``.
A *lead* is the crawler's data for one business. The accent colour, headline and hero photo are
picked from the pack by a hash of the business name, so two roofers in one city don't get the same
site; ``--variant`` overrides the pick.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from app.outreach.templates import display_name  # noqa: E402

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "Washington, D.C.",
}

_text = Environment(autoescape=False, undefined=StrictUndefined)


def fill(value, ctx):
    """Render every string in a trade pack (nested lists and dicts too) with the lead's values."""
    if isinstance(value, str):
        return _text.from_string(value).render(ctx) if "{{" in value else value
    if isinstance(value, list):
        return [fill(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, ctx) for k, v in value.items()}
    return value


class Images:
    """Image URLs for the templates.

    ``https://images.unsplash.com/...`` sources get Unsplash's resizing parameters. ``assets/...``
    sources are our own photos (``sites/assets/``): each size a page asks for is resized with Pillow
    into the site's own ``assets/`` folder when the site is written, never larger than the original.
    An ``assets/`` source may end in ``#left,top,right,bottom`` (percent) to use only that part of it.
    """

    def __init__(self) -> None:
        self.wanted: set[tuple[str, int]] = set()

    @staticmethod
    def name(src: str, w: int) -> str:
        path, _, box = src.partition("#")
        return path.rsplit(".", 1)[0] + (f"-crop-{box.replace(',', '-')}" if box else "") + f"-{w}.jpg"

    def img(self, src: str, w: int, h: int | None = None) -> str:
        if src.startswith("assets/"):
            self.wanted.add((src, w))
            return self.name(src, w)
        return f"{src}?auto=format&fit=crop&w={w}" + (f"&h={h}" if h else "") + "&q=70"

    def srcset(self, src: str, widths=(640, 960, 1400, 2000)) -> str:
        return ", ".join(f"{self.img(src, w)} {w}w" for w in widths)

    def write(self, out: Path) -> None:
        """Copy the photos into ``out``; each size is made once, in ``.image-cache`` next to ``out``,
        since every site of a trade uses the same photos."""
        from PIL import Image
        cache = out.parent / ".image-cache"
        for src, w in sorted(self.wanted):
            path, _, box = src.partition("#")
            source = ROOT / path
            stat = source.stat()
            key = hashlib.md5(f"{src}|{w}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]
            cached = cache / f"{key}.jpg"
            if not cached.is_file():
                cache.mkdir(parents=True, exist_ok=True)
                with Image.open(source) as im:
                    im = im.convert("RGB")
                    if box:
                        l, t, r, b = (float(v) / 100 for v in box.split(","))
                        im = im.crop((round(im.width * l), round(im.height * t), round(im.width * r),
                                      round(im.height * b)))
                    if im.width > w:
                        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
                    partial = cached.with_suffix(".part")
                    im.save(partial, "JPEG", quality=80, optimize=True, progressive=True)
                partial.replace(cached)
            target = out / self.name(src, w)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, target)


def area_map(area: list[dict], home_name: str) -> dict | None:
    """Where each service-area town sits on a square map (percent), or None without coordinates.

    Towns keep their real direction and distance from the home city (flat-earth miles, fine within
    ~50 miles); up to three distance rings; each label goes on the side where it hits nothing.
    """
    if len(area) < 2 or any(a.get("lat") is None or a.get("lon") is None for a in area):
        return None
    home = next((a for a in area if a["name"] == home_name), area[0])
    k = math.cos(math.radians(home["lat"]))

    def miles(a):
        return (a["lon"] - home["lon"]) * 69.17 * k, (a["lat"] - home["lat"]) * 69.0

    towns = [a for a in area if a is not home]
    offsets = [miles(a) for a in towns] + [(0.0, 0.0)]
    xs, ys = [o[0] for o in offsets], [o[1] for o in offsets]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = min(70 / max(max(xs) - min(xs), max(ys) - min(ys), 1), 8)  # percent per mile

    def at(dx, dy):
        return round(50 + (dx - cx) * scale, 2), round(50 - (dy - cy) * scale, 2)

    hx, hy = at(0, 0)
    placed = []
    for a, (dx, dy) in zip(towns, offsets):
        x, y = at(dx, dy)
        placed.append({"name": a["name"], "x": x, "y": y, "mi": round(math.hypot(dx, dy))})
    points = placed + [{"x": hx, "y": hy}]
    far = max(math.hypot(dx, dy) for dx, dy in offsets) or 1
    step = next((n for n in (2, 5, 10, 20, 25, 50, 100) if far / n <= 3), 100)

    def clash(t, sign):
        return any(o is not t and 0 < (o["x"] - t["x"]) * sign < 24 and abs(o["y"] - t["y"]) < 9 for o in points)

    for t in placed:
        first = -1 if t["x"] > 72 else 1
        sign = first if not clash(t, first) else (-first if not clash(t, -first) else first)
        t["side"] = "right" if sign == 1 else "left"
    rings = []
    for m in range(step, math.ceil(far / step) * step + 1, step):
        r = round(m * scale, 2)
        # the label sits where the ring crosses the side of the map with fewer towns
        up = sum(t["y"] < hy for t in placed) <= sum(t["y"] > hy for t in placed)
        ly = next((y for y in ((hy - r, hy + r) if up else (hy + r, hy - r)) if 4 < y < 96), None)
        rings.append({"r": r, "label": f"{m} mi", "ly": None if ly is None else round(ly, 2)})
    return {"home": {"name": home["name"], "x": hx, "y": hy}, "towns": placed, "rings": rings}


def cropped(explorer: dict) -> dict:
    """The explorer with its photo cut to ``crop`` and its spots moved to match.

    ``crop`` is ``[left, top, right, bottom]`` in percent of the photo; keep right - left equal to
    bottom - top so the photo keeps its shape. Spots stay in percent of the whole photo, so a crop
    can change without placing them again.
    """
    l, t, r, b = explorer["crop"]
    spots = [{**s, "x": round((s["x"] - l) * 100 / (r - l), 2), "y": round((s["y"] - t) * 100 / (b - t), 2)}
             for s in explorer["spots"]]
    return {**explorer, "photo": f"{explorer['photo']}#{l:g},{t:g},{r:g},{b:g}", "spots": spots}


def photos_for(pack: dict, state: str, role: str) -> list[dict]:
    """A pack's photos for one place on the page (``"role"``: "hero", the default, or "cta").

    Photos made for the lead's state (``"states": ["TX"]``) win; photos for other states never show.
    """
    usable = [p for p in pack["hero_photos"] if p.get("role", "hero") == role
              and (not p.get("states") or state in p["states"])]
    return [p for p in usable if p.get("states")] or usable


def context(pack: dict, lead: dict, *, variant: int | None, preview: bool, ends: datetime) -> dict:
    business = display_name(lead["business"])
    state = (lead.get("state") or "").upper()
    base = {"business": business, "city": lead["city"], "state": state,
            "state_name": STATES.get(state, state or lead["city"])}
    seed = int(hashlib.md5(f"{business}|{lead['city']}".lower().encode()).hexdigest(), 16)
    pick = (lambda items, shift: items[(variant if variant is not None else seed >> shift) % len(items)])
    digits = re.sub(r"\D", "", lead.get("phone") or "")
    if len(digits) == 10:
        digits = "1" + digits
    places = [a if isinstance(a, dict) else {"name": a} for a in (lead.get("service_area") or [lead["city"]])]
    area = [a["name"] for a in places]
    heroes = photos_for(pack, state, "hero")
    hero = pick(heroes, 16)
    closers = [p for p in photos_for(pack, state, "cta") + heroes if p["src"] != hero["src"]]
    logo_name = business
    for word in pack.get("logo_strip", []):
        short = re.sub(rf"[\s,&-]*\b{re.escape(word)}$", "", logo_name, flags=re.I).strip()
        if short != logo_name and len(short) >= 3:
            logo_name = short
            break
    trade = fill(pack, base)
    if trade.get("explorer", {}).get("crop"):
        trade["explorer"] = cropped(trade["explorer"])
    return {
        **base,
        "trade": trade,
        "lead": lead,
        "phone": lead.get("phone") or "",
        "phone_href": f"tel:+{digits}" if digits else "",
        "sms_href": f"sms:+{digits}" if digits else "",
        "email": lead.get("email") or "",
        "rating": lead.get("rating"),
        "reviews": lead.get("reviews"),
        "maps_url": lead.get("maps_url") or "",
        # Google's own page with the business's reviews open; the Maps link when there is no place id
        "reviews_url": (f"https://search.google.com/local/reviews?placeid={lead['place_id']}"
                        if lead.get("place_id") else lead.get("maps_url") or ""),
        "area": area,
        "area_map": area_map(places, lead["city"]),
        "accent": pick(pack["accents"], 0),
        "headline": fill(pick(pack["headlines"], 8), base),
        "hero": hero,
        "cta_photo": closers[0] if closers else None,
        "logo_name": logo_name,
        "logo_tag": f"{pack['label']} · {lead['city']}",
        "preview": preview,
        "preview_ends": ends.isoformat(timespec="seconds"),
        "keep_href": f"mailto:piyush@leadspilot.co?subject={business.replace(' ', '%20')}%20-%20keep%20my%20website",
        "form_endpoint": "",
        "year": datetime.now().year,
    }


def render(pack_path: Path, lead_path: Path, out: Path, *, variant=None, preview=True, ends=None) -> Path:
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    lead = json.loads(lead_path.read_text(encoding="utf-8"))
    return render_lead(pack, lead, out, variant=variant, preview=preview, ends=ends)


def render_lead(pack: dict, lead: dict, out: Path, *, variant=None, preview=True, ends=None) -> Path:
    """Write one business's site into ``out``: ``index.html``, ``site.css``, ``site.js`` and its photos."""
    design = ROOT / "designs" / pack["design"]
    env = Environment(loader=FileSystemLoader(design), autoescape=True, undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    images = Images()
    env.filters.update(img=images.img, srcset=images.srcset)
    ends = ends or (datetime.now(timezone.utc) + timedelta(days=7))
    ctx = context(pack, lead, variant=variant, preview=preview, ends=ends)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(env.get_template("index.html.j2").render(ctx), encoding="utf-8")
    for asset in ("site.css", "site.js"):
        shutil.copyfile(design / asset, out / asset)
    images.write(out)
    return out / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack", type=Path)
    ap.add_argument("lead", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--variant", type=int, help="force accent/headline/photo number N instead of the hash pick")
    ap.add_argument("--live", action="store_true", help="the paid site: no countdown bar, no preview notes")
    args = ap.parse_args()
    out = args.out or ROOT.parent / "data" / "sites" / args.lead.stem
    print(render(args.pack, args.lead, out, variant=args.variant, preview=not args.live))


if __name__ == "__main__":
    main()
