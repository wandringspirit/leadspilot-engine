"""Who opened which preview website (docs/SITES.md).

The Worker behind ``SITES_URL`` writes every visit into its database: ``view`` when a page loads,
``seen`` once it has been open 3 seconds, ``call`` / ``email`` / ``keep`` when a phone, email or
keep-my-website link is tapped, ``ended`` when an ended site is opened. It keeps a hash of the visitor's
IP address (never the address), their network, city and country. Every ``EVERY_S`` the panel fetches the
new ones (``GET <SITES_URL>/_visits``, ``SITES_VISITS_KEY`` as the bearer token), keeps them in
``data/sites/visits.jsonl`` and hands them to the outreach manager.

Not every visit is the business:

* **you** - from this computer's own connection (the Worker says which hash that is), or from your own
  country: the businesses are elsewhere (``US``) and your phone isn't on this computer's connection;
* **copy** - opened from a test copy of an email (``?copy`` on the link, ``OUTREACH_TEST_COPY_TO``);
* **check** - a mail server or security scanner opening the link to check it: a bot's User-Agent, a
  network like Microsoft's or Google's, or a page load right after the email without the page staying
  open (decided in the manager, which knows when the email went out).

Everything else is a **visitor**.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

EVERY_S = 300
TIMEOUT_S = 30

#: User-Agents of programs, not people
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|scan|fetch|python|curl|wget|httpx|go-http|java/|okhttp|"
                    r"headless|phantom|lighthouse|pingdom|monitor|proofpoint|mimecast|barracuda|safelinks", re.I)
#: networks that open links to check them (mail security, clouds) - people don't browse from them
SCANNER_ORG = re.compile(r"^(microsoft|google llc|amazon|digitalocean|ovh|hetzner|linode|akamai|cloudflare|"
                         r"proofpoint|mimecast|barracuda|cisco|forcepoint|zscaler|trend micro|fortinet|sophos|"
                         r"palo alto|oracle|alibaba|tencent|choopa|vultr|leaseweb|m247)", re.I)

Handler = Callable[[list[dict]], None]


def who_is(visit: dict, mine: set[str], my_countries: set[str]) -> str:
    """you | copy | check | visitor - see the module's docstring (``check`` by timing is the manager's)."""
    if visit.get("copy"):
        return "copy"
    if visit.get("who") in mine or (visit.get("country") or "") in my_countries:
        return "you"
    if BOT_UA.search(visit.get("ua") or "") or SCANNER_ORG.search(visit.get("org") or ""):
        return "check"
    return "visitor"


def place(visit: dict) -> str:
    """Where it came from, e.g. "Comcast Cable · Houston, US"."""
    town = ", ".join(part for part in (visit.get("city"), visit.get("country")) if part)
    return " · ".join(part for part in (visit.get("org"), town) if part)


class Visits:
    def __init__(self, cfg: Settings, *, fetch: Callable[[int], Awaitable[dict]] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.dir = cfg.DATA_DIR / "sites"
        self.fetch = fetch or self._fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = {"after": 0, "mine": [], "countries": [], "at": None, "ok_at": None, "error": ""}
        if self._state_file.is_file():
            try:
                self.state.update(json.loads(self._state_file.read_text(encoding="utf-8")))
            except ValueError:
                pass
        self._task: asyncio.Task | None = None

    @property
    def on(self) -> bool:
        return bool(self.cfg.SITES_VISITS_KEY)

    @property
    def _state_file(self) -> Path:
        return self.dir / "visits.json"

    @property
    def _log_file(self) -> Path:
        return self.dir / "visits.jsonl"

    def status(self) -> dict:
        return {"on": self.on, "at": self.state["at"], "ok_at": self.state["ok_at"], "error": self.state["error"],
                "countries": self.state["countries"]}

    def classify(self, visit: dict) -> str:
        return who_is(visit, set(self.state["mine"]), set(self.state["countries"]))

    # ------------------------------------------------------------------
    async def sync(self, handle: Handler) -> int:
        """Fetch the visits since the last time, write them down and hand them to ``handle``; how many."""
        self.state["at"] = _iso(self.clock())
        new: list[dict] = []
        try:
            while True:
                page = await self.fetch(self.state["after"])
                rows = page.get("visits") or []
                me = page.get("me") or {}
                if me.get("who") and me["who"] not in self.state["mine"]:
                    self.state["mine"] = (self.state["mine"] + [me["who"]])[-20:]
                if me.get("country") and me["country"] not in self.state["countries"]:
                    self.state["countries"].append(me["country"])
                if rows:
                    new += rows
                    self.state["after"] = max(int(r["id"]) for r in rows)
                if not page.get("more") or not rows:
                    break
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self.state["error"] = _problem(exc)
            log.warning("Website visits couldn't be fetched: %s", self.state["error"])
            self._save()
            return 0
        for visit in new:
            visit["as"] = self.classify(visit)
        if new:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._log_file.open("a", encoding="utf-8") as file:
                file.writelines(json.dumps(v, ensure_ascii=False) + "\n" for v in new)
            handle(new)
        self.state.update(ok_at=_iso(self.clock()), error="")
        self._save()
        return len(new)

    async def _fetch(self, after: int) -> dict:
        url = f"{self.cfg.SITES_URL.rstrip('/')}/_visits"
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(url, params={"after": after},
                                        headers={"Authorization": f"Bearer {self.cfg.SITES_VISITS_KEY}"})
        if response.status_code == 404:
            raise ValueError("the websites don't know this key - run  python sites/deploy.py --visits-key")
        response.raise_for_status()
        return response.json()

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self.state, indent=1) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    def start_loop(self, handle: Handler) -> None:
        if self.on and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._loop(handle), name="site-visits")

    async def _loop(self, handle: Handler) -> None:
        while True:
            try:
                await self.sync(handle)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Website visits: unexpected error")
            await asyncio.sleep(EVERY_S)

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def _problem(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} from {exc.request.url.host}"
    return str(exc) or type(exc).__name__


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None
