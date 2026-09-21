"""Playwright based Google Maps scraper.

Pipeline (see docs/SCRAPING.md for diagrams):

1. open ``https://www.google.com/maps/search/<query>`` (English UI forced)
2. scroll the results feed until ``limit`` place links are collected or Google
   says the list has ended - meanwhile capture the ``/search?tbm=map`` XHR
   responses, which carry a structured record for every listed place
3. visit every place page with a small pool of tabs; capture the
   ``/maps/preview/place`` XHR (full record incl. description + review count)
   and read the DOM as a fallback
4. merge the sources into a ``PlaceRow`` and hand it to the caller via ``on_row``

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
from typing import Any, Awaitable, Callable
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
FEED_LINKS_JS = r"""
(sel) => Array.from(document.querySelectorAll(sel)).map(a => {
  const card = a.closest('div[jsaction*="mouseover"]') || a.parentElement;
  const text = (el) => (el ? (el.textContent || '').trim() : '');
  const reviews = card ? text(card.querySelector('span.UY7F9')) : '';
  const rating = card ? text(card.querySelector('span.MW4etd')) : '';
  return { href: a.href, name: a.getAttribute('aria-label') || '', reviews, rating };
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

    @property
    def place_id(self) -> str:
        return place_id_from_href(self.href)


@dataclass
class ScrapeStats:
    query: str = ""
    requested: int = 0
    links_found: int = 0
    scraped: int = 0
    failed: int = 0
    reached_end: bool = False
    errors: list[str] = field(default_factory=list)


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
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def scrape(self, query: str, limit: int) -> list[PlaceRow]:
        limit = max(1, min(int(limit), self.cfg.MAX_RESULTS))
        self.stats = ScrapeStats(query=query, requested=limit)
        started = time.monotonic()
        log.info("Starting %s Chromium for %r (%d result(s) wanted)", "headless" if self.headless else "headed", query, limit)
        await self._start()
        try:
            page = await self._context.new_page()  # type: ignore[union-attr]
            await self._progress("collecting", "Opening Google Maps", 0, limit)
            log.info("Opening %s", self.search_url(query))
            refs, search_records, single = await self._collect_place_links(page, query, limit)
            self.stats.links_found = len(refs) + (1 if single else 0)
            await page.close()
            if single is not None:
                log.info("Google returned a single place directly: %s", single.name)
            else:
                log.info("Collected %d place link(s)%s, %d structured record(s) captured from the results feed",
                         len(refs), " - end of Google's list reached" if self.stats.reached_end else "",
                         len(search_records))

            rows: list[PlaceRow] = []
            if single is not None:
                rows.append(single)
                await self._emit_row(single)
            elif refs:
                await self._progress("details", f"Fetching details for {len(refs)} places", 0, len(refs))
                rows = await self._scrape_details(refs, search_records)
            rows.sort(key=lambda r: r.rank)
            self.stats.scraped = len(rows)
            log.info("Scraping finished: %d place(s) in %.1fs, %d failed", len(rows), time.monotonic() - started, self.stats.failed)
            await self._progress("done", f"Scraped {len(rows)} places", len(rows), len(rows) or limit)
            return rows
        finally:
            await self._stop()
            log.info("Browser closed")

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

    async def _collect_place_links(
        self, page: Page, query: str, limit: int
    ) -> tuple[list[PlaceRef], dict[str, dict[str, Any]], PlaceRow | None]:
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

        feed = await page.query_selector(FEED_SELECTOR)
        if not feed:
            # Google jumps straight to the place when the query is unambiguous.
            if await page.query_selector(PLACE_HEADER_SELECTOR):
                log.info("No results feed - Google opened a place page directly")
                await self._wait_for_details(page)
                await capture.settle()
                ref = PlaceRef(rank=1, href=page.url)
                dom = await self._dom_details(page)
                rec = _pick_record(capture, ref.place_id, dom.get("name", ""))
                return [], capture.by_place_id, build_row(ref, dom, rec, {}, default_region=self.cfg.DEFAULT_REGION)
            body = (await page.inner_text("body"))[:600] if await page.query_selector("body") else ""
            if any(t in body for t in NO_RESULTS_TEXTS):
                log.warning("Google Maps reports no results for %r", query)
                self.stats.reached_end = True
                return [], {}, None
            log.error("Results feed did not appear; page url=%s title=%r", page.url, await page.title())
            raise RuntimeError("Google Maps results feed did not appear (blocked, captcha or layout change?)")

        log.info("Results feed loaded, collecting links")
        seen: dict[str, PlaceRef] = {}
        stalls = 0
        while len(seen) < limit and stalls < self.cfg.SCROLL_STALL_LIMIT:
            links = await page.evaluate(FEED_LINKS_JS, PLACE_LINK_SELECTOR)
            before = len(seen)
            for link in links:
                key = link["href"].split("?", 1)[0]
                if key not in seen and len(seen) < limit:
                    seen[key] = PlaceRef(rank=len(seen) + 1, href=link["href"], name=link["name"],
                                         card_reviews=link.get("reviews", ""), card_rating=link.get("rating", ""))
            await self._progress("collecting", f"Found {len(seen)} of {limit} places", len(seen), limit)
            if len(seen) != before:
                log.info("Feed: %d of %d place(s) found", len(seen), limit)
            if len(seen) >= limit:
                break

            feed_text = await page.evaluate("(sel) => (document.querySelector(sel) || {}).innerText || ''", FEED_SELECTOR)
            if any(t in feed_text for t in END_OF_LIST_TEXTS):
                log.info("Reached the end of Google's list at %d place(s)", len(seen))
                self.stats.reached_end = True
                break

            await page.evaluate(
                "(sel) => { const f = document.querySelector(sel); if (f) f.scrollTop = f.scrollHeight; }",
                FEED_SELECTOR,
            )
            await page.wait_for_timeout(self.cfg.SCROLL_PAUSE_MS + random.randint(0, 400))
            stalls = stalls + 1 if len(seen) == before else 0
        if stalls >= self.cfg.SCROLL_STALL_LIMIT:
            log.warning("Feed stopped growing after %d scroll(s) without new results (%d collected)", stalls, len(seen))

        await capture.settle()
        return list(seen.values()), capture.by_place_id, None

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
    # Phase 2: visit every place page
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
                    async with lock:
                        results.append(row)
                        done += 1
                        await self._progress("details", f"Scraped {done} of {total}: {row.name or ref.name}", done, total)
                    log.info("#%d %s | %s | %s | %s reviews%s", ref.rank, row.name or ref.name,
                             row.phone or "no phone", row.website or "no website",
                             row.review_count if row.review_count is not None else "?",
                             "" if row.description else " | no description")
                    await self._emit_row(row)
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
