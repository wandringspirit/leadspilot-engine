"""The send window: the days and hours emails go out, in the leads' time zone.

``OUTREACH_DAYS`` is a list of days and day ranges (``mon-fri``,
``mon,wed,fri``, ``mon-fri,sun``), ``OUTREACH_HOURS`` a range of whole hours
(``8-12`` = from 08:00 until 11:59), both in ``OUTREACH_TIMEZONE``. Pure
functions (tests/test_outreach.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class WindowError(ValueError):
    """A send-window setting that can't be understood; the message says which and why."""


@dataclass(frozen=True)
class SendWindow:
    tz: tzinfo
    tz_name: str
    days: frozenset[int]           # 0 = Monday
    start: int                     # first hour (inclusive)
    end: int                       # last hour (exclusive), up to 24

    @classmethod
    def parse(cls, tz_name: str, days: str, hours: str) -> "SendWindow":
        try:
            tz = ZoneInfo(tz_name.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise WindowError(f"unknown time zone {tz_name!r} (OUTREACH_TIMEZONE) - use a name like "
                              "America/Chicago; on Windows the time zones come with: pip install tzdata") from exc
        start, end = parse_hours(hours)
        return cls(tz=tz, tz_name=tz_name.strip(), days=parse_days(days), start=start, end=end)

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def local_date(self, moment: datetime) -> date:
        return self.local(moment).date()

    def is_open(self, moment: datetime) -> bool:
        t = self.local(moment)
        return t.weekday() in self.days and self.start <= t.hour < self.end

    def next_open(self, moment: datetime) -> datetime:
        """``moment`` when the window is open, else the next time it opens (UTC)."""
        if self.is_open(moment):
            return moment
        t = self.local(moment)
        for ahead in range(8):
            day = t.date() + timedelta(days=ahead)
            if day.weekday() not in self.days:
                continue
            opens = datetime.combine(day, time(self.start), self.tz)
            if opens > t:
                return opens.astimezone(timezone.utc)
        raise WindowError("the send window never opens")  # pragma: no cover - parse_days rejects no days

    def label(self) -> str:
        """'Mon–Fri 08:00–12:00 (America/Chicago)'."""
        return f"{days_label(self.days)} {self.start:02d}:00–{self.end:02d}:00 ({self.tz_name})"


def parse_days(text: str) -> frozenset[int]:
    days: set[int] = set()
    for part in (text or "").lower().replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        try:
            a = DAY_NAMES.index(first[:3])
            b = DAY_NAMES.index(last[:3]) if last else a
        except ValueError:
            raise WindowError(f"can't read OUTREACH_DAYS={text!r} - write days like mon-fri or mon,wed,fri") from None
        days.update((a + i) % 7 for i in range((b - a) % 7 + 1))
    if not days:
        raise WindowError("OUTREACH_DAYS has no days - write days like mon-fri")
    return frozenset(days)


def parse_hours(text: str) -> tuple[int, int]:
    first, sep, last = (text or "").replace(" ", "").partition("-")
    try:
        start, end = int(first), int(last)
    except ValueError:
        raise WindowError(f"can't read OUTREACH_HOURS={text!r} - write hours like 8-12") from None
    if not (sep and 0 <= start < end <= 24):
        raise WindowError(f"OUTREACH_HOURS={text!r} - write the first and the last hour like 8-12 (0 to 24)")
    return start, end


def days_label(days: frozenset[int]) -> str:
    """{0,1,2,3,4} -> 'Mon–Fri'; {0,2,4} -> 'Mon, Wed, Fri'."""
    ordered = sorted(days)
    runs: list[list[int]] = []
    for d in ordered:
        if runs and d == runs[-1][-1] + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    names = [n.capitalize() for n in DAY_NAMES]
    parts = [f"{names[r[0]]}–{names[r[-1]]}" if len(r) > 2 else ", ".join(names[d] for d in r) for r in runs]
    return "every day" if len(days) == 7 else ", ".join(parts)


def day_label(day: date) -> str:
    """date(2026, 9, 29) -> '29 Sep 2026' (the same in every locale)."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
