"""A daily backup of the panel's own data to Google Drive (docs/CLOUD.md §8).

Once a day, after ``BACKUP_HOUR`` (the computer's time), the files that exist nowhere else go into
``DRIVE_FOLDER/Backups/leadspilot-backup-YYYY-MM-DD.zip``: the campaigns, the contacted list, the business
types (their email texts), the autopilot, the activity log and the searches. The newest ``KEEP`` are kept;
older ones go to Drive's trash. Never in it: the sign-ins (``google_drive.json``, ``wrangler/``), the
preview websites (``sites/``, ``deploy/`` - online at Cloudflare) and the logs.

Restoring: unzip the newest one into the panel's ``data/`` while the panel is stopped (docs/CLOUD.md §10).
It runs in DriveSync's loop - on the cloud server, never on the search computer.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.config import Settings

if TYPE_CHECKING:
    from app.drive.sync import DriveSync

log = logging.getLogger(__name__)

FOLDER = "Backups"
PREFIX = "leadspilot-backup-"
KEEP = 7
BACKUP_HOUR = 3                 # 03:00, the computer's time - nothing is sent then
RETRY = timedelta(minutes=30)   # after a failed backup
FILES = ("contacted.json", "business_types.json", "autopilot.json", "activity.jsonl", "alerts.json",
         "master_sheet.json")
FOLDERS = ("campaigns", "jobs")
ZIP_TYPE = "application/zip"


def make_zip(data_dir: Path) -> bytes:
    """The backup: FILES and everything in FOLDERS, at their paths inside ``data/``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            path = data_dir / name
            if path.is_file():
                archive.writestr(name, path.read_bytes())
        for folder in FOLDERS:
            root = data_dir / folder
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.name.endswith((".part", ".tmp")):
                    archive.writestr(path.relative_to(data_dir).as_posix(), path.read_bytes())
    return buffer.getvalue()


class Backup:
    def __init__(self, cfg: Settings, sync: "DriveSync", clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.sync = sync
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state: dict = self._load()

    @property
    def _file(self) -> Path:
        return self.cfg.DATA_DIR / "backup.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            log.warning("could not write %s: %s", self._file, exc)

    def status(self) -> dict:
        """{"at", "name", "size", "link", "error"} of the last backup ('' / 0 when none yet)."""
        return {key: self.state.get(key, 0 if key == "size" else "") for key in ("at", "name", "size", "link", "error")}

    async def run_if_due(self) -> bool:
        now = self.clock()
        local = now.astimezone()
        if local.hour < BACKUP_HOUR or self.state.get("on") == local.date().isoformat():
            return False
        tried = self.state.get("tried")
        if tried and now - datetime.fromisoformat(tried) < RETRY:
            return False
        return await self.run(now)

    async def run(self, now: datetime | None = None) -> bool:
        """Make today's backup now; False (and ``error`` in the status) when it couldn't."""
        from app.drive.client import DriveError
        now = now or self.clock()
        local = now.astimezone()
        name = f"{PREFIX}{local.date().isoformat()}.zip"
        self.state["tried"] = now.isoformat()
        try:
            content = await asyncio.to_thread(make_zip, self.cfg.DATA_DIR)
            file = await self.sync._upload((FOLDER,), name, content, "", mime=ZIP_TYPE)
            await self._prune()
        except DriveError as exc:
            self.state["error"] = str(exc)
            self._save()
            log.warning("Google Drive: the backup failed: %s", exc)
            return False
        self.state.update({"on": local.date().isoformat(), "at": now.isoformat(timespec="seconds"), "name": name,
                           "size": len(content), "link": file.get("webViewLink", ""), "error": ""})
        self._save()
        log.info("Google Drive: backup saved (%s/%s, %d KB)", FOLDER, name, len(content) // 1024)
        return True

    async def _prune(self) -> None:
        folder = await self.sync.folder(FOLDER)
        files = await self.sync.client.files(folder["id"])
        backups = sorted((f for f in files if f.get("name", "").startswith(PREFIX) and f["name"].endswith(".zip")),
                         key=lambda f: f["name"], reverse=True)
        for old in backups[KEEP:]:
            await self.sync.client.trash(old["id"])
            log.info("Google Drive: old backup %s moved to the trash", old["name"])
