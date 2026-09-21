"""In-memory ring buffer for log records, exposed to the panel as "Live logs".

`LogBuffer` is a standard `logging.Handler`: every record emitted anywhere in the
process (scraper, enricher, job manager, ...) is stored as a small dict with a
monotonically increasing sequence number, so clients can poll
``GET /api/logs?since=<seq>`` and receive only what is new.

Records are tagged with the job they belong to through `current_job_id`, a
`contextvars.ContextVar` that the job manager sets inside each job task.
asyncio copies the context into every task it creates, so the scraper's worker
tabs and the enricher's concurrent fetches inherit the tag automatically.
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

current_job_id: ContextVar[str | None] = ContextVar("current_job_id", default=None)

# Loggers that would drown the useful lines (one entry per HTTP request).
EXCLUDED_LOGGERS = ("uvicorn.access", "httpx", "httpcore", "hpack", "asyncio")

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
          "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.DEBUG)
        self.capacity = max(10, int(capacity))
        self._entries: deque[dict[str, Any]] = deque(maxlen=self.capacity)
        self._seq = 0
        self._lock = threading.Lock()

    # -- logging.Handler ----------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(EXCLUDED_LOGGERS):
            return
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            entry = {
                "seq": 0,
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "levelno": record.levelno,
                "logger": record.name,
                "message": message,
                "job_id": current_job_id.get(),
            }
            with self._lock:
                self._seq += 1
                entry["seq"] = self._seq
                self._entries.append(entry)
        except Exception:  # pragma: no cover - never let logging break the app
            self.handleError(record)

    # -- queries --------------------------------------------------------------
    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def entries(
        self,
        *,
        since: int | None = None,
        limit: int = 200,
        job_id: str | None = None,
        min_level: int | None = None,
    ) -> dict[str, Any]:
        """Return entries newer than ``since`` (oldest first).

        ``since=None`` returns the most recent ``limit`` entries instead (initial
        load). ``truncated`` tells the client to poll again immediately because
        more matching entries are waiting.
        """
        with self._lock:
            snapshot = list(self._entries)
            last_seq = self._seq
        first_seq = snapshot[0]["seq"] if snapshot else last_seq + 1

        def matches(e: dict[str, Any]) -> bool:
            if job_id and e["job_id"] != job_id:
                return False
            if min_level is not None and e["levelno"] < min_level:
                return False
            return True

        if since is None:
            selected = [e for e in snapshot if matches(e)][-limit:]
            truncated = False
        else:
            selected = [e for e in snapshot if e["seq"] > since and matches(e)]
            truncated = len(selected) > limit
            selected = selected[:limit]

        # When everything that matches has been returned the client may skip
        # straight to the newest sequence number (filtered-out lines included).
        next_seq = selected[-1]["seq"] if truncated else last_seq
        return {
            "entries": [{k: v for k, v in e.items() if k != "levelno"} for e in selected],
            "next": next_seq,
            "first_seq": first_seq,
            "last_seq": last_seq,
            "truncated": truncated,
        }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


log_buffer = LogBuffer()


def install(capacity: int | None = None, root: logging.Logger | None = None) -> LogBuffer:
    """Attach the buffer to the root logger (idempotent)."""
    root = root or logging.getLogger()
    if capacity and capacity != log_buffer.capacity:
        log_buffer.capacity = capacity
        with log_buffer._lock:
            log_buffer._entries = deque(log_buffer._entries, maxlen=capacity)
    if log_buffer not in root.handlers:
        root.addHandler(log_buffer)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return log_buffer
