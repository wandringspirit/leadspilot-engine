"""Who opened which website (app/outreach/visits.py, OutreachManager.record_visits) and the daily test copy.
No network: the Worker's answers are made up here."""
from __future__ import annotations

import json
from datetime import timedelta

from app.outreach.mailbox import mailbox_problems
from app.outreach.manager import OutreachManager
from app.outreach.visits import Visits, place, who_is
from tests.test_outreach import TUESDAY_9AM, Clock, FakeMailbox, make_settings, run, started

ME, BUSINESS = "aaaa", "bbbb"


def visit(slug, kind="view", *, at=TUESDAY_9AM, who=BUSINESS, id=1, **extra):
    return {"id": id, "at": at.isoformat().replace("+00:00", "Z"), "slug": slug, "kind": kind, "who": who,
            "org": "Comcast Cable", "country": "US", "city": "Houston", "ua": "Mozilla/5.0 (iPhone)", "copy": 0,
            "as": "visitor", **extra}


def test_whose_visit_it_is():
    mine, countries = {ME}, {"IN"}
    assert who_is(visit("s"), mine, countries) == "visitor"
    assert who_is(visit("s", who=ME), mine, countries) == "you"                    # this computer
    assert who_is(visit("s", country="IN"), mine, countries) == "you"              # your phone
    assert who_is(visit("s", copy=1), mine, countries) == "copy"
    assert who_is(visit("s", ua="Mozilla/5.0 (compatible; Googlebot/2.1)"), mine, countries) == "check"
    assert who_is(visit("s", org="Microsoft Corporation"), mine, countries) == "check"   # Safe Links
    assert place(visit("s")) == "Comcast Cable · Houston, US"


def test_visits_are_fetched_page_by_page_and_kept(tmp_path):
    pages = {0: {"visits": [visit("a", id=1), visit("a", "seen", id=2)], "more": True,
                 "me": {"who": ME, "country": "IN"}},
             2: {"visits": [visit("b", who=ME, id=3)], "more": False, "me": {"who": ME, "country": "IN"}}}
    asked = []

    async def fetch(after):
        asked.append(after)
        return pages[after]

    visits = Visits(make_settings(tmp_path, SITES_VISITS_KEY="k"), fetch=fetch, clock=Clock(TUESDAY_9AM))
    handed = []
    assert run(visits.sync(handed.extend)) == 3 and asked == [0, 2]
    assert [v["as"] for v in handed] == ["visitor", "visitor", "you"]
    assert len((tmp_path / "sites" / "visits.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    again = Visits(make_settings(tmp_path, SITES_VISITS_KEY="k"), fetch=fetch)
    assert again.state["after"] == 3 and again.state["mine"] == [ME] and again.state["countries"] == ["IN"]

    async def broken(after):
        raise ValueError("the websites don't know this key")

    again.fetch = broken
    assert run(again.sync(handed.extend)) == 0 and "key" in again.status()["error"] and len(handed) == 3


def test_a_business_opening_its_website(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    run(manager.tick())                                              # the first email at 09:00
    lead = campaign.leads[0]
    lead.site = "palm-breeze"
    notes = []
    manager._note = lambda what, detail="", c=None, l=None: notes.append((what, detail))
    sent = lead.sent[0].sent_at
    # a page load 30 seconds after the email that didn't stay open: a mail server checking the link
    manager.record_visits([visit("palm-breeze", at=sent + timedelta(seconds=30), org="Rackspace", id=1)])
    assert lead.site_opened_at is None and notes == []
    # the same, but the page stayed open: a person
    manager.record_visits([visit("palm-breeze", at=sent + timedelta(seconds=40), id=2),
                           visit("palm-breeze", "seen", at=sent + timedelta(seconds=43), id=3)])
    assert lead.site_opened_at == sent + timedelta(seconds=40) and lead.site_visits == 1
    assert notes == [("Website opened", "Comcast Cable · Houston, US")]
    # later that day: counted, not written down again; a tap is, once
    later = sent + timedelta(hours=2)
    manager.record_visits([visit("palm-breeze", at=later, id=4), visit("palm-breeze", "call", at=later, id=5),
                           visit("palm-breeze", "call", at=later, id=6)])
    assert lead.site_visits == 2 and lead.site_taps == ["call"]
    assert [what for what, _ in notes] == ["Website opened", "Tapped call"]
    # yours, a test copy's, a scanner's, a site that isn't a campaign's: not counted
    manager.record_visits([visit("palm-breeze", at=later, id=7, **{"as": "you"}),
                           visit("palm-breeze", at=later, id=8, **{"as": "copy"}),
                           visit("palm-breeze", at=later, id=9, **{"as": "check"}),
                           visit("someone-else", at=later, id=10)])
    assert lead.site_visits == 2
    # the next day: written down again; saved with the campaign
    manager.record_visits([visit("palm-breeze", at=sent + timedelta(days=1), id=11)])
    assert [what for what, _ in notes].count("Website opened") == 2
    saved = json.loads((tmp_path / "campaigns" / f"{campaign.id}.json").read_text(encoding="utf-8"))
    assert saved["leads"][0]["site_visits"] == 3 and saved["leads"][0]["site_taps"] == ["call"]
    assert "Website Opened" in manager.results(campaign).decode("utf-8-sig").splitlines()[0]


def test_the_days_first_email_goes_to_your_test_addresses_too(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock, OUTREACH_TEST_COPY_TO="me@gmail.com, not-an-address")
    run(manager.tick())
    assert [m["To"] for m in mailbox.sent] == ["palmbreeze@gmail.com", "me@gmail.com"]
    assert mailbox.sent[1]["Subject"] == mailbox.sent[0]["Subject"]
    assert mailbox.sent[1].get_content() == mailbox.sent[0].get_content()
    clock.advance(seconds=61)
    run(manager.tick())
    assert [m["To"] for m in mailbox.sent][2:] == ["hampshire@gmail.com"]   # only the day's first
    assert manager.status()["test_copy_to"] == ["me@gmail.com"]


def test_the_test_copy_link_is_marked(tmp_path):
    class Sites:
        def url(self, slug):
            return f"https://sites.example/{slug}/"

    mailbox = FakeMailbox()
    manager = OutreachManager(make_settings(tmp_path, OUTREACH_TEST_COPY_TO="me@gmail.com"), mailbox=mailbox,
                              clock=Clock(TUESDAY_9AM))
    manager.sites = Sites()
    _, _, campaign, _ = started(tmp_path / "other")
    lead = campaign.leads[0]
    lead.site = "palm"
    run(manager._test_copy(campaign, lead, 1, "website for Palm", "See it: https://sites.example/palm/\n"))
    assert "https://sites.example/palm/?copy" in mailbox.sent[0].get_content()


def test_a_gmail_login_must_be_an_address(tmp_path):
    problems = mailbox_problems(make_settings(tmp_path, SMTP_HOST="smtp.gmail.com", IMAP_HOST="imap.gmail.com",
                                              MAIL_USERNAME="piyush.reach.leadspilot.co"))
    assert any("not an email address" in p and "MAIL_USERNAME" in p for p in problems)
