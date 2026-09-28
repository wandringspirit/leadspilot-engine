"""cli.py end to end, with Google Maps replaced by the in-memory feed of tests/test_scan.py."""
import asyncio
import csv
import io

import app.jobs as jobs
import cli
from app.scraper.maps import GoogleBlocked
from tests.test_scan import FeedScraper, listing
from tests.test_sitefinder import FakeWeb, page

LISTINGS = [
    listing("https://www.site1.com/"),                 # own website          -> skipped
    listing(""),                                       # no website, phone    -> kept
    listing("", phone=""),                             # nothing to reach     -> skipped
    listing("https://www.facebook.com/plumber4"),      # only a Facebook page -> kept
]


class FakeMaps(FeedScraper):
    def __init__(self, cfg, *, headless=None, on_progress=None):
        super().__init__(LISTINGS)
        self.on_progress = on_progress


def test_cli_business_place_filters_and_short_result_note(tmp_path, monkeypatch, capsys):
    web = FakeWeb()                                    # offline: no page loads
    monkeypatch.setattr(jobs, "WebsiteEnricher", lambda cfg, **kw: web)
    monkeypatch.setattr(cli, "MapsScraper", FakeMaps)
    out = tmp_path / "leads.csv"
    args = cli.build_parser().parse_args(
        ["plumbers", "Austin, TX", "--website", "without", "--contact", "phone", "-n", "50", "-o", str(out), "-q"])
    assert asyncio.run(cli.run(args)) == 0

    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8-sig"))))
    assert [(r["Rank"], r["Website"]) for r in rows] == [("2", ""), ("4", "https://www.facebook.com/plumber4")]
    err = capsys.readouterr().err
    assert "wrote 2 row(s) for 2 place(s)" in err and "(no website · has a phone; 4 listing(s) scanned)" in err
    assert ("note: Only 2 of the 4 businesses Google Maps lists for “plumbers” in Austin, TX match your filters "
            "(no website · has a phone) — that's all of them (you asked for 50).") in err
    assert web.looked_up == ["https://www.facebook.com/plumber4"], "its Facebook page was checked for a website"


def test_cli_leaves_out_a_business_whose_social_page_links_to_its_website(tmp_path, monkeypatch, capsys):
    web = FakeWeb({"https://place4.com/": page()},
                  profile_links={"https://www.facebook.com/plumber4": ["https://place4.com/"]})
    monkeypatch.setattr(jobs, "WebsiteEnricher", lambda cfg, **kw: web)
    monkeypatch.setattr(cli, "MapsScraper", FakeMaps)
    out = tmp_path / "leads.csv"
    args = cli.build_parser().parse_args(
        ["plumbers", "Austin, TX", "--website", "without", "--contact", "phone", "-o", str(out), "-q"])
    assert asyncio.run(cli.run(args)) == 0

    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8-sig"))))
    assert [r["Rank"] for r in rows] == ["2"]
    err = capsys.readouterr().err
    assert "left out 1 place(s) with a website Google Maps does not list (1 by profile page)" in err


def test_cli_reports_a_google_block_and_still_writes_what_it_found(tmp_path, monkeypatch, capsys):
    class BlockedAfterFirstPlace(FakeMaps):
        async def _open_results(self, page, url, label):
            if "Dallas" in url:
                raise GoogleBlocked("https://www.google.com/sorry/index?continue=x", "203.0.113.7")
            return await super()._open_results(page, url, label)

    monkeypatch.setattr(jobs, "WebsiteEnricher", lambda cfg, **kw: FakeWeb())
    monkeypatch.setattr(cli, "MapsScraper", BlockedAfterFirstPlace)
    out = tmp_path / "leads.csv"
    args = cli.build_parser().parse_args(
        ["plumbers", "Austin, TX; Dallas, TX", "--website", "without", "--contact", "phone", "-o", str(out), "-q"])
    assert asyncio.run(cli.run(args)) == cli.EXIT_BLOCKED == 3

    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8-sig"))))
    assert [r["Rank"] for r in rows] == ["2", "4"]
    err = capsys.readouterr().err
    assert "wrote 2 row(s) for 2 place(s)" in err
    assert ("error: Google Maps blocked the search: it showed its “unusual traffic” check (a captcha) instead of "
            "results, so it is treating this IP address (203.0.113.7) as a robot. The 2 businesses found before "
            "that are kept.") in err


def test_cli_rejects_an_empty_search(capsys):
    args = cli.build_parser().parse_args(["", "Austin, TX"])
    assert asyncio.run(cli.run(args)) == 2
    assert "say what to look for" in capsys.readouterr().err


def test_cli_filter_flags():
    f = cli.filters_from_args(cli.build_parser().parse_args(
        ["plumbers", "--website", "with", "--contact", "phone_and_email", "--profiles-are-websites"]))
    assert (f.website.value, f.contact.value, f.profiles_as_no_website) == ("with", "phone_and_email", False)
    default = cli.filters_from_args(cli.build_parser().parse_args(["roofers near houston"]))
    assert not default.active and default.profiles_as_no_website and not default.search_web
    web = cli.filters_from_args(cli.build_parser().parse_args(["roofing", "--website", "without", "--search-web"]))
    assert web.search_web


def test_cli_whole_area_needs_a_place(capsys):
    args = cli.build_parser().parse_args(["roofing", "--whole-area"])
    assert asyncio.run(cli.run(args)) == 2
    assert "--whole-area needs a place" in capsys.readouterr().err


def test_cli_whole_area_searches_each_place_in_sections(tmp_path, monkeypatch):
    planned = []

    class Recorder(FeedScraper):
        def __init__(self, cfg, *, headless=None, on_progress=None):
            super().__init__(LISTINGS)

        async def scrape(self, searches, limit, *, selector=None):
            planned.extend(searches)
            return []

    monkeypatch.setattr(cli, "MapsScraper", Recorder)
    args = cli.build_parser().parse_args(["roofing", "Katy, TX; Pasadena, TX", "--whole-area",
                                          "-o", str(tmp_path / "x.csv"), "-q"])
    assert asyncio.run(cli.run(args)) == 0
    assert [(s.text, s.area) for s in planned] == [("roofing", "Katy, TX"), ("roofing", "Pasadena, TX")]
