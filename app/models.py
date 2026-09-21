"""Pydantic models shared by the scraper, the job manager, the API and the CLI.

`PlaceRow` is the single source of truth for what a result row looks like.
The CSV column order lives in `app/export/csv_export.py` and is documented in
docs/DATA_MODEL.md - keep the three in sync (tests/test_docs.py checks it).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class PlaceRow(BaseModel):
    """One row of output. A place can produce several rows (one per email)."""

    row_id: str = Field(default_factory=lambda: new_id("r_"))
    rank: int = 0                      # 1-based position in the Maps results
    name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""                  # ISO-3166 alpha-2 (e.g. "US")
    email: str = ""
    website: str = ""
    phone: str = ""                    # national format, e.g. "(817) 606-8303"
    phone_unformatted: str = ""        # E.164, e.g. "+18176068303"
    url: str = ""                      # https://www.google.com/maps/search/?api=1&query=...
    review_count: int | None = None
    description: str = ""

    # --- extra fields (exported only with `extended=1`) -----------------
    address: str = ""
    category: str = ""
    rating: float | None = None
    place_id: str = ""
    maps_url: str = ""                 # the /maps/place/... link we visited
    latitude: float | None = None
    longitude: float | None = None
    email_source: str = ""             # page where the email was found
    phone_source: str = ""             # "maps" | "website"
    enrichment_status: str = ""        # "" | pending | done | not_found | no_website | error
    enrichment_note: str = ""

    def clone_with_email(self, email: str, source: str) -> "PlaceRow":
        data = self.model_dump()
        data.update(row_id=new_id("r_"), email=email, email_source=source)
        return PlaceRow(**data)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    ENRICHING = "enriching"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"   # server restarted while the job was running


class Progress(BaseModel):
    phase: str = "queued"           # queued | collecting | details | enriching | done
    message: str = ""
    current: int = 0
    total: int = 0
    percent: float = 0.0


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=200,
                       examples=["roofing companies near houston"])
    limit: int = Field(20, ge=1, le=500, description="Number of places wanted")
    enrich: bool = Field(False, description="Look up emails/phones on websites after scraping")
    headless: bool | None = Field(None, description="Override HEADLESS setting for this job")


class EnrichRequest(BaseModel):
    row_ids: list[str] | None = Field(
        None, description="Rows to enrich. Omit to enrich every row missing an email or phone.")
    force: bool = Field(False, description="Re-run even if the row already has an email")


class Job(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job_"))
    query: str
    limit: int
    enrich: bool = False
    status: JobStatus = JobStatus.QUEUED
    progress: Progress = Field(default_factory=Progress)
    rows: list[PlaceRow] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict)

    # ---- helpers --------------------------------------------------------
    def touch(self) -> None:
        self.updated_at = _now()

    def summary(self) -> "JobSummary":
        return JobSummary(
            id=self.id, query=self.query, limit=self.limit, status=self.status,
            progress=self.progress, row_count=len(self.rows),
            created_at=self.created_at, updated_at=self.updated_at,
            finished_at=self.finished_at, error=self.error,
        )


class LogEntry(BaseModel):
    seq: int
    ts: datetime
    level: str
    logger: str
    message: str
    job_id: str | None = None


class LogsResponse(BaseModel):
    entries: list[LogEntry]
    next: int = Field(description="Pass as `since` on the next poll")
    first_seq: int = Field(description="Oldest sequence number still in the buffer")
    last_seq: int
    truncated: bool = Field(description="More matching lines are waiting - poll again immediately")


class JobSummary(BaseModel):
    id: str
    query: str
    limit: int
    status: JobStatus
    progress: Progress
    row_count: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
