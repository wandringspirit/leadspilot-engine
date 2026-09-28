import math

import pytest

from app.scraper.area import (
    MAP_W,
    MAX_SECTION_ZOOM,
    PANEL_W,
    VIEWPORT_H,
    Section,
    from_world,
    section_from_url,
    to_world,
)

HOUSTON_URL = ("https://www.google.com/maps/place/Houston,+TX,+USA/@29.8168824,-95.6814834,10z/data=!3m1!4b1"
               "!4m6!3m5!1s0x8640b8b4488d8501:0xca0d02def365053b!8m2!3d29.7600771!4d-95.3701108!16zL20vMDNsMm4")


@pytest.mark.parametrize("lat, lng", [(0, 0), (29.7604, -95.3698), (-33.8688, 151.2093), (51.5072, -0.1276)])
def test_world_pixels_round_trip(lat, lng):
    back = from_world(*to_world(lat, lng))
    assert back == pytest.approx((lat, lng), abs=1e-9)


def test_world_pixels_orientation():
    assert to_world(0, 0) == pytest.approx((128, 128))
    x_west, _ = to_world(0, -90)
    _, y_north = to_world(45, 0)
    assert x_west < 128 and y_north < 128          # x grows east, y grows south


def test_section_from_url_is_the_visible_map():
    s = section_from_url(HOUSTON_URL)
    assert s is not None
    scale = 2 ** 10
    assert s.w == pytest.approx(MAP_W / scale) and s.h == pytest.approx(VIEWPORT_H / scale)
    cx, _ = to_world(29.8168824, -95.6814834)
    assert s.x == pytest.approx(cx + PANEL_W / 2 / scale)   # half a results panel east of the window centre
    lat, lng = s.center
    assert -95.45 < lng < -95.35 and 29.7 < lat < 29.9      # i.e. on the city itself
    assert s.width_km == pytest.approx(MAP_W * 40_075_016.686 * math.cos(math.radians(lat)) / 256 / 1024 / 1000)
    assert 120 < s.width_km < 135                              # 958 px at ~132.6 m per pixel
    assert section_from_url("https://www.google.com/maps/search/houston") is None
    assert section_from_url("") is None


def test_section_url_puts_the_section_in_the_visible_map():
    s = section_from_url(HOUSTON_URL)
    url = s.url("roofing contractor")
    assert url.startswith("https://www.google.com/maps/search/roofing+contractor/@29.")
    lat, lng, zoom = (float(v) for v in url.rsplit("/@", 1)[1].rstrip("z").split(","))
    assert zoom == pytest.approx(s.zoom, abs=0.01) and zoom < 10    # the 10 % margin zooms out a little
    x, _ = to_world(lat, lng)
    assert x == pytest.approx(s.x - PANEL_W / 2 / 2 ** zoom, abs=1e-6)  # window centre = west of the section


def test_quarters_tile_the_section_one_zoom_closer():
    s = section_from_url(HOUSTON_URL)
    parts = s.quarters()
    assert len(parts) == 4
    assert all(p.w == pytest.approx(s.w / 2) and p.h == pytest.approx(s.h / 2) for p in parts)
    assert all(p.zoom == pytest.approx(s.zoom + 1) for p in parts)
    assert sum(p.w * p.h for p in parts) == pytest.approx(s.w * s.h)
    xs = sorted({round(p.x, 9) for p in parts})
    assert xs == pytest.approx([s.x - s.w / 4, s.x + s.w / 4])


def test_near_and_distance():
    s = section_from_url(HOUSTON_URL)
    assert s.near(*s.center)
    assert not s.near(32.7767, -96.7970)          # Dallas is not on Houston's map
    a, b = s.quarters()[:2]
    assert a.distance(b) == pytest.approx(s.w / 2)


def test_sections_reach_the_max_zoom_after_a_few_splits():
    s = section_from_url(HOUSTON_URL)
    levels = 0
    while s.zoom < MAX_SECTION_ZOOM:
        s, levels = s.quarters()[0], levels + 1
    assert levels == math.ceil(MAX_SECTION_ZOOM - section_from_url(HOUSTON_URL).zoom)
    assert s.width_km < 3
