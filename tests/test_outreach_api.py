"""The outreach and Google Drive routes through FastAPI's TestClient - fake mailbox, fake Google, no network."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.drive.client import DriveClient
from app.drive.sync import DriveSync
from app.jobs import JobManager
from app.models import JobStatus
from app.outreach.manager import OutreachManager
from tests.test_drive import FakeGoogle
from tests.test_outreach import TUESDAY_9AM, Clock, FakeMailbox, make_settings, plumbers_job


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(settings)
    cfg = make_settings(tmp_path, OUTREACH_AUTOSEND=False, GOOGLE_CLIENT_ID="cid.apps.googleusercontent.com",
                        GOOGLE_CLIENT_SECRET="secret")
    mailbox = FakeMailbox()
    outreach = OutreachManager(cfg, mailbox=mailbox, clock=Clock(TUESDAY_9AM))
    google = FakeGoogle()
    drive = DriveSync(cfg, DriveClient(cfg, transport=httpx.MockTransport(google.handler)))
    monkeypatch.setattr(main_module, "manager", manager)
    monkeypatch.setattr(main_module, "outreach", outreach)
    monkeypatch.setattr(main_module, "drive", drive)
    with TestClient(main_module.app) as client:
        yield client, manager, outreach, mailbox, google


def seed(manager: JobManager, business_type_id: str = ""):
    job = plumbers_job()
    job.business_type_id = business_type_id
    manager.jobs[job.id] = job
    manager.save(job)
    return job


def type_id(client, name: str) -> str:
    return next(t["id"] for t in client.get("/api/business-types").json() if t["name"] == name)


def test_business_type_routes(env):
    client, *_ = env
    types = client.get("/api/business-types").json()
    assert {"Roofer", "Plumber"} <= {t["name"] for t in types}
    created = client.post("/api/business-types", json={"name": "Dentist", "search": "dentist", "trade": "dentists"})
    assert created.status_code == 201 and created.json()["steps"][0]["day"] == 0
    tid = created.json()["id"]
    assert client.post("/api/business-types", json={"name": "dentist"}).status_code == 409
    bad = client.patch(f"/api/business-types/{tid}", json={"steps": [{"day": 0, "subject": "hi", "body": "{Nme}"}]})
    assert bad.status_code == 400 and "unknown placeholder {Nme}" in bad.json()["detail"]
    ok = client.patch(f"/api/business-types/{tid}", json={"steps": [{"day": 0, "subject": "hi {Business}",
                                                                     "body": "Smile, {Business}!"}]})
    assert ok.status_code == 200 and len(ok.json()["steps"]) == 1
    preview = client.get(f"/api/business-types/{tid}/preview").json()
    assert preview["samples"][0]["emails"][0]["body"].startswith("Smile, Acme!")
    assert client.post(f"/api/business-types/{tid}/test", json={"step": 1}).json()["to"] == "me@mysender.com"
    assert client.delete(f"/api/business-types/{tid}").status_code == 204
    assert client.get(f"/api/business-types/{tid}").status_code == 404


def test_a_search_remembers_its_business_type(env):
    client, manager, *_ = env
    assert client.post("/api/scrape", json={"business": "plumber", "place": "Houston",
                                            "business_type_id": "bt_nope"}).status_code == 400
    job = seed(manager, type_id(client, "Plumber"))
    summary = next(j for j in client.get("/api/jobs").json() if j["id"] == job.id)
    assert summary["business_type_id"] == job.business_type_id and summary["email_count"] == 7


def test_campaign_routes(env):
    client, manager, outreach, mailbox, _ = env
    job = seed(manager, type_id(client, "Plumber"))
    assert client.post("/api/campaigns", json={}).status_code == 422
    assert client.post("/api/campaigns", json={"job_ids": ["job_missing"]}).status_code == 404
    created = client.post("/api/campaigns", json={"job_ids": [job.id]})
    assert created.status_code == 201
    campaign = created.json()
    assert campaign["name"] == "Plumber · Houston" and campaign["business_type_name"] == "Plumber"
    assert [lead["business"] for lead in campaign["leads"]] == ["Palm Breeze Plumbing", "Hampshire Plumbing",
                                                              "Lia Constructions"]
    cid = campaign["id"]
    listed = client.get("/api/campaigns").json()
    assert listed[0]["counts"]["queued"] == 3 and listed[0]["status"] == "draft"
    bad = client.patch(f"/api/campaigns/{cid}", json={"steps": [{"day": 1, "subject": "x", "body": "y"}]})
    assert bad.status_code == 400 and "day 0" in bad.json()["detail"]
    preview = client.get(f"/api/campaigns/{cid}/preview").json()
    assert preview["to"] == "palmbreeze@gmail.com" and len(preview["emails"]) == 3
    lia = campaign["leads"][2]["id"]
    assert client.patch(f"/api/campaigns/{cid}/leads/{lia}", json={"skipped": True}).json()["status"] == "skipped"
    assert client.post(f"/api/campaigns/{cid}/test", json={"step": 2}).json()["subject"].startswith("[Test] Re: ")
    assert mailbox.sent[-1]["To"] == "me@mysender.com"
    started = client.post(f"/api/campaigns/{cid}/start")
    assert started.status_code == 200 and started.json()["status"] == "running"
    assert client.delete(f"/api/campaigns/{cid}").status_code == 409
    assert client.post(f"/api/campaigns/{cid}/pause").json()["status"] == "paused"
    assert client.delete(f"/api/campaigns/{cid}").status_code == 204
    assert client.get(f"/api/campaigns/{cid}").status_code == 404
    status = client.get("/api/outreach").json()
    assert status["ready"] and status["window"] == "Mon–Fri 08:00–12:00 (America/Chicago)"


def test_start_refused_without_a_complete_setup(env, monkeypatch):
    client, manager, outreach, *_ = env
    job = seed(manager)
    cid = client.post("/api/campaigns", json={"job_ids": [job.id], "trade": "plumbers"}).json()["id"]
    monkeypatch.setattr(outreach.cfg, "SENDER_ADDRESS", "")
    refused = client.post(f"/api/campaigns/{cid}/start")
    assert refused.status_code == 400 and "SENDER_ADDRESS" in refused.json()["detail"]


def test_a_running_search_cant_become_a_campaign(env):
    client, manager, *_ = env
    job = seed(manager)
    job.status = JobStatus.RUNNING
    assert client.post("/api/campaigns", json={"job_ids": [job.id]}).status_code == 409


def test_drive_routes_before_connecting(env):
    client, manager, *_ = env
    job = seed(manager)
    status = client.get("/api/drive").json()
    assert status["configured"] and not status["connected"] and status["folder"] == "My Drive"
    assert client.post(f"/api/jobs/{job.id}/drive").status_code == 400
    assert client.get(f"/api/drive/files?business_type_id={type_id(client, 'Roofer')}").status_code == 400
    assert client.post("/api/drive/connect").status_code == 400             # not opened at 127.0.0.1
    assert client.get("/?state=nobody-asked&code=x").status_code == 200       # just the panel


def test_connect_drive_save_a_search_and_run_a_campaign_from_a_file(env):
    client, manager, outreach, _, google = env
    url = client.post("http://127.0.0.1:8000/api/drive/connect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    back = client.get(f"/?state={state}&code=good-code&scope=x", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].startswith("/?drive=connected")
    assert client.get("/api/drive").json()["email"] == "pat@gmail.com"

    roofer = type_id(client, "Roofer")
    job = seed(manager, type_id(client, "Plumber"))
    saved = client.post(f"/api/jobs/{job.id}/drive").json()
    assert saved["path"].startswith("Plumber/plumber_in_houston_")
    assert manager.get(job.id).drive_path == saved["path"]

    folder = next(f for f in google.files.values() if f["name"] == "Plumber")
    roofer_folder = google.add("Roofer", "root", "application/vnd.google-apps.folder")
    lead_file = google.add("my roofers.csv", roofer_folder,
                           content=b"Name,Email,City\nAcme Roofing LLC,acme@gmail.com,Katy\n")
    files = client.get(f"/api/drive/files?business_type_id={roofer}").json()
    assert [f["name"] for f in files["files"]] == ["my roofers.csv"]
    created = client.post("/api/campaigns", json={"drive_file_ids": [lead_file], "business_type_id": roofer}).json()
    assert created["name"] == "Roofer · my roofers" and created["drive_files"][0]["name"] == "my roofers.csv"
    assert [(lead["business"], lead["city"]) for lead in created["leads"]] == [("Acme Roofing", "Katy")]
    client.post(f"/api/campaigns/{created['id']}/start")
    results = client.post(f"/api/campaigns/{created['id']}/drive").json()
    assert results["path"].startswith("Roofer/Outreach/roofer_my_roofers_")
    uploaded = next(f for f in google.files.values() if f["name"] == results["path"].rsplit("/", 1)[1])
    assert b"Acme Roofing,acme@gmail.com,queued,0 of 3" in google.content[uploaded["id"]]
    assert folder["parents"] == ["root"]
