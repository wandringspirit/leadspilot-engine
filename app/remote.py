"""The search computer (ROLE=searcher): runs the cloud server's searches - docs/CLOUD.md.

Google Maps blocks searches from data-centre addresses, so the cloud server (ROLE=sender) doesn't search
itself: every search it starts - the autopilot's and the ones started in its panel - waits there until this
computer asks for it. This loop:

1. asks the sender for a search (``POST /api/remote/next``) - every ``IDLE_S`` while it has none;
2. runs it here, under the sender's job id, like any search (it shows in this panel too);
3. reports its progress every ``BUSY_S`` (``.../progress``) - the sender shows it, and answers ``stop`` when
   the search was cancelled there or given up on (this computer slept for ``REMOTE_SILENCE_S``);
4. sends the finished search back (``.../result``): the sender makes the campaign from it.

Every round it also tells the sender which Google Drive sign-in it has (``POST /api/remote/drive``, an id,
not the sign-in): the sender can't connect Drive itself (Google only sends a sign-in back to 127.0.0.1), so
a sign-in made here is handed to it, and one Google ended there is forgotten here too - this panel then
asks to connect again.

It also watches the cloud server: when it can't be reached for ``DOWN_ALERT_S`` it emails you (``email_me``,
OutreachManager.email_me - ALERT_TO), once, and again when it's back. The cloud server watches this computer
the other way round (app/outreach/alerts.py: *Laptop not searching*).

The searches it still has to report are kept in ``data/remote.json``, so a result that couldn't be sent
(no internet) is sent later, and a search the panel's restart interrupted is reported as interrupted - the
sender searches that place again.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from app import __version__
from app.config import Settings
from app.drive.client import DriveClient
from app.filters import SearchFilters
from app.jobs import ACTIVE_STATES, JobManager

log = logging.getLogger(__name__)

IDLE_S = 60             # how often it asks for a search while it has none
BUSY_S = 30             # how often it reports a running search
ERROR_S = 120           # after the sender couldn't be reached
DOWN_ALERT_S = 30 * 60  # it couldn't be reached this long: you get an email


class RemoteError(Exception):
    pass


class RemoteSearcher:
    def __init__(self, cfg: Settings, jobs: JobManager, *, drive: DriveClient | None = None,
                 email_me: Callable[[str, str], Awaitable[bool]] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.jobs = jobs
        self.email_me = email_me                   # None: no emails about the cloud server being down
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.down_since: datetime | None = None    # when the cloud server stopped answering
        self._down_emailed = False
        self.drive = drive                         # this computer's Google Drive sign-in (None: not handed over)
        self._transport = transport
        self._task: asyncio.Task | None = None
        self.pending: list[str] = self._load()     # the sender's searches this computer still has to report
        self.state = "not started"                 # what it's doing (the panel, the logs)
        self.last_contact: datetime | None = None
        self._reported_error = ""

    # ------------------------------------------------------------------
    # What it still has to report
    # ------------------------------------------------------------------
    @property
    def _file(self) -> Path:
        return self.cfg.DATA_DIR / "remote.json"

    def _load(self) -> list[str]:
        try:
            return list(json.loads(self._file.read_text(encoding="utf-8")).get("pending", []))
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("unreadable %s (%s) - starting afresh", self._file, exc)
            return []

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        partial = self._file.with_suffix(".json.part")
        partial.write_text(json.dumps({"pending": self.pending}), encoding="utf-8")
        partial.replace(self._file)

    def _drop(self, job_id: str) -> None:
        if job_id in self.pending:
            self.pending.remove(job_id)
            self._save()

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    def problem(self) -> str:
        if not self.cfg.CLOUD_URL.strip():
            return "CLOUD_URL is empty - the cloud panel's address"
        if not self.cfg.CLOUD_URL.strip().startswith(("https://", "http://")):
            return "CLOUD_URL must start with https://"
        if not self.cfg.SEARCHER_TOKEN:
            return "SEARCHER_TOKEN is empty - the same one as on the cloud server"
        return ""

    def status(self) -> dict:
        return {"role": "searcher", "cloud_url": self.cfg.CLOUD_URL, "state": self.state,
                "last_contact": self.last_contact.isoformat() if self.last_contact else None,
                "pending": list(self.pending)}

    def start_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="remote-searcher")

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        log.info("Search computer for %s: asking it for searches every %ds", self.cfg.CLOUD_URL or "?", IDLE_S)
        while True:
            try:
                wait = await self.step()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Search computer: unexpected error")
                wait = ERROR_S
            await asyncio.sleep(wait)

    async def step(self) -> float:
        """One round; returns how long to wait before the next."""
        problem = self.problem()
        if problem:
            self.state = problem
            return IDLE_S
        try:
            async with self._client() as client:
                wait = await self._step(client)
        except RemoteError as exc:
            self.state = f"the cloud server: {exc}"
            if str(exc) != self._reported_error:          # once, not every two minutes
                log.warning("Search computer: %s", exc)
                self._reported_error = str(exc)
            await self._down(str(exc))
            return ERROR_S
        self._reported_error = ""
        self.last_contact = self.clock()
        await self._up()
        return wait

    async def _down(self, problem: str) -> None:
        now = self.clock()
        self.down_since = self.down_since or now
        if self._down_emailed or self.email_me is None or (now - self.down_since).total_seconds() < DOWN_ALERT_S:
            return
        since = self.down_since.astimezone().strftime("%a %d %b, %H:%M")
        body = (f"The laptop hasn't reached the cloud server ({self.cfg.CLOUD_URL}) since {since}: {problem}.\n\n"
                "While it's down nothing is emailed - campaigns, follow-ups and reply checks wait - and the master "
                "sheet isn't updated. Searches wait too.\n\n"
                "Check: is the server running? `ssh leadspilot-cloud`, then `systemctl status leadspilot cloudflared` "
                "(docs/CLOUD.md, docs/TROUBLESHOOTING.md › Cloud server). If the laptop's own internet is down, "
                "this email only goes out once it's back.\n\nYou get another email when it answers again.")
        if await self.email_me("Cloud server not reachable", body):
            self._down_emailed = True

    async def _up(self) -> None:
        if self.down_since is None:
            return
        since, self.down_since = self.down_since, None
        if self._down_emailed and self.email_me is not None:
            self._down_emailed = False
            await self.email_me("Cloud server reachable again",
                                f"The laptop reaches the cloud server again (it couldn't since "
                                f"{since.astimezone().strftime('%a %d %b, %H:%M')}). Sending and searches carry on.")

    async def _sync_drive(self, client: httpx.AsyncClient) -> None:
        """Hand the cloud server this computer's Google Drive sign-in when it hasn't got it; forget ours when
        the cloud server found that Google ended it."""
        if self.drive is None:
            return
        mine = self.drive.sign_in_id
        answer = await self._post(client, "/api/remote/drive", {"id": mine}, accept=(404,))
        if "_status" in answer:                            # an older cloud panel: no hand-over
            return
        if mine and answer.get("lost") == mine:
            self.drive.lost_elsewhere()
        elif mine and answer.get("id") != mine and self.drive.token is not None:
            token = self.drive.token
            await self._post(client, "/api/remote/drive",
                             {"id": mine, "refresh_token": token.refresh_token, "email": token.email})
            log.info("Search computer: Google Drive sign-in (%s) handed to the cloud server",
                     token.email or "account without an email")

    async def _step(self, client: httpx.AsyncClient) -> float:
        await self._sync_drive(client)
        busy = False
        for job_id in list(self.pending):
            job = self.jobs.get(job_id)
            if job is None:                                # deleted here: the sender gives up on it by itself
                self._drop(job_id)
                continue
            if job.status in ACTIVE_STATES:
                answer = await self._post(client, f"/api/remote/jobs/{job_id}/progress",
                                          {"progress": job.progress.model_dump(mode="json"), "rows": len(job.rows)})
                if answer.get("stop"):
                    log.info("Search computer: the cloud server stopped %r - cancelling it here", job.query)
                    self.jobs.cancel(job_id)
                    self._drop(job_id)
                else:
                    busy = True
                    self.state = f"searching {job.query}"
                continue
            answer = await self._post(client, f"/api/remote/jobs/{job_id}/result", job.model_dump(mode="json"),
                                      accept=(409,))
            if answer.get("_status") == 409:
                log.warning("Search computer: the cloud server didn't take %r: %s", job.query, answer.get("detail"))
            else:
                log.info("Search computer: %r (%s, %d row(s)) sent to the cloud server", job.query,
                         job.status.value, len(job.rows))
            self._drop(job_id)
        if busy:
            return BUSY_S
        answer = await self._post(client, "/api/remote/next", {"version": __version__})
        spec = answer.get("job")
        if not spec:
            self.state = "waiting for a search"
            return IDLE_S
        job = self.jobs.create_scrape(spec["query"], spec["limit"], business=spec.get("business", ""),
                                      place=spec.get("place", ""), whole_area=spec.get("whole_area", False),
                                      filters=SearchFilters.model_validate(spec.get("filters") or {}),
                                      enrich=spec.get("enrich", False),
                                      business_type_id=spec.get("business_type_id", ""), job_id=spec["id"])
        self.pending.append(job.id)
        self._save()
        self.state = f"searching {job.query}"
        log.info("Search computer: searching %r for the cloud server", job.query)
        return BUSY_S

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.cfg.SEARCHER_TOKEN}"}
        if self.cfg.CLOUD_ACCESS_ID and self.cfg.CLOUD_ACCESS_SECRET:
            headers |= {"CF-Access-Client-Id": self.cfg.CLOUD_ACCESS_ID,
                        "CF-Access-Client-Secret": self.cfg.CLOUD_ACCESS_SECRET}
        return httpx.AsyncClient(base_url=self.cfg.CLOUD_URL.strip().rstrip("/"), headers=headers, timeout=60,
                                 transport=self._transport, follow_redirects=False)

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict, *,
                    accept: tuple[int, ...] = ()) -> dict:
        try:
            response = await client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise RemoteError(f"can't be reached ({exc.__class__.__name__}: {exc})") from exc
        if response.status_code in accept:
            detail = _detail(response)
            return {"_status": response.status_code, "detail": detail}
        if response.status_code in (301, 302, 303, 307, 308):     # Cloudflare Access sends us to its login page
            raise RemoteError("it asks for a login - set CLOUD_ACCESS_ID and CLOUD_ACCESS_SECRET (docs/CLOUD.md)")
        if response.status_code == 401:
            raise RemoteError("it refused the SEARCHER_TOKEN - it must be the same on both computers")
        if response.status_code == 404:
            raise RemoteError("it doesn't hand out searches - is its ROLE sender, and its SEARCHER_TOKEN set?")
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            raise RemoteError(f"it answered {response.status_code}: {_detail(response)}")
        try:
            return response.json()
        except ValueError as exc:
            raise RemoteError("it didn't answer with JSON - is CLOUD_URL the panel's address?") from exc


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", ""))
    except ValueError:
        return response.text[:200]
