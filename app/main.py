"""FastAPI application: JSON API + the static web panel.

Run with ``python run.py`` (or ``uvicorn app.main:app``). API reference lives
in docs/API.md - update it when you touch a route here (tests/test_docs.py
checks that every route is mentioned).
"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.export.csv_export import column_headers, rows_to_csv_bytes
from app.jobs import JobManager
from app.logbuffer import LEVELS, install as install_log_buffer, log_buffer
from app.models import EnrichRequest, Job, JobStatus, JobSummary, LogsResponse, ScrapeRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too chatty
install_log_buffer(settings.LOG_BUFFER_SIZE)              # feeds the panel's "Live logs"
log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Google Maps Lead Crawler %s ready - %d job(s) loaded, headless=%s, detail concurrency=%d",
             __version__, len(manager.jobs), settings.HEADLESS, settings.DETAIL_CONCURRENCY)
    yield
    log.info("Shutting down")


app = FastAPI(
    title="Google Maps Lead Crawler",
    version=__version__,
    description="Scrape Google Maps listings, enrich them with emails/phones from the business website, export CSV.",
    lifespan=lifespan,
)
manager = JobManager(settings)


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "jobs": len(manager.jobs)}


@app.get("/api/config")
async def config() -> dict:
    """Non-secret settings the panel needs for validation and hints."""
    return {
        "max_results": settings.MAX_RESULTS,
        "max_scanned": settings.MAX_SCANNED,
        "headless": settings.HEADLESS,
        "detail_concurrency": settings.DETAIL_CONCURRENCY,
        "enrich_max_pages": settings.ENRICH_MAX_PAGES,
        "columns": column_headers(False),
        "extended_columns": column_headers(True),
    }


@app.post("/api/scrape", response_model=JobSummary, status_code=202)
async def start_scrape(req: ScrapeRequest) -> JobSummary:
    if req.limit > settings.MAX_RESULTS:
        raise HTTPException(400, f"limit must be <= {settings.MAX_RESULTS}")
    job = manager.create_scrape(req.search_query, req.limit, business=req.business, place=req.place,
                                whole_area=req.whole_area, filters=req.filters, enrich=req.enrich,
                                headless=req.headless)
    return job.summary()


@app.get("/api/jobs", response_model=list[JobSummary])
async def list_jobs() -> list[JobSummary]:
    return [j.summary() for j in manager.list_jobs()]


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    return _job_or_404(job_id)


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> Response:
    if not manager.delete(job_id):
        raise HTTPException(404, "job not found")
    return Response(status_code=204)


@app.post("/api/jobs/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(job_id: str) -> JobSummary:
    job = _job_or_404(job_id)
    if not manager.cancel(job_id):
        raise HTTPException(409, "job is not running")
    return job.summary()


@app.post("/api/jobs/{job_id}/enrich", response_model=JobSummary, status_code=202)
async def enrich_job(job_id: str, req: EnrichRequest | None = None) -> JobSummary:
    job = _job_or_404(job_id)
    req = req or EnrichRequest()
    if job.status in (JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.ENRICHING):
        raise HTTPException(409, f"job is {job.status.value}; wait for it to finish")
    if not job.rows:
        raise HTTPException(400, "job has no rows to enrich")
    if not manager.start_enrich(job, row_ids=req.row_ids, force=req.force):
        raise HTTPException(409, "an enrichment is already running for this job")
    return job.summary()


@app.get("/api/jobs/{job_id}/export.csv")
async def export_csv(job_id: str, extended: bool = Query(False)) -> Response:
    job = _job_or_404(job_id)
    suffix = job.filters.slug()
    filename = (_safe_filename(job.query) + (f"_{suffix}" if suffix else "") + ("_extended" if extended else "")
                + f"_{job.created_at.astimezone():%d_%m_%Y}.csv")  # the day the search ran (server's local date)
    payload = rows_to_csv_bytes(job.rows, extended=extended)
    try:  # keep a copy on disk for convenience
        (settings.exports_dir / f"{job.id}{'_extended' if extended else ''}.csv").write_bytes(payload)
    except OSError as exc:  # pragma: no cover
        log.warning("could not persist export: %s", exc)
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/logs", response_model=LogsResponse)
async def get_logs(
    since: int | None = Query(None, ge=0, description="Return only lines with seq > since; omit for the newest lines"),
    limit: int = Query(200, ge=1, le=1000),
    job_id: str | None = Query(None, description="Only lines produced while this job was running"),
    level: str | None = Query(None, description="Minimum level: DEBUG, INFO, WARNING, ERROR"),
) -> dict:
    """Live log lines from the in-memory ring buffer (see docs/API.md)."""
    min_level = None
    if level:
        if level.upper() not in LEVELS:
            raise HTTPException(400, f"level must be one of {', '.join(LEVELS)}")
        min_level = LEVELS[level.upper()]
    return log_buffer.entries(since=since, limit=limit, job_id=job_id, min_level=min_level)


# ----------------------------------------------------------------------
# Static panel
# ----------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index() -> HTMLResponse:
    # version-stamp the asset URLs so browsers never serve a stale panel after an upgrade
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("{{version}}", __version__)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _job_or_404(job_id: str) -> Job:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return (cleaned or "results")[:60]


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception) -> JSONResponse:  # pragma: no cover
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"{exc.__class__.__name__}: {exc}"})
