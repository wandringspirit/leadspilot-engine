"""Email outreach: templates, leads, the send window, the mailbox parsing and the send loop.

Everything runs against a fake mailbox and a fake clock - no network, no real email.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models import Job, JobStatus, PlaceRow
from app.outreach.leads import email_problem, leads_from_jobs, search_city
from app.outreach.mailbox import (MailAuthError, MailError, MailboxMessage, RecipientRefused, build_message,
                                  classify, imap_date, mail_password, mailbox_problems, parse_headers)
from app.outreach.manager import OutreachError, OutreachManager, steps_problems
from app.outreach.models import (BusinessTypeCreate, BusinessTypeUpdate, Campaign, CampaignCreate, CampaignStatus,
                                 CampaignUpdate, Lead, LeadStatus, LeadUpdate, SendTestRequest, Step)
from app.outreach.schedule import SendWindow, WindowError, day_label, days_label, parse_days, parse_hours
from app.outreach.templates import (compose, display_name, missing_fields, render, required_fields,
                                    template_errors)

UTC = timezone.utc
TUESDAY_9AM = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)      # 09:00 in Houston (CDT, UTC-5)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        _env_file=None, DATA_DIR=tmp_path, SMTP_HOST="smtp.test", SMTP_PORT=587, IMAP_HOST="imap.test",
        MAIL_USERNAME="me@mysender.com", MAIL_PASSWORD="secret", SENDER_NAME="Pat", SENDER_COMPANY="Pat's Web Co",
        SENDER_ADDRESS="1 Main St, Houston, TX 77002", OUTREACH_AUTOSEND=True, OUTREACH_TIMEZONE="America/Chicago",
        OUTREACH_DAYS="mon-fri", OUTREACH_HOURS="8-12", OUTREACH_DAILY_LIMIT=30, OUTREACH_MIN_GAP_S=60,
        OUTREACH_MAX_GAP_S=60, OUTREACH_CHECK_MINUTES=10, OUTREACH_MIN_REVIEWS=10, OUTREACH_MIN_RATING=4.0,
    )
    values.update(overrides)
    return Settings(**values)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class FakeMailbox:
    def __init__(self) -> None:
        self.sent = []
        self.inbox: list[MailboxMessage] = []
        self.fetches = 0
        self.send_error: Exception | None = None
        self.fetch_error: Exception | None = None

    async def send(self, message) -> None:
        if self.send_error:
            raise self.send_error
        self.sent.append(message)

    async def fetch(self, since, limit=500):
        self.fetches += 1
        if self.fetch_error:
            raise self.fetch_error
        return list(self.inbox)


def plumbers_job(place: str = "Houston") -> Job:
    job = Job(query=f"plumber in {place}", business="plumber", place=place, limit=20, status=JobStatus.COMPLETED)
    rows = [
        PlaceRow(rank=1, name="Palm Breeze Plumbing LLC", email="palmbreeze@gmail.com", rating=4.8, review_count=57,
                 place_id="p1", phone="(713) 555-0101"),
        PlaceRow(rank=2, name="Hampshire Plumbing", email="hampshire@gmail.com", rating=4.6, review_count=17,
                 place_id="p2", website="https://www.facebook.com/hampshireplumbing"),
        PlaceRow(rank=3, name="Own Site Plumbing", email="bob@ownsiteplumbing.com", place_id="p3",
                 website="https://ownsiteplumbing.com/"),
        PlaceRow(rank=4, name="BBB Only Plumbing", email="info@bbbemail.org", place_id="p4"),
        PlaceRow(rank=4, name="BBB Only Plumbing", email="carrie@bbbemail.org", place_id="p4"),
        PlaceRow(rank=5, name="No Email Plumbing", phone="(713) 555-0105", place_id="p5"),
        PlaceRow(rank=6, name="Lia Constructions LLC", email="lia@gmail.com", rating=5.0, review_count=3,
                 place_id="p6"),
        PlaceRow(rank=6, name="Lia Constructions LLC", email="name@example.com", rating=5.0, review_count=3,
                 place_id="p6"),
    ]
    job.rows.extend(rows)
    return job


def make_manager(tmp_path, clock=None, **overrides):
    mailbox = FakeMailbox()
    manager = OutreachManager(make_settings(tmp_path, **overrides), mailbox=mailbox,
                              clock=clock or Clock(TUESDAY_9AM), rng=random.Random(7))
    return manager, mailbox


def reply(sender: str, subject: str, *, refs=(), message_id="<r1@mail.test>", auto=False) -> MailboxMessage:
    return MailboxMessage(sender=sender, subject=subject, message_id=message_id, refs=set(refs), auto=auto,
                          received_at=TUESDAY_9AM)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------
def test_render_fills_placeholders_and_optional_parts():
    text = "Hi {Business}[[ in {City}]]. [[{Rating} stars||No stars]] {Unknown}"
    assert render(text, {"Business": "Acme", "City": "Houston", "Rating": "4.8"}) == \
        "Hi Acme in Houston. 4.8 stars {Unknown}"
    assert render(text, {"Business": "Acme"}) == "Hi Acme. No stars {Unknown}"


def test_render_does_not_fill_values_twice_and_tidies_lines():
    assert render("{Business}", {"Business": "{City} Roofing"}) == "{City} Roofing"
    assert render("a  \n\n\n\nb\n\n", {}) == "a\n\nb"


def test_template_errors():
    assert template_errors("Hi {Business} [[{City}||x]]") == []
    assert "unknown placeholder {Buisness}" in template_errors("Hi {Buisness}")[0]
    assert "not closed" in template_errors("Hi [[ {City}")[0]
    assert "inside each other" in template_errors("[[a [[b]] c]]")[0]
    assert required_fields("{Business} [[{City}]] {Trade}") == {"Business", "Trade"}


@pytest.mark.parametrize("name, expected", [
    ("High Caliber Roofing LLC.", "High Caliber Roofing"),
    ("Berger Plumbing, LLC", "Berger Plumbing"),
    ("D&M Roofing Company LLC", "D&M Roofing Company"),
    ('The "GI" Guy LLC', 'The "GI" Guy'),
    ("Acme Roofing Inc.", "Acme Roofing"),
    ("Imperial Plumbing Co.", "Imperial Plumbing Co."),
    ("Lia Constructions LLC Plumbing", "Lia Constructions LLC Plumbing"),
    ("LLC", "LLC"),
])
def test_display_name(name, expected):
    assert display_name(name) == expected


def test_compose_first_email_and_follow_up(tmp_path):
    cfg = make_settings(tmp_path)
    campaign = Campaign(name="Plumbers · Houston", trade="plumbers")
    lead = Lead(email="a@gmail.com", business="Palm Breeze Plumbing", city="Houston", rating=4.8, reviews=57)
    subject, body = compose(campaign, lead, 1, cfg)
    assert subject == "website for Palm Breeze Plumbing"
    assert "while looking at plumbers in Houston. 4.8 stars from 57 reviews, but there's no website" in body
    assert body.endswith("Pat\n\nPat's Web Co\n1 Main St, Houston, TX 77002\n"
                         "If you'd rather not hear from me, just reply \"no\" and I won't email again.")
    few = Lead(email="b@gmail.com", business="Lia Constructions", city="Houston", rating=5.0, reviews=3)
    assert "There's no website on your Google listing, so people" in compose(campaign, few, 1, cfg)[1]
    subject, body = compose(campaign, lead, 2, cfg)
    assert subject == "Re: website for Palm Breeze Plumbing"
    assert body.startswith("Hi there,\n\nJust bringing this back up")


def test_missing_fields(tmp_path):
    cfg = make_settings(tmp_path)
    campaign = Campaign(name="x", trade="plumbers")
    assert missing_fields(campaign, Lead(email="a@b.co", business="Acme", city=""), cfg) == []   # [[ in {City}]]
    campaign.steps[1].body = "Still keen, {Business} in {City}?"
    assert missing_fields(campaign, Lead(email="a@b.co", business="Acme", city=""), cfg) == ["City"]


def test_steps_problems():
    good = [Step(day=0, subject="hi {Business}", body="text"), Step(day=3, body="more")]
    assert steps_problems(good, "{SenderAddress}") == []
    problems = steps_problems([Step(day=2, subject="", body="{Oops}"), Step(day=2, body="x")], "no address")
    text = " | ".join(problems)
    assert "day 0" in text and "must come after day 2" in text and "needs a subject" in text
    assert "unknown placeholder {Oops}" in text and "{SenderAddress}" in text


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------
@pytest.mark.parametrize("address, bad", [
    ("palmbreeze@gmail.com", False),
    ("owner@smithplumbing.com", False),
    ("info@bbbemail.org", True),
    ("name@example.com", True),
    ("page@facebook.com", True),
    ("no-at-sign", True),
])
def test_email_problem(address, bad):
    assert bool(email_problem(address)) is bad


def test_leads_from_jobs():
    leads, left_out = leads_from_jobs([plumbers_job()], taken={})
    assert [lead.business for lead in leads] == ["Palm Breeze Plumbing", "Hampshire Plumbing", "Lia Constructions"]
    lia = leads[2]
    assert lia.email == "lia@gmail.com" and lia.other_emails == [] and lia.city == "Houston"
    reasons = {o.name: o.reason for o in left_out}
    assert reasons["Own Site Plumbing"] == "has a website (ownsiteplumbing.com)"
    assert reasons["BBB Only Plumbing"].startswith("no address of its own: address of bbbemail.org")
    assert reasons["No Email Plumbing"] == "no email"


def test_leads_duplicates_and_taken():
    first, second = plumbers_job(), plumbers_job("Katy")
    leads, left_out = leads_from_jobs([first, second], taken={"hampshire@gmail.com": "emailed on 1 Sep 2026"})
    assert [lead.business for lead in leads] == ["Palm Breeze Plumbing", "Lia Constructions"]
    reasons = [o.reason for o in left_out]
    assert "emailed on 1 Sep 2026" in reasons
    assert reasons.count("found by two of the selected searches") == 6     # every place of the second job


def test_search_city():
    job = Job(query="q", business="plumber", place="Katy, TX; Sugar Land, TX", limit=5)
    assert search_city(job, PlaceRow(city="Sugar Land")) == "Sugar Land, TX"
    assert search_city(job, PlaceRow(city="Richmond")) == "Richmond"
    assert search_city(job, PlaceRow(city="")) == "Katy, TX"


# --------------------------------------------------------------------------
# send window
# --------------------------------------------------------------------------
def test_parse_days_and_hours():
    assert parse_days("mon-fri") == frozenset(range(5))
    assert parse_days("mon, wed,fri") == frozenset({0, 2, 4})
    assert parse_days("fri-mon") == frozenset({4, 5, 6, 0})
    assert parse_hours("8-12") == (8, 12)
    for bad in ("weekdays", ""):
        with pytest.raises(WindowError):
            parse_days(bad)
    for bad in ("8", "12-8", "8-25", "x-y"):
        with pytest.raises(WindowError):
            parse_hours(bad)
    assert days_label(frozenset(range(5))) == "Mon–Fri"
    assert days_label(frozenset({0, 2, 4})) == "Mon, Wed, Fri"
    assert day_label(date(2026, 9, 29)) == "29 Sep 2026"


def test_window_open_and_next_open():
    window = SendWindow.parse("America/Chicago", "mon-fri", "8-12")
    assert window.is_open(TUESDAY_9AM)
    assert not window.is_open(TUESDAY_9AM + timedelta(hours=3))                        # 12:00
    friday_noon = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)
    assert window.next_open(friday_noon) == datetime(2026, 10, 5, 13, 0, tzinfo=UTC)   # Monday 08:00 CDT
    early = datetime(2026, 9, 29, 11, 0, tzinfo=UTC)                                   # 06:00 CDT
    assert window.next_open(early) == datetime(2026, 9, 29, 13, 0, tzinfo=UTC)
    # after the clocks change (CST, UTC-6)
    assert window.next_open(datetime(2026, 11, 2, 12, 0, tzinfo=UTC)) == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    assert window.label() == "Mon–Fri 08:00–12:00 (America/Chicago)"
    with pytest.raises(WindowError, match="tzdata"):
        SendWindow.parse("Mars/Olympus", "mon-fri", "8-12")


# --------------------------------------------------------------------------
# mailbox: building and reading emails
# --------------------------------------------------------------------------
def test_build_message_threads_follow_ups():
    message = build_message(sender="Pat <me@mysender.com>", to="a@gmail.com", subject="Re: hello", body="text",
                            message_id="<m2@mysender.com>", earlier_ids=["<m1@mysender.com>"],
                            unsubscribe="me@mysender.com")
    assert message["In-Reply-To"] == "<m1@mysender.com>" and message["References"] == "<m1@mysender.com>"
    assert message["List-Unsubscribe"] == "<mailto:me@mysender.com?subject=unsubscribe>"
    assert message.get_content_type() == "text/plain" and message.get_content().strip() == "text"


def test_parse_headers_reply_auto_reply_and_bounce():
    raw = (b"From: Palm Breeze <PalmBreeze@gmail.com>\r\nSubject: Re: website for Palm Breeze Plumbing\r\n"
           b"Date: Tue, 29 Sep 2026 10:15:00 -0500\r\nMessage-ID: <abc@mail.gmail.com>\r\n"
           b"In-Reply-To: <m1@mysender.com>\r\nReferences: <m0@x> <m1@mysender.com>\r\n\r\n")
    message = parse_headers(raw)
    assert message.sender == "palmbreeze@gmail.com" and message.message_id == "<abc@mail.gmail.com>"
    assert message.refs == {"<m0@x>", "<m1@mysender.com>"} and not message.auto and not message.bounce
    assert message.received_at == datetime(2026, 9, 29, 15, 15, tzinfo=UTC)
    auto = parse_headers(b"From: a@b.com\r\nSubject: Out of Office: back Monday\r\nAuto-Submitted: auto-replied\r\n\r\n")
    assert auto.auto and not auto.bounce
    bounce = parse_headers(b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
                           b"Subject: Delivery Status Notification (Failure)\r\n"
                           b"X-Failed-Recipients: palmbreeze@gmail.com\r\n\r\n")
    assert bounce.bounce and bounce.failed == ["palmbreeze@gmail.com"]
    delay = parse_headers(b"From: mailer-daemon@googlemail.com\r\nSubject: Delivery Status Notification (Delay)\r\n\r\n")
    assert not delay.bounce


def test_classify():
    addresses = {"palmbreeze@gmail.com": "L1", "lia@gmail.com": "L2"}
    ids = {"<m1@mysender.com>": "L1"}
    assert classify(reply("palmbreeze@gmail.com", "Re: website"), addresses=addresses, message_ids=ids) == ("L1", "reply")
    other = reply("owner@home.net", "Re: website", refs=["<m1@mysender.com>"])
    assert classify(other, addresses=addresses, message_ids=ids) == ("L1", "reply")
    assert classify(reply("lia@gmail.com", "unsubscribe"), addresses=addresses, message_ids=ids) == ("L2", "unsubscribe")
    assert classify(reply("lia@gmail.com", "Auto: away", auto=True), addresses=addresses,
                    message_ids=ids) == ("L2", "auto_reply")
    assert classify(reply("stranger@x.com", "hello"), addresses=addresses, message_ids=ids) is None
    assert classify(reply("me@mysender.com", "Re: website", refs=["<m1@mysender.com>"]), addresses=addresses,
                    message_ids=ids, own_address="me@mysender.com") is None
    bounce = MailboxMessage(sender="postmaster@outlook.com", bounce=True, text="Delivery to lia@gmail.com failed")
    assert classify(bounce, addresses=addresses, message_ids=ids) == ("L2", "bounce")


def test_imap_date_and_passwords(tmp_path):
    assert imap_date(date(2026, 9, 3)) == "03-Sep-2026"
    assert mail_password(make_settings(tmp_path, SMTP_HOST="smtp.gmail.com", MAIL_PASSWORD="abcd efgh ijkl mnop")) \
        == "abcdefghijklmnop"
    assert mail_password(make_settings(tmp_path, SMTP_HOST="smtp.zoho.com", MAIL_PASSWORD="my pass word")) \
        == "my pass word"


def test_mailbox_problems(tmp_path):
    assert mailbox_problems(make_settings(tmp_path)) == []
    problems = " | ".join(mailbox_problems(make_settings(tmp_path, SMTP_HOST="", IMAP_HOST="", SENDER_ADDRESS="")))
    assert "SMTP_HOST" in problems and "IMAP_HOST" in problems and "SENDER_ADDRESS" in problems


# --------------------------------------------------------------------------
# the manager: campaigns and the send loop
# --------------------------------------------------------------------------
def started(tmp_path, clock=None, **overrides):
    manager, mailbox = make_manager(tmp_path, clock, **overrides)
    job = plumbers_job()

    async def go():
        campaign = await manager.create(CampaignCreate(job_ids=[job.id], trade="plumbers"), [job])
        await manager.start(campaign)
        return campaign

    return manager, mailbox, run(go()), job


def test_create_campaign_from_business_type(tmp_path):
    manager, _ = make_manager(tmp_path)
    plumbers = next(t for t in manager.list_business_types() if t.name == "Plumber")
    run(manager.update_business_type(plumbers, BusinessTypeUpdate(steps=[
        Step(day=0, subject="hello {Business}", body="Plumbing hello from {SenderName}")])))
    job = plumbers_job()
    job.business_type_id = plumbers.id
    campaign = run(manager.create(CampaignCreate(job_ids=[job.id]), [job]))
    assert campaign.name == "Plumber · Houston" and campaign.trade == "plumbers"
    assert campaign.business_type_name == "Plumber" and len(campaign.steps) == 1
    assert campaign.steps[0].subject == "hello {Business}" and campaign.status == CampaignStatus.DRAFT
    assert len(campaign.leads) == 3 and len(campaign.left_out) == 3
    # the campaign has its own copy of the emails
    run(manager.update_business_type(plumbers, BusinessTypeUpdate(trade="pipe people")))
    assert manager.get(campaign.id).trade == "plumbers"
    # persisted, and read back by a fresh manager
    again = OutreachManager(make_settings(tmp_path), mailbox=FakeMailbox())
    assert again.get(campaign.id).leads[0].email == "palmbreeze@gmail.com"


def test_start_needs_a_mailbox_and_complete_emails(tmp_path):
    manager, _ = make_manager(tmp_path, SENDER_ADDRESS="", IMAP_HOST="")
    job = plumbers_job()
    campaign = run(manager.create(CampaignCreate(job_ids=[job.id]), [job]))
    with pytest.raises(OutreachError) as err:
        run(manager.start(campaign))
    message = str(err.value)
    assert "IMAP_HOST" in message and "SENDER_ADDRESS" in message and "{Trade}" in message
    assert campaign.status == CampaignStatus.DRAFT


def test_first_emails_go_out_in_the_window_with_a_pause(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    run(manager.tick())
    assert mailbox.fetches == 1 and len(mailbox.sent) == 1        # the mailbox is checked before sending
    first = mailbox.sent[0]
    assert first["To"] == "palmbreeze@gmail.com" and first["Subject"] == "website for Palm Breeze Plumbing"
    assert first["From"] == "Pat <me@mysender.com>"
    assert "looking at plumbers in Houston. 4.8 stars from 57 reviews" in first.get_content()
    lead = campaign.leads[0]
    assert lead.status == LeadStatus.SENT and lead.next_due == TUESDAY_9AM + timedelta(days=3)
    run(manager.tick())
    assert len(mailbox.sent) == 1                                   # the pause between two emails
    clock.advance(seconds=61)
    run(manager.tick())
    assert [m["To"] for m in mailbox.sent] == ["palmbreeze@gmail.com", "hampshire@gmail.com"]
    assert mailbox.fetches == 1                                     # the check a minute ago is recent enough
    assert json.loads((tmp_path / "contacted.json").read_text(encoding="utf-8"))["palmbreeze@gmail.com"][
        "status"] == "emailed"


def test_stopping_lets_the_email_being_sent_finish(tmp_path):
    """Stop / Restart / Ctrl+C: an email that is on its way gets written down, so it never goes out twice."""
    class SlowMailbox(FakeMailbox):
        def __init__(self) -> None:
            super().__init__()
            self.sending, self.release = asyncio.Event(), asyncio.Event()

        async def send(self, message) -> None:
            self.sending.set()
            await self.release.wait()
            await super().send(message)

    mailbox = SlowMailbox()
    manager = OutreachManager(make_settings(tmp_path), mailbox=mailbox, clock=Clock(TUESDAY_9AM),
                              rng=random.Random(7))
    job = plumbers_job()

    async def scenario():
        campaign = await manager.create(CampaignCreate(job_ids=[job.id], trade="plumbers"), [job])
        await manager.start(campaign)
        manager.start_loop()
        await asyncio.wait_for(mailbox.sending.wait(), 5)       # the loop is sending the first email
        stopping = asyncio.create_task(manager.stop_loop())
        await asyncio.sleep(0.05)
        assert not stopping.done()                               # waiting for it
        mailbox.release.set()
        await asyncio.wait_for(stopping, 5)
        return campaign

    campaign = run(scenario())
    assert len(mailbox.sent) == 1 and len(campaign.leads[0].sent) == 1 and manager._task is None
    again = OutreachManager(make_settings(tmp_path), mailbox=FakeMailbox())      # as after a restart
    assert len(again.get(campaign.id).leads[0].sent) == 1


def test_nothing_goes_out_outside_the_window(tmp_path):
    clock = Clock(TUESDAY_9AM + timedelta(hours=3))                 # 12:00 in Houston
    manager, mailbox, _, _ = started(tmp_path, clock)
    run(manager.tick())
    assert mailbox.sent == []
    clock.now = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)            # Saturday 10:00
    run(manager.tick())
    assert mailbox.sent == []
    status = manager.status()
    assert status["next_send_at"] == datetime(2026, 10, 5, 13, 0, tzinfo=UTC).isoformat()


def test_daily_limit(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, _, _ = started(tmp_path, clock, OUTREACH_DAILY_LIMIT=2)
    for _ in range(4):
        run(manager.tick())
        clock.advance(seconds=61)
    assert len(mailbox.sent) == 2 and manager.status()["sent_today"] == 2


def test_follow_ups_thread_and_a_reply_stops_them(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    for _ in range(3):                                              # the three first emails
        run(manager.tick())
        clock.advance(seconds=61)
    assert len(mailbox.sent) == 3
    first_id = mailbox.sent[0]["Message-ID"]
    clock.now = TUESDAY_9AM + timedelta(days=3, minutes=5)          # Friday: the first follow-up is due
    run(manager.tick())
    follow_up = mailbox.sent[3]
    assert follow_up["To"] == "palmbreeze@gmail.com" and follow_up["Subject"] == "Re: website for Palm Breeze Plumbing"
    assert follow_up["In-Reply-To"] == first_id and follow_up["References"] == first_id
    # Palm Breeze answers - no third email for them
    mailbox.inbox = [reply("palmbreeze@gmail.com", "Re: website for Palm Breeze Plumbing")]
    clock.advance(minutes=11)
    run(manager.tick())
    lead = campaign.leads[0]
    assert lead.status == LeadStatus.REPLIED and lead.next_due is None
    assert lead.inbound[0].kind == "reply" and lead.note == "Re: website for Palm Breeze Plumbing"
    clock.now = TUESDAY_9AM + timedelta(days=7, minutes=30)         # next Tuesday: third emails are due
    for _ in range(4):
        run(manager.tick())
        clock.advance(seconds=61)
    assert [m["To"] for m in mailbox.sent[4:]].count("palmbreeze@gmail.com") == 0


def test_bounce_unsubscribe_and_auto_reply(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    for _ in range(3):
        run(manager.tick())
        clock.advance(seconds=61)
    mailbox.inbox = [
        MailboxMessage(sender="mailer-daemon@googlemail.com", bounce=True, failed=["hampshire@gmail.com"],
                       message_id="<b1@x>"),
        reply("lia@gmail.com", "unsubscribe", message_id="<u1@x>"),
        reply("palmbreeze@gmail.com", "Out of office", auto=True, message_id="<a1@x>"),
    ]
    run(manager.check_now())
    palm, hampshire, lia = campaign.leads
    assert hampshire.status == LeadStatus.BOUNCED and lia.status == LeadStatus.UNSUBSCRIBED
    assert palm.status == LeadStatus.SENT and palm.inbound[0].kind == "auto_reply"
    run(manager.check_now())                                        # the same emails again: nothing twice
    assert len(palm.inbound) == 1


def test_campaign_finishes_after_the_last_email(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    run(manager.update(campaign, CampaignUpdate(steps=[Step(day=0, subject="hi {Business}", body="one"),
                                                       Step(day=2, body="two")])))
    for day in (0, 2):
        clock.now = TUESDAY_9AM + timedelta(days=day)
        for _ in range(3):
            run(manager.tick())
            clock.advance(seconds=61)
    assert len(mailbox.sent) == 6
    assert campaign.status == CampaignStatus.FINISHED
    assert all(lead.status == LeadStatus.DONE for lead in campaign.leads)


def test_mailbox_login_refused_pauses_the_campaigns(tmp_path):
    manager, mailbox, campaign, _ = started(tmp_path)
    mailbox.send_error = MailAuthError("the mailbox refused the login (535 bad password)")
    run(manager.tick())
    assert campaign.status == CampaignStatus.PAUSED and "535 bad password" in campaign.error
    assert campaign.leads[0].status == LeadStatus.QUEUED


def test_refused_address_fails_that_lead_only(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    mailbox.send_error = RecipientRefused("the address was refused: 550 no such user")
    run(manager.tick())
    mailbox.send_error = None
    clock.advance(seconds=61)
    run(manager.tick())
    assert campaign.leads[0].status == LeadStatus.FAILED and "550" in campaign.leads[0].note
    assert mailbox.sent[0]["To"] == "hampshire@gmail.com" and campaign.status == CampaignStatus.RUNNING


def test_no_email_while_replies_cant_be_checked(tmp_path):
    manager, mailbox, _, _ = started(tmp_path)
    mailbox.fetch_error = MailError("could not reach imap.test:993")
    run(manager.tick())
    assert mailbox.sent == [] and "imap.test" in manager.status()["last_check"]["error"]


def test_nobody_gets_the_first_email_twice(tmp_path):
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, job = started(tmp_path, clock)
    run(manager.tick())                                             # Palm Breeze is emailed
    second = run(manager.create(CampaignCreate(job_ids=[job.id], trade="plumbers"), [job]))
    assert second.leads == []
    reasons = {o.name: o.reason for o in second.left_out}
    assert reasons["Palm Breeze Plumbing LLC"] == "emailed on 29 Sep 2026 (campaign “plumber · Houston”)"
    assert reasons["Hampshire Plumbing"] == "already in campaign “plumber · Houston”"
    run(manager.pause(campaign))
    run(manager.delete(campaign.id))
    third = run(manager.create(CampaignCreate(job_ids=[job.id], trade="plumbers"), [job]))
    assert [lead.business for lead in third.leads] == ["Hampshire Plumbing", "Lia Constructions"]


def test_delete_needs_a_pause_and_leads_can_be_skipped(tmp_path):
    manager, mailbox, campaign, _ = started(tmp_path)
    with pytest.raises(OutreachError) as err:
        run(manager.delete(campaign.id))
    assert err.value.status == 409
    palm = campaign.leads[0]
    run(manager.update_lead(campaign, palm.id, LeadUpdate(skipped=True, business="Palm Breeze")))
    assert palm.status == LeadStatus.SKIPPED and palm.business == "Palm Breeze"
    run(manager.tick())
    assert mailbox.sent[0]["To"] == "hampshire@gmail.com"
    run(manager.update_lead(campaign, palm.id, LeadUpdate(skipped=False)))
    assert palm.status == LeadStatus.QUEUED


def test_changing_the_days_moves_the_next_follow_up(tmp_path):
    manager, mailbox, campaign, _ = started(tmp_path)
    run(manager.tick())
    steps = [s.model_copy() for s in campaign.steps]
    steps[1].day = 5
    steps[2].day = 9
    run(manager.update(campaign, CampaignUpdate(steps=steps)))
    assert campaign.leads[0].next_due == TUESDAY_9AM + timedelta(days=5)


def test_send_test_goes_to_yourself(tmp_path):
    manager, mailbox, campaign, _ = started(tmp_path)
    result = run(manager.send_test(campaign, SendTestRequest(step=2)))
    assert result["to"] == "me@mysender.com" and mailbox.sent[0]["To"] == "me@mysender.com"
    assert mailbox.sent[0]["Subject"] == "[Test] Re: website for Palm Breeze Plumbing"
    assert campaign.leads[0].status == LeadStatus.QUEUED           # a test changes nothing


def test_business_types(tmp_path):
    manager, _ = make_manager(tmp_path)
    names = [t.name for t in manager.list_business_types()]
    assert "Roofer" in names and "Plumber" in names
    assert not (tmp_path / "business_types.json").exists()          # the defaults aren't written until changed
    created = run(manager.create_business_type(BusinessTypeCreate(name="Dog groomers", search="dog groomer",
                                                                  trade="dog groomers")))
    with pytest.raises(OutreachError) as err:
        run(manager.create_business_type(BusinessTypeCreate(name="dog GROOMERS")))
    assert err.value.status == 409
    with pytest.raises(OutreachError, match="unknown placeholder"):
        run(manager.update_business_type(created, BusinessTypeUpdate(steps=[Step(day=0, subject="x", body="{Nope}")])))
    run(manager.delete_business_type(next(t.id for t in manager.list_business_types() if t.name == "Mover")))
    again = OutreachManager(make_settings(tmp_path), mailbox=FakeMailbox())
    names = [t.name for t in again.list_business_types()]
    assert "Dog groomers" in names and "Mover" not in names
    preview = again.preview_business_type(again.get_business_type(created.id))
    with_stars, few = preview["samples"]
    assert "4.8 stars from 57 reviews" in with_stars["emails"][0]["body"]
    assert "There's no website on your Google listing" in few["emails"][0]["body"]


def test_two_versions_take_turns_and_each_keeps_its_own_emails(tmp_path):
    from app.outreach.manager import results_csv
    clock = Clock(TUESDAY_9AM)
    manager, mailbox, campaign, _ = started(tmp_path, clock)
    run(manager.update(campaign, CampaignUpdate(steps_b=[
        Step(day=0, subject="hello {Business}", body="Version B, no link yet"),
        Step(day=2, body="B follow-up")])))
    for _ in range(3):                                              # the three first emails
        run(manager.tick())
        clock.advance(seconds=61)
    assert [lead.variant for lead in campaign.leads] == ["A", "B", "A"]
    assert mailbox.sent[0]["Subject"] == "website for Palm Breeze Plumbing"
    assert mailbox.sent[1]["Subject"].startswith("hello ") and "Version B, no link yet" in mailbox.sent[1].get_content()
    a, b = campaign.leads[0], campaign.leads[1]
    assert a.next_due == a.sent[0].sent_at + timedelta(days=3)     # A's follow-up on day 3
    assert b.next_due == b.sent[0].sent_at + timedelta(days=2)     # B's on day 2
    clock.now = b.next_due + timedelta(minutes=5)
    run(manager.tick())
    assert mailbox.sent[3]["Subject"].startswith("Re: hello ") and "B follow-up" in mailbox.sent[3].get_content()
    assert b.status == LeadStatus.DONE                              # B has two emails, not three
    rows = results_csv(campaign, None).decode("utf-8-sig").splitlines()
    assert rows[0].endswith(",Version") and rows[2].endswith(",B") and "2 of 2" in rows[2]
    # a version B that can't be sent stops the change
    with pytest.raises(OutreachError, match="version B"):
        run(manager.update(campaign, CampaignUpdate(steps_b=[Step(day=2, subject="x", body="y")])))
