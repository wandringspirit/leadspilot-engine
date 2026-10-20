"""Preview websites for campaigns (docs/SITES.md).

With ``PREVIEW_SITES`` on, every business a campaign emails gets a preview website at
``SITES_URL/<slug>/`` - built with ``sites/build.py`` when the campaign is made, put online with
``sites/deploy.py`` - and its first email waits until the website is online: the email links it. A business whose website can't be built (it isn't in the searched area, it's a supply shop …)
is left out of the campaign with the reason.

Building runs in a thread (it renders pages and copies photos). Uploading runs Cloudflare's ``wrangler``
in the background, one upload at a time, ``PUBLISH_DELAY_S`` after a change so a burst of changes - a new
campaign's sites, a morning's first emails - goes up together. ``data/sites/sites.json`` keeps each site's
dates; how long a site stays online follows from them (``sites/deploy.py``: ``ends_at``).
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import threading
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.config import Settings
from app.models import Job, PlaceRow

log = logging.getLogger(__name__)

PUBLISH_DELAY_S = 30
RETRY_S = 600                                # after a failed upload
UPLOAD_TIMEOUT_S = 600

Uploader = Callable[[], Awaitable[None]]     # uploads data/deploy/public; raises SiteError when it can't

#: what ``record`` writes down: the first email, the first email with the link, the last email, a reply
EVENTS = ("first_email_at", "link_sent_at", "last_email_at", "replied_at")


class SiteError(Exception):
    """An upload that didn't work; the message says why in plain words."""


class SiteService:
    def __init__(self, cfg: Settings, *, uploader: Uploader | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.dir = cfg.DATA_DIR / "sites"
        self.public = cfg.DATA_DIR / "deploy" / "public"     # what gets uploaded (sites/worker/wrangler.jsonc)
        self.uploader: Uploader = uploader or self._wrangler
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()                # sites.json: builds (threads) and uploads
        self._manifest_cache: tuple[int, dict] = (-1, {})
        self._task: asyncio.Task | None = None
        self._again = False
        self._uploading = False
        self.last_upload: dict = {"at": None, "ok_at": None, "error": "", "online": 0}

    # ------------------------------------------------------------------
    # What the manager asks
    # ------------------------------------------------------------------
    def url(self, slug: str) -> str:
        return f"{self.cfg.SITES_URL.rstrip('/')}/{slug}/" if slug else ""

    def has_design(self, business_type: str) -> bool:
        from sites.build import trade_pack
        return trade_pack(business_type) is not None

    def is_live(self, slug: str) -> bool:
        """The site is online (uploaded, not ended) - its business's first email can go out."""
        from sites.deploy import ends_at
        site = self._manifest().get(slug) if slug else None
        if not site or not site.get("published_at"):
            return False
        end = ends_at(site)
        return end is None or end > self.clock()

    def unpublished(self) -> list[str]:
        """Sites built but never online yet (their businesses' first emails wait for them)."""
        from sites.deploy import ends_at
        now = self.clock()
        return [slug for slug, site in self._manifest().items()
                if not site.get("published_at") and (site.get("hold") or (ends_at(site) or now) > now)]

    def status(self) -> dict:
        last = self.last_upload
        return {"url": self.cfg.SITES_URL.rstrip("/"), "uploading": self._uploading,
                "waiting": len(self.unpublished()),
                "online": sorted(slug for slug in self._manifest() if self.is_live(slug)),
                "last_upload": {"at": _iso(last["at"]), "ok_at": _iso(last["ok_at"]), "error": last["error"],
                                "online": last["online"]}}

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    def build(self, business_type: str, items: list[tuple[str, Job, PlaceRow, str]]) -> dict[str, tuple[str, str]]:
        """Build the sites of a campaign's businesses (blocking - run it in a thread).

        ``items``: (lead id, the search it came from, its row there with the lead's email, the place that
        was searched for it). Returns lead id -> (slug, "") or ("", why it has no website).
        """
        from sites.build import build_site, load_manifest, save_manifest, searched_city, trade_pack
        found = trade_pack(business_type)
        if not found:
            return {lead_id: ("", f"there's no website design for {business_type or 'this business type'} yet")
                    for lead_id, *_ in items}
        trade, pack = found
        results: dict[str, tuple[str, str]] = {}
        searches: dict[tuple[str, str], tuple] = {}
        with self._lock:
            manifest = load_manifest(self.dir)
            for lead_id, job, row, place in items:
                key = (job.id, place)
                if key not in searches:
                    searches[key] = searched_city(place, [r.model_dump() for r in job.rows])
                try:
                    results[lead_id] = build_site(row.model_dump(), searches[key], pack, trade, manifest, self.dir,
                                                  job_id=job.id)
                except Exception:  # noqa: BLE001 - one bad business must not stop the campaign
                    log.exception("The website of %s couldn't be built", row.name)
                    results[lead_id] = ("", "its website couldn't be built - see the log")
            save_manifest(manifest, self.dir)
        built = sum(bool(slug) for slug, _ in results.values())
        log.info("Built %d preview website(s) for %s, %d left out", built, business_type, len(results) - built)
        return results

    def record(self, slug: str, event: str, at: datetime) -> None:
        """Write down the first time ``event`` happened to a site - how long it stays online follows from it
        (``sites/deploy.py``: ``ends_at``) - and upload soon, for the new end date."""
        if event not in EVENTS:
            raise ValueError(f"unknown site event {event!r}")
        if self._update(slug, event, at):
            self.request_upload()

    def _update(self, slug: str, field: str, at: datetime) -> bool:
        from sites.build import load_manifest, save_manifest
        with self._lock:
            manifest = load_manifest(self.dir)
            if slug not in manifest or manifest[slug].get(field):
                return False
            manifest[slug][field] = at.isoformat(timespec="seconds")
            save_manifest(manifest, self.dir)
            return True

    def _manifest(self) -> dict:
        """sites.json, read again only when it changed (the send loop asks for every queued lead)."""
        path = self.dir / "sites.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return {}
        if mtime != self._manifest_cache[0]:
            from sites.build import load_manifest
            # no lock: saving replaces the file in one step, and a build holding the lock could take a while
            self._manifest_cache = (mtime, load_manifest(self.dir))
        return self._manifest_cache[1]

    # ------------------------------------------------------------------
    # Uploading
    # ------------------------------------------------------------------
    def request_upload(self) -> None:
        """Upload in a short while; further changes until then go up with it."""
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.get_running_loop().create_task(self._upload_soon(), name="sites-upload")

    async def _upload_soon(self) -> None:
        delay = PUBLISH_DELAY_S
        while True:
            await asyncio.sleep(delay)
            self._again = False
            ok = await self.upload()
            if ok and not self._again:
                return
            delay = PUBLISH_DELAY_S if ok else RETRY_S

    async def upload(self) -> bool:
        """Put every site that hasn't ended online now; False (and ``last_upload.error``) when it failed."""
        from sites.deploy import mark_published, prepare
        self.last_upload["at"] = self.clock()
        self._uploading = True
        try:
            slugs = await asyncio.to_thread(self._locked, prepare, self.dir, self.public)
            await self.uploader()
            await asyncio.to_thread(self._locked, mark_published, slugs, self.dir, self.clock())
        except SiteError as exc:
            self.last_upload["error"] = str(exc)
            log.warning("The preview websites couldn't be uploaded: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_upload["error"] = f"{type(exc).__name__}: {exc}"
            log.exception("The preview websites couldn't be uploaded")
            return False
        finally:
            self._uploading = False
        self.last_upload.update(ok_at=self.clock(), error="", online=len(slugs))
        log.info("Preview websites online: %d", len(slugs))
        return True

    def _locked(self, func, *args):
        with self._lock:
            return func(*args)

    async def _wrangler(self) -> None:
        from sites.deploy import PUBLIC, WORKER, wrangler_command, wrangler_env
        if self.public.resolve() != PUBLIC.resolve():
            raise SiteError(f"wrangler uploads {PUBLIC} - DATA_DIR points somewhere else ({self.cfg.DATA_DIR})")
        try:
            command = wrangler_command()
        except FileNotFoundError as exc:
            raise SiteError(str(exc)) from exc
        try:
            # in a thread: asyncio's own subprocesses need the Proactor loop on Windows
            result = await asyncio.to_thread(subprocess.run, command, cwd=WORKER, env=wrangler_env(),
                                             capture_output=True, text=True,
                                             encoding="utf-8", errors="replace", timeout=UPLOAD_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise SiteError("Node.js isn't installed - get it from https://nodejs.org") from exc
        except subprocess.TimeoutExpired as exc:
            raise SiteError(f"the upload took longer than {UPLOAD_TIMEOUT_S // 60} minutes") from exc
        if result.returncode != 0:
            raise SiteError(_wrangler_problem(result.stdout + result.stderr))

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


def _wrangler_problem(output: str) -> str:
    """What went wrong, from wrangler's output, in a line."""
    text = output.lower()
    if "wrangler login" in text or "not authenticated" in text or "authentication error" in text:
        return "not logged in to Cloudflare - run: python sites/deploy.py --login"
    lines = [(raw, re.sub(r"^[\s✘▲]*(\[(ERROR|WARNING)\]\s*)?", "", raw).strip())
             for raw in output.splitlines() if raw.strip()]
    errors = [clean for raw, clean in lines if "error" in raw.lower()]
    return (errors or [clean for _, clean in lines] or ["wrangler failed without saying why"])[-1][:300]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
