"""Map sections for "whole area" searches.

Google Maps lists at most about 120 businesses per search and puts the most
prominent ones first, so a city-wide search mostly returns established
businesses - the ones that have websites. Zoomed into a small part of the map,
a search lists the businesses *there*, and when fewer than ~120 match, the
list runs out: it holds all of them. So a place is searched as a quadtree:

1. open the place and read the map view Google fits to it (``/@lat,lng,zoomz``
   in the URL) - the root section;
2. search the business in each section; a section whose list is still full is
   split into four quarters, searched later, until the lists run out or the
   sections are about 2 km wide (``MAX_SECTION_ZOOM``).

A section is a rectangle in Google's Web-Mercator "world pixels" (the world is
256 * 2**zoom pixels wide). It is searched at the zoom that fits it into the
visible map - the browser window minus the results panel that covers the left
of the (full-width) map - with a 10 % margin. Pure functions, no I/O
(tests/test_area.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

TILE_PX = 256
VIEWPORT_W, VIEWPORT_H = 1366, 900     # browser window used by the scraper
PANEL_W = 408                          # results panel over the left of the map
MAP_W = VIEWPORT_W - PANEL_W           # visible map width
FIT_MARGIN = 0.9                       # sections fill 90 % of the visible map
MAX_SECTION_ZOOM = 16                  # ~2 km wide - denser spots are not split further
EARTH_CIRCUMFERENCE_M = 40_075_016.686

_VIEW_RE = re.compile(r"/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)z")


def to_world(lat: float, lng: float) -> tuple[float, float]:
    """Latitude / longitude -> world pixels at zoom 0 (x: 0..256 west to east, y: 0..256 north to south)."""
    siny = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    return (TILE_PX * (0.5 + lng / 360),
            TILE_PX * (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)))


def from_world(x: float, y: float) -> tuple[float, float]:
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / TILE_PX))))
    return lat, x / TILE_PX * 360 - 180


@dataclass(frozen=True)
class Section:
    """A rectangle of the map: centre ``x, y`` and size ``w, h``, all in world pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return from_world(self.x, self.y)

    @property
    def zoom(self) -> float:
        """The zoom at which the section fits into the visible map (with the margin)."""
        return math.log2(min(MAP_W * FIT_MARGIN / self.w, VIEWPORT_H * FIT_MARGIN / self.h))

    @property
    def width_km(self) -> float:
        lat, _ = self.center
        return self.w * EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / TILE_PX / 1000

    def url(self, text: str, base: str = "https://www.google.com/maps") -> str:
        """Search ``text`` with this section in the middle of the visible map.

        The URL centre is the centre of the whole window, so it sits half a
        results panel west of the visible map's centre.
        """
        zoom = round(self.zoom, 2)   # the zoom Google will use - the shift must match it
        lat, lng = from_world(self.x - PANEL_W / 2 / 2 ** zoom, self.y)
        return f"{base}/search/{quote_plus(text.strip())}/@{lat:.7f},{lng:.7f},{zoom:.2f}z"

    def quarters(self) -> list[Section]:
        w, h = self.w / 2, self.h / 2
        return [Section(self.x + sx * w / 2, self.y + sy * h / 2, w, h) for sy in (-1, 1) for sx in (-1, 1)]

    def near(self, lat: float, lng: float) -> bool:
        """Is the point on this section, give or take 10 % (the visible map is 1/FIT_MARGIN larger)?

        Used to tell a full list *about this part of the map* - worth splitting -
        from Google zooming out to show businesses elsewhere.
        """
        px, py = to_world(lat, lng)
        return abs(px - self.x) <= 0.6 * self.w and abs(py - self.y) <= 0.6 * self.h

    def distance(self, other: Section) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


def section_from_url(url: str) -> Section | None:
    """The visible map of a Google Maps URL (``/@lat,lng,zoomz`` is the window's centre)."""
    m = _VIEW_RE.search(url or "")
    if not m:
        return None
    lat, lng, zoom = float(m.group(1)), float(m.group(2)), float(m.group(3))
    x, y = to_world(lat, lng)
    scale = 2 ** zoom
    return Section(x + PANEL_W / 2 / scale, y, MAP_W / scale, VIEWPORT_H / scale)
