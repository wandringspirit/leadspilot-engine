from contextlib import AsyncExitStack

import app.jobs as jobs
from app.config import Settings
from app.enrichment.extractors import EmailHit
from app.enrichment.sitefinder import SiteFinder
from app.enrichment.websearch import WebSearch
from app.enrichment.website import ContactResult
from app.filters import UNLISTED_WEBSITE, SearchFilters
from app.jobs import (
    PlaceFilter,
    apply_contact_result,
    blocked_message,
    apply_listing_emails,
    group_by_place,
    plan_searches,
    result_notice,
    select_enrichment_targets,
)
from app.models import Job, PlaceRow, build_query, split_places
from app.scraper.maps import PlaceRef, Search
from tests.test_sitefinder import FakeSearch, FakeWeb, page

NO_SITE = SearchFilters(website="without", contact="phone_or_email")


def _row(rank: int, **kw) -> PlaceRow:
    base = dict(rank=rank, name=f"Place {rank}", website=f"https://p{rank}.com", phone="(817) 555-0100",
                phone_unformatted="+18175550100", country="US")
    base.update(kw)
    return PlaceRow(**base)


def _result(website: str, emails: list[str], phones: list[str] | None = None, error: str | None = None) -> ContactResult:
    return ContactResult(
        website=website,
        emails=[EmailHit(email=e, source=website + "contact", same_domain=True, score=100) for e in emails],
        phones=phones or [],
        pages_checked=[website],
        error=error,
    )


def test_group_by_place():
    rows = [_row(1), _row(2), _row(1)]
    groups = group_by_place(rows)
    assert sorted(groups) == [1, 2]
    assert len(groups[1]) == 2


def test_select_targets_only_missing_by_default():
    rows = [_row(1, email="a@p1.com"), _row(2), _row(3, phone="", phone_unformatted="", email="x@p3.com")]
    targets = select_enrichment_targets(rows, row_ids=None, force=False)
    assert sorted(targets) == [2, 3]


def test_select_targets_respects_row_ids_and_force():
    rows = [_row(1, email="a@p1.com"), _row(2)]
    assert select_enrichment_targets(rows, row_ids=[rows[0].row_id], force=False) == {}
    assert sorted(select_enrichment_targets(rows, row_ids=[rows[0].row_id], force=True)) == [1]


def test_apply_single_email_fills_row():
    rows = [_row(1), _row(2)]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com"]))
    assert added == []
    assert rows[0].email == "info@p1.com"
    assert rows[0].email_source == "https://p1.com/contact"
    assert rows[0].enrichment_status == "done"
    assert rows[1].email == ""


def test_apply_multiple_emails_duplicates_rows_in_place():
    rows = [_row(1), _row(2)]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com", "sales@p1.com", "hb@p1.com"]))
    assert len(added) == 2
    assert [r.email for r in rows] == ["info@p1.com", "sales@p1.com", "hb@p1.com", ""]
    assert [r.rank for r in rows] == [1, 1, 1, 2]
    assert len({r.row_id for r in rows}) == 4, "clones need fresh row ids"
    clone = rows[1]
    assert clone.name == rows[0].name and clone.website == rows[0].website and clone.phone == rows[0].phone
    assert all(r.enrichment_status == "done" for r in rows[:3])


def test_apply_skips_emails_already_present():
    rows = [_row(1, email="info@p1.com")]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com", "new@p1.com"]))
    assert len(added) == 1 and added[0].email == "new@p1.com"
    assert [r.email for r in rows] == ["info@p1.com", "new@p1.com"]


def test_apply_fills_missing_phone_from_website():
    rows = [_row(1, phone="", phone_unformatted="")]
    apply_contact_result(rows, 1, _result("https://p1.com/", [], phones=["+18175550123"]))
    assert rows[0].phone == "(817) 555-0123"
    assert rows[0].phone_unformatted == "+18175550123"
    assert rows[0].phone_source == "website"
    assert rows[0].enrichment_status == "done"


def test_apply_not_found_and_error_statuses():
    rows = [_row(1), _row(2)]
    apply_contact_result(rows, 1, _result("https://p1.com/", []))
    assert rows[0].enrichment_status == "not_found"
    apply_contact_result(rows, 2, _result("https://p2.com/", [], error="HTTP 403"))
    assert rows[1].enrichment_status == "error"
    assert "403" in rows[1].enrichment_note


def test_apply_unknown_rank_is_noop():
    rows = [_row(1)]
    assert apply_contact_result(rows, 99, _result("https://x.com/", ["a@x.com"])) == []
    assert rows[0].email == ""


# ----------------------------------------------------------------------
# emails written in the listing
# ----------------------------------------------------------------------
def test_apply_listing_emails_follows_the_one_row_per_email_rule():
    rows = [_row(1, website="", maps_url="https://www.google.com/maps/place/Joes",
                 description="Email info@joes.com or joe [at] gmail [dot] com for a quote")]
    added = apply_listing_emails(rows)
    assert [r.email for r in rows] == ["info@joes.com", "joe@gmail.com"]
    assert len(added) == 1 and added[0].rank == 1 and added[0].row_id != rows[0].row_id
    assert {r.email_source for r in rows} == {"https://www.google.com/maps/place/Joes"}
    assert apply_listing_emails([_row(2)]) == [] and apply_listing_emails([]) == []


# ----------------------------------------------------------------------
# PlaceFilter - the scraper's selector
# ----------------------------------------------------------------------
def test_place_filter_prefilter_uses_the_feed_hint():
    f = PlaceFilter(NO_SITE)
    assert f.prefilter(PlaceRef(rank=1, href="", website_hint="https://acme.com")) == "has a website"
    assert f.prefilter(PlaceRef(rank=1, href="", website_hint="https://www.facebook.com/acme")) == ""
    assert f.prefilter(PlaceRef(rank=1, href="", website_hint="")) == ""
    assert f.prefilter(PlaceRef(rank=1, href="", website_hint=None)) == ""


async def test_place_filter_keeps_places_without_a_website_that_have_a_phone():
    f = PlaceFilter(NO_SITE)
    rows, reason = await f.accept(_row(1, website=""))
    assert reason == "" and [r.rank for r in rows] == [1]
    assert await f.accept(_row(2)) == ([], "has a website")
    assert await f.accept(_row(3, website="", phone="", phone_unformatted="")) == ([], "no phone or email")


async def test_place_filter_looks_for_an_email_only_where_it_decides():
    calls = []

    async def lookup(url, *, region=None):
        calls.append((url, region))
        return _result(url, ["hello@joes.com"])

    f = PlaceFilter(NO_SITE, lookup=lookup)
    assert f.wants_lookups
    rows, _ = await f.accept(_row(1, website="https://www.facebook.com/joes"))   # has a phone: no lookup
    assert rows and calls == []
    rows, reason = await f.accept(_row(2, website="https://www.facebook.com/joes2", phone="",
                                       phone_unformatted="", country="GB"))
    assert reason == "" and rows[0].email == "hello@joes.com" and rows[0].enrichment_status == "done"
    assert calls == [("https://www.facebook.com/joes2", "GB")]


async def test_place_filter_rejects_when_the_lookup_finds_nothing_or_fails():
    async def nothing(url, *, region=None):
        return _result(url, [])

    async def broken(url, *, region=None):
        raise RuntimeError("boom")

    def silent():
        return _row(3, website="https://www.facebook.com/x", phone="", phone_unformatted="")

    assert await PlaceFilter(NO_SITE, lookup=nothing).accept(silent()) == ([], "no phone or email")
    assert await PlaceFilter(NO_SITE, lookup=broken).accept(silent()) == ([], "no phone or email")
    assert not PlaceFilter(SearchFilters(website="without", contact="phone")).wants_lookups


async def test_default_place_filter_keeps_everything_and_adds_listing_emails():
    f = PlaceFilter()
    assert not f.active and not f.wants_lookups
    rows, reason = await f.accept(_row(1, description="Write to office@p1.com"))
    assert reason == "" and rows[0].email == "office@p1.com"


# ---- websites the listing does not show ------------------------------------
async def test_place_filter_skips_a_place_whose_email_domain_is_its_website():
    web = FakeWeb({"https://joesplumbing.com/": page("(817) 555-0100")})
    f = PlaceFilter(NO_SITE, site_finder=SiteFinder(web))
    assert await f.accept(_row(1, website="", description="Email info@joesplumbing.com")) == ([], UNLISTED_WEBSITE)
    rows, reason = await f.accept(_row(2, website="", description="Email joe@gmail.com"))
    assert reason == "" and rows[0].email == "joe@gmail.com"
    assert f.stats() == {"unlisted_websites": {"email domain": 1}, "web_searches": 0}


async def test_place_filter_checks_only_places_that_match_without_a_website():
    web = FakeWeb()                                   # joes.com does not load
    f = PlaceFilter(NO_SITE, site_finder=SiteFinder(web))
    rows, reason = await f.accept(_row(1, website="", phone="", phone_unformatted="", description="joe@joes.com"))
    assert reason == "" and rows[0].email == "joe@joes.com"
    assert await f.accept(_row(2)) == ([], "has a website")
    assert await f.accept(_row(3, website="", phone="", phone_unformatted="")) == ([], "no phone or email")
    assert web.fetched == ["https://joes.com/"], "places the filters reject are not checked"
    other = PlaceFilter(SearchFilters(website="any", contact="phone"), site_finder=SiteFinder(web))
    assert not other.checks_sites and (await other.accept(_row(4, website="", description="a@joes4.com")))[1] == ""
    assert web.fetched == ["https://joes.com/"]


async def test_place_filter_keeps_what_the_social_page_shows():
    profile = "https://www.facebook.com/joes"
    web = FakeWeb()

    async def contacts(url, *, region=None):
        web.looked_up.append(url)
        return _result(url, ["hello@joes.com"])

    web.find_contacts = contacts
    finder = SiteFinder(web)
    f = PlaceFilter(NO_SITE, lookup=finder.contacts, site_finder=finder)
    rows, reason = await f.accept(_row(1, website=profile))           # has a phone: no email lookup ...
    assert reason == "" and rows[0].email == "hello@joes.com" and rows[0].enrichment_status == "done"
    rows, reason = await f.accept(_row(2, website=profile + "2", phone="", phone_unformatted=""))
    assert reason == "" and [r.email for r in rows] == ["hello@joes.com"]    # ... one lookup each, applied once
    assert web.looked_up == [profile, profile + "2"]


async def test_place_filter_keeps_a_place_when_the_website_check_breaks():
    class Broken(SiteFinder):
        async def find(self, rows):
            raise RuntimeError("boom")

    f = PlaceFilter(NO_SITE, site_finder=Broken(FakeWeb()))
    rows, reason = await f.accept(_row(1, website=""))
    assert reason == "" and len(rows) == 1


async def test_place_filter_open_sets_up_what_the_filters_need(monkeypatch):
    monkeypatch.setattr(jobs, "WebsiteEnricher", lambda cfg, **kw: FakeWeb())
    cfg = Settings()
    plain = PlaceFilter(SearchFilters(website="with", contact="phone"))
    async with AsyncExitStack() as stack:
        await plain.open(stack, cfg)
    assert plain.site_finder is None and plain.lookup is None

    checks = PlaceFilter(SearchFilters(website="without", contact="phone"))
    searches = PlaceFilter(SearchFilters(website="without", contact="phone", search_web=True))
    ignored = PlaceFilter(SearchFilters(website="with", contact="email", search_web=True))
    async with AsyncExitStack() as stack:
        for f in (checks, searches, ignored):
            await f.open(stack, cfg)
    assert checks.site_finder is not None and checks.site_finder.search is None
    assert isinstance(searches.site_finder.search, WebSearch)
    assert ignored.lookup is not None and ignored.site_finder.search is None and not ignored.checks_sites


async def test_place_filter_says_when_web_search_was_switched_off():
    search = FakeSearch()
    search.searches, search.disabled = 12, "the search engines refused more automated searches"
    f = PlaceFilter(SearchFilters(website="without", search_web=True), site_finder=SiteFinder(FakeWeb(), search=search))
    assert f.notice() == ("Web search stopped after 12 searches (the search engines refused more automated "
                          "searches); the businesses after that were only checked by their email domain and "
                          "social page.")
    assert f.stats()["web_searches"] == 12
    search.searches, search.disabled = 0, "the search engines could not be reached"
    assert f.notice() == ("Web search did not work (the search engines could not be reached), so businesses "
                          "were only checked by their email domain and social page.")
    assert PlaceFilter(NO_SITE).notice() == "" and PlaceFilter(NO_SITE).stats()["web_searches"] == 0


# ----------------------------------------------------------------------
# result_notice - "only 35 found"
# ----------------------------------------------------------------------
def _job(limit=200, filters=None, **stats) -> Job:
    return Job(query=build_query("plumbers", "Austin, TX"), business="plumbers", place="Austin, TX",
               limit=limit, filters=filters or SearchFilters(), stats=stats)


def test_build_query():
    assert build_query(" plumbers ", " Austin, TX ") == "plumbers in Austin, TX"
    assert build_query("roofers near houston", "") == "roofers near houston"


def test_no_notice_when_the_limit_was_reached_or_the_run_did_not_finish():
    assert result_notice(_job(limit=20, matched=20, links_found=20, stopped="limit")) == ""
    assert result_notice(_job(matched=5, links_found=5, stopped="")) == ""


def test_notice_for_a_complete_list():
    assert result_notice(_job(matched=35, links_found=35, stopped="end_of_list")) == (
        "Only 35 businesses found for “plumbers” in Austin, TX — that's every listing Google Maps has "
        "for this search (you asked for 200).")


def test_notice_for_a_complete_list_with_filters():
    assert result_notice(_job(filters=NO_SITE, matched=35, links_found=87, stopped="end_of_list")) == (
        "Only 35 of the 87 businesses Google Maps lists for “plumbers” in Austin, TX match your filters "
        "(no website · phone or email) — that's all of them (you asked for 200).")
    one = result_notice(_job(filters=NO_SITE, matched=1, links_found=87, stopped="end_of_list"))
    assert one.startswith("Only 1 of the 87 businesses") and " matches your filters " in one
    assert result_notice(_job(filters=NO_SITE, matched=0, links_found=87, stopped="end_of_list")) == (
        "None of the 87 businesses Google Maps lists for “plumbers” in Austin, TX match your filters "
        "(no website · phone or email).")


def test_notice_when_google_cut_the_list_short():
    text = result_notice(_job(filters=NO_SITE, matched=35, links_found=120, stopped="end_of_list"))
    assert text == ("Google Maps stopped listing “plumbers” in Austin, TX after 120 results — it shows at most "
                    "about 120 per search, so there may be more. 35 of them match your filters (no website · "
                    "phone or email). Search the whole area (section by section) to find more.")


def test_notice_for_other_stops():
    assert result_notice(_job(matched=0, links_found=0, stopped="no_results")) == (
        "Google Maps has no results for “plumbers” in Austin, TX.")
    assert "stopped loading after 60 listings" in result_notice(_job(matched=60, links_found=60, stopped="stalled"))
    assert "MAX_SCANNED" in result_notice(_job(filters=NO_SITE, matched=3, links_found=1000, stopped="scan_limit"))
    free_text = Job(query="roofers near houston", limit=50, stats={"matched": 1, "links_found": 1, "stopped": "single"})
    assert result_notice(free_text) == "Google Maps shows a single place for “roofers near houston”."


def test_blocked_message_says_what_happened_what_was_kept_and_what_to_do():
    job = _job(limit=50, matched=12, links_found=240, stopped="blocked", blocked_ip="203.0.113.7")
    assert blocked_message(job) == (
        "Google Maps blocked the search: it showed its “unusual traffic” check (a captcha) instead of results, "
        "so it is treating this IP address (203.0.113.7) as a robot. The 12 businesses found before that are "
        "kept. Wait at least 30 minutes before searching again – searching straight away keeps the block going "
        "– then search a smaller area or fewer businesses, lower DETAIL_CONCURRENCY, or set PROXY_SERVER to "
        "search from another IP address.")
    assert "treating this computer's IP address as a robot. Nothing was found before that." in \
        blocked_message(_job(matched=0, stopped="blocked"))
    assert "The 1 business found before that is kept." in blocked_message(_job(matched=1, stopped="blocked"))
    assert result_notice(job) == "", "the block is the job's error, not a short-result notice"


# ----------------------------------------------------------------------
# several places / whole area
# ----------------------------------------------------------------------
def test_split_places():
    assert split_places("Katy, TX; Pasadena,  TX ;\nKaty, TX;;") == ["Katy, TX", "Pasadena, TX"]
    assert split_places("") == [] and split_places("Austin, TX") == ["Austin, TX"]


def test_plan_searches():
    def job(**kw):
        return Job(query="q", limit=5, **kw)
    assert plan_searches(job(business="roofing", place="Houston")) == [Search("roofing in Houston")]
    assert plan_searches(job(business="roofing", place="Katy; Pasadena")) == [
        Search("roofing in Katy"), Search("roofing in Pasadena")]
    assert plan_searches(job(business="roofing", place="Katy; Pasadena", whole_area=True)) == [
        Search("roofing", area="Katy"), Search("roofing", area="Pasadena")]
    assert plan_searches(job(business="roofing")) == [Search("roofing")]
    assert plan_searches(Job(query="roofers near houston", limit=5)) == [Search("roofers near houston")]


def _area_job(filters=None, whole_area=True, place="Houston, TX", **stats) -> Job:
    return Job(query=build_query("roofing", place), business="roofing", place=place, whole_area=whole_area,
               limit=50, filters=filters or SearchFilters(), stats=stats)


def test_notice_after_searching_the_whole_area():
    assert result_notice(_area_job(NO_SITE, matched=35, links_found=812, sections=37, stopped="searched_all")) == (
        "Searched all of Houston, TX for “roofing” (37 map sections): 812 businesses listed, 35 of them match "
        "your filters (no website · phone or email) — that's all of them (you asked for 50).")
    assert result_notice(_area_job(matched=41, links_found=41, sections=5, stopped="searched_all")) == (
        "Only 41 businesses found in all of Houston, TX for “roofing” (5 map sections) — that's every listing "
        "Google Maps has there (you asked for 50).")
    assert result_notice(_area_job(NO_SITE, matched=0, links_found=0, sections=1, stopped="searched_all")) == (
        "Google Maps has no results for “roofing” in Houston, TX.")
    dense = result_notice(_area_job(NO_SITE, matched=0, links_found=900, sections=80, dense=2,
                                    stopped="searched_all"))
    assert "None of them match your filters" in dense and dense.endswith("A few very busy spots may hold more.")
    assert result_notice(_area_job(NO_SITE, matched=12, links_found=2000, sections=100,
                                   stopped="section_limit")) == (
        "Stopped after 100 map sections of Houston, TX (MAX_SECTIONS setting) with 12 found for “roofing” "
        "(you asked for 50). Raise “Most map sections per search” in Settings or search a smaller area to "
        "cover the rest.")


def test_notice_after_several_places():
    text = result_notice(_area_job(NO_SITE, whole_area=False, place="Katy, TX; Pasadena, TX", matched=4,
                                   links_found=230, searches=2, full_lists=2, stopped="searched_all"))
    assert text.startswith("Searched Katy, TX; Pasadena, TX for “roofing” (2 searches): 230 businesses listed, "
                           "4 of them match")
    assert text.endswith("Some searches hit Google's limit of about 120 results — search the whole area "
                         "(section by section) to find more.")
