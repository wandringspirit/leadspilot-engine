"""The feed-scanning loop of MapsScraper, driven by an in-memory results feed.

Only the browser primitives are replaced (`_start`, `_open_results`,
`_feed_links`, `_feed_at_end`, `_scroll_feed`, `_scrape_one`); the rounds,
pre-filtering, acceptance and statistics are the real code.
"""
from __future__ import annotations

from app.config import Settings
from app.models import PlaceRow
from app.scraper.maps import MapsScraper, RecordCapture, website_hint


def href(i: int) -> str:
    return (f"https://www.google.com/maps/place/Place+{i}/data=!4m7!3m6!1s0x0:0x{i}!8m2!3d30.2!4d-97.7"
            f"!16s%2Fg%2F1!19sChIJplace{i}?authuser=0&hl=en")


def listing(website: str = "", *, phone: str = "(512) 555-0100", record: bool = True,
            card_website: str | None = None, page_website: str | None = None) -> dict:
    """One result. `website` is what the feed's record says, `page_website` what the place page shows."""
    return {"website": website, "phone": phone, "record": record, "card_website": card_website,
            "page_website": website if page_website is None else page_website}


class FakePage:
    def on(self, *_args) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeContext:
    async def new_page(self) -> FakePage:
        return FakePage()


class FeedScraper(MapsScraper):
    """MapsScraper whose browser is an in-memory results feed that loads `page_size` results per scroll."""

    def __init__(self, listings: list[dict], *, page_size: int = 20, end_text: bool = True, **settings) -> None:
        cfg = Settings(MIN_DELAY_MS=0, MAX_DELAY_MS=0, SCROLL_STALL_LIMIT=2, DETAIL_CONCURRENCY=3, **settings)
        self.emitted: list[PlaceRow] = []
        self.progress: list[tuple] = []
        super().__init__(cfg, headless=True, on_row=self.emitted.append,
                         on_progress=lambda *event: self.progress.append(event))
        self.listings, self.page_size, self.end_text = listings, page_size, end_text
        self.loaded = min(page_size, len(listings))
        self.visited: list[int] = []
        self.scrolls = 0

    async def _start(self) -> None:
        self._context = FakeContext()

    async def _stop(self) -> None:
        self._context = None

    async def _open_results(self, page, url, label):
        capture = RecordCapture(lambda url: False)
        for i, item in enumerate(self.listings, start=1):
            if item["record"]:
                capture.add({"place_id": f"ChIJplace{i}", "name": f"Place {i}", "website": item["website"]})
        return capture, None

    async def _feed_links(self, page):
        return [{"href": href(i), "name": f"Place {i}", "reviews": "", "rating": "",
                 "website": item["card_website"]}
                for i, item in enumerate(self.listings[: self.loaded], start=1)]

    async def _feed_at_end(self, page) -> bool:
        return self.end_text and self.loaded >= len(self.listings)

    async def _scroll_feed(self, page) -> None:
        self.scrolls += 1
        self.loaded = min(self.loaded + self.page_size, len(self.listings))

    async def _scrape_one(self, page, capture, ref, search_rec) -> PlaceRow:
        self.visited.append(ref.rank)
        item = self.listings[ref.rank - 1]
        return PlaceRow(rank=ref.rank, name=ref.name, website=item["page_website"], phone=item["phone"])


class NoWebsiteWithPhone:
    """Selector keeping places without a website that have a phone."""

    active = True

    def prefilter(self, ref) -> str:
        return "has a website" if ref.website_hint else ""   # "" = none, None = unknown -> open it

    async def accept(self, row: PlaceRow):
        if row.website:
            return [], "has a website"
        if not row.phone:
            return [], "no phone"
        return [row], ""


def every_fifth_without_website(n: int) -> list[dict]:
    return [listing("" if i % 5 == 0 else f"https://site{i}.com") for i in range(1, n + 1)]


async def test_without_filters_one_round_collects_limit_and_scrapes_all():
    scraper = FeedScraper([listing(f"https://site{i}.com") for i in range(1, 51)])
    rows = await scraper.scrape("plumbers in Austin", 10)
    assert [r.rank for r in rows] == list(range(1, 11))
    assert sorted(scraper.visited) == list(range(1, 11))
    assert (scraper.stats.links_found, scraper.stats.scraped, scraper.stats.matched) == (10, 10, 10)
    assert scraper.stats.skipped == {} and scraper.scrolls == 0
    assert scraper.stats.stopped == "limit"


async def test_inactive_selector_never_skips_listings():
    class KeepAll(NoWebsiteWithPhone):
        active = False

        async def accept(self, row):
            return [row], ""

    scraper = FeedScraper([listing(f"https://site{i}.com") for i in range(1, 51)])
    rows = await scraper.scrape("plumbers in Austin", 5, selector=KeepAll())
    assert [r.rank for r in rows] == [1, 2, 3, 4, 5]
    assert scraper.stats.skipped == {} and scraper.progress[-1][0] == "done"
    assert scraper.progress[-1][1] == "Scraped 5 places"


async def test_listings_with_a_website_are_skipped_without_opening_them():
    scraper = FeedScraper(every_fifth_without_website(50))
    rows = await scraper.scrape("plumbers in Austin", 4, selector=NoWebsiteWithPhone())
    assert [r.rank for r in rows] == [5, 10, 15, 20]              # ranks keep their place in Google's list
    assert sorted(scraper.visited) == [5, 10, 15, 20], "only candidates are opened"
    assert scraper.stats.skipped == {"has a website": 16}
    assert (scraper.stats.links_found, scraper.stats.matched, scraper.stats.stopped) == (20, 4, "limit")
    assert sorted(r.rank for r in scraper.emitted) == [5, 10, 15, 20]
    assert scraper.progress[-1] == ("done", "Found 4 matching places in 20 listings", 4, 4)
    assert all(total == 4 for *_, total in scraper.progress), "filtered progress counts matches out of the limit"


async def test_scrolls_further_until_enough_matches():
    scraper = FeedScraper(every_fifth_without_website(50))
    rows = await scraper.scrape("plumbers in Austin", 6, selector=NoWebsiteWithPhone())
    assert [r.rank for r in rows] == [5, 10, 15, 20, 25, 30]
    assert scraper.scrolls == 1 and scraper.stats.links_found == 30   # stopped reading at the 6th candidate


async def test_rejected_candidates_are_replaced_from_further_down():
    listings = every_fifth_without_website(50)
    listings[4] = listing("", page_website="https://five.com")      # feed says none, the place page has one
    listings[9] = listing("", phone="")                              # no phone
    scraper = FeedScraper(listings)
    rows = await scraper.scrape("plumbers in Austin", 3, selector=NoWebsiteWithPhone())
    assert [r.rank for r in rows] == [15, 20, 25]
    assert scraper.stats.skipped == {"has a website": 20 + 1, "no phone": 1}
    assert scraper.stats.matched == 3 and scraper.stats.scraped == 5


async def test_fewer_matches_when_googles_list_ends():
    scraper = FeedScraper(every_fifth_without_website(12))
    rows = await scraper.scrape("plumbers in Austin", 5, selector=NoWebsiteWithPhone())
    assert [r.rank for r in rows] == [5, 10]
    assert scraper.stats.reached_end and scraper.stats.links_found == 12
    assert scraper.stats.stopped == "end_of_list"


async def test_card_website_button_skips_and_unknown_listings_are_opened():
    listings = [listing(record=False, card_website="https://acme.com"),   # card shows a Website button
                listing(record=False),                                   # nothing known -> must open it
                listing(record=False, page_website="https://late.com")]  # unknown, page reveals a website
    scraper = FeedScraper(listings)
    rows = await scraper.scrape("plumbers in Austin", 3, selector=NoWebsiteWithPhone())
    assert [r.rank for r in rows] == [2]
    assert sorted(scraper.visited) == [2, 3]
    assert scraper.stats.skipped == {"has a website": 2}


async def test_accepted_place_can_add_rows_and_limit_counts_places():
    class TwoEmails(NoWebsiteWithPhone):
        async def accept(self, row):
            return [row.model_copy(update={"email": "a@x.com"}), row.model_copy(update={"email": "b@x.com"})], ""

    scraper = FeedScraper(every_fifth_without_website(50))
    rows = await scraper.scrape("plumbers in Austin", 2, selector=TwoEmails())
    assert [(r.rank, r.email) for r in rows] == [(5, "a@x.com"), (5, "b@x.com"), (10, "a@x.com"), (10, "b@x.com")]
    assert scraper.stats.matched == 2


async def test_scan_stops_at_max_scanned():
    scraper = FeedScraper([listing(f"https://site{i}.com") for i in range(1, 101)], MAX_SCANNED=25)
    rows = await scraper.scrape("plumbers in Austin", 3, selector=NoWebsiteWithPhone())
    assert rows == [] and scraper.visited == []
    assert scraper.stats.links_found == 25 and not scraper.stats.reached_end
    assert scraper.stats.stopped == "scan_limit"


async def test_stalled_feed_stops_after_scroll_stall_limit():
    scraper = FeedScraper([listing(f"https://site{i}.com") for i in range(1, 21)], end_text=False)
    rows = await scraper.scrape("plumbers in Austin", 3, selector=NoWebsiteWithPhone())
    assert rows == [] and not scraper.stats.reached_end
    assert scraper.scrolls == scraper.cfg.SCROLL_STALL_LIMIT + 1
    assert scraper.stats.stopped == "stalled"


async def test_single_place_result_goes_through_the_selector():
    class SinglePlace(FeedScraper):
        async def _open_results(self, page, url, label):
            return None, PlaceRow(rank=1, name="Only One", website="https://only.com", phone="1")

    scraper = SinglePlace([])
    assert await scraper.scrape("only one plumber", 5, selector=NoWebsiteWithPhone()) == []
    assert scraper.stats.skipped == {"has a website": 1} and scraper.stats.stopped == "single"
    assert [r.name for r in await SinglePlace([]).scrape("only one plumber", 5)] == ["Only One"]


def test_website_hint():
    assert website_hint(None, {"website": "https://acme.com/?utm_source=gmb"}) == "https://acme.com/"
    assert website_hint(None, {"website": ""}) == ""                             # record: none
    assert website_hint("https://card.com", {"website": ""}) == "https://card.com"  # button adds evidence
    assert website_hint("https://card.com", None) == "https://card.com"
    assert website_hint(None, None) is None                                        # unknown
