"""OutreachManager: business types, campaigns and the send loop.

Everything lives in one process, like the job manager: campaigns are JSON files
under ``data/campaigns``, business types are ``data/business_types.json``, and
every address that was ever emailed is kept in ``data/contacted.json`` so no
business gets the first email twice - even after its campaign is deleted.

The send loop (``tick`` every ``TICK_S`` seconds, only with
``OUTREACH_AUTOSEND``) sends at most one email per tick, only inside the send
window, never more than ``OUTREACH_DAILY_LIMIT`` a day, with a random pause of
``OUTREACH_MIN_GAP_S``–``OUTREACH_MAX_GAP_S`` between two emails. Follow-ups
that are due go before first emails. Before an email goes out the mailbox is
checked for replies (at most ``FRESH_CHECK`` earlier); a reply, an unsubscribe
or a bounce stops that business's follow-ups. When the mailbox refuses the
login or the email, every running campaign is paused with the reason.

With preview websites on (``sites``, docs/SITES.md) a new campaign builds every business's website, and
a business's first email waits until its website is online. A business type with a website design starts
with emails that link it (``{Website}``); what they send - the first email, the link, the last email - and
a reply decide how long the website stays online.

Campaigns made, started, paused and finished, every email sent, every reply and bounce go into the
activity log (``activity``, ``app/outreach/activity.py``) - for the panel and the master sheet.

Emails carry no ``List-Unsubscribe`` header unless ``OUTREACH_UNSUBSCRIBE_HEADER`` is on: a one-to-one
email with one has looked like a mailing to Gmail. Every footer says how to opt out by replying.

See docs/OUTREACH.md.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import random
import re
import tempfile
from contextlib import suppress
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import TypeAdapter

from app.config import Settings
from app.models import Job, JobStatus, PlaceRow, split_places
from app.outreach.activity import ActivityLog
from app.outreach.leads import leads_from_jobs
from app.outreach.mailbox import (declined, MailAuthError, MailboxClient, Mailbox, MailError, MailRejected, RecipientRefused,
                                  build_message, classify, imap_problems, mailbox_problems, new_message_id,
                                  sender_address, sender_header, smtp_problems, written_part)
from app.outreach.models import (OPEN_STATUSES, BusinessType, BusinessTypeCreate, BusinessTypeUpdate, Campaign,
                                 CampaignCreate, CampaignStatus, CampaignUpdate, DriveSource, InboundEmail, Lead,
                                 LeadStatus, LeadUpdate, LeftOut, SendTestRequest, SentEmail, Step, default_steps)
from app.outreach.schedule import SendWindow, WindowError, day_label
from app.outreach.sites import SiteService
from app.outreach.visits import Visits, place
from app.outreach.templates import (DEFAULT_BUSINESS_TYPES, DEFAULT_FOOTER, PLACEHOLDERS, SITE_FOOTER, SITE_STEPS,
                                    compose, lead_values, links_site, missing_fields, template_errors, used_fields)

log = logging.getLogger(__name__)

TICK_S = 20                              # how often the loop looks for work
FRESH_CHECK = timedelta(minutes=2)       # an email only goes out after a reply check this recent
WATCH_DAYS = 45                          # replies are looked for this long after the last email either way
ALERT_TEXT_CHARS = 2000                  # how much of a reply its alert quotes
RETRY_AFTER = timedelta(minutes=5)       # pause after a failed send
MAX_FAILURES = 3                         # failed sends in a row before the campaigns are paused
STOP_WAIT_S = 90                         # stopping waits this long at most for the email being sent
LINK_CHECK = timedelta(minutes=2)        # a website opened this soon after its email: a mail server checking it
PERSON_KINDS = frozenset({"seen", "call", "email", "keep"})   # visits only a person makes
TAPS = {"call": "Tapped call", "email": "Tapped email", "keep": "Asked to keep website",
        "quote": "Website form sent"}

_STOPPED_BY = {"reply": LeadStatus.REPLIED, "unsubscribe": LeadStatus.UNSUBSCRIBED, "bounce": LeadStatus.BOUNCED}
_WATCHED = (LeadStatus.SENT, LeadStatus.DONE, LeadStatus.REPLIED)
_BusinessTypes = TypeAdapter(list[BusinessType])


class OutreachError(Exception):
    """A request the manager can't carry out; ``status`` is the HTTP status for the API."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def versions_problems(steps: list[Step], steps_b: list[Step], footer: str) -> list[str]:
    """steps_problems for both versions of the emails (``steps_b`` may be [] - no second version)."""
    problems = steps_problems(steps, footer)
    if steps_b:
        problems += [f"version B: {p}" for p in steps_problems(steps_b, footer) if not p.startswith("footer")
                     and "{SenderAddress}" not in p]
    return problems


def steps_problems(steps: list[Step], footer: str) -> list[str]:
    """What is wrong with a set of emails ([] when they can be sent)."""
    problems: list[str] = []
    for number, step in enumerate(steps, 1):
        label = "the first email" if number == 1 else f"follow-up {number - 1}"
        for part, text in (("subject", step.subject), ("text", step.body)):
            problems += [f"{label} ({part}): {error}" for error in template_errors(text)]
    if steps and steps[0].day != 0:
        problems.append("the first email goes out on day 0")
    for number, (before, step) in enumerate(zip(steps, steps[1:]), 1):
        if step.day <= before.day:
            problems.append(f"follow-up {number} (day {step.day}) must come after day {before.day}")
    if steps and not steps[0].subject.strip():
        problems.append("the first email needs a subject")
    problems += [f"footer: {error}" for error in template_errors(footer)]
    if "SenderAddress" not in used_fields(footer):
        problems.append("the footer needs {SenderAddress} - US law (CAN-SPAM) wants your postal address in "
                        "every email")
    return problems


def _last_contact(lead: Lead) -> datetime:
    """The last email sent to a lead or, once they've answered, the last they wrote if that's later."""
    moments = [lead.sent[-1].sent_at]
    if lead.status == LeadStatus.REPLIED:
        moments += [i.received_at for i in lead.inbound if i.received_at and i.received_at.tzinfo]
    return max(moments)


def _already_noted(lead: Lead, message: MailboxMessage) -> bool:
    if message.message_id:
        return any(i.message_id == message.message_id for i in lead.inbound)
    return any(i.sender == message.sender and i.subject == message.subject and i.received_at == message.received_at
               for i in lead.inbound)


def _alert_text(text: str) -> str:
    return "\n".join(written_part(text))[:ALERT_TEXT_CHARS]


def _alert_body(campaign: Campaign, lead: Lead, inbound: InboundEmail, website: str) -> str:
    details = [("From", inbound.sender), ("Subject", inbound.subject), ("Phone", lead.phone), ("Website", website),
               ("Campaign", campaign.name)]
    return "\n".join([
        f"{lead.business} replied to your email.", "",
        *(["They wrote:", "", inbound.alert_text] if inbound.alert_text else ["Open their email to read it."]), "",
        *[f"{label}: {value}" for label, value in details if value], "",
        "Answer from their own email in your inbox. This alert only went to you.",
    ])


class OutreachManager:
    def __init__(self, cfg: Settings, *, mailbox: MailboxClient | None = None,
                 clock: Callable[[], datetime] | None = None, rng: random.Random | None = None,
                 sites: SiteService | None = None, activity: ActivityLog | None = None,
                 visits: Visits | None = None,
                 check_domain: Callable[[str], Awaitable[str]] | None = None) -> None:
        self.cfg = cfg
        self.check_domain = check_domain
        self.activity = activity                 # None: nothing is written down (tests)
        self.mailbox: MailboxClient = mailbox or Mailbox(cfg)
        self.sites = sites                       # None: no preview websites, first emails don't wait
        self.visits = visits                     # who opened their website - only for the status here
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.Random()
        self.campaigns: dict[str, Campaign] = {}
        self.business_types: dict[str, BusinessType] = {}
        self.contacted: dict[str, dict] = {}     # address -> business, place id, campaign, first email, status
        self.next_send_at: datetime | None = None
        self.last_check: dict = {"at": None, "ok_at": None, "error": "", "emails": 0, "replies": 0, "bounces": 0}
        self._failures = 0
        self._task: asyncio.Task | None = None
        self._sending: asyncio.Event | None = None    # set once the email being sent is written down
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @property
    def _types_file(self) -> Path:
        return self.cfg.DATA_DIR / "business_types.json"

    @property
    def _contacted_file(self) -> Path:
        return self.cfg.DATA_DIR / "contacted.json"

    def _load(self) -> None:
        for file in sorted(self.cfg.campaigns_dir.glob("camp_*.json")) if self.cfg.campaigns_dir.is_dir() else []:
            try:
                campaign = Campaign.model_validate_json(file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping unreadable campaign file %s: %s", file, exc)
                continue
            self.campaigns[campaign.id] = campaign
        types: list[BusinessType] | None = None
        if self._types_file.is_file():
            try:
                types = _BusinessTypes.validate_json(self._types_file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("unreadable %s (%s) - showing the default business types", self._types_file, exc)
        if types is None:
            # a fresh install: the defaults live in memory until the first change is saved
            types = [BusinessType(name=name, search=search, trade=trade, **self._default_emails(name))
                     for name, search, trade in DEFAULT_BUSINESS_TYPES]
        self.business_types = {t.id: t for t in types}
        if self._contacted_file.is_file():
            try:
                self.contacted = json.loads(self._contacted_file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("unreadable %s: %s", self._contacted_file, exc)
        if self.campaigns:
            running = sum(c.status == CampaignStatus.RUNNING for c in self.campaigns.values())
            log.info("loaded %d campaign(s), %d running", len(self.campaigns), running)

    def _default_emails(self, type_name: str) -> dict:
        """The emails a new business type starts with: ones that link its website when it has a design."""
        if self.sites is not None and self.sites.has_design(type_name):
            return {"steps": [Step(**step) for step in SITE_STEPS], "footer": SITE_FOOTER}
        return {"steps": default_steps(), "footer": DEFAULT_FOOTER}

    def _note(self, what: str, detail: str = "", campaign: Campaign | None = None, lead: Lead | None = None) -> None:
        if self.activity is not None:
            self.activity.add(what, detail, campaign=campaign.name if campaign else "",
                              business=lead.business if lead else "", email=lead.email if lead else "")

    def _save(self, campaign: Campaign) -> None:
        campaign.updated_at = self.clock()
        _write(self.cfg.campaigns_dir / f"{campaign.id}.json", campaign.model_dump_json())

    def _save_types(self) -> None:
        types = sorted(self.business_types.values(), key=lambda t: t.name.lower())
        _write(self._types_file, _BusinessTypes.dump_json(types, indent=1).decode("utf-8"))

    def _save_contacted(self) -> None:
        _write(self._contacted_file, json.dumps(self.contacted, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _window(self) -> tuple[SendWindow | None, str]:
        try:
            return SendWindow.parse(self.cfg.OUTREACH_TIMEZONE, self.cfg.OUTREACH_DAYS, self.cfg.OUTREACH_HOURS), ""
        except WindowError as exc:
            return None, str(exc)

    def problems(self) -> list[str]:
        """What keeps campaigns from running ([] when they can)."""
        window, error = self._window()
        return mailbox_problems(self.cfg) + ([error] if error else [])

    def status(self) -> dict:
        now = self.clock()
        cfg = self.cfg
        window, _ = self._window()
        problems = self.problems()
        sent_today = self.sent_today(now)
        running = sum(c.status == CampaignStatus.RUNNING for c in self.campaigns.values())
        next_send = None
        pick = self._next_email(now) or self._next_email(now, any_time=True)
        if window and not problems and cfg.OUTREACH_AUTOSEND and pick is not None:
            at = max(now, self.next_send_at or now)
            lead = pick[1]
            if lead.status == LeadStatus.SENT and lead.next_due:
                at = max(at, lead.next_due)
            if sent_today >= cfg.OUTREACH_DAILY_LIMIT:
                tomorrow = window.local_date(now) + timedelta(days=1)
                at = max(at, datetime.combine(tomorrow, time(0), window.tz).astimezone(timezone.utc))
            next_send = window.next_open(at)
        check = self.last_check
        return {
            "ready": not problems,
            "problems": problems,
            "autosend": cfg.OUTREACH_AUTOSEND,
            "running": running,
            "sender": sender_header(cfg),
            "smtp": f"{cfg.SMTP_HOST}:{cfg.SMTP_PORT}" if cfg.SMTP_HOST else "",
            "imap": f"{cfg.IMAP_HOST}:{cfg.IMAP_PORT}" if cfg.IMAP_HOST else "",
            "window": window.label() if window else "",
            "window_open": bool(window and window.is_open(now)),
            "daily_limit": cfg.OUTREACH_DAILY_LIMIT,
            "sent_today": sent_today,
            "next_send_at": _iso(next_send),
            "last_check": {"at": _iso(check["at"]), "error": check["error"], "emails": check["emails"],
                           "replies": check["replies"], "bounces": check["bounces"]},
            "contacted": len(self.contacted),
            "placeholders": PLACEHOLDERS,
            "min_reviews": cfg.OUTREACH_MIN_REVIEWS,
            "min_rating": cfg.OUTREACH_MIN_RATING,
            "sites": self.sites.status() if self.sites else None,
            "visits": self.visits.status() if self.visits else None,
            "test_copy_to": test_copy_addresses(cfg),
        }

    def sent_today(self, now: datetime) -> int:
        window, _ = self._window()
        today = window.local_date(now) if window else now.date()
        day = (lambda moment: window.local_date(moment)) if window else (lambda moment: moment.date())
        return sum(1 for c in self.campaigns.values() for lead in c.leads for s in lead.sent if day(s.sent_at) == today)

    # ------------------------------------------------------------------
    # Business types
    # ------------------------------------------------------------------
    def list_business_types(self) -> list[BusinessType]:
        return sorted(self.business_types.values(), key=lambda t: t.name.lower())

    def get_business_type(self, type_id: str) -> BusinessType:
        found = self.business_types.get(type_id)
        if not found:
            raise OutreachError("business type not found", 404)
        return found

    async def create_business_type(self, req: BusinessTypeCreate) -> BusinessType:
        async with self._guard():
            name = " ".join(req.name.split())
            self._unique_name(name)
            defaults = self._default_emails(name)
            steps = req.steps or defaults["steps"]
            footer = defaults["footer"] if req.footer is None else req.footer
            problems = steps_problems(steps, footer)
            if problems:
                raise OutreachError("; ".join(problems))
            created = BusinessType(name=name, search=req.search.strip(), trade=req.trade.strip(),
                                   steps=steps, footer=footer)
            self.business_types[created.id] = created
            self._save_types()
            log.info("Business type %r added", created.name)
            return created

    async def update_business_type(self, business_type: BusinessType, req: BusinessTypeUpdate) -> BusinessType:
        async with self._guard():
            steps = req.steps if req.steps is not None else business_type.steps
            steps_b = req.steps_b if req.steps_b is not None else business_type.steps_b
            footer = req.footer if req.footer is not None else business_type.footer
            problems = versions_problems(steps, steps_b, footer)
            if problems:
                raise OutreachError("; ".join(problems))
            if req.name is not None and req.name.strip().lower() != business_type.name.lower():
                self._unique_name(" ".join(req.name.split()))
            if req.name is not None:
                business_type.name = " ".join(req.name.split())
            if req.search is not None:
                business_type.search = req.search.strip()
            if req.trade is not None:
                business_type.trade = req.trade.strip()
            business_type.steps, business_type.steps_b, business_type.footer = steps, steps_b, footer
            business_type.updated_at = self.clock()
            self._save_types()
            return business_type

    async def delete_business_type(self, type_id: str) -> None:
        async with self._guard():
            if self.business_types.pop(type_id, None) is None:
                raise OutreachError("business type not found", 404)
            self._save_types()

    def _unique_name(self, name: str) -> None:
        if any(t.name.lower() == name.lower() for t in self.business_types.values()):
            raise OutreachError(f"there is already a business type called {name!r}", 409)

    def preview_business_type(self, business_type: BusinessType) -> dict:
        samples = []
        for label, lead in _sample_leads():
            samples.append({"label": label, "to": lead.email, "emails": self._emails(business_type, lead),
                            "values": lead_values(business_type, lead, self.cfg)})
        return {"from": sender_header(self.cfg), "samples": samples}

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------
    def list_campaigns(self) -> list[Campaign]:
        return sorted(self.campaigns.values(), key=lambda c: c.created_at, reverse=True)

    def get(self, campaign_id: str) -> Campaign:
        found = self.campaigns.get(campaign_id)
        if not found:
            raise OutreachError("campaign not found", 404)
        return found

    async def create(self, req: CampaignCreate, jobs: list[Job],
                     files: list[tuple[DriveSource, list[PlaceRow]]] = ()) -> Campaign:
        """A draft campaign for the businesses of ``jobs`` and of lead ``files`` (read from Drive)."""
        async with self._guard():
            type_id = req.business_type_id
            if type_id is None:   # the jobs' business type, when they share one
                ids = {j.business_type_id for j in jobs}
                type_id = ids.pop() if len(ids) == 1 else ""
            business_type = self.business_types.get(type_id) if type_id else None
            if type_id and business_type is None and req.business_type_id:
                raise OutreachError("business type not found", 404)
            file_jobs = [Job(id=f"file_{source.id}", query=source.name, limit=max(1, len(rows)), rows=rows,
                             status=JobStatus.COMPLETED) for source, rows in files]
            leads, left_out = leads_from_jobs([*jobs, *file_jobs], taken=self._taken())
            if self.sites is not None and leads:
                leads, left_out = await self._build_sites(business_type, [*jobs, *file_jobs], leads, left_out)
            campaign = Campaign(
                name=" ".join(req.name.split()) or _default_name(jobs, [s for s, _ in files], business_type),
                trade=req.trade.strip() or (business_type.trade if business_type else ""),
                job_ids=[j.id for j in jobs],
                drive_files=[source for source, _ in files],
                business_type_id=business_type.id if business_type else "",
                business_type_name=business_type.name if business_type else "",
                steps=[s.model_copy() for s in business_type.steps] if business_type else default_steps(),
                steps_b=[s.model_copy() for s in business_type.steps_b] if business_type else [],
                footer=business_type.footer if business_type else DEFAULT_FOOTER,
                sites=self.sites is not None, leads=leads, left_out=left_out,
            )
            self.campaigns[campaign.id] = campaign
            self._save(campaign)
            if self.sites is not None and any(lead.site for lead in leads):
                self.sites.request_upload()
            log.info("Campaign %r created: %d business(es) to email, %d left out", campaign.name, len(leads),
                     len(left_out))
            self._note("Campaign created", f"{len(leads)} business(es) to email, {len(left_out)} left out", campaign)
            return campaign

    async def update(self, campaign: Campaign, req: CampaignUpdate) -> Campaign:
        async with self._guard():
            steps = req.steps if req.steps is not None else campaign.steps
            steps_b = req.steps_b if req.steps_b is not None else campaign.steps_b
            footer = req.footer if req.footer is not None else campaign.footer
            problems = versions_problems(steps, steps_b, footer)
            if problems:
                raise OutreachError("; ".join(problems))
            if req.name is not None:
                campaign.name = " ".join(req.name.split())
            if req.trade is not None:
                campaign.trade = req.trade.strip()
            if req.steps is not None or req.steps_b is not None:
                campaign.steps, campaign.steps_b = steps, steps_b
                self._reschedule(campaign)
            campaign.footer = footer
            self._finish_if_done(campaign, self.clock())
            self._save(campaign)
            return campaign

    async def delete(self, campaign_id: str) -> None:
        async with self._guard():
            campaign = self.get(campaign_id)
            if campaign.status == CampaignStatus.RUNNING:
                raise OutreachError("the campaign is sending - pause it first", 409)
            del self.campaigns[campaign_id]
            with suppress(OSError):
                (self.cfg.campaigns_dir / f"{campaign_id}.json").unlink(missing_ok=True)
            log.info("Campaign %r deleted (the businesses it emailed stay on the contacted list)", campaign.name)

    async def start(self, campaign: Campaign) -> Campaign:
        async with self._guard():
            if campaign.status == CampaignStatus.RUNNING:
                return campaign
            if campaign.status == CampaignStatus.FINISHED:
                raise OutreachError("the campaign has finished - every business got its emails", 409)
            problems = self.problems() + self.campaign_problems(campaign)
            if problems:
                raise OutreachError("Can't start yet: " + " · ".join(problems))
            campaign.status, campaign.error = CampaignStatus.RUNNING, ""
            campaign.started_at = campaign.started_at or self.clock()
            self._save(campaign)
            waiting = sum(lead.status in OPEN_STATUSES for lead in campaign.leads)
            log.info("Campaign %r started: %d business(es) to email", campaign.name, waiting)
            self._note("Campaign started", f"{waiting} business(es) to email", campaign)
            return campaign

    async def pause(self, campaign: Campaign) -> Campaign:
        async with self._guard():
            if campaign.status == CampaignStatus.RUNNING:
                campaign.status = CampaignStatus.PAUSED
                self._save(campaign)
                log.info("Campaign %r paused", campaign.name)
                self._note("Campaign paused", "by you", campaign)
            return campaign

    # -- Google Drive: app/drive/sync.py uploads, the campaign keeps where to -------
    def results(self, campaign: Campaign) -> bytes:
        """The campaign's results CSV (who was emailed when, who answered …)."""
        return results_csv(campaign, self._window()[0], self.cfg.SITES_URL if self.sites else "")

    def record_drive(self, campaign: Campaign, file: dict, path: str, digest: str) -> None:
        campaign.drive_file_id, campaign.drive_link = file.get("id", ""), file.get("webViewLink", "")
        campaign.drive_path, campaign.drive_hash, campaign.drive_synced_at = path, digest, self.clock()
        self._save(campaign)

    def campaign_problems(self, campaign: Campaign) -> list[str]:
        """What keeps this campaign from starting (besides the mailbox settings)."""
        problems = versions_problems(campaign.steps, campaign.steps_b, campaign.footer)
        if campaign.sites and self.sites is not None and not self.sites.has_design(campaign.business_type_name):
            problems.append(f"there's no website design for {campaign.business_type_name or 'these businesses'} "
                            "yet, and the emails send their website")
        waiting = [lead for lead in campaign.leads if lead.status in OPEN_STATUSES]
        if not waiting:
            problems.append("there is no business left to email")
        texts = [campaign.footer] + [t for s in campaign.all_steps() for t in (s.subject, s.body)]
        if "Trade" in set().union(*(used_fields(t) for t in texts)) and not campaign.trade.strip():
            problems.append("fill in what the emails call these businesses ({Trade}), e.g. roofers")
        missing: dict[str, list[str]] = {}
        for lead in waiting:
            for name in missing_fields(campaign, lead, self.cfg):
                if name not in ("Trade", "SenderName", "SenderCompany", "SenderAddress"):
                    missing.setdefault(name, []).append(lead.business)
        for name, businesses in missing.items():
            shown = ", ".join(businesses[:3]) + (f" and {len(businesses) - 3} more" if len(businesses) > 3 else "")
            problems.append(f"{{{name}}} is empty for {shown} - put it inside [[ … ]] or leave "
                            f"{'it' if len(businesses) == 1 else 'them'} out")
        return problems

    async def update_lead(self, campaign: Campaign, lead_id: str, req: LeadUpdate) -> Lead:
        async with self._guard():
            lead = campaign.lead(lead_id)
            if lead is None:
                raise OutreachError("lead not found", 404)
            if req.business is not None:
                lead.business = " ".join(req.business.split())
            if req.skipped is True and lead.status != LeadStatus.SKIPPED:
                if lead.status not in OPEN_STATUSES:
                    raise OutreachError(f"{lead.business} has {lead.status.value} - nothing more is sent anyway", 409)
                lead.status, lead.next_due, lead.note = LeadStatus.SKIPPED, None, "left out by you"
            elif req.skipped is False and lead.status == LeadStatus.SKIPPED:
                other = self.contacted.get(lead.email.lower())
                if other and other.get("campaign_id") != campaign.id:
                    raise OutreachError(f"{lead.email} was emailed by campaign “{other.get('campaign')}”", 409)
                lead.status, lead.note = (LeadStatus.SENT if lead.sent else LeadStatus.QUEUED), ""
                self._schedule(campaign, lead)
                if campaign.status == CampaignStatus.FINISHED and lead.status in OPEN_STATUSES:
                    campaign.status, campaign.finished_at = CampaignStatus.PAUSED, None
            self._finish_if_done(campaign, self.clock())
            self._save(campaign)
            return lead

    def preview(self, campaign: Campaign, lead_id: str | None = None) -> dict:
        lead = self._lead_for(campaign, lead_id)
        return {
            "lead_id": lead.id, "business": lead.business, "to": lead.email, "from": sender_header(self.cfg),
            "missing": missing_fields(campaign, lead, self.cfg),
            "emails": self._emails(campaign, lead),
            "values": lead_values(campaign, lead, self.cfg),   # the panel fills the templates live while you type
        }

    async def send_test(self, source: Campaign | BusinessType, req: SendTestRequest) -> dict:
        """Send one email of a campaign / business type to yourself (subject starts with [Test])."""
        problems = smtp_problems(self.cfg)
        if problems:
            raise OutreachError("Can't send: " + " · ".join(problems))
        if req.step > len(source.steps):
            raise OutreachError(f"there are only {len(source.steps)} emails")
        if isinstance(source, Campaign):
            lead = self._lead_for(source, req.lead_id)
        else:
            lead = _sample_leads()[0][1]
        subject, body = compose(source, lead, req.step, self.cfg)
        me = sender_address(self.cfg)
        message = build_message(sender=sender_header(self.cfg), to=me, subject=f"[Test] {subject}", body=body,
                                message_id=new_message_id(me))
        try:
            await self.mailbox.send(message)
        except MailError as exc:
            raise OutreachError(f"The test email could not be sent: {exc}", 502) from exc
        log.info("Test email (email %d of %r, as for %s) sent to %s", req.step, source.name, lead.business, me)
        return {"to": me, "subject": str(message["Subject"])}

    async def check_now(self) -> dict:
        """Check the mailbox for replies and bounces now (the panel's *Check replies now*)."""
        problems = imap_problems(self.cfg)
        if problems:
            raise OutreachError("Can't check: " + " · ".join(problems))
        async with self._guard():
            await self._check(self.clock())
        if self.last_check["error"]:
            raise OutreachError(self.last_check["error"], 502)
        return self.status()["last_check"]

    def _lead_for(self, campaign: Campaign, lead_id: str | None) -> Lead:
        if lead_id:
            lead = campaign.lead(lead_id)
            if lead is None:
                raise OutreachError("lead not found", 404)
            return lead
        lead = next((l for l in campaign.leads if l.status in OPEN_STATUSES), None) or next(iter(campaign.leads), None)
        if lead is None:
            raise OutreachError("this campaign has no businesses to email")
        return lead

    def _emails(self, source: Campaign | BusinessType, lead: Lead) -> list[dict]:
        sent = {s.step: s for s in lead.sent}
        emails = []
        for number, step in enumerate(source.steps_of(lead), 1):
            subject, body = compose(source, lead, number, self.cfg)
            emails.append({"step": number, "day": step.day, "subject": subject, "body": body,
                           "sent_at": _iso(sent[number].sent_at) if number in sent else None})
        return emails

    # ------------------------------------------------------------------
    # The send loop
    # ------------------------------------------------------------------
    def start_loop(self) -> None:
        if not self.cfg.OUTREACH_AUTOSEND:
            log.info("Outreach: OUTREACH_AUTOSEND is off - campaigns don't send and replies are only checked "
                     "on request")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="outreach")

    async def stop_loop(self) -> None:
        if self._task is None:
            return
        if self._sending is not None:
            # Cancelled half-way, an email could leave the mail server without being written down - and go
            # out a second time after the next start. Let it finish; the mail timeouts bound the wait.
            log.info("Outreach: waiting for the email being sent")
            with suppress(TimeoutError):
                async with asyncio.timeout(STOP_WAIT_S):
                    await self._sending.wait()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Outreach: unexpected error")
            await asyncio.sleep(TICK_S)

    async def tick(self) -> None:
        """One round: check the mailbox when it is time, send the next email when one is due."""
        async with self._guard():
            now = self.clock()
            running = any(c.status == CampaignStatus.RUNNING for c in self.campaigns.values())
            watched = self._watched(now)
            if not running and not watched:
                return
            if self.problems():
                return                                   # the panel shows them
            window, _ = self._window()
            is_open = window is not None and window.is_open(now)
            pick = None
            if (running and is_open and self.cfg.OUTREACH_AUTOSEND
                    and self.sent_today(now) < self.cfg.OUTREACH_DAILY_LIMIT
                    and (self.next_send_at is None or now >= self.next_send_at)):
                pick = self._next_email(now)
            # outside the send window replies only need to show up in the panel: once an hour is enough
            every = timedelta(minutes=max(1, self.cfg.OUTREACH_CHECK_MINUTES) if is_open else 60)
            stale = self.last_check["at"] is None or now - self.last_check["at"] >= every
            if (watched and stale) or (pick and not self._fresh(now)):
                await self._check(now)
            if pick:
                pick = self._next_email(now)             # a reply may just have stopped it
                if pick and self._fresh(now):
                    await self._send(*pick, now)

    def site_ready(self, campaign: Campaign, lead: Lead) -> bool:
        """The lead's first email may go out: its website is online (or the campaign has no websites)."""
        return not campaign.sites or self.sites is None or self.sites.is_live(lead.site)

    async def _build_sites(self, business_type: BusinessType | None, jobs: list[Job], leads: list[Lead],
                           left_out: list[LeftOut]) -> tuple[list[Lead], list[LeftOut]]:
        """Build each lead's website; a lead whose website can't be built is left out, with the reason."""
        type_name = business_type.name if business_type else ""
        if not self.sites.has_design(type_name):
            return leads, left_out          # campaign_problems() says so, and the campaign can't start
        by_id = {job.id: job for job in jobs}
        items = []
        for lead in leads:
            job = by_id[lead.job_id]
            row = _row_of(job, lead)
            items.append((lead.id, job, row.model_copy(update={"email": lead.email}), lead.city))
        results = await asyncio.to_thread(self.sites.build, type_name, items)
        kept: list[Lead] = []
        for lead in leads:
            slug, why = results[lead.id]
            if slug:
                lead.site = slug
                kept.append(lead)
            else:
                left_out.append(LeftOut(name=lead.name, email=lead.email, reason=why))
        return kept, left_out

    def _fresh(self, now: datetime) -> bool:
        ok_at = self.last_check["ok_at"]
        return ok_at is not None and not self.last_check["error"] and now - ok_at <= FRESH_CHECK

    def _next_email(self, now: datetime, *, any_time: bool = False) -> tuple[Campaign, Lead] | None:
        """The next email to send: due follow-ups first (earliest first), then first emails in order.

        ``any_time`` also takes follow-ups that aren't due yet (for the status's "next email").
        """
        best, best_key = None, None
        running = [c for c in self.campaigns.values() if c.status == CampaignStatus.RUNNING]
        for campaign in sorted(running, key=lambda c: c.started_at or c.created_at):
            for index, lead in enumerate(campaign.leads):
                if lead.status == LeadStatus.SENT and len(lead.sent) < len(campaign.steps_of(lead)):
                    due = lead.next_due or now
                    if due > now and not any_time:
                        continue
                    key = (0, due, index)
                elif lead.status == LeadStatus.QUEUED:
                    if not self.site_ready(campaign, lead):
                        continue
                    key = (1, campaign.started_at or campaign.created_at, index)
                else:
                    continue
                if best_key is None or key < best_key:
                    best, best_key = (campaign, lead), key
        return best

    async def _send(self, campaign: Campaign, lead: Lead, now: datetime) -> None:
        self._sending = asyncio.Event()
        try:
            await self._deliver(campaign, lead, now)
        finally:
            self._sending.set()                         # stop_loop may cancel the loop now
            self._sending = None

    async def _deliver(self, campaign: Campaign, lead: Lead, now: datetime) -> None:
        step = len(lead.sent) + 1
        if step == 1 and self.check_domain is not None:
            problem = await self.check_domain(lead.email)
            if problem:
                lead.status, lead.next_due, lead.note = LeadStatus.FAILED, None, problem
                log.warning("Not emailing %s <%s>: %s", lead.business, lead.email, problem)
                self._note("Address skipped", problem, campaign, lead)
                self._mark_contacted(campaign, lead, "failed", now)   # never try this address again
                self._finish_if_done(campaign, now)
                self._save(campaign)
                return
        if step == 1 and campaign.steps_b and not lead.variant:
            lead.variant = self._next_variant()
        subject, body = compose(campaign, lead, step, self.cfg)
        me = sender_address(self.cfg)
        message_id = new_message_id(me)
        message = build_message(sender=sender_header(self.cfg), to=lead.email, subject=subject, body=body,
                                message_id=message_id, earlier_ids=[s.message_id for s in lead.sent],
                                unsubscribe=me if self.cfg.OUTREACH_UNSUBSCRIBE_HEADER else "")
        try:
            await self.mailbox.send(message)
        except RecipientRefused as exc:
            lead.status, lead.next_due, lead.note = LeadStatus.FAILED, None, str(exc)
            log.warning("Email %d to %s <%s> not sent: %s", step, lead.business, lead.email, exc)
            self._note("Email refused", f"email {step}: {exc}", campaign, lead)
            self._mark_contacted(campaign, lead, "failed", now)       # never try this address again
            self._finish_if_done(campaign, now)
            self._save(campaign)
            self._after_send(now)
            return
        except (MailAuthError, MailRejected) as exc:
            log.error("Sending stopped: %s", exc)
            self._pause_all(f"Paused: {exc}")
            return
        except MailError as exc:
            self._failures += 1
            self.next_send_at = now + RETRY_AFTER
            if self._failures >= MAX_FAILURES:
                log.error("Sending failed %d times in a row, campaigns paused: %s", self._failures, exc)
                self._pause_all(f"Paused after {self._failures} failed attempts to send: {exc}")
                self._failures = 0
            else:
                log.warning("Could not send email %d to %s (%s) - trying again in 5 minutes", step, lead.email, exc)
            return
        self._failures = 0
        lead.sent.append(SentEmail(step=step, sent_at=now, message_id=message_id, subject=subject))
        if step == 1:
            self._mark_contacted(campaign, lead, "emailed", now)
        if lead.site and self.sites is not None:
            self._site_dates(campaign, lead, step, now)
        self._schedule(campaign, lead)
        version = f" (version {lead.variant})" if lead.variant else ""
        log.info("Sent email %d of %d%s to %s <%s> (campaign %r)", step, len(campaign.steps_of(lead)), version,
                 lead.business, lead.email, campaign.name)
        self._note("Email sent", f"email {step} of {len(campaign.steps_of(lead))}{version}: {subject}", campaign,
                   lead)
        self._finish_if_done(campaign, now)
        self._save(campaign)
        self._after_send(now)
        if test_copy_addresses(self.cfg) and self.sent_today(now) == 1:
            await self._test_copy(campaign, lead, step, subject, body)

    def _next_variant(self) -> str:
        """A or B for a business about to get its first email: whichever fewer businesses have had so far
        (across all campaigns), A on a tie - so the two versions stay even."""
        given = [lead.variant for c in self.campaigns.values() for lead in c.leads if lead.variant]
        return "B" if given.count("A") > given.count("B") else "A"

    async def _test_copy(self, campaign: Campaign, lead: Lead, step: int, subject: str, body: str) -> None:
        """The day's first email, word for word, to your own addresses (``OUTREACH_TEST_COPY_TO``): which tab
        does it land in? Its website link carries ``?copy``, so opening it isn't counted as the business's."""
        if lead.site and self.sites is not None:
            url = self.sites.url(lead.site)
            body = body.replace(url, url + "?copy")
        me = sender_address(self.cfg)
        for address in test_copy_addresses(self.cfg):
            message = build_message(sender=sender_header(self.cfg), to=address, subject=subject, body=body,
                                    message_id=new_message_id(me),
                                    unsubscribe=me if self.cfg.OUTREACH_UNSUBSCRIBE_HEADER else "")
            try:
                await self.mailbox.send(message)
            except MailError as exc:
                log.warning("The test copy to %s wasn't sent: %s", address, exc)
                self._note("Test copy not sent", f"to {address}: {exc}", campaign)
                continue
            log.info("Sent a test copy of %s's email %d to %s", lead.business, step, address)
            self._note("Test copy sent", f"to {address}: {lead.business}'s email {step} – which tab is it in?", campaign)

    # ------------------------------------------------------------------
    # Website visits (app/outreach/visits.py)
    # ------------------------------------------------------------------
    def record_visits(self, visits: list[dict]) -> None:
        """New visits to the businesses' websites. Those of the business - not yours, not a test copy's, not
        a link check - are counted on its lead; the first of each day and every tap go into the activity log."""
        owners = {lead.site: (c, lead) for c in self.campaigns.values() for lead in c.leads if lead.site}
        # the page stayed open or something was tapped: a person, even right after the email
        people = {(v.get("slug"), v.get("who")) for v in visits if v.get("kind") in PERSON_KINDS}
        window, _ = self._window()
        day = (lambda moment: window.local_date(moment)) if window else (lambda moment: moment.date())
        changed: dict[str, Campaign] = {}
        for visit in sorted(visits, key=lambda v: v.get("id", 0)):
            found = owners.get(visit.get("slug"))
            if not found or visit.get("as") != "visitor":
                continue
            campaign, lead = found
            at = datetime.fromisoformat(visit["at"].replace("Z", "+00:00"))
            kind = visit.get("kind", "")
            if (kind in ("view", "ended") and (visit.get("slug"), visit.get("who")) not in people
                    and any(timedelta(0) <= at - s.sent_at <= LINK_CHECK for s in lead.sent)):
                continue                        # a mail server opening the link to check it
            first_today = lead.site_last_visit_at is None or day(lead.site_last_visit_at) != day(at)
            lead.site_opened_at = lead.site_opened_at or at
            lead.site_last_visit_at = max(at, lead.site_last_visit_at or at)
            lead.site_from = place(visit) or lead.site_from
            if kind in ("view", "ended"):
                lead.site_visits += 1
            if first_today and kind in ("view", "ended", "seen"):
                what = "Ended website opened" if kind == "ended" else "Website opened"
                self._note(what, " · ".join(filter(None, [place(visit), _visit_count(lead)])), campaign, lead)
            if kind in TAPS and kind not in lead.site_taps:
                lead.site_taps.append(kind)
                self._note(TAPS[kind], place(visit), campaign, lead)
            changed[campaign.id] = campaign
        for campaign in changed.values():
            self._save(campaign)
        if changed:
            log.info("Website visits: %d business(es) opened their website",
                     len({(c.id, lead.id) for c in changed.values() for lead in c.leads if lead.site_opened_at}))

    def _site_dates(self, campaign: Campaign, lead: Lead, step: int, now: datetime) -> None:
        """Email ``step`` went out: write down what it means for the lead's website (``sites/deploy.py``)."""
        if step == 1:
            self.sites.record(lead.site, "first_email_at", now)
        if links_site(campaign, lead, step):
            self.sites.record(lead.site, "link_sent_at", now)
            if step == len(campaign.steps_of(lead)):          # it says the website comes down tomorrow
                self.sites.record(lead.site, "last_email_at", now)

    def _after_send(self, now: datetime) -> None:
        low, high = sorted((max(0, self.cfg.OUTREACH_MIN_GAP_S), max(0, self.cfg.OUTREACH_MAX_GAP_S)))
        self.next_send_at = now + timedelta(seconds=self.rng.uniform(low, high))

    def _schedule(self, campaign: Campaign, lead: Lead) -> None:
        """Set a lead's status and next follow-up from the emails it has had."""
        if lead.status not in OPEN_STATUSES:
            return
        count, steps = len(lead.sent), campaign.steps_of(lead)
        if count == 0:
            lead.status, lead.next_due = LeadStatus.QUEUED, None
        elif count >= len(steps):
            lead.status, lead.next_due = LeadStatus.DONE, None
        else:
            gap = steps[count].day - steps[count - 1].day
            lead.status = LeadStatus.SENT
            lead.next_due = lead.sent[-1].sent_at + timedelta(days=max(1, gap))

    def _reschedule(self, campaign: Campaign) -> None:
        for lead in campaign.leads:
            self._schedule(campaign, lead)

    def _finish_if_done(self, campaign: Campaign, now: datetime) -> None:
        if campaign.status in (CampaignStatus.RUNNING, CampaignStatus.PAUSED) and not any(
                lead.status in OPEN_STATUSES for lead in campaign.leads):
            campaign.status, campaign.finished_at = CampaignStatus.FINISHED, now
            counts = campaign.counts()
            log.info("Campaign %r finished: %d replied, %d without a reply, %d bounced", campaign.name,
                     counts["replied"], counts["done"], counts["bounced"])
            self._note("Campaign finished", f"{counts['replied']} replied, {counts['done']} without a reply, "
                                            f"{counts['bounced']} bounced", campaign)

    def pause_running(self, reason: str) -> int:
        """Pause every running campaign with ``reason`` (the autopilot's safety stop); how many."""
        return self._pause_all(reason)

    def _pause_all(self, reason: str) -> int:
        paused = 0
        for campaign in self.campaigns.values():
            if campaign.status == CampaignStatus.RUNNING:
                campaign.status, campaign.error = CampaignStatus.PAUSED, reason
                self._save(campaign)
                self._note("Campaign paused", reason, campaign)
                paused += 1
        return paused

    # ------------------------------------------------------------------
    # Replies and bounces
    # ------------------------------------------------------------------
    def _watched(self, now: datetime) -> list[tuple[Campaign, Lead]]:
        """Leads whose emails are looked for: those who haven't answered, for WATCH_DAYS after their last email,
        and those who have, for WATCH_DAYS after they last wrote (the conversation goes on in your inbox)."""
        since = now - timedelta(days=WATCH_DAYS)
        return [(c, lead) for c in self.campaigns.values() for lead in c.leads
                if lead.status in _WATCHED and lead.sent and _last_contact(lead) >= since]

    async def _check(self, now: datetime) -> None:
        watched = self._watched(now)
        addresses: dict[str, str] = {}
        message_ids: dict[str, str] = {}
        leads: dict[str, tuple[Campaign, Lead]] = {}
        for campaign, lead in watched:
            key = f"{campaign.id}/{lead.id}"
            leads[key] = (campaign, lead)
            for address in [lead.email, *lead.other_emails]:
                addresses[address.lower()] = key
            for sent in lead.sent:
                message_ids[sent.message_id] = key
        first = min((lead.sent[0].sent_at for _, lead in watched), default=now)
        self.last_check["at"] = now
        try:
            messages = await self.mailbox.fetch((first - timedelta(days=1)).date())
        except MailAuthError as exc:
            self.last_check["error"] = str(exc)
            log.error("Checking for replies failed: %s", exc)
            self._pause_all(f"Paused: {exc}")
            return
        except MailError as exc:
            self.last_check["error"] = str(exc)
            log.warning("Could not check the mailbox for replies (nothing is sent until it works): %s", exc)
            return
        replies = bounces = 0
        changed: dict[str, Campaign] = {}
        own = sender_address(self.cfg)
        for message in messages:
            hit = classify(message, addresses=addresses, message_ids=message_ids, own_address=own)
            if hit is None:
                continue
            key, kind = hit
            campaign, lead = leads[key]
            if lead.status not in _WATCHED or (lead.status == LeadStatus.REPLIED and kind == "bounce"):
                continue                                  # an earlier email stopped it already; they can be reached
            if _already_noted(lead, message):
                continue
            lead.inbound.append(InboundEmail(kind=kind, received_at=message.received_at, sender=message.sender,
                                             subject=message.subject, message_id=message.message_id,
                                             alert_text=_alert_text(message.text) if kind == "reply" else None))
            changed[campaign.id] = campaign
            if kind == "delay":
                log.info("Delivery to %s <%s> is taking long (%s) - the follow-ups continue", lead.business,
                         lead.email, message.subject)
                self._note("Delivery delayed", message.subject, campaign, lead)
                continue
            if kind == "auto_reply":
                log.info("Automatic reply from %s <%s> (%s) - the follow-ups continue", lead.business,
                         message.sender, message.subject)
                self._note("Automatic reply", message.subject, campaign, lead)
                continue
            lead.status, lead.next_due = _STOPPED_BY[kind], None
            lead.note = message.subject or kind
            self._note({"reply": "Reply", "bounce": "Bounce", "unsubscribe": "Unsubscribe"}[kind],
                       message.subject or "", campaign, lead)
            if kind == "reply" and lead.site and self.sites is not None:
                self.sites.record(lead.site, "replied_at", now)     # online while you talk
                if not declined(message.text) and self.sites.set_hold(lead.site, True):
                    self._note("Website on hold", "they replied: no more countdown", campaign, lead)
            self._mark_contacted(campaign, lead, lead.status.value, now)
            if kind in ("bounce", "unsubscribe") and lead.site and self.sites is not None:
                self.sites.request_upload()      # its website's quote requests stop going to it (sites/deploy.py)
            if kind == "bounce":
                bounces += 1
                log.warning("Bounce for %s <%s> - the address doesn't work, no more emails to it", lead.business,
                            lead.email)
            else:
                replies += 1
                log.info("%s from %s <%s>: %r - no more emails to them", "Unsubscribe" if kind == "unsubscribe"
                         else "Reply", lead.business, message.sender or lead.email, message.subject)
            self._finish_if_done(campaign, now)
        for campaign in changed.values():
            self._save(campaign)
        self.last_check.update(ok_at=now, error="", emails=len(messages), replies=replies, bounces=bounces)
        await self._send_alerts()

    def alert_to(self) -> str:
        """Where emails to you go: ALERT_TO (comma-separated), else the From address."""
        return ", ".join(a.strip() for a in self.cfg.ALERT_TO.split(",") if a.strip()) or sender_address(self.cfg)

    async def email_me(self, subject: str, body: str) -> bool:
        """A note to yourself (e.g. Google Drive disconnected), to ALERT_TO - else the From address. False: it
        couldn't go out."""
        if mailbox_problems(self.cfg):
            log.warning("Couldn't email you %r: the mailbox settings aren't complete", subject)
            return False
        me = sender_address(self.cfg)
        message = build_message(sender=sender_header(self.cfg), to=self.alert_to(), subject=f"[Leads Pilot] {subject}",
                                body=body,
                                message_id=new_message_id(me))
        try:
            await self.mailbox.send(message)
        except MailError as exc:
            log.warning("Couldn't email you %r: %s", subject, exc)
            return False
        log.info("Emailed you: %s", subject)
        return True

    async def _send_alerts(self) -> None:
        """Tell you about each reply in an email of its own. One that can't go out is tried after the next check."""
        me = sender_address(self.cfg)
        for campaign in self.campaigns.values():
            for lead in campaign.leads:
                for inbound in lead.inbound:
                    if inbound.alert_text is None:
                        continue
                    website = self.sites.url(lead.site) if self.sites is not None and lead.site else ""
                    message = build_message(sender=sender_header(self.cfg), to=self.alert_to(),
                                            subject=" ".join(f"[Leads Pilot] Reply from {lead.business}".split()),
                                            body=_alert_body(campaign, lead, inbound, website),
                                            message_id=new_message_id(me))
                    try:
                        await self.mailbox.send(message)
                    except MailError as exc:
                        log.warning("A reply alert couldn't be emailed, it's tried again after the next check: %s", exc)
                        return
                    inbound.alert_text = None
                    self._save(campaign)
                    log.info("Reply alert for %s emailed to you", lead.business)

    # ------------------------------------------------------------------
    # Who was emailed already
    # ------------------------------------------------------------------
    def _mark_contacted(self, campaign: Campaign, lead: Lead, status: str, now: datetime) -> None:
        entry = self.contacted.setdefault(lead.email.lower(), {
            "business": lead.business, "place_id": lead.place_id, "campaign_id": campaign.id,
            "campaign": campaign.name, "first_email": _iso(now),
        })
        entry["status"] = status
        self._save_contacted()

    def _taken(self) -> dict[str, str]:
        """Addresses / place ids no new campaign may email, with the reason shown for them."""
        taken: dict[str, str] = {}
        window, _ = self._window()
        for address, info in self.contacted.items():
            when = info.get("first_email", "")
            try:
                moment = datetime.fromisoformat(when)
                day = day_label(window.local_date(moment) if window else moment.date())
            except (TypeError, ValueError):
                day = "earlier"
            reason = f"emailed on {day} (campaign “{info.get('campaign', '')}”)"
            taken[address] = reason
            if info.get("place_id"):
                taken[info["place_id"]] = reason
        for campaign in self.campaigns.values():
            reason = f"already in campaign “{campaign.name}”"
            for lead in campaign.leads:
                if lead.status == LeadStatus.SKIPPED and not lead.sent:
                    continue
                for address in [lead.email, *lead.other_emails]:
                    taken.setdefault(address.lower(), reason)
                if lead.place_id:
                    taken.setdefault(lead.place_id, reason)
        return taken

    def _guard(self) -> asyncio.Lock:
        """The manager's lock, for the running event loop (tests run several loops one after another)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _row_of(job: Job, lead: Lead) -> PlaceRow:
    """The search row a lead was made from (the one with its email when the place has several)."""
    rows = [r for r in job.rows if (lead.place_id and r.place_id == lead.place_id) or r.name == lead.name]
    return next((r for r in rows if r.email.lower() == lead.email.lower()), rows[0])


def _default_name(jobs: list[Job], files: list[DriveSource], business_type: BusinessType | None = None) -> str:
    """'Roofer · Houston', 'Roofer · my_list' (+ '+2 more' for more sources)."""
    more = len(jobs) + len(files) - 1
    if jobs:
        first = jobs[0]
        what = business_type.name if business_type else (first.business or first.query)
        places = split_places(first.place)
        name = f"{what} · {places[0]}" if places else what
    else:
        stem = files[0].name.rsplit(".", 1)[0]
        name = f"{business_type.name} · {stem}" if business_type else stem
    return name + (f" +{more} more" if more > 0 else "")


RESULT_COLUMNS = ["Business", "Email", "Status", "Emails Sent", "First Email", "Last Email", "Next Email",
                  "Answered", "Answer", "City", "Phone", "Rating", "Reviews", "Name On Maps", "Place ID", "Note",
                  "Website", "Website Opened", "Last Visit", "Website Visits", "Tapped", "Visited From", "Version"]


def results_csv(campaign: Campaign, window: SendWindow | None, sites_url: str = "") -> bytes:
    """The campaign's results as CSV (UTF-8 with BOM, like the lead lists): one row per business."""
    def when(moment: datetime | None) -> str:
        if moment is None:
            return ""
        return (window.local(moment) if window else moment).strftime("%Y-%m-%d %H:%M")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(RESULT_COLUMNS)
    for lead in campaign.leads:
        answer = next((i for i in reversed(lead.inbound) if i.kind not in ("auto_reply", "delay")), None)
        writer.writerow([
            lead.business, lead.email, lead.status.value, f"{len(lead.sent)} of {len(campaign.steps_of(lead))}",
            when(lead.sent[0].sent_at) if lead.sent else "", when(lead.sent[-1].sent_at) if lead.sent else "",
            when(lead.next_due) if lead.status == LeadStatus.SENT else "",
            when(answer.received_at) if answer else "", answer.subject if answer else "",
            lead.city, lead.phone, "" if lead.rating is None else f"{lead.rating:g}",
            "" if lead.reviews is None else lead.reviews, lead.name, lead.place_id, lead.note,
            f"{sites_url.rstrip('/')}/{lead.site}/" if lead.site and sites_url else "",
            when(lead.site_opened_at), when(lead.site_last_visit_at), lead.site_visits or "",
            ", ".join(lead.site_taps), lead.site_from, lead.variant,
        ])
    return (chr(0xFEFF) + buffer.getvalue()).encode("utf-8")


def test_copy_addresses(cfg: Settings) -> list[str]:
    """``OUTREACH_TEST_COPY_TO`` as a list of addresses (anything that isn't one is ignored)."""
    return [a for a in re.split(r"[,;\s]+", cfg.OUTREACH_TEST_COPY_TO) if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a)]


def _visit_count(lead: Lead) -> str:
    return f"{lead.site_visits} visit(s) so far" if lead.site_visits > 1 else ""


def _sample_leads() -> list[tuple[str, Lead]]:
    """Made-up businesses for the business-type preview: one with good reviews, one with few."""
    return [
        ("4.8 stars, 57 reviews", Lead(email="owner@example.com", business="Acme", name="Acme LLC",
                                       city="Houston", rating=4.8, reviews=57, site="acme")),
        ("few reviews", Lead(email="owner@example.com", business="Acme", name="Acme LLC",
                             city="Houston", rating=5.0, reviews=3, site="acme")),
    ]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _write(path: Path, text: str) -> None:
    """Write a file atomically (temp file + rename), like the job manager does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp)
        raise
