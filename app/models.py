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

from pydantic import BaseModel, Field, computed_field, model_validator

from app.filters import SearchFilters
from app.scraper.parsing import website_type as classify_website


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def build_query(business: str, place: str = "") -> str:
    """The text typed into Google Maps: 'plumbers' + 'Austin, TX' -> 'plumbers in Austin, TX'."""
    business, place = business.strip(), place.strip()
    return f"{business} in {place}" if business and place else business


def split_places(place: str) -> list[str]:
    """'Katy, TX; Pasadena, TX' -> ['Katy, TX', 'Pasadena, TX'] (';' or new lines separate places)."""
    out: list[str] = []
    for part in (place or "").replace("\n", ";").split(";"):
        part = " ".join(part.split())
        if part and part not in out:
            out.append(part)
    return out


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def website_type(self) -> str:
        """none | own | profile (a Facebook / Yelp / booking ... page, see PROFILE_DOMAINS)."""
        return classify_website(self.website)

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
    BLOCKED = "blocked"           # Google showed its "unusual traffic" check; rows found before are kept


class Progress(BaseModel):
    phase: str = "queued"           # queued | collecting | details | enriching | done
    message: str = ""
    current: int = 0
    total: int = 0
    percent: float = 0.0


class ScrapeRequest(BaseModel):
    business: str = Field("", max_length=120, examples=["plumbers"],
                          description="What to look for: a business type or keyword")
    place: str = Field("", max_length=500, examples=["Austin, TX"],
                       description="Where to look: a city, area or address - several separated by ';'")
    whole_area: bool = Field(False, description="Search each place section by section instead of one "
                                                "search, to get past Google's ~120 results per search")
    query: str = Field("", max_length=200, examples=["roofing companies near houston"],
                       description="A complete free-text search, used when `business` is empty")
    limit: int = Field(20, ge=1, le=500, description="Number of matching places wanted")
    filters: SearchFilters = Field(default_factory=SearchFilters)
    enrich: bool = Field(False, description="Look up emails/phones on websites after scraping")
    headless: bool | None = Field(None, description="Override HEADLESS setting for this job")
    business_type_id: str = Field("", max_length=40, description="The saved business type this search is for "
                                                                 "(its emails are used for a campaign)")

    @model_validator(mode="after")
    def _needs_a_search(self) -> "ScrapeRequest":
        self.business, self.place, self.query = self.business.strip(), self.place.strip(), self.query.strip()
        if not self.business:
            self.place = ""  # a free-text query already says where
        if len(self.search_query) < 2:
            raise ValueError("enter a business (e.g. 'plumbers') and a place (e.g. 'Austin, TX'), or a query")
        if self.whole_area and not split_places(self.place):
            raise ValueError("searching the whole area needs a business and a place")
        return self

    @property
    def search_query(self) -> str:
        return build_query(self.business, self.place) if self.business else self.query


class EnrichRequest(BaseModel):
    row_ids: list[str] | None = Field(
        None, description="Rows to enrich. Omit to enrich every row missing an email or phone.")
    force: bool = Field(False, description="Re-run even if the row already has an email")


class Job(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job_"))
    query: str                          # the search sent to Google Maps
    business: str = ""                  # empty for free-text queries (and jobs from before 1.2)
    place: str = ""                     # one place, or several separated by ';'
    whole_area: bool = False            # each place searched section by section
    business_type_id: str = ""          # the saved business type the search was for ('' = typed in)
    limit: int
    filters: SearchFilters = Field(default_factory=SearchFilters)
    enrich: bool = False
    status: JobStatus = JobStatus.QUEUED
    progress: Progress = Field(default_factory=Progress)
    rows: list[PlaceRow] = Field(default_factory=list)
    error: str | None = None
    notice: str = ""                    # why fewer places than `limit` were found ('' otherwise)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    drive_file_id: str = ""             # the CSV's copy in Google Drive (docs/DRIVE.md)
    drive_link: str = ""
    drive_path: str = ""                # e.g. "Roofer/roofing_in_houston_no_website_email_29_09_2026.csv"

    # ---- helpers --------------------------------------------------------
    def touch(self) -> None:
        self.updated_at = _now()

    def summary(self) -> "JobSummary":
        return JobSummary(
            id=self.id, query=self.query, business=self.business, place=self.place,
            whole_area=self.whole_area, business_type_id=self.business_type_id, filters=self.filters,
            limit=self.limit, status=self.status, progress=self.progress, row_count=len(self.rows),
            email_count=sum(1 for r in self.rows if r.email), found=self.stats.get("matched"), notice=self.notice,
            created_at=self.created_at, updated_at=self.updated_at,
            finished_at=self.finished_at, error=self.error, drive_path=self.drive_path, drive_link=self.drive_link,
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
    business: str = ""
    place: str = ""
    whole_area: bool = False
    business_type_id: str = ""
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int
    status: JobStatus
    progress: Progress
    row_count: int
    email_count: int = Field(0, description="Rows with an email")
    found: int | None = Field(None, description="Places kept (rows can be more: one per email); null until scraped")
    notice: str = ""
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    drive_path: str = ""
    drive_link: str = ""
