"""The master sheet in Google Drive: a coloured Google Sheet, or CSV files while the Sheets API is off
(app/drive/sheets.py, app/drive/sync.py). A fake Drive client: nothing goes to Google."""
from __future__ import annotations

from app.drive.client import DriveError
from app.drive.sync import MASTER_FILES, DriveSync
from tests.test_outreach import make_settings

TABLES = {"status": (["What", "Now"], [["Sending", "on"], ["Autopilot", "⚠ STOPPED - bounces"]]),
          "campaigns": (["Campaign", "Status"], [["All campaigns (1)", ""], ["Roofer · Katy, TX", "running"]]),
          "activity": (["When", "What"], [["2026-10-16 18:30", "Email sent"]])}


class Response:
    def __init__(self, data: dict) -> None:
        self.data, self.content = data, b"{}"

    def json(self) -> dict:
        return self.data


class FakeClient:
    connected = configured = True
    email = "me@gmail.com"

    def __init__(self, sheets_on: bool) -> None:
        self.sheets_on = sheets_on
        self.calls: list[tuple[str, str, dict]] = []
        self.uploads: list[str] = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json") or {}))
        if "sheets.googleapis.com" in url:
            if not self.sheets_on:
                raise DriveError("Google Drive: 403 - Google Sheets API has not been used in project 1 before "
                                 "or it is disabled.", 403)
            if method == "GET":
                return Response({"sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]})
            return Response({})
        return Response({"id": "sheet1", "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1"})

    async def upload(self, folder_id, name, content, *, mime="text/csv", file_id=""):
        self.uploads.append(name)
        return {"id": name, "webViewLink": f"https://drive.google.com/{name}"}


async def test_the_master_sheet_is_a_coloured_google_sheet(tmp_path):
    client = FakeClient(sheets_on=True)
    drive = DriveSync(make_settings(tmp_path), client=client)
    assert await drive.sync_master(TABLES)
    assert drive.status()["master"]["sheet"]["link"].endswith("/sheet1") and drive.status()["sheets_off"] == ""
    made = next(body for method, url, body in client.calls if url.endswith("/files"))
    assert made["mimeType"] == "application/vnd.google-apps.spreadsheet" and made["name"] == "Leads Pilot - Master sheet"
    formatting = next(body for method, url, body in client.calls if url.endswith(":batchUpdate"))
    kinds = [next(iter(r)) for r in formatting["requests"]]
    assert {"addSheet", "updateSheetProperties", "repeatCell", "setBasicFilter", "addConditionalFormatRule"} <= set(kinds)
    values = next(body for method, url, body in client.calls if url.endswith("values:batchUpdate"))
    written = {d["range"]: d["values"] for d in values["data"]}
    assert written["Campaigns!A1"][2] == ["Roofer · Katy, TX", "running"]
    assert written["Status!A1"][0] == ["What", "Now"] and written["Status!A1"][2][1].startswith("⚠")
    tabs = [r["addSheet"]["properties"] for r in formatting["requests"] if "addSheet" in r]
    assert [t["title"] for t in tabs] == ["Activity", "Status", "Calls"]
    assert {"sheetId": 2, "index": 0} in [r["updateSheetProperties"]["properties"] for r in formatting["requests"]
                                          if "updateSheetProperties" in r]           # Status is the first tab
    assert not client.uploads
    # unchanged: nothing written; formatted only once, even after a restart
    assert not await drive.sync_master(TABLES)
    again = DriveSync(make_settings(tmp_path), client=client)
    client.calls.clear()
    assert await again.sync_master(TABLES, force=True)
    assert not any(url.endswith(":batchUpdate") and not url.endswith("values:batchUpdate") for _, url, _ in client.calls)


async def test_without_the_sheets_api_it_is_csv_files_until_it_is_switched_on(tmp_path):
    client = FakeClient(sheets_on=False)
    drive = DriveSync(make_settings(tmp_path), client=client)
    assert await drive.sync_master(TABLES)
    assert sorted(client.uploads) == sorted(MASTER_FILES[part] for part in TABLES)
    assert "Sheets API is off" in drive.status()["sheets_off"] and drive.last_error == ""
    # switched on: the next try (after SHEETS_RETRY) makes the sheet, even with nothing changed
    client.sheets_on = True
    drive._sheets_off_at = drive._sheets_off_at.replace(year=2000)
    assert await drive.sync_master(TABLES)
    assert "sheet" in drive.status()["master"] and drive.status()["sheets_off"] == ""
