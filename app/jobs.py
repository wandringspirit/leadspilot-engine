"""In-process job manager.

A *job* is one search (business + place, or a free-text query) with a limit
and optional filters. Jobs run as asyncio tasks in the FastAPI event loop,
publish progress into the ``Job`` model and are persisted as JSON under
``data/jobs`` so the panel can show history after a restart. ``PlaceFilter``
applies the job's filters while the scraper runs. Enrichment (email/phone
lookup on websites) runs against an existing job and mutates its rows in
place - including the "one row per email" rule.

See docs/ARCHITECTURE.md for the state machine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app.config import Settings, settings as default_settings
from app.enrichment.extractors import EmailHit, emails_in_text
from app.enrichment.website import ContactResult, WebsiteEnricher, enrich_many
from app.filters import EMAIL_CONTACTS, SearchFilters
from app.logbuffer import current_job_id
from app.models import Job, JobStatus, PlaceRow, Progress
from app.scraper.maps import MapsScraper, PlaceRef, ScrapeStats
from app.scraper.parsing import format_phone

log = logging.getLogger(__name__)

ACTIVE_STATES = {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ENRICHING}

# Google Maps ends every results list at about GOOGLE_LIST_LIMIT listings, however many
# places exist; a list that ends at NEAR_GOOGLE_LIMIT or more was probably cut short.
GOOGLE_LIST_LIMIT = 120
NEAR_GOOGLE_LIMIT = 100

Lookup = Callable[..., Awaitable[ContactResult]]   # WebsiteEnricher.find_contacts(url, region=...)


class JobManager:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._scrape_slots = asyncio.Semaphore(2)
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _path(self, job_id: str) -> Path:
        return self.cfg.jobs_dir / f"{job_id}.json"

    def _load_from_disk(self) -> None:
        for file in sorted(self.cfg.jobs_dir.glob("job_*.json")):
            try:
                job = Job.model_validate_json(file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping unreadable job file %s: %s", file, exc)
                continue
            if job.status in ACTIVE_STATES:
                job.status = JobStatus.INTERRUPTED
                job.progress.message = "Server restarted while the job was running"
            self.jobs[job.id] = job
        log.info("loaded %d job(s) from %s", len(self.jobs), self.cfg.jobs_dir)

    def save(self, job: Job) -> None:
        job.touch()
        path = self._path(job.id)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(job.model_dump_json(indent=None))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_jobs(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        job = self.jobs.pop(job_id, None)
        if not job:
            return False
        self.cancel(job_id)
        try:
            self._path(job_id).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------
    def create_scrape(self, query: str, limit: int, *, business: str = "", place: str = "",
                      filters: SearchFilters | None = None, enrich: bool = False,
                      headless: bool | None = None) -> Job:
        job = Job(query=query.strip(), business=business.strip(), place=place.strip(), limit=limit,
                  filters=filters or SearchFilters(), enrich=enrich)
        # schedule first: if no event loop is running we must not leave a ghost job behind
        task = asyncio.create_task(self._run_scrape(job, headless=headless), name=job.id)
        self.jobs[job.id] = job
        self._tasks[job.id] = task
        self.save(job)
        log.info("Job %s queued: %r, %d result(s) wanted%s%s", job.id, job.query, job.limit,
                 f" ({job.filters.label})" if job.filters.active else "",
                 ", enrichment afterwards" if enrich else "")
        return job

    async def _run_scrape(self, job: Job, *, headless: bool | None) -> None:
        current_job_id.set(job.id)  # tags every log line of this task (and its child tasks)
        async with self._scrape_slots:
            job.status = JobStatus.RUNNING
            job.progress = Progress(phase="collecting", message="Starting browser")
            self.save(job)
            started = asyncio.get_event_loop().time()
            last_saved = 0.0

            async def on_progress(phase: str, message: str, current: int, total: int) -> None:
                nonlocal last_saved
                job.progress = Progress(
                    phase=phase, message=message, current=current, total=total,
                    percent=round(100.0 * current / total, 1) if total else 0.0,
                )
                job.touch()
                now = asyncio.get_event_loop().time()
                if now - last_saved > 2.0:
                    self.save(job)
                    last_saved = now

            async def on_row(row: PlaceRow) -> None:
                job.rows.append(row)
                job.rows.sort(key=lambda r: r.rank)

            scraper = MapsScraper(self.cfg, headless=headless, on_progress=on_progress, on_row=on_row)
            selector = PlaceFilter(job.filters, default_region=self.cfg.DEFAULT_REGION)
            try:
                async with AsyncExitStack() as stack:
                    if selector.wants_lookups:
                        enricher = await stack.enter_async_context(WebsiteEnricher(self.cfg))
                        selector.lookup = enricher.find_contacts
                    await scraper.scrape(job.query, job.limit, selector=selector)
                job.stats = stats_to_dict(scraper.stats)
                job.notice = result_notice(job)
                if job.notice:
                    log.info("Job %s: %s", job.id, job.notice)
                # places already looked up while filtering are not fetched a second time
                fresh = [r.row_id for r in job.rows if not r.enrichment_status]
                if job.enrich and fresh:
                    job.status = JobStatus.ENRICHING
                    await self._enrich(job, row_ids=fresh, force=False)
                job.status = JobStatus.COMPLETED
                job.progress.phase = "done"
                found = job.stats["matched"]
                job.progress.message = (f"Done - {found} of {job.limit} found" if found < job.limit
                                        else f"Done - {len(job.rows)} rows")
                job.progress.percent = 100.0
                log.info("Job %s completed: %d row(s) in %.1fs", job.id, len(job.rows),
                         asyncio.get_event_loop().time() - started)
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                job.progress.message = f"Cancelled with {len(job.rows)} rows collected"
                log.warning("Job %s cancelled with %d row(s) collected", job.id, len(job.rows))
            except Exception as exc:  # noqa: BLE001
                log.exception("Job %s failed: %s", job.id, exc)
                job.status = JobStatus.FAILED
                job.error = f"{exc.__class__.__name__}: {exc}"
                job.progress.message = "Failed"
            finally:
                job.finished_at = datetime.now(timezone.utc)
                self.save(job)
                self._tasks.pop(job.id, None)

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task and not task.done():
            log.info("Cancelling job %s", job_id)
            task.cancel()
            return True
        return False

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------
    def start_enrich(self, job: Job, *, row_ids: list[str] | None, force: bool) -> bool:
        if job.id in self._tasks and not self._tasks[job.id].done():
            return False
        self._tasks[job.id] = asyncio.create_task(self._run_enrich(job, row_ids, force), name=f"enrich-{job.id}")
        return True

    async def _run_enrich(self, job: Job, row_ids: list[str] | None, force: bool) -> None:
        current_job_id.set(job.id)
        previous_status = job.status
        job.status = JobStatus.ENRICHING
        self.save(job)
        log.info("Enrichment requested for job %s (%s%s)", job.id,
                 f"{len(row_ids)} selected row(s)" if row_ids else "all rows missing an email or phone",
                 ", force" if force else "")
        try:
            await self._enrich(job, row_ids=row_ids, force=force)
            job.status = JobStatus.COMPLETED if previous_status in (JobStatus.COMPLETED, JobStatus.ENRICHING) else previous_status
            job.progress.phase = "done"
            job.progress.percent = 100.0
        except asyncio.CancelledError:
            job.status = previous_status
            job.progress.message = "Enrichment cancelled"
            log.warning("Enrichment for job %s cancelled", job.id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Enrichment for job %s failed: %s", job.id, exc)
            job.status = previous_status
            job.error = f"enrichment failed: {exc.__class__.__name__}: {exc}"
        finally:
            self.save(job)
            self._tasks.pop(job.id, None)

    async def _enrich(self, job: Job, *, row_ids: list[str] | None, force: bool) -> None:
        """Look up emails/phones for the selected places and apply the results."""
        targets = select_enrichment_targets(job.rows, row_ids=row_ids, force=force)
        websites = {str(rank): rows[0].website for rank, rows in targets.items() if rows[0].website}

        # Rows without a website can never be enriched - say so explicitly.
        for rank, rows in targets.items():
            if not rows[0].website:
                for r in rows:
                    r.enrichment_status = "no_website"
            else:
                for r in rows:
                    r.enrichment_status = "pending"

        total = len(websites)
        done = 0
        job.progress = Progress(phase="enriching", message=f"Checking {total} website(s)", current=0, total=total)
        self.save(job)
        skipped = sum(1 for rows in targets.values() if not rows[0].website)
        log.info("Enrichment: %d website(s) to check, %d place(s) without a website", total, skipped)
        if not websites:
            job.progress.message = "Nothing to enrich"
            return

        region = next((r.country for rows in targets.values() for r in rows if r.country), self.cfg.DEFAULT_REGION)

        async def on_result(key: str, result: ContactResult) -> None:
            nonlocal done
            added = apply_contact_result(job.rows, int(key), result, default_region=self.cfg.DEFAULT_REGION)
            name = next((r.name for r in job.rows if r.rank == int(key)), key)
            if result.email_addresses or result.phones:
                log.info("Enriched #%s %s: %s%s%s", key, name,
                         ", ".join(result.email_addresses) or "no email",
                         f", {len(result.phones)} phone(s)" if result.phones else "",
                         f" (+{len(added)} duplicate row(s))" if added else "")
            elif result.error:
                log.warning("Enrichment failed for #%s %s: %s", key, name, result.error)
            else:
                log.info("Nothing found for #%s %s (%d page(s) checked)", key, name, len(result.pages_checked))
            done += 1
            job.progress = Progress(
                phase="enriching", message=f"Checked {done} of {total} websites", current=done, total=total,
                percent=round(100.0 * done / total, 1),
            )
            self.save(job)

        await enrich_many(websites, cfg=self.cfg, region=region, on_result=on_result)
        job.progress.message = f"Enrichment finished ({done} websites checked)"
        with_email = sum(1 for r in job.rows if r.email)
        log.info("Enrichment finished: %d website(s) checked, %d of %d row(s) now have an email",
                 done, with_email, len(job.rows))


# ----------------------------------------------------------------------
# Filtering while scraping (the scraper's selector)
# ----------------------------------------------------------------------
class PlaceFilter:
    """Applies a job's `SearchFilters` to the places the scraper finds.

    * ``prefilter`` skips a listing whose website, as the results feed shows
      it, already rules it out - its place page is never opened;
    * ``accept`` copies emails written in the listing's description into the
      Email column, looks for an email on the website / social page when only
      a missing email keeps the place out (needs ``lookup``), then applies the
      filters to the finished place.

    With the default filters every place is kept (listing emails still added).
    """

    def __init__(self, filters: SearchFilters | None = None, *, lookup: Lookup | None = None,
                 default_region: str = "US") -> None:
        self.filters = filters or SearchFilters()
        self.lookup = lookup
        self.default_region = default_region

    @property
    def active(self) -> bool:
        return self.filters.active

    @property
    def wants_lookups(self) -> bool:
        """True when an email found on a website can decide whether a place matches."""
        return self.filters.contact in EMAIL_CONTACTS

    def prefilter(self, ref: PlaceRef) -> str:
        return self.filters.website_reason(ref.website_hint)

    async def accept(self, row: PlaceRow) -> tuple[list[PlaceRow], str]:
        rows = [row]
        apply_listing_emails(rows)
        reason = self.filters.check(rows)
        if reason and self.lookup and self.filters.email_would_help(rows):
            log.info("Looking for an email on %s for #%d %s (%s so far)", row.website, row.rank, row.name, reason)
            try:
                result = await self.lookup(row.website, region=row.country or self.default_region)
            except Exception as exc:  # noqa: BLE001 - a failed lookup only means "no email"
                log.warning("Email lookup failed for #%d %s: %s", row.rank, row.name, exc)
            else:
                apply_contact_result(rows, row.rank, result, default_region=self.default_region)
                reason = self.filters.check(rows)
        return ([] if reason else rows), reason


def stats_to_dict(stats: ScrapeStats) -> dict:
    return {
        "links_found": stats.links_found, "scraped": stats.scraped, "matched": stats.matched,
        "skipped": dict(stats.skipped), "failed": stats.failed, "reached_end": stats.reached_end,
        "stopped": stats.stopped, "errors": stats.errors[:20],
    }


def result_notice(job: Job) -> str:
    """Explain a result smaller than the limit ('' when there is nothing to explain).

    Shown above the results in the panel and printed by the CLI, e.g.
    "Only 35 businesses found for “plumbers” in Austin, TX — that's every
    listing Google Maps has for this search (you asked for 200)."
    """
    stats = job.stats
    found, listings, stopped = stats.get("matched", 0), stats.get("links_found", 0), stats.get("stopped", "")
    if found >= job.limit or stopped in ("", "limit"):
        return ""
    subject = (f"“{job.business}”" + (f" in {job.place}" if job.place else "")) if job.business else f"“{job.query}”"
    asked = f"you asked for {job.limit}"
    filters = job.filters.label if job.filters.active else ""
    if stopped == "no_results" or listings == 0:
        return f"Google Maps has no results for {subject}."
    if stopped == "single":
        tail = "" if found else f", and it does not match your filters ({filters})"
        return f"Google Maps shows a single place for {subject}{tail}."
    if stopped == "end_of_list" and listings < NEAR_GOOGLE_LIMIT:
        if filters:
            listed = f"the {_businesses(listings)} Google Maps lists for {subject}"
            if not found:
                return f"{_matching(found, listed, filters)}."
            return f"Only {_matching(found, listed, filters)} — that's all of them ({asked})."
        return (f"Only {_businesses(found)} found for {subject} — that's every listing Google Maps "
                f"has for this search ({asked}).")
    if stopped == "end_of_list":
        matched = f" {_matching(found, 'them', filters)}." if filters else ""
        return (f"Google Maps stopped listing {subject} after {listings} results — it shows at most about "
                f"{GOOGLE_LIST_LIMIT} per search, so there may be more.{matched} Search smaller areas "
                "(neighbourhoods or nearby towns) to find more.")
    if stopped == "scan_limit":
        return (f"Stopped after scanning {listings} listings for {subject} (MAX_SCANNED setting) with "
                f"{found} matching ({asked}).")
    return (f"The results list for {subject} stopped loading after {listings} listings, so there may be more "
            f"({found} kept, {asked}). Try again later, or raise SCROLL_PAUSE_MS / SCROLL_STALL_LIMIT.")


def _businesses(n: int) -> str:
    return f"{n} business" if n == 1 else f"{n} businesses"


def _matching(found: int, among: str, filters: str) -> str:
    """'35 of <among> match your filters (…)', '1 of … matches …', 'None of … match …'."""
    return f"{found or 'None'} of {among} {'matches' if found == 1 else 'match'} your filters ({filters})"


# ----------------------------------------------------------------------
# Pure helpers (unit tested in tests/test_jobs.py)
# ----------------------------------------------------------------------
def group_by_place(rows: list[PlaceRow]) -> dict[int, list[PlaceRow]]:
    groups: dict[int, list[PlaceRow]] = {}
    for row in rows:
        groups.setdefault(row.rank, []).append(row)
    return groups


def select_enrichment_targets(
    rows: list[PlaceRow], *, row_ids: list[str] | None, force: bool
) -> dict[int, list[PlaceRow]]:
    """Return {rank: rows} for places that should be enriched."""
    groups = group_by_place(rows)
    wanted_ranks: set[int] | None = None
    if row_ids:
        wanted_ranks = {r.rank for r in rows if r.row_id in set(row_ids)}
    targets: dict[int, list[PlaceRow]] = {}
    for rank, place_rows in groups.items():
        if wanted_ranks is not None and rank not in wanted_ranks:
            continue
        missing = any(not r.email for r in place_rows) or any(not r.phone for r in place_rows)
        if force or missing:
            targets[rank] = place_rows
    return targets


def apply_contact_result(rows: list[PlaceRow], rank: int, result: ContactResult, *, default_region: str = "US") -> list[PlaceRow]:
    """Write a website lookup result into the job rows.

    * missing phone -> filled on every row of the place (first phone found)
    * one email      -> written into the first row that has no email
    * more emails    -> the place row is duplicated once per extra email
    Returns the rows that were added.
    """
    place_rows = [r for r in rows if r.rank == rank]
    if not place_rows:
        return []
    region = place_rows[0].country or default_region

    if result.phones:
        national, e164 = format_phone(result.phones[0], region)
        for r in place_rows:
            if not r.phone:
                r.phone, r.phone_unformatted, r.phone_source = national, e164, "website"

    added, unique_hits = _add_emails(rows, rank, result.emails)
    place_rows.extend(added)

    for r in place_rows:
        if result.error and not (unique_hits or result.phones):
            r.enrichment_status, r.enrichment_note = "error", result.error
        elif unique_hits or (result.phones and r.phone_source == "website"):
            r.enrichment_status, r.enrichment_note = "done", f"checked {len(result.pages_checked)} page(s)"
        else:
            r.enrichment_status, r.enrichment_note = "not_found", f"checked {len(result.pages_checked)} page(s)"
    return added


def apply_listing_emails(rows: list[PlaceRow]) -> list[PlaceRow]:
    """Put addresses written in the Google listing itself (its description) into the Email column.

    Same one-row-per-email rule as website enrichment; ``email_source`` is the
    listing's Maps URL. Returns the rows that were added.
    """
    if not rows:
        return []
    first = rows[0]
    hits = [EmailHit(email=e, source=first.maps_url or first.url) for e in emails_in_text(first.description)]
    return _add_emails(rows, first.rank, hits)[0]


def _add_emails(rows: list[PlaceRow], rank: int, hits: list[EmailHit]) -> tuple[list[PlaceRow], list[EmailHit]]:
    """Write new addresses into a place's rows: empty Email cells first, then one cloned
    row per further address, inserted right after the place's last row.

    Returns (rows added, hits that were new to the place).
    """
    place_rows = [r for r in rows if r.rank == rank]
    existing = {r.email for r in place_rows if r.email}
    unique_hits: list[EmailHit] = []
    for hit in hits:
        if hit.email not in existing:
            existing.add(hit.email)
            unique_hits.append(hit)

    added: list[PlaceRow] = []
    empty_rows = [r for r in place_rows if not r.email]
    for hit in unique_hits:
        if empty_rows:
            target = empty_rows.pop(0)
            target.email, target.email_source = hit.email, hit.source
        else:
            added.append(place_rows[0].clone_with_email(hit.email, hit.source))

    if added:
        last_index = max(i for i, r in enumerate(rows) if r.rank == rank)
        rows[last_index + 1:last_index + 1] = added
    return added, unique_hits


def job_to_json(job: Job) -> str:
    return json.dumps(job.model_dump(mode="json"), ensure_ascii=False)
