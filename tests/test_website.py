"""WebsiteEnricher's fetch strategy, with the network replaced by a dict of pages."""
from app.config import Settings
from app.enrichment.website import WebsiteEnricher

PROFILE = "https://www.facebook.com/joesplumbing"


class FakeWeb(WebsiteEnricher):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(Settings(ENRICH_BROWSER_FALLBACK=False))
        self.pages, self.fetched = pages, []

    async def _fetch(self, url: str) -> tuple[str, str]:
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError("HTTP 404")
        return self.pages[url], url


async def test_a_profile_page_is_the_only_page_read():
    web = FakeWeb({PROFILE: '<a href="/joesplumbing/about">About</a> <p>Email: joe@gmail.com</p>'})
    async with web:
        result = await web.find_contacts(PROFILE)
    assert web.fetched == [PROFILE], "never the platform's own /contact, /about ... pages"
    assert result.email_addresses == ["joe@gmail.com"]


async def test_an_own_website_is_crawled_for_contact_pages():
    site = "https://joesplumbing.com/"
    web = FakeWeb({site: '<a href="/contact-us">Contact us</a>',
                   "https://joesplumbing.com/contact-us": "<p>info@joesplumbing.com</p>"})
    async with web:
        result = await web.find_contacts(site)
    assert web.fetched[0] == site and "https://joesplumbing.com/contact-us" in web.fetched
    assert result.email_addresses == ["info@joesplumbing.com"]
