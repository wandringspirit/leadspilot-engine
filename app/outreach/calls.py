"""The master sheet's Calls tab: the roofers to phone, in order, and what you noted about each call.

A row per business that was emailed and has a phone number (one per number; not those that unsubscribed).
You fill in three columns in the sheet - **Outcome** (a dropdown), **Call back on** (a date) and **Notes** -
and ``app/drive/sync.py`` reads them back before each write (``record_calls`` in the outreach manager keeps
them on the lead). The rest of the row is written by the panel.

Order: call-backs that are due, then businesses not called yet - the ones that opened their website, tapped
or replied first, then the most recently emailed - then the ones that didn't answer. Call-backs for later and
finished calls come last, without a number. Pure functions (tests/test_calls.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.outreach.models import LeadStatus

if TYPE_CHECKING:
    from app.config import Settings
    from app.outreach.models import Campaign, Lead

NO_ANSWER, VOICEMAIL, CALL_BACK = "No answer", "Left voicemail", "Call back"
INTERESTED, NOT_INTERESTED, WRONG_NUMBER, SIGNED_UP = "Interested", "Not interested", "Wrong number", "Signed up"
#: the Outcome dropdown
OUTCOMES = [NO_ANSWER, VOICEMAIL, CALL_BACK, INTERESTED, NOT_INTERESTED, WRONG_NUMBER, SIGNED_UP]
#: outcomes that stop the automatic emails: you're talking to them yourself now, or they said no
STOP_EMAILS = frozenset({INTERESTED, NOT_INTERESTED, SIGNED_UP})
FINISHED = frozenset({INTERESTED, NOT_INTERESTED, WRONG_NUMBER, SIGNED_UP})

CALL_COLUMNS = ["#", "Business", "Phone", "Town", "Website", "Last email", "Opened website", "Emails",
                "Outcome", "Call back on", "Notes", "Campaign", "Lead"]
#: the columns you fill in (the rest is overwritten)
OUTCOME_COL, CALL_BACK_COL, NOTES_COL, LEAD_COL = 8, 9, 10, 12
CALL_HOURS = (9, 11)       # when to call, their time: early, before the day on the roof - and not before 9am (Texas)
SHEETS_EPOCH = date(1899, 12, 30)     # day 0 of a Google Sheets date

Table = tuple[list[str], list[list]]


def phone_digits(phone: str) -> str:
    """'+1 (214) 555-0100' -> '2145550100' (US numbers; others as given, digits only)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def show_phone(phone: str) -> str:
    digits = phone_digits(phone)
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else (phone or "").strip()


def parse_day(value) -> str:
    """What the sheet gave for a 'Call back on' cell -> ISO date ('2026-11-17'), or the text as typed."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (SHEETS_EPOCH + timedelta(days=int(value))).isoformat()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return text


def sheet_day(value: str):
    """A lead's call_back for the sheet: a date as a Sheets date number (the column shows it as a date)."""
    try:
        return (date.fromisoformat(value) - SHEETS_EPOCH).days
    except (TypeError, ValueError):
        return value or ""


def _due(lead: "Lead", today: date) -> bool:
    try:
        return date.fromisoformat(lead.call_back) <= today
    except ValueError:
        return True                              # no date, or one written as text: call back now


def _last_email(lead: "Lead") -> datetime:
    return max(s.sent_at for s in lead.sent)


def _engaged(lead: "Lead") -> datetime | None:
    moments = [lead.site_last_visit_at, lead.site_opened_at,
               *(m.received_at for m in lead.inbound if m.kind == "reply")]
    moments = [m for m in moments if m is not None]
    return max(moments) if moments else None


def _group(lead: "Lead", today: date) -> int:
    outcome = lead.call_outcome
    if outcome == CALL_BACK:
        return 0 if _due(lead, today) else 4
    if not outcome:
        return 1 if _engaged(lead) is not None or lead.site_taps else 2
    if outcome in (NO_ANSWER, VOICEMAIL):
        return 3
    return 5


def callable_leads(campaigns: list["Campaign"]) -> list[tuple["Campaign", "Lead"]]:
    """Emailed businesses with a phone number, one per number (the most recently emailed), not unsubscribed."""
    best: dict[str, tuple["Campaign", "Lead"]] = {}
    for campaign in campaigns:
        for lead in getattr(campaign, "leads", []):
            digits = phone_digits(getattr(lead, "phone", ""))
            if len(digits) < 10 or not getattr(lead, "sent", None) or lead.status == LeadStatus.UNSUBSCRIBED:
                continue
            known = best.get(digits)
            if known is None or (lead.call_outcome and not known[1].call_outcome) or (
                    bool(lead.call_outcome) == bool(known[1].call_outcome) and _last_email(lead) > _last_email(known[1])):
                best[digits] = (campaign, lead)
    return list(best.values())


def ordered(campaigns: list["Campaign"], today: date) -> list[tuple[int, "Campaign", "Lead"]]:
    """(group, campaign, lead), in calling order."""
    far = datetime.max.replace(tzinfo=timezone.utc)

    def key(item):
        campaign, lead = item
        group = _group(lead, today)
        called = lead.called_at or far
        if group == 0:
            return group, lead.call_back, called
        if group == 1:
            return group, -(_engaged(lead) or _last_email(lead)).timestamp(), 0
        if group == 2:
            return group, -_last_email(lead).timestamp(), 0
        if group == 3:
            return group, called.timestamp() if lead.called_at else 0, 0
        if group == 4:
            return group, lead.call_back, 0
        return group, -(lead.called_at.timestamp() if lead.called_at else 0), 0

    return [(_group(lead, today), campaign, lead)
            for campaign, lead in sorted(callable_leads(campaigns), key=key)]


def calls_table(campaigns: list["Campaign"], cfg: "Settings", today: date | None = None) -> Table:
    today = today or datetime.now().astimezone().date()
    rows, number = [], 0
    for group, campaign, lead in ordered(campaigns, today):
        if group <= 3:
            number += 1
        rows.append([number if group <= 3 else "", lead.business or lead.name, show_phone(lead.phone), lead.city,
                     f"{cfg.SITES_URL.rstrip('/')}/{lead.site}/" if lead.site else "",
                     _local_day(_last_email(lead)), _opened(lead), _emails(campaign, lead),
                     lead.call_outcome, sheet_day(lead.call_back), lead.call_note, campaign.name, lead.id])
    return CALL_COLUMNS, rows


def summary(campaigns: list["Campaign"], cfg: "Settings", now: datetime) -> str:
    """The Status tab's Calls row: how many to call, call-backs due, interested - and when to call."""
    today = now.astimezone().date()
    groups = [group for group, _, lead in ordered(campaigns, today)]
    leads = [lead for _, _, lead in ordered(campaigns, today)]
    if not leads:
        return ""
    to_call = sum(group <= 3 for group in groups)
    due = groups.count(0)
    interested = sum(lead.call_outcome in (INTERESTED, SIGNED_UP) for lead in leads)
    text = f"{to_call} to call ({due} call-back{'' if due == 1 else 's'} due) · {interested} interested or signed up"
    window = call_window(cfg, today)
    return text + (f" · best {window} your time ({CALL_HOURS[0]}–{CALL_HOURS[1]}am theirs) – the Calls tab"
                   if window else " – the Calls tab")


def call_window(cfg: "Settings", day: date) -> str:
    """CALL_HOURS in the leads' time zone (OUTREACH_TIMEZONE) as this computer's time, e.g. '18:30–21:30'."""
    try:
        zone = ZoneInfo(cfg.OUTREACH_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ""
    start, end = (datetime.combine(day, time(hour), zone).astimezone() for hour in CALL_HOURS)
    return f"{start:%H:%M}–{end:%H:%M}"


def _opened(lead: "Lead") -> str:
    if lead.site_opened_at is None and not lead.site_taps:
        return ""
    parts = [_local_day(lead.site_opened_at)] if lead.site_opened_at else []
    if lead.site_visits:
        parts.append(f"{lead.site_visits} page{'' if lead.site_visits == 1 else 's'}")
    if lead.site_taps:
        parts.append("tapped " + ", ".join(lead.site_taps))
    return " · ".join(parts) or "yes"


def _emails(campaign: "Campaign", lead: "Lead") -> str:
    total = len(campaign.steps_of(lead))
    return {
        LeadStatus.SENT: f"{len(lead.sent)} of {total} sent",
        LeadStatus.DONE: lead.note if lead.call_outcome in STOP_EMAILS and lead.note else f"all {total} sent, no reply",
        LeadStatus.REPLIED: "replied",
        LeadStatus.BOUNCED: "bounced",
        LeadStatus.FAILED: "bad address",
        LeadStatus.SKIPPED: "left out",
    }.get(lead.status, lead.status.value)


def _local_day(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone().strftime("%Y-%m-%d")
