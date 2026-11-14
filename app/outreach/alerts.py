"""Emails to you about the panel itself, so it doesn't need watching (docs/OUTREACH.md §9).

* **Problems** as they happen: the activity log's problem events (``PROBLEMS``) - the autopilot stopped (it
  also stops on too many bounces), Google blocked a search, searches listing nothing (Google quietly limiting
  the laptop), the web check refused, a campaign paused (not by you), an email refused,
  a campaign not made or not started - and, on the cloud server, the laptop not asking for searches for
  ``SILENCE_H`` hours. Collected and sent together: at most one email every ``GAP_S``.
* **A daily summary** at ``SUMMARY_HOUR`` (the computer's time): yesterday's emails and what came back, the
  campaigns, the next email, the autopilot, the laptop, Google Drive and the last day's problems.
* The same as rows (``status_rows``) for the master sheet's **Status** tab (app/drive/sheets.py).

They go to ``ALERT_TO`` (else the From address) through ``OutreachManager.email_me``. Google Drive
disconnecting has its own email (main.py). What is still to be sent is kept in ``data/alerts.json``, so a
restart loses nothing and doesn't send a day's summary twice.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app.config import Settings
from app.drive.backup import KEEP as KEEP_BACKUPS
from app.outreach.calls import summary as calls_summary
from app.outreach.master import DAILY_COLUMNS, daily_table

log = logging.getLogger(__name__)

PROBLEMS = {"Autopilot stopped", "Search blocked", "Searches empty", "Web check refused", "Campaign paused",
            "Email refused", "Campaign not made", "Campaign not started"}
LAPTOP_SILENT = "Laptop not searching"
GAP_S = 30 * 60          # at most one problem email this often
LOOP_S = 60
SILENCE_H = 24           # ROLE=sender: the laptop hasn't asked for a search for this long
SUMMARY_HOUR = 8         # the daily summary goes out at 08:00 (the computer's time) ...
RETRY_S = 30 * 60        # ... and again this long after it couldn't
QUEUE_KEPT = 50

#: what to do about each problem - the email says it under the list
ADVICE = {
    "Autopilot stopped": "The autopilot doesn't start again by itself: fix the cause, then switch it on in the "
                         "cloud panel (Outreach › Autopilot).",
    "Search blocked": "Google showed its 'unusual traffic' page to the laptop: wait a day before searching again.",
    "Searches empty": "Google Maps showed the laptop no businesses at all, search after search - most likely Google "
                      "quietly limiting it (no captcha). The autopilot stopped and those places are first in line "
                      "again: wait a day, then switch it on in the cloud panel (Outreach › Autopilot).",
    "Web check refused": "DuckDuckGo and Bing refused the laptop's searches for websites Google Maps doesn't list "
                         "(or couldn't be reached), so some businesses were checked only by their email domain and "
                         "social page - a few may have a website after all. Nothing stopped; it usually clears by "
                         "the next search. If it comes with every search, the searches need spacing out.",
    "Campaign paused": "A paused campaign sends nothing until it is started again (Outreach › the campaign › Start).",
    "Email refused": "The mail server refused that address: nothing to do, the business isn't emailed again.",
    "Campaign not made": "The search's businesses are kept: make the campaign by hand, or let the next search go on.",
    "Campaign not started": "Start it by hand once the cause is fixed (Outreach › the campaign › Start).",
    LAPTOP_SILENT: "Is the laptop on, awake and online? Its panel starts with Windows (the Startup shortcut). "
                   "Searches wait in the cloud until it asks again; sending isn't affected.",
}

EmailMe = Callable[[str, str], Awaitable[bool]]
WARN = "\u26a0"         # a value that needs a look (the Status tab colours its row red)


def _n(count: int, word: str, plural: str = "") -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _local(moment: datetime) -> str:
    return moment.astimezone().strftime("%a %d %b, %H:%M")


def _parse(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Alerts:
    def __init__(self, cfg: Settings, email_me: EmailMe, status: Callable[[], dict], *,
                 last_contact: Callable[[], datetime | None] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        """``status()``: what the summary reports - {"campaigns", "outreach", "autopilot", "drive", "remote"}
        (main.py). ``last_contact()``: ROLE=sender - when the laptop last asked (None: not since the start)."""
        self.cfg = cfg
        self.email_me = email_me
        self.status = status
        self.last_contact = last_contact
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = self.clock()
        self._task: asyncio.Task | None = None
        state = self._load()
        self.queue: list[dict] = list(state.get("queue", []))
        self.last_sent = _parse(state.get("last_sent", ""))
        self.summary_on: str = state.get("summary_on", "")        # the day the last summary went out
        self.summary_tried = _parse(state.get("summary_tried", ""))
        self.laptop_silent = bool(state.get("laptop_silent", False))
        self.recent: list[dict] = list(state.get("recent", []))   # the last day's problems (the summary)

    # ------------------------------------------------------------------
    # Its file
    # ------------------------------------------------------------------
    @property
    def _file(self) -> Path:
        return self.cfg.DATA_DIR / "alerts.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("unreadable %s (%s) - starting afresh", self._file, exc)
            return {}

    def _save(self) -> None:
        state = {"queue": self.queue, "last_sent": self.last_sent.isoformat() if self.last_sent else "",
                 "summary_on": self.summary_on,
                 "summary_tried": self.summary_tried.isoformat() if self.summary_tried else "",
                 "laptop_silent": self.laptop_silent, "recent": self.recent}
        with suppress(OSError):
            self._file.parent.mkdir(parents=True, exist_ok=True)
            partial = self._file.with_suffix(".json.part")
            partial.write_text(json.dumps(state), encoding="utf-8")
            partial.replace(self._file)

    # ------------------------------------------------------------------
    # Problems
    # ------------------------------------------------------------------
    def note(self, entry: dict) -> None:
        """Every activity log entry (ActivityLog.listeners): keep the problems for the next email."""
        what = entry.get("what", "")
        if what not in PROBLEMS or (what == "Campaign paused" and entry.get("detail") == "by you"):
            return
        self._add({key: entry.get(key, "") for key in ("at", "what", "detail", "campaign", "business")})

    def _add(self, problem: dict) -> None:
        self.queue = (self.queue + [problem])[-QUEUE_KEPT:]
        self.recent = (self.recent + [problem])[-QUEUE_KEPT:]
        self._save()

    def _check_laptop(self, now: datetime) -> None:
        if self.cfg.ROLE != "sender" or self.last_contact is None:
            return
        contact = self.last_contact()
        since = contact or self.started_at
        if now - since < timedelta(hours=SILENCE_H):
            if contact is not None and self.laptop_silent:
                self.laptop_silent = False
                self._save()
            return
        if not self.laptop_silent:
            self.laptop_silent = True
            when = f"since {_local(contact)}" if contact else f"since the cloud panel started ({_local(since)})"
            self._add({"at": now.isoformat(timespec="seconds"), "what": LAPTOP_SILENT,
                       "detail": f"the laptop hasn't asked the cloud server for a search {when}",
                       "campaign": "", "business": ""})

    @staticmethod
    def problems_text(problems: list[dict]) -> str:
        lines = []
        for p in problems:
            at = _parse(p.get("at", ""))
            where = " · ".join(x for x in (p.get("campaign"), p.get("business")) if x)
            lines.append(f"• {_local(at) if at else ''} – {p.get('what')}"
                         + (f" ({where})" if where else "") + (f": {p['detail']}" if p.get("detail") else ""))
        advice = [ADVICE[w] for w in dict.fromkeys(p.get("what") for p in problems) if w in ADVICE]
        return "\n".join(lines) + ("\n\nWhat to do:\n" + "\n".join(f"- {a}" for a in advice) if advice else "")

    async def _send_problems(self, now: datetime) -> None:
        if not self.queue or (self.last_sent and (now - self.last_sent).total_seconds() < GAP_S):
            return
        problems = list(self.queue)
        whats = list(dict.fromkeys(p["what"] for p in problems))
        subject = whats[0] if len(problems) == 1 else f"{len(problems)} problems: " + ", ".join(whats)
        body = ("Something needs a look:\n\n" + self.problems_text(problems)
                + "\n\nThe master sheet's Activity tab has the details.")
        if await self.email_me(subject, body):
            log.info("Emailed you about %d problem(s)", len(problems))
            self.queue = self.queue[len(problems):]
            self.last_sent = now
            self._save()

    # ------------------------------------------------------------------
    # The daily summary
    # ------------------------------------------------------------------
    def _recent(self, now: datetime) -> list[dict]:
        day_ago = now - timedelta(days=1)
        return [p for p in self.recent if (_parse(p.get("at", "")) or now) >= day_ago]

    @staticmethod
    def _day(campaigns: list, day) -> str:
        _, rows = daily_table(campaigns, today=day)
        counts = dict.fromkeys(DAILY_COLUMNS[1:], 0)
        for row in rows:
            if row[0] == day.isoformat():
                counts = dict(zip(DAILY_COLUMNS[1:], row[1:]))
        sent = counts["First emails"] + counts["Follow-ups"]
        return (f"{_n(sent, 'email')} sent ({counts['First emails']} first, {counts['Follow-ups']} follow-ups) · "
                f"{_n(counts['Replies'], 'reply', 'replies')} · {_n(counts['Websites opened'], 'website')} opened · "
                f"{_n(counts['Bounces'], 'bounce')} · {_n(counts['Unsubscribes'], 'unsubscribe')}")

    def status_rows(self, now: datetime) -> list[list[str]]:
        """How things stand, a [what, now] row each - the master sheet's Status tab and the daily summary. A
        value that needs a look starts with WARN."""
        info = self.status()
        local = now.astimezone()
        sender = self.cfg.ROLE == "sender"
        rows = [["Updated", f"{_local(now)} – written every few minutes while the "
                            f"{'cloud server' if sender else 'panel'} runs; an old time here means it isn't running"]]

        outreach = info["outreach"]
        if outreach.get("problems"):
            sending = f"{WARN} NOT READY – " + "; ".join(outreach["problems"])
        elif not outreach.get("autosend"):
            sending = f"{WARN} OFF – OUTREACH_AUTOSEND is false, nothing is sent"
        else:
            nxt = _parse(outreach.get("next_send_at") or "")
            sending = (f"on – next email {_local(nxt) if nxt else 'not planned'} · "
                       f"{outreach.get('sent_today', 0)} of {outreach.get('daily_limit')} sent today")
        rows.append(["Sending", sending])
        verify = info.get("verify") or {}
        if verify:
            if not verify.get("on"):
                check = "off – only the domain is checked (EMAIL_VERIFY_KEY isn't set)"
            elif verify.get("error"):
                at = _parse(verify.get("error_at") or "")
                check = (f"{WARN} the verifier didn't answer{' at ' + _local(at) if at else ''}: {verify['error']} – "
                         f"addresses are emailed unchecked until it does")
            else:
                check = (f"on – each address is checked with Reoon before its first email · "
                         f"{verify.get('checked', 0)} checked, {verify.get('skipped', 0)} skipped since the panel started")
            rows.append(["Address check", check])
        campaigns = info["campaigns"]
        rows.append(["Today", self._day(campaigns, local.date())])
        rows.append(["Yesterday", self._day(campaigns, (local - timedelta(days=1)).date())])
        running = sum(c.status.value == "running" for c in campaigns)
        rows.append(["Campaigns", f"{running} running, {len(campaigns)} in all · "
                                  f"{_n(outreach.get('contacted', 0), 'business', 'businesses')} emailed so far"])
        calls = calls_summary(campaigns, self.cfg, now)
        if calls:
            rows.append(["Calls", calls])

        auto = info["autopilot"]
        if auto.get("stopped"):
            autopilot = f"{WARN} STOPPED – {auto['stopped']}"
        elif not auto.get("enabled"):
            autopilot = "off"
        else:
            doing = f"searching {auto['searching']}" if auto.get("searching") else f"next search {auto.get('next') or '–'}"
            autopilot = (f"on – {_n(auto.get('waiting', 0), 'business', 'businesses')} waiting for a first email · "
                         f"{doing} · {auto.get('searches_today', 0)} of {auto.get('searches_per_day', 0)} searches today")
        rows.append(["Autopilot", autopilot])

        remote = info.get("remote") or {}
        if remote.get("role") == "sender":
            contact = _parse(remote.get("last_contact") or "")
            quiet = now - (contact or self.started_at) >= timedelta(hours=SILENCE_H)
            if contact is None:
                laptop = f"hasn't asked for a search since the cloud panel started ({_local(self.started_at)})"
            else:
                doing = f"searching {', '.join(remote['searching'])}" if remote.get("searching") else "waiting for a search"
                laptop = f"last asked {_local(contact)} – {doing}"
            rows.append(["Laptop", (f"{WARN} " if quiet else "") + laptop + (" – is it on?" if quiet else "")])

        drive = info["drive"]
        if drive.get("connected"):
            rows.append(["Google Drive", f"connected ({drive.get('email') or 'your account'})"])
        elif drive.get("configured"):
            lost = _parse(drive.get("lost_at") or "")
            rows.append(["Google Drive", f"{WARN} DISCONNECTED" + (f" since {_local(lost)}" if lost else "")
                         + (" – connect it in the laptop's panel" if sender else " – connect it in the Outreach tab")])

        backup = drive.get("backup") or {}
        if drive.get("connected") and "backup" in drive:
            last = _parse(backup.get("at") or "")
            stale = last is None or now - last > timedelta(hours=48)
            text = (f"{_local(last)} – {backup.get('name')} ({max(1, backup.get('size', 0) // 1024)} KB) in Google "
                    f"Drive › Backups, the newest {KEEP_BACKUPS} kept" if last else "none yet - the first one is made after 03:00")
            if backup.get("error"):
                text += f" – the last try failed: {backup['error']}"
            rows.append(["Backup", (f"{WARN} " if stale and last else "") + text])

        recent = self._recent(now)
        whats = list(dict.fromkeys(p.get("what", "") for p in recent))
        rows.append(["Problems (last day)", "none" if not recent else
                     f"{WARN} {len(recent)} – " + ", ".join(whats) + " – the Activity tab has them (red rows)"])
        return rows

    def summary_text(self, now: datetime) -> str:
        local = now.astimezone()
        rows = dict(self.status_rows(now))
        yesterday = (local - timedelta(days=1)).strftime("%a %d %b")
        lines = [f"Leads Pilot – {local.strftime('%A %d %b %Y')}", "",
                 f"Yesterday ({yesterday}): {rows['Yesterday']}", ""]
        lines += [f"{label}: {rows[label]}" for label in ("Sending", "Address check", "Campaigns", "Calls", "Autopilot", "Laptop", "Google Drive",
                                                          "Backup")
                  if label in rows]
        recent = self._recent(now)
        lines += ["", f"Problems in the last day: {len(recent) or 'none'}"]
        if recent:
            lines.append(self.problems_text(recent))
        return "\n".join(lines)

    async def _send_summary(self, now: datetime) -> None:
        local = now.astimezone()
        today = local.date().isoformat()
        if local.hour < SUMMARY_HOUR or self.summary_on == today:
            return
        if self.summary_tried and (now - self.summary_tried).total_seconds() < RETRY_S:
            return
        self.summary_tried = now
        if await self.email_me(f"Daily summary – {local.strftime('%a %d %b')}", self.summary_text(now)):
            log.info("Daily summary emailed to you")
            self.summary_on = today
            day_ago = now - timedelta(days=1)
            self.recent = [p for p in self.recent if (_parse(p.get("at", "")) or now) >= day_ago]
        self._save()

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    async def step(self) -> None:
        now = self.clock()
        self._check_laptop(now)
        await self._send_problems(now)
        await self._send_summary(now)

    def start_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="alerts")

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(LOOP_S)
            try:
                await self.step()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Alerts: unexpected error")
