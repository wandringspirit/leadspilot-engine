"""The master sheet: every campaign on one page, and everything that happened (docs/AUTOPILOT.md).

Two CSV files Google Drive keeps in ``DRIVE_FOLDER`` (``app/drive/sync.py``), also downloadable from
the panel:

* **campaigns** - a row per campaign (an *All campaigns* row first): its status, how many businesses
  were emailed, are waiting, replied, bounced …, and how many emails went out;
* **activity** - the activity log, newest first: searches, campaigns, emails, replies, bounces.

Times are the computer's own. Pure functions (tests/test_autopilot.py).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from app.outreach.models import Campaign, LeadStatus

CAMPAIGN_COLUMNS = ["Campaign", "Business type", "Status", "Created", "Started", "Businesses", "Emailed",
                    "Waiting", "In sequence", "Replied", "No reply", "Bounced", "Unsubscribed", "Refused",
                    "Skipped", "Left out", "Emails sent", "Last email", "Results file"]
ACTIVITY_COLUMNS = ["When", "What", "Campaign", "Business", "Email", "Detail"]


def campaigns_csv(campaigns: list[Campaign]) -> bytes:
    rows = [_campaign_row(c) for c in sorted(campaigns, key=lambda c: c.created_at, reverse=True)]
    total = {column: "" for column in CAMPAIGN_COLUMNS}
    total.update(Campaign=f"All campaigns ({len(rows)})")
    for column in CAMPAIGN_COLUMNS[5:17]:
        total[column] = sum(row[column] for row in rows)
    total["Last email"] = max((row["Last email"] for row in rows if row["Last email"]), default="")
    return _csv(CAMPAIGN_COLUMNS, [total, *rows])


def activity_csv(entries: list[dict]) -> bytes:
    """``entries``: the activity log, newest first."""
    rows = [{"When": _local(entry.get("at", "")), "What": entry.get("what", ""), "Campaign": entry.get("campaign", ""),
             "Business": entry.get("business", ""), "Email": entry.get("email", ""), "Detail": entry.get("detail", "")}
            for entry in entries]
    return _csv(ACTIVITY_COLUMNS, rows)


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


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return (chr(0xFEFF) + buffer.getvalue()).encode("utf-8")
