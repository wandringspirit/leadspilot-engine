"""Quote form requests from the preview websites (docs/SITES.md).

The form on a website posts to its Worker, which keeps the request in its database. Every ``EVERY_S`` the
panel fetches the waiting ones (``GET <SITES_URL>/_requests``, ``SITES_VISITS_KEY`` as the bearer token),
emails each to the business from the outreach mailbox and deletes it there (``POST /_requests``). A request
that can't be emailed stays and is tried again, so it arrives once the panel is back; nothing is kept once
it has been delivered.

It goes to you instead of the business when it was sent from this computer, your own country or a test copy
of an email (so you can try a form), when the business asked not to be emailed, or when its address doesn't
work (so a request is never lost).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import httpx

from app.config import Settings
from app.outreach.mailbox import MailError, RecipientRefused, build_message, new_message_id, sender_address, \
    sender_header
from app.outreach.manager import OutreachManager
from app.outreach.sites import SiteService
from app.outreach.visits import Visits

log = logging.getLogger(__name__)

EVERY_S = 60
TIMEOUT_S = 30
LABELS = (("problem", "What they need"), ("address", "Where"), ("when", "How soon"), ("name", "Name"),
          ("phone", "Phone"), ("email", "Email"))
#: what ``contacted.json`` says about a business that must not get this email
NOT_EMAILED = {"unsubscribed", "bounced", "failed"}


class SiteRequests:
    def __init__(self, cfg: Settings, outreach: OutreachManager, sites: SiteService, visits: Visits, *,
                 fetch: Callable[[], Awaitable[list[dict]]] | None = None,
                 finish: Callable[[list[int]], Awaitable[None]] | None = None) -> None:
        self.cfg = cfg
        self.outreach = outreach
        self.sites = sites
        self.visits = visits
        self.fetch = fetch or self._fetch
        self.finish = finish or self._finish
        self._emailed: set[int] = set()              # emailed, not deleted yet: never twice
        self._task: asyncio.Task | None = None

    async def sync(self) -> int:
        """Email the waiting requests; how many went out."""
        if self.outreach.problems():
            return 0                                 # the mailbox isn't set up: they wait
        try:
            rows = await self.fetch()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Website requests couldn't be fetched: %s", exc)
            return 0
        emailed = 0
        for row in rows:
            if row["id"] in self._emailed:
                continue
            try:
                await self._deliver(row)
            except MailError as exc:
                log.warning("A website request couldn't be emailed, it stays for the next try: %s", exc)
                break
            self._emailed.add(row["id"])
            emailed += 1
        if self._emailed:
            try:
                await self.finish(sorted(self._emailed))
                self._emailed.clear()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Emailed website requests couldn't be deleted yet: %s", exc)
        return emailed

    async def _deliver(self, row: dict) -> None:
        slug = row["slug"]
        site = self.sites.manifest().get(slug) or {}
        address = (site.get("email") or "").lower()
        to_you = (not address or self.visits.classify(row) in ("you", "copy")
                  or self.outreach.contacted.get(address, {}).get("status") in NOT_EMAILED)
        message = self._message(row, site, address, to_you)
        try:
            await self.outreach.mailbox.send(message)
        except RecipientRefused:
            await self.outreach.mailbox.send(self._message(row, site, address, True))
        log.info("Website request for %s emailed%s", site.get("business") or slug, " to you" if to_you else "")

    def _message(self, row: dict, site: dict, address: str, to_you: bool):
        fields = json.loads(row["data"])
        lines = [f"{label}: {fields[key]}" for key, label in LABELS if fields.get(key)]
        url = self.sites.url(row["slug"])
        business = site.get("business") or row["slug"]
        body = "\n".join([f"New request from your website, {url}", "", *lines, "",
                          "Call them back soon: they may be asking other roofers too."])
        own = sender_address(self.cfg)
        subject = f"New inspection request from {fields.get('name') or 'your website'}"
        if to_you:
            body = f"This would have gone to {business} <{address}>.\n\n{body}"
            subject = f"[Not sent to {business}] {subject}"
        return build_message(sender=sender_header(self.cfg), to=own if to_you else address, subject=subject,
                             body=body, message_id=new_message_id(own), reply_to=fields.get("email") or "")

    # ------------------------------------------------------------------
    def _url(self) -> str:
        return f"{self.cfg.SITES_URL.rstrip('/')}/_requests"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.SITES_VISITS_KEY}"}

    async def _fetch(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(self._url(), headers=self._auth())
        response.raise_for_status()
        return response.json().get("requests") or []

    async def _finish(self, ids: list[int]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.post(self._url(), headers=self._auth(), json={"done": ids})
        response.raise_for_status()

    # ------------------------------------------------------------------
    def start_loop(self) -> None:
        if self.visits.on and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._loop(), name="site-requests")

    async def _loop(self) -> None:
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Website requests: unexpected error")
            await asyncio.sleep(EVERY_S)

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
