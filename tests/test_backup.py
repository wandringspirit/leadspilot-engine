"""The daily backup of the panel's own data to Google Drive (app/drive/backup.py)."""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date, datetime, timedelta

from app.config import Settings
from app.drive.backup import KEEP, Backup, make_zip
from app.drive.client import DriveError


def data_dir(tmp_path):
    data = tmp_path / "data"
    (data / "campaigns").mkdir(parents=True)
    (data / "jobs").mkdir()
    (data / "wrangler").mkdir()
    (data / "sites").mkdir()
    (data / "campaigns" / "c1.json").write_text('{"name": "Roofer"}', encoding="utf-8")
    (data / "jobs" / "j1.json").write_text("{}", encoding="utf-8")
    for name in ("contacted.json", "business_types.json", "autopilot.json", "activity.jsonl",
                 "google_drive.json", "wrangler/config.toml", "sites/index.html"):
        (data / name).write_text("x", encoding="utf-8")
    return data


def test_the_backup_has_the_data_but_no_sign_ins(tmp_path):
    names = zipfile.ZipFile(io.BytesIO(make_zip(data_dir(tmp_path)))).namelist()
    assert {"campaigns/c1.json", "jobs/j1.json", "contacted.json", "business_types.json", "autopilot.json",
            "activity.jsonl"} <= set(names)
    assert not any(n.startswith(("google_drive", "wrangler", "sites")) for n in names)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


class FakeSync:
    """Drive: a Backups folder holding files by name."""

    def __init__(self):
        days = [date(2026, 10, 1) + timedelta(days=i) for i in range(KEEP + 5)]     # more than are kept
        self.stored: dict[str, bytes] = {f"leadspilot-backup-{d.isoformat()}.zip": b"old" for d in days}
        self.trashed: list[str] = []
        self.fail = False
        self.client = self

    async def _upload(self, folders, name, content, file_id, *, mime="text/csv"):
        if self.fail:
            raise DriveError("Google Drive: 500", 500)
        assert folders == ("Backups",) and mime == "application/zip"
        self.stored[name] = content
        return {"id": name, "webViewLink": f"https://drive.google.com/{name}"}

    async def folder(self, *names):
        return {"id": "backups"}

    async def files(self, folder_id):                    # the client's files() - same object
        return [{"id": n, "name": n} for n in self.stored]

    async def trash(self, file_id):
        self.trashed.append(file_id)
        del self.stored[file_id]


def test_once_a_day_after_three_keeping_the_newest_seven(tmp_path):
    cfg = Settings(_env_file=None, DATA_DIR=data_dir(tmp_path))
    sync = FakeSync()
    clock = Clock(datetime(2026, 11, 12, 2, 30).astimezone())
    backup = Backup(cfg, sync, clock=clock)
    assert not asyncio.run(backup.run_if_due())                       # before 03:00
    clock.at = datetime(2026, 11, 12, 3, 5).astimezone()
    assert asyncio.run(backup.run_if_due())
    assert "leadspilot-backup-2026-11-12.zip" in sync.stored and len(sync.stored) == KEEP
    oldest = [f"leadspilot-backup-{(date(2026, 10, 1) + timedelta(days=i)).isoformat()}.zip" for i in range(6)]
    assert sorted(sync.trashed) == oldest                               # the oldest go to the trash
    status = backup.status()
    assert status["name"] == "leadspilot-backup-2026-11-12.zip" and status["size"] > 0 and not status["error"]
    clock.at += timedelta(hours=5)
    assert not asyncio.run(backup.run_if_due())                       # once a day ...
    assert not asyncio.run(Backup(cfg, sync, clock=clock).run_if_due())   # ... also after a restart

    sync.fail = True                                                   # the next day Drive fails
    clock.at = datetime(2026, 11, 13, 3, 1).astimezone()
    assert not asyncio.run(backup.run_if_due()) and "500" in backup.status()["error"]
    clock.at += timedelta(minutes=10)
    sync.fail = False
    assert not asyncio.run(backup.run_if_due())                       # retried after 30 minutes
    clock.at += timedelta(minutes=25)
    assert asyncio.run(backup.run_if_due()) and not backup.status()["error"]
