import pytest

from app.enrichment.sitefinder import (
    MAX_LINKS,
    FoundSite,
    SiteFinder,
    candidate_sites,
    domain_matches_name,
    is_free_mail,
    is_third_party,
    own_email_domains,
    search_queries,
    site_root,
)
from app.enrichment.website import ContactResult
from app.models import PlaceRow

PHONE, E164 = "(281) 932-4462", "+12819324462"
SITE = "https://roofing4texas.com/"
NAME_QUERY = "Roofing Solutions Enterprise LLC Houston Texas"
PHONE_QUERY = f'"{PHONE}"'
FILLER = "Residential and commercial roof repair, replacement and inspections. " * 5


def page(*phones: str, text: str = FILLER, extra: str = "") -> str:
    lines = "".join(f"<p>Call us: {p}</p>" for p in phones)
    return f"<html><body><h1>Welcome</h1><p>{text}</p>{lines}{extra}</body></html>"


def place(**changes) -> list[PlaceRow]:
    data = dict(rank=1, name="Roofing Solutions Enterprise LLC", city="Houston", state="Texas", country="US",
                phone=PHONE, phone_unformatted=E164, website="https://www.glossgenius.com/roofing-solutions")
    data.update(changes)
    return [PlaceRow(**data)]


class FakeWeb:
    """fetch_page / find_contacts over a dict of pages: url -> html or (final url, html)."""

    def __init__(self, pages: dict | None = None, profile_links: dict | None = None) -> None:
        self.pages, self.profile_links = pages or {}, profile_links or {}
        self.fetched: list[str] = []
        self.looked_up: list[str] = []

    async def __aenter__(self) -> "FakeWeb":     # used in place of WebsiteEnricher by job tests
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def fetch_page(self, url: str) -> tuple[str, str]:
        self.fetched.append(url)
        value = self.pages.get(url, "")
        return value if isinstance(value, tuple) else (url, value)

    async def find_contacts(self, website: str, *, region: str | None = None) -> ContactResult:
        self.looked_up.append(website)
        return ContactResult(website=website, external_links=list(self.profile_links.get(website, [])))


class FakeSearch:
    def __init__(self, results: dict[str, list[str]] | None = None) -> None:
        self.results, self.queries = results or {}, []

    async def search(self, query: str) -> list[str]:
        self.queries.append(query)
        return self.results.get(query, [])


# ---------------------------------------------------------------- helpers
def test_free_mail_and_own_email_domains():
    assert is_free_mail("gmail.com") and is_free_mail("yahoo.co.uk") and is_free_mail("houston.rr.com")
    assert not is_free_mail("roofing4texas.com")
    emails = ["a@gmail.com", "info@roofing4texas.com", "office@Roofing4Texas.com", "x@bbbemail.org",
              "page@facebook.com", "b@sbcglobal.net", "not-an-email"]
    assert own_email_domains(emails) == ["roofing4texas.com"]


@pytest.mark.parametrize("url, expected", [
    ("https://www.facebook.com/RoofingSolutions", True),
    ("https://www.yelp.com/biz/roofing-solutions-houston", True),
    ("https://www.mapquest.com/us/texas/roofing-solutions", True),
    ("https://www.hcad.org.gov.example/", True),
    ("https://www.houstontx.gov/permits", True),
    ("https://play.google.com/store", True),
    (SITE, False),
    ("https://roofingsolutions.wixsite.com/home", False),
])
def test_is_third_party(url, expected):
    assert is_third_party(url) is expected


@pytest.mark.parametrize("domain, name, expected", [
    ("american-ridge.com", "American Ridge Roofing LLC", True),
    ("americanridgeroofing.net", "American Ridge Roofing LLC", True),
    ("joesplumbing.com", "Joe's Plumbing", True),
    ("acme.com", "Acme", True),
    ("roofing4texas.com", "Roofing Solutions Enterprise LLC", False),   # one word of three
    ("acmeplumbing.com", "Best Plumbing Co", False),
    ("", "Acme", False),
])
def test_domain_matches_name(domain, name, expected):
    assert domain_matches_name(domain, name) is expected


def test_search_queries_use_name_and_city_then_the_phone():
    assert search_queries(place()[0]) == [NAME_QUERY, PHONE_QUERY]
    assert search_queries(place(phone="", city="", state="")[0]) == ["Roofing Solutions Enterprise LLC"]


def test_candidate_sites_unwraps_drops_third_parties_and_keeps_one_link_per_site():
    links = ["https://l.facebook.com/l.php?u=https%3A%2F%2Froofing4texas.com%2Fabout&h=AT",
             "https://roofing4texas.com/contact", "https://www.yelp.com/biz/x", "https://www.instagram.com/x/",
             "mailto:info@roofing4texas.com", "https://www.bbb.org/us/tx/houston/profile/x",
             *[f"https://site{i}.com/" for i in range(10)]]
    out = candidate_sites(links)
    assert out[0] == "https://roofing4texas.com/about"
    assert len(out) == MAX_LINKS and out[1:] == [f"https://site{i}.com/" for i in range(MAX_LINKS - 1)]


def test_site_root():
    assert site_root("https://www.example.com/about?x=1#top") == "https://www.example.com/"
    assert site_root("http://example.com") == "http://example.com/"
    assert site_root("mailto:a@b.com") == ""


# ---------------------------------------------------------------- email domain
async def test_email_domain_site_showing_the_phone_is_theirs():
    web = FakeWeb({SITE: page(PHONE)})
    found = await SiteFinder(web).find(place(email="info@roofing4texas.com"))
    assert found == FoundSite(SITE, "email domain")


async def test_email_domain_site_named_after_the_business_is_theirs_without_the_phone():
    web = FakeWeb({"https://americanridge.com/": page("(713) 555-0199")})
    found = await SiteFinder(web).find(place(name="American Ridge Roofing", email="office@americanridge.com",
                                             website=""))
    assert found == FoundSite("https://americanridge.com/", "email domain")


async def test_email_domain_follows_redirects_but_not_to_a_social_page():
    web = FakeWeb({SITE: ("https://www.facebook.com/RoofingSolutions", page(PHONE))})
    assert await SiteFinder(web).find(place(email="info@roofing4texas.com", website="")) is None


@pytest.mark.parametrize("html", [
    page(text="This domain is for sale! " + FILLER),          # parked
    page(text="Coming soon"),                                    # too little text to be a website
    "",                                                          # does not load
])
async def test_email_domain_without_a_real_site_is_not_a_website(html):
    web = FakeWeb({"https://americanridge.com/": html})
    rows = place(name="American Ridge Roofing", email="office@americanridge.com", website="")
    assert await SiteFinder(web).find(rows) is None


async def test_free_mail_and_platform_addresses_are_not_checked():
    web = FakeWeb()
    rows = place(email="roofing4texas@gmail.com", website="") + place(email="x@bbbemail.org", website="")
    assert await SiteFinder(web).find(rows) is None
    assert web.fetched == [] and web.looked_up == []


# ---------------------------------------------------------------- profile page
async def test_link_on_the_profile_page_to_a_site_showing_the_phone():
    profile = "https://www.glossgenius.com/roofing-solutions"
    web = FakeWeb({SITE: page(PHONE)},
                  profile_links={profile: ["https://www.yelp.com/biz/x", "https://roofing4texas.com/services"]})
    finder = SiteFinder(web)
    assert await finder.find(place()) == FoundSite(SITE, "profile page")
    assert web.looked_up == [profile] and web.fetched == [SITE], "home page checked, third-party link skipped"
    assert finder.looked_up(profile) is not None and finder.looked_up("https://other/") is None


async def test_profile_page_is_looked_up_once_per_job():
    profile = "https://www.glossgenius.com/roofing-solutions"
    web = FakeWeb()
    finder = SiteFinder(web)
    await finder.contacts(profile, region="US")          # the email lookup while filtering ...
    assert await finder.find(place()) is None             # ... is reused by the profile-page check
    assert web.looked_up == [profile]


async def test_a_site_that_does_not_show_the_phone_is_not_theirs():
    profile = "https://www.glossgenius.com/roofing-solutions"
    web = FakeWeb({"https://www.gafroofing.com/": page("(800) 555-0100")},
                  profile_links={profile: ["https://www.gafroofing.com/shingles"]})
    assert await SiteFinder(web).find(place()) is None


async def test_the_phone_on_a_contact_page_counts():
    home = page(extra='<a href="/contact-us">Contact us</a>')
    web = FakeWeb({SITE: home, "https://roofing4texas.com/contact-us": page(PHONE)},
                  profile_links={"https://www.glossgenius.com/roofing-solutions": [SITE]})
    assert await SiteFinder(web).find(place()) == FoundSite(SITE, "profile page")


# ---------------------------------------------------------------- web search
async def test_web_search_finds_the_site_by_phone():
    search = FakeSearch({NAME_QUERY: ["https://www.yelp.com/biz/roofing-solutions", SITE]})
    web = FakeWeb({SITE: page(PHONE)})
    assert await SiteFinder(web, search=search).find(place(website="")) == FoundSite(SITE, "web search")
    assert search.queries == [NAME_QUERY]


async def test_web_search_tries_the_phone_number_when_the_name_finds_nothing():
    search = FakeSearch({PHONE_QUERY: [SITE]})
    web = FakeWeb({SITE: page(PHONE)})
    assert await SiteFinder(web, search=search).find(place(website="")) == FoundSite(SITE, "web search")
    assert search.queries == [NAME_QUERY, PHONE_QUERY]


async def test_a_directory_page_about_the_business_is_not_its_website():
    listing = "https://roofers-near-me.info/houston/roofing-solutions-enterprise"
    busy = page(*[f"(713) 555-01{i:02d}" for i in range(8)], PHONE)
    search = FakeSearch({NAME_QUERY: [listing, "https://busy-directory.com/"], PHONE_QUERY: [listing]})
    web = FakeWeb({listing: page(PHONE), "https://roofers-near-me.info/": page("(800) 555-0100"),
                   "https://busy-directory.com/": busy})
    assert await SiteFinder(web, search=search).find(place(website="")) is None
    assert listing not in web.fetched, "the site's home page is checked, not the listing page"
    assert search.queries == [NAME_QUERY, PHONE_QUERY]


async def test_no_web_search_unless_a_searcher_is_given():
    web = FakeWeb({SITE: page(PHONE)})
    assert await SiteFinder(web).find(place(website="")) is None
    assert web.fetched == []


async def test_with_a_phone_a_search_result_named_like_the_business_still_has_to_show_it():
    lookalike = "https://roofingsolutionsenterprise.com/"
    search = FakeSearch({NAME_QUERY: [lookalike]})
    web = FakeWeb({lookalike: page("(713) 868-2211", text="Roofing Solutions Enterprise, Houston. " + FILLER)})
    assert await SiteFinder(web, search=search).find(place(website="")) is None


async def test_without_a_phone_a_search_result_must_carry_the_name_and_mention_the_city():
    rows = place(name="American Ridge Roofing", phone="", phone_unformatted="", website="",
                 email="americanridge@gmail.com")
    other_city = "https://americanridgeroofing.net/"
    theirs = "https://americanridge.com/"
    search = FakeSearch({"American Ridge Roofing Houston Texas": [other_city, theirs]})
    web = FakeWeb({other_city: page(text="Serving Denver, Colorado. " + FILLER),
                   theirs: page(text="Serving Houston and Katy. " + FILLER)})
    assert await SiteFinder(web, search=search).find(rows) == FoundSite(theirs, "web search")


async def test_pages_are_fetched_once_per_job():
    directory = "https://roofers-near-me.info/"
    search = FakeSearch({NAME_QUERY: [directory + "a"], PHONE_QUERY: [directory + "b"],
                         "Other Roofing Houston Texas": [directory + "c"]})
    web = FakeWeb({directory: page("(800) 555-0100")})
    finder = SiteFinder(web, search=search)
    await finder.find(place(website=""))
    await finder.find(place(name="Other Roofing", phone="", phone_unformatted="", website=""))
    assert web.fetched.count(directory) == 1
