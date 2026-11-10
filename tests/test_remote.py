"""Searching on one computer, sending from another (ROLE=searcher / ROLE=sender) - docs/CLOUD.md."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.jobs as jobs_module
import app.main as main_module
from app.config import Settings
from app.jobs import JobManager
from app.models import Job, JobStatus, PlaceRow, Progress
from app.outreach import autopilot as autopilot_module
from app.remote import RemoteSearcher

TOKEN = "test-token-0123456789"
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def sender_manager(tmp_path, **overrides) -> JobManager:
    cfg = Settings(_env_file=None, DATA_DIR=tmp_path / "cloud", ROLE="sender", SEARCHER_TOKEN=TOKEN, **overrides)
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobManager(cfg)


def finished_copy(job: Job, rows: int = 2, status: JobStatus = JobStatus.COMPLETED) -> Job:
    """What the search computer sends back for the sender's ``job``."""
    result = job.model_copy(deep=True)
    result.status = status
    result.rows = [PlaceRow(rank=i, name=f"Roofer {i}", email=f"r{i}@example.com") for i in range(1, rows + 1)]
    result.stats = {"matched": rows}
    result.finished_at = NOW
    return result


# --------------------------------------------------------------------------
# The sender's job manager
# --------------------------------------------------------------------------
def test_a_senders_search_waits_for_the_search_computer(tmp_path):
    async def scenario():
        manager = sender_manager(tmp_path)
        finished: list[Job] = []

        async def on_finished(job):
            finished.append(job)

        manager.on_finished = on_finished
        job = manager.create_scrape("roofing in Katy, TX", 60, place="Katy, TX", whole_area=True)
        assert job.remote and job.status == JobStatus.QUEUED and not manager._tasks   # no browser here

        claimed = manager.claim_remote(NOW)
        assert claimed is job and job.status == JobStatus.RUNNING and job.remote_seen_at == NOW
        assert manager.claim_remote(NOW) is None                                    # handed out once

        assert manager.remote_progress(job.id, Progress(phase="details", message="Place 3 of 9"), 3, NOW)
        assert job.progress.message == "Place 3 of 9" and job.stats["rows_so_far"] == 3

        manager.remote_result(finished_copy(job))
        await asyncio.sleep(0)                                                      # the hook runs as a task
        assert job.status == JobStatus.COMPLETED and len(job.rows) == 2 and finished == [job]
        with pytest.raises(ValueError):                                             # sent twice
            manager.remote_result(finished_copy(job))
        assert not manager.remote_progress(job.id, Progress(), 2, NOW)              # finished: stop

    asyncio.run(scenario())


def test_a_restart_of_the_sender_keeps_the_search_computers_searches(tmp_path):
    async def scenario():
        manager = sender_manager(tmp_path)
        waiting = manager.create_scrape("roofing in Katy, TX", 60)
        running = manager.create_scrape("roofing in Spring, TX", 60)
        running.created_at -= timedelta(minutes=1)
        manager.claim_remote(NOW)
        reloaded = JobManager(manager.cfg)
        assert reloaded.get(waiting.id).status == JobStatus.QUEUED          # not "interrupted"
        assert reloaded.get(running.id).status == JobStatus.RUNNING

    asyncio.run(scenario())


def test_a_search_computer_that_stops_reporting_is_given_up_on(tmp_path):
    async def scenario():
        manager = sender_manager(tmp_path)
        finished: list[Job] = []

        async def on_finished(job):
            finished.append(job)

        manager.on_finished = on_finished
        job = manager.create_scrape("roofing in Katy, TX", 60)
        manager.claim_remote(NOW)
        assert manager.expire_remote(NOW + timedelta(minutes=19), 20 * 60) == []
        assert manager.expire_remote(NOW + timedelta(minutes=21), 20 * 60) == [job]
        await asyncio.sleep(0)
        assert job.status == JobStatus.INTERRUPTED and finished == [job]
        assert not manager.remote_progress(job.id, Progress(), 0, NOW)      # the late search computer stops
        with pytest.raises(ValueError):
            manager.remote_result(finished_copy(job))

    asyncio.run(scenario())


def test_cancelling_a_search_computers_search(tmp_path):
    async def scenario():
        manager = sender_manager(tmp_path)
        job = manager.create_scrape("roofing in Katy, TX", 60)
        manager.claim_remote(NOW)
        assert manager.cancel(job.id) and job.status == JobStatus.CANCELLED
        assert not manager.remote_progress(job.id, Progress(), 0, NOW)

    asyncio.run(scenario())


async def test_the_autopilot_searches_a_place_again_when_the_search_computer_went_quiet(tmp_path):
    """The given-up search counts as a failed one: the place is first in line again, before the next."""
    import random

    from app.outreach.activity import ActivityLog
    from app.outreach.autopilot import Autopilot, AutopilotUpdate
    from app.outreach.manager import OutreachManager
    from tests.test_outreach import TUESDAY_9AM, Clock, FakeMailbox, make_settings

    clock = Clock(TUESDAY_9AM)
    cfg = make_settings(tmp_path, ROLE="sender", SEARCHER_TOKEN=TOKEN)
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    activity = ActivityLog(tmp_path / "activity.jsonl", clock=clock)
    outreach = OutreachManager(cfg, mailbox=FakeMailbox(), clock=clock, rng=random.Random(7), activity=activity)
    jobs = JobManager(cfg)
    pilot = Autopilot(cfg, jobs, outreach, activity, clock=clock)
    jobs.on_finished = pilot.job_finished
    plumber = next(t for t in outreach.list_business_types() if t.name == "Plumber")
    pilot.update(AutopilotUpdate(business_type_id=plumber.id, places=["Katy, TX", "Pearland, TX"], enabled=True))

    await pilot.tick()
    job = jobs.get(pilot.state.runs[-1].job_id)
    assert job.remote and job.place == "Katy, TX" and pilot.state.next_place == 1
    jobs.claim_remote(clock())
    clock.advance(seconds=autopilot_module.REMOTE_SILENCE_S + 60)
    await pilot.tick()
    assert pilot.waiting_for == "the search computer stopped reporting"
    await asyncio.sleep(0.01)                          # job_finished runs
    run = pilot.state.runs[-1]
    assert run.failed and "searched again" in run.note and pilot.state.next_place == 0
    await pilot.tick()                                 # the same place again, not the next one
    assert jobs.get(pilot.state.runs[-1].job_id).place == "Katy, TX" and len(pilot.state.runs) == 2


# --------------------------------------------------------------------------
# The sender's routes
# --------------------------------------------------------------------------
@pytest.fixture
def cloud(tmp_path, monkeypatch):
    manager = sender_manager(tmp_path)
    monkeypatch.setattr(main_module, "manager", manager)
    monkeypatch.setattr(main_module, "settings", manager.cfg)
    with TestClient(main_module.app) as client:
        yield client, manager


def test_the_remote_routes_need_the_token(cloud):
    client, manager = cloud
    assert client.post("/api/remote/next").status_code == 401
    assert client.post("/api/remote/next", headers={"Authorization": "Bearer wrong"}).status_code == 401
    manager.cfg.ROLE = "all"
    response = client.post("/api/remote/next", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 404                  # only a sender hands out searches


def test_the_search_computer_takes_a_search_and_sends_it_back(cloud):
    client, manager = cloud
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/remote/next", headers=auth, json={"version": "1.19.0"}).status_code == 204
    job = manager.create_scrape("roofing in Katy, TX", 60, place="Katy, TX", business_type_id="bt_1")

    spec = client.post("/api/remote/next", headers=auth, json={}).json()["job"]
    assert spec["id"] == job.id and spec["place"] == "Katy, TX" and spec["business_type_id"] == "bt_1"
    status = client.get("/api/remote").json()
    assert status["role"] == "sender" and status["searching"] == [job.query] and status["last_contact"]

    progress = {"progress": {"phase": "details", "message": "Place 1 of 4"}, "rows": 1}
    assert client.post(f"/api/remote/jobs/{job.id}/progress", headers=auth, json=progress).json() == {"stop": False}

    result = finished_copy(job).model_dump(mode="json")
    response = client.post(f"/api/remote/jobs/{job.id}/result", headers=auth, json=result)
    assert response.status_code == 200 and response.json()["row_count"] == 2
    assert client.post(f"/api/remote/jobs/{job.id}/result", headers=auth, json=result).status_code == 409
    assert client.post("/api/remote/jobs/job_other/result", headers=auth, json=result).status_code == 400


def test_settings_work_at_a_trusted_name_only(cloud):
    _, manager = cloud
    # the tunnel (cloudflared) runs on the server: its requests come from 127.0.0.1
    client = TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    behind_login = {"host": "panel.example.com", "origin": "https://panel.example.com"}
    assert client.get("/api/settings", headers=behind_login).status_code == 403
    manager.cfg.TRUSTED_HOSTS = "panel.example.com"
    assert client.get("/api/settings", headers=behind_login).status_code == 200
    other_site = {"host": "panel.example.com", "origin": "https://evil.example"}
    assert client.get("/api/settings", headers=other_site).status_code == 403
    assert client.get("/api/settings", headers={"host": "other.example.com"}).status_code == 403


# --------------------------------------------------------------------------
# The search computer
# --------------------------------------------------------------------------
class _QuickScraper:
    """A search that finds two businesses at once (no browser)."""

    def __init__(self, cfg, *, headless=None, on_progress=None, on_row=None):
        from app.scraper.maps import ScrapeStats
        self.stats, self.on_row = ScrapeStats(), on_row

    async def scrape(self, searches, limit, *, selector=None):
        for rank in (1, 2):
            await self.on_row(PlaceRow(rank=rank, name=f"Roofer {rank}", email=f"r{rank}@example.com"))
            self.stats.matched += 1


def test_the_search_computer_runs_the_senders_search_and_sends_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "MapsScraper", _QuickScraper)

    async def scenario():
        cloud_jobs = sender_manager(tmp_path)
        monkeypatch.setattr(main_module, "manager", cloud_jobs)
        monkeypatch.setattr(main_module, "settings", cloud_jobs.cfg)
        finished: list[Job] = []

        async def on_finished(job):
            finished.append(job)

        cloud_jobs.on_finished = on_finished
        wanted = cloud_jobs.create_scrape("roofing in Katy, TX", 2, place="Katy, TX")

        cfg = Settings(_env_file=None, DATA_DIR=tmp_path / "laptop", ROLE="searcher", SEARCHER_TOKEN=TOKEN,
                       CLOUD_URL="https://panel.example.com")
        cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
        laptop = RemoteSearcher(cfg, JobManager(cfg), transport=httpx.ASGITransport(app=main_module.app))

        assert await laptop.step() == 30                       # took the search
        assert laptop.pending == [wanted.id] and wanted.status == JobStatus.RUNNING
        for _ in range(20):                                    # the quick search finishes
            await asyncio.sleep(0.01)
            if laptop.jobs.get(wanted.id).status == JobStatus.COMPLETED:
                break
        assert await laptop.step() == 60                       # sent it back, then: nothing waiting
        await asyncio.sleep(0)
        assert laptop.pending == [] and wanted.status == JobStatus.COMPLETED and len(wanted.rows) == 2
        assert finished == [wanted] and laptop.state == "waiting for a search"
        assert RemoteSearcher(cfg, laptop.jobs).pending == []   # remembered across a restart

    asyncio.run(scenario())


def test_the_search_computer_says_what_is_wrong(tmp_path):
    cfg = Settings(_env_file=None, DATA_DIR=tmp_path, ROLE="searcher")
    searcher = RemoteSearcher(cfg, JobManager(cfg))
    asyncio.run(searcher.step())
    assert "CLOUD_URL" in searcher.state

    cfg.CLOUD_URL, cfg.SEARCHER_TOKEN = "https://panel.example.com", "wrong"

    def login_page(request):
        return httpx.Response(302, headers={"location": "https://example.cloudflareaccess.com/"})

    searcher = RemoteSearcher(cfg, JobManager(cfg), transport=httpx.MockTransport(login_page))
    assert asyncio.run(searcher.step()) == 120
    assert "CLOUD_ACCESS_ID" in searcher.state

    searcher = RemoteSearcher(cfg, JobManager(cfg), transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    asyncio.run(searcher.step())
    assert "SEARCHER_TOKEN" in searcher.state
