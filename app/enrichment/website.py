"""Fetch a business website and discover contact emails / phone numbers.

Strategy (see docs/ENRICHMENT.md):

1. GET the homepage with httpx (fast, no browser)
2. GET up to ``ENRICH_MAX_PAGES`` likely contact pages (links found on the
   homepage that look like contact/about pages + well-known paths)
3. if nothing was found and the site looks JavaScript-rendered, render the
   homepage + first contact page with Chromium and try again

A *profile page* (Facebook page, Yelp listing, booking page, ... - see
``PROFILE_DOMAINS``) is the one page on that platform that belongs to the
business, so only that page is fetched (and rendered if needed): the
platform's own /contact or /about pages say nothing about the business.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from app.config import Settings, settings as default_settings
from app.enrichment.extractors import (
    EmailHit,
    ExtractResult,
    candidate_pages,
    extract_from_html,
    set_default_region,
)
from app.scraper.parsing import normalise_website, website_type

log = logging.getLogger(__name__)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


@dataclass
class ContactResult:
    website: str
    emails: list[EmailHit] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    pages_checked: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)   # links to other sites found on the pages
    used_browser: bool = False
    error: str | None = None

    @property
    def email_addresses(self) -> list[str]:
        seen: list[str] = []
        for hit in self.emails:
            if hit.email not in seen:
                seen.append(hit.email)
        return seen


class WebsiteEnricher:
    def __init__(self, cfg: Settings | None = None, *, region: str = "US") -> None:
        self.cfg = cfg or default_settings
        self.region = region or self.cfg.DEFAULT_REGION
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WebsiteEnricher":
        self._client = httpx.AsyncClient(
            headers={**HEADERS, "User-Agent": self.cfg.USER_AGENT},
            timeout=httpx.Timeout(self.cfg.ENRICH_TIMEOUT_S),
            follow_redirects=True,
            verify=False,  # many small-business sites have broken certificate chains
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    async def find_contacts(self, website: str, *, region: str | None = None) -> ContactResult:
        site = normalise_website(website)
        result = ContactResult(website=site)
        if not site:
            result.error = "no website"
            return result
        set_default_region(region or self.region)
        profile = website_type(site) == "profile"

        log.info("Enriching %s%s", site, " (profile page - only that page is read)" if profile else "")
        try:
            home_html, final_url = await self._fetch(site)
        except Exception as exc:  # noqa: BLE001
            result.error = f"homepage fetch failed: {exc.__class__.__name__}: {exc}"
            home_html, final_url = "", site
            log.info("%s: homepage fetch failed (%s: %s)", site, exc.__class__.__name__,
                     str(exc).splitlines()[0] if str(exc) else "")

        merged = ExtractResult()
        if home_html:
            result.pages_checked.append(final_url)
            self._merge(merged, extract_from_html(home_html, final_url, site))

        pages = [] if profile else candidate_pages(final_url or site, merged.contact_links, self.cfg.ENRICH_MAX_PAGES)
        if pages and not (merged.emails and merged.phones):
            fetched = await asyncio.gather(*(self._safe_fetch(u) for u in pages))
            for url, html in fetched:
                if html:
                    result.pages_checked.append(url)
                    self._merge(merged, extract_from_html(html, url, site))

        # JS-rendered sites and sites that block plain HTTP clients (403 etc.)
        # get a second chance with a real browser.
        if not merged.emails and self.cfg.ENRICH_BROWSER_FALLBACK and (not home_html or _looks_js_rendered(home_html)):
            log.info("%s: no email in plain HTML, rendering with Chromium", site)
            try:
                result.used_browser = True
                rendered = await self._render_with_browser(site, merged, result, follow_links=not profile)
                for url, html in rendered:
                    if html:
                        result.pages_checked.append(url + " (rendered)")
                        self._merge(merged, extract_from_html(html, url, site))
            except Exception as exc:  # noqa: BLE001
                log.info("browser fallback failed for %s: %s", site, exc)
                if not result.error:
                    result.error = f"browser fallback failed: {exc.__class__.__name__}"

        result.emails = sorted(merged.emails, key=lambda h: (-h.score, h.email))
        result.phones = merged.phones
        result.external_links = merged.external_links
        if result.error and (result.emails or result.phones):
            result.error = None
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _merge(target: ExtractResult, found: ExtractResult) -> None:
        known = {h.email for h in target.emails}
        for hit in found.emails:
            if hit.email not in known:
                target.emails.append(hit)
                known.add(hit.email)
        for p in found.phones:
            if p not in target.phones:
                target.phones.append(p)
        for link in found.contact_links:
            if link not in target.contact_links:
                target.contact_links.append(link)
        for link in found.external_links:
            if link not in target.external_links:
                target.external_links.append(link)

    async def _fetch(self, url: str) -> tuple[str, str]:
        assert self._client is not None, "use `async with WebsiteEnricher()`"
        resp = await self._client.get(url)
        ctype = resp.headers.get("content-type", "")
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
        if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            return "", str(resp.url)
        if len(resp.content) > self.cfg.ENRICH_MAX_BYTES:
            return resp.text[: self.cfg.ENRICH_MAX_BYTES], str(resp.url)
        return resp.text, str(resp.url)

    async def fetch_page(self, url: str) -> tuple[str, str]:
        """(final URL, HTML) of one page; the HTML is '' when it cannot be fetched."""
        return await self._safe_fetch(url)

    async def _safe_fetch(self, url: str) -> tuple[str, str]:
        try:
            html, final = await self._fetch(url)
            return final, html
        except Exception:  # noqa: BLE001 - 404s etc. are expected for guessed paths
            return url, ""

    async def _render_with_browser(self, site: str, merged: ExtractResult, result: ContactResult, *,
                                   follow_links: bool = True) -> list[tuple[str, str]]:
        """Render the homepage, then (``follow_links``) the most promising contact pages, with Chromium."""
        from playwright.async_api import async_playwright

        out: list[tuple[str, str]] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                context = await browser.new_context(user_agent=self.cfg.USER_AGENT, ignore_https_errors=True,
                                                    locale="en-US")
                context.set_default_timeout(int(self.cfg.ENRICH_TIMEOUT_S * 1000))
                page = await context.new_page()

                async def render(url: str) -> str:
                    try:
                        await page.goto(url, wait_until="domcontentloaded")
                        try:
                            await page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:  # noqa: BLE001 - busy sites never go idle
                            pass
                        await page.wait_for_timeout(500)
                        return await page.content()
                    except Exception as exc:  # noqa: BLE001
                        log.info("render failed for %s: %s", url, exc)
                        return ""

                home = await render(site)
                out.append((site, home))
                if home:
                    found = extract_from_html(home, page.url, site)
                    self._merge(merged, found)
                    if merged.emails or not follow_links:
                        return out
                    for url in candidate_pages(page.url, found.contact_links, min(3, self.cfg.ENRICH_MAX_PAGES)):
                        html = await render(url)
                        out.append((url, html))
                        if html:
                            self._merge(merged, extract_from_html(html, url, site))
                            if merged.emails:
                                break
            finally:
                await browser.close()
        return out


def _looks_js_rendered(html: str) -> bool:
    """Heuristic: almost no text but plenty of script tags -> SPA."""
    if not html:
        return True
    lowered = html.lower()
    script_count = lowered.count("<script")
    text_len = len(lowered.split("<body", 1)[-1])
    return script_count >= 3 and text_len < 15_000 or "id=\"root\"" in lowered or "id=\"__next\"" in lowered


async def enrich_many(
    websites: dict[str, str],
    *,
    cfg: Settings | None = None,
    region: str = "US",
    on_result=None,
) -> dict[str, ContactResult]:
    """Enrich several websites concurrently. ``websites`` maps key -> url."""
    cfg = cfg or default_settings
    semaphore = asyncio.Semaphore(cfg.ENRICH_CONCURRENCY)
    results: dict[str, ContactResult] = {}

    async with WebsiteEnricher(cfg, region=region) as enricher:
        async def run(key: str, url: str) -> None:
            async with semaphore:
                res = await enricher.find_contacts(url)
            results[key] = res
            if on_result:
                maybe = on_result(key, res)
                if asyncio.iscoroutine(maybe):
                    await maybe

        await asyncio.gather(*(run(k, u) for k, u in websites.items()))
    return results
