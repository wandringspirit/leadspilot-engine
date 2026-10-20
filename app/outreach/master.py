"""The master sheet: every campaign on one page, and everything that happened (docs/AUTOPILOT.md).

Two tables, kept in Google Drive by ``app/drive/sync.py``: a Google Sheet with a tab for each, coloured
(``app/drive/sheets.py``), or - while the Sheets API isn't switched on - two CSV files. The panel also
offers them as CSV downloads:

* **Campaigns** - a row per campaign (an *All campaigns* row first): its status, how many businesses
  were emailed, are waiting, replied, bounced, opened their website …, and how many emails went out;
* **Activity** - the activity log, newest first: searches, campaigns, emails, replies, bounces.

Times are the computer's own. Pure functions (tests/test_autopilot.py).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from app.outreach.models import Campaign, LeadStatus

CAMPAIGN_COLUMNS = ["Campaign", "Business type", "Status", "Created", "Started", "Businesses", "Emailed",
                    "Waiting", "In sequence", "Replied", "No reply", "Bounced", "Unsubscribed", "Refused",
                    "Skipped", "Left out", "Opened website", "Tapped", "Emails sent", "Last email",
                    "Results file"]
COUNT_COLUMNS = CAMPAIGN_COLUMNS[5:19]
ACTIVITY_COLUMNS = ["When", "What", "Campaign", "Business", "Email", "Detail"]

Table = tuple[list[str], list[list]]          # (header, rows)


def campaigns_table(campaigns: list[Campaign]) -> Table:
    rows = [_campaign_row(c) for c in sorted(campaigns, key=lambda c: c.created_at, reverse=True)]
    total = {column: "" for column in CAMPAIGN_COLUMNS}
    total["Campaign"] = f"All campaigns ({len(rows)})"
    for column in COUNT_COLUMNS:
        total[column] = sum(row[column] for row in rows)
    total["Last email"] = max((row["Last email"] for row in rows if row["Last email"]), default="")
    return CAMPAIGN_COLUMNS, [[row[c] for c in CAMPAIGN_COLUMNS] for row in [total, *rows]]


def activity_table(entries: list[dict]) -> Table:
    """``entries``: the activity log, newest first."""
    return ACTIVITY_COLUMNS, [[_local(e.get("at", "")), e.get("what", ""), e.get("campaign", ""),
                               e.get("business", ""), e.get("email", ""), e.get("detail", "")] for e in entries]


def campaigns_csv(campaigns: list[Campaign]) -> bytes:
    return to_csv(campaigns_table(campaigns))


def activity_csv(entries: list[dict]) -> bytes:
    return to_csv(activity_table(entries))


def to_csv(table: Table) -> bytes:
    header, rows = table
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return (chr(0xFEFF) + buffer.getvalue()).encode("utf-8")


def _campaign_row(campaign: Campaign) -> dict:
    counts = campaign.counts()
    sent = [s.sent_at for lead in campaign.leads for s in lead.sent]
    return {
        "Campaign": campaign.name,
        "Business type": campaign.business_type_name,
        "Status": campaign.status.value + (f" ({campaign.error})" if campaign.error else ""),
        "Created": _local(campaign.created_at),
        "Started": _local(campaign.started_at),
        "Businesses": len(campaign.leads),
        "Emailed": sum(bool(lead.sent) for lead in campaign.leads),
        "Waiting": counts[LeadStatus.QUEUED.value],
        "In sequence": counts[LeadStatus.SENT.value],
        "Replied": counts[LeadStatus.REPLIED.value],
        "No reply": counts[LeadStatus.DONE.value],
        "Bounced": counts[LeadStatus.BOUNCED.value],
        "Unsubscribed": counts[LeadStatus.UNSUBSCRIBED.value],
        "Refused": counts[LeadStatus.FAILED.value],
        "Skipped": counts[LeadStatus.SKIPPED.value],
        "Left out": len(campaign.left_out),
        "Opened website": sum(lead.site_opened_at is not None for lead in campaign.leads),
        "Tapped": sum(bool(lead.site_taps) for lead in campaign.leads),
        "Emails sent": len(sent),
        "Last email": _local(max(sent)) if sent else "",
        "Results file": campaign.drive_link,
    }


def _local(moment: datetime | str | None) -> str:
    if not moment:
        return ""
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return moment
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
