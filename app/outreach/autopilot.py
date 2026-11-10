"""Autopilot: finds businesses and starts their campaigns by itself (docs/AUTOPILOT.md).

It works through a list of places for one business type. Every ``TICK_S`` it looks whether the
running campaigns are about to run out of businesses to email - fewer than ``min_waiting`` waiting
for their first email - and if so searches the next place on the list: the whole area, businesses
without a website that have an email. When the search finishes it makes the campaign
("Roofer · Katy, TX", the business type's emails, a website for each business) and starts it.

It holds back:

* at most ``searches_per_day`` searches a day (the send window's time zone), one at a time;
* while a campaign was paused because of the mailbox (a refused login …) or the mailbox settings
  have a problem;
* and it stops itself - pausing the running campaigns too - when ``BOUNCE_LIMIT`` of the last
  ``BOUNCE_WINDOW`` businesses emailed since it was switched on bounced, or Google blocked a
  search. Switching it on again starts the bounce count afresh.

Its settings and runs are ``data/autopilot.json``; what it did goes into the activity log.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.config import Settings
from app.filters import ContactFilter, SearchFilters, WebsiteFilter
from app.jobs import ACTIVE_STATES, JobManager
from app.models import Job, JobStatus, build_query
from app.outreach.activity import ActivityLog
from app.outreach.manager import OutreachError, OutreachManager
from app.outreach.models import CampaignCreate, CampaignStatus, LeadStatus

log = logging.getLogger(__name__)

TICK_S = 60
BOUNCE_WINDOW = 20
BOUNCE_LIMIT = 2
RUNS_KEPT = 200
#: a search that ended this way is searched again (e.g. the browser didn't survive the laptop sleeping) - until
#: FAILS_BEFORE_SKIP of that place's searches in a row failed: then the next place. A cancelled search was
#: stopped on purpose.
RETRIED = {JobStatus.FAILED, JobStatus.INTERRUPTED}
FAILS_BEFORE_SKIP = 3

#: Texas first: Houston's suburbs, Dallas–Fort Worth, San Antonio, Austin
DEFAULT_PLACES = [
    "Katy, TX", "Sugar Land, TX", "Pearland, TX", "The Woodlands, TX", "Spring, TX", "Cypress, TX",
    "League City, TX", "Pasadena, TX",
    "Dallas, TX", "Fort Worth, TX", "Arlington, TX", "Plano, TX", "Frisco, TX", "McKinney, TX", "Denton, TX",
    "San Antonio, TX", "New Braunfels, TX", "Austin, TX", "Round Rock, TX", "Georgetown, TX",
]


def search_filters() -> SearchFilters:
    """No website (a Facebook page doesn't count, nor one only found on the web), has an email."""
    return SearchFilters(website=WebsiteFilter.WITHOUT, contact=ContactFilter.EMAIL, profiles_as_no_website=True,
                         search_web=True)


class AutopilotRun(BaseModel):
    place: str
    job_id: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    found: int = 0                       # businesses the search found
    campaign_id: str = ""
    emailing: int = 0                    # of them, in the campaign
    note: str = ""
    failed: bool = False                 # ended with an error (RETRIED): searched again once


class AutopilotState(BaseModel):
    enabled: bool = False
    business_type_id: str = ""
    places: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACES))
    next_place: int = 0                  # index into places
    min_waiting: int = 10
    searches_per_day: int = 2
    limit: int = 60                      # businesses wanted per search
    stopped: str = ""                    # why it stopped itself ('' = it didn't)
    on_since: datetime | None = None     # bounces before this don't count
    runs: list[AutopilotRun] = Field(default_factory=list)


class AutopilotUpdate(BaseModel):
    enabled: bool | None = None
    business_type_id: str | None = Field(None, max_length=40)
    places: list[str] | None = Field(None, max_length=500, description="Searched in this order, e.g. 'Katy, TX'")
    next_place: int | None = Field(None, ge=0, description="Index of the next place to search")
    min_waiting: int | None = Field(None, ge=1, le=500)
    searches_per_day: int | None = Field(None, ge=1, le=10)
    limit: int | None = Field(None, ge=1, le=500)


class Autopilot:
    def __init__(self, cfg: Settings, jobs: JobManager, outreach: OutreachManager, activity: ActivityLog, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.jobs = jobs
        self.outreach = outreach
        self.activity = activity
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = self._load()
        self.waiting_for = ""            # why it isn't searching right now (for the panel)
        self._task: asyncio.Task | None = None
        self._busy = asyncio.Lock()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @property
    def _file(self) -> Path:
        return self.cfg.DATA_DIR / "autopilot.json"

    def _load(self) -> AutopilotState:
        if self._file.is_file():
            try:
                return AutopilotState.model_validate_json(self._file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("unreadable %s (%s) - the autopilot starts switched off", self._file, exc)
        state = AutopilotState()
        roofer = next((t for t in self.outreach.business_types.values() if t.name.lower() == "roofer"), None)
        state.business_type_id = roofer.id if roofer else ""
        return state

    def _save(self) -> None:
        self.state.runs = self.state.runs[-RUNS_KEPT:]
        self._file.parent.mkdir(parents=True, exist_ok=True)
        partial = self._file.with_suffix(".json.part")
        partial.write_text(self.state.model_dump_json(indent=1), encoding="utf-8")
        partial.replace(self._file)

    def update(self, req: AutopilotUpdate) -> dict:
        state = self.state
        if req.business_type_id is not None:
            if req.business_type_id not in self.outreach.business_types:
                raise OutreachError("business type not found", 404)
            state.business_type_id = req.business_type_id
        if req.places is not None:
            places = [" ".join(p.split()) for p in req.places if p.strip()]
            if places != state.places:               # the next place stays next where it is still listed
                upcoming = state.places[state.next_place] if state.next_place < len(state.places) else ""
                state.next_place = places.index(upcoming) if upcoming in places else state.next_place
                state.places = places
        for field in ("next_place", "min_waiting", "searches_per_day", "limit"):
            value = getattr(req, field)
            if value is not None:
                setattr(state, field, value)
        state.next_place = min(state.next_place, len(state.places))
        # a stopped autopilot is still ``enabled``, so switching it on has to count as a change
        if req.enabled is not None and (req.enabled != state.enabled or (req.enabled and state.stopped)):
            state.enabled = req.enabled
            if req.enabled:
                if not state.business_type_id:
                    state.enabled = False
                    raise OutreachError("pick the business type the autopilot looks for")
                state.stopped, state.on_since = "", self.clock()
            self.activity.add("Autopilot on" if state.enabled else "Autopilot off", "by you")
            log.info("Autopilot switched %s", "on" if state.enabled else "off")
        self._save()
        return self.status()

    # ------------------------------------------------------------------
    # What the panel shows
    # ------------------------------------------------------------------
    def status(self) -> dict:
        state = self.state
        business_type = self.outreach.business_types.get(state.business_type_id)
        running = self._running_run()
        return {
            **state.model_dump(mode="json", exclude={"runs"}),
            "business_type_name": business_type.name if business_type else "",
            "next": state.places[state.next_place] if state.next_place < len(state.places) else "",
            "waiting": self.waiting(),
            "searches_today": self.searches_today(self.clock()),
            "searching": running.place if running else "",
            "searching_job_id": running.job_id if running else "",
            "holding": self.waiting_for,
            "runs": [run.model_dump(mode="json") for run in reversed(state.runs[-30:])],
        }

    def waiting(self) -> int:
        """Businesses of running campaigns still waiting for their first email."""
        return sum(lead.status == LeadStatus.QUEUED for c in self.outreach.campaigns.values()
                   if c.status == CampaignStatus.RUNNING for lead in c.leads)

    def searches_today(self, now: datetime) -> int:
        zone = self._zone()
        today = now.astimezone(zone).date()
        return sum(run.started_at.astimezone(zone).date() == today for run in self.state.runs)

    def _zone(self) -> ZoneInfo | timezone:
        try:
            return ZoneInfo(self.cfg.OUTREACH_TIMEZONE)
        except Exception:  # noqa: BLE001 - the send window reports a bad time zone
            return timezone.utc

    def _running_run(self) -> AutopilotRun | None:
        for run in reversed(self.state.runs):
            if run.finished_at is None:
                job = self.jobs.get(run.job_id)
                if job is not None and job.status in ACTIVE_STATES:
                    return run
        return None

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    def start_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="autopilot")

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Autopilot: unexpected error")
            await asyncio.sleep(TICK_S)

    async def tick(self) -> None:
        async with self._busy:
            self.waiting_for = await self._tick(self.clock())

    async def _tick(self, now: datetime) -> str:
        """One look; returns why it didn't search ('' when it did)."""
        state = self.state
        self._close_lost_runs(now)
        if not state.enabled:
            return "switched off"
        if state.stopped:
            return state.stopped
        bounced = self._recent_bounces()
        if bounced >= BOUNCE_LIMIT:
            self._stop(f"{bounced} of the last {BOUNCE_WINDOW} businesses emailed bounced - check the addresses "
                       "and the mailbox, then switch the autopilot on again", pause_campaigns=True)
            return state.stopped
        if self._running_run():
            return "a search is running"
        problems = self.outreach.problems()
        if problems:
            return "the mailbox settings: " + " · ".join(problems)
        paused = [c for c in self.outreach.campaigns.values() if c.status == CampaignStatus.PAUSED and c.error]
        if paused:
            return f"campaign “{paused[0].name}” was paused: {paused[0].error}"
        waiting = self.waiting()
        if waiting >= state.min_waiting:
            return f"{waiting} businesses are waiting for their first email"
        if self.searches_today(now) >= state.searches_per_day:
            return f"{state.searches_per_day} search(es) today already"
        business_type = self.outreach.business_types.get(state.business_type_id)
        if business_type is None:
            self._stop("its business type was deleted - pick one")
            return state.stopped
        if state.next_place >= len(state.places):
            self._stop("every place on the list has been searched - add more places")
            return state.stopped
        place = state.places[state.next_place]
        state.next_place += 1
        job = self.jobs.create_scrape(build_query(business_type.search or business_type.name, place), state.limit,
                                      business=business_type.search or business_type.name, place=place,
                                      whole_area=True, filters=search_filters(), business_type_id=business_type.id)
        state.runs.append(AutopilotRun(place=place, job_id=job.id, started_at=now))
        self._save()
        self.activity.add("Search started", f"{job.query} (whole area, no website, has an email)")
        log.info("Autopilot: searching %r (%d businesses waiting)", job.query, waiting)
        return ""

    def _close_lost_runs(self, now: datetime) -> None:
        """A search that stopped without telling us (the panel restarted mid-search): search it again."""
        changed = False
        for run in self.state.runs:
            if run.finished_at is not None:
                continue
            job = self.jobs.get(run.job_id)
            if job is not None and job.status in ACTIVE_STATES:
                continue
            if job is not None and job.status != JobStatus.INTERRUPTED and job.finished_at is not None:
                continue                                 # finished; job_finished is on its way
            run.finished_at, run.note = now, "the search didn't finish (the panel stopped?) - it is searched again"
            if run.place in self.state.places:
                self.state.next_place = min(self.state.next_place, self.state.places.index(run.place))
            changed = True
        if changed:
            self._save()

    def _requeue(self, run: AutopilotRun) -> bool:
        """Put a failed search's place first in line again - unless FAILS_BEFORE_SKIP in a row have failed."""
        run.failed = True
        fails = 0
        for earlier in reversed([r for r in self.state.runs if r.place == run.place]):
            if not earlier.failed:
                break
            fails += 1
        if fails >= FAILS_BEFORE_SKIP or run.place not in self.state.places:
            return False
        self.state.next_place = min(self.state.next_place, self.state.places.index(run.place))
        return True

    def _recent_bounces(self) -> int:
        since = self.state.on_since
        emailed = [lead for c in self.outreach.campaigns.values() for lead in c.leads
                   if lead.sent and (since is None or lead.sent[0].sent_at >= since)]
        emailed.sort(key=lambda lead: lead.sent[0].sent_at, reverse=True)
        return sum(lead.status == LeadStatus.BOUNCED for lead in emailed[:BOUNCE_WINDOW])

    def _stop(self, reason: str, *, pause_campaigns: bool = False) -> None:
        self.state.stopped = reason
        self._save()
        paused = self.outreach.pause_running(f"Paused by the autopilot: {reason}") if pause_campaigns else 0
        self.activity.add("Autopilot stopped", reason + (f" ({paused} campaign(s) paused)" if paused else ""))
        log.warning("Autopilot stopped: %s", reason)

    # ------------------------------------------------------------------
    # A search finished
    # ------------------------------------------------------------------
    async def job_finished(self, job: Job) -> None:
        """Called for every finished job (main.py); acts on the autopilot's own."""
        run = next((r for r in self.state.runs if r.job_id == job.id and r.finished_at is None), None)
        if run is None:
            return
        async with self._busy:
            await self._finished(run, job)

    async def _finished(self, run: AutopilotRun, job: Job) -> None:
        run.finished_at, run.found = self.clock(), len(job.rows)
        if job.status == JobStatus.BLOCKED:
            run.note = "Google blocked the search"
            self._save()
            self.activity.add("Search blocked", f"{job.query}: {job.error or 'Google showed its captcha'}")
            self._stop("Google blocked a search (its “unusual traffic” check) - wait a day, then switch it on again")
            return
        if job.status != JobStatus.COMPLETED:
            run.note = f"the search {job.status.value}" + (f": {job.error}" if job.error else "")
            if job.status in RETRIED and self._requeue(run):
                run.note += " - it is searched again"
            self._save()
            self.activity.add("Search stopped", f"{job.query}: {run.note}")
            return
        self.activity.add("Search finished", f"{job.query}: {len(job.rows)} business(es) without a website found")
        if not job.rows:
            run.note = "nothing found"
            self._save()
            return
        business_type = self.outreach.business_types.get(job.business_type_id)
        try:
            campaign = await self.outreach.create(
                CampaignCreate(job_ids=[job.id], business_type_id=job.business_type_id,
                               name=f"{business_type.name if business_type else 'Businesses'} · {run.place}"), [job])
        except OutreachError as exc:
            run.note = f"no campaign: {exc}"
            self._save()
            self.activity.add("Campaign not made", f"{job.query}: {exc}")
            return
        run.campaign_id = campaign.id
        run.emailing = sum(lead.status == LeadStatus.QUEUED for lead in campaign.leads)
        if not run.emailing:
            run.note = "every business was left out (emailed before, no address of its own …)"
            self._save()
            return
        try:
            await self.outreach.start(campaign)
            run.note = "started"
        except OutreachError as exc:
            run.note = f"not started: {exc}"
            self.activity.add("Campaign not started", str(exc), campaign=campaign.name)
        self._save()
