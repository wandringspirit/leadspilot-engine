"""The autopilot (searches the next place and starts its campaign), the activity log and the master sheet
(app/outreach/autopilot.py, activity.py, master.py, docs/AUTOPILOT.md). A fake job manager: no real search runs."""
from __future__ import annotations

import csv
import io
import random
from datetime import timedelta

import pytest

from app.models import Job, JobStatus
from app.outreach.activity import ActivityLog
from app.outreach.autopilot import BOUNCE_LIMIT, Autopilot, AutopilotUpdate
from app.outreach.manager import OutreachError, OutreachManager
from app.outreach.master import activity_csv, campaigns_csv
from app.outreach.models import CampaignStatus, LeadStatus, LeftOut
from tests.test_outreach import TUESDAY_9AM, Clock, FakeMailbox, make_settings, plumbers_job


class FakeJobs:
    """Takes the place of JobManager: create_scrape makes a queued job; the test finishes it."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def create_scrape(self, query, limit, *, business="", place="", whole_area=False, filters=None, enrich=False,
                      headless=None, business_type_id="") -> Job:
        job = Job(query=query, business=business, place=place, whole_area=whole_area, limit=limit,
                  filters=filters, business_type_id=business_type_id, status=JobStatus.QUEUED)
        self.jobs[job.id] = job
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)


def make_autopilot(tmp_path, **overrides):
    clock = Clock(TUESDAY_9AM)
    cfg = make_settings(tmp_path, **overrides)
    activity = ActivityLog(tmp_path / "activity.jsonl", clock=clock)
    outreach = OutreachManager(cfg, mailbox=FakeMailbox(), clock=clock, rng=random.Random(7), activity=activity)
    jobs = FakeJobs()
    pilot = Autopilot(cfg, jobs, outreach, activity, clock=clock)
    plumber = next(t for t in outreach.list_business_types() if t.name == "Plumber")
    pilot.update(AutopilotUpdate(business_type_id=plumber.id, places=["Katy, TX", "Pearland, TX"], enabled=True))
    return pilot, outreach, jobs, activity, clock


async def finish(pilot: Autopilot, jobs: FakeJobs, **kwargs) -> Job:
    """The running search finishes with the plumbers of tests/test_outreach.py."""
    job = jobs.get(pilot.state.runs[-1].job_id)
    job.rows = plumbers_job().rows
    job.status = kwargs.get("status", JobStatus.COMPLETED)
    job.finished_at = pilot.clock()
    await pilot.job_finished(job)
    return job


async def test_it_searches_the_next_place_and_starts_its_campaign(tmp_path):
    pilot, outreach, jobs, activity, _ = make_autopilot(tmp_path)
    assert pilot.state.business_type_id and pilot.state.enabled
    assert not pilot.state.runs                    # nothing yet: update() doesn't search
    await pilot.tick()
    run = pilot.state.runs[-1]
    job = jobs.get(run.job_id)
    assert (job.query, job.whole_area, job.filters.website.value, job.filters.contact.value) == \
        ("plumber in Katy, TX", True, "without", "email")
    assert pilot.status()["searching"] == "Katy, TX" and pilot.status()["next"] == "Pearland, TX"
    await pilot.tick()
    assert len(pilot.state.runs) == 1 and pilot.waiting_for == "a search is running"

    await finish(pilot, jobs)
    campaign = outreach.get(run.campaign_id)
    assert campaign.name == "Plumber · Katy, TX" and campaign.status == CampaignStatus.RUNNING
    assert run.emailing == sum(lead.status == LeadStatus.QUEUED for lead in campaign.leads) > 0
    assert [e["what"] for e in activity.entries()][:4] == \
        ["Campaign started", "Campaign created", "Search finished", "Search started"]

    # enough businesses waiting now (min 2): no new search
    pilot.update(AutopilotUpdate(min_waiting=2))
    await pilot.tick()
    assert len(pilot.state.runs) == 1 and "waiting for their first email" in pilot.waiting_for


async def test_it_holds_to_the_searches_a_day(tmp_path):
    pilot, _, jobs, _, clock = make_autopilot(tmp_path)
    pilot.update(AutopilotUpdate(searches_per_day=1))
    await pilot.tick()
    job = jobs.get(pilot.state.runs[-1].job_id)
    job.status, job.finished_at = JobStatus.COMPLETED, clock()     # found nothing
    await pilot.job_finished(job)
    await pilot.tick()
    assert len(pilot.state.runs) == 1 and "today already" in pilot.waiting_for
    clock.advance(days=1)
    await pilot.tick()
    assert [r.place for r in pilot.state.runs] == ["Katy, TX", "Pearland, TX"]


async def test_it_stops_when_emails_bounce_and_pauses_the_campaigns(tmp_path):
    pilot, outreach, jobs, activity, clock = make_autopilot(tmp_path)
    await pilot.tick()
    await finish(pilot, jobs)
    campaign = outreach.get(pilot.state.runs[-1].campaign_id)
    for lead in campaign.leads[:BOUNCE_LIMIT]:
        await outreach._send(campaign, lead, clock())
        lead.status = LeadStatus.BOUNCED
    await pilot.tick()
    assert "bounced" in pilot.state.stopped and campaign.status == CampaignStatus.PAUSED
    assert activity.entries()[0]["what"] == "Autopilot stopped"
    # saving its settings doesn't clear the stop
    pilot.update(AutopilotUpdate(min_waiting=5))
    assert "bounced" in pilot.state.stopped
    # switched on again (the panel's button sends just this): the old bounces don't count
    clock.advance(minutes=1)
    pilot.update(AutopilotUpdate(enabled=True))
    assert pilot.state.stopped == "" and pilot.state.enabled and pilot._recent_bounces() == 0
    assert pilot.state.on_since == clock() and activity.entries()[0]["what"] == "Autopilot on"
    # asked again while it is on: nothing changes, so the bounce count doesn't start afresh
    clock.advance(minutes=1)
    pilot.update(AutopilotUpdate(enabled=True))
    assert pilot.state.on_since == clock() - timedelta(minutes=1)


async def test_a_blocked_search_stops_it_and_a_lost_one_is_searched_again(tmp_path):
    pilot, _, jobs, _, clock = make_autopilot(tmp_path)
    await pilot.tick()
    lost = jobs.get(pilot.state.runs[-1].job_id)
    lost.status = JobStatus.INTERRUPTED                 # the panel restarted mid-search
    clock.advance(minutes=1)
    await pilot.tick()
    assert [r.place for r in pilot.state.runs] == ["Katy, TX", "Katy, TX"]
    await finish(pilot, jobs, status=JobStatus.BLOCKED)
    assert "Google blocked" in pilot.state.stopped


def test_editing_the_places_keeps_the_next_one(tmp_path):
    pilot, *_ = make_autopilot(tmp_path)
    pilot.state.next_place = 1                          # Pearland next
    pilot.update(AutopilotUpdate(places=["Katy, TX", "Spring, TX", "Pearland, TX"]))
    assert pilot.status()["next"] == "Pearland, TX"
    with pytest.raises(OutreachError):
        pilot.update(AutopilotUpdate(business_type_id="bt_nope"))


async def test_the_master_sheet(tmp_path):
    pilot, outreach, jobs, activity, clock = make_autopilot(tmp_path)
    await pilot.tick()
    await finish(pilot, jobs)
    campaign = outreach.get(pilot.state.runs[-1].campaign_id)
    await outreach._send(campaign, campaign.leads[0], clock())
    columns, total, row = list(csv.reader(io.StringIO(campaigns_csv(outreach.list_campaigns()).decode("utf-8-sig"))))
    assert columns[:3] == ["Campaign", "Business type", "Status"]
    assert total[0] == "All campaigns (1)" and row[0] == "Plumber · Katy, TX" and row[2] == "running"
    emailed, sent = columns.index("Emailed"), columns.index("Emails sent")
    assert total[emailed] == row[emailed] == "1" and total[sent] == "1"
    log = activity_csv(activity.entries()).decode("utf-8-sig").splitlines()
    assert log[0] == "When,What,Campaign,Business,Email,Detail" and ",Email sent," in log[1]
    # the log survives a restart
    assert ActivityLog(tmp_path / "activity.jsonl").entries()[0]["what"] == "Email sent"


async def test_the_daily_table_counts_first_emails_follow_ups_and_what_came_back(tmp_path):
    from datetime import date, datetime, timedelta
    from app.outreach.master import DAILY_COLUMNS, daily_table
    from app.outreach.models import InboundEmail, SentEmail
    pilot, outreach, jobs, activity, clock = make_autopilot(tmp_path)
    await pilot.tick()
    await finish(pilot, jobs)
    campaign = outreach.get(pilot.state.runs[-1].campaign_id)
    noon = datetime(2026, 11, 2, 12).astimezone()                     # a Monday noon, the computer's own time
    a, b = campaign.leads[0], campaign.leads[1]
    a.sent = [SentEmail(step=1, sent_at=noon, message_id="1", subject="s"),
              SentEmail(step=2, sent_at=noon + timedelta(days=3), message_id="2", subject="Re: s")]
    b.sent = [SentEmail(step=1, sent_at=noon, message_id="3", subject="s")]
    a.inbound = [InboundEmail(kind="reply", received_at=noon + timedelta(days=3, hours=1)),
                 InboundEmail(kind="auto_reply", received_at=noon)]          # not counted
    b.inbound = [InboundEmail(kind="bounce", received_at=noon + timedelta(minutes=5))]
    a.site_opened_at = noon + timedelta(hours=1)
    columns, rows = daily_table([campaign], today=date(2026, 11, 6))
    assert columns == DAILY_COLUMNS
    assert [r[0] for r in rows] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    by_day = {r[0]: dict(zip(columns, r)) for r in rows}
    assert by_day["2026-11-02"] | {"Day": ""} == {"Day": "", "First emails": 2, "Follow-ups": 0, "Replies": 0,
                                                 "Websites opened": 1, "Bounces": 1, "Unsubscribes": 0}
    assert by_day["2026-11-05"]["Follow-ups"] == 1 and by_day["2026-11-05"]["Replies"] == 1
    assert sum(by_day["2026-11-03"][c] for c in columns[1:]) == 0          # a day without anything is still a row
    assert daily_table([]) == (DAILY_COLUMNS, [])


async def test_the_master_sheet_says_when_a_search_left_nobody_to_email(tmp_path):
    pilot, outreach, jobs, activity, clock = make_autopilot(tmp_path)
    await pilot.tick()
    await finish(pilot, jobs)
    campaign = outreach.get(pilot.state.runs[-1].campaign_id)
    campaign.status, campaign.left_out = CampaignStatus.DRAFT, [LeftOut(name=lead.business, email=lead.email,
                                                                        reason="emailed before") for lead in campaign.leads]
    campaign.leads = []
    assert campaign.nobody_to_email() and campaign.summary().left_out == len(campaign.left_out) > 0
    row = list(csv.reader(io.StringIO(campaigns_csv([campaign]).decode("utf-8-sig"))))[2]
    assert row[2] == f"nobody to email ({len(campaign.left_out)} left out)"
    campaign.left_out = []                               # a draft you are still setting up
    assert not campaign.nobody_to_email()
    assert list(csv.reader(io.StringIO(campaigns_csv([campaign]).decode("utf-8-sig"))))[2][2] == "draft"
