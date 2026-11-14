"""The master sheet as a Google Sheet: Status, Calls, Campaigns and Activity tabs, coloured (docs/AUTOPILOT.md).

Made once in ``DRIVE_FOLDER`` ("Leads Pilot - Master sheet"), formatted once - a purple header row that
stays at the top, filter buttons, column widths, rows coloured by status (Campaigns) or by what happened
(Activity) - and after that only its values are written. Needs the Google Sheets API switched on in the
Google Cloud project of ``GOOGLE_CLIENT_ID``; without it ``app/drive/sync.py`` writes CSV files instead.
``data/master_sheet.json`` remembers the sheet.

The Calls tab is the one you write in: its Outcome, Call back on and Notes columns are read back
(``read_calls``) before every write, so the panel keeps what you noted (app/outreach/calls.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.drive.client import API, SHEET_TYPE, DriveClient, DriveError
from app.outreach.calls import CALL_BACK_COL, CALL_COLUMNS, LEAD_COL, NOTES_COL, OUTCOME_COL, OUTCOMES, parse_day
from app.outreach.master import COUNT_COLUMNS

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
NAME = "Leads Pilot - Master sheet"
FORMAT_VERSION = 5                     # raise it to format existing sheets again
CAMPAIGNS, ACTIVITY, STATUS, CALLS = 0, 1, 2, 3   # sheet ids - Status is shown first, then Calls

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
TEAL_TAB = {"red": 0.06, "green": 0.44, "blue": 0.36}       # the Calls tab: the one you write in
FILL_IN_HEADER = {"red": 0.75, "green": 0.45, "blue": 0.02}  # header of the columns you fill in

#: rows coloured by the Status column (C) - paused carries its reason, so it is matched at the start
CAMPAIGN_COLOURS = [("^running", GREEN), ("^paused", YELLOW), ("^finished", BLUE), ("^nobody to email", GREY)]
#: rows coloured by What (B)
ACTIVITY_COLOURS = [
    ("^(Reply)$", GREEN),
    ("^(Website opened|Tapped call|Tapped email|Asked to keep website|Ended website opened|Call)$", TEAL),
    ("^(Bounce|Email refused|Autopilot stopped|Search blocked|Searches empty|Web check refused|Campaign paused|"
     "Campaign not started|"
     "Test copy not sent)$", RED),
    ("^(Email sent)$", BLUE),
    ("^(Campaign created|Campaign started|Campaign finished|Autopilot on)$", YELLOW),
    ("^(Search started|Search finished|Search stopped)$", LILAC),
    ("^(Automatic reply|Delivery delayed|Address skipped|Unsubscribe|Autopilot off|Campaign not made|"
     "Test copy sent)$", GREY),
]
CAMPAIGN_WIDTHS = [240, 105, 170, 125, 125] + [88] * len(COUNT_COLUMNS) + [125, 300]
ACTIVITY_WIDTHS = [125, 140, 210, 230, 240, 460]
STATUS_WIDTHS = [160, 900]
CALLS_WIDTHS = [36, 210, 120, 110, 250, 90, 160, 150, 130, 105, 300, 190, 110]
#: Calls rows coloured by Outcome (I)
CALLS_COLOURS = [("^(Interested|Signed up)$", GREEN), ("^Call back$", YELLOW),
                 ("^(Not interested|Wrong number)$", GREY), ("^(No answer|Left voicemail)$", BLUE)]
#: Status rows whose value needs a look (app/outreach/alerts.py WARN)
STATUS_WARN = "^\u26a0"
TITLES = {"status": "Status", "calls": "Calls", "campaigns": "Campaigns", "activity": "Activity"}


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

    async def read_calls(self) -> dict[str, dict]:
        """What you wrote in the Calls tab: {lead id: {"outcome", "call_back", "note"}} - {} before the sheet
        or its Calls tab exists, or when its header isn't the one this panel writes."""
        sheet_id = self.state.get("id", "")
        if not sheet_id or self.state.get("format") != FORMAT_VERSION:
            return {}
        try:
            response = await self._call("GET", f"{SHEETS_API}/{sheet_id}/values/Calls!A1:Z", None,
                                        params={"valueRenderOption": "UNFORMATTED_VALUE",
                                                "dateTimeRenderOption": "SERIAL_NUMBER"})
        except SheetsOff:
            raise
        except DriveError as exc:
            if exc.status in (400, 404):         # no Calls tab yet, or the sheet was deleted
                return {}
            raise
        rows = response.get("values", [])
        if not rows or rows[0][:len(CALL_COLUMNS)] != CALL_COLUMNS:
            return {}

        def cell(row: list, column: int):
            return row[column] if column < len(row) else ""

        edits = {}
        for row in rows[1:]:
            lead_id = str(cell(row, LEAD_COL)).strip()
            if lead_id:
                edits[lead_id] = {"outcome": str(cell(row, OUTCOME_COL)).strip(),
                                  "call_back": parse_day(cell(row, CALL_BACK_COL)),
                                  "note": str(cell(row, NOTES_COL)).strip()}
        return edits

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
        parts = [part for part in TITLES if part in tables]
        await self._call("POST", f"{SHEETS_API}/{sheet_id}/values:batchClear",
                         {"ranges": [f"{TITLES[part]}!A1:Z" for part in parts]})
        data = [{"range": f"{TITLES[part]}!A1", "values": [tables[part][0], *tables[part][1]]} for part in parts]
        await self._call("POST", f"{SHEETS_API}/{sheet_id}/values:batchUpdate",
                         {"valueInputOption": "RAW", "data": data})

    async def _format(self, sheet_id: str) -> None:
        response = await self._call("GET", f"{SHEETS_API}/{sheet_id}", None,
                                    params={"fields": "sheets(properties(sheetId,title),conditionalFormats)"})
        sheets = {s["properties"]["sheetId"]: s for s in response.get("sheets", [])}
        requests: list[dict] = []
        if CAMPAIGNS not in sheets or set(sheets) - {CAMPAIGNS, ACTIVITY, STATUS, CALLS}:   # a fresh one's only tab is id 0
            raise DriveError("the master sheet doesn't look like one this panel made", 409)
        requests.append(_properties(CAMPAIGNS, "Campaigns", HEADER, frozen_columns=1))
        if ACTIVITY in sheets:
            requests.append(_properties(ACTIVITY, "Activity", HEADER))
        else:
            requests.append({"addSheet": {"properties": {"sheetId": ACTIVITY, "title": "Activity",
                                                         "gridProperties": {"frozenRowCount": 1}}}})
            sheets[ACTIVITY] = {}
        if STATUS in sheets:
            requests.append(_properties(STATUS, "Status", HEADER))
        else:
            requests.append({"addSheet": {"properties": {"sheetId": STATUS, "title": "Status",
                                                         "gridProperties": {"frozenRowCount": 1}}}})
            sheets[STATUS] = {}
        if CALLS in sheets:
            requests.append(_properties(CALLS, "Calls", TEAL_TAB, frozen_columns=2))
        else:
            requests.append({"addSheet": {"properties": {"sheetId": CALLS, "title": "Calls", "tabColor": TEAL_TAB,
                                                         "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}}}})
            sheets[CALLS] = {}
        requests.append({"updateSheetProperties": {"properties": {"sheetId": STATUS, "index": 0}, "fields": "index"}})
        requests.append({"updateSheetProperties": {"properties": {"sheetId": CALLS, "index": 1}, "fields": "index"}})
        for sid in (CAMPAIGNS, ACTIVITY, STATUS, CALLS):      # formatting again: the old colour rules go
            for _ in sheets[sid].get("conditionalFormats", []):
                requests.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}})
        for sid, widths in ((CAMPAIGNS, CAMPAIGN_WIDTHS), (ACTIVITY, ACTIVITY_WIDTHS), (CALLS, CALLS_WIDTHS)):
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
        requests.append(_header(STATUS))                      # Status: two wide columns, wrapped, red when needed
        requests += [{"updateDimensionProperties": {
            "range": {"sheetId": STATUS, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize"}} for i, width in enumerate(STATUS_WIDTHS)]
        requests.append({"repeatCell": {
            "range": {"sheetId": STATUS, "startRowIndex": 1},
            "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
                                           "textFormat": {"fontSize": 11}}},
            "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)"}})
        requests.append({"repeatCell": {
            "range": {"sheetId": STATUS, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}})
        requests.append(_colour_rule(STATUS, 1, len(STATUS_WIDTHS), "B", STATUS_WARN, RED))
        requests += _calls_format()
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


def _calls_format() -> list[dict]:
    """The Calls tab: an Outcome dropdown, a date picker for Call back on, an orange header over the columns
    you fill in, rows coloured by outcome."""
    def column(index: int) -> dict:
        return {"sheetId": CALLS, "startRowIndex": 1, "startColumnIndex": index, "endColumnIndex": index + 1}

    requests = [
        {"setDataValidation": {"range": column(OUTCOME_COL), "rule": {
            "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in OUTCOMES]},
            "strict": False, "showCustomUi": True}}},
        {"setDataValidation": {"range": column(CALL_BACK_COL), "rule": {
            "condition": {"type": "DATE_IS_VALID"}, "strict": False}}},
        {"repeatCell": {"range": column(CALL_BACK_COL),
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "ddd d mmm"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": CALLS, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": OUTCOME_COL, "endColumnIndex": NOTES_COL + 1},
                        "cell": {"userEnteredFormat": {"backgroundColor": FILL_IN_HEADER}},
                        "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {"range": {"sheetId": CALLS, "startRowIndex": 1, "startColumnIndex": NOTES_COL,
                                  "endColumnIndex": NOTES_COL + 1},
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.wrapStrategy"}},
    ]
    requests += [_colour_rule(CALLS, 1, len(CALLS_WIDTHS), "I", pattern, colour) for pattern, colour in CALLS_COLOURS]
    return requests


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
