"""Google's "unusual traffic" check: recognising it and stopping the scrape with what was found."""
import asyncio

import pytest

from app.scraper.maps import GoogleBlocked, RecordCapture, Search, blocked_ip, is_blocked_page
from tests.test_area_search import GeoScraper, city
from tests.test_scan import FeedScraper, listing

SORRY_URL = ("https://www.google.com/sorry/index?continue=https://www.google.com/maps/search/roofing%2Bin%2BHouston/"
             "&q=EgTLAHEHGJ2u")
SORRY_TEXT = ("About this page\n\nOur systems have detected unusual traffic from your computer network. This page "
              "checks to see if it's really you sending the requests, and not a robot.\nWhy did this happen?\n\n"
              "IP address: 203.0.113.7\nTime: 2026-09-28T17:02:11Z\nURL: https://www.google.com/maps/search/roofing")
HARD_BLOCK_TEXT = ("We're sorry...\n... but your computer or network may be sending automated queries. To protect "
                   "our users, we can't process your request right now.")
MAPS_URL = "https://www.google.com/maps/search/roofing+in+Houston/?hl=en"


@pytest.mark.parametrize("url, text, status, expected", [
    (SORRY_URL, "", None, True),
    ("https://www.google.co.uk/sorry/index?continue=https://www.google.co.uk/maps", "", None, True),
    (MAPS_URL, SORRY_TEXT, None, True),
    (MAPS_URL, HARD_BLOCK_TEXT, None, True),
    (MAPS_URL, "", 429, True),
    (MAPS_URL, "Roofing contractors · Houston, TX · Open 24 hours", 200, False),
    ("https://example.com/sorry/index", "", None, False),
])
def test_is_blocked_page(url, text, status, expected):
    assert is_blocked_page(url, text, status) is expected


def test_blocked_ip():
    assert blocked_ip(SORRY_TEXT) == "203.0.113.7"
    assert blocked_ip("IP address: 2001:db8::7\nTime: …") == "2001:db8::7"
    assert blocked_ip(HARD_BLOCK_TEXT) == "" and blocked_ip("") == ""


def test_google_blocked_message():
    assert str(GoogleBlocked(SORRY_URL, "203.0.113.7")) == \
        "Google showed its “unusual traffic” check instead of Google Maps (IP address 203.0.113.7)"
    assert GoogleBlocked(SORRY_URL).ip == "" and GoogleBlocked(SORRY_URL).url == SORRY_URL


async def test_record_capture_notes_answers_google_refused():
    class Answer:
        url = "https://www.google.com/search?tbm=map&q=roofing"

        def __init__(self, status: int) -> None:
            self.status = status

        async def text(self) -> str:
            return ""

    capture = RecordCapture(lambda url: True)
    capture._on_response(Answer(200))
    assert not capture.rate_limited
    capture._on_response(Answer(429))
    await capture.settle()
    assert capture.rate_limited


# ---------------------------------------------------------------------------
# the scraper stops at once and keeps what it found
# ---------------------------------------------------------------------------
class BlockingGeo(GeoScraper):
    """GeoScraper whose Google answers with the "unusual traffic" check after ``searches`` result lists,
    or on the place page after ``places`` place pages (only that one - later pages would work, so the
    other tabs have to be stopped, not fail by chance)."""

    def __init__(self, businesses, *, searches: int | None = None, places: int | None = None, **settings):
        super().__init__(businesses, **settings)
        self.block_searches, self.block_places, self.pages = searches, places, 0
        self.emitted: list = []
        self.on_row = self.emitted.append

    async def _open_results(self, page, url, label):
        if self.block_searches is not None and len(self.opened) >= self.block_searches:
            self.opened.append(url)
            raise GoogleBlocked(SORRY_URL, "203.0.113.7")
        return await super()._open_results(page, url, label)

    async def _scrape_one(self, page, capture, ref, search_rec):
        self.pages += 1
        if self.block_places is not None and self.pages == self.block_places + 1:
            raise GoogleBlocked(SORRY_URL)
        return await super()._scrape_one(page, capture, ref, search_rec)


async def test_a_blocked_whole_area_search_stops_and_keeps_what_it_found():
    biz = city()
    blocked = BlockingGeo(biz, searches=3)
    rows = await blocked.scrape(Search("roofing", area="Houston"), 500)
    assert blocked.stats.stopped == "blocked" and blocked.stats.blocked_ip == "203.0.113.7"
    assert len(blocked.opened) == 4, "nothing is searched after the block - not even the blocked section's quarters"

    three = GeoScraper(biz, MAX_SECTIONS=3)                 # the same search, stopped after 3 sections
    expected = await three.scrape(Search("roofing", area="Houston"), 500)
    assert rows and [(r.rank, r.name) for r in rows] == [(r.rank, r.name) for r in expected]
    assert blocked.stats.matched == len(rows) and not blocked.stats.errors


async def test_a_block_on_the_first_search_ends_the_scrape_without_raising():
    s = BlockingGeo(city(), searches=0)
    assert await s.scrape("roofing in Houston", 20) == []
    assert s.stats.stopped == "blocked" and len(s.opened) == 1


async def test_place_pages_stop_when_one_shows_the_check():
    s = BlockingGeo(city(), places=5)                  # the 6th place page opened shows Google's check
    rows = await s.scrape("roofing in Houston", 20)
    opened, kept = s.pages, len(s.emitted)
    await asyncio.sleep(0.05)
    assert (s.pages, len(s.emitted)) == (opened, kept), "the other tabs were stopped as well"
    assert s.stats.stopped == "blocked" and s.stats.failed == 0, "no retries, no half-empty rows"
    assert 5 <= len(rows) < 20 and len(rows) == kept


async def test_later_places_are_not_searched_after_a_block():
    s = BlockingGeo(city(), searches=1)
    rows = await s.scrape([Search("roofing in Katy"), Search("roofing in Pasadena"), Search("roofing in Pearland")],
                          200)
    assert len(s.opened) == 2 and s.stats.stopped == "blocked" and len(rows) == 120


async def test_a_feed_that_stops_because_google_refuses_its_requests_counts_as_blocked():
    class RefusedFeed(FeedScraper):
        async def _open_results(self, page, url, label):
            capture, single = await super()._open_results(page, url, label)
            capture.rate_limited = True
            return capture, single

    s = RefusedFeed([listing("") for _ in range(10)], end_text=False)
    rows = await s.scrape("plumbers in Austin", 50)
    assert s.stats.stopped == "blocked" and len(rows) == 10

    plain = FeedScraper([listing("") for _ in range(10)], end_text=False)
    await plain.scrape("plumbers in Austin", 50)
    assert plain.stats.stopped == "stalled"
