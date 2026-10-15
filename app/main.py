"""FastAPI application: JSON API + the static web panel.

Run with ``python run.py`` (or ``uvicorn app.main:app``). API reference lives
in docs/API.md - update it when you touch a route here (tests/test_docs.py
checks that every route is mentioned).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__, settings_editor
from app.config import settings
from app.control import browse_url, control
from app.drive.client import DriveError
from app.drive.sync import DriveSync, folder_name
from app.export.csv_export import column_headers, job_filename, rows_to_csv_bytes
from app.jobs import ACTIVE_STATES, JobManager
from app.logbuffer import LEVELS, install as install_log_buffer, log_buffer
from app.models import EnrichRequest, Job, JobStatus, JobSummary, LogsResponse, ScrapeRequest
from app.outreach.activity import ActivityLog
from app.outreach.autopilot import Autopilot, AutopilotUpdate
from app.outreach.manager import OutreachError, OutreachManager
from app.outreach.master import activity_csv, campaigns_csv
from app.outreach.sites import SiteService
from app.outreach.models import (BusinessType, BusinessTypeCreate, BusinessTypeUpdate, Campaign, CampaignCreate,
                                 CampaignStatus, CampaignSummary, CampaignUpdate, DriveSource, Lead, LeadUpdate,
                                 SendTestRequest)
from app.settings_editor import SettingsError, SettingsUpdate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too chatty
install_log_buffer(settings.LOG_BUFFER_SIZE)              # feeds the panel's "Live logs"
log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ENV_FILE = Path(settings.model_config["env_file"])    # the .env the Settings tab saves to

@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Leads Pilot Engine %s ready - %d job(s) loaded, headless=%s, detail concurrency=%d",
             __version__, len(manager.jobs), settings.HEADLESS, settings.DETAIL_CONCURRENCY)
    outreach.start_loop()
    autopilot.start_loop()
    if sites is not None and sites.unpublished():
        sites.request_upload()                  # an upload that hadn't happened when the server stopped
    if settings.DRIVE_AUTO_UPLOAD:
        drive.start_loop(outreach, master=_master_files)
    yield
    await autopilot.stop_loop()
    await outreach.stop_loop()
    await drive.stop_loop()
    if sites is not None:
        await sites.stop()
    log.info("Shutting down")


app = FastAPI(
    title="Leads Pilot Engine",
    version=__version__,
    description="Scrape Google Maps listings, enrich them with emails/phones from the business website, export CSV.",
    lifespan=lifespan,
)
manager = JobManager(settings)
sites = SiteService(settings) if settings.PREVIEW_SITES else None
activity = ActivityLog(settings.DATA_DIR / "activity.jsonl")
outreach = OutreachManager(settings, sites=sites, activity=activity)
autopilot = Autopilot(settings, manager, outreach, activity)
drive = DriveSync(settings)


def _master_files() -> dict[str, bytes]:
    """The master sheet's two files (app/outreach/master.py)."""
    return {"campaigns": campaigns_csv(outreach.list_campaigns()), "activity": activity_csv(activity.entries())}


async def _job_finished(job: Job) -> None:
    """A search (or an enrichment) finished: the autopilot makes its campaign, and its CSV goes to Google
    Drive (DRIVE_AUTO_UPLOAD)."""
    try:
        await autopilot.job_finished(job)
    except Exception:  # noqa: BLE001 - the Drive upload below still happens
        log.exception("Autopilot: could not act on the finished search %s", job.id)
    if settings.DRIVE_AUTO_UPLOAD and drive.connected and job.rows:
        try:
            await _save_job_to_drive(job)
        except DriveError as exc:
            log.warning("Google Drive: could not save %r: %s", job.query, exc)


manager.on_finished = _job_finished


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
        "server": _server_info(),
    }


# ----------------------------------------------------------------------
# Settings (the .env file) and the server itself - only from this computer
# ----------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings(request: Request) -> dict:
    """Every setting: group, label, help, the value in use (never a password) and what's saved in .env."""
    _this_computer(request)
    return {**settings_editor.describe(settings, ENV_FILE), "server": _server_info()}


@app.patch("/api/settings")
async def update_settings(req: SettingsUpdate, request: Request) -> dict:
    """Save settings to .env and use them right away (HOST, PORT, DATA_DIR, LOG_BUFFER_SIZE after a restart)."""
    _this_computer(request)
    try:
        changed, restart = settings_editor.save(settings, req.values, ENV_FILE)
    except SettingsError as exc:
        raise HTTPException(400, {"message": str(exc), "fields": exc.errors}) from exc
    await _apply_settings(set(changed) - set(restart))
    if changed:
        log.info("Settings saved to %s: %s", ENV_FILE.name, ", ".join(changed))
    view = settings_editor.describe(settings, ENV_FILE)
    return {**view, "server": _server_info(), "changed": changed, "restart": restart}


@app.post("/api/server/stop")
async def stop_server(request: Request) -> dict:
    """Stop the panel (its Stop button). Campaigns carry on sending when it is started again."""
    _this_computer(request)
    if not control.can_stop:
        raise HTTPException(409, "this server wasn't started with start.bat or run.py - stop it where it runs")
    running = sum(c.status == CampaignStatus.RUNNING for c in outreach.campaigns.values())
    log.info("Stopping (the panel's Stop button)%s", f" - {running} campaign(s) wait until it runs again"
             if running else "")
    control.stop()
    return {"stopping": True}


@app.post("/api/server/restart")
async def restart_server(request: Request) -> dict:
    """Restart the panel so saved settings like PORT take effect (only when started with start.bat)."""
    _this_computer(request)
    if not control.can_restart:
        raise HTTPException(409, "the panel can only restart itself when it was started with start.bat (or "
                                 "start.py) - stop it and start it again")
    try:
        fresh = settings_editor.load(ENV_FILE)
        url = browse_url(fresh.HOST, fresh.PORT)
    except ValueError:
        url = ""
    log.info("Restarting (the panel's Restart button)")
    control.stop(restart=True)
    return {"restarting": True, "url": url}


@app.post("/api/scrape", response_model=JobSummary, status_code=202)
async def start_scrape(req: ScrapeRequest) -> JobSummary:
    if req.limit > settings.MAX_RESULTS:
        raise HTTPException(400, f"limit must be <= {settings.MAX_RESULTS}")
    if req.business_type_id and req.business_type_id not in outreach.business_types:
        raise HTTPException(400, "unknown business type - reload the page")
    job = manager.create_scrape(req.search_query, req.limit, business=req.business, place=req.place,
                                whole_area=req.whole_area, filters=req.filters, enrich=req.enrich,
                                headless=req.headless, business_type_id=req.business_type_id)
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
    filename = job_filename(job, extended=extended)   # ends with the day the search ran (server's local date)
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
# Email outreach (docs/OUTREACH.md)
# ----------------------------------------------------------------------
@app.get("/api/outreach")
async def outreach_status() -> dict:
    """Mailbox settings, send window, today's count, the last reply check."""
    return outreach.status()


@app.post("/api/outreach/check")
async def outreach_check() -> dict:
    """Check the mailbox for replies and bounces now."""
    return await _outreach(outreach.check_now())


@app.get("/api/autopilot")
async def autopilot_status() -> dict:
    """The autopilot: on or off, its places, what it's waiting for, its recent searches."""
    return autopilot.status()


@app.patch("/api/autopilot")
async def update_autopilot(req: AutopilotUpdate) -> dict:
    """Switch the autopilot on or off, change its business type, places or limits."""
    return _outreach_call(autopilot.update, req)


@app.get("/api/activity")
async def get_activity(limit: int = Query(100, ge=1, le=5000)) -> list[dict]:
    """What happened, newest first: searches, campaigns, emails, replies, bounces."""
    return activity.entries(limit)


@app.get("/api/master/{part}.csv")
async def master_csv(part: str) -> Response:
    """The master sheet as CSV: ``campaigns`` (a row per campaign) or ``activity`` (the activity log)."""
    files = _master_files()
    if part not in files:
        raise HTTPException(404, "the master sheet has campaigns.csv and activity.csv")
    return Response(content=files[part], media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="leads-pilot-{part}.csv"'})


@app.get("/api/business-types", response_model=list[BusinessType])
async def list_business_types() -> list[BusinessType]:
    return outreach.list_business_types()


@app.post("/api/business-types", response_model=BusinessType, status_code=201)
async def create_business_type(req: BusinessTypeCreate) -> BusinessType:
    return await _outreach(outreach.create_business_type(req))


@app.get("/api/business-types/{type_id}", response_model=BusinessType)
async def get_business_type(type_id: str) -> BusinessType:
    return _outreach_call(outreach.get_business_type, type_id)


@app.patch("/api/business-types/{type_id}", response_model=BusinessType)
async def update_business_type(type_id: str, req: BusinessTypeUpdate) -> BusinessType:
    business_type = _outreach_call(outreach.get_business_type, type_id)
    return await _outreach(outreach.update_business_type(business_type, req))


@app.delete("/api/business-types/{type_id}", status_code=204)
async def delete_business_type(type_id: str) -> Response:
    await _outreach(outreach.delete_business_type(type_id))
    return Response(status_code=204)


@app.get("/api/business-types/{type_id}/preview")
async def preview_business_type(type_id: str) -> dict:
    """The business type's emails for two made-up businesses (with and without enough reviews)."""
    return outreach.preview_business_type(_outreach_call(outreach.get_business_type, type_id))


@app.post("/api/business-types/{type_id}/test")
async def test_business_type(type_id: str, req: SendTestRequest | None = None) -> dict:
    """Send one of the business type's emails to yourself."""
    business_type = _outreach_call(outreach.get_business_type, type_id)
    return await _outreach(outreach.send_test(business_type, req or SendTestRequest()))


@app.get("/api/campaigns", response_model=list[CampaignSummary])
async def list_campaigns() -> list[CampaignSummary]:
    return [c.summary() for c in outreach.list_campaigns()]


@app.post("/api/campaigns", response_model=Campaign, status_code=201)
async def create_campaign(req: CampaignCreate) -> Campaign:
    jobs = []
    for job_id in dict.fromkeys(req.job_ids):
        job = _job_or_404(job_id)
        if job.status in ACTIVE_STATES:
            raise HTTPException(409, f"the search “{job.query}” is still running - wait until it has finished")
        jobs.append(job)
    files = []
    for file_id in dict.fromkeys(req.drive_file_ids):
        try:
            file, rows = await drive.read_lead_file(file_id)
        except DriveError as exc:
            raise HTTPException(502 if exc.status >= 500 or not exc.status else 400, str(exc)) from exc
        files.append((DriveSource(id=file["id"], name=file.get("name", file_id), link=file.get("webViewLink", "")),
                      rows))
    return await _outreach(outreach.create(req, jobs, files))


@app.get("/api/campaigns/{campaign_id}", response_model=Campaign)
async def get_campaign(campaign_id: str) -> Campaign:
    return _outreach_call(outreach.get, campaign_id)


@app.patch("/api/campaigns/{campaign_id}", response_model=Campaign)
async def update_campaign(campaign_id: str, req: CampaignUpdate) -> Campaign:
    return await _outreach(outreach.update(_outreach_call(outreach.get, campaign_id), req))


@app.delete("/api/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str) -> Response:
    await _outreach(outreach.delete(campaign_id))
    return Response(status_code=204)


@app.post("/api/campaigns/{campaign_id}/start", response_model=Campaign)
async def start_campaign(campaign_id: str) -> Campaign:
    return await _outreach(outreach.start(_outreach_call(outreach.get, campaign_id)))


@app.post("/api/campaigns/{campaign_id}/pause", response_model=Campaign)
async def pause_campaign(campaign_id: str) -> Campaign:
    return await _outreach(outreach.pause(_outreach_call(outreach.get, campaign_id)))


@app.get("/api/campaigns/{campaign_id}/preview")
async def preview_campaign(campaign_id: str, lead_id: str | None = Query(None)) -> dict:
    """Every email of the campaign as one business will get it."""
    return _outreach_call(outreach.preview, _outreach_call(outreach.get, campaign_id), lead_id)


@app.post("/api/campaigns/{campaign_id}/test")
async def test_campaign(campaign_id: str, req: SendTestRequest | None = None) -> dict:
    """Send one of the campaign's emails, filled in for one of its businesses, to yourself."""
    campaign = _outreach_call(outreach.get, campaign_id)
    return await _outreach(outreach.send_test(campaign, req or SendTestRequest()))


@app.patch("/api/campaigns/{campaign_id}/leads/{lead_id}", response_model=Lead)
async def update_lead(campaign_id: str, lead_id: str, req: LeadUpdate) -> Lead:
    return await _outreach(outreach.update_lead(_outreach_call(outreach.get, campaign_id), lead_id, req))


@app.post("/api/campaigns/{campaign_id}/drive")
async def save_campaign_to_drive(campaign_id: str) -> dict:
    """Upload the campaign's results to Google Drive now."""
    campaign = _outreach_call(outreach.get, campaign_id)
    _drive_ready()
    try:
        await drive.sync_campaigns(outreach, only=campaign.id, force=True)
    except DriveError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"path": campaign.drive_path, "link": campaign.drive_link}


# ----------------------------------------------------------------------
# Google Drive (docs/DRIVE.md)
# ----------------------------------------------------------------------
@app.get("/api/drive")
async def drive_status() -> dict:
    """Whether Google Drive is set up and connected, as whom, and the folder files go to."""
    return drive.status()


@app.post("/api/drive/connect")
async def drive_connect(request: Request) -> dict:
    """The Google consent page to open; Google then sends the browser back to this server."""
    if request.url.hostname not in ("127.0.0.1", "localhost", "::1", "[::1]"):
        raise HTTPException(400, f"open the panel at http://127.0.0.1:{request.url.port or settings.PORT} to connect "
                                 "Google Drive - Google only sends the sign-in back to this computer")
    try:
        return {"auth_url": drive.client.auth_url(str(request.base_url))}
    except DriveError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/drive/disconnect", status_code=204)
async def drive_disconnect() -> Response:
    await drive.client.disconnect()
    return Response(status_code=204)


@app.get("/api/drive/files")
async def drive_files(business_type_id: str = Query(..., description="Whose folder to list")) -> dict:
    """The lead files (CSV / Google Sheets) in a business type's Drive folder."""
    business_type = _outreach_call(outreach.get_business_type, business_type_id)
    _drive_ready()
    try:
        return await drive.lead_files(folder_name(business_type.name))
    except DriveError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/jobs/{job_id}/drive")
async def save_job_to_drive(job_id: str) -> dict:
    """Upload the search's CSV to its business type's Drive folder now."""
    job = _job_or_404(job_id)
    if job.status in ACTIVE_STATES:
        raise HTTPException(409, "the search is still running - it is saved when it has finished")
    _drive_ready()
    try:
        return await _save_job_to_drive(job)
    except DriveError as exc:
        raise HTTPException(502, str(exc)) from exc


# ----------------------------------------------------------------------
# Static panel
# ----------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index(request: Request) -> Response:
    state = request.query_params.get("state", "")
    if state and drive.client.is_pending(state):
        return await _finish_drive_sign_in(request, state)
    # version-stamp the asset URLs so browsers never serve a stale panel after an upgrade
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("{{version}}", __version__)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


async def _finish_drive_sign_in(request: Request, state: str) -> Response:
    """Google sent the browser back from its consent page (the redirect URI is this server's root)."""
    code, error = request.query_params.get("code", ""), request.query_params.get("error", "")
    try:
        if error or not code:
            drive.client.cancel_sign_in(state)
            raise DriveError("access was not granted" if error == "access_denied" else f"Google answered {error}")
        email = await drive.client.finish_sign_in(code, state)
    except DriveError as exc:
        log.warning("Google Drive: connecting failed: %s", exc)
        return RedirectResponse(f"/?drive=error&message={quote(str(exc))}#outreach", status_code=303)
    return RedirectResponse(f"/?drive=connected&message={quote(email)}#outreach", status_code=303)


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
_LOOPBACK_CLIENTS = ("127.0.0.1", "::1")
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def _this_computer(request: Request) -> None:
    """Settings, Stop and Restart only for the panel opened on this computer: not for other computers on the
    network (HOST=0.0.0.0), and not for other web sites - their requests carry their own Origin (or, sent
    through a DNS name pointing at 127.0.0.1, a Host that isn't one of ours)."""
    client = request.client.host if request.client else ""
    origin = request.headers.get("origin", "")
    same_origin = not origin or origin.rstrip("/") == f"{request.url.scheme}://{request.url.netloc}"
    if client not in _LOOPBACK_CLIENTS or request.url.hostname not in _LOOPBACK_NAMES or not same_origin:
        raise HTTPException(403, "only the panel opened on this computer can do this - open "
                                 f"http://127.0.0.1:{request.url.port or settings.PORT}")


def _server_info() -> dict:
    return {"can_stop": control.can_stop, "can_restart": control.can_restart,
            "url": browse_url(settings.HOST, settings.PORT), "env_file": str(ENV_FILE)}


async def _apply_settings(names: set[str]) -> None:
    """Saved settings the running server takes up now (everything else is read where it's used)."""
    if "OUTREACH_AUTOSEND" in names:
        if settings.OUTREACH_AUTOSEND:
            outreach.start_loop()
        else:
            await outreach.stop_loop()
    if "DRIVE_AUTO_UPLOAD" in names:
        if settings.DRIVE_AUTO_UPLOAD:
            drive.start_loop(outreach, master=_master_files)
        else:
            await drive.stop_loop()
    if names & {"DRIVE_FOLDER", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"}:
        drive.forget_folders()


def _job_or_404(job_id: str) -> Job:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _job_folder(job: Job) -> str:
    """The Drive folder of a search: its business type's name, else what was searched ('Dentist')."""
    business_type = outreach.business_types.get(job.business_type_id) if job.business_type_id else None
    return folder_name(business_type.name if business_type else (job.business or "Other"))


async def _save_job_to_drive(job: Job) -> dict:
    info = await drive.save_job(job, _job_folder(job))
    job.drive_file_id, job.drive_link, job.drive_path = info["id"], info["link"], info["path"]
    manager.save(job)
    log.info("Google Drive: saved %r to %s", job.query, info["path"])
    return {"path": info["path"], "link": info["link"]}


def _drive_ready() -> None:
    if not drive.connected:
        raise HTTPException(400, "Google Drive is not connected - connect it in the Outreach tab")


async def _outreach(awaitable):
    """Await an outreach operation; its OutreachError becomes the matching HTTP error."""
    try:
        return await awaitable
    except OutreachError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _outreach_call(function, *args):
    try:
        return function(*args)
    except OutreachError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception) -> JSONResponse:  # pragma: no cover
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"{exc.__class__.__name__}: {exc}"})
