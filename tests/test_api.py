"""API tests - use a fresh JobManager on a temp directory, never touch the network."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.jobs import JobManager
from app.models import Job, JobStatus, PlaceRow
from app.scraper.maps import Search


class _FakeEnricher:
    """Stands in for WebsiteEnricher: the Facebook page of 'Facebook Roofing' lists an email."""

    lookups: list[str] = []

    def __init__(self, cfg=None, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def find_contacts(self, website, *, region=None):
        from app.enrichment.extractors import EmailHit
        from app.enrichment.website import ContactResult

        self.lookups.append(website)
        emails = [EmailHit(email="hello@fbroofing.com", source=website)] if "fbroofing" in website else []
        return ContactResult(website=website, emails=emails, pages_checked=[website])


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(jobs_module, "WebsiteEnricher", _FakeEnricher)  # never touch the network
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
    assert c.post("/api/scrape", json={"place": "Houston, TX"}).status_code == 422          # what to look for?
    assert c.post("/api/scrape", json={"business": "  ", "place": "Houston"}).status_code == 422
    assert c.post("/api/scrape", json={"business": "roofers", "filters": {"website": "sometimes"}}).status_code == 422
    assert c.post("/api/scrape", json={"business": "roofers", "whole_area": True}).status_code == 422  # where?


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
    """Stands in for MapsScraper so the API test never opens a browser.

    Serves LISTINGS through the job's selector the way the real scraper does
    (prefilter -> accept -> stats) until `limit` places are kept.
    """

    LISTINGS = [
        dict(name="Fake Roofer", website="https://fake.example", phone="(713) 555-0100"),
        dict(name="No Site Roofing", phone="(713) 555-0101"),
        dict(name="Facebook Roofing", website="https://www.facebook.com/fbroofing"),
        dict(name="Silent Roofing"),                        # no website, no phone, no email
    ]
    queries: list[str] = []

    def __init__(self, cfg, *, headless=None, on_progress=None, on_row=None):
        self.on_progress, self.on_row = on_progress, on_row
        from app.scraper.maps import ScrapeStats
        self.stats = ScrapeStats()

    async def scrape(self, query, limit, *, selector=None):
        from app.scraper.maps import PlaceRef

        self.queries.append(query)
        await self.on_progress("collecting", "Found 1 of 1 places", 1, 1)
        kept_rows = []
        for rank, item in enumerate(self.LISTINGS, start=1):
            if self.stats.matched >= limit:
                break
            self.stats.links_found += 1
            row = PlaceRow(rank=rank, city="Houston", country="US", **item)
            reason = selector.prefilter(PlaceRef(rank=rank, href="", website_hint=row.website)) \
                if selector and selector.active else ""
            if not reason:
                self.stats.scraped += 1
                kept, reason = await selector.accept(row) if selector else ([row], "")
            if reason:
                self.stats.skipped[reason] = self.stats.skipped.get(reason, 0) + 1
                continue
            self.stats.matched += 1
            for r in kept:
                await self.on_row(r)
            kept_rows += kept
        self.stats.stopped = "limit" if self.stats.matched >= limit else "end_of_list"
        self.stats.reached_end = self.stats.stopped == "end_of_list"
        await self.on_progress("done", "Scraped", self.stats.matched, limit)
        return kept_rows


def _wait_for(c, job_id: str) -> dict:
    import time

    for _ in range(50):
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running", "enriching"):
            return job
        time.sleep(0.1)
    return job


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


def test_business_and_place_make_the_search(client, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    res = c.post("/api/scrape", json={"business": " roofers ", "place": "Houston, TX", "limit": 1})
    assert res.status_code == 202, res.text
    summary = res.json()
    assert (summary["query"], summary["business"], summary["place"]) == ("roofers in Houston, TX", "roofers", "Houston, TX")
    assert summary["filters"] == {"website": "any", "contact": "any", "profiles_as_no_website": True,
                                  "label": "all businesses"}
    job = _wait_for(c, summary["id"])
    assert _FakeScraper.queries[-1] == [Search("roofers in Houston, TX")]
    assert job["status"] == "completed" and job["notice"] == "" and job["progress"]["message"] == "Done - 1 rows"


def test_filtered_job_keeps_matches_and_explains_a_short_result(client, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    job_id = c.post("/api/scrape", json={
        "business": "roofers", "place": "Houston, TX", "limit": 200,
        "filters": {"website": "without", "contact": "phone_or_email"}}).json()["id"]
    job = _wait_for(c, job_id)
    assert job["status"] == "completed", job
    # a Facebook page is not a website; with no phone listed, its email (found on that page) makes it a match;
    # the silent place has no way to reach it
    assert [r["name"] for r in job["rows"]] == ["No Site Roofing", "Facebook Roofing"]
    facebook = job["rows"][1]
    assert (facebook["email"], facebook["website_type"], facebook["enrichment_status"]) == \
        ("hello@fbroofing.com", "profile", "done")
    assert _FakeEnricher.lookups[-1:] == ["https://www.facebook.com/fbroofing"], "one lookup, only where it decides"
    assert job["stats"]["skipped"] == {"has a website": 1, "no phone or email": 1}
    assert job["notice"] == ("Only 2 of the 4 businesses Google Maps lists for “roofers” in Houston, TX match your "
                             "filters (no website · phone or email) — that's all of them (you asked for 200).")
    assert job["progress"]["message"] == "Done - 2 of 200 found"
    summary = next(j for j in c.get("/api/jobs").json() if j["id"] == job_id)
    assert summary["found"] == 2 and summary["notice"] == job["notice"]
    res = c.get(f"/api/jobs/{job_id}/export.csv?extended=true")
    assert 'filename="roofers_in_houston_tx_no_website_phone_or_email_extended.csv"' in res.headers["content-disposition"]
    assert ",Website Type," in res.content.decode("utf-8-sig").splitlines()[0]


def test_short_unfiltered_result_says_that_is_everything(client, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    job_id = c.post("/api/scrape", json={"business": "roofers", "place": "Houston, TX", "limit": 200}).json()["id"]
    job = _wait_for(c, job_id)
    assert len(job["rows"]) == 4
    assert job["notice"] == ("Only 4 businesses found for “roofers” in Houston, TX — that's every listing "
                             "Google Maps has for this search (you asked for 200).")


def test_jobs_saved_before_filters_existed_still_load(client):
    import json

    _, manager = client
    old = {"id": "job_0123456789ab", "query": "roofers near houston", "limit": 2, "status": "completed",
           "rows": [{"row_id": "r_1", "rank": 1, "name": "Old Roofer", "website": "https://old.example"}],
           "stats": {"links_found": 2, "scraped": 2, "failed": 0, "reached_end": False, "errors": []}}
    (settings.jobs_dir / "job_0123456789ab.json").write_text(json.dumps(old), encoding="utf-8")
    job = JobManager(settings).jobs["job_0123456789ab"]
    assert job.business == "" and not job.filters.active and job.notice == ""
    assert job.rows[0].website_type == "own"
    assert job.summary().found is None


def test_whole_area_and_several_places_plan_one_search_per_place(client, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MapsScraper", _FakeScraper)
    c, _ = client
    body = {"business": "roofers", "place": "Katy, TX; Pasadena, TX", "limit": 5}
    summary = c.post("/api/scrape", json={**body, "whole_area": True}).json()
    assert summary["whole_area"] is True and summary["place"] == "Katy, TX; Pasadena, TX"
    _wait_for(c, summary["id"])
    assert _FakeScraper.queries[-1] == [Search("roofers", area="Katy, TX"), Search("roofers", area="Pasadena, TX")]
    _wait_for(c, c.post("/api/scrape", json=body).json()["id"])
    assert _FakeScraper.queries[-1] == [Search("roofers in Katy, TX"), Search("roofers in Pasadena, TX")]
