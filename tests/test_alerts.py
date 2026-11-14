"""Emails to you: problems as they happen, a daily summary, the laptop going quiet (app/outreach/alerts.py)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import Settings
from app.outreach.activity import ActivityLog
from app.outreach.alerts import BUSY_SILENCE_H, GAP_S, LAPTOP_SILENT, SILENCE_H, SUMMARY_HOUR, Alerts


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class Inbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.works = True

    async def __call__(self, subject: str, body: str) -> bool:
        if self.works:
            self.sent.append((subject, body))
        return self.works


def local(hour: int, day: int = 12) -> datetime:
    """That hour on 2026-11-<day>, the computer's own time (the summary goes by it)."""
    return datetime(2026, 11, day, hour, 0).astimezone()


def status(**overrides) -> dict:
    sent = SimpleNamespace(step=1, sent_at=local(20, day=11))
    lead = SimpleNamespace(sent=[sent], inbound=[], site_opened_at=None)
    campaign = SimpleNamespace(status=SimpleNamespace(value="running"), leads=[lead])
    info = {"campaigns": [campaign],
            "outreach": {"problems": [], "autosend": True, "next_send_at": local(19).isoformat(),
                         "daily_limit": 10, "sent_today": 0, "contacted": 49},
            "autopilot": {"enabled": True, "stopped": "", "waiting": 2, "searching": "", "next": "Plano, TX",
                          "searches_today": 1, "searches_per_day": 3},
            "drive": {"configured": True, "connected": True, "email": "me@gmail.com", "lost_at": ""},
            "remote": {"role": "sender", "last_contact": local(7).isoformat(), "searching": []}}
    info.update(overrides)
    return info


def make(tmp_path, at: datetime, *, role: str = "sender", contact=None, **status_overrides):
    cfg = Settings(_env_file=None, DATA_DIR=tmp_path, ROLE=role)
    clock, inbox = Clock(at), Inbox()
    alerts = Alerts(cfg, inbox, lambda: status(**status_overrides), last_contact=lambda: contact, clock=clock)
    return alerts, clock, inbox


def test_the_laptop_going_quiet_during_a_search_is_reported_after_an_hour(tmp_path):
    contact = local(9, day=10)
    alerts, clock, inbox = make(tmp_path, contact + timedelta(minutes=50), contact=contact)
    waiting = [1]
    alerts.searches_for_laptop = lambda: waiting[0]
    asyncio.run(alerts.step())
    silent = lambda: [b for s, b in inbox.sent if s == LAPTOP_SILENT]  # noqa: E731
    assert silent() == []                                               # 50 minutes: fine
    clock.at = contact + timedelta(hours=BUSY_SILENCE_H, minutes=1)
    waiting[0] = 0
    asyncio.run(alerts.step())
    assert silent() == []                                               # nothing for it to do: a day's grace
    waiting[0] = 1
    asyncio.run(alerts.step())
    assert len(silent()) == 1 and "a search is waiting for it" in silent()[0]


def test_problems_are_emailed_together_at_most_every_half_hour(tmp_path):
    alerts, clock, inbox = make(tmp_path, local(7), contact=local(7))
    alerts.summary_on = local(7).date().isoformat()                     # no summary in this test
    activity = ActivityLog(tmp_path / "activity.jsonl", clock=clock)
    activity.listeners.append(alerts.note)
    activity.add("Email sent", "email 1 of 3")                          # not a problem
    activity.add("Campaign paused", "by you", campaign="Roofer · Katy, TX")   # you did it
    activity.add("Autopilot stopped", "2 of the last 20 businesses emailed bounced")
    activity.add("Campaign paused", "Paused by the autopilot", campaign="Roofer · Katy, TX")
    asyncio.run(alerts.step())
    assert len(inbox.sent) == 1
    subject, body = inbox.sent[0]
    assert subject == "2 problems: Autopilot stopped, Campaign paused"
    assert "bounced" in body and "Roofer · Katy, TX" in body and "switch it on" in body and "by you" not in body
    assert alerts.queue == []

    activity.add("Email refused", "email 1: 550 no such user", business="Acme Roofing")
    clock.at += timedelta(seconds=GAP_S - 60)
    asyncio.run(alerts.step())
    assert len(inbox.sent) == 1                                         # not yet: half an hour between them
    clock.at += timedelta(seconds=60)
    asyncio.run(alerts.step())
    assert inbox.sent[-1][0] == "Email refused"

    inbox.works = False                                                 # the mailbox is down: kept for later
    activity.add("Search blocked", "roofing in Plano, TX: captcha")
    clock.at += timedelta(seconds=GAP_S)
    asyncio.run(alerts.step())
    assert [p["what"] for p in Alerts(alerts.cfg, inbox, status).queue] == ["Search blocked"]   # kept across a restart


def test_the_laptop_going_quiet_for_a_day_is_reported_once(tmp_path):
    contact = local(7, day=10)
    alerts, clock, inbox = make(tmp_path, local(6, day=11), contact=contact)
    asyncio.run(alerts.step())
    assert inbox.sent == []                                             # 23 hours (and before 08:00): fine
    clock.at = contact + timedelta(hours=SILENCE_H, minutes=1)
    asyncio.run(alerts.step())
    assert inbox.sent[-1][0] == LAPTOP_SILENT and "Startup shortcut" in inbox.sent[-1][1]
    clock.at += timedelta(hours=2)
    asyncio.run(alerts.step())
    assert [s for s, _ in inbox.sent if s == LAPTOP_SILENT] == [LAPTOP_SILENT]   # once per silence
    assert not make(tmp_path / "laptop", clock.at, role="searcher", contact=contact)[0]._check_laptop(clock.at)


def test_the_daily_summary_goes_out_once_a_morning(tmp_path):
    alerts, clock, inbox = make(tmp_path, local(SUMMARY_HOUR - 1), contact=local(7))
    asyncio.run(alerts.step())
    assert inbox.sent == []                                             # before 08:00
    clock.at = local(SUMMARY_HOUR, day=12) + timedelta(minutes=1)
    asyncio.run(alerts.step())
    subject, body = inbox.sent[0]
    assert subject.startswith("Daily summary")
    assert "Yesterday (Wed 11 Nov): 1 email sent (1 first, 0 follow-ups) · 0 replies" in body
    assert "Campaigns: 1 running, 1 in all · 49 businesses emailed so far" in body
    assert "Sending: on – next email" in body
    assert "Autopilot: on" in body and "Plano, TX" in body and "Laptop: last asked" in body
    assert "Google Drive: connected" in body and "Problems in the last day: none" in body
    clock.at += timedelta(hours=3)
    asyncio.run(alerts.step())
    assert len(inbox.sent) == 1                                         # once a day ...
    again, _, inbox2 = make(tmp_path, clock.at, contact=local(7))
    asyncio.run(again.step())
    assert inbox2.sent == []                                            # ... also after a restart


def test_the_summary_says_what_is_wrong(tmp_path):
    alerts, _, _ = make(tmp_path, local(9), contact=None,
                        autopilot={"stopped": "Google blocked a search"},
                        drive={"configured": True, "connected": False, "lost_at": local(3).isoformat()},
                        outreach={"problems": ["MAIL_PASSWORD is empty"]},
                        remote={"role": "sender", "last_contact": None})
    body = alerts.summary_text(local(9))
    assert "Autopilot: ⚠ STOPPED – Google blocked a search" in body
    assert "Google Drive: ⚠ DISCONNECTED since" in body and "laptop's panel" in body
    assert "Sending: ⚠ NOT READY – MAIL_PASSWORD is empty" in body
    rows = dict(alerts.status_rows(local(9)))
    assert list(rows)[0] == "Updated" and rows["Problems (last day)"] == "none"
    assert rows["Today"].startswith("0 emails sent") and not rows["Laptop"].startswith("⚠")   # just started
    assert "hasn't asked for a search since the cloud panel started" in body
