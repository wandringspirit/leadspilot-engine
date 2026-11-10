"""The master sheet as a Google Sheet: a Campaigns tab and an Activity tab, coloured (docs/AUTOPILOT.md).

Made once in ``DRIVE_FOLDER`` ("Leads Pilot - Master sheet"), formatted once - a purple header row that
stays at the top, filter buttons, column widths, rows coloured by status (Campaigns) or by what happened
(Activity) - and after that only its values are written. Needs the Google Sheets API switched on in the
Google Cloud project of ``GOOGLE_CLIENT_ID``; without it ``app/drive/sync.py`` writes CSV files instead.
``data/master_sheet.json`` remembers the sheet.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.drive.client import API, SHEET_TYPE, DriveClient, DriveError
from app.outreach.master import COUNT_COLUMNS

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
NAME = "Leads Pilot - Master sheet"
FORMAT_VERSION = 3                     # raise it to format existing sheets again
CAMPAIGNS, ACTIVITY = 0, 1             # sheet ids

HEADER = {"red": 0.29, "green": 0.11, "blue": 0.43}         # deep purple
WHITE = {"red": 1, "green": 1, "blue": 1}
TOTAL = {"red": 0.91, "green": 0.89, "blue": 0.95}
GREEN = {"red": 0.85, "green": 0.94, "blue": 0.85}
YELLOW = {"red": 1, "green": 0.95, "blue": 0.78}
RED = {"red": 0.98, "green": 0.84, "blue": 0.84}
BLUE = {"red": 0.87, "green": 0.92, "blue": 0.98}
LILAC = {"red": 0.93, "green": 0.9, "blue": 0.98}
GREY = {"red": 0.94, "green": 0.94, "blue": 0.94}
TEAL = {"red": 0.8, "green": 0.94, "blue": 0.92}

#: rows coloured by the Status column (C) - paused carries its reason, so it is matched at the start
CAMPAIGN_COLOURS = [("^running", GREEN), ("^paused", YELLOW), ("^finished", BLUE), ("^nobody to email", GREY)]
#: rows coloured by What (B)
ACTIVITY_COLOURS = [
    ("^(Reply)$", GREEN),
    ("^(Website opened|Tapped call|Tapped email|Asked to keep website|Ended website opened)$", TEAL),
    ("^(Bounce|Email refused|Autopilot stopped|Search blocked|Campaign paused|Campaign not started|"
     "Test copy not sent)$", RED),
    ("^(Email sent)$", BLUE),
    ("^(Campaign created|Campaign started|Campaign finished|Autopilot on)$", YELLOW),
    ("^(Search started|Search finished|Search stopped)$", LILAC),
    ("^(Automatic reply|Delivery delayed|Address skipped|Unsubscribe|Autopilot off|Campaign not made|"
     "Test copy sent)$", GREY),
]
CAMPAIGN_WIDTHS = [240, 105, 170, 125, 125] + [88] * len(COUNT_COLUMNS) + [125, 300]
ACTIVITY_WIDTHS = [125, 140, 210, 230, 240, 460]


class SheetsOff(DriveError):
    """The Sheets API isn't switched on in the Google Cloud project."""


class MasterSheet:
    def __init__(self, client: DriveClient, state_file: Path) -> None:
        self.client = client
        self.state_file = state_file
        self.state: dict = {}
        if state_file.is_file():
            try:
                self.state = json.loads(state_file.read_text(encoding="utf-8"))
            except ValueError:
                self.state = {}

    @property
    def link(self) -> str:
        return self.state.get("link", "")

    async def write(self, folder_id: str, tables: dict[str, tuple[list[str], list[list]]]) -> dict:
        """Write ``tables`` ({"campaigns": (header, rows), "activity": …}); makes the sheet when needed."""
        sheet_id = await self._sheet(folder_id)
        try:
            await self._values(sheet_id, tables)
        except DriveError as exc:
            if exc.status != 404:
                raise
            self.state = {}                                  # deleted in Drive: a new one
            sheet_id = await self._sheet(folder_id)
            await self._values(sheet_id, tables)
        return {"id": sheet_id, "link": self.link}

    async def _sheet(self, folder_id: str) -> str:
        sheet_id = self.state.get("id", "")
        if not sheet_id:
            response = await self.client.request(
                "POST", f"{API}/files", params={"fields": "id,webViewLink", "supportsAllDrives": "true"},
                json={"name": NAME, "parents": [folder_id], "mimeType": SHEET_TYPE})
            made = response.json()
            sheet_id = made["id"]
            self.state = {"id": sheet_id, "link": made.get("webViewLink", ""), "format": 0}
            self._save()
        if self.state.get("format") != FORMAT_VERSION:
            await self._format(sheet_id)
            self.state["format"] = FORMAT_VERSION
            self._save()
        return sheet_id

    async def _values(self, sheet_id: str, tables: dict) -> None:
        await self._call("POST", f"{SHEETS_API}/{sheet_id}/values:batchClear",
                         {"ranges": ["Campaigns!A1:Z", "Activity!A1:Z"]})
        data = [{"range": f"{title}!A1", "values": [header, *rows]}
                for title, (header, rows) in (("Campaigns", tables["campaigns"]), ("Activity", tables["activity"]))]
        await self._call("POST", f"{SHEETS_API}/{sheet_id}/values:batchUpdate",
                         {"valueInputOption": "RAW", "data": data})

    async def _format(self, sheet_id: str) -> None:
        response = await self._call("GET", f"{SHEETS_API}/{sheet_id}", None,
                                    params={"fields": "sheets(properties(sheetId,title),conditionalFormats)"})
        sheets = {s["properties"]["sheetId"]: s for s in response.get("sheets", [])}
        requests: list[dict] = []
        first = next(iter(sheets), CAMPAIGNS)
        if first != CAMPAIGNS or CAMPAIGNS not in sheets:      # a fresh spreadsheet's only tab is id 0
            raise DriveError("the master sheet doesn't look like one this panel made", 409)
        requests.append(_properties(CAMPAIGNS, "Campaigns", HEADER, frozen_columns=1))
        if ACTIVITY in sheets:
            requests.append(_properties(ACTIVITY, "Activity", HEADER))
        else:
            requests.append({"addSheet": {"properties": {"sheetId": ACTIVITY, "title": "Activity",
                                                         "gridProperties": {"frozenRowCount": 1}}}})
            sheets[ACTIVITY] = {}
        for sid in (CAMPAIGNS, ACTIVITY):                     # formatting again: the old colour rules go
            for _ in sheets[sid].get("conditionalFormats", []):
                requests.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}})
        for sid, widths in ((CAMPAIGNS, CAMPAIGN_WIDTHS), (ACTIVITY, ACTIVITY_WIDTHS)):
            requests += [_header(sid), _filter(sid, len(widths))]
            requests += [{"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": width}, "fields": "pixelSize"}} for i, width in enumerate(widths)]
            requests.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 1},
                "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
                                               "textFormat": {"fontSize": 10}}},
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)"}})
        requests.append({"repeatCell": {                      # the counts, centred
            "range": {"sheetId": CAMPAIGNS, "startRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 5 + len(COUNT_COLUMNS)},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}})
        requests.append({"repeatCell": {                      # the All campaigns row
            "range": {"sheetId": CAMPAIGNS, "startRowIndex": 1, "endRowIndex": 2},
            "cell": {"userEnteredFormat": {"backgroundColor": TOTAL, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}})
        requests += [_colour_rule(CAMPAIGNS, 2, len(CAMPAIGN_WIDTHS), "C", pattern, colour)
                     for pattern, colour in CAMPAIGN_COLOURS]
        requests += [_colour_rule(ACTIVITY, 1, len(ACTIVITY_WIDTHS), "B", pattern, colour)
                     for pattern, colour in ACTIVITY_COLOURS]
        await self._call("POST", f"{SHEETS_API}/{sheet_id}:batchUpdate", {"requests": requests})

    async def _call(self, method: str, url: str, body: dict | None, **kwargs) -> dict:
        try:
            response = await self.client.request(method, url, **({"json": body} if body is not None else {}), **kwargs)
        except DriveError as exc:
            text = str(exc)
            if exc.status == 403 and ("has not been used" in text or "is disabled" in text or "SERVICE_DISABLED" in text):
                raise SheetsOff(text, 403) from exc
            raise
        return response.json() if response.content else {}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")


def _properties(sheet_id: int, title: str, colour: dict, frozen_columns: int = 0) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "title": title, "tabColor": colour,
                       "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": frozen_columns}},
        "fields": "title,tabColor,gridProperties(frozenRowCount,frozenColumnCount)"}}


def _header(sheet_id: int) -> dict:
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {
            "backgroundColor": HEADER, "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": WHITE}}},
        "fields": "userEnteredFormat(backgroundColor,verticalAlignment,wrapStrategy,textFormat)"}}


def _filter(sheet_id: int, columns: int) -> dict:
    return {"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0,
                                                    "startColumnIndex": 0, "endColumnIndex": columns}}}}


def _colour_rule(sheet_id: int, first_row: int, columns: int, column: str, pattern: str, colour: dict) -> dict:
    """Colour whole rows whose ``column`` matches ``pattern`` (from row index ``first_row`` down)."""
    formula = f'=REGEXMATCH(${column}{first_row + 1}, "{pattern}")'
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sheet_id, "startRowIndex": first_row, "startColumnIndex": 0, "endColumnIndex": columns}],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                        "format": {"backgroundColor": colour}}}}}
