"""Find the website a business has but does not list on Google Maps.

Google Maps shows the site the owner entered in their Business Profile - often
nothing, or a Facebook / booking / BBB page - even when the business has a
website of its own. A place that passes a "no website" search is checked with:

1. **email domain** - an address on a domain of its own (not gmail & co., not a
   platform address) whose website is up and shows the business's phone number,
   or is named after the business;
2. **profile page** - a link on its Facebook / BBB / booking page to a site that
   shows its phone number;
3. **web search** (optional, ``SearchFilters.search_web``) - its name and city,
   then its phone number; a result that is not a platform or directory and whose
   site shows the phone number.

A site *shows the phone number* when its home page, or one of its first
``CONTACT_PAGES`` contact pages, lists that number (E.164 match) among at most
``MAX_PHONES`` different numbers - a page listing many businesses is a
directory. The home page is checked rather than the page a link points to, so a
directory's page about the business does not count. A business without a phone
on Maps is matched by name instead: the site's domain has to contain its name
(and, for a web search result, the site has to mention its city).

Pages are fetched with plain HTTP (no browser); a site that only renders with
JavaScript is not recognised. Pure helpers are unit tested in
tests/test_sitefinder.py.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Protocol, Sequence
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.enrichment.extractors import candidate_pages, extract_from_html, set_default_region
from app.scraper.parsing import unwrap_redirect, website_domain, website_type

if TYPE_CHECKING:
    from app.enrichment.website import ContactResult
    from app.models import PlaceRow

MAX_LINKS = 5          # profile-page links / search results checked per business (per search)
MAX_PHONES = 5         # a page with more different phone numbers than this is a directory
CONTACT_PAGES = 2      # contact pages read when the home page does not show the phone number
MIN_TEXT = 200         # characters of text a site needs to count as a website when matched by name
TEXT_CHARS = 20_000    # text kept per page for the name / city / parked-domain checks
CACHE_PAGES = 500      # pages remembered per job (sites that come up for many businesses)

FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me", "pm.me",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "zohomail.com", "yandex.com", "tutanota.com", "fastmail.com",
    # internet providers' mailboxes
    "att.net", "sbcglobal.net", "bellsouth.net", "comcast.net", "xfinity.com", "verizon.net", "cox.net",
    "charter.net", "spectrum.net", "earthlink.net", "juno.com", "netzero.net", "optonline.net", "frontier.com",
    "frontiernet.net", "windstream.net", "centurylink.net", "q.com", "roadrunner.com", "rr.com", "twc.com",
    "embarqmail.com", "suddenlink.net", "btinternet.com", "sky.com", "virginmedia.com", "talktalk.net",
    "ntlworld.com", "bigpond.com", "optusnet.com.au", "shaw.ca", "rogers.com", "sympatico.ca", "videotron.ca",
    "telus.net", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr", "laposte.net", "web.de", "t-online.de",
    "freenet.de", "libero.it", "virgilio.it", "tiscali.it", "rediffmail.com", "qq.com", "163.com", "126.com",
    "naver.com", "hanmail.net", "daum.net", "mail.ru", "inbox.ru", "list.ru", "bk.ru", "uol.com.br",
    "bol.com.br", "terra.com.br", "prodigy.net.mx",
})
_FREE_MAIL_PREFIXES = ("yahoo.", "hotmail.", "outlook.", "live.", "gmx.", "aol.", "icloud.", "yandex.")

# Sites that list businesses, or link from their pages, but are nobody's own website
# (profile platforms - Facebook, Yelp, BBB, booking pages ... - are in PROFILE_DOMAINS).
THIRD_PARTY_DOMAINS = frozenset({
    # directories & lead sites
    "mapquest.com", "bizapedia.com", "opencorporates.com", "chamberofcommerce.com", "superpages.com",
    "whitepages.com", "yellowbook.com", "yell.com", "cylex.us.com", "cylex-usa.com", "hotfrog.com",
    "brownbook.net", "merchantcircle.com", "citysearch.com", "local.com", "birdeye.com", "trustpilot.com",
    "glassdoor.com", "indeed.com", "zoominfo.com", "dnb.com", "buzzfile.com", "bloomberg.com", "alignable.com",
    "expertise.com", "homeguide.com", "buildzoom.com", "networx.com", "fixr.com", "craigslist.org", "zillow.com",
    "loc8nearme.com", "find-open.com", "allbiz.com", "cybo.com", "showmelocal.com", "n49.com", "ezlocal.com",
    "411.com", "411.ca", "canada411.ca", "yellowpages.ca", "yellowpages.com.au", "truelocal.com.au",
    "hipages.com.au", "checkatrade.com", "trustatrade.com", "ratedpeople.com", "mybuilder.com", "groupon.com",
    "foursquare.com", "bbbemail.org", "bbbprograms.org", "give.org",
    # search engines, big platforms, app stores, CDNs
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "apple.com", "waze.com", "wikipedia.org",
    "reddit.com", "quora.com", "medium.com", "amazon.com", "ebay.com", "microsoft.com", "meta.com", "meta.ai",
    "messenger.com", "oculus.com", "workplace.com", "fbsbx.com", "gstatic.com", "googleapis.com", "fbcdn.net",
    "cloudflare.com", "w3.org", "schema.org", "wix.com", "squarespace.com", "godaddy.com", "wordpress.org",
    "gravatar.com",
})
_GENERIC_NAME_WORDS = {"llc", "inc", "ltd", "co", "corp", "corporation", "company", "the", "and", "of",
                       "pllc", "llp", "lp", "plc", "gmbh", "pvt"}
PARKED_TEXTS = ("domain is for sale", "buy this domain", "this domain may be for sale", "domain parking",
                "parked free", "is parked", "hugedomains", "afternic")


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def is_free_mail(domain: str) -> bool:
    domain = domain.lower()
    return domain in FREE_MAIL_DOMAINS or domain.endswith(".rr.com") or domain.startswith(_FREE_MAIL_PREFIXES)


def is_third_party(url: str) -> bool:
    """Profile platforms, directories, search engines, government sites - never a business's own site."""
    host = website_domain(url)
    if not host or website_type(url) == "profile":
        return True
    if host.endswith((".gov", ".edu", ".mil")) or ".gov." in host:
        return True
    return any(host == d or host.endswith("." + d) for d in THIRD_PARTY_DOMAINS)


def own_email_domains(emails: Iterable[str]) -> list[str]:
    """Domains of addresses that could be the business's own (not free mail, not a platform)."""
    out: list[str] = []
    for email in emails:
        domain = (email or "").rpartition("@")[2].strip().lower()
        if "." in domain and not is_free_mail(domain) and not is_third_party(f"https://{domain}/") \
                and domain not in out:
            out.append(domain)
    return out


def name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) >= 3 and t not in _GENERIC_NAME_WORDS]


def domain_matches_name(domain: str, name: str) -> bool:
    """'american-ridge.com' vs 'American Ridge Roofing LLC' -> True (two words of the name in the domain)."""
    label = re.sub(r"[^a-z0-9]", "", (domain or "").lower().rsplit(".", 1)[0])
    tokens = name_tokens(name)
    hits = [t for t in tokens if t in label]
    return len(hits) >= 2 or (len(hits) == 1 and len(tokens) == 1)


def search_queries(row: PlaceRow) -> list[str]:
    """Name + city first; the phone number (exact phrase) when that finds nothing."""
    where = " ".join(p for p in (row.city, row.state) if p)
    queries = [f"{row.name} {where}".strip()]
    if row.phone:
        queries.append(f'"{row.phone}"')
    return queries


def candidate_sites(urls: Iterable[str]) -> list[str]:
    """Links worth checking: redirects unwrapped, third-party sites dropped, one per domain, MAX_LINKS at most."""
    out: list[str] = []
    domains: set[str] = set()
    for url in urls:
        url = unwrap_redirect(url)
        domain = website_domain(url)
        if url.startswith(("http://", "https://")) and domain not in domains and not is_third_party(url):
            domains.add(domain)
            out.append(url)
        if len(out) >= MAX_LINKS:
            break
    return out


def site_root(url: str) -> str:
    """'https://www.example.com/about?x=1' -> 'https://www.example.com/'."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/" if parts.scheme in ("http", "https") and parts.netloc else ""


def visible_text(html: str) -> str:
    """The page's text, lower case, whitespace collapsed (scripts and styles left out)."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip().lower()


@dataclass(frozen=True)
class Page:
    """What the checks need from one fetched page."""
    url: str                           # after redirects
    phones: frozenset[str]             # E.164
    contact_links: tuple[str, ...]
    text: str                          # visible_text(), first TEXT_CHARS characters

    def shows_phone(self, phone: str) -> bool:
        return bool(phone) and phone in self.phones and len(self.phones) <= MAX_PHONES

    def named_after(self, name: str, *domains: str) -> bool:
        """A real site (enough text, not a parked domain) whose domain contains the business's name."""
        return (len(self.text) >= MIN_TEXT and not any(p in self.text for p in PARKED_TEXTS)
                and any(domain_matches_name(d, name) for d in (website_domain(self.url), *domains) if d))


@dataclass(frozen=True)
class FoundSite:
    url: str
    how: str      # email domain | profile page | web search


class PageSource(Protocol):
    """What SiteFinder needs from WebsiteEnricher."""
    async def fetch_page(self, url: str) -> tuple[str, str]: ...
    async def find_contacts(self, website: str, *, region: str | None = None) -> ContactResult: ...


class Searcher(Protocol):
    async def search(self, query: str) -> list[str]: ...


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------
class SiteFinder:
    """Runs the checks for one job; pages and profile-page lookups are fetched once per job."""

    def __init__(self, web: PageSource, *, search: Searcher | None = None, default_region: str = "US") -> None:
        self.web, self.search, self.default_region = web, search, default_region
        self._contacts: dict[str, ContactResult] = {}
        self._pages: OrderedDict[tuple[str, str], Page | None] = OrderedDict()

    async def contacts(self, url: str, *, region: str | None = None) -> ContactResult:
        """``find_contacts`` once per URL - the email lookups and the profile-page check share it."""
        if url not in self._contacts:
            self._contacts[url] = await self.web.find_contacts(url, region=region)
        return self._contacts[url]

    def looked_up(self, url: str) -> ContactResult | None:
        """The lookup result for ``url`` if it was fetched during this job."""
        return self._contacts.get(url)

    async def find(self, rows: Sequence[PlaceRow]) -> FoundSite | None:
        """The business's own website, when one of the checks finds it (``rows`` = the place's rows)."""
        row = rows[0]
        region = row.country or self.default_region
        checked: set[str] = set()

        async def first(urls: list[str], how: str, *, by_name: bool = False, city: bool = False) -> FoundSite | None:
            fresh = []
            for url in urls:
                if website_domain(url) not in checked:
                    checked.add(website_domain(url))
                    fresh.append(url)
            sites = await asyncio.gather(*(self._site_of(u, row, region, by_name=by_name, city=city) for u in fresh))
            return next((FoundSite(site, how) for site in sites if site), None)

        domains = own_email_domains(r.email for r in rows)[:2]
        found = await first([f"https://{d}/" for d in domains], "email domain", by_name=True)
        if found is None and row.website_type == "profile":
            profile = await self.contacts(row.website, region=region)
            found = await first(candidate_sites(profile.external_links), "profile page")
        if found is None and self.search is not None:
            for query in search_queries(row):
                found = await first(candidate_sites(await self.search.search(query)), "web search", city=True)
                if found is not None:
                    break
        return found

    async def _site_of(self, url: str, row: PlaceRow, region: str, *, by_name: bool, city: bool) -> str:
        """The site's home page URL when the site at ``url`` belongs to the business, else ''."""
        home = await self._page(site_root(url), region)
        if home is None or is_third_party(home.url):
            return ""
        phone = row.phone_unformatted
        if phone:
            if home.shows_phone(phone):
                return home.url
            for link in candidate_pages(home.url, list(home.contact_links), CONTACT_PAGES):
                page = await self._page(link, region)
                if page is not None and website_domain(page.url) == website_domain(home.url) \
                        and page.shows_phone(phone):
                    return home.url
        if by_name or not phone:
            in_city = not city or not row.city or row.city.lower() in home.text
            if in_city and home.named_after(row.name, website_domain(url)):
                return home.url
        return ""

    async def _page(self, url: str, region: str) -> Page | None:
        """Fetch and read a page (None when it cannot be loaded); remembered for the rest of the job."""
        if not url:
            return None
        key = (region, url)
        if key in self._pages:
            self._pages.move_to_end(key)
            return self._pages[key]
        final_url, html = await self.web.fetch_page(url)
        page = None
        if html:
            set_default_region(region)
            found = extract_from_html(html, final_url, final_url)
            page = Page(url=final_url, phones=frozenset(found.phones), contact_links=tuple(found.contact_links),
                        text=visible_text(html)[:TEXT_CHARS])
        self._pages[key] = page
        while len(self._pages) > CACHE_PAGES:
            self._pages.popitem(last=False)
        return page
