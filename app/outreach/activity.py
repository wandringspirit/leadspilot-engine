"""What happened, in order: searches, campaigns, emails, replies (docs/AUTOPILOT.md).

One line per event in ``data/activity.jsonl`` - written by the outreach manager (campaigns, emails,
replies, bounces) and the autopilot (searches, its pauses). The panel shows the newest; the master
sheet (``app/outreach/master.py``) puts all of it in Google Drive.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class ActivityLog:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self.changed_at: datetime | None = None
        if path.is_file():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    self._entries.append(json.loads(line))
                except ValueError:
                    log.warning("%s line %d is unreadable - skipped", path.name, number)

    def add(self, what: str, detail: str = "", *, campaign: str = "", business: str = "", email: str = "") -> dict:
        """Write down one event: ``what`` is short ("Email sent"), ``detail`` says the rest."""
        entry = {"at": self.clock().isoformat(timespec="seconds"), "what": what, "detail": detail,
                 "campaign": campaign, "business": business, "email": email}
        with self._lock:
            self._entries.append(entry)
            self.changed_at = self.clock()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:  # pragma: no cover - the log must never stop the work
                log.warning("could not write %s: %s", self.path, exc)
        return entry

    def entries(self, limit: int | None = None) -> list[dict]:
        """Newest first."""
        with self._lock:
            newest = list(reversed(self._entries))
        return newest[:limit] if limit else newest
