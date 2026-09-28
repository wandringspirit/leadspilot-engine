"""Whole-area search against a fake Google Maps that knows where businesses are.

Like Google, the fake lists the businesses on the visible map of a search URL,
most prominent first, 120 at most; a text search lists the city's top 120; a
view with nothing on it "zooms out" and lists the nearest businesses instead.
Businesses without a website are the least prominent - the situation where a
single city-wide search finds none of them.
"""
import random
from urllib.parse import unquote_plus

import pytest

from app.config import Settings
from app.filters import SearchFilters
from app.jobs import PlaceFilter
from app.models import PlaceRow
from app.scraper.area import Section, from_world, section_from_url
from app.scraper.maps import MAPS_BASE, MapsScraper, RecordCapture, Search
from tests.test_area import HOUSTON_URL
from tests.test_scan import FakeContext

ROOT = section_from_url(HOUSTON_URL)
LEADS = SearchFilters(website="without", contact="phone_or_email")


def city(n: int = 600, *, no_site_every: int = 20, within: Section = ROOT, seed: int = 1) -> list[dict]:
    """``n`` businesses spread over ``within``; every ``no_site_every``-th has no website and ranks last."""
    rng = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        x = within.x + (rng.random() - 0.5) * within.w
        y = within.y + (rng.random() - 0.5) * within.h
        lat, lng = from_world(x, y)
        no_site = i % no_site_every == 0
        out.append({"id": f"ChIJgeo{i}", "name": f"Roofer {i}", "x": x, "y": y, "lat": lat, "lng": lng,
                    "website": "" if no_site else f"https://roofer{i}.com/", "phone": f"(713) 555-{i:04d}",
                    "prominence": rng.random() * 0.1 if no_site else 0.2 + rng.random()})
    return out


def href(b: dict) -> str:
    return (f"https://www.google.com/maps/place/{b['name'].replace(' ', '+')}/data=!4m7!3m6!1s0x0:0x1!8m2"
            f"!3d{b['lat']:.7f}!4d{b['lng']:.7f}!16s%2Fg%2F1!19s{b['id']}?authuser=0&hl=en")


class GeoScraper(MapsScraper):
    def __init__(self, businesses: list[dict], *, root: Section | None = ROOT, fail: tuple = (), **settings):
        cfg = Settings(MIN_DELAY_MS=0, MAX_DELAY_MS=0, SCROLL_STALL_LIMIT=1, DETAIL_CONCURRENCY=4, **settings)
        super().__init__(cfg, headless=True)
        self.businesses, self.root, self.fail = businesses, root, fail
        self.by_id = {b["id"]: b for b in businesses}
        self.text_results: dict[str, list[dict]] = {}
        self.opened: list[str] = []
        self.feed: list[dict] = []
        self.loaded = 0

    async def _start(self):
        self._context = FakeContext()

    async def _stop(self):
        self._context = None

    async def _locate(self, page, place):
        return self.root

    async def _open_results(self, page, url, label):
        self.opened.append(url)
        if any(part in url for part in self.fail):
            raise RuntimeError("Google Maps results feed did not appear")
        view = section_from_url(url)
        if view is None:        # a text search
            text = unquote_plus(url.split("/search/", 1)[1].split("/", 1)[0])
            feed = sorted(self.text_results.get(text, self.businesses), key=lambda b: -b["prominence"])
        else:
            feed = sorted((b for b in self.businesses
                           if abs(b["x"] - view.x) <= view.w / 2 and abs(b["y"] - view.y) <= view.h / 2),
                          key=lambda b: -b["prominence"])
            if not feed:        # nothing here: Google zooms out to the nearest businesses
                feed = sorted(self.businesses, key=lambda b: (b["x"] - view.x) ** 2 + (b["y"] - view.y) ** 2)
        self.feed, self.loaded = feed[:120], min(20, len(feed))
        capture = RecordCapture(lambda u: False)
        for b in self.feed:
            capture.add({"place_id": b["id"], "name": b["name"], "website": b["website"]})
        return capture, None

    async def _feed_links(self, page):
        return [{"href": href(b), "name": b["name"], "reviews": "", "rating": "", "website": None}
                for b in self.feed[: self.loaded]]

    async def _feed_at_end(self, page):
        return self.loaded >= len(self.feed)

    async def _scroll_feed(self, page):
        self.loaded = min(self.loaded + 20, len(self.feed))

    async def _scrape_one(self, page, capture, ref, search_rec):
        b = self.by_id[ref.place_id]
        return PlaceRow(rank=ref.rank, name=b["name"], website=b["website"], phone=b["phone"], country="US",
                        place_id=b["id"], maps_url=ref.href.split("?", 1)[0])


def without_website(businesses):
    return {b["name"] for b in businesses if not b["website"]}


async def test_one_city_wide_search_only_sees_the_top_120():
    s = GeoScraper(city())
    rows = await s.scrape(Search("roofing in Houston"), 50, selector=PlaceFilter(LEADS))
    assert rows == [] and s.stats.links_found == 120
    assert s.stats.skipped == {"has a website": 120} and s.stats.stopped == "end_of_list"


async def test_whole_area_search_finds_the_businesses_below_the_top_120():
    biz = city()
    s = GeoScraper(biz)
    rows = await s.scrape(Search("roofing", area="Houston"), 50, selector=PlaceFilter(LEADS))
    assert {r.name for r in rows} == without_website(biz) and len(rows) == 30
    assert s.stats.links_found == len(biz), "every business was listed by some section"
    assert s.stats.sections == 1 + 4 + 16 and s.stats.dense == 0      # split while the lists were full
    assert s.stats.stopped == "searched_all"
    assert sorted(r.rank for r in rows) == sorted({r.rank for r in rows}), "one rank per place"
    assert all(url.startswith(f"{MAPS_BASE}/search/roofing/@") for url in s.opened)
    assert s.stats.skipped == {"has a website": 570}, "each place is checked once, however many sections list it"


async def test_whole_area_search_stops_at_the_limit():
    s = GeoScraper(city())
    rows = await s.scrape(Search("roofing", area="Houston"), 5, selector=PlaceFilter(LEADS))
    assert len(rows) == 5 and s.stats.stopped == "limit"
    assert s.stats.sections < 1 + 4 + 16


async def test_max_sections_caps_the_search():
    s = GeoScraper(city(), MAX_SECTIONS=5)
    rows = await s.scrape(Search("roofing", area="Houston"), 50, selector=PlaceFilter(LEADS))
    assert rows == [] and s.stats.sections == 5 and s.stats.stopped == "section_limit"


async def test_empty_sections_where_google_zooms_out_are_not_split():
    ne = ROOT.quarters()[1]                                    # north-east quarter
    biz = city(240, within=Section(ne.x, ne.y, ne.w / 2, ne.h / 2))   # a cluster in its middle
    s = GeoScraper(biz)
    rows = await s.scrape(Search("roofing", area="Houston"), 50, selector=PlaceFilter(LEADS))
    assert {r.name for r in rows} == without_website(biz)
    assert s.stats.sections == 1 + 4 + 4 and s.stats.stopped == "searched_all"


async def test_area_search_falls_back_to_a_text_search_when_the_map_cannot_be_read():
    s = GeoScraper(city(), root=None)
    await s.scrape(Search("roofing", area="Houston"), 50, selector=PlaceFilter(LEADS))
    assert s.opened == [s.search_url("roofing in Houston")] and s.stats.sections == 0


async def test_several_places_are_searched_in_turn_and_duplicates_dropped():
    biz = city(150)
    s = GeoScraper(biz)
    s.text_results = {"roofing in Katy": biz[:100], "roofing in Pasadena": biz[50:]}
    rows = await s.scrape([Search("roofing in Katy"), Search("roofing in Pasadena")], 500)
    assert len(rows) == 150 and sorted(r.rank for r in rows) == list(range(1, 151))
    assert s.stats.searches == 2 and s.stats.stopped == "searched_all"


async def test_a_failed_section_is_searched_again_as_quarters():
    biz = city()
    root_view = ROOT.url("roofing").rsplit("/@", 1)[1]
    s = GeoScraper(biz, fail=(root_view,))
    rows = await s.scrape(Search("roofing", area="Houston"), 50, selector=PlaceFilter(LEADS))
    assert len(rows) == 30 and len(s.stats.errors) == 1


async def test_a_failing_search_is_skipped_but_three_in_a_row_fail_the_job():
    biz = city()
    ok = GeoScraper(biz, fail=("roofing+in+Katy",))
    rows = await ok.scrape([Search("roofing in Katy"), Search("roofing in Pasadena")], 10)
    assert len(rows) == 10 and len(ok.stats.errors) == 1
    bad = GeoScraper(biz, fail=("/search/",))
    with pytest.raises(RuntimeError, match="3 searches in a row"):
        await bad.scrape(Search("roofing", area="Houston"), 10, selector=PlaceFilter(LEADS))
    assert bad.stats.sections == 3


async def test_a_single_failing_search_still_fails_the_job():
    s = GeoScraper(city(), fail=("/search/",))
    with pytest.raises(RuntimeError, match="feed did not appear"):
        await s.scrape("roofing in Houston", 10)
