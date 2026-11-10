"""Google Drive: reading lead files, signing in, the REST calls and where files go - against a fake Google."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import Settings
from app.drive.client import (FOLDER_TYPE, SCOPE, SHEET_TYPE, DriveAuthError, DriveClient, DriveError,
                              folder_id_from)
from app.drive.sync import DriveSync, folder_name
from app.export.csv_export import rows_to_csv_bytes
from app.export.csv_import import CsvFormatError, rows_from_csv
from app.models import Job, JobStatus, PlaceRow


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# lead files
# --------------------------------------------------------------------------
def test_rows_from_the_crawlers_own_csv():
    # the export orders one business's rows by row id - fixed ids keep the order the test expects
    rows = [PlaceRow(row_id="r_1", rank=3, name="Palm Breeze Plumbing", email="palm@gmail.com", city="Houston",
                     rating=4.8, review_count=57, place_id="p1", phone="(713) 555-0101"),
            PlaceRow(row_id="r_2", rank=3, name="Palm Breeze Plumbing", email="office@gmail.com", city="Houston",
                     place_id="p1"),
            PlaceRow(row_id="r_3", rank=9, name="Lia Constructions", email="lia@gmail.com", place_id="p2")]
    for extended in (False, True):
        back = rows_from_csv(rows_to_csv_bytes(rows, extended=extended).decode("utf-8"))
        assert [(r.name, r.email, r.rank) for r in back] == [
            ("Palm Breeze Plumbing", "palm@gmail.com", 1), ("Palm Breeze Plumbing", "office@gmail.com", 1),
            ("Lia Constructions", "lia@gmail.com", 2)]
        assert back[0].review_count == 57 and back[0].city == "Houston"
        assert back[0].rating == (4.8 if extended else None)


def test_rows_from_a_hand_made_file():
    text = "﻿name;EMAIL;City;Rating;Notes\nAcme Roofing;acme@gmail.com;Katy;4,6;call first\n;x@y.com;;;\n"
    rows = rows_from_csv(text)
    assert len(rows) == 1 and rows[0].name == "Acme Roofing" and rows[0].rating == 4.6 and rows[0].city == "Katy"
    with pytest.raises(CsvFormatError, match="no Email column"):
        rows_from_csv("Name,Phone\nAcme,123\n")
    with pytest.raises(CsvFormatError, match="empty"):
        rows_from_csv("")


def test_folder_names_and_ids():
    assert folder_name("plumber") == "Plumber" and folder_name("") == "Other" and folder_name("a/b") == "A b"
    assert folder_id_from("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWx?usp=sharing") == \
        "1AbCdEfGhIjKlMnOpQrStUvWx"
    assert folder_id_from("1AbCdEfGhIjKlMnOpQrStUvWx") == "1AbCdEfGhIjKlMnOpQrStUvWx"
    assert folder_id_from("Leads") == "" and folder_id_from("Leads/2026") == ""


# --------------------------------------------------------------------------
# a fake Google
# --------------------------------------------------------------------------
class FakeGoogle:
    """Token endpoint + a tiny in-memory Drive (folders, files, uploads)."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.refreshes = 0
        self.expire_next = False
        self._next = 0

    def add(self, name: str, parent: str, mime: str = "text/csv", content: bytes = b"") -> str:
        self._next += 1
        file_id = f"id{self._next:022d}"
        self.files[file_id] = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent],
                               "modifiedTime": "2026-09-29T10:00:00Z", "webViewLink": f"https://drive/{file_id}"}
        self.content[file_id] = content
        return file_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append((request.method, url.path))
        if url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            if url.path == "/revoke":
                return httpx.Response(200)
            if form["grant_type"] == ["authorization_code"]:
                assert form["code"] == ["good-code"] and form["code_verifier"][0]
                return httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 3600,
                                                 "scope": SCOPE})
            self.refreshes += 1
            if form["refresh_token"] == ["revoked"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"a{self.refreshes + 1}", "expires_in": 3600})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"error": {"message": "expired"}})
        path = url.path
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "pat@gmail.com"}})
        if path == "/drive/v3/files" and request.method == "GET":
            query = url.params["q"]
            parent = query.split("'")[1]
            found = [f for f in self.files.values() if parent in f["parents"]]
            if "name = '" in query:
                name = query.split("name = '")[1].split("' and")[0].replace("\\'", "'")
                found = [f for f in found if f["name"] == name]
            folders = "mimeType = 'application/vnd.google-apps.folder'" in query
            found = [f for f in found if (f["mimeType"] == FOLDER_TYPE) == folders]
            return httpx.Response(200, json={"files": found})
        if path == "/drive/v3/files" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(200, json=self.files[self.add(body["name"], body["parents"][0], body["mimeType"])])
        if path == "/upload/drive/v3/files" and request.method == "POST":
            boundary = request.headers["content-type"].split("boundary=")[1].encode()
            parts = request.content.split(b"--" + boundary)
            metadata = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].strip())
            data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
            file_id = self.add(metadata["name"], metadata["parents"][0], metadata["mimeType"], data)
            return httpx.Response(200, json=self.files[file_id])
        if path.startswith("/upload/drive/v3/files/") and request.method == "PATCH":
            file_id = path.rsplit("/", 1)[1]
            if file_id not in self.files:
                return httpx.Response(404, json={"error": {"message": "File not found", "errors": [{"reason": "notFound"}]}})
            self.content[file_id] = request.content
            return httpx.Response(200, json=self.files[file_id])
        if path.startswith("/drive/v3/files/"):
            file_id = path.split("/")[4]
            if file_id not in self.files:
                return httpx.Response(404, json={"error": {"message": "File not found"}})
            if path.endswith("/export"):
                return httpx.Response(200, content=self.content[file_id])
            if url.params.get("alt") == "media":
                return httpx.Response(200, content=self.content[file_id])
            return httpx.Response(200, json=self.files[file_id])
        return httpx.Response(500, json={"error": {"message": f"unexpected {request.method} {path}"}})


def make_client(tmp_path, google: FakeGoogle, **overrides) -> DriveClient:
    cfg = Settings(_env_file=None, DATA_DIR=tmp_path, GOOGLE_CLIENT_ID="cid.apps.googleusercontent.com",
                   GOOGLE_CLIENT_SECRET="secret", **overrides)
    return DriveClient(cfg, transport=httpx.MockTransport(google.handler))


def signed_in(tmp_path, google: FakeGoogle, **overrides) -> DriveClient:
    client = make_client(tmp_path, google, **overrides)
    url = client.auth_url("http://127.0.0.1:8000/")
    state = parse_qs(urlparse(url).query)["state"][0]
    assert run(client.finish_sign_in("good-code", state)) == "pat@gmail.com"
    return client


# --------------------------------------------------------------------------
# signing in
# --------------------------------------------------------------------------
def test_sign_in_and_the_token_file(tmp_path):
    google = FakeGoogle()
    client = make_client(tmp_path, google)
    assert client.configured and not client.connected
    url = client.auth_url("http://127.0.0.1:8000/")
    params = parse_qs(urlparse(url).query)
    assert params["redirect_uri"] == ["http://127.0.0.1:8000/"] and params["scope"] == [SCOPE]
    assert params["access_type"] == ["offline"] and params["code_challenge_method"] == ["S256"]
    with pytest.raises(DriveAuthError, match="expired"):
        run(client.finish_sign_in("good-code", "made-up-state"))
    run(client.finish_sign_in("good-code", params["state"][0]))
    assert client.connected and client.email == "pat@gmail.com"
    again = make_client(tmp_path, google)                     # read back from data/google_drive.json
    assert again.connected and again.email == "pat@gmail.com"
    run(again.disconnect())
    assert not again.connected and not (tmp_path / "google_drive.json").exists()


def test_expired_access_token_is_refreshed(tmp_path):
    google = FakeGoogle()
    client = signed_in(tmp_path, google)
    google.expire_next = True
    run(client.get_file(google.add("x.csv", "root")))
    assert google.refreshes == 1


def test_revoked_sign_in(tmp_path):
    google = FakeGoogle()
    client = signed_in(tmp_path, google)
    lost: list[bool] = []
    client.on_lost = lambda: lost.append(True)
    client.token.refresh_token = "revoked"
    client.token.expires_at = 0
    with pytest.raises(DriveAuthError, match="connect Google Drive again"):
        run(client.get_file("anything"))
    assert not client.connected and lost == [True]           # reported once: the sign-in is forgotten now
    with pytest.raises(DriveAuthError):
        run(client.get_file("anything"))
    assert lost == [True]


# --------------------------------------------------------------------------
# where files go
# --------------------------------------------------------------------------
def roofing_job() -> Job:
    job = Job(query="roofing in Houston", business="roofing", place="Houston", limit=5, status=JobStatus.COMPLETED,
              created_at=datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
    job.rows.append(PlaceRow(rank=1, name="Acme Roofing", email="acme@gmail.com", city="Houston", rating=4.9))
    return job


def test_a_search_goes_to_its_business_types_folder(tmp_path):
    google = FakeGoogle()
    client = signed_in(tmp_path, google, DRIVE_FOLDER="Leads")
    sync = DriveSync(client.cfg, client)
    job = roofing_job()
    info = run(sync.save_job(job, "Roofer"))
    assert info["path"] == "Roofer/roofing_in_houston_29_09_2026.csv"
    names = {f["name"]: f for f in google.files.values()}
    leads, roofer = names["Leads"], names["Roofer"]
    assert leads["parents"] == ["root"] and roofer["parents"] == [leads["id"]]
    uploaded = names["roofing_in_houston_29_09_2026.csv"]
    assert uploaded["parents"] == [roofer["id"]]
    assert b"Acme Roofing" in google.content[uploaded["id"]] and b"Rating" in google.content[uploaded["id"]]
    # saved again (after an enrichment): the same file, no new folders
    job.drive_file_id = info["id"]
    job.rows[0].email = "hello@acme.com"
    before = len(google.files)
    run(sync.save_job(job, "Roofer"))
    assert len(google.files) == before and b"hello@acme.com" in google.content[uploaded["id"]]


def test_the_file_is_made_again_when_it_was_deleted_in_drive(tmp_path):
    google = FakeGoogle()
    client = signed_in(tmp_path, google)
    sync = DriveSync(client.cfg, client)
    job = roofing_job()
    job.drive_file_id = "id0000000000000000009999"                 # gone
    info = run(sync.save_job(job, "Roofer"))
    assert info["id"] != job.drive_file_id and info["id"] in google.files


def test_lead_files_of_a_folder_and_reading_one(tmp_path):
    google = FakeGoogle()
    client = signed_in(tmp_path, google)
    sync = DriveSync(client.cfg, client)
    folder = run(sync.folder("Plumber"))
    csv_id = google.add("my list.csv", folder["id"], content=b"Name,Email\nPalm Breeze,palm@gmail.com\n")
    sheet_id = google.add("cleaned", folder["id"], SHEET_TYPE, content=b"Name,Email,City\nLia,lia@gmail.com,Katy\n")
    google.add("notes.pdf", folder["id"], "application/pdf")
    google.add("Outreach", folder["id"], FOLDER_TYPE)
    listed = run(sync.lead_files("Plumber"))
    assert [f["name"] for f in listed["files"]] == ["my list.csv", "cleaned"]
    assert [f["sheet"] for f in listed["files"]] == [False, True]
    file, rows = run(sync.read_lead_file(sheet_id))
    assert file["name"] == "cleaned" and rows[0].name == "Lia" and rows[0].city == "Katy"
    google.content[csv_id] = b"Business,Phone\nx,1\n"
    with pytest.raises(DriveError, match="my list.csv: the file has no Name and no Email column"):
        run(sync.read_lead_file(csv_id))


def test_a_shared_folder_by_its_url(tmp_path):
    google = FakeGoogle()
    shared = google.add("Leads (shared)", "someone-elses-drive", FOLDER_TYPE)
    client = signed_in(tmp_path, google, DRIVE_FOLDER=f"https://drive.google.com/drive/folders/{shared}")
    sync = DriveSync(client.cfg, client)
    assert run(sync.folder("Roofer"))["parents"] == [shared]
    bad = signed_in(tmp_path, google, DRIVE_FOLDER="https://drive.google.com/drive/folders/1NoSuchFolderAtAllXXXXXX")
    with pytest.raises(DriveError, match="DRIVE_FOLDER"):
        run(DriveSync(bad.cfg, bad).folder("Roofer"))
