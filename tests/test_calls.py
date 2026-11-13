"""The master sheet's Calls tab: who to phone, in what order, and what you noted coming back
(app/outreach/calls.py, OutreachManager.record_calls, MasterSheet.read_calls)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.drive.client import DriveError
from app.drive.sheets import MasterSheet
from app.outreach.calls import (CALL_COLUMNS, LEAD_COL, OUTCOME_COL, calls_table, parse_day, sheet_day, show_phone,
                                summary)
from app.outreach.models import Campaign, InboundEmail, Lead, LeadStatus, SentEmail
from tests.test_outreach import make_manager, make_settings

NOW = datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 13)


def lead(name, phone="(214) 555-0100", days_ago=1, **extra) -> Lead:
    sent = [SentEmail(step=1, sent_at=NOW - timedelta(days=days_ago), message_id=f"<{name}@x>", subject="s")]
    return Lead(business=name, email=f"{name.lower()}@x.com", phone=phone, city="Plano", sent=sent,
                status=LeadStatus.SENT, **extra)


def table(*leads: Lead, cfg=None, campaign_name="Roofer · Plano, TX"):
    campaign = Campaign(name=campaign_name, business_type_name="Roofer", leads=list(leads))
    header, rows = calls_table([campaign], cfg, today=TODAY)
    assert header == CALL_COLUMNS
    return campaign, rows


def test_the_order_is_call_backs_due_then_interested_looking_then_newest_then_no_answer(tmp_path):
    cfg = make_settings(tmp_path)
    leads = [
        lead("Old", "214-555-0001", days_ago=9),
        lead("New", "214-555-0002", days_ago=1),
        lead("Looked", "214-555-0003", days_ago=8, site_opened_at=NOW - timedelta(days=2), site_visits=3),
        lead("Missed", "214-555-0004", call_outcome="No answer", called_at=NOW - timedelta(hours=3)),
        lead("Later", "214-555-0005", call_outcome="Call back", call_back="2026-11-20"),
        lead("Due", "214-555-0006", call_outcome="Call back", call_back="2026-11-12"),
        lead("Said no", "214-555-0007", call_outcome="Not interested", called_at=NOW),
        lead("No phone", ""),
        lead("Stop", "214-555-0008", ),
        Lead(business="Never emailed", email="n@x.com", phone="214-555-0009"),
    ]
    leads[8].status = LeadStatus.UNSUBSCRIBED
    _, rows = table(*leads, cfg=cfg)
    assert [r[1] for r in rows] == ["Due", "Looked", "New", "Old", "Missed", "Later", "Said no"]
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, "", ""]          # only the ones to call are numbered
    looked = rows[1]
    assert looked[2] == "(214) 555-0003" and looked[6].endswith("3 pages") and looked[7] == "1 of 3 sent"
    assert rows[5][9] == (date(2026, 11, 20) - date(1899, 12, 30)).days   # a date the sheet shows as one


def test_one_row_per_phone_number(tmp_path):
    _, rows = table(lead("Acme", "+1 214 555 0100", days_ago=5), lead("Acme Roofing", "(214) 555-0100", days_ago=1),
                    cfg=make_settings(tmp_path))
    assert [r[1] for r in rows] == ["Acme Roofing"]


def test_dates_from_the_sheet():
    assert parse_day(46343) == "2026-11-17" and sheet_day("2026-11-17") == 46343
    assert parse_day("2026-11-17") == "2026-11-17" and parse_day("next Tue") == "next Tue"
    assert sheet_day("next Tue") == "next Tue" and parse_day("") == ""
    assert show_phone("+1 (214) 555-0100") == "(214) 555-0100"


def test_the_status_row_says_how_many_and_when(tmp_path):
    cfg = make_settings(tmp_path)
    campaign, _ = table(lead("A", "214-555-0001"), lead("B", "214-555-0002", call_outcome="Interested"), cfg=cfg)
    text = summary([campaign], cfg, NOW)
    assert text.startswith("1 to call (0 call-backs due) · 1 interested or signed up · best ")
    assert "(7–10am theirs)" in text
    assert summary([], cfg, NOW) == ""


async def test_what_you_note_is_kept_and_interested_stops_the_emails(tmp_path):
    manager, _ = make_manager(tmp_path)
    a, b = lead("A", "214-555-0001"), lead("B", "214-555-0002")
    campaign = Campaign(name="Roofer · Plano, TX", leads=[a, b])
    manager.campaigns[campaign.id] = campaign
    changed = await manager.record_calls({
        a.id: {"outcome": "Interested", "call_back": "", "note": "wants it for the form"},
        b.id: {"outcome": "Call back", "call_back": "2026-11-17", "note": ""},
        "l_gone": {"outcome": "No answer", "call_back": "", "note": ""},
    })
    assert changed == 2
    assert a.call_outcome == "Interested" and a.call_note == "wants it for the form" and a.called_at is not None
    assert a.status == LeadStatus.DONE and a.next_due is None and "no more emails" in a.note
    assert b.status == LeadStatus.SENT and b.call_back == "2026-11-17"             # a call-back keeps the emails
    # the same values again: nothing changes
    assert await manager.record_calls({a.id: {"outcome": "Interested", "call_back": "",
                                              "note": "wants it for the form"}}) == 0


class Response:
    def __init__(self, data):
        self.data, self.content = data, b"{}"

    def json(self):
        return self.data


class SheetClient:
    def __init__(self, values=None, error=None):
        self.values, self.error = values, error

    async def request(self, method, url, **kwargs):
        if self.error:
            raise self.error
        return Response({"values": self.values})


async def test_the_calls_tab_is_read_back(tmp_path):
    row = [""] * len(CALL_COLUMNS)
    row[OUTCOME_COL], row[OUTCOME_COL + 1], row[OUTCOME_COL + 2], row[LEAD_COL] = "Call back", 46343, " ring at 8 ", "l_1"
    sheet = MasterSheet(SheetClient([CALL_COLUMNS, row, ["", "no lead id"]]), tmp_path / "s.json")
    assert await sheet.read_calls() == {}                             # no sheet yet
    sheet.state = {"id": "s1", "format": 5}
    assert await sheet.read_calls() == {"l_1": {"outcome": "Call back", "call_back": "2026-11-17", "note": "ring at 8"}}
    sheet.client = SheetClient([["Something", "else"], row])         # a layout this panel didn't write
    assert await sheet.read_calls() == {}
    sheet.client = SheetClient(error=DriveError("Unable to parse range: Calls!A1:Z", 400))   # no Calls tab yet
    assert await sheet.read_calls() == {}


def test_replies_count_as_interest(tmp_path):
    replied = lead("Replied", "214-555-0001", days_ago=9,
                   inbound=[InboundEmail(kind="reply", received_at=NOW - timedelta(days=1))])
    _, rows = table(lead("New", "214-555-0002"), replied, cfg=make_settings(tmp_path))
    assert [r[1] for r in rows] == ["Replied", "New"]
