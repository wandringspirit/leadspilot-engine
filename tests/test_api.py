"""API tests - use a fresh JobManager on a temp directory, never touch the network."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.jobs import JobManager
from app.models import Job, JobStatus, PlaceRow


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(settings)
    monkeypatch.setattr(main_module, "manager", manager)
    with TestClient(main_module.app) as c:
        yield c, manager


def _seed_job(manager: JobManager, rows: int = 2) -> Job:
    job = Job(query="roofing companies near houston", limit=rows, status=JobStatus.COMPLETED)
    for i in range(1, rows + 1):
        job.rows.append(PlaceRow(rank=i, name=f"Roofer {i}", city="Houston", state="Texas", country="US",
                                 website=f"https://roofer{i}.com", phone="(713) 555-0100",
                                 phone_unformatted="+17135550100", review_count=10 * i))
    manager.jobs[job.id] = job
    manager.save(job)
    return job


def test_health_and_config(client):
    c, _ = client
    assert c.get("/api/health").json()["status"] == "ok"
    cfg = c.get("/api/config").json()
    assert cfg["columns"][0] == "Name" and cfg["max_results"] == settings.MAX_RESULTS


def test_index_serves_panel(client):
    c, _ = client
    res = c.get("/")
    assert res.status_code == 200 and "Google Maps Lead Crawler" in res.text


def test_scrape_validation(client):
    c, _ = client
    assert c.post("/api/scrape", json={"query": "x", "limit": 5}).status_code == 422
    assert c.post("/api/scrape", json={"query": "roofers", "limit": 0}).status_code == 422
    assert c.post("/api/scrape", json={"query": "roofers", "limit": 100000}).status_code == 422


def test_list_get_delete_job(client):
    c, manager = client
    job = _seed_job(manager)
    listed = c.get("/api/jobs").json()
    assert [j["id"] for j in listed] == [job.id]
    assert listed[0]["row_count"] == 2
    full = c.get(f"/api/jobs/{job.id}").json()
    assert full["rows"][0]["name"] == "Roofer 1"
    assert c.delete(f"/api/jobs/{job.id}").status_code == 204
    assert c.get(f"/api/jobs/{job.id}").status_code == 404
    assert c.delete(f"/api/jobs/{job.id}").status_code == 404


def test_export_csv(client):
    c, manager = client
    job = _seed_job(manager)
    res = c.get(f"/api/jobs/{job.id}/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert 'filename="roofing_companies_near_houston.csv"' in res.headers["content-disposition"]
    text = res.content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "Name,City,State,Country,Email,Website,Phone,Phone Unformatted,Url,Rank,Review Count,Description"
    assert lines[1].startswith("Roofer 1,Houston,Texas,US,,https://roofer1.com,(713) 555-0100,+17135550100,")
    ext = c.get(f"/api/jobs/{job.id}/export.csv?extended=true")
    assert ext.content.decode("utf-8-sig").splitlines()[0].endswith(",Enrichment Status")


def test_cancel_and_enrich_guards(client):
    c, manager = client
    job = _seed_job(manager)
    assert c.post(f"/api/jobs/{job.id}/cancel").status_code == 409  # nothing running
    empty = Job(query="empty", limit=1, status=JobStatus.COMPLETED)
    manager.jobs[empty.id] = empty
    assert c.post(f"/api/jobs/{empty.id}/enrich", json={}).status_code == 400
    running = Job(query="busy", limit=1, status=JobStatus.RUNNING)
    running.rows.append(PlaceRow(rank=1, name="x"))
    manager.jobs[running.id] = running
    assert c.post(f"/api/jobs/{running.id}/enrich", json={}).status_code == 409
    assert c.post("/api/jobs/nope/enrich", json={}).status_code == 404


def test_jobs_are_persisted_and_reloaded(client):
    c, manager = client
    job = _seed_job(manager)
    reloaded = JobManager(settings)
    assert job.id in reloaded.jobs
    assert reloaded.jobs[job.id].rows[1].name == "Roofer 2"


def test_interrupted_jobs_are_flagged_on_reload(client):
    c, manager = client
    job = Job(query="x", limit=1, status=JobStatus.RUNNING)
    manager.jobs[job.id] = job
    manager.save(job)
    reloaded = JobManager(settings)
    assert reloaded.jobs[job.id].status == JobStatus.INTERRUPTED


class _FakeScraper:
    """Stands in for MapsScraper so the API test never opens a browser."""

    def __init__(self, cfg, *, headless=None, on_progress=None, on_row=None):
        self.on_progress, self.on_row = on_progress, on_row
        from app.scraper.maps import ScrapeStats
        self.stats = ScrapeStats()

    async def scrape(self, query, limit):
        await self.on_progress("collecting", "Found 1 of 1 places", 1, 1)
        row = PlaceRow(rank=1, name="Fake Roofer", website="https://fake.example", city="Houston", country="US")
        await self.on_row(row)
        await self.on_progress("done", "Scraped 1 places", 1, 1)
        self.stats.scraped = 1
        return [row]


def test_scrape_endpoint_runs_job_in_background(client, monkeypatch):
    import time

    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    res = c.post("/api/scrape", json={"query": "roofers in houston", "limit": 1})
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    for _ in range(50):
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job
    assert job["rows"][0]["name"] == "Fake Roofer"
    assert job["progress"]["percent"] == 100.0
    assert job["stats"]["scraped"] == 1


def test_logs_endpoint_streams_new_lines(client):
    import logging

    c, _ = client
    log = logging.getLogger("app.test_logs")
    log.info("hello from the api test")
    data = c.get("/api/logs").json()
    assert any(e["message"] == "hello from the api test" for e in data["entries"])
    assert set(data["entries"][0]) == {"seq", "ts", "level", "logger", "message", "job_id"}
    cursor = data["next"]
    assert c.get(f"/api/logs?since={cursor}").json()["entries"] == []
    log.warning("second line")
    newer = c.get(f"/api/logs?since={cursor}&level=WARNING").json()
    assert [e["message"] for e in newer["entries"]] == ["second line"]
    assert newer["next"] >= cursor + 1
    assert c.get("/api/logs?level=LOUD").status_code == 400
    assert c.get("/api/logs?limit=0").status_code == 422


def test_logs_are_tagged_with_the_job(client, monkeypatch):
    import time

    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    job_id = c.post("/api/scrape", json={"query": "roofers in dallas", "limit": 1}).json()["id"]
    for _ in range(50):
        if c.get(f"/api/jobs/{job_id}").json()["status"] == "completed":
            break
        time.sleep(0.1)
    tagged = c.get(f"/api/logs?job_id={job_id}").json()["entries"]
    assert tagged, "job lines should be tagged with the job id"
    assert all(e["job_id"] == job_id for e in tagged)
    assert any("completed" in e["message"] for e in tagged)
