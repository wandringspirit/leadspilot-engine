"""Preview websites: a business's site from its search row, how long a site stays online, and a campaign's
first emails waiting for their websites (sites/, app/outreach/sites.py, docs/SITES.md)."""
from __future__ import annotations

import os
import random
import stat
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Job, JobStatus, PlaceRow
from app.outreach.mailbox import MailboxMessage
from app.outreach.manager import OutreachManager
from app.outreach.models import CampaignCreate
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
    # read-only folders (copied from a read-only design folder) are still replaced next time
    os.chmod(public / "fresh", stat.S_IREAD)
    os.chmod(public / "fresh" / "index.html", stat.S_IREAD)
    assert prepare(sites, public, now) == ["fresh", "kept"]


# --------------------------------------------------------------------------
# building a business's site
# --------------------------------------------------------------------------
def test_the_searched_city_and_its_state():
    city, state, place = searched_city("Katy, TX", [])
    assert (city, state) == ("Katy", "TX") and place["lat"] == pytest.approx(29.79, abs=0.05)
    city, state, place = searched_city("houston", [{"state": "Texas"}])
    assert (city, state) == ("Houston", "TX") and place is not None
    # no business says its state: the Houston near them, not Houston, Missouri
    city, state, place = searched_city("Houston", [{"latitude": HOUSTON[0], "longitude": HOUSTON[1]}])
    assert (city, state) == ("Houston", "TX")
    # a place the town list doesn't know: the middle of the businesses found
    city, state, center = searched_city("Nowhere Flats", [{"state": "Texas", "latitude": 30.0, "longitude": -95.0},
                                                         {"state": "Texas", "latitude": 31.0, "longitude": -96.0}])
    assert (city, state) == ("Nowhere Flats", "TX") and center == {"lat": 31.0, "lon": -95.0}


def test_only_trades_with_a_design_have_a_pack():
    assert trade_pack("Roofer")[0] == "roofer"
    assert trade_pack("Plumber") is None and trade_pack("") is None


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

        # a reply keeps it up while you talk
        manager.mailbox.inbox.append(MailboxMessage(sender="texashomeroof@gmail.com", subject="Re: website",
                                                    message_id="<r@x>", received_at=clock()))
        await manager._check(clock())
        site = load_manifest(sites.dir)["texas-home-roofing"]
        assert ends_at(site) == datetime.fromisoformat(site["replied_at"]) + timedelta(days=30)
    finally:
        await sites.stop()


async def test_a_campaign_for_a_trade_without_a_design_cannot_start(tmp_path):
    manager, sites, _, _ = site_manager(tmp_path)
    plumber = next(t for t in manager.list_business_types() if t.name == "Plumber")
    job = Job(query="plumber in Houston", business="plumber", place="Houston", limit=5, status=JobStatus.COMPLETED)
    job.rows.append(PlaceRow(rank=1, name="Palm Breeze Plumbing", email="palm@gmail.com", place_id="p1"))
    campaign = await manager.create(CampaignCreate(job_ids=[job.id], business_type_id=plumber.id), [job])
    assert [lead.site for lead in campaign.leads] == [""]
    assert any("no website design for Plumber" in p for p in manager.campaign_problems(campaign))


async def test_a_failed_upload_says_why(tmp_path):
    manager, sites, _, _ = site_manager(tmp_path)

    async def not_logged_in() -> None:
        from app.outreach.sites import SiteError
        raise SiteError("not logged in to Cloudflare - in sites\\worker run: npx wrangler login")

    sites.uploader = not_logged_in
    assert not await sites.upload()
    assert "wrangler login" in manager.status()["sites"]["last_upload"]["error"]
