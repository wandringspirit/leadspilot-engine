"""Web search, used to find a website a business does not list on Google Maps.

Plain HTTP requests to DuckDuckGo's HTML results page, with Bing as the
fallback - deliberately not Google, so these searches cannot get the IP that
scrapes Google Maps flagged. Searches run one at a time, ``WEB_SEARCH_DELAY_S``
apart. An engine that answers with a captcha / "unusual traffic" page is not
asked again during the job; when both refuse, or ``MAX_FAILURES`` searches in a
row reach neither, searching stops for the job (``disabled`` says why).

The result-page parsers are pure functions (tests/test_websearch.py).
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import Settings, settings as default_settings
from app.scraper.parsing import unwrap_redirect

log = logging.getLogger(__name__)

ENGINES = ("duckduckgo", "bing")
MAX_FAILURES = 3          # searches in a row that reach no engine before searching stops
HEADERS = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
BLOCKED_TEXTS = ("anomaly", "unusual traffic", "captcha", "are you a robot", "bots use duckduckgo")


class SearchRefused(Exception):
    """The engine answered with a captcha / rate-limit page instead of results."""


def _result_urls(anchors, base: str, own_host: str) -> list[str]:
    urls: list[str] = []
    for a in anchors:
        url = unwrap_redirect(urljoin(base, a.get("href", "")))
        if url.startswith(("http://", "https://")) and own_host not in urlsplit(url).netloc and url not in urls:
            urls.append(url)
    return urls


def parse_duckduckgo(html: str) -> list[str]:
    """Result URLs of an html.duckduckgo.com page, in order, ads left out."""
    soup = BeautifulSoup(html or "", "lxml")
    anchors = [r.select_one("a.result__a") for r in soup.select("div.result")
               if "result--ad" not in (r.get("class") or [])]
    return _result_urls([a for a in anchors if a is not None], "https://duckduckgo.com/", "duckduckgo.com")


def parse_bing(html: str) -> list[str]:
    """Result URLs of a www.bing.com/search page, in order."""
    soup = BeautifulSoup(html or "", "lxml")
    return _result_urls(soup.select("li.b_algo h2 a[href]"), "https://www.bing.com/", "bing.com")


class WebSearch:
    def __init__(self, cfg: Settings | None = None, *, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg or default_settings
        self._client = client
        self._own_client = client is None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._refused: set[str] = set()
        self._failures = 0
        self.disabled = ""       # why searching stopped for this job
        self.searches = 0

    async def aclose(self) -> None:
        if self._client is not None and self._own_client:
            await self._client.aclose()
            self._client = None

    def _engines(self) -> list[str]:
        first = self.cfg.WEB_SEARCH_ENGINE.lower() if self.cfg.WEB_SEARCH_ENGINE.lower() in ENGINES else ENGINES[0]
        return [first] + [e for e in ENGINES if e != first]

    async def search(self, query: str) -> list[str]:
        """Result URLs for ``query`` ([] when nothing was found or searching is off)."""
        if self.disabled:
            return []
        async with self._lock:
            wait = self._last + self.cfg.WEB_SEARCH_DELAY_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                for engine in self._engines():
                    if engine in self._refused:
                        continue
                    try:
                        urls = await self._search(engine, query)
                    except SearchRefused as exc:
                        log.warning("%s refused a web search (%s) - not asking it again in this job", engine, exc)
                        self._refused.add(engine)
                        continue
                    except httpx.HTTPError as exc:
                        log.warning("%s web search failed: %s", engine, exc)
                        continue
                    self.searches += 1
                    self._failures = 0
                    log.info("Web search %r (%s): %d result(s)", query, engine, len(urls))
                    return urls
                self._failures += 1
                if len(self._refused) == len(ENGINES):
                    self.disabled = "the search engines refused more automated searches"
                elif self._failures >= MAX_FAILURES:
                    self.disabled = "the search engines could not be reached"
                if self.disabled:
                    log.warning("Web search switched off for this job: %s", self.disabled)
                return []
            finally:
                self._last = time.monotonic()

    async def _search(self, engine: str, query: str) -> list[str]:
        if self._client is None:
            self._client = httpx.AsyncClient(headers={**HEADERS, "User-Agent": self.cfg.USER_AGENT},
                                             timeout=httpx.Timeout(self.cfg.ENRICH_TIMEOUT_S), follow_redirects=True)
        if engine == "duckduckgo":
            resp = await self._client.post("https://html.duckduckgo.com/html/", data={"q": query})
            urls = parse_duckduckgo(resp.text)
        else:
            resp = await self._client.get("https://www.bing.com/search",
                                          params={"q": query, "setlang": "en", "count": "10"})
            urls = parse_bing(resp.text)
        if resp.status_code in (202, 403, 429) or (not urls and any(t in resp.text.lower() for t in BLOCKED_TEXTS)):
            raise SearchRefused(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return urls
