"""Where the crawler's files go in Google Drive, and keeping them up to date.

```
DRIVE_FOLDER/                      (a folder of My Drive, a shared folder's URL, or My Drive itself)
  Leads Pilot - Master sheet       a Google Sheet: a Campaigns tab (every campaign on one page) and an
                                   Activity tab (everything that happened) - app/drive/sheets.py
  (Leads Pilot - campaigns.csv / activity.csv instead, while the Sheets API is off)
  Roofer/                          one folder per business type (its name)
    roofing_in_houston_no_website_email_29_09_2026.csv    each search, when it finishes
    my_cleaned_list.csv            a lead file you put there - a campaign can run from it
    Outreach/
      Roofer · Houston.csv         each campaign's results, updated while it runs
  Plumber/
    …
  Backups/
    leadspilot-backup-2026-11-12.zip   the panel's own data, once a day, the newest 30 - app/drive/backup.py
```

A search's CSV is the extended export (the default columns first, then the
extra ones); it is written again after an enrichment. Campaign results are
and the master sheet are uploaded when they changed, at most every ``RESULTS_EVERY``. Folders are made
when missing and remembered for the session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from app.config import Settings
from app.drive.backup import Backup
from app.drive.client import SHEET_TYPE, DriveClient, DriveError, folder_id_from
from app.drive.sheets import NAME as SHEET_NAME, MasterSheet, SheetsOff
from app.export.csv_export import job_filename, rows_to_csv_bytes, safe_filename
from app.export.csv_import import CsvFormatError, rows_from_csv
from app.outreach.master import to_csv
from app.models import Job, PlaceRow

if TYPE_CHECKING:
    from app.outreach.manager import OutreachManager
    from app.outreach.models import Campaign

log = logging.getLogger(__name__)

OUTREACH_FOLDER = "Outreach"
RESULTS_EVERY = timedelta(minutes=5)     # campaign results are uploaded at most this often
LOOP_S = 60
MASTER_FILES = {"status": "Leads Pilot - status.csv", "campaigns": "Leads Pilot - campaigns.csv",
                "activity": "Leads Pilot - activity.csv"}
SHEETS_RETRY = timedelta(minutes=10)     # the Sheets API was off: try it again after this
LEAD_FILE_TYPES = ("text/csv", "text/comma-separated-values", "application/vnd.ms-excel", SHEET_TYPE)


def folder_name(text: str) -> str:
    """A Drive folder name from a business type / search: 'plumber' -> 'Plumber'."""
    text = " ".join(text.replace("/", " ").split())
    return (text[:1].upper() + text[1:]) if text else "Other"


class DriveSync:
    def __init__(self, cfg: Settings, client: DriveClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or DriveClient(cfg)
        self.last_error = ""
        self._folders: dict[tuple[str, ...], dict] = {}
        self._task: asyncio.Task | None = None
        # where the master sheet went: "sheet" -> {link, at}, or (CSV) part -> {id, link, hash, at}
        self.master: dict[str, dict] = {}
        self.sheet = MasterSheet(self.client, cfg.DATA_DIR / "master_sheet.json")
        self.sheets_off = ""                 # why the master sheet is CSV files ('' = it isn't)
        self._sheets_off_at: datetime | None = None
        self._master_hash = ""
        self._master_at: datetime | None = None
        self.backup = Backup(cfg, self)        # once a day, in the loop (app/drive/backup.py)

    @property
    def connected(self) -> bool:
        return self.client.connected

    def status(self) -> dict:
        problems = []
        if not self.client.configured:
            problems.append("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set - make a Desktop app OAuth client in "
                            "the Google Cloud console (docs/DRIVE.md)")
        return {
            "configured": self.client.configured,
            "connected": self.connected,
            "lost_at": "" if self.connected else self.client.lost_at,   # Google ended the sign-in then
            "email": self.client.email,
            "folder": self.cfg.DRIVE_FOLDER.strip() or "My Drive",
            "auto_upload": self.cfg.DRIVE_AUTO_UPLOAD,
            "problems": problems,
            "last_error": self.last_error,
            "master": {part: {"name": SHEET_NAME if part == "sheet" else MASTER_FILES[part],
                              "link": file.get("link", ""), "at": file.get("at")}
                       for part, file in self.master.items()},
            "sheets_off": self.sheets_off,
            "backup": self.backup.status(),
        }

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------
    async def folder(self, *names: str) -> dict:
        """The folder ``DRIVE_FOLDER/<names…>`` (made where missing)."""
        key = tuple(names)
        if key in self._folders:
            return self._folders[key]
        if names:
            parent = await self.folder(*names[:-1])
            found = await self.client.folder(parent["id"], names[-1])
        else:
            found = await self._root()
        self._folders[key] = found
        return found

    async def _root(self) -> dict:
        setting = self.cfg.DRIVE_FOLDER.strip()
        folder_id = folder_id_from(setting)
        if folder_id:
            try:
                return await self.client.get_file(folder_id)
            except DriveError as exc:
                raise DriveError(f"DRIVE_FOLDER: the folder {setting!r} can't be opened ({exc})", exc.status) from exc
        current = {"id": "root", "name": "My Drive", "webViewLink": "https://drive.google.com/drive/my-drive"}
        for part in [p.strip() for p in setting.replace("\\", "/").split("/") if p.strip()]:
            current = await self.client.folder(current["id"], part)
        return current

    def forget_folders(self) -> None:
        """After a failed call a remembered folder may be gone (deleted in Drive): look them up again."""
        self._folders.clear()

    # ------------------------------------------------------------------
    # Searches
    # ------------------------------------------------------------------
    async def save_job(self, job: Job, business_folder: str) -> dict:
        """Upload a search's CSV to its business type's folder; returns {id, link, path}."""
        name = job_filename(job)
        content = rows_to_csv_bytes(job.rows, extended=True)
        file = await self._upload((business_folder,), name, content, job.drive_file_id)
        return {"id": file.get("id", ""), "link": file.get("webViewLink", ""), "path": f"{business_folder}/{name}"}

    # ------------------------------------------------------------------
    # Lead files
    # ------------------------------------------------------------------
    async def lead_files(self, business_folder: str) -> dict:
        """The CSV files and Google Sheets in a business type's folder (a campaign can run from them)."""
        folder = await self._call(self.folder, business_folder)
        files = await self._call(self.client.files, folder["id"])
        leads = [f for f in files if f.get("mimeType") in LEAD_FILE_TYPES or f.get("name", "").lower().endswith(".csv")]
        return {"folder": business_folder, "folder_link": folder.get("webViewLink", ""),
                "files": [{"id": f["id"], "name": f["name"], "modified": f.get("modifiedTime", ""),
                           "link": f.get("webViewLink", ""), "sheet": f.get("mimeType") == SHEET_TYPE}
                          for f in leads]}

    async def read_lead_file(self, file_id: str) -> tuple[dict, list[PlaceRow]]:
        """(the file, its rows) - raises DriveError when it isn't in the crawler's CSV format."""
        file = await self._call(self.client.get_file, file_id)
        content = await self._call(self.client.download, file)
        try:
            rows = rows_from_csv(content.decode("utf-8-sig", errors="replace"))
        except CsvFormatError as exc:
            raise DriveError(f"{file.get('name', file_id)}: {exc}") from exc
        return file, rows

    # ------------------------------------------------------------------
    # Campaign results
    # ------------------------------------------------------------------
    async def save_campaign(self, campaign: "Campaign", content: bytes) -> tuple[dict, str]:
        """Upload a campaign's results CSV; returns (the file, its path)."""
        business_folder = folder_name(campaign.business_type_name or "Other")
        name = f"{safe_filename(campaign.name)}_{campaign.id[-6:]}.csv"
        file = await self._upload((business_folder, OUTREACH_FOLDER), name, content, campaign.drive_file_id)
        return file, f"{business_folder}/{OUTREACH_FOLDER}/{name}"

    async def sync_campaigns(self, outreach: "OutreachManager", *, only: str = "", force: bool = False) -> int:
        """Upload the results of campaigns that changed (at most every RESULTS_EVERY unless ``force``)."""
        now = datetime.now(timezone.utc)
        uploaded = 0
        for campaign in list(outreach.campaigns.values()):
            if (only and campaign.id != only) or (campaign.status.value == "draft" and not only):
                continue
            content = outreach.results(campaign)
            digest = hashlib.sha1(content).hexdigest()
            if digest == campaign.drive_hash and not force:
                continue
            if (not force and campaign.drive_synced_at is not None
                    and now - campaign.drive_synced_at < RESULTS_EVERY):
                continue
            file, path = await self.save_campaign(campaign, content)
            outreach.record_drive(campaign, file, path, digest)
            uploaded += 1
            log.info("Google Drive: saved the results of %r to %s", campaign.name, path)
        return uploaded

    async def sync_master(self, tables: dict[str, tuple[list[str], list[list]]], *, force: bool = False) -> bool:
        """Write the master sheet (``tables``: campaigns / activity -> (header, rows)) when it changed, at most
        every RESULTS_EVERY: the Google Sheet, or CSV files while the Sheets API is off."""
        now = datetime.now(timezone.utc)
        digest = hashlib.sha1(json.dumps(tables, default=str).encode("utf-8")).hexdigest()
        try_sheet = self.sheets_off == "" or self._sheets_off_at is None or now - self._sheets_off_at >= SHEETS_RETRY
        retry = bool(self.sheets_off) and try_sheet
        if not force and not retry and (digest == self._master_hash
                                        or (self._master_at and now - self._master_at < RESULTS_EVERY)):
            return False
        if try_sheet:
            try:
                folder = await self.folder()
                result = await self._call(self.sheet.write, folder["id"], tables)
                self.master = {"sheet": {"link": result["link"], "at": now.isoformat()}}
                if self.sheets_off:
                    log.info("Google Drive: the Sheets API is on - the master sheet is a Google Sheet now")
                self.sheets_off, self._master_hash, self._master_at = "", digest, now
                log.info("Google Drive: master sheet saved (%s)", SHEET_NAME)
                return True
            except SheetsOff:
                if not self.sheets_off:
                    log.warning("Google Drive: the Google Sheets API is off - the master sheet is saved as CSV "
                                "files until it is switched on (docs/AUTOPILOT.md)")
                self.sheets_off = ("the Google Sheets API is off in your Google Cloud project - switch it on and "
                                   "the master sheet becomes a coloured Google Sheet")
                self._sheets_off_at = now
                self.last_error = ""
        if digest == self._master_hash and not force:
            return False
        for part, table in tables.items():
            known = self.master.get(part, {})
            file = await self._upload((), MASTER_FILES[part], to_csv(table), known.get("id", ""))
            self.master[part] = {"id": file.get("id", ""), "link": file.get("webViewLink", ""), "at": now.isoformat()}
        self.master.pop("sheet", None)
        self._master_hash, self._master_at = digest, now
        log.info("Google Drive: master sheet saved (%s)", ", ".join(MASTER_FILES.values()))
        return True

    def start_loop(self, outreach: "OutreachManager",
                   master: "Callable[[], dict[str, tuple[list[str], list[list]]]] | None" = None) -> None:
        """Keep campaign results (and, with ``master`` - it makes the master sheet's files - the master
        sheet) up to date in Drive."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(outreach, master), name="drive")

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self, outreach: "OutreachManager",
                    master: "Callable[[], dict[str, tuple[list[str], list[list]]]] | None") -> None:
        while True:
            await asyncio.sleep(LOOP_S)
            if not self.connected:
                continue
            try:
                await self.sync_campaigns(outreach)
                if master is not None:
                    await self.sync_master(master())
                    await self.backup.run_if_due()      # only where the master sheet is kept: the cloud server
                self.last_error = ""
            except DriveError as exc:
                self.last_error = str(exc)
                log.warning("Google Drive: could not save campaign results: %s", exc)
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Google Drive: unexpected error")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    async def _upload(self, folders: tuple[str, ...], name: str, content: bytes, file_id: str, *,
                      mime: str = "text/csv") -> dict:
        async def once() -> dict:
            folder = await self.folder(*folders)
            return await self.client.upload(folder["id"], name, content, mime=mime, file_id=file_id)
        return await self._call(once)

    async def _call(self, function, *args):
        """A Drive call; a 404 (a folder deleted in Drive meanwhile) is tried once more from scratch."""
        try:
            result = await function(*args)
        except DriveError as exc:
            if exc.status != 404 or not self._folders:
                self.last_error = str(exc)
                raise
            self.forget_folders()
            try:
                result = await function(*args)
            except DriveError as again:
                self.last_error = str(again)
                raise
        self.last_error = ""
        return result
