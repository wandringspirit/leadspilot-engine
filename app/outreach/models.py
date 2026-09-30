"""Models of the email outreach: campaigns, their leads and email steps.

Stored as JSON under ``data/campaigns`` and returned by the ``/api/campaigns``
routes (docs/API.md, docs/OUTREACH.md).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import new_id
from app.outreach.templates import DEFAULT_FOOTER, DEFAULT_STEPS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus(str, Enum):
    QUEUED = "queued"              # nothing sent yet
    SENT = "sent"                  # in the sequence: the next follow-up is scheduled
    DONE = "done"                  # every email sent, no reply
    REPLIED = "replied"            # answered: no more emails
    UNSUBSCRIBED = "unsubscribed"  # asked not to be emailed (unsubscribe link or a "stop" reply)
    BOUNCED = "bounced"            # the address does not exist
    FAILED = "failed"              # the mail server refused the address
    SKIPPED = "skipped"            # left out by you


#: leads that still get emails
OPEN_STATUSES = frozenset({LeadStatus.QUEUED, LeadStatus.SENT})


class SentEmail(BaseModel):
    step: int                      # 1 = the first email
    sent_at: datetime
    message_id: str
    subject: str


class InboundEmail(BaseModel):
    """Something that arrived in the mailbox for a lead."""
    kind: str                      # reply | auto_reply | unsubscribe | bounce
    received_at: datetime | None = None
    sender: str = ""
    subject: str = ""
    message_id: str = ""


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: new_id("l_"))
    job_id: str = ""
    place_id: str = ""
    name: str = ""                 # as listed on Google Maps
    business: str = ""             # as written in the emails ({Business}) - editable
    email: str                     # the address the emails go to
    other_emails: list[str] = Field(default_factory=list)   # further usable addresses (not emailed)
    city: str = ""                 # {City}: the place that was searched
    rating: float | None = None
    reviews: int | None = None
    phone: str = ""
    category: str = ""
    maps_url: str = ""
    status: LeadStatus = LeadStatus.QUEUED
    next_due: datetime | None = None   # earliest time for the next follow-up
    sent: list[SentEmail] = Field(default_factory=list)
    inbound: list[InboundEmail] = Field(default_factory=list)
    note: str = ""                 # why it stopped: the reply's subject, the server's answer ...


class LeftOut(BaseModel):
    """A business of the selected jobs that did not become a lead, and why."""
    name: str
    email: str = ""
    reason: str


class Step(BaseModel):
    day: int = Field(0, ge=0, le=90, description="Days after the first email (0 for the first email)")
    subject: str = Field("", max_length=200, description="Empty for a follow-up: 'Re: ' + the first subject")
    body: str = Field(..., max_length=5000)

    @field_validator("body")
    @classmethod
    def _has_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an email needs some text")
        return value


def default_steps() -> list[Step]:
    return [Step(**step) for step in DEFAULT_STEPS]


class CampaignStatus(str, Enum):
    DRAFT = "draft"                # being set up - nothing is sent
    RUNNING = "running"            # the server sends in the send window
    PAUSED = "paused"              # by you, or automatically after a mailbox problem (see `error`)
    FINISHED = "finished"          # every lead got all its emails or stopped


class BusinessType(BaseModel):
    """A kind of business you look for ("Roofers", "Plumbers" …): what the search form types into
    Google Maps, and the emails a campaign for these businesses starts with."""
    id: str = Field(default_factory=lambda: new_id("bt_"))
    name: str                      # shown in the search form's Business type list
    search: str = ""               # typed into Google Maps, e.g. "roofing"
    trade: str = ""                # {Trade} in the emails, e.g. "roofers"
    steps: list[Step] = Field(default_factory=default_steps)
    footer: str = DEFAULT_FOOTER
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class DriveSource(BaseModel):
    """A lead file in Google Drive a campaign was made from."""
    id: str
    name: str
    link: str = ""


class Campaign(BaseModel):
    id: str = Field(default_factory=lambda: new_id("camp_"))
    name: str
    trade: str = ""                # {Trade}, e.g. "roofers"
    job_ids: list[str] = Field(default_factory=list)
    drive_files: list[DriveSource] = Field(default_factory=list)   # lead files the businesses came from
    business_type_id: str = ""     # the business type the emails were copied from ('' = the default emails)
    business_type_name: str = ""
    steps: list[Step] = Field(default_factory=default_steps)
    footer: str = DEFAULT_FOOTER   # added to every email: postal address and how to opt out
    status: CampaignStatus = CampaignStatus.DRAFT
    error: str = ""                # why it was paused automatically
    leads: list[Lead] = Field(default_factory=list)
    left_out: list[LeftOut] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    drive_file_id: str = ""        # the results CSV in Google Drive (docs/DRIVE.md)
    drive_link: str = ""
    drive_path: str = ""
    drive_hash: str = ""           # of the content last uploaded - only changes are uploaded
    drive_synced_at: datetime | None = None

    def lead(self, lead_id: str) -> Lead | None:
        return next((lead for lead in self.leads if lead.id == lead_id), None)

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in LeadStatus}
        for lead in self.leads:
            counts[lead.status.value] += 1
        return counts

    def summary(self) -> "CampaignSummary":
        return CampaignSummary(
            id=self.id, name=self.name, trade=self.trade, business_type_name=self.business_type_name, status=self.status,
            error=self.error, leads=len(self.leads), counts=self.counts(),
            emails_sent=sum(len(lead.sent) for lead in self.leads), steps=len(self.steps), job_ids=self.job_ids,
            created_at=self.created_at, started_at=self.started_at, finished_at=self.finished_at,
        )


class CampaignSummary(BaseModel):
    id: str
    name: str
    trade: str = ""
    business_type_name: str = ""
    status: CampaignStatus
    error: str = ""
    leads: int
    counts: dict[str, int] = Field(description="Leads per status")
    emails_sent: int
    steps: int
    job_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------
class CampaignCreate(BaseModel):
    job_ids: list[str] = Field(default_factory=list, description="Jobs whose businesses become the leads")
    drive_file_ids: list[str] = Field(default_factory=list,
                                      description="Lead files in Google Drive (the crawler's CSV format)")
    business_type_id: str | None = Field(
        None, description="Business type whose emails the campaign starts with. Omit: the jobs' business type "
                          "when they share one, else the default emails; '' = the default emails")
    name: str = Field("", max_length=120, description="Defaults to the first job's business · place")
    trade: str = Field("", max_length=60, examples=["roofers"],
                       description="{Trade} in the emails; defaults to the business type's")

    @model_validator(mode="after")
    def _has_a_source(self) -> "CampaignCreate":
        if not self.job_ids and not self.drive_file_ids:
            raise ValueError("pick at least one search or lead file")
        return self


class BusinessTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, examples=["Plumbers"])
    search: str = Field("", max_length=120, examples=["plumber"], description="Typed into Google Maps")
    trade: str = Field("", max_length=60, examples=["plumbers"], description="{Trade} in the emails")
    steps: list[Step] | None = Field(None, min_length=1, max_length=5, description="Omit for the default emails")
    footer: str | None = Field(None, max_length=1000, description="Omit for the default footer")


class BusinessTypeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    search: str | None = Field(None, max_length=120)
    trade: str | None = Field(None, max_length=60)
    steps: list[Step] | None = Field(None, min_length=1, max_length=5)
    footer: str | None = Field(None, max_length=1000)


class CampaignUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    trade: str | None = Field(None, max_length=60)
    steps: list[Step] | None = Field(None, min_length=1, max_length=5,
                                     description="The first email and up to 4 follow-ups")
    footer: str | None = Field(None, max_length=1000)


class LeadUpdate(BaseModel):
    business: str | None = Field(None, min_length=1, max_length=120, description="{Business} for this lead")
    skipped: bool | None = Field(None, description="true: send this business nothing (more)")


class SendTestRequest(BaseModel):
    lead_id: str | None = Field(None, description="Whose details to fill in; default: the first lead")
    step: int = Field(1, ge=1, le=5, description="Which email: 1 = the first, 2 = the first follow-up …")
