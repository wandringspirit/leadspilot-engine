"""Preview websites: a business's site from its search row, how long a site stays online, and a campaign's
first emails waiting for their websites (sites/, app/outreach/sites.py, docs/SITES.md)."""
from __future__ import annotations

import html
import json
import os
import random
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.models import Job, JobStatus, PlaceRow
from app.outreach.mailbox import MailboxMessage
from app.outreach.manager import OutreachManager
from app.outreach.models import CampaignCreate, LeadStatus
from app.outreach.sites import SiteService, _wrangler_problem
from sites.build import build_site, load_manifest, searched_city, trade_pack
from sites.deploy import ends_at, prepare
from tests.test_outreach import TUESDAY_9AM, Clock, FakeMailbox, make_settings

HOUSTON = (29.7604, -95.3698)
TORONTO = (43.7198, -79.3776)


def roofer_row(**overrides) -> dict:
    row = dict(rank=1, name="Texas Home Roofing LLC", email="texashomeroof@gmail.com", city="", state="",
               category="Roofing contractor", rating=4.9, review_count=25, place_id="p-texas",
               phone="(281) 555-0101", latitude=HOUSTON[0] + 0.1, longitude=HOUSTON[1])
    row.update(overrides)
    return row


@pytest.fixture(scope="module")
def sites_dir(tmp_path_factory):
    # one folder for the module: the photos are resized once, then copied
    return tmp_path_factory.mktemp("sites")


# --------------------------------------------------------------------------
# how long a site stays online
# --------------------------------------------------------------------------
def test_how_long_a_site_stays_up():
    built = {"built_at": "2026-10-01T00:00:00+00:00"}
    assert ends_at(built) == datetime(2026, 10, 31, tzinfo=timezone.utc)
    emailed = {**built, "first_email_at": "2026-10-05T00:00:00+00:00"}
    assert ends_at(emailed) == datetime(2026, 10, 26, tzinfo=timezone.utc)
    linked = {**emailed, "link_sent_at": "2026-10-05T00:00:00+00:00"}
    assert ends_at(linked) == datetime(2026, 10, 19, tzinfo=timezone.utc)
    last = {**linked, "last_email_at": "2026-10-11T00:00:00+00:00"}       # "comes down tomorrow"
    assert ends_at(last) == datetime(2026, 10, 13, tzinfo=timezone.utc)
    replied = {**last, "replied_at": "2026-10-12T00:00:00+00:00"}
    assert ends_at(replied) == datetime(2026, 11, 11, tzinfo=timezone.utc)
    assert ends_at({**replied, "hold": True}) is None


def test_prepare_uploads_only_sites_still_up(tmp_path):
    sites, public = tmp_path / "sites", tmp_path / "public"
    for slug in ("fresh", "ended", "kept"):
        (sites / slug).mkdir(parents=True)
        (sites / slug / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    (sites / "sites.json").write_text(
        '{"fresh": {"built_at": "%s"}, "ended": {"built_at": "%s"}, "kept": {"built_at": "%s", "hold": true}}'
        % (now.isoformat(), old, old), encoding="utf-8")
    assert prepare(sites, public, now) == ["fresh", "kept"]
    assert (public / "fresh" / "index.html").is_file() and not (public / "ended").exists()
    status = (public / "_status.json").read_text(encoding="utf-8")
    assert '"hold": true' in status and "2026-11-14" in status
    assert json.loads(status)["fresh"]["to"] == ""                       # no address: requests wait for the panel
    # read-only folders (copied from a read-only design folder) are still replaced next time
    os.chmod(public / "fresh", stat.S_IREAD)
    os.chmod(public / "fresh" / "index.html", stat.S_IREAD)
    assert prepare(sites, public, now) == ["fresh", "kept"]


def test_quote_requests_go_straight_to_the_business_unless_it_must_not_be_emailed():
    from sites.deploy import deliver_to
    contacted = {"gone@x.com": {"status": "bounced"}, "stop@x.com": {"status": "unsubscribed"},
                 "ok@x.com": {"status": "emailed"}}
    assert deliver_to({"email": " OK@x.com "}, contacted) == "ok@x.com"
    assert deliver_to({"email": "new@x.com"}, contacted) == "new@x.com"
    assert deliver_to({"email": "gone@x.com"}, contacted) == deliver_to({"email": "stop@x.com"}, contacted) == ""
    assert deliver_to({}, contacted) == ""


# --------------------------------------------------------------------------
# building a business's site
# --------------------------------------------------------------------------
def test_the_searched_city_and_its_state():
    city, state, place, country = searched_city("Katy, TX", [])
    assert (city, state, country) == ("Katy", "TX", "US") and place["lat"] == pytest.approx(29.79, abs=0.05)
    city, state, place, _ = searched_city("houston", [{"state": "Texas"}])
    assert (city, state) == ("Houston", "TX") and place is not None
    # no business says its state: the Houston near them, not Houston, Missouri
    city, state, place, _ = searched_city("Houston", [{"latitude": HOUSTON[0], "longitude": HOUSTON[1]}])
    assert (city, state) == ("Houston", "TX")
    # a place the town list doesn't know: the middle of the businesses found
    city, state, center, _ = searched_city("Nowhere Flats", [{"state": "Texas", "latitude": 30.0, "longitude": -95.0},
                                                            {"state": "Texas", "latitude": 31.0, "longitude": -96.0}])
    assert (city, state) == ("Nowhere Flats", "TX") and center == {"lat": 31.0, "lon": -95.0}
    # outside the US: the country is written ("WA" alone is Washington); New Zealand has no region
    assert searched_city("Perth, WA, Australia", [])[::3] == ("Perth", "AU")
    assert searched_city("Perth, WA, Australia", [])[1] == "WA"
    assert searched_city("Calgary, AB, Canada", [])[:2] == ("Calgary", "AB")
    assert searched_city("Hamilton, New Zealand", [])[::3] == ("Hamilton", "NZ")
    assert searched_city("Geelong", [{"country": "AU", "state": "Victoria"}])[1::2] == ("VIC", "AU")


def test_only_trades_with_a_design_have_a_pack():
    assert trade_pack("Roofer")[0] == "roofer" and trade_pack("Plumber")[0] == "plumber"
    assert trade_pack("Pest control") is None and trade_pack("") is None


def test_a_business_without_a_city_gets_the_searched_one(sites_dir):
    trade, pack = trade_pack("Roofer")
    manifest: dict = {}
    slug, why = build_site(roofer_row(), searched_city("Houston", [{"state": "Texas"}]), pack, trade, manifest,
                           sites_dir, job_id="job_1")
    assert (slug, why) == ("texas-home-roofing", "")
    site = manifest[slug]
    assert (site["city"], site["state"], site["email"], site["trade"]) == ("Houston", "TX", "texashomeroof@gmail.com",
                                                                           "roofer")
    page = (sites_dir / slug / "index.html").read_text(encoding="utf-8")
    assert "Houston, TX" in page and 'class="rate-card"' in page
    assert (sites_dir / slug / "assets" / "roofer").is_dir()
    # built again (the same Google place): the same address
    again, _ = build_site(roofer_row(city="Katy", state="Texas"), searched_city("Houston", []), pack, trade, manifest,
                          sites_dir)
    assert again == slug and manifest[slug]["city"] == "Katy"


def test_the_keep_link_carries_the_whole_name(sites_dir):
    trade, pack = trade_pack("Roofer")
    manifest: dict = {}
    slug, why = build_site(roofer_row(name="Hill & Sons Roofing", place_id="p-hill", email="hill@example.com"),
                           searched_city("Houston", [{"state": "Texas"}]), pack, trade, manifest, sites_dir)
    assert why == ""
    page = (sites_dir / slug / "index.html").read_text(encoding="utf-8")
    link = re.search(r'class="pv-keep" href="([^"]+)"', page).group(1)
    url = urlsplit(html.unescape(link))
    assert parse_qs(url.query) == {"subject": ["Hill & Sons Roofing - keep my website"]}


def test_a_plumber_in_australia_gets_litres_its_state_and_a_dialable_number(sites_dir):
    trade, pack = trade_pack("Plumber")
    manifest: dict = {}
    row = dict(rank=1, name="Geelong Plumbing Pty Ltd", email="geelongplumbing@gmail.com", city="Geelong",
               state="Victoria", country="AU", category="Plumber", rating=4.8, review_count=31, place_id="p-geelong",
               phone="(03) 5222 1234", phone_unformatted="+61352221234", latitude=-38.15, longitude=144.36)
    slug, why = build_site(row, searched_city("Geelong, VIC, Australia", [row]), pack, trade, manifest, sites_dir)
    assert why == "" and manifest[slug]["country"] == "AU" and manifest[slug]["state"] == "VIC"
    page = (sites_dir / slug / "index.html").read_text(encoding="utf-8")
    assert 'href="tel:+61352221234"' in page and "Geelong, VIC" in page
    assert "litres a year" in page and "gallons" not in page and 'data-per-dpm="189"' in page
    assert "Washington" not in page


def test_a_roofer_in_australia_gets_tiles_and_roof_space_not_shingles(sites_dir):
    trade, pack = trade_pack("Roofer")
    manifest: dict = {}
    row = roofer_row(name="Geelong Roofing Pty Ltd", email="geelongroof@gmail.com", place_id="p-geelong-roof",
                     city="Geelong", state="Victoria", country="AU", latitude=-38.15, longitude=144.36,
                     phone="(03) 5222 1234", phone_unformatted="+61352221234")
    slug, why = build_site(row, searched_city("Geelong, VIC, Australia", [row]), pack, trade, manifest, sites_dir)
    page = (sites_dir / slug / "index.html").read_text(encoding="utf-8")
    assert why == "" and "Cracked or slipped tiles" in page and "roof space" in page
    assert not re.findall(r">[^<]*shingle[^<]*<", page, re.I), "only the photos' descriptions may say shingles"
    us_slug, _ = build_site(roofer_row(name="Bayou Roofing", place_id="p-bayou-roof", email="bayouroof@gmail.com"),
                            searched_city("Houston", [{"state": "Texas"}]), pack, trade, manifest, sites_dir)
    us = (sites_dir / us_slug / "index.html").read_text(encoding="utf-8")
    assert "Curled, cracked or missing shingles" in us and "How long does a shingle roof last in Texas?" in us


def test_a_plumber_in_the_us_still_gets_gallons(sites_dir):
    trade, pack = trade_pack("Plumber")
    manifest: dict = {}
    slug, why = build_site(roofer_row(name="Bayou Plumbing", place_id="p-bayou", email="bayou@gmail.com",
                                      category="Plumber", phone_unformatted="+12815550101"),
                           searched_city("Houston", [{"state": "Texas"}]), pack, trade, manifest, sites_dir)
    page = (sites_dir / slug / "index.html").read_text(encoding="utf-8")
    assert why == "" and "gallons a year" in page and 'data-per-dpm="50"' in page and "litres" not in page
    assert 'href="tel:+12815550101"' in page and "says the EPA" in page


@pytest.mark.parametrize("overrides, reason", [
    (dict(latitude=TORONTO[0], longitude=TORONTO[1]), "miles from Houston, outside the searched area"),
    (dict(category="Roofing supply store"), "a roofing supply store, not a business of the trade"),
])
def test_businesses_that_get_no_site(sites_dir, overrides, reason):
    trade, pack = trade_pack("Roofer")
    manifest: dict = {}
    slug, why = build_site(roofer_row(place_id="p-other", email="other@gmail.com", **overrides),
                           searched_city("Houston", [{"state": "Texas"}]), pack, trade, manifest, sites_dir)
    assert slug == "" and reason in why and manifest == {}


def test_a_low_rating_stays_off_the_site(sites_dir):
    trade, pack = trade_pack("Roofer")
    slug, _ = build_site(roofer_row(name="Low Stars Roofing", place_id="p-low", email="low@gmail.com", rating=3.3,
                                    review_count=21),
                         searched_city("Houston", [{"state": "Texas"}]), pack, trade, {}, sites_dir)
    assert 'class="rate-card"' not in (sites_dir / slug / "index.html").read_text(encoding="utf-8")


def test_wrangler_problems_in_plain_words():
    assert "deploy.py --login" in _wrangler_problem("✘ [ERROR] You are not authenticated. Please run `wrangler login`.")
    assert _wrangler_problem("building...\n✘ [ERROR] A request to the Cloudflare API failed.\n") == \
        "A request to the Cloudflare API failed."


# --------------------------------------------------------------------------
# campaigns
# --------------------------------------------------------------------------
def roofers_job() -> Job:
    job = Job(query="roofing in Houston", business="roofing", place="Houston", limit=20, status=JobStatus.COMPLETED)
    job.rows.extend([
        PlaceRow(**roofer_row()),
        PlaceRow(**roofer_row(rank=2, name="Far Away Roofing", email="far@gmail.com", place_id="p-far",
                              latitude=TORONTO[0], longitude=TORONTO[1])),
        PlaceRow(**roofer_row(rank=3, name="IXCA Roofing Supplies", email="orders@ixca.com", place_id="p-ixca",
                              category="Roofing supply store")),
    ])
    return job


def site_manager(tmp_path):
    clock = Clock(TUESDAY_9AM)
    uploads: list[int] = []

    async def uploader() -> None:
        uploads.append(1)

    cfg = make_settings(tmp_path, PREVIEW_SITES=True)
    sites = SiteService(cfg, uploader=uploader, clock=clock)
    manager = OutreachManager(cfg, mailbox=FakeMailbox(), clock=clock, rng=random.Random(7), sites=sites)
    return manager, sites, uploads, clock


async def test_a_campaign_builds_its_websites_and_first_emails_wait_for_them(tmp_path):
    manager, sites, uploads, clock = site_manager(tmp_path)
    roofer = next(t for t in manager.list_business_types() if t.name == "Roofer")
    job = roofers_job()
    campaign = await manager.create(CampaignCreate(job_ids=[job.id], business_type_id=roofer.id), [job])
    try:
        assert campaign.sites and [lead.site for lead in campaign.leads] == ["texas-home-roofing"]
        reasons = {o.name: o.reason for o in campaign.left_out}
        assert "outside the searched area" in reasons["Far Away Roofing"]
        assert "supply store" in reasons["IXCA Roofing Supplies"]

        await manager.start(campaign)
        now = clock()
        assert manager._next_email(now) is None                 # the website isn't online yet
        assert sites.unpublished() == ["texas-home-roofing"]

        assert await sites.upload() and uploads == [1]
        assert (tmp_path / "deploy" / "public" / "texas-home-roofing" / "index.html").is_file()
        pick = manager._next_email(now)
        assert pick is not None and pick[1].site == "texas-home-roofing"
        assert "texas-home-roofing" in manager.status()["sites"]["online"]

        await manager._send(*pick, now)
        site = load_manifest(sites.dir)["texas-home-roofing"]
        assert site["first_email_at"] == site["link_sent_at"] == now.isoformat(timespec="seconds")
        assert "https://sites.leadspilot.co/texas-home-roofing/" in manager.results(campaign).decode("utf-8-sig")
    finally:
        await sites.stop()


async def test_the_roofer_emails_link_the_site_and_set_its_end(tmp_path):
    manager, sites, _, clock = site_manager(tmp_path)
    roofer = next(t for t in manager.list_business_types() if t.name == "Roofer")
    job = roofers_job()
    campaign = await manager.create(CampaignCreate(job_ids=[job.id], business_type_id=roofer.id), [job])
    try:
        assert await sites.upload()
        await manager.start(campaign)
        lead = campaign.leads[0]
        link = "https://sites.leadspilot.co/texas-home-roofing/"
        for step in (1, 2, 3):
            await manager._send(campaign, lead, clock())
            message = manager.mailbox.sent[-1]
            assert link in message.get_content() and message["List-Unsubscribe"] is None
            clock.advance(days=3)
        assert str(manager.mailbox.sent[0]["Subject"]) == "Texas Home Roofing website"
        assert "comes down tomorrow" in manager.mailbox.sent[-1].get_content()
        site = load_manifest(sites.dir)["texas-home-roofing"]
        last = datetime.fromisoformat(site["last_email_at"])
        assert ends_at(site) == last + timedelta(days=2)

        # a reply that says no keeps it up for a while, no more
        manager.mailbox.inbox.append(MailboxMessage(sender="texashomeroof@gmail.com", subject="Re: website",
                                                    message_id="<r@x>", received_at=clock(),
                                                    text="No\n\nSent from my iPhone\n> Hi,"))
        await manager._check(clock())
        site = load_manifest(sites.dir)["texas-home-roofing"]
        assert not site["hold"] and ends_at(site) == datetime.fromisoformat(site["replied_at"]) + timedelta(days=30)

        # any other reply puts it on hold
        assert sites.set_hold("texas-home-roofing", False) is False
        lead = next(lead for c in manager.campaigns.values() for lead in c.leads if lead.site == "texas-home-roofing")
        lead.status, lead.inbound = LeadStatus.SENT, []
        manager.mailbox.inbox = [MailboxMessage(sender="texashomeroof@gmail.com", subject="Re: website",
                                                message_id="<r2@x>", received_at=clock(), text="What does it cost?")]
        await manager._check(clock())
        site = load_manifest(sites.dir)["texas-home-roofing"]
        assert site["hold"] and ends_at(site) is None and sites.status()["held"] == ["texas-home-roofing"]
        with pytest.raises(KeyError):
            sites.set_hold("nobody", True)
    finally:
        await sites.stop()


async def test_a_campaign_for_a_trade_without_a_design_cannot_start(tmp_path):
    manager, sites, _, _ = site_manager(tmp_path)
    pests = next(t for t in manager.list_business_types() if t.name == "Pest control")
    job = Job(query="pest control service in Houston", business="pest control service", place="Houston", limit=5,
              status=JobStatus.COMPLETED)
    job.rows.append(PlaceRow(rank=1, name="Bayou Bug Guys", email="bugguys@gmail.com", place_id="p1"))
    campaign = await manager.create(CampaignCreate(job_ids=[job.id], business_type_id=pests.id), [job])
    assert [lead.site for lead in campaign.leads] == [""]
    assert any("no website design for Pest control" in p for p in manager.campaign_problems(campaign))


async def test_a_failed_upload_says_why(tmp_path):
    manager, sites, _, _ = site_manager(tmp_path)

    async def not_logged_in() -> None:
        from app.outreach.sites import SiteError
        raise SiteError("not logged in to Cloudflare - in sites\\worker run: npx wrangler login")

    sites.uploader = not_logged_in
    assert not await sites.upload()
    assert "wrangler login" in manager.status()["sites"]["last_upload"]["error"]


def test_the_plumber_pack_renders_its_own_drawing_and_drips(tmp_path):
    """The plumber pack: drips instead of rain, the drawn plumbing check inlined, its own words."""
    from sites.render import render
    root = Path(__file__).resolve().parent.parent / "sites"
    html = render(root / "trades" / "plumber.json", root / "samples" / "plumber-houston.json", tmp_path / "p").read_text(
        encoding="utf-8")
    assert html.count('data-fx="drips"') == 2 and '<svg class="hx"' in html and "hx-flow-cold" in html
    assert '"@type": "Plumber"' in html and "Book a plumber" in html and "#i-wrench" in html
    words = re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", html, flags=re.S)).lower()   # what a visitor reads
    assert "roof" not in words and "inspection" not in words


def test_plumbers_get_website_b_and_website_a_stays_renderable(tmp_path):
    """Plumbers get website B (the flow design): A's words and drawing plus the helper, the drip calculator,
    the rating tank and the waves. Website A ("extends": plumber.json, the bold design) still renders."""
    from sites.render import load_pack, render
    root = Path(__file__).resolve().parent.parent / "sites"
    pack = load_pack(root / "trades" / "plumber-a.json")
    assert pack["design"] == "bold" and pack["label"] == "Plumbing" and "extends" not in pack
    a = render(root / "trades" / "plumber-a.json", root / "samples" / "plumber-houston.json", tmp_path / "a",
               variant=0).read_text(encoding="utf-8")
    assert '<svg class="hx"' in a and 'role="tab"' not in a and "data-calc" not in a and 'class="wave"' not in a
    assert load_pack(root / "trades" / "plumber.json")["design"] == "flow"
    html = render(root / "trades" / "plumber.json", root / "samples" / "plumber-houston.json", tmp_path / "b",
                  variant=0).read_text(encoding="utf-8")
    assert '<svg class="hx"' in html and html.count('data-fx="drips"') == 2
    assert html.count('role="tab"') == 4 and html.count('role="tabpanel"') == 4 and 'data-pick="water-heaters"' in html
    assert "data-calc-in" in html and ">1,500<" in html and ">88<" in html       # 30 drips a minute
    assert 'class="ring tank"' in html and "--lvl:10.1px" in html and html.count('class="wave"') == 7
    assert "--wave:url(&quot;" not in html and "fill=%22%231EC8E8%22" in html     # the data URI is not escaped
    assert (tmp_path / "b" / "site.css").read_text(encoding="utf-8").count(".help-tab") > 3


def test_hvac_gets_the_air_design_with_its_thermostat_calculator_and_gauge(tmp_path):
    """HVAC businesses get the air design: a thermostat in the hero, the system check drawing, the "what's your
    system doing?" helper, the old-AC calculator (the page's first numbers match what its JS shows) and the gauge."""
    from sites.render import load_pack, render
    root = Path(__file__).resolve().parent.parent / "sites"
    assert trade_pack("HVAC")[1]["design"] == "air"
    pack = load_pack(root / "trades" / "hvac.json")
    words = json.dumps(pack).lower()
    assert not re.search(r"\bfree\b|\blicensed\b|24/7", words)
    out = tmp_path / "hvac"
    page = render(root / "trades" / "hvac.json", root / "samples" / "hvac-phoenix.json", out, variant=0).read_text(
        encoding="utf-8")
    assert '<svg class="hx"' in page and "hx-fan" in page and page.count('data-fx="air"') == 2
    assert 'role="slider"' in page and 'aria-valuenow="72"' in page and "--t:0.48" in page
    assert "stroke-dasharray:36.0 100" in page and 'data-room-start="77"' in page
    assert page.count('role="tab"') == 4 and 'data-pick="ac-repair"' in page
    assert "data-ec-in" in page and ">$28<" in page and ">$113<" in page          # $150 a month, 13 -> 16 SEER
    assert 'class="ring gauge"' in page and "--r:4.8" in page
    assert "wave" not in page and "drip" not in page
    css = (out / "site.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: no-preference" in css and ".dial-knob" in css and ".hero-frost" in css


def test_electricians_get_the_spark_design_with_its_switch_led_calculator_and_power_check(tmp_path):
    """Electricians get the spark design: a wall switch and dimmer in the hero, current on circuit traces
    (data-fx="spark"), the power check drawing, the helper, the LED calculator (the page's first numbers match what
    its JS shows) and the gauge."""
    from sites.render import load_pack, render
    root = Path(__file__).resolve().parent.parent / "sites"
    assert trade_pack("Electrician")[1]["design"] == "spark"
    pack = load_pack(root / "trades" / "electrician.json")
    assert not re.search(r"\bfree\b|\blicensed\b|24/7", json.dumps(pack).lower())
    out = tmp_path / "electrician"
    page = render(root / "trades" / "electrician.json", root / "samples" / "electrician-denver.json", out,
                  variant=0).read_text(encoding="utf-8")
    assert '<svg class="hx"' in page and "sx-meter-disc" in page and page.count('data-fx="spark"') == 2
    assert 'aria-pressed="true"' in page and "data-dim" in page and "<span data-watts>43</span>" in page
    assert page.count('role="tab"') == 4 and 'data-pick="outlets"' in page
    assert ">$190<" in page and ">1,117<" in page and ">$223<" in page and ">$34<" in page   # 20 bulbs, 3 h, 17¢
    assert 'class="ring gauge"' in page and "data-dial" not in page and "data-ecalc" not in page
    css = (out / "site.css").read_text(encoding="utf-8")
    assert ".sw-plate" in css and "prefers-reduced-motion: no-preference" in css and ".ec-" not in css
