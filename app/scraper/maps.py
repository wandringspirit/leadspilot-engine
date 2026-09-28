"""Playwright based Google Maps scraper.

Pipeline (see docs/SCRAPING.md for diagrams):

1. open ``https://www.google.com/maps/search/<query>`` (English UI forced)
2. scroll the results feed and collect place links - meanwhile capture the
   ``/search?tbm=map`` XHR responses, which carry a structured record for
   every listed place
3. visit the place pages with a small pool of tabs; capture the
   ``/maps/preview/place`` XHR (full record incl. description + review count)
   and read the DOM as a fallback
4. merge the sources into a ``PlaceRow`` and hand it to the caller via ``on_row``

Steps 2-4 run in rounds until ``limit`` places have been *kept*. Without
filters that is a single round (collect ``limit`` links, scrape them all).
With a selector (the job's filters, see app/filters.py) listings the feed
already rules out - e.g. they have a website when the search wants businesses
without one - are skipped without opening their page, every scraped place goes
through ``selector.accept()``, and the feed is scrolled further until enough
places matched or Google's list ends.

The scraper is a plain async class so it can be driven by the FastAPI job
manager and by the CLI alike.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote_plus

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from app.config import Settings, settings as default_settings
from app.models import PlaceRow
from app.scraper.parsing import (
    build_maps_search_url,
    country_to_code,
    expand_region,
    format_phone,
    normalise_website,
    parse_address,
    parse_rating,
    parse_review_count,
)
from app.scraper.records import (
    is_place_response,
    is_search_response,
    records_from_html,
    records_from_rpc,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, int, int], Awaitable[None] | None]
RowCallback = Callable[[PlaceRow], Awaitable[None] | None]

MAPS_BASE = "https://www.google.com/maps"
FEED_SELECTOR = 'div[role="feed"]'
PLACE_LINK_SELECTOR = 'div[role="feed"] a[href*="/maps/place/"]'
PLACE_HEADER_SELECTOR = "h1.DUwDvf, div[role='main'] h1"
END_OF_LIST_TEXTS = ("You've reached the end of the list", "reached the end of the list")
NO_RESULTS_TEXTS = ("No results", "can't find", "did not match any", "Google Maps can't find")
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PARTS = ("/maps/vt/", "googleusercontent.com", "gstatic.com/images", "doubleclick",
                     "google-analytics", "/maps/preview/log204")

# Collect place links (+ what the result card shows) from the feed.
# `website` is the href of the card's "Website" button, or null when the card shows
# none - absence is never trusted (layouts change), only presence is used.
FEED_LINKS_JS = r"""
(sel) => Array.from(document.querySelectorAll(sel)).map(a => {
  const card = a.closest('div[jsaction*="mouseover"]');
  const scope = card || a.parentElement;
  const text = (el) => (el ? (el.textContent || '').trim() : '');
  const reviews = scope ? text(scope.querySelector('span.UY7F9')) : '';
  const rating = scope ? text(scope.querySelector('span.MW4etd')) : '';
  const site = card ? card.querySelector('a[data-value="Website"], a[aria-label^="Visit"][aria-label$="website"]') : null;
  return { href: a.href, name: a.getAttribute('aria-label') || '', reviews, rating,
           website: site ? (site.getAttribute('href') || null) : null };
})
"""

# JavaScript evaluated inside a place page. Returns raw strings; Python cleans them.
DETAIL_JS = r"""
() => {
  const q = (sel, root = document) => root.querySelector(sel);
  const text = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const attr = (sel, name) => { const el = q(sel); return el ? (el.getAttribute(name) || '') : ''; };
  const strip = (value, prefix) => value.replace(new RegExp('^' + prefix + '\\s*', 'i'), '').trim();

  const name = text(q('h1.DUwDvf')) || text(q('h1'));
  const category = text(q('button.DkEaL')) || text(q('button[jsaction*="category"]'));

  let rating = '', reviews = '';
  const f7 = q('div.F7nice');
  if (f7) {
    rating = text(f7.querySelector('span[aria-hidden="true"]'));
    const reviewEl = f7.querySelector('span[aria-label*="review"]');
    reviews = reviewEl ? (reviewEl.getAttribute('aria-label') || text(reviewEl)) : '';
    if (!reviews) { const m = text(f7).match(/\(([\d,.\s]+[kKmM]?)\)/); if (m) reviews = m[1]; }
  }
  if (!reviews) {
    const anyReview = q('span[aria-label$="reviews"], span[aria-label$="review"]');
    if (anyReview) reviews = anyReview.getAttribute('aria-label') || '';
  }

  const address = strip(attr('button[data-item-id="address"]', 'aria-label'), 'Address:');
  const website = attr('a[data-item-id="authority"]', 'href') || attr('a[aria-label^="Website:"]', 'href');
  const phoneBtn = q('button[data-item-id^="phone:tel:"]');
  const phone = phoneBtn ? strip(phoneBtn.getAttribute('aria-label') || '', 'Phone:') : '';
  const phoneRaw = phoneBtn ? (phoneBtn.getAttribute('data-item-id') || '').replace('phone:tel:', '') : '';
  const description = text(q('div.PYvSYb'));
  const closed = !!q('span[aria-label*="Permanently closed"]');

  return { name, category, rating, reviews, address, website, phone, phoneRaw, description, closed,
           url: location.href };
}
"""


@dataclass
class PlaceRef:
    rank: int
    href: str
    name: str = ""
    card_reviews: str = ""
    card_rating: str = ""
    card_website: str | None = None   # href of the result card's Website button (None: none seen)
    website_hint: str | None = None   # website known before opening the page ("" = none, None = unknown)

    @property
    def place_id(self) -> str:
        return place_id_from_href(self.href)


@dataclass
class ScrapeStats:
    query: str = ""
    requested: int = 0
    links_found: int = 0      # listings read from the results feed
    scraped: int = 0          # place pages scraped
    matched: int = 0          # places kept (all scraped places when there is no filter)
    skipped: dict[str, int] = field(default_factory=dict)   # reason -> places left out by the filters
    failed: int = 0
    reached_end: bool = False
    # why the scan stopped: limit | end_of_list | no_results | single | stalled | scan_limit
    stopped: str = ""
    errors: list[str] = field(default_factory=list)


class PlaceSelector(Protocol):
    """Decides which places a scrape keeps - implemented by ``app.jobs.PlaceFilter``."""

    @property
    def active(self) -> bool:
        """False when every place is kept (the feed is then read like an unfiltered search)."""

    def prefilter(self, ref: PlaceRef) -> str:
        """Reason to skip a listing without opening its page ('' = open it)."""

    async def accept(self, row: PlaceRow) -> tuple[list[PlaceRow], str]:
        """The rows to keep for a scraped place (it may add rows, one per email), or ([], reason)."""


class RecordCapture:
    """Collects place records from XHR responses of one page."""

    def __init__(self, matcher: Callable[[str], bool]) -> None:
        self.matcher = matcher
        self.records: list[dict[str, Any]] = []
        self.by_place_id: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task] = set()

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        if not self.matcher(response.url):
            return
        task = asyncio.ensure_future(self._consume(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume(self, response: Response) -> None:
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001 - response body may be gone
            return
        for rec in records_from_rpc(body):
            self.add(rec)

    def add(self, rec: dict[str, Any]) -> None:
        self.records.append(rec)
        if rec.get("place_id"):
            self.by_place_id[rec["place_id"]] = rec

    async def settle(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def clear(self) -> None:
        self.records.clear()
        self.by_place_id.clear()


class MapsScraper:
    def __init__(
        self,
        cfg: Settings | None = None,
        *,
        headless: bool | None = None,
        on_progress: ProgressCallback | None = None,
        on_row: RowCallback | None = None,
    ) -> None:
        self.cfg = cfg or default_settings
        self.headless = self.cfg.HEADLESS if headless is None else headless
        self.on_progress = on_progress
        self.on_row = on_row
        self.stats = ScrapeStats()
        self._selector: PlaceSelector | None = None
        self._limit = 0
        self._seen: dict[str, PlaceRef] = {}
        self._feed_done = False
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def scrape(self, query: str, limit: int, *, selector: PlaceSelector | None = None) -> list[PlaceRow]:
        """Return up to ``limit`` places for ``query`` - with a selector, ``limit`` places it accepted."""
        limit = max(1, min(int(limit), self.cfg.MAX_RESULTS))
        self.stats = ScrapeStats(query=query, requested=limit)
        self._selector, self._limit = selector, limit
        self._seen, self._feed_done = {}, False
        started = time.monotonic()
        log.info("Starting %s Chromium for %r (%d %s wanted)", "headless" if self.headless else "headed",
                 query, limit, "matching place(s)" if self._filtered else "result(s)")
        await self._start()
        try:
            page = await self._context.new_page()  # type: ignore[union-attr]
            await self._progress("collecting", "Opening Google Maps", 0, limit)
            log.info("Opening %s", self.search_url(query))
            capture, single = await self._open_results(page, query)
            rows: list[PlaceRow] = []
            if single is not None:
                log.info("Google returned a single place directly: %s", single.name)
                self.stats.links_found = self.stats.scraped = 1
                self.stats.stopped = "single"
                rows, reason = await self._select(single)
                if rows:
                    self.stats.matched = 1
                    for row in rows:
                        await self._emit_row(row)
                else:
                    self._count_skip(reason)
                    log.info("%s does not match the filters: %s", single.name, reason)
            elif capture is not None:
                rows = await self._scan(page, capture, limit)
            await page.close()
            rows.sort(key=lambda r: r.rank)
            elapsed = time.monotonic() - started
            if self._filtered:
                skipped = ", ".join(f"{n} {reason}" for reason, n in
                                    sorted(self.stats.skipped.items(), key=lambda kv: -kv[1])) or "none"
                log.info("Scan finished in %.1fs: %d of %d matching place(s) from %d listing(s), "
                         "%d place page(s) opened, %d failed; skipped: %s", elapsed, self.stats.matched, limit,
                         self.stats.links_found, self.stats.scraped, self.stats.failed, skipped)
                await self._progress("done", f"Found {self.stats.matched} matching places in "
                                     f"{self.stats.links_found} listings", self.stats.matched, limit)
            else:
                log.info("Scraping finished: %d place(s) in %.1fs, %d failed", self.stats.matched, elapsed, self.stats.failed)
                await self._progress("done", f"Scraped {len(rows)} places", len(rows), len(rows) or limit)
            return rows
        finally:
            await self._stop()
            log.info("Browser closed")

    @property
    def _filtered(self) -> bool:
        return self._selector is not None and self._selector.active

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        self._pw = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                f"--lang={self.cfg.LOCALE}",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        }
        if self.cfg.PROXY_SERVER:
            launch_kwargs["proxy"] = {"server": self.cfg.PROXY_SERVER}
        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            locale=self.cfg.LOCALE,
            user_agent=self.cfg.USER_AGENT,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": f"{self.cfg.LOCALE},en;q=0.9"},
        )
        self._context.set_default_timeout(self.cfg.PAGE_TIMEOUT_MS)
        if self.cfg.BLOCK_MEDIA:
            await self._context.route("**/*", self._route_filter)

    async def _stop(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    await closer.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:  # pragma: no cover
                pass
        self._pw = self._browser = self._context = None

    @staticmethod
    async def _route_filter(route, request) -> None:
        if request.resource_type in BLOCKED_RESOURCE_TYPES or any(p in request.url for p in BLOCKED_URL_PARTS):
            await route.abort()
        else:
            await route.continue_()

    # ------------------------------------------------------------------
    # Phase 1: collect place links from the results feed
    # ------------------------------------------------------------------
    def search_url(self, query: str) -> str:
        url = f"{MAPS_BASE}/search/{quote_plus(query.strip())}/?hl=en"
        if self.cfg.REGION_CODE:
            url += f"&gl={self.cfg.REGION_CODE.lower()}"
        return url

    async def _open_results(self, page: Page, query: str) -> tuple[RecordCapture | None, PlaceRow | None]:
        """Open the search page.

        Returns ``(capture, None)`` when the results feed appeared (``capture``
        collects the feed's place records), ``(None, row)`` when Google jumped
        straight to a single place and ``(None, None)`` when it found nothing.
        """
        capture = RecordCapture(is_search_response)
        capture.attach(page)
        response = await page.goto(self.search_url(query), wait_until="domcontentloaded")
        await self._dismiss_consent(page)
        try:
            for rec in records_from_html(await response.text() if response else ""):
                capture.add(rec)
        except Exception:  # noqa: BLE001
            pass

        try:
            await page.wait_for_selector(f"{FEED_SELECTOR}, {PLACE_HEADER_SELECTOR}", timeout=self.cfg.PAGE_TIMEOUT_MS)
        except PlaywrightTimeout:
            pass

        if await page.query_selector(FEED_SELECTOR):
            return capture, None
        # Google jumps straight to the place when the query is unambiguous.
        if await page.query_selector(PLACE_HEADER_SELECTOR):
            log.info("No results feed - Google opened a place page directly")
            await self._wait_for_details(page)
            await capture.settle()
            ref = PlaceRef(rank=1, href=page.url)
            dom = await self._dom_details(page)
            rec = _pick_record(capture, ref.place_id, dom.get("name", ""))
            return None, build_row(ref, dom, rec, {}, default_region=self.cfg.DEFAULT_REGION)
        body = (await page.inner_text("body"))[:600] if await page.query_selector("body") else ""
        if any(t in body for t in NO_RESULTS_TEXTS):
            log.warning("Google Maps reports no results for %r", query)
            self.stats.reached_end = True
            self.stats.stopped = "no_results"
            return None, None
        log.error("Results feed did not appear; page url=%s title=%r", page.url, await page.title())
        raise RuntimeError("Google Maps results feed did not appear (blocked, captcha or layout change?)")

    async def _scan(self, page: Page, capture: RecordCapture, limit: int) -> list[PlaceRow]:
        """Read the feed and scrape places in rounds until ``limit`` places are kept.

        Each round asks the feed for as many *candidates* (listings the selector
        did not rule out) as places are still missing, then scrapes them. Without
        filters every candidate is kept, so one round collects ``limit`` links and
        scrapes them all. With filters, rejected places are replaced by scrolling
        further until enough matched or the feed has nothing left.
        """
        log.info("Results feed loaded, collecting links")
        kept: list[PlaceRow] = []
        rounds = 0
        while self.stats.matched < limit and not self._feed_done:
            refs = await self._next_candidates(page, capture, limit - self.stats.matched)
            self.stats.links_found = len(self._seen)
            if not refs:
                break
            rounds += 1
            if self._filtered:
                log.info("Round %d: %d candidate(s) to check, %d listing(s) scanned so far",
                         rounds, len(refs), len(self._seen))
            else:
                log.info("Collected %d place link(s)%s, %d structured record(s) captured from the results feed",
                         len(refs), " - end of Google's list reached" if self.stats.reached_end else "",
                         len(capture.by_place_id))
                await self._progress("details", f"Fetching details for {len(refs)} places", 0, len(refs))
            kept += await self._scrape_details(refs, capture.by_place_id)
        if self.stats.matched >= limit:
            self.stats.stopped = "limit"
        return kept

    async def _next_candidates(self, page: Page, capture: RecordCapture, want: int) -> list[PlaceRef]:
        """Scroll until ``want`` new candidates are found, or the feed ends / stalls / hits MAX_SCANNED."""
        seen, limit = self._seen, self._limit
        scan_cap = max(limit, self.cfg.MAX_SCANNED)
        candidates: list[PlaceRef] = []
        stalls = 0
        while len(candidates) < want:
            links = await self._feed_links(page)
            await capture.settle()  # records of the listings that just loaded
            before = len(seen)
            for link in links:
                key = link["href"].split("?", 1)[0]
                if key in seen:
                    continue
                if len(seen) >= scan_cap:
                    break
                ref = PlaceRef(rank=len(seen) + 1, href=link["href"], name=link.get("name", ""),
                               card_reviews=link.get("reviews", ""), card_rating=link.get("rating", ""),
                               card_website=link.get("website"))
                seen[key] = ref
                ref.website_hint = website_hint(ref.card_website, capture.by_place_id.get(ref.place_id))
                reason = self._selector.prefilter(ref) if self._filtered else ""  # type: ignore[union-attr]
                if reason:
                    self._count_skip(reason)
                    log.debug("Skipping #%d %s without opening it: %s", ref.rank, ref.name, reason)
                    continue
                candidates.append(ref)
                if len(candidates) >= want:
                    break  # links not read yet stay unseen and are picked up by the next round
            if self._filtered:
                await self._progress("collecting", f"Scanned {len(seen)} listings · {self.stats.matched} of "
                                     f"{limit} matches so far", self.stats.matched, limit)
                if len(seen) != before:
                    log.info("Feed: %d listing(s) scanned, %d new candidate(s)", len(seen), len(candidates))
            else:
                await self._progress("collecting", f"Found {len(seen)} of {limit} places", len(seen), limit)
                if len(seen) != before:
                    log.info("Feed: %d of %d place(s) found", len(seen), limit)
            if len(candidates) >= want:
                break
            if len(seen) >= scan_cap:
                log.warning("Stopped scanning after %d listing(s) (MAX_SCANNED)", len(seen))
                self._feed_done, self.stats.stopped = True, "scan_limit"
                break
            if await self._feed_at_end(page):
                log.info("Reached the end of Google's list at %d listing(s)", len(seen))
                self.stats.reached_end = self._feed_done = True
                self.stats.stopped = "end_of_list"
                break
            if stalls >= self.cfg.SCROLL_STALL_LIMIT:
                log.warning("Feed stopped growing after %d scroll(s) without new results (%d collected)",
                            stalls, len(seen))
                self._feed_done, self.stats.stopped = True, "stalled"
                break
            await self._scroll_feed(page)
            stalls = stalls + 1 if len(seen) == before else 0
        return candidates

    # Browser primitives of the feed loop (overridden in tests/test_scan.py).
    async def _feed_links(self, page: Page) -> list[dict[str, Any]]:
        return await page.evaluate(FEED_LINKS_JS, PLACE_LINK_SELECTOR)

    async def _feed_at_end(self, page: Page) -> bool:
        feed_text = await page.evaluate("(sel) => (document.querySelector(sel) || {}).innerText || ''", FEED_SELECTOR)
        return any(t in feed_text for t in END_OF_LIST_TEXTS)

    async def _scroll_feed(self, page: Page) -> None:
        await page.evaluate(
            "(sel) => { const f = document.querySelector(sel); if (f) f.scrollTop = f.scrollHeight; }",
            FEED_SELECTOR,
        )
        await page.wait_for_timeout(self.cfg.SCROLL_PAUSE_MS + random.randint(0, 400))

    async def _dismiss_consent(self, page: Page) -> None:
        """Handle the EU cookie consent interstitial if it shows up."""
        if "consent.google" not in page.url and not await page.query_selector('form[action*="consent"]'):
            return
        for selector in ('button:has-text("Accept all")', 'button:has-text("Reject all")',
                         'form[action*="consent"] button', 'button[aria-label*="Accept"]'):
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    log.info("Dismissed Google's consent dialog")
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Phase 2: visit the candidates' place pages
    # ------------------------------------------------------------------
    async def _scrape_details(self, refs: list[PlaceRef], search_records: dict[str, dict[str, Any]]) -> list[PlaceRow]:
        queue: asyncio.Queue[PlaceRef] = asyncio.Queue()
        for ref in refs:
            queue.put_nowait(ref)
        results: list[PlaceRow] = []
        done = 0
        total = len(refs)
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal done
            page = await self._context.new_page()  # type: ignore[union-attr]
            capture = RecordCapture(is_place_response)
            capture.attach(page)
            try:
                while True:
                    try:
                        ref = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    capture.clear()
                    row = await self._scrape_one(page, capture, ref, search_records.get(ref.place_id, {}))
                    kept, reason = await self._select(row)
                    async with lock:
                        results.extend(kept)
                        done += 1
                        self.stats.scraped += 1
                        if kept:
                            self.stats.matched += 1
                        else:
                            self._count_skip(reason)
                        if self._filtered:
                            await self._progress(
                                "details", f"{self.stats.matched} of {self._limit} matches so far · "
                                f"{self.stats.scraped} places checked, {len(self._seen)} listings scanned",
                                self.stats.matched, self._limit)
                        else:
                            await self._progress("details", f"Scraped {done} of {total}: {row.name or ref.name}", done, total)
                    log.info("#%d %s | %s | %s | %s reviews%s%s", ref.rank, row.name or ref.name,
                             row.phone or "no phone", row.website or "no website",
                             row.review_count if row.review_count is not None else "?",
                             "" if row.description else " | no description",
                             "" if not self._filtered else " | match" if kept else f" | skipped: {reason}")
                    for kept_row in kept:
                        await self._emit_row(kept_row)
                    await asyncio.sleep(random.uniform(self.cfg.MIN_DELAY_MS, self.cfg.MAX_DELAY_MS) / 1000)
            finally:
                await page.close()

        workers = [asyncio.create_task(worker()) for _ in range(max(1, min(self.cfg.DETAIL_CONCURRENCY, total)))]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            for w in workers:
                w.cancel()
            raise
        return results

    async def _scrape_one(self, page: Page, capture: RecordCapture, ref: PlaceRef, search_rec: dict[str, Any]) -> PlaceRow:
        last_error: Exception | None = None
        for attempt in range(self.cfg.DETAIL_RETRIES + 1):
            try:
                response = await page.goto(ref.href, wait_until="domcontentloaded")
                await self._dismiss_consent(page)
                await page.wait_for_selector(PLACE_HEADER_SELECTOR, timeout=self.cfg.PAGE_TIMEOUT_MS)
                await self._wait_for_details(page)
                # the full record arrives through the preview/place XHR - give it a moment
                for _ in range(16):
                    await capture.settle()
                    if capture.records:
                        break
                    await page.wait_for_timeout(250)
                if not capture.records:
                    try:
                        for rec in records_from_html(await response.text() if response else ""):
                            capture.add(rec)
                    except Exception:  # noqa: BLE001
                        pass
                dom = await self._dom_details(page)
                full_rec = _pick_record(capture, ref.place_id, dom.get("name") or ref.name)
                return build_row(ref, dom, full_rec, search_rec, default_region=self.cfg.DEFAULT_REGION)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - we retry on anything
                last_error = exc
                log.warning("Place #%d %s: attempt %d of %d failed: %s", ref.rank, ref.name, attempt + 1,
                            self.cfg.DETAIL_RETRIES + 1, str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__)
                await asyncio.sleep(1.0 + attempt)
        self.stats.failed += 1
        self.stats.errors.append(f"#{ref.rank} {ref.name}: {last_error}")
        log.error("Place #%d %s: giving up, using the results-feed data only", ref.rank, ref.name)
        row = build_row(ref, {}, {}, search_rec, default_region=self.cfg.DEFAULT_REGION)
        row.enrichment_note = f"details page failed: {last_error}"
        return row

    async def _wait_for_details(self, page: Page) -> None:
        try:
            await page.wait_for_selector(
                'button[data-item-id="address"], a[data-item-id="authority"], button[data-item-id^="phone"]',
                timeout=4000,
            )
        except PlaywrightTimeout:
            pass

    async def _dom_details(self, page: Page) -> dict[str, Any]:
        try:
            return await page.evaluate(DETAIL_JS) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("DOM extraction failed on %s: %s", page.url, exc)
            return {}

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    async def _select(self, row: PlaceRow) -> tuple[list[PlaceRow], str]:
        """The rows to keep for a scraped place, or ([], reason) when the selector rejects it."""
        if self._selector is None:
            return [row], ""
        try:
            return await self._selector.accept(row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad place must not end the scrape
            log.warning("Could not check #%d %s against the filters: %s", row.rank, row.name, exc)
            return [], "check failed"

    def _count_skip(self, reason: str) -> None:
        self.stats.skipped[reason] = self.stats.skipped.get(reason, 0) + 1

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    async def _progress(self, phase: str, message: str, current: int, total: int) -> None:
        if self.on_progress:
            result = self.on_progress(phase, message, current, total)
            if asyncio.iscoroutine(result):
                await result

    async def _emit_row(self, row: PlaceRow) -> None:
        if self.on_row:
            result = self.on_row(row)
            if asyncio.iscoroutine(result):
                await result


# ----------------------------------------------------------------------
# Row assembly (pure, unit-tested)
# ----------------------------------------------------------------------
_PLACE_ID_RE = re.compile(r"!19s(ChI[\w-]+)")
_LATLNG_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")


def place_id_from_href(href: str) -> str:
    m = _PLACE_ID_RE.search(href or "")
    return m.group(1) if m else ""


def website_hint(card_website: str | None, search_rec: dict[str, Any] | None) -> str | None:
    """A listing's website as far as the results feed tells, before its page is opened.

    The feed's structured record is trusted either way (no website there means
    none, ``""``); the card's Website button only ever *adds* a website. Without
    a record and without a button the answer is ``None`` - unknown, open the page.
    """
    if search_rec:
        return normalise_website(search_rec.get("website") or card_website or "")
    return normalise_website(card_website) if card_website else None


def _pick_record(capture: RecordCapture, place_id: str, name: str) -> dict[str, Any]:
    if place_id and place_id in capture.by_place_id:
        return capture.by_place_id[place_id]
    if name:
        for rec in reversed(capture.records):
            if rec.get("name", "").strip().lower() == name.strip().lower():
                return rec
    return capture.records[-1] if capture.records else {}


def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return ""


def build_row(
    ref: PlaceRef,
    dom: dict[str, Any],
    full_rec: dict[str, Any],
    search_rec: dict[str, Any],
    *,
    default_region: str = "US",
) -> PlaceRow:
    """Merge the data sources into one PlaceRow.

    Precedence: place-page record > search-page record > DOM > result card /
    link URL. Records are structured (no parsing needed); the DOM is what a
    human sees and is the fallback when Google reshuffles its JSON.
    """
    dom, full_rec, search_rec = dom or {}, full_rec or {}, search_rec or {}
    rec = {**search_rec, **{k: v for k, v in full_rec.items() if v not in (None, "", [])}}

    name = _first(rec.get("name"), dom.get("name"), ref.name)
    address = _first(rec.get("address"), dom.get("address"))
    parsed = parse_address(address)

    country = country_to_code(_first(rec.get("country"), parsed["country"]))
    city = _first(rec.get("city"), parsed["city"])
    region = expand_region(_first(rec.get("state"), parsed["state"]), country or default_region)

    website = normalise_website(_first(rec.get("website"), dom.get("website")))
    phone, phone_e164 = format_phone(_first(rec.get("phone"), dom.get("phoneRaw"), dom.get("phone")), country or default_region)

    review_count = parse_review_count(_first(rec.get("review_count"), dom.get("reviews"), ref.card_reviews))
    rating = parse_rating(_first(rec.get("rating"), dom.get("rating"), ref.card_rating))

    place_id = _first(rec.get("place_id"), ref.place_id)
    lat, lng = rec.get("latitude"), rec.get("longitude")
    if lat is None or lng is None:
        m = _LATLNG_RE.search(ref.href or "")
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))

    return PlaceRow(
        rank=ref.rank,
        name=name,
        city=city,
        state=region,
        country=country,
        website=website,
        phone=phone,
        phone_unformatted=phone_e164,
        phone_source="maps" if phone else "",
        url=build_maps_search_url(name, place_id),
        review_count=review_count,
        description=" ".join(str(_first(rec.get("description"), dom.get("description"))).split()),
        address=address,
        category=_first(rec.get("category"), dom.get("category")),
        rating=rating,
        place_id=place_id,
        maps_url=(ref.href or "").split("?", 1)[0],
        latitude=lat if isinstance(lat, (int, float)) else None,
        longitude=lng if isinstance(lng, (int, float)) else None,
    )
